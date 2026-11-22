"""saani/queue.py: one test job at a time on the PC, a landing first, then the landing tests, then the full run; dead jobs and dead runners are
dropped; the same landing run of one worktree and commit is shared."""
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

import pytest

from saani import queue as q




def job(id_, kind, created, state="waiting", **kw):
    return {"id": id_, "kind": kind, "created": created, "state": state, "worktree": "wt", "head": "abc", "command": ["t"], **kw}


def test_a_landing_runs_first_then_the_landing_tests_then_the_full_run_each_by_arrival():
    jobs = [job("f", "full", "1"), job("t2", "landing", "3"), job("l", "land", "4"), job("t1", "landing", "2")]
    assert [j["id"] for j in q.order(jobs)] == ["l", "t1", "t2", "f"]
    assert q.first_waiting(jobs + [job("r", "land", "0", state="running")])["id"] == "l"
    assert q.first_waiting([job("r", "landing", "0", state="running")]) is None


def test_the_same_landing_run_of_one_worktree_and_commit_is_shared_but_not_across_worktrees_or_from_before():
    me = job("me", "landing", "5")
    assert q.twin(me, [job("a", "landing", "1", state="running")])["id"] == "a"
    assert q.twin(me, [job("a", "landing", "1", state="done", finished="6", code=0)])["id"] == "a"
    assert q.twin(me, [job("a", "landing", "1", state="done", finished="4", code=1)]) is None          # finished before: a rerun really runs
    assert q.twin(me, [job("a", "landing", "1", state="running", worktree="other")]) is None           # other code
    assert q.twin(me, [job("a", "landing", "1", state="running", head="def")]) is None
    assert q.twin(me, [job("a", "landing", "1", state="waiting")]) is None
    assert q.twin(job("me", "full", "5"), [job("a", "full", "1", state="running")]) is None          # only the landing tests are shared


def test_the_summary_gives_jobs_and_median_wait_and_run_per_kind():
    rows = [{"kind": "landing", "wait": 0, "run": 60}, {"kind": "landing", "wait": 30, "run": 90}, {"kind": "landing", "wait": 10, "run": 70},
            {"kind": "full", "wait": 5, "run": 1200}]
    assert q.summary(rows) == {"landing": {"jobs": 3, "wait": 10, "run": 70}, "full": {"jobs": 1, "wait": 5, "run": 1200}}


def test_this_process_is_alive_and_a_finished_one_is_not():
    assert q.alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not q.alive(p.pid) and not q.alive(None) and not q.alive(-1)


