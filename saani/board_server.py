"""Saani at a glance: a local status page and the board (docs/SAANI.md). Standard library only; read-only (it starts, stops and writes
nothing); pure functions first (tests/test_board_server.py).

  saani ui                     serve http://127.0.0.1:<port>/ (this PC only), refreshed every 5 s: / is the isometric board (/board still opens it), /kanban the kanban page
  saani ui --open              the same, and open the board in the browser
  saani ui --json              print the state once and exit
  saani board ...              the same as saani ui (the command's former name, kept as an alias)

The status page answers four questions, in plain words:
1. What waits for me, what is being done, what is ready to ship? One card per issue (column_for): Waiting for you (the `answer` label:
   your answer; the `act` label: your action), Ready (open, nothing blocks it, nobody on it yet), Building (a worktree with work in it), Testing (tests
   running in its worktree, or the final check of `saani land`), Merged on the integration branch (`in_dev`), To deploy (`in_main`),
   Deployed (closed in the last LIVE_DAYS days, folded). Issues labelled idea stay off. Each card gives a prompt to paste to the Architect
   (prompt_for): the owner talks to one agent, the Architect, who starts the workers.
2. Who works on what? The Claude Code sessions of the project's folders active in the last hours, from the metadata of the session files in
   ~/.claude/projects/ (folder, branch, last activity, model, and the issue numbers of the worktree folders its lines name; only those
   numbers are kept, never message or command text). A session goes on the card its branch names, else the worktree it runs in, else the
   worktree its commands used last (agent_issue).
3. Which subagents exist and what do they do? The roster: the subagents defined in .claude/agents/*.md, Saani's planned roles, and the
   built-in ones Claude Code offers, with the runs seen in the last hours (from the subagents' .meta.json).
4. How busy is this PC? CPU, memory, disk and the heaviest programs (Windows CIM through PowerShell; the disk from Python), as a curve.
Below: the test runs, the landings and where CI would run. The issues come from gh, cached a minute.

/board serves board.html (the isometric board), written without <html>/<head>/<body> because it is also published as an artifact whose
host adds them; here the server adds the same skeleton. It reads /api/state and falls back to /mock-state.json (a synthetic sample).
"""
import collections
import concurrent.futures
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, core, glance, progress

HERE = pathlib.Path(__file__).resolve().parent
SAANI_PY = HERE.parent / "saani.py"
PAGE_FILE = HERE / "status.html"
BOARD_FILE = HERE / "board.html"
MOCK_FILE = HERE / "samples" / "board-sample.json"
CLAUDE_PROJECTS = pathlib.Path.home() / ".claude" / "projects"
SESSION_HOURS = 6                      # sessions touched in the last hours are listed
ACTIVE_SECONDS = 120                   # a file written in the last two minutes: working
IDLE_SECONDS = 30 * 60                 # under thirty minutes: idle; older: asleep
TEST_LIVE_SECONDS = 120                # a test output written in the last two minutes: tests running
TAIL_BYTES = 256 * 1024
LIVE_DAYS = 7
COLUMNS = [
    ("you", "Waiting for you", "Your answer or your action. Copy the card's prompt and paste it to the Architect."),
    ("ready", "Ready", "Open, nothing blocks it, nobody on it yet: ready to build. The Architect can start it. Command: /saani:new <issue>."),
    ("building", "Building", "An agent works on it in its own folder (a worktree). Command: /saani:land when it is committed."),
    ("testing", "Testing", "Tests run in its folder, or the last check before it lands. Command: /saani:test (/saani:progress for a long run)."),
    ("dev", "Merged", "Done and merged into the integration branch: goes to production with the next pull request. Command: /saani:ci pr."),
    ("main", "To deploy", "Merged into production: ready to deploy. Goes live with the next deploy. Command: /saani:ci merged <pr>."),
    ("close", "To close", "Merged into production, nothing of it to deploy (it touches no `[deploy] paths`): close it. Command: /saani:ci merged <pr>."),
    ("live", "Deployed", f"Last {LIVE_DAYS} days: deployed and closed. Folded by default."),
]
WORKTREE_IN_TEXT = re.compile(r"[\\/]([^\\/\s\"']+-wt)[\\/]+(\d+)-")
# Saani's roles, until .claude/agents/ (or the plugin) defines them.
PLANNED_ROLES = [
    ("Coder", "Sonnet or Opus", "writes the change for one issue in its own worktree"),
    ("Tester", "Haiku", "runs the tests and says what a failure means"),
    ("Reviewer", "Opus", "reviews the diff with fresh eyes, never the coder's reasoning"),
    ("Scribe", "Haiku", "issue comments and labels"),
    ("Researcher", "Sonnet", "reads docs and code, brings back the answer"),
    ("Sentinel", "Sonnet", "server diagnostics with a read-only key"),
    ("Deployer", "Sonnet", "deploys production after the owner's tap"),
]
BUILTIN = {
    "Explore": "searches many files and folders, read-only, brings back where things are",
    "general-purpose": "research and multi-step tasks",
    "Plan": "designs an implementation plan, read-only",
    "web-fetch": "reads a web page and answers a question about it",
    "claude-code-guide": "answers questions about Claude Code itself",
    "statusline-setup": "sets up Claude Code's status line",
    "fork": "a copy of the Architect for a side task",
}
FRIENDLY = {"python": "Python · tests, scripts, this page", "chrome": "Chromium · browser tests", "headless_shell": "Chromium · browser tests",
            "node": "Node · Claude Code", "claude": "Claude Code", "code": "VS Code", "msmpeng": "Windows Defender",
            "docker desktop": "Docker", "vmmem": "WSL / Docker VM", "vmmemwsl": "WSL / Docker VM", "msedge": "Edge", "firefox": "Firefox"}
