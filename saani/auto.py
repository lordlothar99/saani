"""An auto run's choice of work (docs/SAANI.md "An auto run"): `saani auto pick` says which open issues the run may take now, in order,
how many may run side by side, and why each of the others is left. A script, no model. Pure functions first (tests/test_auto.py); the
reading of GitHub, git and the machine is in `pick`. Standard library only.
"""
import ctypes
import datetime
import json
import math
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time

from . import config, core, glance, queue, status

MACHINE_PARALLEL = 2      # the most a machine-sized run (`[auto] parallel = 0`, the default) keeps in flight; a fixed n goes up to config.MAX_PARALLEL
GB_PER_ISSUE = 4          # the free memory below which a run drops to 1 issue; each running issue gives this much back
CPUS_PER_ISSUE = 4
ISSUE_LIMIT = 500         # open issues read
GIT_TIMEOUT = 60
GH_TIMEOUT = 60
PATH_TOKEN = re.compile(r"`([^`\s]+)`")
FILE_NAME = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,4}$")


def cfg():
    return config.current()


# ---------------------------------------------------------------- sizing

def parallel_size(configured, cpus, free_gb, running=0):
    """How many issues an auto run keeps in flight, per project (another project's run has its own slots). `configured` 0: auto-sized,
    min(2, cpus // 4), at least 1, and 1 when the free memory (the `running` issues' own memory given back) cannot hold even one issue
    (free_gb None: unknown, the cores alone decide); any n >= 1 is fixed, up to 10 (the machine is not asked). The memory is not divided per issue:
    other projects' worktrees use it too, and counting it would starve a second project below its own slots."""
    if configured and configured >= 1:
        return min(config.MAX_PARALLEL, int(configured))
    by_cpu = max(1, (cpus or 1) // CPUS_PER_ISSUE)
    short_of_ram = free_gb is not None and free_gb + running * GB_PER_ISSUE < GB_PER_ISSUE
    return min(MACHINE_PARALLEL, by_cpu, 1 if short_of_ram else MACHINE_PARALLEL)


def parallel_warning(parallel):
    """The warning for a run with more than `config.WARN_PARALLEL` slots (None otherwise): shown where the slots are shown."""
    if parallel <= config.WARN_PARALLEL:
        return None
    return (f"warning: {parallel} slots (more than {config.WARN_PARALLEL}): each slot is a coder, a reviewer and a test run side by side, "
            "so expect more tokens, more memory and more landing conflicts")


def free_ram_gb():
    """Free memory in GB from the standard library, or None when this system cannot say (macOS, an error)."""
    try:
        if sys.platform == "win32":
            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total_phys", ctypes.c_ulonglong),
                            ("avail_phys", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong), ("avail_ext", ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.avail_phys / 2 ** 30
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii", errors="replace") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / 2 ** 20
    except (OSError, ValueError, AttributeError):
        pass
    return None


def free_slots(parallel, running):
    """Issues that may start now: the size of the run less the issues already in worktrees, never below 0."""
    return max(0, parallel - running)


# ---------------------------------------------------------------- overlap

def issue_paths(body):
    """The paths an issue body names between backticks: a token with a / or a file extension (`saani/core.py`, `saani.toml`; a folder like `docs/` is ignored)."""
    out = []
    for token in PATH_TOKEN.findall(body or ""):
        t = token.strip(".,;:()").removeprefix("./")
        if not t or t.startswith(("-", "http", "#")) or not re.fullmatch(r"[\w./@+-]+", t):
            continue
        if t.endswith("/"):                                # a folder is too wide a claim: ignored
            continue
        if ("/" in t or FILE_NAME.search(t)) and t not in out:
            out.append(t)
    return out


def paths_overlap(a, b):
    """Best effort: the same path, or one inside the other when that one names a folder. A bare file name is a root-level file: it is
    the same only as that exact name (`saani.toml` is not `examples/saani.toml`)."""
    a, b = a.strip("/"), b.strip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def first_overlap(paths, others):
    """(path, other path) of the first overlap between two path lists, or None."""
    for p in paths:
        for q in others:
            if paths_overlap(p, q):
                return p, q
    return None


# ---------------------------------------------------------------- choice

def milestone_order(name):
    """Versions ascending (v1.2.0 before v1.10.0), then the other named milestones alphabetically, then no milestone."""
    if not name:
        return (2, (), "")
    m = re.match(r"v?(\d+(?:\.\d+)*)", name.strip(), re.I)
    return (0, tuple(int(x) for x in m.group(1).split(".")), "") if m else (1, (), name.lower())


def bug_types():
    """The issue Types whose branch prefix in [issues] types is `bug`; else the literal Bug."""
    return {t for t, prefix in cfg().types.items() if prefix == "bug"} or {"Bug"}


def order_key(issue):
    """Milestone (milestone_order), then Bug first, then the issue number."""
    return (milestone_order(issue.get("milestone")), 0 if issue.get("type") in bug_types() else 1, issue["number"])


def choose(issues, started, running, today, labels=None, types=None, only=None, branched=(), statuses=None):
    """Splits the open `issues` ([{number, title, labels, milestone, body, type}]) into those a run may take now and those it leaves.
    `statuses`: the repo's GitHub issue statuses (saani/status.py): every ready issue is taken except those in Backlog (no status, Todo and the
    others are eligible; also with `only`); None (the repo has none, or they are off): no status filter.
    `started`: issue numbers with a worktree folder; `branched`: with a worker branch only; `only`: take just these (set before the overlap pass); `running`: {issue number: [paths its worktree changed]}.
    Returns (eligible, skipped): eligible [{number, title, milestone, type, flags, decision}] (`decision`: the issue's latest `Decision`, #102) in the run's order, skipped [{number, reason}]."""
    labels = labels or cfg().labels
    types = cfg().types if types is None else types
    owner_labels = {labels["answer"], labels["act"], labels["idea"]}
    open_numbers = {i["number"] for i in issues}
    eligible, skipped, named = [], [], {}
    for i in sorted(issues, key=order_key):
        n, have, flags = i["number"], set(i.get("labels") or []), []
        g = glance.parse(i.get("body"))
        fit = (g.get("auto") or {}).get("fit")
        reason = None
        if labels["in_dev"] in have or labels["in_main"] in have:
            reason = "already on dev" if labels["in_dev"] in have else "already on main"
        elif n in started or n in running:
            reason = "already in a worktree"
        elif n in branched:
            reason = "has a worker branch"
        elif have & owner_labels:
            reason = "needs the owner or is an idea (label " + ", ".join(sorted(have & owner_labels)) + ")"
        elif statuses is not None and status.step_of(i.get("status"), statuses) == "backlog":
            reason = "in Backlog"
        elif statuses is not None and status.step_of(i.get("status"), statuses) in ("done", "closed"):
            reason = "status: " + i["status"]["name"] + " (open, but marked finished)"   # reopened with Done, or left open as Not planned
        elif fit == "no":
            reason = "Auto: no"
        elif glance.is_blocked(g, open_numbers, today):
            still = [b for b in (g.get("blocked_by") or {}).get("issues", []) if b in open_numbers]
            reason = "blocked by " + (", ".join(f"#{b}" for b in still) or "a date")
        elif (g.get("needs_owner") or {}).get("kind", "none") != "none":
            reason = "needs the owner: " + g["needs_owner"]["kind"]
        elif i.get("type") not in types:
            reason = "no Type"
        if reason:
            skipped.append({"number": n, "reason": reason})
            continue
        if fit is None:
            flags.append("no At a glance")
        elif fit == "partly":
            flags.append("partly")
        if only is not None and n not in only:
            continue
        paths = issue_paths(i.get("body"))
        hit = None
        for m, theirs in list(running.items()) + list(named.items()):
            found = first_overlap(paths, theirs)
            if found:
                hit = (m, found[0])
                break
        if hit:
            skipped.append({"number": n, "reason": f"overlaps #{hit[0]} ({hit[1]})"})
            continue
        named[n] = paths
        eligible.append({"number": n, "title": i.get("title", ""), "milestone": i.get("milestone"), "type": i.get("type"), "flags": flags,
                         "decision": i.get("decision")})
    return eligible, sorted(skipped, key=lambda s: s["number"])


# ---------------------------------------------------------------- reading the world

def gh(*args):
    """The output of `gh`; a failure (not installed, timeout, refused) stops the command with gh's own message, never an empty answer."""
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GH_TIMEOUT)
    except FileNotFoundError:
        raise SystemExit("saani auto pick: gh is not installed or not on the PATH")
    except subprocess.TimeoutExpired:
        raise SystemExit(f"saani auto pick: gh {' '.join(args[:2])} took more than {GH_TIMEOUT} s")
    if r.returncode != 0:
        raise SystemExit(f"saani auto pick: gh {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def read_types():
    """{issue number: its GitHub issue Type} of the open issues, from the REST API (`gh issue list` has no such field, #38)."""
    return read_types_and_statuses()[0]


def read_types_and_statuses():
    """({number: Type}, {number: {name, category}}) of the open issues, from one REST listing: `gh issue list` has neither field (#38,
    saani/status.py)."""
    out = gh("api", "--paginate", f"repos/{cfg().repo}/issues?state=open&per_page=100", "--jq",
             r'.[] | select(.pull_request | not) | "\(.number)\t\(.type.name // "")\t\(.status.name // "")\t\(.status.category // "")"')
    types, statuses = {}, {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        n = int(parts[0])
        if parts[1]:
            types[n] = parts[1]
        if len(parts) > 3 and parts[2]:
            statuses[n] = {"name": parts[2], "category": parts[3] or None}
    return types, statuses


def read_issues():
    """The open issues (up to ISSUE_LIMIT) with their body and Type."""
    try:
        rows = json.loads(gh("issue", "list", "--repo", cfg().repo, "--state", "open", "--limit", str(ISSUE_LIMIT), "--json",
                             "number,title,labels,milestone,body,comments") or "[]")
    except json.JSONDecodeError:
        raise SystemExit("saani auto pick: gh issue list gave an answer that is not JSON")
    types, statuses = read_types_and_statuses()
    from . import comments
    return [{"number": r["number"], "title": r["title"], "labels": [x["name"] for x in r.get("labels", [])],
             "milestone": (r.get("milestone") or {}).get("title"), "body": r.get("body") or "", "type": types.get(r["number"]),
             "status": statuses.get(r["number"]),
             "decision": comments.decision_of(comments.trusted_bodies(r.get("comments")))}
            for r in rows]


def started_issues(folders, branches):
    """(numbers with a `<n>-` worktree folder, numbers with a worker branch local or on origin)."""
    out = {int(m.group(1)) for f in folders if (m := re.match(r"(\d+)-", f))}
    branched = set()
    for b in branches:
        m = cfg().worker_branch.match(b.strip().removeprefix("* ").removeprefix("remotes/").removeprefix("origin/"))
        if m:
            branched.add(int(m.group(2)))
    return out, branched


def fetch():
    """`git fetch --prune` of origin with a time limit and no prompt; a failure is a warning (the answer may be a little old)."""
    try:
        r = subprocess.run(["git", "fetch", "-q", "--prune", "origin"], cwd=cfg().root, capture_output=True, text=True, timeout=GIT_TIMEOUT,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            print(f"saani auto pick: git fetch failed, branches may be out of date: {r.stderr.strip()}", file=sys.stderr)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"saani auto pick: git fetch not done ({type(e).__name__}), branches may be out of date", file=sys.stderr)


def running_issues():
    """{issue number: [paths changed in its worktree against the integration branch]} for each worker branch that has a worktree."""
    c, out = cfg(), {}
    for path, branch in core.worktrees():
        m = c.worker_branch.match(branch)
        if m and core.in_wt_dir(path):
            diff = core.git("diff", "--name-only", f"origin/{c.integration}...{branch}", check=False)
            out[int(m.group(2))] = diff.stdout.split() if diff.returncode == 0 else []
    return out


def pick_statuses():
    """The repo's statuses for the pick; a gh failure stops the pick (never a guess that would let Backlog issues through)."""
    try:
        return status.available(strict=True)
    except SystemExit as e:
        raise SystemExit(f"saani auto pick: {e}")


def run_started_issues():
    """The issues `saani start` started inside the run that is on (the journal's `started` lines of its id). Empty when no run is on: then
    every worktree is the owner's own."""
    try:
        rec = read_record()
    except SystemExit:
        rec = None
    return {int(r["issue"]) for r in read_journal(rec["id"]) if r.get("kind") == "started" and r.get("issue") is not None} if rec else set()


def run_parked_issues():
    """The issues the run that is on has parked (#110): the issue's latest `started`, `blocked` or `landed` journal line is `blocked`
    (`saani comment <n> blocked`). They keep their worktree but take no slot and are not offered again; a later `started` (the issue
    was started again) or `landed` clears it. Empty when no run is on."""
    try:
        rec = read_record()
    except SystemExit:
        rec = None
    last = {}
    for r in read_journal(rec["id"]) if rec else []:
        if r.get("issue") is not None and r.get("kind") in ("started", "blocked", "landed"):
            last[int(r["issue"])] = r["kind"]
    return {n for n, kind in last.items() if kind == "blocked"}


def pick_note(got):
    """One sentence for why a pick gives nothing to start (no slot, or nothing eligible), else None."""
    if got.get("eligible") and got["slots"] > 0:
        return None
    if got["slots"] <= 0:
        mine = ", ".join(f"#{n}" for n in got["running"])
        return f"no slot: this run's issues {mine} are in flight ({got['parallel']} allowed)"
    n = len(got["skipped"])
    return f"nothing eligible ({n} {'issue' if n == 1 else 'issues'} skipped)"


def pick(today=None, only=None):
    """The whole answer of `saani auto pick` as a dict: slots, parallel, running (this run's issues), owner_running (the owner's own
    worktrees: they never take a slot of the run), eligible, skipped."""
    c = cfg()
    fetch()
    running = running_issues()
    mine = run_started_issues()
    folders = [p.name for p in c.wt_dir.iterdir()] if c.wt_dir.is_dir() else []
    branches = core.git("branch", "-a", "--format=%(refname:short)", check=False).stdout.splitlines()
    issues = read_issues()
    for i in issues:                                  # a running issue's own named paths count too, as well as what it changed so far
        if i["number"] in running:
            running[i["number"]] = running[i["number"]] + issue_paths(i["body"])
    started, branched = started_issues(folders, branches)
    eligible, skipped = choose(issues, started, running, today or datetime.date.today().isoformat(), only=set(only) if only else None,
                               branched=branched, statuses=pick_statuses())
    # The owner's worktrees stay in `running` for the overlap rule above (their files still count) but not for the slots: the run's slots
    # are [auto] parallel less the issues this run started. The memory check reads the machine's free memory, which already shows
    # the owner's work, so the machine is not overloaded by this choice.
    # An issue the run parked (blocked) keeps its worktree (and its files still count above) but takes no slot, and is not offered again.
    parked = run_parked_issues() & mine
    own = sorted(n for n in running if n in mine and n not in parked)
    owner = sorted(n for n in running if n not in mine)
    if parked:
        eligible = [e for e in eligible if e["number"] not in parked]
        skipped = sorted([s for s in skipped if s["number"] not in parked]
                         + [{"number": n, "reason": "parked: blocked in this run"} for n in parked
                            if any(i["number"] == n for i in issues)], key=lambda s: s["number"])
    parallel = parallel_size(c.parallel, os.cpu_count(), free_ram_gb(), len(own))
    got = {"slots": free_slots(parallel, len(own)), "parallel": parallel, "running": own, "owner_running": owner,
           "parked": sorted(parked), "eligible": eligible, "skipped": skipped}
    warning = parallel_warning(parallel)
    if warning:
        got["warning"] = warning
    return got


WAIT_EVERY_MINUTES = 3    # `pick --wait` looks again this often (GitHub and git are read each time, no model)


def stop_rules_hit():
    """[(rule, value, limit)] of `saani auto status --check`'s stop rules for the run that is on, each journalled as a `stop` line like
    status --check does. When the record is gone while the guard still holds a run of this repository (the owner ran `auto off`): the
    rule `record missing` (nothing to journal into). None when no run is on (a pick outside a run has no stop rules)."""
    rec = read_record()
    if not rec:
        return [("record missing", 0, 0)] if any(guard_live(s) for s, _ in own_entries()) else []
    hit = reached(rec)
    for k, value, limit in hit:
        shown = f"{value:.1f}" if k == "hours" else str(value)
        journal(None, "stop", note=f"{LIMIT_WORDS[k]} reached ({shown} of {limit})")
    return hit


def wait_pick(minutes, only=None, every=WAIT_EVERY_MINUTES, pick_fn=None, stop_fn=None, clock=None, sleep=None):
    """`saani auto pick --wait <minutes>`: `pick`'s answer as soon as an issue is eligible; else, after `minutes`, the last answer (an
    empty `eligible`); sooner when a stop rule of the run is reached (then `stop` lists the rules and `eligible` is empty). It looks
    again every `every` minutes and spends no model. `clock` (seconds) and `sleep` are injected by the tests."""
    pick_fn, stop_fn = pick_fn or pick, stop_fn or stop_rules_hit
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    start = clock()
    deadline = start + minutes * 60
    while True:
        hit = stop_fn()
        if hit:
            got = pick_fn(only=only)
            got["eligible"] = []
            got["stop"] = [{"rule": k, "value": v, "limit": limit} for k, v, limit in hit]
            break
        got = pick_fn(only=only)
        remaining = deadline - clock()
        if (got["eligible"] and got["slots"] > 0) or remaining <= 0:       # eligible issues with no free slot are no news: keep waiting
            break
        sleep(min(every * 60, remaining))
        if clock() >= deadline:                 # no pick that could end past the deadline (the caller's Bash call has a timeout)
            break
    got["waited_minutes"] = round((clock() - start) / 60, 1)
    got["keep_on_quiet_minutes"] = cfg().keep_on_quiet_minutes
    return got


def cmd_pick(args):
    wait = getattr(args, "wait", None)
    if wait is not None:
        if wait is True or not math.isfinite(wait) or wait <= 0:
            raise SystemExit("saani auto pick: --wait needs the minutes to wait (a number more than 0), e.g. --wait 8")
        got = wait_pick(wait, only=args.only)
        args.json = True                      # the same JSON as `pick --json`, plus waited_minutes, keep_on_quiet_minutes and, at a stop rule, stop
    else:
        got = pick(only=args.only)
    note = None if got.get("stop") else pick_note(got)
    if note:
        got["note"] = note
        journal_pick(note)                    # inside a run only: the report says why a run that did nothing stopped
    if args.limit is not None:
        got["eligible"] = got["eligible"][:max(0, args.limit)]
    if args.json:
        print(json.dumps(got, indent=1, ensure_ascii=False))
        return 0
    running = ", ".join(f"#{n}" for n in got["running"]) or "none"
    print(f"slots: {got['slots']} free of {got['parallel']} (running: {running})")
    if got.get("warning"):
        print(got["warning"])
    if got.get("owner_running"):
        owner = ", ".join(f"#{n}" for n in got["owner_running"])
        print(f"the owner's worktrees {owner} are in flight: they take no slot (slots = [auto] parallel less this run's own running issues), "
              "but a file they change is not given to another issue")
    if got.get("parked"):
        print("parked (blocked, worktree kept): " + ", ".join(f"#{n}" for n in got["parked"]) + ": they take no slot and are not offered again")
    if note:
        print(note)
    print(f"eligible ({len(got['eligible'])}), in the run's order:")
    for e in got["eligible"]:
        extra = [e["type"] or "no Type", e["milestone"] or "no milestone"] + e["flags"]
        print(f"  #{e['number']} {e['title']} [{'; '.join(extra)}]")
        if e.get("decision"):
            print(f"    Decision: {e['decision']}")
    if not got["eligible"]:
        print("  none")
    print(f"skipped ({len(got['skipped'])}):")
    for s in got["skipped"]:
        print(f"  #{s['number']} {s['reason']}")
    return 0


# ---------------------------------------------------------------- saani auto wait: keep the headless session's turn open (#111)

WAIT_DEFAULT_MINUTES = 8          # `auto wait` without --minutes (a Bash call lasts 10 minutes at most)
WAIT_MAX_MINUTES = 9.5            # the most `auto wait --minutes` accepts
WAIT_LOOK_SECONDS = 15           # `auto wait` looks at the run's branches this often (local git only, no model, no gh)


def flight_marks(rec_id):
    """{issue: (worktree path, the branch's commit or None)} of the issues this run started whose worker worktree still exists."""
    c = cfg()
    started = {int(r["issue"]) for r in read_journal(rec_id) if r.get("kind") == "started" and r.get("issue") is not None}
    out = {}
    for path, branch in core.worktrees():
        m = c.worker_branch.match(branch)
        if m and core.in_wt_dir(path) and int(m.group(2)) in started:
            head = core.git("rev-parse", branch, check=False)
            out[int(m.group(2))] = (str(path), head.stdout.strip() or None if head.returncode == 0 else None)
    return out


def wait_coder(minutes, rec_id, marks_fn=None, stop_fn=None, every=WAIT_LOOK_SECONDS, clock=None, sleep=None):
    """`saani auto wait`: blocks, with no model, until a worktree of this run gets a new commit (its coder handed back) or goes away, a
    stop rule is reached, or `minutes` pass; at once when nothing of this run is in flight. Returns {"in_flight": [issues now],
    "finished": [issues that handed back], "reason": "none in flight" | "handed back" | "stop" | "time limit", "waited_minutes", "stop"?}.
    It starts no loop of its own in the session: the loop is here, which is why the guard allows this command."""
    marks_fn = marks_fn or (lambda: flight_marks(rec_id))
    stop_fn = stop_fn or stop_rules_hit
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    start = clock()
    deadline = start + minutes * 60
    first = marks_fn()

    def done(reason, now, **extra):
        finished = sorted(n for n, m in first.items() if n not in now or now[n][1] != m[1])
        return {"reason": reason, "in_flight": sorted(n for n in now if n not in finished), "finished": finished,
                "waited_minutes": round((clock() - start) / 60, 1), **extra}
    if not first:
        return done("none in flight", {})
    while True:
        hit = stop_fn()
        if hit:
            return done("stop", marks_fn(), stop=[{"rule": k, "value": v, "limit": limit} for k, v, limit in hit])
        now = marks_fn()
        if any(n not in now or now[n][1] != m[1] for n, m in first.items()):
            return done("handed back", now)
        remaining = deadline - clock()
        if remaining <= 0:
            return done("time limit", now)
        sleep(min(every, remaining))


def cmd_wait(args):
    minutes = getattr(args, "minutes", None)
    minutes = WAIT_DEFAULT_MINUTES if minutes is None else minutes
    if not math.isfinite(minutes) or minutes <= 0:
        raise SystemExit("saani auto wait: --minutes needs a number more than 0, e.g. --minutes 8")
    if minutes > WAIT_MAX_MINUTES:
        raise SystemExit(f"saani auto wait: --minutes is at most {WAIT_MAX_MINUTES:g} (a Bash call lasts 10 minutes at most)")
    rec = read_record()
    if not rec:
        raise SystemExit("saani auto wait: no run is on")
    print(json.dumps(wait_coder(minutes, rec["id"]), indent=1, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- the run: saani auto on | off | status | report

RECORD = "saani-auto-run.json"      # in the repository's shared git folder: one run per repository, git never tracks it
LIMITS = {"refusals": 40, "same_reason": 10, "hours": 10}   # the stop rules, in one place (docs/SAANI.md, An auto run); a run copies them at `on`
LIMIT_WORDS = {"refusals": "refusals in total", "same_reason": "refusals for the same reason in a row", "hours": "hours"}
EXIT_LIMIT = 3            # `saani auto status --check`: a limit is reached, stop the run
MAX_TIMELINE = 80
REFS = re.compile(r"\brefs #(\d+)", re.I)
LEFT_MARKS = ("\u2753", "\u26d4")   # a comment that starts with the mark of the question or blocked kind (after a Markdown heading mark) left the issue waiting


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(moment):
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(text):
    return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def record_path():
    """`<shared git folder>/saani-auto-run.json`, found without running git (the guard's own reader); None outside a repository."""
    from . import guard
    dirs = guard.git_dirs(cfg().root)
    return pathlib.Path(dirs[1]) / RECORD if dirs else None


def read_record():
    """The run's record ({id, started, folder, deploy, limits}), or None when no run is on."""
    path = record_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") and data.get("started") else None


def build_dir():
    path = cfg().root / "build"
    path.mkdir(exist_ok=True)
    return path


def journal_path(run_id):
    return build_dir() / f"saani-auto-{run_id}.jsonl"


def journal(issue, kind, now=None, note=None):
    """While a run is on, one JSON line {time, issue, kind[, note]} in build/saani-auto-<id>.jsonl (the report's timeline). Nothing otherwise,
    and never an error: the journal never undoes the work it notes."""
    try:
        rec = read_record()
        if rec:
            row = {"time": stamp(now or utc_now()), "issue": int(issue) if issue is not None else None, "kind": str(kind)}
            if note:
                row["note"] = str(note)
            with open(journal_path(rec["id"]), "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
    except (Exception, SystemExit):
        pass


def journal_pick(note):
    """A `pick` journal line with the reason a pick gave nothing to start; not again while it is the same as the last such line."""
    try:
        rec = read_record()
        if rec:
            last = [r for r in read_journal(rec["id"]) if r["kind"] == "pick"]
            if not last or last[-1].get("note") != note:
                journal(None, "pick", note=note)
    except (Exception, SystemExit):
        pass


def read_journal(run_id):
    out = []
    try:
        with open(journal_path(run_id), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and "time" in row and "kind" in row:
                    out.append(row)
    except OSError:
        pass
    return out


# ---- the project and the refusals (the guard's registry and log are the source; this reads them, never writes them)

def project_of(entry):
    """The normalized main checkout an entry of the guard's registry was made for."""
    from . import guard
    return guard.norm(entry.get("main") or "") if isinstance(entry, dict) and entry.get("main") else None


def entries():
    """[(session id, entry)] of the guard's registry (over marks included: the guard still refuses their calls), read only."""
    from . import guard
    out = []
    try:
        names = [x for x in os.listdir(guard.registry()) if x.endswith((".json", guard.OVER))]
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(guard.registry(), name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append((name[:-5], data))
    return out


def here():
    from . import guard
    return guard.norm(cfg().root)


def own_entries(run_id=None):
    """The registry's entries made for this repository's main checkout (and this run, when `run_id` is given)."""
    return [(s, e) for s, e in entries() if project_of(e) == here() and (run_id is None or e.get("id") == run_id)]


def refusal_rows(run_id):
    """The run's refusals in order. A line that names its project (`project`) counts only for this repository. The guard's lines name
    the run only (a project key is for the guard to add): such a line counts unless the registry holds entries of that run id for other
    projects and none for this one (another repository's run of the same id)."""
    from . import guard
    foreign = any(e.get("id") == run_id for _, e in entries()) and not own_entries(run_id)
    out = []
    try:
        with open(os.path.join(guard.registry(), guard.REFUSALS), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("run") != run_id:
                    continue
                if row.get("project") is not None:
                    if guard.norm(str(row["project"])) != here():
                        continue
                elif foreign:
                    continue
                out.append(row)
    except OSError:
        pass
    return out


def refusal_reasons(run_id):
    """The reasons of the run's refusals in the order they happened."""
    return [str(r.get("reason", "?")) for r in refusal_rows(run_id)]


def refusals_by_reason(run_id):
    by = {}
    for reason in refusal_reasons(run_id):
        by[reason] = by.get(reason, 0) + 1
    return by


def longest_run(reasons):
    """The most times one reason came one after the other."""
    best = streak = 0
    for i, r in enumerate(reasons):
        streak = streak + 1 if i and reasons[i - 1] == r else 1
        best = max(best, streak)
    return best


def counts(rec, now=None):
    """What each stop rule measures now: {refusals, same_reason, hours}, from this project's refusals of this run."""
    reasons = refusal_reasons(rec["id"])
    return {"refusals": len(reasons), "same_reason": longest_run(reasons),
            "hours": ((now or utc_now()) - parse_stamp(rec["started"])).total_seconds() / 3600}


def reached(rec, now=None):
    """[(rule, value, limit)] of the stop rules that are reached (value >= limit)."""
    limits = {**LIMITS, **(rec.get("limits") or {})}
    got = counts(rec, now)
    return [(k, got[k], limits[k]) for k in LIMITS if got[k] >= limits[k]]


def duration(seconds):
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d} min"


def valid_id(text):
    from . import guard
    return bool(guard.RUN_ID.fullmatch(str(text)))


# ---- on

PENDING = ".pending"


def mark_pending(run_id):
    """Leaves `<run_id>.pending` in the guard's registry (~/.saani/auto) before `saani auto on` prints its marker line. bin/saani starts
    Python for the guard hook only when the registry holds an entry, an over mark or such a mark, so the PostToolUse hook of this call
    gets to register the session. The mark turns nothing on: the guard's rules for the switch are unchanged. It stays until `saani auto
    off` (end_guard_entries; with no record, clear_stale_pending). The registry gets the refusal log too, so it is never empty (bin/saani reads an empty registry as one it
    cannot list)."""
    from . import guard
    folder = guard.registry()
    try:
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, guard.REFUSALS), "a", encoding="utf-8").close()
        with open(os.path.join(folder, run_id + PENDING), "w", encoding="utf-8") as f:
            f.write("saani auto on: waiting for the guard's PostToolUse hook; removed by saani auto off\n")
    except OSError as e:
        raise SystemExit(f"saani auto on: cannot write the guard's mark in {folder}: {e}")


def clear_pending(run_id):
    """Removes the mark of `mark_pending`, if there is one."""
    from . import guard
    if valid_id(run_id):
        try:
            os.remove(os.path.join(guard.registry(), run_id + PENDING))
        except OSError:
            pass


PENDING_GRACE = 10 * 60      # seconds: a younger .pending mark may be a `saani auto on` whose PostToolUse hook has not run yet


def clear_stale_pending(now=None):
    """Removes the `.pending` marks of runs the guard holds no entry of (over marks included), whatever the project, once they are older
    than PENDING_GRACE (by the file's time): a `saani auto on` that wrote its mark but not its record, or whose session never registered.
    Only the plain `saani auto off` with no record calls it. A younger mark stays: it may be another project's `saani auto on` waiting
    for its hook, and without the mark bin/saani would not start Python for that hook, so an away session would never be registered and
    would run with no guard. A mark whose time cannot be read, or lies in the future, counts as young. Returns (the run ids removed, how
    many marks were kept as recent), the ids sorted."""
    import time
    from . import guard
    folder = guard.registry()
    try:
        names = os.listdir(folder)
    except OSError:
        return [], 0
    now = time.time() if now is None else now
    held = {e.get("id") for _, e in entries()}
    removed, recent = [], 0
    for name in names:
        run_id = name[:-len(PENDING)]
        if not name.lower().endswith(PENDING) or run_id in held:
            continue
        path = os.path.join(folder, name)
        try:
            age = now - os.stat(path).st_mtime
        except OSError:
            age = -1
        if not 0 <= age or age < PENDING_GRACE:
            recent += 1
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        removed.append(run_id)
    return sorted(removed), recent


def end_guard_entries(run_id):
    """Removes the guard's registry entries of this run made for this repository (every session that registered it), through the guard's
    `turn_off`; the entries of another project's run with the same id stay; and the run's `.pending` mark. Returns how many entries."""
    from . import guard
    clear_pending(run_id)
    mine = own_entries(run_id)
    if len(mine) == len([1 for _, e in entries() if e.get("id") == run_id]):      # no other project holds this id: the guard's own call
        return guard.turn_off_run(run_id) or 0
    for session, _ in mine:
        guard.turn_off(session)
    return len(mine)


def run_folder(given, action):
    """The run's folder and id for `saani auto on` and `away`: a worktree of this project inside the worktrees folder (not that folder
    itself), `--folder` or the current folder; the id is --id's (given by the caller), else the folder's name without `auto-`."""
    from . import guard
    c = cfg()
    cwd = os.getcwd()
    try:
        folder = guard.resolve(given, cwd) if given else guard.norm(cwd)
    except guard.Refuse as e:
        raise SystemExit(f"saani auto {action}: --folder {given}: {e.why}")
    wt = guard.norm(c.wt_dir)
    if not (guard.inside(folder, wt) and folder != wt and os.path.isdir(folder)):
        raise SystemExit(f"saani auto {action}: the run's folder must be a folder inside {c.wt_dir} (made by `saani auto`; "
                         "run this from it or pass --folder)")
    dirs = guard.git_dirs(folder)
    if not dirs or guard.norm(os.path.dirname(dirs[1])) != here() or not os.path.isfile(os.path.join(os.path.dirname(dirs[1]), config.FILE)):
        raise SystemExit(f"saani auto {action}: the run's folder is not a worktree of this project ({config.FILE} in its main checkout)")
    return folder


def run_id_of(given, folder, action):
    run_id = given or os.path.basename(folder).removeprefix("auto-")
    if not valid_id(run_id):
        raise SystemExit(f"saani auto {action}: run id '{run_id}': letters, digits, - and _ only, at most 64")
    return run_id


def cmd_on(args):
    from . import guard
    folder = run_folder(args.folder, "on")
    run_id = run_id_of(args.id, folder, "on")
    old = read_record()
    now = utc_now()
    if old and (now - parse_stamp(old["started"])).total_seconds() < guard.MAX_HOURS * 3600:
        raise SystemExit(f"saani auto on: run {old['id']} is on (since {old['started']}): `saani auto off` first")
    path = record_path()
    if path is None:
        raise SystemExit("saani auto on: not inside a git checkout")
    here_mode = bool(getattr(args, "here", False))
    rec = {"id": run_id, "started": stamp(now), "folder": str(folder), "deploy": bool(args.deploy), "limits": dict(LIMITS),
           "mode": "here" if here_mode else "away"}
    token = os.environ.get(WATCH_VAR)
    if token and not here_mode:
        rec["watch"] = token          # the watcher that started this session (#67): only it ends this record; written here and nowhere else
    if not here_mode:
        # The guard's mark before the record: when it cannot be written, no record is left to block the next `saani auto on`. Without it
        # bin/saani would not start Python for the PostToolUse hook of this call when no other run is registered.
        mark_pending(run_id)
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    warning = parallel_warning(parallel_size(cfg().parallel, os.cpu_count(), None))
    if warning:
        print(warning)                    # before the marker line below, which stays the last line
    clones_warning = config.present_reference_clones(cfg())[1]
    if clones_warning:
        print(clones_warning)             # a missing clone: the guard registers the run without it
    if here_mode:
        # The interactive sub-mode (#50): the record (stop rules, journal, report) without the guard's switch. This line is not the
        # marker, so the guard leaves the owner's session in present mode: the owner answers the prompts and the questions.
        print(f"saani auto: here {run_id} folder={folder} (interactive: the guard stays in present mode)")
        return 0
    # The guard's PostToolUse hook registers the session after this process exits 0 and printed this line: the last line of stdout, on
    # success only, and no other line like it. The id is --id's and the folder --folder's when given (the guard checks both); the
    # .pending mark is written above.
    print(f"saani auto: on {run_id} folder={folder}")
    return 0


# ---- status and off

def status_text(rec, now=None):
    now = now or utc_now()
    started = parse_stamp(rec["started"])
    limits = {**LIMITS, **(rec.get("limits") or {})}
    got = counts(rec, now)
    by = refusals_by_reason(rec["id"])
    lines = [f"auto run {rec['id']}", f"  started: {rec['started']}, elapsed {duration((now - started).total_seconds())}",
             f"  folder:  {rec.get('folder')}", f"  deploy:  {'yes' if rec.get('deploy') else 'no'}",
             f"  refusals: {got['refusals']}" + (": " + ", ".join(f"{r} {n}" for r, n in sorted(by.items(), key=lambda x: -x[1])) if by else ""),
             "  limits (a stop at any of them):"]
    for k in LIMITS:
        value = f"{got[k]:.1f}" if k == "hours" else str(got[k])
        lines.append(f"    {LIMIT_WORDS[k]}: {value} of {limits[k]}" + ("  REACHED" if got[k] >= limits[k] else ""))
    return "\n".join(lines)


def away_gone(rec, alive=None):
    """A line when an away run's process (its id written by `saani auto away`) is gone while the run is still on; else None."""
    if rec.get("mode") != "away":
        return None
    try:
        pid = int(pid_path(rec["id"]).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if (alive or queue.alive)(pid):
        return None
    return (f"  WARNING: the away run's process ({pid}) is gone but the run is still on: it stopped without ending. Read the log "
            f"build/saani-auto-{rec['id']}.log, then end the run from a terminal: saani auto off --run {rec['id']}")


def cmd_status(args):
    rec = read_record()
    if not rec:
        if args.check:
            live = [e for s, e in own_entries() if guard_live(s)]
            if live:
                print(f"STOP: the run record is missing (the guard still holds run {live[0].get('id')} for this repository): "
                      "stop the run; the owner ends it with `saani auto off --run <id>`.")
                return EXIT_LIMIT
        print("no auto run is on")
        return 0
    print(status_text(rec))
    gone = away_gone(rec)
    if gone:
        print(gone)
    if not args.check:
        return 0
    hit = reached(rec)
    for k, value, limit in hit:
        shown = f"{value:.1f}" if k == "hours" else str(value)
        print(f"STOP: {LIMIT_WORDS[k]} reached ({shown} of {limit})")
        journal(None, "stop", note=f"{LIMIT_WORDS[k]} reached ({shown} of {limit})")
    if hit:
        print("stop starting issues, finish the end of the run (full run, the pull request, `saani auto report`), then stop: the run is "
              "switched off when its session ends (`saani auto off --run <id>` ends it earlier, from a terminal).")
    return EXIT_LIMIT if hit else 0


def guard_live(session):
    """True when the guard still holds this session: an entry, over or not (an over session is still refused until the owner's off)."""
    from . import guard
    try:
        return guard.state(session) is not None
    except Exception:
        return True


def cmd_off(args):
    """The owner's end of the run, from a terminal (the guard refuses it inside the run): the guard's entries of the run, then the record.
    `--run ID` ends that run even when the record is gone or another. With neither, it removes the stale `.pending` marks of the guard
    (clear_stale_pending)."""
    rec = read_record()
    run_id = args.run_id or (rec or {}).get("id")
    if not run_id:
        stale, recent = clear_stale_pending()
        if stale:
            print(f"removed {len(stale)} .pending mark{'' if len(stale) == 1 else 's'} of the guard with no run registered: "
                  + ", ".join(stale))
        if recent:
            print(f"left {recent} .pending mark{'' if recent == 1 else 's'} of the guard younger than {PENDING_GRACE // 60} minutes "
                  "(a `saani auto on` may be waiting for its hook): run `saani auto off` again later")
        print("no auto run is on")
        return 0
    if not valid_id(run_id):
        raise SystemExit(f"saani auto off: run id '{run_id}': letters, digits, - and _ only, at most 64")
    mine = rec and rec["id"] == run_id
    if mine:
        print(status_text(rec))
    gone = end_guard_entries(run_id)
    from . import guard
    unreadable = guard.remove_unreadable_over()
    if unreadable:
        print(f"removed {len(unreadable)} unreadable over mark{'' if len(unreadable) == 1 else 's'} of the guard (no run id to match): "
              + ", ".join(unreadable))
    if mine:
        path = record_path()
        try:
            os.remove(path)
        except OSError as e:
            raise SystemExit(f"saani auto off: cannot remove {path}: {e}")
    print(f"auto run {run_id} is off ({gone} guard entr{'y' if gone == 1 else 'ies'} removed" + ("" if mine else "; no record of this run here")
          + f"). The journal build/saani-auto-{run_id}.jsonl and the report build/saani-auto-{run_id}.md stay.")
    return 0


# ---- report

def landed_issues(rec):
    """[(issue number, first line of the commit)] of the integration commits since the start that say `refs #n`, oldest first."""
    r = core.git("log", f"origin/{cfg().integration}", f"--since={rec['started']}", "--reverse", "--format=%s%x1f%B%x1e", check=False)
    out, seen = [], set()
    for chunk in (r.stdout if r.returncode == 0 else "").split("\x1e"):
        subject, _, body = chunk.strip().partition("\x1f")
        for m in REFS.finditer(body):
            n = int(m.group(1))
            if n not in seen:
                seen.add(n)
                out.append((n, subject))
    return out


def issue_title(n):
    try:
        return gh("issue", "view", str(n), "--repo", cfg().repo, "--json", "title", "--jq", ".title").strip()
    except SystemExit:
        return ""


def status_mark(body):
    """The mark of a status comment (the emoji after its `###`), or None when the comment is not one of ours."""
    from . import comments
    text = (body or "").lstrip("# \t")
    for heading, _, _ in comments.KINDS.values():
        mark = heading.split(" ", 2)[1]
        if text.startswith(mark):
            return mark
    return None


def left_issues(rec, journal_rows, landed=()):
    """{issue: (kind, title)} of the issues left waiting: the open issues whose latest status comment, written since the start, is the
    question or the blocked kind (GitHub); issues GitHub does not list are judged by the journal's latest line for them. An issue that
    landed during the run is never left."""
    out, seen = {}, set()
    try:
        rows = json.loads(gh("issue", "list", "--repo", cfg().repo, "--state", "open", "--limit", "200", "--json",
                             "number,title,comments") or "[]")
        start = parse_stamp(rec["started"])
        for row in rows:
            seen.add(row["number"])
            latest = None
            for cm in row.get("comments") or []:
                mark = status_mark(cm.get("body"))
                try:
                    when = parse_stamp(cm.get("createdAt"))
                except (TypeError, ValueError):
                    continue
                if mark and (latest is None or when >= latest[0]):
                    latest = (when, mark)
            if latest and latest[0] >= start and latest[1] in LEFT_MARKS:
                out[row["number"]] = ("question" if latest[1] == LEFT_MARKS[0] else "blocked", row.get("title", ""))
    except (SystemExit, ValueError, TypeError):
        seen.clear()
    last = {}
    for row in journal_rows:
        if row.get("issue") is not None and row.get("kind") != "stop":
            last[row["issue"]] = row["kind"]
    for n, kind in last.items():
        if kind in ("question", "blocked") and n not in seen and n not in out:
            out[n] = (kind, issue_title(n))
    return {n: v for n, v in out.items() if n not in set(landed)}


NO_BUMP = "no version bump: merging creates no release; bump it or merge as is"


def commits_word(n):
    return f"{n} commit" + ("" if n == 1 else "s")


def end_decision(ahead, pr, landed, version_file, before, after):
    """What the End of a run does about the integration -> production pull request (pure). `ahead`: the commits the integration branch
    has that production has not; `pr`: the number of the open release pull request, or None; `landed`: how many issues the run landed (the
    result's `landed_count`); `version_file`, `before`, `after`: `[release] version_file` and the version in it on production and on the integration branch.
    The pull request is opened or updated whenever the branch is ahead, whether the run landed anything or not. When it does not change
    the version (or no version file is set) it says so: `saani merge` then creates no release. The run never bumps the version."""
    action = "none" if not ahead else ("update" if pr else "open")
    if not ahead:
        why = "the integration branch is not ahead of production: no pull request"
    elif not landed:
        why = f"the integration branch was ahead: {commits_word(ahead)} (the run landed nothing)"
    else:
        why = f"the integration branch is ahead of production: {commits_word(ahead)}"
    no_bump = bool(ahead) and (not version_file or after is None or before == after)
    return {"action": action, "ahead": ahead, "pr": pr, "landed_count": landed, "why": why, "no_bump": no_bump, "note": NO_BUMP if no_bump else None}


def ahead_count():
    """The commits of the integration branch that production does not have, from the remote branches; a failure stops, never "0"."""
    c = cfg()
    r = core.git("rev-list", "--count", f"origin/{c.production}..origin/{c.integration}", check=False)
    try:
        if r.returncode != 0:
            raise ValueError("git failed")
        return int((r.stdout or "").strip())
    except ValueError:
        raise SystemExit(f"saani auto end: cannot count the commits of {c.integration} ahead of {c.production}: {(r.stderr or r.stdout or '').strip()}")


def open_release_pr():
    """The number of the open integration -> production pull request, or None."""
    c = cfg()
    rows = json.loads(gh("pr", "list", "--repo", c.repo, "--base", c.production, "--head", c.integration, "--state", "open",
                         "--json", "number") or "[]")
    return rows[0]["number"] if rows else None


def end_state(landed):
    """`end_decision` from what git and GitHub say now (origin is fetched first, a failure only warns); `landed`: how many issues the run landed."""
    from . import notes
    c = cfg()
    fetch()
    ahead = ahead_count()
    before = after = None
    if ahead and c.version_file:
        before = notes.version_of(notes.file_at(c.production, c.version_file))
        after = notes.version_of(notes.file_at(c.integration, c.version_file))
    return end_decision(ahead, open_release_pr() if ahead else None, landed, c.version_file, before, after)


def end_or_error(landed):
    """`end_state` for a report: a failure never stops the report, it becomes {"error": brief reason} (the section says it could not be read)."""
    try:
        return end_state(landed)
    except (SystemExit, Exception) as e:
        text = " ".join(str(getattr(e, "code", None) or e or type(e).__name__).split())
        return {"error": text[:200]}


def cmd_end(args):
    """`saani auto end [--json]`: what the End does about the release pull request (see end_decision); changes nothing."""
    rec = read_record()
    if not rec:
        raise SystemExit("saani auto end: no auto run is on")
    d = end_state(len(landed_issues(rec)))
    if args.json:
        print(json.dumps(d))
        return 0
    print(d["why"])
    print({"none": "no pull request", "open": "open the release pull request (milestone, refs #n of every in-dev issue)",
           "update": f"update the open release pull request #{d['pr']}"}[d["action"]])
    if d["note"]:
        print(f"description and report: {d['note']}")
    return 0


def cost_text(rec, now):
    from . import cost
    try:
        return cost.text(cost.read(cfg(), parse_stamp(rec["started"]).date(), now.date()))
    except (OSError, ValueError, KeyError) as e:
        return f"cost not read: {type(e).__name__}"


def idle_reason(journal_rows):
    """Why a run that did nothing stopped, from its journal: the last `pick` line's note (no slot, nothing eligible), when the run
    started no issue at all; else None."""
    if any(r.get("kind") == "started" for r in journal_rows):
        return None
    notes = [r.get("note") for r in journal_rows if r.get("kind") == "pick" and r.get("note")]
    return notes[-1] if notes else None


def render_report(rec, now, landed, left, by_reason, journal_rows, cost, pr=None, stopped=None, reason=None, end=None):
    """The report's Markdown from what was read (pure). `landed`: [(number, title, commit subject)]; `left`: {number: (kind, title)};
    `stopped`: the stop rules reached ([(rule, value, limit)]), none: the run finished the list. The folder is its name only: a report on
    a pull request holds no path of the owner's machine."""
    started = parse_stamp(rec["started"])
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    folder = os.path.basename(str(rec.get("folder") or "").replace("\\", "/").rstrip("/"))
    why = "; ".join(f"{LIMIT_WORDS[k]} ({v:.1f} of {lim})" if k == "hours" else f"{LIMIT_WORDS[k]} ({v} of {lim})"
                    for k, v, lim in (stopped or []))
    if reason:
        why = f"{why}; {reason}" if why else str(reason)
    elif not why:
        why = (idle_reason(journal_rows) if not landed else None) or "finished the list"
    why = esc(why)
    out = [f"# Auto run {rec['id']}", "", "| | |", "|---|---|", f"| Start | {rec['started']} |", f"| End | {stamp(now)} |",
           f"| Duration | {duration((now - started).total_seconds())} |", f"| Stopped by | {why} |", f"| Id | {rec['id']} |",
           f"| Folder | {esc(folder)} |", f"| Deploy | {'the owner started the run so' if rec.get('deploy') else 'no'} |", "",
           f"## Issues landed on the integration branch ({len(landed)})"]
    out += [f"- #{n} {esc(title) or esc(subject)}" for n, title, subject in landed] or ["- none"]
    if end and end.get("error"):
        out += ["", "## Release pull request", f"- Could not be read, check by hand whether the integration branch is ahead of production: {esc(end['error'])}"]
    elif end:
        out += ["", "## Release pull request", f"- {esc(end['why'])[0].upper()}{esc(end['why'])[1:]}"]
        if end.get("note"):
            out.append(f"- {esc(end['note']).capitalize()}")
    out += ["", f"## Issues left with a question or blocked ({len(left)})"]
    if left:
        out += ["| Issue | Kind | Title |", "|---|---|---|"] + [f"| #{n} | {k} | {esc(t)} |" for n, (k, t) in sorted(left.items())]
    else:
        out.append("- none")
    out += ["", f"## Refusals by the guard ({sum(by_reason.values())})"]
    out += [f"- {esc(r)}: {n}" for r, n in sorted(by_reason.items(), key=lambda x: -x[1])] or ["- none"]
    out += ["", "## Cost", "```", cost, "```", "(an estimate from Claude Code's transcripts, by whole days from the start's date; saani/cost.py)"]
    if journal_rows:
        out += ["", "## Timeline"]
        shown = journal_rows[-MAX_TIMELINE:]
        out += [f"- {r['time']} " + (f"#{r['issue']} " if r.get("issue") is not None else "") + f"{r['kind']}"
                + (f": {esc(r['note'])}" if r.get("note") else "") for r in shown]
        if len(journal_rows) > len(shown):
            out.append(f"- ({len(journal_rows) - len(shown)} older lines are in the journal)")
    n = f" {pr}" if pr is not None else ""
    out += ["", "## Next step for the owner",
            f"- `/saani:merge{n}` merges the green pull request, relabels the issues and removes this run's folder and branch.",
            f"- `/saani:merge{n} --deploy` does the same, then deploys."]
    return "\n".join(out) + "\n"


def cmd_report(args):
    rec = read_record()
    if not rec:
        raise SystemExit("saani auto report: no auto run is on (run it before the owner ends it)")
    if args.pr is not None and args.pr < 1:
        raise SystemExit("saani auto report: --pr is a pull request number, 1 or more")
    if args.post and args.pr is None:
        raise SystemExit("saani auto report --post: name the pull request with --pr N")
    now = utc_now()
    rows = read_journal(rec["id"])
    reason = (getattr(args, "reason", None) or "").strip() or None
    if reason:
        journal(None, "stop", note=reason)
    landed = [(n, issue_title(n), subject) for n, subject in landed_issues(rec)]
    end = end_or_error(len(landed))
    text = render_report(rec, now, landed, left_issues(rec, rows, [n for n, _, _ in landed]), refusals_by_reason(rec["id"]), rows,
                         cost_text(rec, now), args.pr, reached(rec, now), reason=reason, end=end)
    path = build_dir() / f"saani-auto-{rec['id']}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"written: {path}")
    if args.post:
        gh("pr", "comment", str(args.pr), "--repo", cfg().repo, "--body-file", str(path))
        print(f"posted on pull request #{args.pr}")
    return 0


# ---- away: the run in a headless Claude Code session, started from the owner's session (#50)

def away_command(run_id, folder, only=(), deploy=False, claude="claude", keep_on=False):
    """The headless session of an away run: it runs the skill's away part (`/saani:auto run ...`), which switches the run on in that
    session (so the guard registers it, never the owner's session) and does the run. Every permission prompt becomes a refusal Claude
    reads (`--permission-prompts none`); Claude Code's classifier decides what the guard does not (`--permission-mode auto`)."""
    prompt = f"/saani:auto run {run_id} --folder {folder}" + (" --deploy" if deploy else "") + (" --keep-on" if keep_on else "") + "".join(f" {n}" for n in only)
    return [claude, "-p", prompt, "--permission-mode", "auto", "--permission-prompts", "none"]


WAIT_VAR = "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"   # `claude -p` stops its background tasks this long after the last turn; 0 = wait for them


WATCH_VAR = "SAANI_AUTO_WATCH"   # the watcher's token for its session: `saani auto on` copies it into the run's record (#67)


def away_env(environ=None, token=None):
    """The headless session's environment: ours, and `claude -p` waits for its background coders (#52); with `token`, the watcher's."""
    env = {**(os.environ if environ is None else environ), WAIT_VAR: "0"}
    env.pop(WATCH_VAR, None)            # never one inherited from elsewhere
    if token:
        env[WATCH_VAR] = token
    return env


def by_hand(argv, windows=None):
    """The command to type for an away run, with the variable the started session gets (PowerShell 5.1 on Windows, else sh)."""
    if os.name == "nt" if windows is None else windows:
        return f"$env:{WAIT_VAR}='0'; {shown(argv)}"
    return f"{WAIT_VAR}=0 {shown(argv)}"


def norm_key(path):
    """A string-only, cross-OS key for a path in Claude Code's JSON config: slashes forward, no trailing slash, case folded for drive-letter
    paths. It never resolves anything (the config may name folders of another OS or that no longer exist), so it is not core.same_path."""
    text = str(path).replace("\\", "/").rstrip("/")
    return text.casefold() if os.name == "nt" or re.match(r"^[A-Za-z]:/", text) else text


def folder_and_parents(folder):
    """The folder's normalized key and each parent's, nearest first."""
    key = norm_key(folder)
    keys = [key]
    while key.rpartition("/")[0]:
        key = key.rpartition("/")[0]
        keys.append(key)
    return keys


def claude_config_file(home=None, environ=None):
    """Claude Code's user file: `$CLAUDE_CONFIG_DIR/.claude.json` when that variable is set, else `<home>/.claude.json`."""
    given = (os.environ if environ is None else environ).get("CLAUDE_CONFIG_DIR")
    return pathlib.Path(given) / ".claude.json" if given else (pathlib.Path(home) if home else pathlib.Path.home()) / ".claude.json"


def folder_trusted(folder, home=None, environ=None):
    """True when Claude Code's user file says the trust dialog was accepted for the folder or one of its parents; a missing or unreadable
    file, or key, is False (fail safe). Reads only that one flag of that file; never writes it."""
    try:
        data = json.loads(claude_config_file(home, environ).read_text(encoding="utf-8"))
        wanted = set(folder_and_parents(folder))
        return any(norm_key(k) in wanted and isinstance(v, dict) and v.get("hasTrustDialogAccepted") is True
                   for k, v in data["projects"].items())
    except (OSError, ValueError, AttributeError, TypeError, KeyError, RecursionError, MemoryError):
        return False


def pid_path(run_id):
    return build_dir() / f"saani-auto-{run_id}.pid"


def start_detached(argv, cwd, log, popen=subprocess.Popen):
    """Start `argv` so it outlives the session that starts it, its output appended to `log`. Windows: its own console, shown minimized
    (the owner can see the run is there) and out of the starting session's job when the job allows it; elsewhere: a new session. The
    process id."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        common = {"cwd": str(cwd), "stdout": out, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "env": away_env()}
        if os.name != "nt":
            return popen(argv, start_new_session=True, **common).pid
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 7                                                    # SW_SHOWMINNOACTIVE: minimized, focus stays put
        flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            return popen(argv, creationflags=flags | 0x01000000, startupinfo=info, **common).pid   # CREATE_BREAKAWAY_FROM_JOB
        except OSError:                                                         # the job allows no breakaway: start it inside
            return popen(argv, creationflags=flags, startupinfo=info, **common).pid


def shown(argv):
    """The command as the owner would type it in PowerShell or bash."""
    return " ".join(a if re.fullmatch(r"[\w@%+=:,./\-]+", a) else '"' + a.replace('"', '\\"') + '"' for a in argv)


def cmd_away(args, which=None, popen=subprocess.Popen):
    import shutil
    folder = run_folder(args.folder, "away")
    run_id = run_id_of(args.id, folder, "away")
    from . import guard
    old = read_record()
    if old and (utc_now() - parse_stamp(old["started"])).total_seconds() < guard.MAX_HOURS * 3600:
        raise SystemExit(f"saani auto away: run {old['id']} is on (since {old['started']}): `saani auto off` first (nothing was started)")
    main = pathlib.Path(cfg().root)                     # the main folder: the project's committed relative settings resolve from it
    log = main / "build" / f"saani-auto-{run_id}.log"
    keep_on = bool(getattr(args, "keep_on", False))
    argv = away_command(run_id, folder, args.only or (), bool(args.deploy), keep_on=keep_on)
    exe = (which or shutil.which)("claude")
    if exe is None:
        print("saani auto away: `claude` is not on the PATH, so the run cannot start by itself. Start it by hand, in a terminal, from "
              f"{main}:\n  {by_hand(argv)}\n{BY_HAND_END.format(id=run_id)}")
        return 2
    if not folder_trusted(main):
        print(f"saani auto away: WARNING: {main} is not marked as trusted in Claude Code's config, so the project's allow rules may be "
              "ignored by the headless session. Open Claude Code interactively in that folder once and accept the trust dialog "
              "(the run starts anyway).")
    try:
        pid_path(run_id).unlink(missing_ok=True)           # the watcher writes the session's id here; none of an older start stays
    except OSError:
        pass
    try:
        pid = start_detached(watch_command(run_id, folder, args.only or (), bool(args.deploy), keep_on), main, log, popen)
    except OSError as e:
        print(f"saani auto away: the headless session could not start ({e}). Start it by hand, in a terminal, from {main}:\n  {by_hand(argv)}\n"
              + BY_HAND_END.format(id=run_id))
        return 2
    print(f"saani auto: away {run_id} started (watcher process {pid}), in a minimized window" if os.name == "nt" else
          f"saani auto: away {run_id} started (watcher process {pid})")
    warning = parallel_warning(parallel_size(cfg().parallel, os.cpu_count(), None))
    if warning:
        print(f"  {warning}")
    print(f"  log:    {log}")
    print("  follow: saani auto status (once the run is on), the log, or /saani:ui")
    if keep_on:
        print(f"  mode:   --keep-on: the run stays on and takes newly eligible issues, until a stop rule or {cfg().keep_on_quiet_minutes:g} quiet minutes end it the normal way")
    print(f"  end:    the run is switched off by itself when the session ends (saani auto off --run {run_id} ends it earlier, from a terminal)")
    return 0


BY_HAND_END = ("  Started by hand there is no watcher: when the session ends, end the run yourself with `/saani:auto off` "
               "(or `saani auto off --run {id}` from a terminal).")


def watch_command(run_id, folder, only=(), deploy=False, keep_on=False, python=None):
    """What `saani auto away` starts detached: this saani's `auto watch`, which runs the session and then switches the run off."""
    saani_py = pathlib.Path(__file__).resolve().parent.parent / "saani.py"
    return ([python or sys.executable, "-I", str(saani_py), "auto", "watch", "--id", run_id, "--folder", str(folder)]
            + (["--deploy"] if deploy else []) + (["--keep-on"] if keep_on else []) + (["--only"] + [str(n) for n in only] if only else []))


def watch_note(text):
    print(f"saani auto watch {stamp(utc_now())}: {text}", flush=True)


def cut_off_issues(rec):
    """[(issue, worktree path)] of the issues this run started that still have a worker worktree and did not land: what a session that
    ended left in flight. Reads git and the journal only; empty when it cannot tell."""
    try:
        landed = {n for n, _ in landed_issues(rec)}
        parked = set()                       # the session itself parked these: a blocked or question comment after the started line
        for r in read_journal(rec["id"]):
            if r.get("issue") is None:
                continue
            if r.get("kind") == "started":
                parked.discard(int(r["issue"]))
            elif r.get("kind") in ("blocked", "question"):
                parked.add(int(r["issue"]))
        return sorted((n, p) for n, (p, _) in flight_marks(rec["id"]).items() if n not in landed and n not in parked)
    except (Exception, SystemExit):
        return []


def park_cut_off(rec, what):
    """The watcher comments each cut-off issue as blocked, with where it stands (its worktree). A refusal by gh only prints a warning."""
    from . import comments
    for n, path in cut_off_issues(rec):
        text = (f"The away run {rec['id']} ended ({what}) while this issue was in flight. Its work stands in the worktree folder "
                f"`{pathlib.Path(path).name}`, possibly with uncommitted changes. Nothing was landed for it.")
        try:
            if comments.try_post(n, "blocked", text=text, once=rec["id"]):
                watch_note(f"#{n} commented as blocked (worktree {path})")
        except Exception as e:                 # the watcher always goes on to switch the run off
            watch_note(f"#{n} could not be commented ({e})")


def fallback_report(rec, what):
    """The watcher's safety net: a session that ended without writing its report (it died, was stopped, or never got a slot) still leaves
    one for the owner, in build/saani-auto-<id>.md, with why (the stop rules, else the last pick's reason, else how the session ended).
    It reads git and the journal only (no gh). Never replaces a report that exists; never raises."""
    try:
        path = build_dir() / f"saani-auto-{rec['id']}.md"
        if path.exists():
            return
        now = utc_now()
        rows = read_journal(rec["id"])
        landed = [(n, "", subject) for n, subject in landed_issues(rec)]
        cut = cut_off_issues(rec)
        reason = None if (reached(rec, now) or idle_reason(rows)) else f"the session ended without writing its report ({what})"
        if cut:
            reason = (reason or f"the session ended ({what})") + "; cut off while in flight: " + ", ".join(f"#{n} ({p})" for n, p in cut)
        text = render_report(rec, now, landed, {}, refusals_by_reason(rec["id"]), rows, cost_text(rec, now), None, reached(rec, now),
                             end=end_or_error(len(landed)), reason=reason)
        path.write_text(text, encoding="utf-8")
        watch_note(f"no report was written by the session: written for it, {path}")
    except (Exception, SystemExit) as e:
        watch_note(f"the report could not be written ({e})")


def cmd_watch(args, which=None, popen=subprocess.Popen):
    """The away run's watcher (started by `saani auto away`, never typed): runs the headless session with the log as its output, writes the
    session's process id for `away_gone`, waits for it, and then switches the run off in-process (what the owner would do from a
    terminal). --keep-on only changes the session's prompt (the run stays on and takes new issues inside the session); the watcher's end is the same. A session that cannot start is logged and the run is switched off the same way. Exits 0 whatever the session did."""
    import shutil
    run_id = args.id
    if not run_id or not valid_id(run_id) or not args.folder:
        raise SystemExit("saani auto watch: --id (letters, digits, - and _ only) and --folder are needed; `saani auto away` starts it")
    argv = away_command(run_id, args.folder, args.only or (), bool(args.deploy), keep_on=bool(getattr(args, "keep_on", False)))
    exe = (which or shutil.which)("claude")
    code = None
    token = secrets.token_hex(16)           # the session's `saani auto on` writes it in the record: only a record with it is this one's to end
    try:
        if exe is None:
            raise OSError("`claude` is not on the PATH")
        log = build_dir() / f"saani-auto-{run_id}.log"
        with open(log, "ab") as out:          # the session's output, appended to the log the watcher's own lines go to
            proc = popen([exe] + argv[1:], cwd=str(cfg().root), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env=away_env(token=token))
            try:
                pid_path(run_id).write_text(str(proc.pid), encoding="utf-8")
            except OSError:
                pass
            watch_note(f"session started (process {proc.pid})")
            code = proc.wait()
        what = f"session ended with code {code}"
    except (OSError, SystemExit) as e:
        what = f"session could not start ({e})"
    try:
        rec = read_record()
    except SystemExit:
        rec = None
    if not rec or rec.get("id") != run_id:
        watch_note(f"{what}; no run record for {run_id}, nothing to switch off")
        return 0
    if rec.get("watch") != token:
        watch_note(f"{what}; not this session's record (the run is another start's, or the session never ran `on`): left on, "
                   f"`/saani:auto off` ends it")
        return 0
    watch_note("token matched")
    fallback_report(rec, what)
    park_cut_off(rec, what)
    try:
        cmd_off(type("Args", (), {"run_id": run_id})())
    except SystemExit as e:
        watch_note(f"{what}; the run could not be switched off ({e}): saani auto off --run {run_id}")
        return 0
    watch_note(f"{what}; run switched off")
    return 0


def cmd_run(args):
    """`saani auto on|off|status|report|away`."""
    return {"on": cmd_on, "off": cmd_off, "status": cmd_status, "report": cmd_report, "end": cmd_end, "away": cmd_away,
            "watch": cmd_watch, "wait": cmd_wait}[args.action](args)
