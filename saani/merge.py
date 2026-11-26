"""saani merge [PR] [--deploy [--skip-ci WHY]] [--dry-run]: the owner's one word at the end of an auto run (docs/SAANI.md, "Two modes").

Merges the green integration -> production pull request with a merge commit (never squash or rebase: the trailers stay; the head branch is
never deleted: it is the integration branch), then does what `saani ci merged <PR>` does (relabel, remove the finished auto runs). With
`--deploy` it then runs the deployer (`saani.deploy.run`, with `--skip-ci` if given: it skips the deploy's CI gate, never this pull
request's); without one it stops after the merge and exits 2.
Standard library only; the checks are pure functions (tests/test_merge.py).
"""
import importlib
import json
import re

from . import ci, config, notes

PASSED_CHECKS = ("success", "neutral", "skipped")      # any other conclusion (failure, cancelled, timed_out, action_required, stale...) refuses
MAYBE_NOT_RUN = ("failure", "startup_failure", "cancelled")   # the conclusions a run GitHub never started ends with
# GitHub's annotation on a job it refused to start: "The job was not started because recent account payments have failed or your spending
# limit needs to be increased." Only these words make a failed run "not run"; any other annotation is a real failure.
REFUSED_JOB = re.compile(r"job was not started|spending limit needs|account payments have failed", re.I)


def cfg():
    return config.current()


def find_pr(run, number=None):
    """The pull request number: the one given, else the one open pull request production <- integration. Refuses none or several."""
    c = cfg()
    if number is not None:
        return number
    prs = run.json("gh", "pr", "list", "--repo", c.repo, "--base", c.production, "--head", c.integration, "--state", "open",
                   "--json", "number") or []
    if not prs:
        raise SystemExit(f"no open {c.integration} -> {c.production} pull request: nothing to merge (the auto run opens it; or name one: saani merge <PR>)")
    if len(prs) > 1:
        numbers = ", ".join(f"#{p['number']}" for p in prs)
        raise SystemExit(f"several open {c.integration} -> {c.production} pull requests ({numbers}): close the extra ones, or name the one: saani merge <PR>")
    return prs[0]["number"]


def ci_state(status):
    """The `[ci] status_context` state ("success", "pending", "failure"...) in a commit's combined status, or None when it has none."""
    mine = [s for s in (status or {}).get("statuses", []) if s.get("context") == cfg().ci_status_context]
    return mine[0].get("state") if mine else None


def refusal(pr, status, checks, skipped=()):
    """Why this pull request must not be merged, or None when it may. `pr`: gh pr view; `status`: the commit's combined status;
    `checks`: its check runs; `skipped`: the ids of those that never started (not_run)."""
    c = cfg()
    n = pr.get("number")
    if pr.get("state") != "OPEN":
        return f"pull request #{n} is {pr.get('state')}, not OPEN: nothing to merge"
    if pr.get("baseRefName") != c.production or pr.get("headRefName") != c.integration:
        return (f"pull request #{n} is {pr.get('headRefName')} -> {pr.get('baseRefName')}, not {c.integration} -> {c.production}: "
                f"saani merge only merges the integration branch into production (a hotfix pull request: merge it on GitHub)")
    if pr.get("mergeable") == "UNKNOWN":
        return f"GitHub is still working out whether pull request #{n} is mergeable: wait a minute and run saani merge again"
    if pr.get("mergeable") != "MERGEABLE":
        return (f"pull request #{n} is not mergeable ({pr.get('mergeable')}, {pr.get('mergeStateStatus')}): "
                f"merge {c.production} into {c.integration} (saani land does it), then run saani ci pr")
    sha = (pr.get("headRefOid") or "")[:7]
    state = ci_state(status)
    if state is None:
        return f"no {c.ci_status_context} status on {sha}, the head of #{n}: run saani ci pr"
    if state == "pending":
        return f"CI still running on {sha} ({c.ci_status_context} is pending): wait for it, then run saani merge again"
    if state != "success":
        return f"CI is red on {sha} ({c.ci_status_context}: {state}): fix it, land, then run saani ci pr"
    return check_runs_problem(checks, sha, skipped)


