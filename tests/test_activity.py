"""`saani status`: what the agents do now (saani/activity.py, #57): idle, running, blocked, a test run outside the queue."""
import datetime
import pathlib

from saani import activity

NOW = datetime.datetime(2026, 11, 25, 15, 0)
WT = pathlib.Path("/wt/acme-webshop-wt")


def text(lines, worktrees=(), unknown=False):
    return "\n".join(activity.render(lines, worktrees, outside_unknown=unknown))


def test_idle_is_one_line():
    assert activity.tasks(None, None, [], None, [], [], now=NOW) == []
    assert activity.render([], []) == [activity.IDLE]


def test_idle_still_lists_the_open_worktrees():
    out = activity.render([], [(WT / "12-cart", "task/12-cart")])
    assert out[0] == activity.IDLE
    assert "#12" in out[1] and "nothing running" in out[1]


def test_running_tests_and_a_landing_are_told_with_since():
    runner = {"id": "a", "kind": "landing", "worktree": str(WT / "12-cart")}
    held = {"branch": "task/13-search", "started": "2026-11-25T14:30:00"}
    lines = activity.tasks(held, runner, [], {"id": "2026-11-25_1400", "started": "2026-11-25T13:00:00Z", "mode": "away"}, [], [],
                           now=NOW, job_since=lambda j: "2026-11-25T14:45:00")
    shown = text(lines)
    assert "#13 landing: running since 14:30" in shown
    assert "#12 tests (landing): running since 14:45" in shown
    assert "auto run 2026-11-25_1400: running" in shown
    assert activity.IDLE not in shown


def test_a_waiting_job_is_blocked_on_the_running_one():
    runner = {"id": "a", "kind": "full", "worktree": str(WT / "12-cart")}
    waiting = [{"id": "b", "kind": "landing", "worktree": str(WT / "13-search"), "created": "2026-11-24T09:05:00"}]
    lines = activity.tasks(None, runner, waiting, None, [], [], now=NOW)
    blocked = [line for line in lines if line.startswith("#13")][0]
    assert "blocked since 2026-11-24 09:05" in blocked
    assert "waiting for the full run of #12" in blocked


def test_a_test_run_outside_the_queue_is_not_idle():
    procs = [{"pid": 10, "ppid": 1, "since": "2026-11-25T14:10:00", "cmd": "bash"},
             {"pid": 11, "ppid": 10, "since": "2026-11-25T14:10:05", "cmd": r'"C:\Python\python.exe" -m pytest -q tests'},
             {"pid": 12, "ppid": 11, "since": "2026-11-25T14:10:06", "cmd": "python -m pytest worker"},
             {"pid": 13, "ppid": 1, "since": None, "cmd": "claude -p 'run pytest later'"}]
    found = activity.outside_runs(procs, [], own=999)
    assert [p["pid"] for p in found] == [11]                      # not the worker below it, not a chat that mentions the word
    shown = text(activity.tasks(None, None, [], None, [], found, now=NOW))
    assert "tests outside the queue: running since 14:10" in shown and "pid 11" in shown
    assert activity.IDLE not in shown


def test_a_queue_job_and_what_it_runs_are_not_outside():
    procs = [{"pid": 20, "ppid": 1, "since": None, "cmd": "python -I saani.py test --kind landing -- python -m pytest -q"},
             {"pid": 21, "ppid": 20, "since": None, "cmd": "python -m pytest -q"},
             {"pid": 30, "ppid": 1, "since": None, "cmd": "python scripts/test_all.py"}]
    assert activity.outside_runs(procs, [20], own=999) == []
    assert [p["pid"] for p in activity.outside_runs(procs, [20], known=["scripts/test_all.py"], own=999)] == [30]


def test_the_status_command_itself_is_never_reported():
    procs = [{"pid": 5, "ppid": 1, "since": None, "cmd": "python -m pytest -q"}, {"pid": 6, "ppid": 5, "since": None, "cmd": "python saani.py status"}]
    assert activity.outside_runs(procs, [], own=6) == []


def test_an_unreadable_process_list_is_said_not_hidden():
    out = text([], unknown=True)
    assert activity.IDLE in out and "tests may run outside it" in out


def test_process_lists_are_parsed():
    assert activity.parse_windows("4|1|2026-11-25T14:10:05|python -m pytest\nbad line\n")[0] == {
        "pid": 4, "ppid": 1, "since": "2026-11-25T14:10:05", "cmd": "python -m pytest"}
    row = activity.parse_ps("  77     1 Wed Nov 25 14:02:11 2026 python -m pytest -q\n")[0]
    assert row["pid"] == 77 and row["since"] == "2026-11-25T14:02:11" and row["cmd"] == "python -m pytest -q"
