"""What Saani's machinery is doing right now (`saani status`): one line per task that runs, is blocked or waits, or one line saying everything
is idle. Best effort, and honest about it: Saani knows its own machinery (the landing lock, the test queue, the auto run's record, the
worktrees) and can look at the PC's processes for a test run started outside the queue; it does not know what a Claude session thinks.
Standard library only; the functions take what they read as arguments so they can be tested without a PC."""
import datetime
import os
import pathlib
import re
import subprocess

IDLE = ("no machinery activity (landing, tests, auto run): Saani cannot see what a Claude session is thinking or editing, "
        "nor which role (Architect, coder, tester, reviewer) it has")
OUTSIDE_NOTE = "queue idle, but tests may run outside it (the process list could not be read)"
LIST_TIMEOUT = 4          # seconds the process list may take: status must stay quick
TEST_WORDS = ("pytest", "unittest", "py.test")
ISSUE_IN_NAME = re.compile(r"^(\d+)-")


def when(text, now=None):
    """`HH:MM` for a moment today, `YYYY-MM-DD HH:MM` for an earlier day; the text itself when it is not a moment."""
    try:
        moment = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return str(text)
    if moment.tzinfo:
        moment = moment.astimezone().replace(tzinfo=None)
    now = now or datetime.datetime.now()
    return moment.strftime("%H:%M" if moment.date() == now.date() else "%Y-%m-%d %H:%M")


def issue_of(branch_or_path):
    """The issue number in `<type>/<n>-<slug>` or `<n>-<slug>`, as `#n`; the name itself when there is none. An auto run's branch
    (`auto/<id>`) or folder (`auto-<id>`) has no issue: it reads `auto run <id>` (#130)."""
    parts = str(branch_or_path).replace("\\", "/").rstrip("/").split("/")
    last = parts[-1]
    if len(parts) == 2 and parts[0] == "auto" and last:          # the branch, not a path that has a folder called auto
        return f"auto run {last}"
    if last.startswith("auto-") and len(last) > 5 and last[5].isdigit():
        return f"auto run {last[5:]}"
    m = ISSUE_IN_NAME.search(last)
    return f"#{m.group(1)}" if m else last


def processes():
    """[{pid, ppid, since, cmd}] of the running processes, best effort: PowerShell on Windows, `ps` elsewhere; None when it cannot be read."""
    try:
        if os.name == "nt":
            script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                      "Get-CimInstance Win32_Process | ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.ParentProcessId, "
                      "$(if ($_.CreationDate) { $_.CreationDate.ToString('s') }), ($_.CommandLine -replace '[\\r\\n]+', ' ') }")
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=LIST_TIMEOUT)
            if out.returncode != 0:
                return None
            return parse_windows(out.stdout)
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,lstart=,args="], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=LIST_TIMEOUT, env={**os.environ, "LC_ALL": "C"})
        if out.returncode != 0:
            return None
        return parse_ps(out.stdout)
    except (OSError, subprocess.SubprocessError):
        return None


def parse_windows(text):
    rows = []
    for line in text.splitlines():
        parts = line.strip().split("|", 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "since": parts[2] or None, "cmd": parts[3]})
    return rows


def parse_ps(text):
    rows = []
    for line in text.splitlines():
        parts = line.split(None, 7)                         # pid ppid, lstart (5 words: Mon Nov 25 14:02:11 2026), args
        if len(parts) < 8 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        try:
            since = datetime.datetime.strptime(" ".join(parts[2:7]), "%a %b %d %H:%M:%S %Y").isoformat(timespec="seconds")
        except ValueError:
            since = None
        rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "since": since, "cmd": parts[7]})
    return rows


def base(token):
    """A command word as compared: quotes off, `/` slashes, a path cut to its file name when it has a drive or folder (the executable), lower case,
    `.exe` off."""
    return pathlib.PurePath(token.strip("\"'").replace("\\", "/")).name.lower().removesuffix(".exe")


def words_of(cmd):
    return [t.strip("\"'").replace("\\", "/") for t in cmd.split()]


def matched_word(cmd, known=()):
    """What makes the command line a test run, or None. Detected: (1) `pytest` or `py.test` as the executable; (2) `python` or `py` with
    `pytest`, `py.test` or `unittest` as one of its first three arguments (`python -m pytest`); (3) a project test command (`[tests] landing`
    or `full`, any executable): the line's words start with the command's words, or end with them (its first word compared by file name, so
    `C:\\Python\\python.exe` is `python`). A line that only mentions a word later (an editor, a chat) is not one."""
    tokens = words_of(cmd)
    if not tokens:
        return None
    first = base(tokens[0])
    if first in ("pytest", "py.test"):
        return first
    if first.startswith("python") or first == "py":
        for t in tokens[1:4]:
            if base(t) in TEST_WORDS:
                return base(t)
    for k in known:
        w = words_of(k)
        n = len(w)
        if not n or n > len(tokens):
            continue
        w = [base(w[0])] + w[1:]
        for window in (tokens[:n], tokens[-n:]):
            if [base(window[0])] + window[1:] == w:
                return "[tests] command"
    return None


