"""Saani's numbers over a window of days: what can be counted from the repository and the PC's logs, and a baseline.

    saani kpis                                   # the last 14 days
    saani kpis --since 2026-01-01 --until 2026-01-14 --baseline
    saani kpis --json

Counted here:
- commits on origin/<integration> (no merges) and the issues they name (`refs #n`), per day: "issues landed per day";
- landings (build/saani-landings.jsonl of the main checkout, written by `saani land`): landed, stopped, the ones that met a conflict,
  conflicts per landing, and the conflicts by file;
- test jobs (build/saani-tests.jsonl, the test queue): per kind, jobs, median wait and median run;
- tokens and estimated cost per issue, role and model, from Claude Code's transcripts (saani/cost.py).

Not counted (no machine record; the report says "not measured" and where to take them from): the owner's waiting time and interventions.
`--baseline` adds the window of the same length that ends where this one starts (git history only: the logs start
with Saani).
"""
import argparse
import collections
import datetime
import json
import re
import statistics
import subprocess

from . import config, core, cost, queue

REF = re.compile(r"\brefs?\s+((?:#\d+[,\s]*(?:and\s+)?)+)", re.IGNORECASE)
NUMBER = re.compile(r"#(\d+)")
GENERATED_COMMIT = re.compile(r"^Generated files rebuilt", re.IGNORECASE)
NOT_MEASURED = {"owner_waiting_time": "the map's 'Waiting for you' zone and the issue comments, by hand",
                "owner_interventions": "the long-run reports and the chat, by hand"}


# ---------------------------------------------------------------- pure functions (tested)

def issues_in(message):
    """Issue numbers a commit message names with `refs #n` (also `refs #1, #2 and #3`)."""
    out = []
    for group in REF.findall(message or ""):
        for n in NUMBER.findall(group):
            if int(n) not in out:
                out.append(int(n))
    return out


def in_window(stamp, since, until):
    """True when an ISO date or time falls on a day from `since` to `until`, both included."""
    try:
        day = datetime.date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return False
    return since <= day <= until


def commit_kpis(commits, since, until, today=None):
    """commits: [{"date": "YYYY-MM-DD...", "subject": ..., "body": ...}] -> counts per window and per day. The days of a window
    that ends after `today` are only the days elapsed, so a window still running is not diluted by days to come."""
    mine = [c for c in commits if in_window(c.get("date"), since, until) and not GENERATED_COMMIT.match(c.get("subject", ""))]
    per_day = collections.defaultdict(set)
    issues = set()
    for c in mine:
        found = issues_in(c.get("subject", "") + "\n" + c.get("body", ""))
        issues.update(found)
        per_day[str(c["date"])[:10]].update(found)
    days = (min(until, today) if today else until).toordinal() - since.toordinal() + 1
    return {"commits": len(mine), "issues": len(issues), "days": days,
            "issues_per_day": round(sum(len(v) for v in per_day.values()) / days, 2) if days > 0 else 0.0,
            "per_day": {d: len(v) for d, v in sorted(per_day.items())}}


def landing_kpis(rows, since, until):
    """Rows of the landings log inside the window: landed, stopped, conflicts per landing and by file."""
    mine = [r for r in rows if in_window(r.get("time"), since, until)]
    done = [r for r in mine if r.get("result") == "landed"]
    by_file = collections.Counter(f for r in done for f in (r.get("conflicts") or []))
    seconds = [r["seconds"] for r in done if isinstance(r.get("seconds"), (int, float))]
    return {"landed": len(done), "stopped": len(mine) - len(done),
            "with_conflicts": len([r for r in done if r.get("conflicts")]),
            "conflicts_per_landing": round(sum(by_file.values()) / len(done), 2) if done else 0.0,
            "by_file": dict(by_file.most_common()),
            "auto_resolved": sum(len(r.get("auto_resolved") or []) for r in done),
            "median_seconds": round(statistics.median(seconds)) if seconds else None}


def test_kpis(rows, since, until):
    """Rows of the test queue log inside the window -> per kind: jobs, failed, median wait and run in seconds."""
    out = {}
    for r in rows:
        if not in_window(r.get("time"), since, until):
            continue
        k = out.setdefault(r.get("kind", "?"), {"jobs": 0, "failed": 0, "waits": [], "runs": []})
        k["jobs"] += 1
        k["failed"] += 1 if r.get("code") not in (0, None) else 0
        k["waits"].append(r.get("wait", 0))
        k["runs"].append(r.get("run", 0))
    return {kind: {"jobs": k["jobs"], "failed": k["failed"], "median_wait": round(statistics.median(k["waits"])),
                   "median_run": round(statistics.median(k["runs"]))} for kind, k in sorted(out.items())}


