"""Saani's hybrid CI (docs/SAANI.md, "CI within the free minutes"): GitHub Actions as much as the free minutes allow, the owner's PC for the
rest, decided job by job. Standard library only; pure functions first (tests/test_ci.py).

  saani ci status          the month's Actions minutes, the reserve, and where each job would run now
  saani ci sync            switch the small automatic workflows (`[ci] jobs` with a `workflow`) on or off by budget
  saani ci pr [--head B]   the CI of the open integration -> production pull request: on GitHub (workflow_dispatch of `[ci] workflow`)
                           or on the PC (`[tests] check` then `[tests] full` in a temporary worktree at the pull request's head, the full
                           run through the test queue); either way the result is the commit status `[ci] status_context`
  saani ci merged <PR>     after a merge into production: the relabel ran on GitHub, or run `[ci] relabel` here
  saani ci place <job>     print github or pc for one job, and why
  --dry-run                print every write (dispatch, status, enable/disable) instead of doing it

The rule:
- the minutes used this month come from the billing API (`gh api users/<account>/settings/billing/usage`), every repo of the account (the
  free minutes are one pool); `free_minutes` a month (GitHub Free: 2,000 for private repos, Linux);
- a heavy job runs on GitHub only if `reserve_minutes` stay after it and the month is not spending faster than it passes (`pace_slack`);
  otherwise on the PC;
- a small job runs on GitHub while any minute is left;
- a job GitHub refuses (no step ran: quota out or billing) falls back to the PC by itself, and the month is then treated as spent;
- every decision is appended to build/saani-ci.json of the main checkout (job, place, why, minutes before, the run or the PC's time).
"""
import argparse
import datetime
import json
import math
import os
import shutil
import subprocess
import time

from . import config, core

LOG_NAME = "saani-ci.json"


def cfg():
    return config.current()


# ---------------------------------------------------------------- pure functions

def month_used(usage_items, today):
    """Actions minutes billed in today's billing month, every repo of the account."""
    month = today.strftime("%Y-%m")
    return sum(float(i.get("quantity") or 0) for i in usage_items or []
               if i.get("product") == "actions" and str(i.get("date", "")).startswith(month) and i.get("unitType", "Minutes") == "Minutes")


def budget(used, today, free=None):
    """The month's state: minutes used and left, the share of the month passed, and whether spending is ahead of the month."""
    free = cfg().ci_free_minutes if free is None else free
    first = today.replace(day=1)
    nxt = (first + datetime.timedelta(days=32)).replace(day=1)
    days = (nxt - first).days
    passed = (today - first).days + 1
    left = max(0.0, free - used)
    return {"used": round(used), "free": free, "left": round(left), "days_left": days - passed,
            "share_passed": round(passed / days, 3), "share_used": round(min(used, free) / free, 3)}


def place(job, b, refused=False):
    """('github' or 'pc', why) for one job and the month's budget `b`. `refused`: GitHub refused a job this month."""
    c = cfg()
    spec = c.ci_jobs[job]
    cost = spec["minutes"]
    if refused or b["left"] <= 0:
        return "pc", "GitHub refuses jobs this month (free minutes used up or billing): the PC runs it"
    if b["left"] - cost < 0:
        return "pc", f"only {b['left']} minutes left, the job takes about {cost}"
    if spec.get("heavy"):
        if b["left"] - cost < c.ci_reserve_minutes:
            return "pc", f"{b['left']} minutes left: after this job ({cost}) less than the reserve of {c.ci_reserve_minutes} would stay"
        if b["share_used"] > b["share_passed"] + c.ci_pace_slack:
            return "pc", (f"the month is spending ahead of time ({b['share_used']:.0%} of the minutes used, {b['share_passed']:.0%} of the "
                          f"month passed): saved for later")
    return "github", f"{b['left']} minutes left, the job takes about {cost}"