HISTORY_POINTS = 120                   # the resource curve: 120 samples, about ten minutes
SAMPLE_SECONDS = 5
PS_SAMPLE = ("$o=Get-CimInstance Win32_OperatingSystem;"
             "$c=(Get-CimInstance Win32_Processor|Measure-Object LoadPercentage -Average).Average;"
             "$p=Get-Process|Group-Object ProcessName|ForEach-Object{[pscustomobject]@{n=$_.Name;c=$_.Count;"
             "m=[math]::Round(($_.Group|Measure-Object WorkingSet64 -Sum).Sum/1MB)}}|Sort-Object m -Descending|Select-Object -First 8;"
             "@{cpu=$c;free=$o.FreePhysicalMemory;total=$o.TotalVisibleMemorySize;procs=@($p)}|ConvertTo-Json -Compress -Depth 3")
SKELETON = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>')
_cache = {}
_lock = threading.Lock()
_resources = {"now": None, "history": collections.deque(maxlen=HISTORY_POINTS)}


def cfg():
    return config.current()


# ---------------------------------------------------------------- the projects the one board can show (a list in the user's home)

_project_lock = threading.RLock()        # the settings are process-wide: one request at a time works inside one project
_loaded = {}                             # project folder -> its settings
_home = None                             # the project `saani ui` was started in: shown until the selector says otherwise


def registry_path():
    return pathlib.Path(os.environ.get("SAANI_HOME") or pathlib.Path.home() / ".saani") / "projects.toml"


def read_registry():
    try:
        rows = tomllib.loads(registry_path().read_text(encoding="utf-8")).get("projects")
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, str)] if isinstance(rows, list) else []


def write_registry(folders):
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# The projects the Saani board offers in its selector (saani projects add / remove)", "projects = ["]
    lines += [f" {json.dumps(f)}," for f in folders]
    path.write_text("\n".join(lines + ["]", ""]), encoding="utf-8")


def register(folder):
    """Add a project's main checkout to the list (once)."""
    folder = str(pathlib.Path(folder))
    rows = read_registry()
    if folder not in rows:
        write_registry(rows + [folder])
    return folder


def load_project(folder):
    """The settings of a registered folder, or None when it is gone or has no saani.toml any more."""
    if folder not in _loaded:
        try:
            _loaded[folder] = config.load(folder)
        except (SystemExit, OSError):
            return None
    return _loaded[folder]


def projects():
    """[(settings)] of the registered projects that still load, the home project first; the same repo only once."""
    found = {}
    for folder in ([str(_home.root)] if _home else []) + read_registry():
        c = load_project(folder)
        if c and c.repo not in found:
            found[c.repo] = c
    return list(found.values())


def slug(repo):
    """owner/name as one word for an address: acme-webshop."""
    return repo.replace("/", "-")


def find_project(key):
    """The listed project whose owner/name or slug is `key`."""
    return next((c for c in projects() if key in (c.repo, slug(c.repo))), None)


def in_project(repo, fn):
    """fn() with the settings of the project `repo` (owner/name or its slug; empty: the home project), or None when no such project is listed."""
    with _project_lock:
        target = find_project(repo) if repo else (_home or config.current())
        if target is None:
            return None
        previous = config.current()
        config.use(target)
        try:
            return fn()
        finally:
            config.use(previous)


def needs():
    """{label: need} for the labels that mean the owner is waited for."""
    lab = cfg().labels
    return {lab.get("answer", "help wanted"): "answer", lab.get("act", "owner-action"): "act"}


# ---------------------------------------------------------------- pure functions

def activity(age_seconds, turn_open=None):
    """working only while a turn is open (the chat is thinking or running a tool); once it has answered, idle then asleep by age. Files
    that do not say (turn_open None) fall back on their age: a chat waiting for the owner never shows as working."""
    if turn_open is None:
        if age_seconds < ACTIVE_SECONDS:
            return "working"
    elif turn_open and age_seconds < IDLE_SECONDS:
        return "working"
    return "idle" if age_seconds < IDLE_SECONDS else "asleep"


def session_meta(lines, wt_name=None):
    """From a session file's last lines (JSON), only: the folder, the branch, the last timestamp, the model, and the issue numbers of the
    worktrees its lines name (the last one is its focus). Message and command text are never kept."""
    cwd = branch = last = model = None
    focus = turn_open = None
    for raw in lines:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        cwd = row.get("cwd") or cwd
        branch = row.get("gitBranch") or branch
        last = row.get("timestamp") or last
        model = row.get("requestedModel") or model
        if row.get("type") in ("assistant", "user"):
            found = [n for name, n in WORKTREE_IN_TEXT.findall(raw) if wt_name is None or name == wt_name]
            if found:
                focus = int(found[-1])
            turn_open = turn_is_open(row)
    return {"cwd": cwd, "branch": branch, "last": last, "model": model, "focus": focus, "turn_open": turn_open}


def turn_is_open(row):
    """After this message, is the chat still on its turn? An answer that ended (stop_reason end_turn) or an interruption closes it; a tool
    call, a tool result or a new prompt keeps it open."""
    message = row.get("message") or {}
    if row.get("type") == "assistant":
        return message.get("stop_reason") not in ("end_turn", "stop_sequence", "max_tokens", "refusal")
    content = message.get("content")
    text = content if isinstance(content, str) else " ".join(c.get("text", "") for c in content or [] if isinstance(c, dict))
    return not text.startswith("[Request interrupted")


