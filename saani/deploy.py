"""saani deploy [--dry-run]: the deployer (docs/SAANI.md, "Deploy"). Standard library only.

Steps, each printed, the first failure stops with what to do next:
  1. the production branch's head has the `[ci] status_context` status `success`;
  2. no issue in production since the prod tag has an At a glance "Deploy" row that says not to deploy;
  3. the `[deploy] checks`, then the `[deploy] dry_run` command;
  4. the `[deploy] command` with a timeout, its output kept in build/saani-deploy-<UTC>.log of the main checkout; a success is exit 0 and,
     when `log_ok` is set, a match of that regex in the output;
  5. the prod tag moved to the deployed commit and pushed (the one force push Saani does: the moving tag, nothing else), `### ✔ Deployed`
     on each issue in the range that carries the in_main label, and those issues closed.
`--dry-run` stops after step 3 and prints the deploy command. The key's path is only passed through: it is printed as `<key>`, the key's
content is never read. `saani merge --deploy` calls `run(args)` after the merge.
"""
import datetime
import re
import subprocess
import sys

from . import ci, config, glance, merge

NO_DEPLOY = re.compile(r"^(no\b|⛔|do not)", re.I)       # "None" is not a no: it says there is nothing to deploy
MAX_SHOWN = 40                                           # lines of a failing command's output printed (the log keeps all)


class Stop(Exception):
    """A step failed: the message says what and what to do next."""


def cfg():
    return config.current()


# ---------------------------------------------------------------- pure functions

def expand(command, show_key=False):
    """A command list from the settings with `{python}` and `{key}` replaced. `show_key`: `{key}` becomes `<key>` (for printing)."""
    c = cfg()
    if any("{key}" in part for part in command) and not c.deploy_key:
        raise Stop("a [deploy] command uses {key} but [deploy] key is empty: set the path of the deploy key in saani.toml")
    key = "<key>" if show_key else c.deploy_key
    return [part.replace("{python}", sys.executable).replace("{key}", key) for part in command]


def issues_in(messages):
    """The issue numbers named `refs #n` (any case) in commit messages, in order of first appearance."""
    found = []
    for m in re.findall(r"\brefs\s+#(\d+)", messages or "", re.I):
        if int(m) not in found:
            found.append(int(m))
    return found


def says_do_not_deploy(body):
    """The text of the At a glance Deploy row when it says not to deploy ("No...", "⛔...", "Do not..."), else None."""
    text = glance.table(body).get("deploy")
    return text if text and NO_DEPLOY.match(text.strip()) else None


def succeeded(code, output, log_ok):
    """A deploy succeeded when it exited 0 and, if `log_ok` is set, its output matches it."""
    return code == 0 and (not log_ok or re.search(log_ok, output or "") is not None)


def deployed_comment(sha, log_name):
    return f"### ✔ Deployed\n\n| Commit | Log |\n|---|---|\n| `{sha[:7]}` | `{log_name}` |\n"


def outcome(code):
    return "timed out" if code is None else f"exit {code}"


# ---------------------------------------------------------------- the steps

