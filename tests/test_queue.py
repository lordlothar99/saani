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
    """#44: no guessed delays. The first run holds the runner until the second is queued, both seen in this test's own queue folder
    (tmp_path), so another job on the PC or a slow start cannot change the order."""
    log, calls, started, release = tmp_path / "saani-tests.jsonl", [], threading.Event(), threading.Event()
    folder = tmp_path / q.QUEUE_DIR

    def runner(command, cwd):
        calls.append(command)
        started.set()
        release.wait(15)
        return 3

    def waits_for(condition):
        deadline = time.monotonic() + 15
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert condition()

    a = threading.Thread(target=q.run, args=("landing", ["t"], tmp_path, tmp_path / "wt", "h", log), kwargs={"poll": 0.02, "runner": runner})
    a.start()
    waits_for(started.is_set)                                 # the first run really runs
    result = []
    b = threading.Thread(target=lambda: result.append(q.run("landing", ["t"], tmp_path, tmp_path / "wt", "h", log, poll=0.02, runner=runner)))
    b.start()
    waits_for(lambda: len(list(folder.glob("job-*.json"))) == 2)     # the second is queued behind it
    release.set()
    a.join(15)
    b.join(15)
    assert result == [3] and len(calls) == 1 and [r.get("shared", False) for r in q.rows(log)] == [False, True]


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


# ---------------------------------------------------------------- #58: a green result for the same tree and command is reused

def run_job(tmp_path, kind="landing", command=("t",), code=0, tree="tree1", fresh=False):
    calls = []
    got = q.run(kind, list(command), tmp_path, tmp_path / "wt", "h", tmp_path / "log.jsonl", poll=0.01,
                runner=lambda c, cwd: calls.append(c) or code, tree=tree, fresh=fresh)
    return got, calls


def test_a_green_result_of_the_same_tree_and_command_is_reused_and_logged(tmp_path, capsys):
    assert run_job(tmp_path) == (0, [["t"]])
    capsys.readouterr()
    assert run_job(tmp_path) == (0, [])                                       # not run again
    out = capsys.readouterr().out
    assert "reused: green on tree1 at " in out and "(job " in out
    assert [r.get("reused", False) for r in q.rows(tmp_path / "log.jsonl")] == [False, True]
    assert run_job(tmp_path, kind="land") == (0, [])                          # the run inside saani land is the landing tests


def test_a_full_run_takes_a_green_landing_result_but_a_landing_does_not_take_a_full_one(tmp_path):
    run_job(tmp_path, kind="land")
    assert run_job(tmp_path, kind="full") == (0, [])
    other = tmp_path / "other"
    other.mkdir()
    run_job(other, kind="full")
    assert run_job(other, kind="landing")[1] == [["t"]]


def test_a_dirty_checkout_has_no_tree_so_it_never_reuses_and_never_records(tmp_path):
    run_job(tmp_path)
    assert run_job(tmp_path, tree=None)[1] == [["t"]]                         # dirty: no tree given
    other = tmp_path / "o"
    other.mkdir()
    run_job(other, tree=None)
    assert not list((other / q.QUEUE_DIR).glob("result-*.json"))              # a dirty run is stored for no tree
    assert run_job(other)[1] == [["t"]]


def test_a_red_result_is_never_reused_and_deletes_the_green_before_it(tmp_path):
    assert run_job(tmp_path, code=1) == (1, [["t"]])
    assert run_job(tmp_path, code=1)[1] == [["t"]]                            # red: runs again
    assert run_job(tmp_path)[1] == [["t"]]                                    # runs, green now
    assert run_job(tmp_path, code=1, fresh=True)[1] == [["t"]]                # a red run after a green one...
    assert run_job(tmp_path)[1] == [["t"]]                                    # ...takes the green away: it runs


def test_another_command_or_another_tree_runs(tmp_path):
    run_job(tmp_path)
    assert run_job(tmp_path, command=("t", "-q"))[1] == [["t", "-q"]]
    assert run_job(tmp_path, tree="tree2")[1] == [["t"]]
    assert run_job(tmp_path, command=("t",))[1] == []                         # the first one is still there


def test_fresh_forces_a_run_and_the_new_result_is_stored(tmp_path):
    run_job(tmp_path)
    assert run_job(tmp_path, fresh=True)[1] == [["t"]]
    assert run_job(tmp_path)[1] == []