def subagent_row(meta, age_seconds):
    """A subagent's line from its .meta.json: type, short description, model, and whether it still runs."""
    finished = bool(meta.get("finished"))
    return {"type": meta.get("agentType") or "?", "description": (meta.get("description") or "")[:120],
            "model": meta.get("model") or "", "state": "done" if finished else activity(age_seconds)}


def worktree_row(path, branch, ahead, dirty, last_subject, last_time):
    m = cfg().worker_branch.match(branch or "")
    return {"path": str(path), "folder": pathlib.Path(str(path)).name, "branch": branch, "issue": int(m.group(2)) if m else None,
            "ahead": ahead, "dirty": dirty, "last": last_subject, "when": last_time}


def tier1_row(checkout, row):
    """A line of build/tier1-runs.json (written by a project's tier 1 runner, if it keeps one): when, the estimate, the real time,
    green or red."""
    parts = row.get("parts") or []
    return {"checkout": checkout, "time": row.get("date"), "estimate": row.get("estimate"), "seconds": row.get("wall"),
            "result": "passed" if parts and all(p.get("code") == 0 for p in parts) else ("failed" if parts else "")}


def agent_issue(session, worktrees):
    """The issue a session works on: its branch's number, else the worktree it runs in, else the worktree its commands used last."""
    m = cfg().worker_branch.match(session.get("branch") or "")
    if m:
        return int(m.group(2))
    cwd = (session.get("folder") or "").replace("\\", "/").lower().rstrip("/")
    for w in worktrees:
        if w.get("issue") and cwd and cwd == w["path"].replace("\\", "/").lower().rstrip("/"):
            return w["issue"]
    return session.get("focus")


def need_of(labels):
    """What an issue waits for from the owner: 'answer', 'act', or None."""
    for label, need in needs().items():
        if label in (labels or []):
            return need
    return None


def column_for(issue, worktree=None, testing=(), closing=()):
    """The status page column of an issue (see the module's docstring), or None when it stays off the board."""
    lab = cfg().labels
    labels = set(issue.get("labels") or [])
    if lab.get("idea", "idea") in labels and not worktree:
        return None
    if issue["number"] in testing:
        return "testing"
    if worktree and (worktree.get("ahead") or worktree.get("dirty")):
        return "building"
    if issue.get("state") == "CLOSED":
        return "live"
    if lab.get("in_main", "in-main") in labels:
        return "close" if issue["number"] in closing else "main"
    if lab.get("in_dev", "in-dev") in labels:
        return "dev"
    if need_of(labels):
        return "you"
    return "building" if worktree else "ready"


def prompt_for(number, column, need=None):
    """The sentence the owner pastes to the Architect for a card, or None when the card needs nothing from them."""
    if column == "you":
        return (f"#{number} waits for my answer: give me the context, the options and your recommendation, one question at a time."
                if need == "answer" else f"#{number} waits for me to act: tell me exactly what to do, step by step, with ready-to-paste commands.")
    return {"ready": f"Start #{number}: saani new, implement it, test it and land it.",
            "building": f"Where is #{number}? What is done, what is left, and is anything blocked?",
            "testing": f"How are the tests of #{number} going?"}.get(column)


def column_prompt(column, numbers, pr=None):
    """One prompt for a whole column when its action covers every card at once: one pull request takes all of the integration branch to
    production, one deploy takes all of production live. Merged follows the open pull request when there is one."""
    if not numbers:
        return None
    c = cfg()
    carries = ", ".join(f"#{n}" for n in sorted(numbers))
    if column == "dev" and pr and pr.get("number"):
        n, ci = pr["number"], (pr.get("ci") or {}).get("state")
        return {"green": f"Pull request #{n} is green: tell me what to check before I merge it (it carries {carries}).",
                "red": f"Pull request #{n} is red: find why, fix it, run its CI again and tell me when it is green.",
                "running": f"How is the CI of pull request #{n} going? Tell me when it is green or red, and why if red."}.get(
            ci, f"Run the CI of pull request #{n} (saani ci pr) and tell me when it is green (it carries {carries}).")
    return {"dev": f"Prepare the {c.integration} into {c.production} pull request (it carries {carries}): milestone, CI until green, then "
                   f"tell me what to check before I merge it.",
            "main": f"Give me the deploy command for what is on {c.production} (it carries {carries}): read those issues for deploy "
                    f"warnings first.",
            "close": f"Close {carries}: nothing of it is deployed (it touches no deploy path). Check each one, comment, and close it."}.get(column)


def milestone_key(name):
    """Cards sort by milestone, lowest version first, then by issue number; no milestone goes last."""
    parts = re.findall(r"\d+", name or "")
    return (0, tuple(int(x) for x in parts)) if parts else (1, ())


ISSUE_REF = re.compile(r"#(\d+)")
SKIP_COMMIT = re.compile(r"^(Merge |Generated files rebuilt)")


