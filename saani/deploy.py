"""saani deploy [--dry-run]: the deployer (docs/SAANI.md, "Deploy"). Standard library only.

Steps, each printed, the first failure stops with what to do next:
  1. the production branch's head has the `[ci] status_context` status `success` and no failed or pending check run; a merge commit
     (what `saani merge` leaves) without a status of its own counts when its tree equals a parent's that has them;
  2. no issue in production since the prod tag has an At a glance "Deploy" row that says not to deploy;
  3. the `[deploy] checks`, then the `[deploy] dry_run` command;
  4. production fetched again (it must still be the commit checked), then the `[deploy] command` with a timeout, its output shown as it
     comes and kept in build/saani-deploy-<UTC>.log of the main checkout; a success is exit 0 and, when `log_ok` is set, a match of that
     regex in the output;
  5. the prod tag moved to the deployed commit and pushed (the one force push Saani does: the moving tag, nothing else), `### ✔ Deployed`
     on each issue in the range that carries the in_main label, and those issues closed.
`--dry-run` stops after step 3 and prints the deploy command. The key's path is only passed through: it is printed as `<key>`, the key's
content is never read. `saani merge --deploy` calls `run(args)` after the merge.
"""
import datetime
import json
import re
import subprocess
import sys
import threading

from . import ci, comments, config, glance, kpis, merge


class Stop(Exception):
    """A step failed: the message says what and what to do next."""


def cfg():
    return config.current()


# ---------------------------------------------------------------- pure functions

def expand(command, show_key=False, sha=""):
    """A command list from the settings with `{python}`, `{sha}` (the commit to deploy) and `{key}` replaced. `show_key`: `{key}` becomes
    `<key>` (for printing)."""
    c = cfg()
    if any("{key}" in part for part in command) and not c.deploy_key:
        raise Stop("a [deploy] command uses {key} but [deploy] key is empty: set the path of the deploy key in saani.toml")
    key = "<key>" if show_key else c.deploy_key
    return [part.replace("{python}", sys.executable).replace("{sha}", sha).replace("{key}", key) for part in command]


def issues_in(log):
    """The issue numbers the commit messages of `log` (separated by a record separator, \\x1e) name with `refs #n`, also `refs #12, #13 and
    #14`, once each."""
    found = []
    for message in (log or "").split("\x1e"):
        found += [n for n in kpis.issues_in(message) if n not in found]
    return found


def says_do_not_deploy(body):
    """The text of the At a glance Deploy row when it says not to deploy (glance.deploy_blocked), else None."""
    return glance.deploy_blocked(body)


def same_tree_parents(parents, tree, trees):
    """The parents (shas) whose tree is `tree`, given {sha: tree}."""
    return [p for p in parents if trees.get(p) == tree]


def succeeded(code, output, log_ok):
    """A deploy succeeded when it exited 0 and, if `log_ok` is set, its output matches it."""
    return code == 0 and (not log_ok or re.search(log_ok, output or "") is not None)


def outcome(code):
    return "timed out" if code is None else f"exit {code}"


# ---------------------------------------------------------------- the steps

