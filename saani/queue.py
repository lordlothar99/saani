"""Saani's test queue (docs/SAANI.md): one test job at a time on the PC, by priority. Standard library only; pure functions first
(tests/test_queue.py).

Two test runs at once slow each other down and break browser tests with timeouts; the workers use almost no CPU, the tests are the heavy
load. So every test run of a worker, of a landing or of the full suite goes through here:

  saani test [--kind landing|full] -- <command...>

There is no daemon. Each call writes its job file in the repository's shared git folder (`saani-tests/`, seen by every worktree), waits
until it is the first waiting job by (priority, arrival), takes the runner lock (a file made atomically) and runs its command with the whole
PC; the next job starts when the lock is released. Priority: `land` first (the run inside `saani land`, which holds the landing lock), then
`landing` (the landing tests run ahead of a landing), then `full`. `land` is internal: `saani land` queues it, the command line offers
`landing` and `full` (`tier1`, the old name of `landing`, is still read).
A job whose process is gone is dropped, and so is a runner lock whose holder is gone. Two `landing` jobs of the SAME worktree on the same
commit are one run: the second waits for the first and takes its result (two worktrees hold different code, so their runs are never merged).
Each job writes one line to build/saani-tests.jsonl of the main checkout: kind, worktree, wait and run seconds, exit code.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

PRIORITY = {"land": 0, "landing": 1, "full": 2}
KINDS = ("landing", "full")                     # what `saani test --kind` offers; `land` is queued by `saani land` only
RENAMED_KINDS = {"tier1": "landing"}            # old names, still read (the command line, old log rows and job files)
QUEUE_DIR = "saani-tests"
RUNNER = "runner.lock"
POLL_SECONDS = 5


# ---------------------------------------------------------------- pure functions

def kind_of(kind):
    """A kind under its current name (`tier1` -> `landing`)."""
    return RENAMED_KINDS.get(kind, kind)


def order(jobs):
    """The waiting jobs in the order they run: priority, then arrival, then id (a total order, the same for every caller)."""
    return sorted(jobs, key=lambda j: (PRIORITY.get(kind_of(j.get("kind")), 9), j.get("created", ""), j.get("id", "")))


def first_waiting(jobs):
    waiting = order([j for j in jobs if j.get("state") == "waiting"])
    return waiting[0] if waiting else None


def twin(job, jobs):
    """A `landing` job of the same worktree, commit and command that is running, or that finished after this one was queued: this job takes
    its result. One finished before is not taken (a run started again on purpose, after a flaky failure, really runs)."""
    if kind_of(job.get("kind")) != "landing":
        return None
    for j in jobs:
        if (j.get("id") != job.get("id") and kind_of(j.get("kind")) == "landing"
                and (j.get("state") == "running" or (j.get("state") == "done" and j.get("finished", "") >= job.get("created", "")))
                and (j.get("worktree"), j.get("head"), j.get("command")) == (job.get("worktree"), job.get("head"), job.get("command"))):
            return j
    return None


def summary(rows):
    """Per kind: jobs, median wait and median run seconds."""
    out = {}
    for kind in PRIORITY:
        mine = [r for r in rows if kind_of(r.get("kind")) == kind]
        if mine:
            med = lambda xs: sorted(xs)[len(xs) // 2]
            out[kind] = {"jobs": len(mine), "wait": med([r.get("wait", 0) for r in mine]), "run": med([r.get("run", 0) for r in mine])}
    return out


# ---------------------------------------------------------------- processes and files

def alive(pid):
    """True while the process exists. On Windows, os.kill(pid, 0) would end the process, so it asks the kernel instead."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        handle = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(handle, ctypes.byref(code))
        k.CloseHandle(handle)
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def patient(action, tries=50):
    """Do a file action again while Windows refuses it because another job is reading that file (WinError 32): each job polls the queue."""
    for n in range(tries):
        try:
            return action()
        except PermissionError:
            if n == tries - 1:
                raise
            time.sleep(0.02)


def read_json(path):
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path, data):
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    patient(lambda: os.replace(tmp, path))