def never_started(check, annotations=(), steps=None):
    """True when a completed check run that did not pass never ran: GitHub refused the job (an annotation in REFUSED_JOB: billing or the
    spending limit), or it ended `startup_failure` or `cancelled` without a single step (`steps`: the job's steps, [] none; None unknown).
    Such a run tested nothing: it is "not run", not "failed"."""
    if check.get("status") != "completed" or check.get("conclusion") in PASSED_CHECKS:
        return False
    if any(REFUSED_JOB.search(str(a.get("message") or "")) for a in annotations or ()):
        return True
    return check.get("conclusion") in ("startup_failure", "cancelled") and steps == []


def not_run(run, checks):
    """The ids of the check runs of `checks` that never started (never_started), read with gh: a run's annotations when it has some, and for
    a GitHub Actions run that ended startup_failure or cancelled, its job's steps (an Actions check run's id is its job's id). A read that
    fails leaves the run failed."""
    repo, ids = cfg().repo, set()
    for check in (checks or {}).get("check_runs", []):
        if check.get("id") is None or check.get("status") != "completed" or check.get("conclusion") not in MAYBE_NOT_RUN:
            continue
        annotations, steps = [], None
        try:
            if ((check.get("output") or {}).get("annotations_count") or 0) > 0:
                annotations = run.json("gh", "api", f"repos/{repo}/check-runs/{check.get('id')}/annotations") or []
            if (not never_started(check, annotations) and check.get("conclusion") != "failure"
                    and (check.get("app") or {}).get("slug") == "github-actions"):
                steps = (run.json("gh", "api", f"repos/{repo}/actions/jobs/{check.get('id')}") or {}).get("steps")
        except (SystemExit, ValueError):
            pass
        if never_started(check, annotations, steps):
            ids.add(check.get("id"))
    return ids


def not_run_note(checks, skipped, sha):
    """The line that names the check runs of `sha` GitHub never started, or None."""
    names = [str(r.get("name")) for r in (checks or {}).get("check_runs", []) if r.get("id") in skipped]
    return f"not run on {sha} ({', '.join(names)}): GitHub never started them; they count as not run, not failed" if names else None


def check_runs_problem(checks, sha, skipped=()):
    """Why the check runs of a commit (`gh api .../check-runs`) forbid going on: one failed, or one has not finished. None when all passed.
    `skipped`: the ids of runs that never started (not_run): they are left out, neither passed nor failed. Shared by saani merge and saani
    deploy."""
    runs = [r for r in (checks or {}).get("check_runs", []) if r.get("id") is None or r.get("id") not in skipped]
    bad = [r.get("name") for r in runs if r.get("status") == "completed" and r.get("conclusion") not in PASSED_CHECKS]
    if bad:
        return f"a check run did not pass on {sha} ({', '.join(map(str, bad))}): fix it, land, then run saani ci pr"
    if any(r.get("status") != "completed" for r in runs):
        return f"CI still running on {sha} (a check run has not finished): wait for it, then run saani merge again"
    return None


def deployer():
    """`saani.deploy.run`, or None when the module is missing."""
    try:
        return importlib.import_module("saani.deploy").run
    except (ImportError, AttributeError):
        return None


def run_command(args):
    try:
        return merge_it(args)
    except json.JSONDecodeError as e:
        print(f"refused: gh gave an answer that is not JSON ({e}): nothing was merged; try again")
        return 1


def release_step(number, merge_sha):
    """The GitHub release when the merged pull request changed the version (saani/notes.py): (0, None) when it was made or none was due.
    A failure of any kind never undoes the merge: it prints `next:` with the command for the owner, and gives (1, that line)."""
    if not cfg().version_file:
        return 0, None
    if not merge_sha:
        print(f"no release made: GitHub names no merge commit for #{number} yet")
        line = f"next: saani release create <version> --target <merge commit> --from-pr {number}"
        print(line)
        return 1, line
    version = None
    try:
        notes.release_after_merge(number, merge_sha)
    except BaseException as e:                                   # ReleaseFailed (a SystemExit), OSError, odd gh data...
        if isinstance(e, KeyboardInterrupt):
            raise
        version = getattr(e, "version", None)
        print(f"the merge is done; the release step failed: {e}")
        line = f"next: saani release create {version or '<version>'} --target {merge_sha} --from-pr {number}"
        print(line)
        return 1, line
    return 0, None