def execute(command, timeout=None, echo=True, sink=None):
    """(exit code, output) of a command run in the main checkout, stdout and stderr together. The output is shown as it comes (`echo`: an SSH
    agent's prompt to confirm presence must reach the owner) and written to `sink` (an open file) as it comes. Code None on a timeout: the
    process is killed and the output so far is kept."""
    lines = []
    try:
        proc = subprocess.Popen(command, cwd=cfg().root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        return 127, f"could not start {command[0]}: {e}\n"

    def read():
        for line in proc.stdout:
            lines.append(line)
            if echo:
                print("  | " + line.rstrip("\n"), flush=True)
            if sink:
                sink.write(line)
                sink.flush()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        code = None
    reader.join(5)
    return code, "".join(lines)


def tree_of(gh, rev):
    return gh.read("git", "rev-parse", f"{rev}^{{tree}}")


def green_on_itself(gh, sha):
    """(state, problem) of a commit: its `[ci] status_context` state (None: no status) and what its check runs forbid (None: nothing)."""
    c = cfg()
    status = gh.json("gh", "api", f"repos/{c.repo}/commits/{sha}/status")
    checks = gh.json("gh", "api", f"repos/{c.repo}/commits/{sha}/check-runs?per_page=100", "--paginate")
    return merge.ci_state(status), merge.check_runs_problem(checks, sha[:7])


def step_ci(gh):
    """Step 1: the production head and its CI. Returns the head's sha."""
    c = cfg()
    gh.read("git", "fetch", "-q", "origin", c.production)
    gh.read("git", "fetch", "-q", "--force", "origin", f"refs/tags/{c.prod_tag}:refs/tags/{c.prod_tag}", check=False)
    sha = gh.read("git", "rev-parse", f"origin/{c.production}")
    state, problem = green_on_itself(gh, sha)
    if state not in (None, "success"):
        raise Stop(f"{c.ci_status_context} is {state} on {sha[:7]}, the head of {c.production}: wait for it or fix it, then saani deploy again")
    if state == "success":
        if problem:
            raise Stop(problem + " (then saani deploy again)")
        print(f"1. CI: {c.ci_status_context} is success on {sha[:7]} (origin/{c.production})")
        return sha
    # No status of its own: a merge commit (what `gh pr merge --merge` leaves) is the same content as a parent that was tested.
    parents = gh.read("git", "rev-list", "--parents", "-n", "1", sha).split()[1:]
    if len(parents) == 2:
        tree = tree_of(gh, sha)
        for parent in same_tree_parents(parents, tree, {p: tree_of(gh, p) for p in parents}):
            pstate, pproblem = green_on_itself(gh, parent)
            if pstate == "success" and not pproblem:
                print(f"1. CI: {sha[:7]} is a merge commit with the tree of {parent[:7]}, where {c.ci_status_context} is success "
                      f"and no check run failed or is pending")
                return sha
    raise Stop(f"no {c.ci_status_context} status on {sha[:7]}, the head of {c.production}, and it is not a merge of a green commit with the "
               f"same content: production holds something CI has not seen. Open a new {c.integration} -> {c.production} pull request "
               f"(saani ci pr), merge it (saani merge), then saani deploy again")


def step_issues(gh, sha):
    """Step 2: the issues in production since the prod tag. Returns [(number, label names)] of the range."""
    c = cfg()
    found, blocked = [], []
    if not gh.ok("git", "rev-parse", "-q", "--verify", f"refs/tags/{c.prod_tag}"):
        # A first deploy: there is no range, so the issues merged and not yet deployed are the open ones labelled in_main.
        print(f"2. no {c.prod_tag} tag: a first deploy; the open {c.labels['in_main']} issues are read for the marker, none is commented or closed")
        for issue in gh.json("gh", "issue", "list", "--repo", c.repo, "--label", c.labels["in_main"], "--state", "open", "--limit", "200",
                             "--json", "number,body") or []:
            text = says_do_not_deploy(issue.get("body"))
            if text:
                blocked.append((issue["number"], text))
    else:
        for n in issues_in(gh.read("git", "log", "--format=%B%x1e", f"{c.prod_tag}..{sha}")):
            issue = gh.json("gh", "issue", "view", str(n), "--repo", c.repo, "--json", "number,body,labels") or {}
            text = says_do_not_deploy(issue.get("body"))
            if text:
                blocked.append((n, text))
            found.append((n, [lab.get("name") for lab in issue.get("labels", [])]))
    if blocked:
        listing = "\n".join(f"   #{n}: Deploy: {t}" for n, t in blocked)
        raise Stop(f"issues in {c.production} say not to deploy:\n{listing}\n"
                   f"   Fix the issue or revert its commits (or correct its At a glance Deploy row if it is wrong), then saani deploy again")
    if found:
        listed = ", ".join("#" + str(n) for n, _ in found)
        print(f"2. issues since {c.prod_tag}: {listed}; none says not to deploy")
    return found


def step_checks(sha):
    """Step 3: the checks, then the dry run."""
    c = cfg()
    seconds = c.deploy_timeout_minutes * 60
    for i, command in enumerate(c.deploy_checks, 1):
        shown = " ".join(expand(command, True, sha))
        code, out = execute(expand(command, sha=sha), seconds)
        if code != 0:
            raise Stop(f"check {i} failed ({shown}: {outcome(code)}): fix what it names, then saani deploy again")
        print(f"3. check {i} ok: {shown}")
    if not c.deploy_checks:
        print("3. no [deploy] checks")
    if not c.deploy_dry_run:
        print("3. no [deploy] dry_run: skipped")
        return
    shown = " ".join(expand(c.deploy_dry_run, True, sha))
    code, out = execute(expand(c.deploy_dry_run, sha=sha), seconds)
    if code != 0:
        raise Stop(f"the dry run failed ({shown}: {outcome(code)}): read its output above, fix, then saani deploy again")
    print(f"3. dry run ok: {shown}")


def step_deploy(gh, sha):
    """Step 4: production fetched again, then the deploy; its output into the log as it comes. Returns the log's file name."""
    c = cfg()
    gh.read("git", "fetch", "-q", "origin", c.production)
    now = gh.read("git", "rev-parse", f"origin/{c.production}")
    if now != sha:
        raise Stop(f"origin/{c.production} moved from {sha[:7]} to {now[:7]} while the checks ran: that commit has not been checked. "
                   f"Nothing was deployed; run saani deploy again")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = c.root / "build" / f"saani-deploy-{stamp}.log"
    shown = " ".join(expand(c.deploy_command, True, sha))
    print(f"4. deploying {sha[:7]}: {shown} (timeout {c.deploy_timeout_minutes:g} minutes; a command that uses the key may ask for its tap)",
          flush=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as sink:
        sink.write(f"# saani deploy {sha}\n# {shown}\n")
        code, out = execute(expand(c.deploy_command, sha=sha), c.deploy_timeout_minutes * 60, sink=sink)
        sink.write(f"# exit: {outcome(code)}\n")
    if code is None:
        raise Stop(f"the deploy timed out after {c.deploy_timeout_minutes:g} minutes and was stopped; the log is {log}. The server may be "
                   f"half-done: look at it before running saani deploy again. The prod tag was not moved, no issue closed")
    if not succeeded(code, out, c.deploy_log_ok):
        why = f"exit {code}" if code != 0 else f"the output does not match log_ok ({c.deploy_log_ok})"
        raise Stop(f"the deploy failed ({why}); the log is {log}. The prod tag was not moved, no issue closed: read the log, fix, "
                   f"and run saani deploy again (the deployer agent reads a log and says what it means)")
    print(f"4. deploy ok (exit 0{', log_ok matched' if c.deploy_log_ok else ''}); log: {log.name}")
    return log.name


def step_after(gh, sha, found, log_name):
    """Step 5: the prod tag, then `✔ Deployed` and the close on each issue of the range that carries the in_main label. Returns 0 or 1."""
    c = cfg()
    code = 0
    try:
        gh.read("git", "tag", "-f", c.prod_tag, sha)
        gh.read("git", "push", "origin", f"refs/tags/{c.prod_tag}", "--force")      # the only force push Saani does: the moving tag
        print(f"5. {c.prod_tag} tag moved to {sha[:7]} and pushed")
    except SystemExit as e:
        print(f"5. the deploy is done but the {c.prod_tag} tag was not moved: {e}\n   Move it by hand: git tag -f {c.prod_tag} {sha}, "
              f"then git push origin refs/tags/{c.prod_tag} --force")
        code = 1
    for n, labels in found:
        if c.labels["in_main"] not in labels:
            print(f"   #{n}: not labelled {c.labels['in_main']}: left as it is")
            continue
        try:
            posted = comments.try_post(n, "deployed", {"Commit": sha[:7], "Log": log_name}, io=gh, labels=False, once=sha[:7])
            gh.write("gh", "issue", "close", str(n), "--repo", c.repo, "--reason", "completed")
            print(f"   #{n}: " + ("commented ✔ Deployed and " if posted else "already commented (or the comment failed); ") + "closed")
        except SystemExit as e:
            print(f"   #{n}: could not comment or close ({e}): do it by hand")
            code = 1
    return code


def run(args):
    """`saani deploy` and the deploy step of `saani merge --deploy`: 0 deployed (or the dry run passed), 1 refused or failed."""
    c = cfg()
    if not c.deploy_command:
        print("refused: no [deploy] command in saani.toml (docs/CONFIG.md, [deploy])")
        return 1
    try:
        re.compile(c.deploy_log_ok)
    except re.error as e:
        print(f"refused: [deploy] log_ok is not a valid regex ({e}): fix saani.toml")
        return 1
    gh = ci.Run(False)
    try:
        sha = step_ci(gh)
        found = step_issues(gh, sha)
        step_checks(sha)
        if getattr(args, "dry_run", False):
            print("dry-run: would deploy with: " + " ".join(expand(c.deploy_command, True, sha)))
            return 0
        log_name = step_deploy(gh, sha)
        return step_after(gh, sha, found, log_name)
    except json.JSONDecodeError as e:
        print(f"stopped: gh gave an answer that is not JSON ({e}): nothing was deployed; try again")
        return 1
    except (Stop, SystemExit) as e:
        print(f"stopped: {e}")
        return 1


def add_parsers(sub):
    p = sub.add_parser("deploy", help="deploy production: CI green, no issue says do not deploy, checks, dry run, deploy, log, prod tag, close issues")
    p.add_argument("--dry-run", action="store_true", help="steps 1 to 3 only, then print the deploy command (the key shown as <key>)")
    p.set_defaults(run=run)