def pr_ci(rollup, head, last_run=None, shas=(), comments=()):
    """The CI of the pull request's latest commit, from GitHub's checks and statuses: none, running, stale, green or red. A release-gate
    comment naming the head commit counts as green. With no check on the latest commit, the last PC run says whether an older commit
    passed, so an old green never shows as the current one."""
    states = []
    for r in rollup or []:
        if r.get("__typename") == "CheckRun" or "conclusion" in r:
            if (r.get("status") or "").upper() != "COMPLETED":
                states.append(("running", r.get("name") or "check", ""))
            else:
                ok = (r.get("conclusion") or "").upper() in ("SUCCESS", "NEUTRAL", "SKIPPED")
                states.append(("green" if ok else "red", r.get("name") or "check", ""))
        else:
            st = (r.get("state") or "").upper()
            states.append(({"SUCCESS": "green", "PENDING": "running", "EXPECTED": "running"}.get(st, "red"), r.get("context") or "status",
                           r.get("description") or ""))
    if head and any(f"release-gate: {head}" in (c.get("body") or "") for c in comments or []):
        states = [s for s in states if s[0] != "running"] + [("green", "release gate", f"passed on {head[:7]}")]
    if states:
        for want in ("red", "running", "green"):
            hit = [x for x in states if x[0] == want]
            if hit:
                return {"state": want, "text": "; ".join(f"{name}: {why}" if why else name for _, name, why in hit)[:200]}
    if last_run and last_run.get("sha") and last_run["sha"] != head:
        shas = list(shas)
        after = len(shas) - 1 - shas.index(last_run["sha"]) if last_run["sha"] in shas else None
        since = f"{after} commit{'' if after == 1 else 's'} came after it" if after else "the branch moved after it"
        return {"state": "stale", "text": f"CI {last_run.get('result', '?')} on {last_run['sha'][:7]}, but {since}; GitHub has no result for "
                                          f"the latest commit {(head or '')[:7]}, so it must run again"}
    return {"state": "none", "text": f"no CI result on the latest commit {(head or '')[:7]}"}


def pr_contents(commits, titles):
    """What a pull request carries: its commits grouped by the issue they refer to (the first #n of the headline), newest issue first,
    with the issue's title when the board knows it. Merges and rebuilt generated files are left out."""
    groups, order = {}, []
    for c in commits or []:
        head = c.get("messageHeadline") or ""
        if SKIP_COMMIT.match(head):
            continue
        m = ISSUE_REF.findall(head + " " + (c.get("messageBody") or ""))
        key = int(m[0]) if m else None
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append({"sha": (c.get("oid") or "")[:7], "text": head[:140]})
    order.sort(key=lambda k: (k is None, -(k or 0)))
    return [{"issue": k, "title": titles.get(k, ""), "commits": groups[k]} for k in order]


def pr_row(raw, titles, last_run=None):
    """The open integration -> production pull request as the page shows it, or None."""
    if not raw:
        return None
    commits = raw.get("commits") or []
    return {"number": raw["number"], "title": raw.get("title", "")[:160], "url": raw.get("url"), "draft": raw.get("isDraft", False),
            "created": raw.get("createdAt"), "mergeable": raw.get("mergeable"), "files": raw.get("changedFiles", 0),
            "additions": raw.get("additions", 0), "deletions": raw.get("deletions", 0), "commits": len(commits),
            "ci": pr_ci(raw.get("statusCheckRollup"), raw.get("headRefOid"), last_run, [c.get("oid") for c in commits],
                        raw.get("comments")),
            "contents": pr_contents(commits, titles)}


def lifecycle(issue, open_numbers, today=None):
    """What the issue's At a glance table adds to its card: the long-run fit (marked as read from the table), whether an open issue or a
    date still blocks it, and whether it is a tracking issue (an epic, the live checks) drawn as an anchor, not a task."""
    g = issue.get("glance") or {}
    out = {"blocked": glance.is_blocked(g, open_numbers, today or datetime.date.today().isoformat()),
           "anchor": bool(re.search(r"\(epic\)|^live checks", issue.get("title", ""), re.I))}
    fit = (g.get("long_run") or {}).get("fit")
    if fit:
        out["longrun"] = {"fit": fit, "why": g["long_run"].get("why", ""), "source": "table"}
    out["blocked_by"] = (g.get("blocked_by") or {}).get("issues", [])
    return out


def build_board(issues, worktrees, testing, sessions, runs, pr=None, closing=()):
    """Columns of cards: each issue once, with its worktree, its agents, its test runs, what it needs from the owner and the prompt."""
    by_number = {}
    for i in issues:
        by_number.setdefault(i["number"], i)
    trees = {w["issue"]: w for w in worktrees if w.get("issue")}
    for n, w in trees.items():
        by_number.setdefault(n, {"number": n, "title": w["branch"].split("-", 1)[-1].replace("-", " "), "labels": [], "state": "OPEN"})
    crew = {}
    for s in sessions:
        n = agent_issue(s, worktrees)
        s["issue"] = n
        if n:
            crew.setdefault(n, []).append(s)
    columns = {key: [] for key, _, _ in COLUMNS}
    landed = {cfg().labels["in_dev"], cfg().labels["in_main"]}      # a blocker that reached dev no longer blocks: its code is there
    open_numbers = {n for n, i in by_number.items() if i.get("state", "OPEN") == "OPEN" and not landed & set(i.get("labels") or [])}
    for n, issue in by_number.items():
        w = trees.get(n)
        col = column_for(issue, w, testing, closing)
        if not col:
            continue
        need = need_of(issue.get("labels"))
        columns[col].append({**issue, **lifecycle(issue, open_numbers), "worktree": w, "agents": crew.get(n, []), "need": need,
                             "prompt": prompt_for(n, col, need),
                             "runs": [r for r in runs if w and r.get("checkout") == w["folder"]]})
    order = lambda c: (milestone_key(c.get("milestone")), c["number"])
    carried = {g["issue"] for g in (pr or {}).get("contents", []) if g.get("issue")}
    return [{"id": key, "title": title, "hint": hint, "cards": sorted(columns[key], key=order),
             "prompt": column_prompt(key, [c["number"] for c in columns[key]], pr),
             "pr": {**pr, "inside": sorted(c["number"] for c in columns[key] if c["number"] in carried)} if key == "dev" and pr else None}
            for key, title, hint in COLUMNS]


