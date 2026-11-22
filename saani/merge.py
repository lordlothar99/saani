"""saani merge [PR] [--deploy] [--dry-run]: the owner's one word at the end of an auto run (docs/SAANI.md, "Two modes").

Merges the green integration -> production pull request with a merge commit (never squash or rebase: the trailers stay; the head branch is
never deleted: it is the integration branch), then does what `saani ci merged <PR>` does (relabel, remove the finished auto runs). With
`--deploy` it then runs the deployer (`saani.deploy.run`); without one it stops after the merge and exits 2.
Standard library only; the checks are pure functions (tests/test_merge.py).
"""
import importlib
import json

from . import ci, config

PASSED_CHECKS = ("success", "neutral", "skipped")      # any other conclusion (failure, cancelled, timed_out, action_required, stale...) refuses


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


def refusal(pr, status, checks):
    """Why this pull request must not be merged, or None when it may. `pr`: gh pr view; `status`: the commit's combined status;
    `checks`: its check runs."""
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
    return check_runs_problem(checks, sha)


def check_runs_problem(checks, sha):
    """Why the check runs of a commit (`gh api .../check-runs`) forbid going on: one failed, or one has not finished. None when all passed.
    Shared by saani merge and saani deploy."""
    runs = (checks or {}).get("check_runs", [])
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


def merge_it(args):
    c = cfg()
    gh = ci.Run(args.dry_run)
    n = find_pr(gh, args.pr)
    pr = gh.json("gh", "pr", "view", str(n), "--repo", c.repo, "--json",
                 "number,title,state,baseRefName,headRefName,headRefOid,mergeable,mergeStateStatus,url") or {}
    pr.setdefault("number", n)
    sha = pr.get("headRefOid", "")
    status = gh.json("gh", "api", f"repos/{c.repo}/commits/{sha}/status")
    checks = gh.json("gh", "api", f"repos/{c.repo}/commits/{sha}/check-runs?per_page=100", "--paginate")
    why = refusal(pr, status, checks)
    if why:
        print(f"refused: {why}")
        return 1
    print(f"#{n} {pr.get('title', '')}: {pr['headRefName']} -> {pr['baseRefName']}, mergeable, {c.ci_status_context} green on {sha[:7]}")
    if args.dry_run:
        print(f"dry-run: gh pr merge {n} --merge")
        print(f"dry-run: then saani ci merged {n} (relabel, remove the finished auto runs)")
        if args.deploy:
            print("dry-run: then the deploy" + ("" if deployer() else " (not available yet: saani/deploy.py is missing)"))
        return 0
    gh.write("gh", "pr", "merge", str(n), "--repo", c.repo, "--merge", "--match-head-commit", sha)
    after = gh.json("gh", "pr", "view", str(n), "--repo", c.repo, "--json", "state") or {}
    if after.get("state") != "MERGED":
        print(f"#{n} is {after.get('state')}, not MERGED (a merge queue may only have enqueued it): stopping before the relabel; "
              f"when it is merged, run saani ci merged {n}")
        return 1
    print(f"merged #{n} into {c.production}")
    code = ci.cmd_merged(gh, n)
    if not args.deploy:
        return code
    deploy = deployer()
    if deploy is None:
        print("deploy is not available yet (saani/deploy.py is missing): the merge is done, nothing was deployed")
        return 2
    return deploy(args) or code


def add_parsers(sub):
    p = sub.add_parser("merge", help="merge the green integration -> production pull request, then relabel and clean up")
    p.add_argument("pr", nargs="?", type=int, help="the pull request number (default: the open integration -> production one)")
    p.add_argument("--deploy", action="store_true", help="then deploy (saani deploy)")
    p.add_argument("--dry-run", action="store_true", help="all the checks, print what would happen, merge nothing")
    p.set_defaults(run=run_command)