def merge_it(args):
    c = cfg()
    if getattr(args, "skip_ci", None) is not None and not args.deploy:
        print("refused: --skip-ci skips the CI gate of the deploy: it goes with --deploy (saani merge --deploy --skip-ci \"<why>\")")
        return 1
    gh = ci.Run(args.dry_run)
    n = find_pr(gh, args.pr)
    pr = gh.json("gh", "pr", "view", str(n), "--repo", c.repo, "--json",
                 "number,title,state,baseRefName,headRefName,headRefOid,mergeable,mergeStateStatus,url") or {}
    pr.setdefault("number", n)
    sha = pr.get("headRefOid", "")
    status = gh.json("gh", "api", f"repos/{c.repo}/commits/{sha}/status")
    checks = gh.json("gh", "api", f"repos/{c.repo}/commits/{sha}/check-runs?per_page=100", "--paginate")
    skipped = not_run(gh, checks)
    note = not_run_note(checks, skipped, sha[:7])
    if note:
        print(note)
    why = refusal(pr, status, checks, skipped)
    if why:
        print(f"refused: {why}")
        return 1
    print(f"#{n} {pr.get('title', '')}: {pr['headRefName']} -> {pr['baseRefName']}, mergeable, {c.ci_status_context} green on {sha[:7]}")
    if args.dry_run:
        print(f"dry-run: gh pr merge {n} --merge")
        print(f"dry-run: then saani ci merged {n} (relabel, remove the finished auto runs)")
        try:
            ci.preview_status(gh, n)
        except (SystemExit, OSError, ValueError) as e:
            print(f"dry-run: could not read the issues of #{n} to show their status: {e}")
        if c.version_file:
            print(f"dry-run: then, when the pull request changes the version in {c.version_file}, the GitHub release v<version> with the notes of its issues")
        if args.deploy:
            print("dry-run: then the deploy" + ("" if deployer() else " (not available yet: saani/deploy.py is missing)")
                  + (" without its CI gate (--skip-ci)" if getattr(args, "skip_ci", None) is not None else ""))
        return 0
    gh.write("gh", "pr", "merge", str(n), "--repo", c.repo, "--merge", "--match-head-commit", sha)
    after = gh.json("gh", "pr", "view", str(n), "--repo", c.repo, "--json", "state,mergeCommit") or {}
    if after.get("state") != "MERGED":
        print(f"#{n} is {after.get('state')}, not MERGED (a merge queue may only have enqueued it): stopping before the relabel; "
              f"when it is merged, run saani ci merged {n}")
        return 1
    print(f"merged #{n} into {c.production}")
    code = ci.cmd_merged(gh, n)
    release_code, next_line = release_step(n, (after.get("mergeCommit") or {}).get("oid"))
    code = release_code or code
    if not args.deploy:
        return code
    deploy = deployer()
    if deploy is None:
        print("deploy is not available yet (saani/deploy.py is missing): the merge is done, nothing was deployed")
        if next_line:
            print(next_line)
        return 2
    result = deploy(args) or code
    if next_line:
        print(next_line)                                         # the last line, after the deploy's own output
    return result


def add_parsers(sub):
    p = sub.add_parser("merge", help="merge the green integration -> production pull request, then relabel and clean up")
    p.add_argument("pr", nargs="?", type=int, help="the pull request number (default: the open integration -> production one)")
    p.add_argument("--deploy", action="store_true", help="then deploy (saani deploy)")
    p.add_argument("--skip-ci", metavar="WHY", help="with --deploy: the deploy skips its CI gate (saani deploy --skip-ci); the owner's, never an auto run's")
    p.add_argument("--dry-run", action="store_true", help="all the checks, print what would happen, merge nothing")
    p.set_defaults(run=run_command)