def wanted_workflows(b, refused=False):
    """{workflow name: True to enable, False to disable} for the small workflows that start by themselves."""
    on = not refused and b["left"] >= 10
    return {spec["workflow"]: on for spec in cfg().ci_jobs.values() if "workflow" in spec}


def refused_run(jobs):
    """True when GitHub refused the run: it ended without any job running a step (quota out or billing). A run that ran and failed is a
    real failure, not a refusal."""
    if not jobs:
        return True
    return all(j.get("conclusion") in ("failure", "cancelled", "startup_failure") and not j.get("steps") for j in jobs)


def pick_run(runs, sha, since):
    """The workflow_dispatch run on commit `sha` created at or after `since` (ISO time), newest first; None if not there yet."""
    for r in sorted(runs or [], key=lambda r: r.get("createdAt", ""), reverse=True):
        if r.get("headSha") == sha and r.get("event") == "workflow_dispatch" and r.get("createdAt", "") >= since:
            return r
    return None


def ci_folder(main, sha):
    """The temporary worktree of the PC's CI for commit `sha`, beside the workers' worktrees."""
    return cfg().wt_dir / f"ci-{sha[:12]}"


# ---------------------------------------------------------------- git and gh

class Run:
    def __init__(self, dry):
        self.dry = dry

    def read(self, *cmd, check=True):
        r = subprocess.run(cmd, cwd=cfg().root, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        if check and r.returncode:
            raise SystemExit(f"failed: {' '.join(cmd)}\n{r.stderr.strip()}")
        return r.stdout.strip()

    def json(self, *cmd):
        out = self.read(*cmd)
        return json.loads(out) if out else None

    def write(self, *cmd):
        if self.dry:
            print("dry-run: " + " ".join(cmd))
            return ""
        return self.read(*cmd)


def account():
    return cfg().repo.split("/")[0]


def current_budget(run, today=None):
    today = today or datetime.date.today()
    data = run.json("gh", "api", f"users/{account()}/settings/billing/usage") or {}
    return budget(month_used(data.get("usageItems"), today), today)


def refused_this_month(run, today=None):
    """True when the newest CI workflow run of this month was refused (no step ran)."""
    c = cfg()
    today = today or datetime.date.today()
    runs = run.json("gh", "run", "list", "--repo", c.repo, "--workflow", c.ci_workflow, "--limit", "1", "--json", "databaseId,createdAt") or []
    if not runs or not runs[0]["createdAt"].startswith(today.strftime("%Y-%m")):
        return False
    jobs = (run.json("gh", "run", "view", str(runs[0]["databaseId"]), "--repo", c.repo, "--json", "jobs") or {}).get("jobs", [])
    return refused_run(jobs)


def log(entry):
    path = cfg().root / "build" / LOG_NAME
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    rows.append({"date": datetime.datetime.now().isoformat(timespec="seconds"), **entry})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows[-500:], indent=1), encoding="utf-8")


def set_status(run, sha, state, description, url=None):
    c = cfg()
    args = ["gh", "api", "-X", "POST", f"repos/{c.repo}/statuses/{sha}", "-f", f"state={state}", "-f", f"context={c.ci_status_context}",
            "-f", f"description={description[:139]}"]
    if url:
        args += ["-f", f"target_url={url}"]
    run.write(*args)


def on_github(run, sha, ref):
    """Start the CI workflow on `ref` and wait for it. Returns ('passed'|'failed'|'refused', url, minutes)."""
    c = cfg()
    since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run.write("gh", "workflow", "run", c.ci_workflow, "--repo", c.repo, "--ref", ref)
    if run.dry:
        return "passed", None, 0
    found = None
    for _ in range(60):                                    # the run appears within seconds; wait at most 5 minutes
        time.sleep(5)
        found = pick_run(run.json("gh", "run", "list", "--repo", c.repo, "--workflow", c.ci_workflow, "--limit", "10",
                                  "--json", "databaseId,headSha,event,createdAt,url"), sha, since)
        if found:
            break
    if not found:
        return "refused", None, 0
    start = time.monotonic()
    subprocess.run(["gh", "run", "watch", str(found["databaseId"]), "--repo", c.repo, "--interval", "30"], cwd=c.root,
                   stdin=subprocess.DEVNULL)
    view = run.json("gh", "run", "view", str(found["databaseId"]), "--repo", c.repo, "--json", "conclusion,jobs") or {}
    minutes = math.ceil((time.monotonic() - start) / 60)
    if refused_run(view.get("jobs")):
        return "refused", found.get("url"), minutes
    return ("passed" if view.get("conclusion") == "success" else "failed"), found.get("url"), minutes