def test_a_job_of_a_dead_process_and_a_dead_runner_lock_are_dropped(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    (tmp_path / "job-dead.json").write_text(json.dumps(job("dead", "land", "0", pid=p.pid)), encoding="utf-8")
    (tmp_path / "job-mine.json").write_text(json.dumps(job("mine", "landing", "1", pid=os.getpid())), encoding="utf-8")
    assert [j["id"] for j in q.jobs_in(tmp_path)] == ["mine"] and not (tmp_path / "job-dead.json").exists()
    (tmp_path / q.RUNNER).write_text(json.dumps({"id": "dead", "pid": p.pid}), encoding="utf-8")
    assert q.take_runner(tmp_path, job("mine", "landing", "1", pid=os.getpid()))
    assert not q.take_runner(tmp_path, job("next", "landing", "2", pid=os.getpid()))     # held by a live process


def test_jobs_run_one_at_a_time_and_a_landing_overtakes_waiting_landing_tests(tmp_path):
    log, events, lock = tmp_path / "build" / "saani-tests.jsonl", [], threading.Lock()
    queued = lambda: len(list((tmp_path / q.QUEUE_DIR).glob("job-*.json")))

    def until(n):                                             # waits on the queue's files, never on a guessed delay: a busy PC
        deadline = time.monotonic() + 10                      # starts threads late (2026-11-21: this test failed tier 1 twice)
        while queued() < n and time.monotonic() < deadline:
            time.sleep(0.01)
        assert queued() >= n

    def runner(name, seconds, hold=0):
        def run(command, cwd):
            with lock:
                events.append(("start", name))
            if hold:
                until(hold)                                   # the full run keeps the runner until the other two wait
            time.sleep(seconds)
            with lock:
                events.append(("end", name))
            return 0
        return run

    def go(kind, name, seconds, worktree, hold=0):
        q.run(kind, [name], tmp_path, tmp_path / worktree, "h", log, poll=0.02, runner=runner(name, seconds, hold))

    first = threading.Thread(target=go, args=("full", "full", 0.05, "a", 3))
    first.start()
    deadline = time.monotonic() + 10                          # the full run has really started (holding the runner)
    while ("start", "full") not in events and time.monotonic() < deadline:
        time.sleep(0.01)
    t = threading.Thread(target=go, args=("landing", "landing", 0.05, "b"))
    t.start()
    until(2)                                                  # the landing tests wait first
    land = threading.Thread(target=go, args=("land", "land", 0.05, "c"))
    land.start()
    for th in (first, t, land):
        th.join(15)
    assert events == [("start", "full"), ("end", "full"), ("start", "land"), ("end", "land"), ("start", "landing"), ("end", "landing")]
    rows = q.rows(log)
    assert [r["kind"] for r in rows] == ["full", "land", "landing"] and all(r["code"] == 0 for r in rows)
    assert rows[2]["wait"] >= 0 and not list(tmp_path.glob(f"{q.QUEUE_DIR}/runner.lock"))


def test_the_second_identical_landing_run_takes_the_first_ones_result(tmp_path):
    log, calls = tmp_path / "saani-tests.jsonl", []

    def runner(command, cwd):
        calls.append(command)
        time.sleep(0.3)
        return 3

    a = threading.Thread(target=q.run, args=("landing", ["t"], tmp_path, tmp_path / "wt", "h", log), kwargs={"poll": 0.02, "runner": runner})
    a.start()
    time.sleep(0.1)
    assert q.run("landing", ["t"], tmp_path, tmp_path / "wt", "h", log, poll=0.02, runner=runner) == 3
    a.join(5)
    assert len(calls) == 1 and [r.get("shared", False) for r in q.rows(log)] == [False, True]


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        q.run("nightly", ["t"], tmp_path, tmp_path, "h", tmp_path / "log.jsonl")


def test_a_job_that_took_the_runner_on_a_stale_list_gives_it_back(tmp_path, monkeypatch):
    """2026-11-21: on a busy PC a tier 1 listed the queue before a landing queued, read it after the runner freed, and ran first.
    Holding the runner, it looks once more and yields to the landing."""
    folder = tmp_path / q.QUEUE_DIR
    folder.mkdir()
    land = {"id": "land1", "pid": q.os.getpid(), "kind": "land", "worktree": "c", "head": "h", "command": ["x"], "state": "waiting",
            "created": "2026-11-21T00:00:00.000000"}
    q.write_json(folder / "job-land1.json", land)
    real, calls, started = q.jobs_in, [], []

    def jobs_in(f):
        calls.append(1)
        found = real(f)
        if len(calls) == 1:                                   # the stale first look: the landing is not in it
            return [j for j in found if j["id"] != "land1"]
        if len(calls) == 4:                                   # the landing has run meanwhile
            q.remove(folder / "job-land1.json")
            return [j for j in found if j["id"] != "land1"]
        return found

    monkeypatch.setattr(q, "jobs_in", jobs_in)
    code = q.run("landing", ["t"], tmp_path, tmp_path / "b", "h", tmp_path / "log.jsonl", poll=0.01,
                 runner=lambda command, cwd: started.append(len(calls)) or 0)
    assert code == 0 and started and started[0] >= 4                          # it waited for the landing instead of running first


def test_the_old_kind_tier1_is_read_as_landing():
    """#48: tier1 is the old name of landing: an old job file, an old log row and the command line still work."""
    assert q.kind_of("tier1") == "landing" and q.kind_of("full") == "full"
    jobs = [job("f", "full", "1"), job("old", "tier1", "3"), job("new", "landing", "2")]
    assert [j["id"] for j in q.order(jobs)] == ["new", "old", "f"]
    assert q.twin(job("me", "landing", "5"), [job("a", "tier1", "1", state="running")])["id"] == "a"
    assert q.summary([{"kind": "tier1", "wait": 1, "run": 2}]) == {"landing": {"jobs": 1, "wait": 1, "run": 2}}
    assert q.KINDS == ("landing", "full") and "land" in q.PRIORITY