def execute(command, timeout=None):
    """(exit code, output) of a command run in the main checkout; code None on a timeout (the process is killed)."""
    try:
        r = subprocess.run(command, cwd=cfg().root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        return None, out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    except OSError as e:
        return 127, f"could not start {command[0]}: {e}"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def show(output):
    for line in (output or "").strip().splitlines()[-MAX_SHOWN:]:
        print("  | " + line)


def step_ci(gh):
    """Step 1: the production head and its CI status. Returns the head's sha."""
    c = cfg()
    gh.read("git", "fetch", "-q", "origin", c.production)
    gh.read("git", "fetch", "-q", "--force", "origin", f"refs/tags/{c.prod_tag}:refs/tags/{c.prod_tag}", check=False)
    sha = gh.read("git", "rev-parse", f"origin/{c.production}")
    state = merge.ci_state(gh.json("gh", "api", f"repos/{c.repo}/commits/{sha}/status"))
    if state is None:
        raise Stop(f"no {c.ci_status_context} status on {sha[:7]}, the head of {c.production}: run the CI on that commit "
                   f"(saani ci pr), then saani deploy again")
    if state != "success":
        raise Stop(f"{c.ci_status_context} is {state} on {sha[:7]}, the head of {c.production}: wait for it or fix it, then saani deploy again")
    print(f"1. CI: {c.ci_status_context} is success on {sha[:7]} (origin/{c.production})")
    return sha


def step_issues(gh, sha):
    """Step 2: the issues in production since the prod tag. Returns [(number, label names)] of the range."""
    c = cfg()
    if not gh.ok("git", "rev-parse", "-q", "--verify", f"refs/tags/{c.prod_tag}"):
        print(f"2. no {c.prod_tag} tag: a first deploy, so no issue is read, commented or closed; the tag will be created")
        return []
    found, blocked = [], []
    for n in issues_in(gh.read("git", "log", "--format=%B%x1e", f"{c.prod_tag}..{sha}")):
        issue = gh.json("gh", "issue", "view", str(n), "--repo", c.repo, "--json", "number,body,labels") or {}
        text = says_do_not_deploy(issue.get("body"))
        if text:
            blocked.append((n, text))
        found.append((n, [lab.get("name") for lab in issue.get("labels", [])]))
    if blocked:
        listing = "\n".join(f"   #{n}: Deploy: {t}" for n, t in blocked)
        raise Stop(f"issues in {c.production} since {c.prod_tag} say not to deploy:\n{listing}\n"
                   f"   Fix the issue or revert its commits (or correct its At a glance Deploy row if it is wrong), then saani deploy again")
    print(f"2. issues since {c.prod_tag}: {', '.join(f'#{n}' for n, _ in found) or 'none'}; none says not to deploy")
    return found


def step_checks():
    """Step 3: the checks, then the dry run."""
    c = cfg()
    seconds = c.deploy_timeout_minutes * 60
    for i, command in enumerate(c.deploy_checks, 1):
        shown = " ".join(expand(command, show_key=True))
        code, out = execute(expand(command), seconds)
        if code != 0:
            show(out)
            raise Stop(f"check {i} failed ({shown}: {outcome(code)}): fix what it names, then saani deploy again")
        print(f"3. check {i} ok: {shown}")
    if not c.deploy_checks:
        print("3. no [deploy] checks")
    if not c.deploy_dry_run:
        print("3. no [deploy] dry_run: skipped")
        return
    shown = " ".join(expand(c.deploy_dry_run, show_key=True))
    code, out = execute(expand(c.deploy_dry_run), seconds)
    show(out)
    if code != 0:
        raise Stop(f"the dry run failed ({shown}: {outcome(code)}): read its output above, fix, then saani deploy again")
    print(f"3. dry run ok: {shown}")


def step_deploy(sha):
    """Step 4: the deploy; its output into the log. Returns the log's file name."""
    c = cfg()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = c.root / "build" / f"saani-deploy-{stamp}.log"
    shown = " ".join(expand(c.deploy_command, show_key=True))
    print(f"4. deploying {sha[:7]}: {shown} (timeout {c.deploy_timeout_minutes:g} minutes)", flush=True)
    code, out = execute(expand(c.deploy_command), c.deploy_timeout_minutes * 60)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"# saani deploy {sha}\n# {shown}\n# exit: {outcome(code)}\n{out}", encoding="utf-8")
    if code is None:
        raise Stop(f"the deploy timed out after {c.deploy_timeout_minutes:g} minutes and was stopped; the log is {log}. The server may be "
                   f"half-done: look at it before running saani deploy again. The prod tag was not moved, no issue closed")
    if not succeeded(code, out, c.deploy_log_ok):
        show(out)
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
            gh.write("gh", "issue", "comment", str(n), "--repo", c.repo, "--body", deployed_comment(sha, log_name))
            gh.write("gh", "issue", "close", str(n), "--repo", c.repo, "--reason", "completed")
            print(f"   #{n}: commented ✔ Deployed and closed")
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
    gh = ci.Run(False)
    try:
        sha = step_ci(gh)
        found = step_issues(gh, sha)
        step_checks()
        if getattr(args, "dry_run", False):
            print("dry-run: would deploy with: " + " ".join(expand(c.deploy_command, show_key=True)))
            return 0
        log_name = step_deploy(sha)
        return step_after(gh, sha, found, log_name)
    except (Stop, SystemExit) as e:
        print(f"stopped: {e}")
        return 1


def add_parsers(sub):
    p = sub.add_parser("deploy", help="deploy production: CI green, no issue says do not deploy, checks, dry run, deploy, log, prod tag, close issues")
    p.add_argument("--dry-run", action="store_true", help="steps 1 to 3 only, then print the deploy command (the key shown as <key>)")
    p.set_defaults(run=run)
