"""An auto run's choice of work (docs/SAANI.md "An auto run"): `saani auto pick` says which open issues the run may take now, in order,
how many may run side by side, and why each of the others is left. A script, no model. Pure functions first (tests/test_auto.py); the
reading of GitHub, git and the machine is in `pick`. Standard library only.
"""
import ctypes
import datetime
import json
import os
import re
import subprocess
import sys

from . import config, core, glance

MAX_PARALLEL = 3          # never more issues side by side than this, whatever the settings or the machine say
GB_PER_ISSUE = 4          # free memory one running issue is given (its tests, its tools, its subagent)
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
    """How many issues an auto run keeps in flight. `configured` 0: auto-sized, min(3, cpus // 4, free GB // 4), each at least 1 (free_gb None:
    unknown, the cores alone decide); any n >= 1 is fixed, capped at 3. The memory the `running` issues already use is given back to
    `free_gb`, so the size is the machine's, the same at every pick."""
    if configured and configured >= 1:
        return min(MAX_PARALLEL, int(configured))
    by_cpu = max(1, (cpus or 1) // CPUS_PER_ISSUE)
    by_ram = max(1, int((free_gb + running * GB_PER_ISSUE) // GB_PER_ISSUE)) if free_gb is not None else MAX_PARALLEL
    return min(MAX_PARALLEL, by_cpu, by_ram)


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


def choose(issues, started, running, today, labels=None, types=None, only=None, branched=()):
    """Splits the open `issues` ([{number, title, labels, milestone, body, type}]) into those a run may take now and those it leaves.
    `started`: issue numbers with a worktree folder; `branched`: with a worker branch only; `only`: take just these (set before the overlap pass); `running`: {issue number: [paths its worktree changed]}.
    Returns (eligible, skipped): eligible [{number, title, milestone, type, flags}] in the run's order, skipped [{number, reason}]."""
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
        eligible.append({"number": n, "title": i.get("title", ""), "milestone": i.get("milestone"), "type": i.get("type"), "flags": flags})
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
    out = gh("api", "--paginate", f"repos/{cfg().repo}/issues?state=open&per_page=100", "--jq",
             r'.[] | select(.pull_request | not) | "\(.number)\t\(.type.name // "")"')
    pairs = (line.split("\t") for line in out.splitlines() if "\t" in line)
    return {int(n): t for n, t in pairs if t}


def read_issues():
    """The open issues (up to ISSUE_LIMIT) with their body and Type."""
    try:
        rows = json.loads(gh("issue", "list", "--repo", cfg().repo, "--state", "open", "--limit", str(ISSUE_LIMIT), "--json",
                             "number,title,labels,milestone,body") or "[]")
    except json.JSONDecodeError:
        raise SystemExit("saani auto pick: gh issue list gave an answer that is not JSON")
    types = read_types()
    return [{"number": r["number"], "title": r["title"], "labels": [x["name"] for x in r.get("labels", [])],
             "milestone": (r.get("milestone") or {}).get("title"), "body": r.get("body") or "", "type": types.get(r["number"])}
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


def pick(today=None, only=None):
    """The whole answer of `saani auto pick` as a dict: slots, parallel, running, eligible, skipped."""
    c = cfg()
    fetch()
    running = running_issues()
    folders = [p.name for p in c.wt_dir.iterdir()] if c.wt_dir.is_dir() else []
    branches = core.git("branch", "-a", "--format=%(refname:short)", check=False).stdout.splitlines()
    issues = read_issues()
    for i in issues:                                  # a running issue's own named paths count too, as well as what it changed so far
        if i["number"] in running:
            running[i["number"]] = running[i["number"]] + issue_paths(i["body"])
    started, branched = started_issues(folders, branches)
    eligible, skipped = choose(issues, started, running, today or datetime.date.today().isoformat(), only=set(only) if only else None,
                               branched=branched)
    parallel = parallel_size(c.parallel, os.cpu_count(), free_ram_gb(), len(running))
    return {"slots": free_slots(parallel, len(running)), "parallel": parallel, "running": sorted(running),
            "eligible": eligible, "skipped": skipped}


def cmd_pick(args):
    got = pick(only=args.only)
    if args.limit is not None:
        got["eligible"] = got["eligible"][:max(0, args.limit)]
    if args.json:
        print(json.dumps(got, indent=1, ensure_ascii=False))
        return 0
    running = ", ".join(f"#{n}" for n in got["running"]) or "none"
    print(f"slots: {got['slots']} free of {got['parallel']} (running: {running})")
    print(f"eligible ({len(got['eligible'])}), in the run's order:")
    for e in got["eligible"]:
        extra = [e["type"] or "no Type", e["milestone"] or "no milestone"] + e["flags"]
        print(f"  #{e['number']} {e['title']} [{'; '.join(extra)}]")
    if not got["eligible"]:
        print("  none")
    print(f"skipped ({len(got['skipped'])}):")
    for s in got["skipped"]:
        print(f"  #{s['number']} {s['reason']}")
    return 0