def on_pc(sha):
    """The checks, then the full run, on commit `sha` itself: a detached temporary worktree, removed after (the checkout the command runs
    from may hold another branch, so it is never the one tested). The full run waits for its turn in the test queue as kind `full`.
    Returns ('passed'|'failed', minutes)."""
    c = cfg()
    if not c.full:
        raise SystemExit("no [tests] full command in saani.toml: the PC cannot run the CI")
    start = time.monotonic()
    folder = ci_folder(core.main_checkout(), sha)
    if folder.exists():
        core.git("worktree", "remove", "--force", str(folder), check=False)
    folder.parent.mkdir(parents=True, exist_ok=True)
    core.git("worktree", "add", "--detach", str(folder), sha)
    try:
        (folder / "build").mkdir(exist_ok=True)
        report = core.latest_timing_report()
        if report:
            shutil.copy2(report, folder / "build" / report.name)
        print(f"testing {sha[:7]} in {folder}", flush=True)
        ok = subprocess.run(c.command("check"), cwd=folder).returncode == 0 if c.check else True
        ok = ok and core.queued_run("full", c.command("full"), folder) == 0
        return ("passed" if ok else "failed"), math.ceil((time.monotonic() - start) / 60)
    finally:
        core.git("worktree", "remove", "--force", str(folder), check=False)


def cmd_status(run):
    c = cfg()
    b = current_budget(run)
    refused = refused_this_month(run)
    print(f"Actions minutes this month: {b['used']} used of {b['free']} free, {b['left']} left, {b['days_left']} days left "
          f"({b['share_used']:.0%} used, {b['share_passed']:.0%} of the month passed); reserve {c.ci_reserve_minutes}"
          + ("; GitHub refused the last CI run" if refused else ""))
    for job, spec in c.ci_jobs.items():
        where, why = place(job, b, refused)
        print(f"  {job:14s} {spec['minutes']:3d} min  -> {where:6s}  {why}")
    return 0


def cmd_sync(run):
    c = cfg()
    b = current_budget(run)
    refused = refused_this_month(run)
    states = {w["name"]: w["state"] for w in run.json("gh", "workflow", "list", "--repo", c.repo, "--all", "--json", "name,state") or []}
    for name, on in wanted_workflows(b, refused).items():
        now_on = states.get(name) == "active"
        if on != now_on:
            run.write("gh", "workflow", "enable" if on else "disable", name, "--repo", c.repo)
            log({"job": "sync", "workflow": name, "enabled": on, "minutes_left": b["left"]})
        print(f"{name}: {'on' if on else 'off'}" + (" (changed)" if on != now_on else ""))
    return 0


