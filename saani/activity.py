"""What the agents are doing right now (`saani status`): one line per task that runs, is blocked or waits, or one line saying everything
is idle. Best effort, and honest about it: Saani knows its own machinery (the landing lock, the test queue, the auto run's record, the
worktrees) and can look at the PC's processes for a test run started outside the queue; it does not know what a Claude session thinks.
Standard library only; the functions take what they read as arguments so they can be tested without a PC."""
import datetime
import os
import pathlib
import re
import subprocess

IDLE = "everything is idle: nothing runs, nothing is blocked, nothing waits"
OUTSIDE_NOTE = "queue idle, but tests may run outside it (the process list could not be read)"
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
    """The issue number in `<type>/<n>-<slug>` or `<n>-<slug>`, as `#n`; the name itself when there is none."""
    last = str(branch_or_path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    m = ISSUE_IN_NAME.search(last)
    return f"#{m.group(1)}" if m else last


def processes():
    """[{pid, ppid, since, cmd}] of the running processes, best effort: PowerShell on Windows, `ps` elsewhere; None when it cannot be read."""
    try:
        if os.name == "nt":
            script = ("Get-CimInstance Win32_Process | ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.ParentProcessId, "
                      "$(if ($_.CreationDate) { $_.CreationDate.ToString('s') }), $_.CommandLine }")
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20)
            if out.returncode != 0:
                return None
            return parse_windows(out.stdout)
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,lstart=,args="], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, env={**os.environ, "LC_ALL": "C"})
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


def is_test_command(cmd, known=()):
    """True when the command line starts a test run: pytest or unittest (alone or as `python -m ...`), or one of the project's own test
    commands (`[tests] landing` / `full`). A line that only mentions the word later (an editor, a chat) is not one."""
    tokens = [t.strip('"\'') for t in cmd.split()]
    if not tokens:
        return False
    first = pathlib.PurePath(tokens[0].replace("\\", "/")).name.lower().removesuffix(".exe")
    if first in ("pytest", "py.test"):
        return True
    if first.startswith("python") or first == "py":
        head = [pathlib.PurePath(t.replace("\\", "/")).name.lower().removesuffix(".exe") for t in tokens[1:4]]
        if any(t in TEST_WORDS for t in head):
            return True
        return any(k and k in cmd for k in known)
    return False


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

    return [p for p in procs if p["pid"] in tests and p["pid"] not in skip and not below(p["pid"], skip) and not below(p["pid"], tests)]


def known_commands(c):
    """The project's test commands as text, to recognise them in the process list."""
    out = []
    for cmd in list(c.landing) + list(c.full):
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
    if held:
        lines.append(f"{issue_of(held.get('branch') or '?')} landing: running since {when(held.get('started'), now)} (holds the landing lock)")
    if runner:
        lines.append(f"{name(runner.get('worktree', '?'))} tests ({runner.get('kind')}): running since {when(job_since(runner), now)} (in the queue)")
    for job in waiting:
        on = f"the {runner.get('kind')} run of {name(runner.get('worktree', '?'))}" if runner else "the queue's turn"
        lines.append(f"{name(job.get('worktree', '?'))} tests ({job.get('kind')}): blocked since {when(job_since(job), now)}, waiting for {on}")
    for p in outside:
        lines.append(f"tests outside the queue: running since {when(p['since'], now) if p.get('since') else 'unknown'} "
                     f"(pid {p['pid']}: {p['cmd'][:100]}) - the queue does not know it")
    return lines


def render(lines, open_worktrees, outside_unknown=False):
    """The text of the default `saani status`: the task lines, or the idle line; the open worktrees nothing runs in come after."""
    out = list(lines) if lines else [IDLE]
    if outside_unknown:
        out.append(OUTSIDE_NOTE)
    busy = {line.split(" ", 1)[0] for line in lines}
    quiet = [f"{issue_of(b) if b else pathlib.Path(str(p)).name} ({pathlib.Path(str(p)).name})" for p, b in open_worktrees
             if (issue_of(b) if b else pathlib.Path(str(p)).name) not in busy]
    if quiet:
        out.append("open worktrees, nothing running in them: " + ", ".join(quiet))
    return out