def touches_deployed(files, paths):
    """True when one of `files` is under one of the `paths` (prefixes) a served project deploys."""
    return any(f.startswith(tuple(paths)) for f in files)


def closing_issues(numbers):
    """The issues among `numbers` (on production) whose commits touch nothing deployed: they close at merge. Empty without `[deploy] paths`."""
    paths = cfg().deploy_paths
    out = set()
    for n in numbers if paths else ():
        raw = run("git", "log", f"origin/{cfg().production}", "-E", f"--grep=#{n}\b", "--name-only", "--format=", timeout=60)
        files = [f for f in raw.splitlines() if f.strip()]
        if files and not touches_deployed(files, paths):
            out.add(n)
    return out


def testing_issues(worktrees, runs, landing_issue):
    """Issues whose tests run now: the one holding the landing lock, and every worktree with a live test run."""
    live = {r["checkout"] for r in runs if r.get("live")}
    found = {w["issue"] for w in worktrees if w.get("issue") and w["folder"] in live}
    return found | ({landing_issue} if landing_issue else set())


def frontmatter(text):
    """name, description and model from an agent definition's YAML front matter (simple `key: value` lines only)."""
    out = {}
    if text.startswith("---"):
        for line in text.split("---", 2)[1].splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                out[key.strip()] = value.strip().strip("\"'")
    return out


def roster(defined, sessions):
    """Every subagent kind: defined in the repo, planned for Saani, or built in; with the runs seen in the sessions listed."""
    seen = collections.defaultdict(lambda: {"runs": 0, "running": []})
    for s in sessions:
        for sub in s.get("subagents", []):
            entry = seen[sub["type"]]
            entry["runs"] += 1
            if sub["state"] != "done":
                entry["running"].append({"description": sub["description"], "session": s.get("name"), "state": sub["state"]})
    rows = [{"name": d["name"], "kind": "defined", "model": d.get("model", ""), "what": d.get("description", ""),
             "file": d.get("file", f".claude/agents/{d['name']}.md")} for d in defined]
    names = {r["name"].lower() for r in rows}
    rows += [{"name": n, "kind": "planned", "model": m, "what": w, "file": f"planned: .claude/agents/{n.lower()}.md"}
             for n, m, w in PLANNED_ROLES if n.lower() not in names]
    rows += [{"name": n, "kind": "built-in", "model": "", "what": w, "file": "built into Claude Code (no file in the repo)"} for n, w in BUILTIN.items()]
    rows += [{"name": n, "kind": "built-in", "model": "", "what": "", "file": "built into Claude Code (no file in the repo)"}
             for n in seen if n.lower() not in names and n not in BUILTIN]
    for r in rows:
        r.update(seen.get(r["name"], {"runs": 0, "running": []}))
    def rank(r):                       # running, defined, used lately, planned, the rest
        return 0 if r["kind"] == "defined" else 1 if r["runs"] else 2 if r["kind"] == "planned" else 3
    return sorted(rows, key=lambda r: (-len(r["running"]), rank(r), -r["runs"], r["name"]))


def parse_resources(raw, disk_total, disk_free, cores):
    """The PowerShell sample (JSON) as CPU %, memory used and total in GB, the disk, and the heaviest programs with friendly names."""
    data = json.loads(raw)
    total = float(data.get("total") or 0) / 1048576
    free = float(data.get("free") or 0) / 1048576
    procs = data.get("procs") or []
    if isinstance(procs, dict):
        procs = [procs]
    top = [{"name": FRIENDLY.get(str(p.get("n", "")).lower(), p.get("n")), "count": p.get("c"), "mb": p.get("m")} for p in procs]
    return {"cpu": round(float(data.get("cpu") or 0)), "cores": cores, "mem_used": round(total - free, 1), "mem_total": round(total, 1),
            "disk_free": round(disk_free / 1e9), "disk_total": round(disk_total / 1e9), "top": top}


def host_allowed(host, port):
    """Only the page's own address: a page elsewhere cannot read it through a rebound name (DNS rebinding)."""
    return host in (f"127.0.0.1:{port}", f"localhost:{port}")


def project_info():
    """What the board needs to know about the project: its name, repo, branches and the folders its prompts name."""
    c = cfg()
    return {"name": c.name, "repo": c.repo, "integration": c.integration, "production": c.production, "folders": c.folders,
            "labels": c.labels}


# ---------------------------------------------------------------- collectors (each one fails alone)

def cached(key, seconds, fn):
    key = (cfg().repo, key)                      # one project's answer is never another's
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < seconds:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:                       # one source down never blanks the page
        return {"error": f"{type(e).__name__}: {e}"[:300]} if default is None else default