def test_a_result_expires_after_seven_days_and_the_store_stays_small(tmp_path, monkeypatch):
    folder = tmp_path / q.QUEUE_DIR
    run_job(tmp_path)
    now = q.datetime.datetime.now()
    assert q.find_green(folder, "tree1", ["t"], "landing", now + q.datetime.timedelta(days=6)) is not None
    assert q.find_green(folder, "tree1", ["t"], "landing", now + q.datetime.timedelta(days=8)) is None
    assert q.find_green(folder, "tree1", ["t"], "landing", now - q.datetime.timedelta(days=1)) is None     # a time from the future: unclear
    (result,) = folder.glob("result-*.json")
    data = json.loads(result.read_text(encoding="utf-8"))
    data["finished"] = (now - q.datetime.timedelta(days=8)).isoformat(timespec="seconds")
    result.write_text(json.dumps(data), encoding="utf-8")
    assert run_job(tmp_path, tree="tree2")[1] == [["t"]]                      # the old file is dropped when a result is stored
    assert not result.exists()
    monkeypatch.setattr(q, "RESULT_MAX", 3)
    for n in range(6):
        run_job(tmp_path, tree=f"x{n}")
    assert len(list(folder.glob("result-*.json"))) == 3


def scripted_tree(values, on_call=None):
    """A tree function that answers the given values in turn (the last one again and again); on_call(n) runs before answer n (1-based)."""
    seen = []

    def read():
        seen.append(1)
        if on_call:
            on_call(len(seen))
        return values[min(len(seen), len(values)) - 1]
    return read


def green_job(tmp_path, tree):
    q.store_result(tmp_path / q.QUEUE_DIR, {"id": "other", "kind": "landing", "command": ["t"]}, tree, 0)


def stored(tmp_path):
    return list((tmp_path / q.QUEUE_DIR).glob("result-*.json"))


def test_a_result_stored_by_another_job_while_this_one_waited_is_taken(tmp_path):
    tree = scripted_tree(["tree1"], on_call=lambda n: green_job(tmp_path, "tree1") if n == 2 else None)
    assert run_job(tmp_path, tree=tree) == (0, [])


def test_a_tree_that_changed_while_waiting_gets_no_stale_reuse(tmp_path):
    tree = scripted_tree(["tree1", "tree2", "tree2", "tree2"], on_call=lambda n: green_job(tmp_path, "tree1") if n == 2 else None)
    assert run_job(tmp_path, tree=tree) == (0, [["t"]])                       # green for tree1 exists, but the checkout is tree2 now
    assert q.find_green(tmp_path / q.QUEUE_DIR, "tree2", ["t"], "landing") is not None


def test_a_tree_that_changes_or_gets_dirty_during_the_run_stores_nothing(tmp_path):
    assert run_job(tmp_path, tree=scripted_tree(["tree1", "tree1", "tree1", "tree2"]))[1] == [["t"]]
    assert run_job(tmp_path, tree=scripted_tree(["tree1", "tree1", "tree1", None]))[1] == [["t"]]
    assert run_job(tmp_path, tree=scripted_tree(["tree1", "tree1", None, None]))[1] == [["t"]]
    assert not stored(tmp_path)
    assert run_job(tmp_path, tree=scripted_tree(["tree1"]))[1] == [["t"]]     # the plain path: same tree before and after
    assert len(stored(tmp_path)) == 1


def test_the_newest_results_are_kept_even_in_the_same_second(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "RESULT_MAX", 2)
    base = q.datetime.datetime.now()
    for n in range(4):
        q.store_result(tmp_path, {"id": str(n), "kind": "landing", "command": ["t"]}, f"x{n}", 0, base + q.datetime.timedelta(microseconds=n))
    assert sorted(q.read_json(p)["tree"] for p in tmp_path.glob("result-*.json")) == ["x2", "x3"]


def test_an_unreadable_or_foreign_result_file_is_not_a_result(tmp_path):
    folder = tmp_path / q.QUEUE_DIR
    folder.mkdir()
    key = q.result_key("tree1", ["t"], "landing")
    (folder / f"result-{key}.json").write_text("{not json", encoding="utf-8")
    assert run_job(tmp_path)[1] == [["t"]]
    path = folder / f"result-{key}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "tree": "other"}), encoding="utf-8")
    assert run_job(tmp_path)[1] == [["t"]]