def remove(path):
    patient(lambda: path.unlink(missing_ok=True))


def jobs_in(folder):
    """The job files of the queue; a job whose process is gone is removed (a done job is kept a while for its twin)."""
    out = []
    for path in folder.glob("job-*.json"):
        job = read_json(path)
        if not job:
            continue
        if job.get("state") == "done":
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:                                  # removed by another job meanwhile
                continue
            if age > 3600:
                remove(path)
                continue
        elif not alive(job.get("pid")):
            remove(path)
            continue
        out.append(job)
    return out


def take_runner(folder, job):
    """The runner lock, made atomically; a lock whose holder's process is gone is replaced. True when taken."""
    lock = folder / RUNNER
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = read_json(lock)
            if held is None and not lock.exists():
                continue
            if held and not alive(held.get("pid")):
                remove(lock)
                continue
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"id": job["id"], "pid": job["pid"], "kind": job["kind"], "worktree": job["worktree"]}, f)
        return True
    return False


def release_runner(folder, job):
    held = read_json(folder / RUNNER)
    if held and held.get("id") == job["id"]:
        remove(folder / RUNNER)


def run(kind, command, git_common, worktree, head, log_path, poll=POLL_SECONDS, runner=subprocess.call):
    """Queue the command, wait for its turn, run it with the whole PC; returns its exit code."""
    kind = kind_of(kind)
    if kind not in PRIORITY:
        raise SystemExit(f"unknown kind '{kind}': one of {', '.join(PRIORITY)}")
    folder = pathlib.Path(git_common) / QUEUE_DIR
    folder.mkdir(exist_ok=True)
    job = {"id": uuid.uuid4().hex[:12], "pid": os.getpid(), "kind": kind, "worktree": str(worktree), "head": head,
           "command": command, "state": "waiting", "created": datetime.datetime.now().isoformat(timespec="microseconds")}
    path = folder / f"job-{job['id']}.json"
    write_json(path, job)
    queued, told = time.time(), None
    try:
        while True:
            jobs = jobs_in(folder)
            other = twin(job, jobs)
            if other and other["state"] == "done":
                print(f"saani test: the same landing run of this worktree and commit just finished: its result is taken (exit {other['code']})")
                record(log_path, job, queued, time.time(), time.time(), other["code"], shared=True)
                return other["code"]
            head_job = first_waiting(jobs)
            if not other and head_job and head_job["id"] == job["id"] and take_runner(folder, job):
                # The list above can be stale on a busy PC (listed before a landing queued, read after the runner freed): look once more
                # now that the runner is held, and give it back if a job that goes first is waiting (2026-11-21, a tier 1 overtook a landing).
                again = first_waiting(jobs_in(folder))
                if not again or again["id"] == job["id"]:
                    break
                release_runner(folder, job)
            busy = read_json(folder / RUNNER) or {}
            note = f"waiting: {busy.get('kind', '?')} of {pathlib.Path(busy.get('worktree', '?')).name} runs" if busy else "waiting for a turn"
            if note != told:
                print(f"saani test ({kind}): {note}; {len([j for j in jobs if j.get('state') == 'waiting'])} job(s) waiting")
                told = note
            time.sleep(poll)
        job["state"] = "running"
        write_json(path, job)
        started = time.time()
        try:
            code = runner(command, cwd=worktree)
        finally:
            release_runner(folder, job)
        job.update(state="done", code=code, finished=datetime.datetime.now().isoformat(timespec="microseconds"))
        write_json(path, job)
        record(log_path, job, queued, started, time.time(), code)
        return code
    except BaseException:
        if job["state"] != "done":
            remove(path)
        release_runner(folder, job)
        raise


def record(log_path, job, queued, started, ended, code, shared=False):
    row = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "kind": job["kind"], "worktree": pathlib.Path(job["worktree"]).name,
           "wait": round(started - queued), "run": round(ended - started), "code": code}
    if shared:
        row["shared"] = True
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def rows(log_path):
    if not log_path.is_file():
        return []
    out = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


if __name__ == "__main__":
    sys.exit("use: saani test [--kind landing|full] -- <command...>")