def run(*args, cwd=None, timeout=60):
    r = subprocess.run(list(args), cwd=cwd or cfg().root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout.strip() if r.returncode == 0 else ""


def tasks():
    rows = []
    for path, branch in core.worktrees():
        if not core.in_wt_dir(path):
            continue
        ahead = run("git", "rev-list", "--count", f"origin/{cfg().integration}..HEAD", cwd=path) or "0"
        dirty = len([x for x in run("git", "--no-optional-locks", "status", "--porcelain", cwd=path).splitlines() if x.strip()])
        last = run("git", "log", "-1", "--format=%s%x1f%cr", cwd=path).split("\x1f")
        rows.append(worktree_row(path, branch, int(ahead), dirty, last[0][:100], last[1] if len(last) > 1 else ""))
    return rows


def landing():
    held = core.read_lock(core.lock_path())
    log = core.main_checkout() / "build" / core.LANDINGS
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.is_file() else []
    return {"lock": held, "summary": core.landing_summary(rows),
            "recent": [{k: r.get(k) for k in ("time", "branch", "result", "conflicts", "auto_resolved", "seconds", "head", "why",
                                              "push_retries")} for r in rows[-8:][::-1]]}


def tail_lines(path, size=TAIL_BYTES):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        f.seek(max(0, end - size))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[1:] if end > size else lines


def agents(now=None):
    now = now or time.time()
    out = []
    if not CLAUDE_PROJECTS.is_dir():
        return out
    match = (cfg().session_match or cfg().name).lower()
    wt_name = cfg().wt_dir.name
    for project in CLAUDE_PROJECTS.iterdir():
        if not project.is_dir() or match not in project.name.lower():
            continue
        for f in project.glob("*.jsonl"):
            age = now - f.stat().st_mtime
            if age > SESSION_HOURS * 3600:
                continue
            meta = session_meta(tail_lines(f), wt_name)
            subs = []
            folder = project / f.stem / "subagents"
            if folder.is_dir():
                for m in sorted(folder.glob("*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
                    log = m.with_name(m.name.removesuffix(".meta.json") + ".jsonl")
                    sub_age = now - (log.stat().st_mtime if log.exists() else m.stat().st_mtime)
                    try:
                        subs.append(subagent_row(json.loads(m.read_text(encoding="utf-8")), sub_age))
                    except ValueError:
                        continue
            name = pathlib.Path((meta["cwd"] or "?").replace("\\", "/")).name
            out.append({"session": f.stem[:6], "name": name, "folder": meta["cwd"], "branch": meta["branch"], "model": meta["model"],
                        "focus": meta["focus"], "state": activity(age, meta["turn_open"]), "age_min": round(age / 60), "subagents": subs})
    return sorted(out, key=lambda s: s["age_min"])


def defined_agents():
    rows = []
    folder = cfg().root / ".claude" / "agents"
    for f in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        meta = frontmatter(f.read_text(encoding="utf-8"))
        rows.append({"name": meta.get("name") or f.stem, "description": meta.get("description", "")[:160], "model": meta.get("model", ""),
                     "file": f.relative_to(cfg().root).as_posix()})
    return rows


def tests():
    runs, tier1 = [], []
    now = time.time()
    for root, _ in core.worktrees():
        build = root / "build"
        if not build.is_dir():
            continue
        for meta in build.glob("*.meta.json"):
            out = meta.with_name(meta.name.removesuffix(".meta.json"))
            if out.exists() and now - out.stat().st_mtime < 3600:
                line = safe(lambda: progress.line(out), default="")
                line = line if isinstance(line, str) else ""
                runs.append({"checkout": root.name, "file": out.name, "line": line, "age_min": round((now - out.stat().st_mtime) / 60),
                             "live": now - out.stat().st_mtime < TEST_LIVE_SECONDS and "100%" not in line})
        t1 = build / "tier1-runs.json"
        if t1.is_file():
            try:
                for row in json.loads(t1.read_text(encoding="utf-8"))[-3:]:
                    tier1.append(tier1_row(root.name, row))
            except ValueError:
                pass
    return {"runs": runs, "tier1": sorted(tier1, key=lambda r: str(r.get("time")), reverse=True)[:6]}


def gh_issues(*filters):
    rows = run("gh", "issue", "list", "--repo", cfg().repo, *filters, "--json", "number,title,labels,milestone,state,updatedAt,body", timeout=60)
    return [{"number": r["number"], "title": r["title"][:120], "state": r.get("state", "OPEN"),
             "labels": [x["name"] for x in r.get("labels", [])], "milestone": (r.get("milestone") or {}).get("title"),
             "glance": glance.parse(r.get("body"))}                  # the body itself stays here: the panel asks for it on demand
            for r in json.loads(rows or "[]")]


def issues():
    since = (datetime.date.today() - datetime.timedelta(days=LIVE_DAYS)).isoformat()
    queries = {"open": ("--state", "open", "--limit", "300"),
               "closed": ("--state", "closed", "--search", f"closed:>={since}", "--limit", "40")}
    with concurrent.futures.ThreadPoolExecutor(len(queries)) as pool:
        got = dict(zip(queries, pool.map(lambda q: gh_issues(*q), queries.values())))
    # Both pages place every open issue (an `idea` stays off, see column_for), assigned to the owner or not, and the recently closed ones.
    return {"board": got["open"] + got["closed"], "open": got["open"], "closed": got["closed"]}


def milestones(rows, open_issues):
    """The milestone strip: per open milestone its open and closed issues, its live-checks issue (an open issue titled "Live checks ..."
    in it) and whether it is ready to release (everything closed, something done). `rows` is GitHub's milestone list."""
    out = []
    for m in rows:
        if m.get("state") != "open":
            continue
        title = m["title"]
        checks = next((i["number"] for i in open_issues if i.get("milestone") == title and i["title"].lower().startswith("live checks")), None)
        out.append({"title": title, "open": m["open_issues"], "closed": m["closed_issues"], "checks": checks,
                    "ready": m["open_issues"] == 0 and m["closed_issues"] > 0})
    return sorted(out, key=lambda m: milestone_key(m["title"]))


def hygiene(open_issues, closed_issues, landed, labels):
    """What needs tidying: [{kind, issues}] without the empty ones. closed_labelled: closed but still labelled in-dev / in-main;
    no_milestone: open (not an idea) with no milestone; landed_unlabelled: commits between production and the integration branch name
    them, yet they carry neither in-dev nor in-main."""
    life = {labels["in_dev"], labels["in_main"]}
    found = {
        "closed_labelled": [i["number"] for i in closed_issues if life & set(i["labels"])],
        "no_milestone": [i["number"] for i in open_issues if not i.get("milestone") and labels["idea"] not in i["labels"]],
        "landed_unlabelled": [i["number"] for i in open_issues if i["number"] in landed and not life & set(i["labels"])],
    }
    return [{"kind": k, "issues": sorted(v)} for k, v in found.items() if v]


def landed_refs():
    """Issue numbers that commits between production and the integration branch name (`refs #n`)."""
    c = cfg()
    raw = run("git", "log", f"origin/{c.production}..origin/{c.integration}", "--format=%B", timeout=60)
    return {int(n) for n in re.findall(r"refs #(\d+)", raw)}


def issue_detail(body, comments, url):
    """The panel's Context, Acceptance criteria and History from an issue's Markdown and its comments."""
    def section(name):
        m = re.search(r"^##\s*" + name + r"\s*$(.*?)(?=^##\s|\Z)", body or "", re.M | re.S | re.I)
        return m.group(1).strip() if m else ""
    criteria = [{"done": m.group(1).lower() == "x", "text": m.group(2).strip()}
                for m in re.finditer(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+)$", section("Acceptance criteria"), re.M)]
    history = []
    for c in comments or []:
        text = (c.get("body") or "").strip()
        head = re.match(r"^#{1,4}\s*(.+)", text)
        history.append({"when": (c.get("createdAt") or "")[:10], "kind": head.group(1).strip()[:40] if head else "Comment",
                        "text": (text[head.end():].strip() if head else text)[:300]})
    return {"summary": section("Context")[:1500], "criteria": criteria, "history": history, "url": url}


def issue_page(number):
    raw = run("gh", "issue", "view", str(number), "--repo", cfg().repo, "--json", "body,comments,url", timeout=60)
    got = json.loads(raw) if raw else {}
    return issue_detail(got.get("body"), got.get("comments"), got.get("url"))


def milestone_rows():
    return json.loads(run("gh", "api", f"repos/{cfg().repo}/milestones?state=all&per_page=50", timeout=60) or "[]")


PR_FIELDS = "number,title,url,isDraft,headRefOid,statusCheckRollup,commits,changedFiles,additions,deletions,mergeable,createdAt,comments"


def last_ci_run():
    """The latest pull request CI recorded by `saani ci` in any checkout of the project (build/saani-ci.json)."""
    best = None
    for path, _ in core.worktrees():
        try:
            rows = json.loads((pathlib.Path(path) / "build" / "saani-ci.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in rows if isinstance(rows, list) else []:
            if r.get("pr") and r.get("sha") and (best is None or r.get("date", "") > best.get("date", "")):
                best = r
    return best


def pull_request(titles):
    c = cfg()
    rows = json.loads(run("gh", "pr", "list", "--repo", c.repo, "--base", c.production, "--head", c.integration, "--state", "open",
                          "--json", PR_FIELDS, timeout=60) or "[]")
    if not rows:
        return {"open": None}
    run_ = last_ci_run()
    return {"open": pr_row(rows[0], titles, run_ if run_ and run_.get("pr") == rows[0]["number"] else None)}


def ci():
    return {"text": run(sys.executable, "-I", str(SAANI_PY), "ci", "status", timeout=90) or "saani ci status gave nothing"}


def sample_resources():
    raw = run("powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SAMPLE, timeout=30) if os.name == "nt" else ""
    disk = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/")
    now = parse_resources(raw or "{}", disk.total, disk.free, os.cpu_count())
    point = {"t": time.strftime("%H:%M:%S"), "cpu": now["cpu"], "mem": round(100 * now["mem_used"] / now["mem_total"]) if now["mem_total"] else 0}
    with _lock:
        _resources["now"] = now
        _resources["history"].append(point)


def sampler():
    while True:
        safe(sample_resources)
        time.sleep(SAMPLE_SECONDS)


def resources():
    with _lock:
        return {"now": _resources["now"], "history": list(_resources["history"])}


def waiting(c):
    """How many open issues of project `c` wait for its owner (the answer or act label): that project's own issue list, cached 60 s."""
    with _project_lock:
        previous = config.current()
        config.use(c)
        try:
            lab = c.labels
            return len([i for i in cached("issues", 60, issues)["open"] if {lab["answer"], lab["act"]} & set(i["labels"])])
        finally:
            config.use(previous)


def state():
    trees = safe(tasks, default=[])
    land = safe(landing)
    crew = safe(agents, default=[])
    tst = safe(tests, default={"runs": [], "tier1": []})
    iss = safe(lambda: cached("issues", 60, issues), default={"board": [], "open": [], "closed": []})
    lock = (land or {}).get("lock") if isinstance(land, dict) else None
    m = cfg().worker_branch.match((lock or {}).get("branch") or "")
    testing = testing_issues(trees, tst["runs"], int(m.group(2)) if m else None)
    titles = {i["number"]: i["title"] for i in iss.get("open", []) + iss.get("closed", [])}
    pr = safe(lambda: cached("pr", 30, lambda: pull_request(titles)), default={"open": None})
    strips = safe(lambda: cached("strips", 120, lambda: {
        "milestones": milestones(milestone_rows(), iss.get("open", [])),
        "hygiene": hygiene(iss.get("open", []), iss.get("closed", []), landed_refs(), cfg().labels)}), default={"milestones": [], "hygiene": []})
    in_main = [i["number"] for i in iss.get("open", []) if cfg().labels["in_main"] in i["labels"]]
    closing = safe(lambda: cached("closing", 300, lambda: closing_issues(in_main)), default=set())
    closing = closing if isinstance(closing, set) else set()
    return {"time": datetime.datetime.now().strftime("%H:%M:%S"), "project": safe(project_info, default={}),
            "projects": [{"repo": c.repo, "name": c.name, "slug": slug(c.repo), "waiting": safe(lambda c=c: waiting(c), default=0)}
                         for c in projects()],
            "milestones": strips.get("milestones", []), "hygiene": strips.get("hygiene", []),
            "board": build_board(iss.get("board", []), trees, testing, crew, tst["runs"], (pr or {}).get("open"), closing),
            "crew": crew, "roster": safe(lambda: roster(defined_agents(), crew), default=[]), "resources": resources(),
            "landing": land, "tests": tst, "phases": [], "ci": safe(lambda: cached("ci", 600, ci))}


# ---------------------------------------------------------------- the pages

def page():
    """The status page, read at every request, so a change to its look shows at the next refresh without a restart."""
    return PAGE_FILE.read_text(encoding="utf-8").replace("__REPO__", cfg().repo)


def board_page():
    """The board page with its skeleton, read at every request like page()."""
    return SKELETON + BOARD_FILE.read_text(encoding="utf-8") + "</body></html>"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not host_allowed(self.headers.get("Host", ""), self.server.server_address[1]):
            self.send_error(403)
            return
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        repo = (params.get("project") or [""])[0]           # the selector's choice: owner/name; none: the project the server started in
        if path in ("/kanban", "/kanban/"):
            text = in_project(repo, page)
            body, kind = (text or "").encode("utf-8"), "text/html; charset=utf-8"
        elif path in ("/", "/index.html", "/board", "/board/"):      # the board is the default page; /board is its old address
            body, kind = board_page().encode("utf-8"), "text/html; charset=utf-8"
        elif path in ("/mock-state.json", "/board/mock-state.json"):
            body, kind = MOCK_FILE.read_bytes(), "application/json"
        elif path == "/api/issue":                           # one issue's context, criteria and history, asked for when its panel opens
            number = (params.get("n") or [""])[0]
            data = in_project(repo, lambda: issue_page(int(number))) if number.isdigit() else None
            body, kind = (json.dumps(data) if data is not None else "").encode("utf-8"), "application/json"
        elif path == "/api/state":
            if "1" in params.get("fresh", []):               # the Refresh button: GitHub and the rest asked again now
                with _lock:
                    _cache.clear()
            data = in_project(repo, state)
            text = json.dumps(data) if data is not None else None
            body, kind = (text or "").encode("utf-8"), "application/json"
        else:
            self.send_error(404)
            return
        if not body:                                          # a project that is not in the list
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def projects_command(args):
    """The list the board's selector reads (~/.saani/projects.toml): list, add FOLDER, remove OWNER/NAME."""
    if args.what == "add":
        try:
            folder = config.main_checkout(args.target) if args.target else config.main_checkout()
        except OSError:
            folder = None
        if folder is None or load_project(str(folder)) is None:
            print(f"{args.target or 'here'}: no git checkout with a {config.FILE} there")
            return 1
        register(folder)
        print(f"listed: {load_project(str(folder)).repo} ({folder})")
    elif args.what == "remove":
        if not args.target:
            print("remove needs owner/name")
            return 1
        rows = read_registry()
        keep = [f for f in rows if not (load_project(f) and args.target in (load_project(f).repo, slug(load_project(f).repo)))]
        write_registry(keep)
        print(f"removed {args.target}" if len(keep) < len(rows) else f"{args.target} was not in the list")
    else:
        for c in projects():
            print(f"{c.repo}  {c.root}")
        if not projects():
            print("no project listed: run `saani projects add` in a project, or start `saani ui` in one")
    return 0


def already_running(port):
    """True when a Saani board answers on this port (it is what holds it)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10) as r:
            return "board" in json.loads(r.read())
    except (OSError, ValueError):
        return False


def run_command(args):
    global _home
    _home = cfg()
    if args.json:
        safe(sample_resources)
        print(json.dumps(state(), indent=1))
        return 0
    safe(lambda: register(_home.root))                   # the project it starts in is listed from now on, so any later board offers it
    port = args.port or cfg().board_port
    url = f"http://127.0.0.1:{port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        if not already_running(port):
            raise
        print(f"Saani is already running: {url}#{slug(_home.repo)} (this project is in its selector)")
        if args.open:
            webbrowser.open(f"{url}#{slug(_home.repo)}")
        return 0
    threading.Thread(target=sampler, daemon=True).start()
    print(f"Saani: {url} (board) and {url}kanban (kanban), this PC only; Ctrl+C stops it")
    if args.open:
        webbrowser.open(f"{url}#{slug(_home.repo)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def add_parsers(sub):
    p = sub.add_parser("ui", aliases=["board"], help="the local status page and the isometric board (this PC only); board: alias of ui")
    p.add_argument("--port", type=int)
    p.add_argument("--open", action="store_true", help="open the board in the browser")
    p.add_argument("--json", action="store_true", help="print the state once and exit")
    p.set_defaults(run=run_command)
    p = sub.add_parser("projects", help="the projects the board offers in its selector: list, add [FOLDER], remove OWNER/NAME")
    p.add_argument("what", nargs="?", choices=["list", "add", "remove"], default="list")
    p.add_argument("target", nargs="?", help="add: the project's folder (default: here); remove: owner/name")
    p.set_defaults(run=projects_command)
