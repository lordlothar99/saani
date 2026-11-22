"""An auto run's choice of work (docs/SAANI.md "An auto run"): `saani auto pick` says which open issues the run may take now, in order,
how many may run side by side, and why each of the others is left. A script, no model. Pure functions first (tests/test_auto.py); the
reading of GitHub, git and the machine is in `pick`. Standard library only.
"""
import ctypes
import datetime
import json
import os
import re
import sys

from . import board_server, config, core, glance

MAX_PARALLEL = 3          # never more issues side by side than this, whatever the settings or the machine say
GB_PER_ISSUE = 4          # free memory one running issue is given (its tests, its tools, its subagent)
CPUS_PER_ISSUE = 4
PATH_TOKEN = re.compile(r"`([^`\s]+)`")
FILE_NAME = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,4}$")


def cfg():
    return config.current()


# ---------------------------------------------------------------- sizing

def parallel_size(configured, cpus, free_gb):
    """How many issues an auto run keeps in flight. `configured` 0: auto-sized, min(3, cpus // 4, free GB // 4), each at least 1 (free_gb None:
    unknown, the cores alone decide); any n >= 1 is fixed, capped at 3."""
    if configured and configured >= 1:
        return min(MAX_PARALLEL, int(configured))
    by_cpu = max(1, (cpus or 1) // CPUS_PER_ISSUE)
    by_ram = max(1, int(free_gb // GB_PER_ISSUE)) if free_gb is not None else MAX_PARALLEL
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
    """The paths an issue body names between backticks: a token with a / or a file extension (`saani/core.py`, `docs/`, `saani.toml`)."""
    out = []
    for token in PATH_TOKEN.findall(body or ""):
        t = token.strip(".,;:()").removeprefix("./")
        if not t or t.startswith(("-", "http", "#")) or not re.fullmatch(r"[\w./@+-]+", t):
            continue
        if ("/" in t or FILE_NAME.search(t)) and t not in out:
            out.append(t)
    return out


def paths_overlap(a, b):
    """Best effort: the same path, one inside the other's folder, or a bare file name that is the other's file name."""
    a, b = a.strip("/"), b.strip("/")
    if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
        return True
    return ("/" not in a and a == b.rsplit("/", 1)[-1]) or ("/" not in b and b == a.rsplit("/", 1)[-1])


def first_overlap(paths, others):
    """(path, other path) of the first overlap between two path lists, or None."""
    for p in paths:
        for q in others:
            if paths_overlap(p, q):
                return p, q
    return None


# ---------------------------------------------------------------- choice

def order_key(issue):
    """Milestone by version ascending (none last), then Bug first, then the issue number."""
    return (board_server.milestone_key(issue.get("milestone")), 0 if issue.get("type") == "Bug" else 1, issue["number"])


def choose(issues, started, running, today, labels=None, types=None):
    """Splits the open `issues` ([{number, title, labels, milestone, body, type}]) into those a run may take now and those it leaves.
    `started`: issue numbers that already have a worktree folder or a worker branch; `running`: {issue number: [paths its worktree changed]}.
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
        elif have & owner_labels:
            reason = "needs the owner or is an idea (label " + ", ".join(sorted(have & owner_labels)) + ")"
        elif fit == "no":
            reason = "Auto: no"
        elif glance.is_blocked(g, open_numbers, today):
            reason = "blocked by " + (", ".join(f"#{b}" for b in (g.get("blocked_by") or {}).get("issues", [])) or "a date")
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

def read_issues():
    """The open issues with their body; the Type comes from the REST API (`gh issue list` has no such field, #38)."""
    rows = board_server.run("gh", "issue", "list", "--repo", cfg().repo, "--state", "open", "--limit", "200", "--json",
                            "number,title,labels,milestone,body", timeout=60)
    types = board_server.issue_types("open")
    return [{"number": r["number"], "title": r["title"], "labels": [x["name"] for x in r.get("labels", [])],
             "milestone": (r.get("milestone") or {}).get("title"), "body": r.get("body") or "", "type": types.get(r["number"])}
            for r in json.loads(rows or "[]")]


def started_issues(folders, branches):
    """Issue numbers with a `<n>-` worktree folder or a worker branch (local or on origin)."""
    out = {int(m.group(1)) for f in folders if (m := re.match(r"(\d+)-", f))}
    for b in branches:
        m = cfg().worker_branch.match(b.strip().removeprefix("* ").removeprefix("remotes/").removeprefix("origin/"))
        if m:
            out.add(int(m.group(2)))
    return out


def running_issues():
    """{issue number: [paths changed in its worktree against the integration branch]} for each worker branch that has a worktree."""
    c, out = cfg(), {}
    for path, branch in core.worktrees():
        m = c.worker_branch.match(branch)
        if m and core.in_wt_dir(path):
            diff = core.git("diff", "--name-only", f"origin/{c.integration}...{branch}", check=False)
            out[int(m.group(2))] = diff.stdout.split() if diff.returncode == 0 else []
    return out


def pick(today=None):
    """The whole answer of `saani auto pick` as a dict: slots, parallel, running, eligible, skipped."""
    c = cfg()
    core.git("fetch", "-q", "origin", check=False)
    running = running_issues()
    folders = [p.name for p in c.wt_dir.iterdir()] if c.wt_dir.is_dir() else []
    branches = core.git("branch", "-a", "--format=%(refname:short)", check=False).stdout.splitlines()
    issues = read_issues()
    for i in issues:                                  # a running issue's own named paths count too, as well as what it changed so far
        if i["number"] in running:
            running[i["number"]] = running[i["number"]] + issue_paths(i["body"])
    eligible, skipped = choose(issues, started_issues(folders, branches), running, today or datetime.date.today().isoformat())
    parallel = parallel_size(c.parallel, os.cpu_count(), free_ram_gb())
    return {"slots": free_slots(parallel, len(running)), "parallel": parallel, "running": sorted(running),
            "eligible": eligible, "skipped": skipped}


def cmd_pick(args):
    got = pick()
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