def is_test_command(cmd, known=()):
    return matched_word(cmd, known) is not None


def describe(cmd, known=()):
    """What `saani status` prints of a process: its executable's file name and the word that made it a test run, never the command line
    (its arguments can hold anything)."""
    tokens = words_of(cmd)
    return f"{base(tokens[0]) if tokens else '?'} ... {matched_word(cmd, known) or '?'}"


def outside_runs(procs, queue_pids, known=(), own=None):
    """The test runs that did not come through the queue: test commands that are not a job of the queue, nor below one, nor below another
    test command (pytest's own workers), nor Saani itself. `own` is this process: it and its parents are never reported."""
    by_pid = {p["pid"]: p for p in procs}
    skip = set(queue_pids)
    own = os.getpid() if own is None else own
    while own and own in by_pid and own not in skip:
        skip.add(own)
        own = by_pid[own]["ppid"]
    tests = {p["pid"] for p in procs if is_test_command(p["cmd"], known)}

    def below(pid, targets):
        seen = set()
        while pid in by_pid and pid not in seen:
            seen.add(pid)
            pid = by_pid[pid]["ppid"]
            if pid in targets:
                return True
        return False

    return [{**p, "what": describe(p["cmd"], known)} for p in procs
            if p["pid"] in tests and p["pid"] not in skip and not below(p["pid"], skip) and not below(p["pid"], tests)]


def known_commands(c):
    """The project's test commands as text, to recognise them in the process list."""
    out = []
    for cmd in list(c.landing or []) + list(c.full or []):
        text = " ".join(str(x) for x in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        if text.strip():
            out.append(text.strip())
    return out


def tasks(held, runner, waiting, auto, wt_paths, outside, now=None, job_since=None):
    """[line] for what is running, blocked or waiting. `held`: the landing lock ({branch, started}) or None; `runner`: the queue's running job
    ({kind, worktree, id}) or None; `waiting`: its waiting jobs (oldest first); `auto`: the auto run's record or None; `wt_paths`: the
    workers' worktrees [(path, branch)]; `outside`: test runs outside the queue (outside_runs); `job_since(job)`: when a job started or queued."""
    job_since = job_since or (lambda job: job.get("created"))
    lines = []

    name = issue_of

    if auto:
        lines.append(f"auto run {auto.get('id')}: running since {when(auto.get('started'), now)}"
                     + (f" ({auto.get('mode')} mode)" if auto.get("mode") else ""))
    if held == {}:
        lines.append("landing lock: held (unreadable lock: its holder and start are unknown)")
    elif held:
        lines.append(f"{issue_of(held.get('branch') or '?')} landing: running since {when(held.get('started'), now)} (holds the landing lock)")
    if runner:
        lines.append(f"{name(runner.get('worktree', '?'))} tests ({runner.get('kind')}): running since {when(job_since(runner), now)} (in the queue)")
    for job in waiting:
        on = f"the {runner.get('kind')} run of {name(runner.get('worktree', '?'))}" if runner else "the queue's turn"
        lines.append(f"{name(job.get('worktree', '?'))} tests ({job.get('kind')}): blocked since {when(job_since(job), now)}, waiting for {on}")
    for p in outside:
        lines.append(f"tests outside the queue: running since {when(p['since'], now) if p.get('since') else 'unknown'} "
                     f"(pid {p['pid']}: {p.get('what') or describe(p['cmd'])}) - the queue does not know it")
    return lines


def job_since(job, folder):
    """When a queue job started (its job file's last write) or was queued; for the runner without a job file, the runner lock's time; None
    when nothing says."""
    if job.get("state") == "waiting" and job.get("created"):
        return job["created"]
    for name in (f"job-{job.get('id')}.json", "runner.lock"):
        try:
            return datetime.datetime.fromtimestamp((pathlib.Path(folder) / name).stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            continue
    return job.get("created")


def render(lines, open_worktrees, outside_unknown=False, parked=()):
    """The text of the default `saani status`: the task lines, or the idle line; the open worktrees nothing runs in come after.
    `parked`: the issue numbers the auto run parked (blocked): their worktree is marked, it takes no slot of the run (#110)."""
    out = list(lines) if lines else [IDLE]
    if outside_unknown:
        out.append(OUTSIDE_NOTE)
    busy = {line.split(" ", 1)[0] for line in lines}
    held = {f"#{n}" for n in parked}
    quiet = [f"{issue_of(b) if b else pathlib.Path(str(p)).name} ({pathlib.Path(str(p)).name})"
             + (" parked: blocked, takes no slot" if b and issue_of(b) in held else "") for p, b in open_worktrees
             if (issue_of(b) if b else pathlib.Path(str(p)).name) not in busy]
    if quiet:
        out.append("open worktrees, no landing or tests in them: " + ", ".join(quiet))
    return out