def baseline_window(since, until):
    """The window of the same length that ends the day before `since`."""
    days = (until - since).days + 1
    return since - datetime.timedelta(days=days), since - datetime.timedelta(days=1)


def report(commits, landings, tests, since, until, baseline=False, today=None, tokens=None):
    out = {"window": {"since": since.isoformat(), "until": until.isoformat()},
           "git": commit_kpis(commits, since, until, today),
           "landings": landing_kpis(landings, since, until),
           "tests": test_kpis(tests, since, until),
           "not_measured": NOT_MEASURED}
    if tokens is not None:
        out["tokens"] = tokens
    if baseline:
        b_since, b_until = baseline_window(since, until)
        out["baseline"] = {"window": {"since": b_since.isoformat(), "until": b_until.isoformat()},
                           "git": commit_kpis(commits, b_since, b_until)}
    return out


def text(r):
    g, land = r["git"], r["landings"]
    lines = [f"Saani KPIs, {r['window']['since']} to {r['window']['until']} ({g['days']} days elapsed)",
             f"  dev: {g['commits']} commits, {g['issues']} issues, {g['issues_per_day']} issues per day (each issue once per day it moved)",
             f"  landings: {land['landed']} landed, {land['stopped']} stopped, {land['with_conflicts']} met a conflict, "
             f"{land['conflicts_per_landing']} conflicts per landing, median {land['median_seconds']} s",
             "  conflicts by file: " + (", ".join(f"{f} {n}" for f, n in land["by_file"].items()) or "none")]
    for kind, k in r["tests"].items():
        lines.append(f"  tests {kind}: {k['jobs']} jobs ({k['failed']} failed), median wait {k['median_wait']} s, run {k['median_run']} s")
    if not r["tests"]:
        lines.append("  tests: no test job recorded in the window")
    for name, where in r["not_measured"].items():
        lines.append(f"  {name.replace('_', ' ')}: not measured ({where})")
    if "tokens" in r:
        lines.append(cost.text(r["tokens"]))
    if "baseline" in r:
        b = r["baseline"]
        lines.append(f"Baseline, {b['window']['since']} to {b['window']['until']}: {b['git']['commits']} commits, "
                     f"{b['git']['issues']} issues, {b['git']['issues_per_day']} issues per day")
    return "\n".join(lines)


# ---------------------------------------------------------------- files and git

def dev_commits(ref, since=None):
    sep, end = "\x1f", "\x1e"
    args = ["git", "log", "--no-merges", f"--format=%cs{sep}%s{sep}%b{end}", ref]
    if since:
        args.insert(3, f"--since={since.isoformat()}")
    out = subprocess.run(args, cwd=config.current().root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise SystemExit(f"git log {ref} failed: {out.stderr.strip()}")
    commits = []
    for item in out.stdout.split(end):
        parts = item.strip("\n").split(sep)
        if len(parts) == 3:
            commits.append({"date": parts[0], "subject": parts[1], "body": parts[2]})
    return commits


def run_command(a):
    c = config.current()
    until = a.until or datetime.date.today()
    since = a.since or until - datetime.timedelta(days=13)
    if until < since:
        raise SystemExit("--until is before --since")
    first = baseline_window(since, until)[0] if a.baseline else since
    build = core.main_checkout() / "build"
    r = report(dev_commits(a.ref or f"origin/{c.integration}", first - datetime.timedelta(days=1)), queue.rows(build / core.LANDINGS),
               queue.rows(build / core.TESTS_LOG), since, until, a.baseline, datetime.date.today(), cost.read(c, since, until))
    print(json.dumps(r, indent=2) if a.json else text(r))
    return 0


def add_parsers(sub):
    p = sub.add_parser("kpis", help="the numbers over a window of days, with a baseline")
    p.add_argument("--since", type=datetime.date.fromisoformat)
    p.add_argument("--until", type=datetime.date.fromisoformat)
    p.add_argument("--baseline", action="store_true", help="add the window of the same length just before")
    p.add_argument("--ref", help="default: origin/<integration>")
    p.add_argument("--json", action="store_true")
    p.set_defaults(run=run_command)