def cmd_pr(run, head=None):
    """The CI of the open `head` -> production pull request: the integration branch, or a hotfix branch, same rule and same status."""
    c = cfg()
    head = head or c.integration
    prs = run.json("gh", "pr", "list", "--repo", c.repo, "--base", c.production, "--head", head, "--state", "open",
                   "--json", "number,headRefOid") or []
    if not prs:
        raise SystemExit(f"no open {head} -> {c.production} pull request")
    number, sha = prs[0]["number"], prs[0]["headRefOid"]
    if run.read("git", "rev-parse", f"origin/{head}", check=False) != sha:
        run.read("git", "fetch", "--quiet", "origin")
    b = current_budget(run)
    refused = refused_this_month(run)
    job = "pr-ci" if "pr-ci" in c.ci_jobs else next(iter(c.ci_jobs))
    where, why = place(job, b, refused)
    print(f"pull request #{number} at {sha[:7]}: {where} ({why})", flush=True)
    url, minutes = None, 0
    if where == "github":
        set_status(run, sha, "pending", "running on GitHub Actions")
        result, url, minutes = on_github(run, sha, head)
        if result == "refused":
            print("GitHub refused the run: the PC runs it", flush=True)
            where, why = "pc", "GitHub refused the run (fallback)"
    if where == "pc":
        set_status(run, sha, "pending", "running on the owner's PC")
        result, minutes = ("passed", 0) if run.dry else on_pc(sha)
    state = "success" if result == "passed" else "failure"
    set_status(run, sha, state, f"{result} on {'GitHub Actions' if where == 'github' else 'the PC'} in {minutes} min ({why})", url)
    log({"job": job, "pr": number, "sha": sha, "place": where, "why": why, "result": result, "minutes": minutes,
         "minutes_left_before": b["left"]})
    print(f"{c.ci_status_context} on {sha[:7]}: {state}")
    return 0 if state == "success" else 1


def cmd_merged(run, number):
    """The relabel after a merge: GitHub's workflow runs by itself when it is on (sync); otherwise, or when it was refused, here."""
    c = cfg()
    relabel = c.ci_jobs.get("relabel", {})
    states = {w["name"]: w["state"] for w in run.json("gh", "workflow", "list", "--repo", c.repo, "--all", "--json", "name,state") or []}
    if relabel.get("workflow") and states.get(relabel["workflow"]) == "active":
        runs = run.json("gh", "run", "list", "--repo", c.repo, "--workflow", relabel["workflow"], "--limit", "5",
                        "--json", "databaseId,conclusion,displayTitle") or []
        ok = [r for r in runs if r.get("conclusion") == "success"]
        if ok:
            print(f"the relabel ran on GitHub (run {ok[0]['databaseId']}): nothing to do here")
            log({"job": "relabel", "pr": number, "place": "github", "result": "passed"})
            return 0
    if not c.relabel:
        print(f"the relabel did not run on GitHub and no [ci] relabel command is set: swap {c.labels['in_dev']} for "
              f"{c.labels['in_main']} by hand on the issues of #{number}")
        return 1
    print("the relabel did not run on GitHub: running it here", flush=True)
    command = [*c.command("relabel"), str(number)]
    if run.dry:
        print("dry-run: " + " ".join(command))
        return 0
    token = run.read("gh", "auth", "token")
    code = subprocess.run(command, cwd=c.root, env={**os.environ, "GH_TOKEN": token}).returncode
    log({"job": "relabel", "pr": number, "place": "pc", "result": "passed" if code == 0 else "failed"})
    return code


def run_command(args):
    run = Run(args.dry_run)
    c = cfg()
    if args.what == "merged":
        if not (args.job or "").isdigit():
            raise SystemExit("merged needs the pull request number")
        return cmd_merged(run, int(args.job))
    if args.what == "place":
        if args.job not in c.ci_jobs:
            raise SystemExit("place needs a job: " + ", ".join(c.ci_jobs))
        where, why = place(args.job, current_budget(run), refused_this_month(run))
        print(f"{where}: {why}")
        return 0
    if args.what == "pr":
        return cmd_pr(run, args.head)
    return {"status": cmd_status, "sync": cmd_sync}[args.what](run)


def add_parsers(sub):
    p = sub.add_parser("ci", help="hybrid CI: GitHub Actions within the free minutes, the PC for the rest")
    p.add_argument("what", choices=["status", "sync", "pr", "place", "merged"])
    p.add_argument("job", nargs="?", help="the job for place, the pull request number for merged")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--head", help="pr: the pull request's branch (default: the integration branch; a hotfix branch)")
    p.set_defaults(run=run_command)
