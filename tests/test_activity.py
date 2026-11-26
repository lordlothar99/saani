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
    assert "#12" in out[1] and "no landing or tests" in out[1]


def test_status_says_a_parked_worktree_is_parked():
    out = activity.render([], [(WT / "12-cart", "task/12-cart"), (WT / "13-pay", "task/13-pay")], parked=[12])
    assert "#12 (12-cart) parked: blocked, takes no slot" in out[1] and "#13 (13-pay)" in out[1] and "13-pay) parked" not in out[1]


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
    assert [p["pid"] for p in activity.outside_runs(procs, [20], known=["python scripts/test_all.py"], own=999)] == [30]


def test_a_project_test_command_is_matched_by_its_words_for_any_executable():
    known = ["make check", "python scripts/test_all.py"]
    assert activity.matched_word("make check", known) == "[tests] command"
    assert activity.matched_word(r'"C:\Python\python.exe" scripts/test_all.py --fast', known) == "[tests] command"
    assert activity.matched_word("/usr/bin/make check", known) == "[tests] command"
    assert activity.matched_word("cmd /c make check", known) == "[tests] command"                # the line ends with the command
    assert activity.matched_word("editor notes about make check today", known) is None           # a mention is not the command
    assert activity.matched_word("make checkout", known) is None
    assert activity.matched_word("python other.py", known) is None


def test_only_the_executable_and_the_word_are_printed_never_the_command_line():
    out = activity.describe(r'"C:\Python\python.exe" -m pytest --token=SECRET123 tests')
    assert out == "python ... pytest" and "SECRET" not in out
    procs = [{"pid": 11, "ppid": 1, "since": None, "cmd": "python -m pytest --token=SECRET123"}]
    shown = text(activity.tasks(None, None, [], None, [], activity.outside_runs(procs, [], own=999), now=NOW))
    assert "SECRET" not in shown and "pid 11: python ... pytest" in shown


def test_known_commands_join_the_configured_argv(cfg):
    cfg.landing = [["python", "-m", "pytest", "-q"], "make check", []]
    cfg.full = ["make all"]
    assert activity.known_commands(cfg) == ["python -m pytest -q", "make check", "make all"]


def test_job_since_uses_the_job_file_then_the_runner_lock(tmp_path):
    assert activity.job_since({"state": "waiting", "created": "2026-11-25T09:00:00"}, tmp_path) == "2026-11-25T09:00:00"
    assert activity.job_since({"state": "running", "id": "x"}, tmp_path) is None                      # nothing says
    (tmp_path / "runner.lock").write_text("{}")
    lock = activity.job_since({"state": "running", "id": "x"}, tmp_path)
    assert lock and datetime.datetime.fromisoformat(lock).year >= 2026
    (tmp_path / "job-x.json").write_text("{}")
    assert activity.job_since({"state": "running", "id": "x"}, tmp_path)


def test_an_unreadable_landing_lock_is_not_free():
    assert "held (unreadable lock" in "\n".join(activity.tasks({}, None, [], None, [], [], now=NOW))
    assert activity.tasks(None, None, [], None, [], [], now=NOW) == []


def run_with(monkeypatch, result=None, error=None, os_name="posix"):
    def fake(*a, **k):
        assert k["timeout"] == activity.LIST_TIMEOUT <= 5
        if error:
            raise error
        return result
    monkeypatch.setattr(activity.os, "name", os_name)
    monkeypatch.setattr(activity.subprocess, "run", fake)
    return activity.processes()


def test_processes_failures_are_none_not_an_empty_list(monkeypatch):
    import subprocess
    for name in ("posix", "nt"):
        assert run_with(monkeypatch, error=OSError("no ps"), os_name=name) is None
        assert run_with(monkeypatch, error=subprocess.TimeoutExpired("ps", 4), os_name=name) is None
        assert run_with(monkeypatch, result=subprocess.CompletedProcess([], 1, stdout="", stderr="x"), os_name=name) is None
    ok = subprocess.CompletedProcess([], 0, stdout="  5  1 Wed Nov 25 14:02:11 2026 python -m pytest\n", stderr="")
    assert run_with(monkeypatch, result=ok)[0]["pid"] == 5


def test_a_windows_row_broken_by_a_newline_is_skipped():
    rows = activity.parse_windows("4|1|2026-11-25T14:10:05|python -c\nprint('x')\n7|1||x\n")
    assert [r["pid"] for r in rows] == [4, 7]


# ---------------------------------------------------------------- the command

class Args:
    action = None
    stats = False


def fake_machinery(monkeypatch, tmp_path, held=None, procs=()):
    from saani import auto, core, queue
    monkeypatch.setattr(core, "worktrees_warning", lambda c: "warning: the worktrees folder")
    monkeypatch.setattr(core, "lock_path", lambda cwd=None: tmp_path / "saani-landing.lock")
    monkeypatch.setattr(core, "worktrees", lambda cwd=None: [(tmp_path / "main", "dev"), (tmp_path / "wt" / "12-cart", "task/12-cart")])
    monkeypatch.setattr(core, "in_wt_dir", lambda p: "wt" in pathlib.Path(p).parts)
    monkeypatch.setattr(core, "main_checkout", lambda cwd=None: tmp_path)
    monkeypatch.setattr(core, "prune_candidates", lambda cwd=None: ([], []))
    monkeypatch.setattr(core, "finished_auto_runs", lambda *a, **k: [])
    monkeypatch.setattr(auto, "read_record", lambda: None)
    monkeypatch.setattr(activity, "processes", lambda: None if procs is None else list(procs))
    if held is not None:
        (tmp_path / "saani-landing.lock").write_text(held)
    return core, queue


def test_status_default_view_says_idle_after_the_warning(monkeypatch, tmp_path, capsys):
    core, _ = fake_machinery(monkeypatch, tmp_path)
    assert core.cmd_status(Args()) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("warning:") and out[1] == activity.IDLE
    assert "#12" in out[2] and "landings:" not in "\n".join(out)


def test_status_default_view_names_a_landing_a_dead_runner_and_a_test_run_outside(monkeypatch, tmp_path, capsys):
    core, queue = fake_machinery(monkeypatch, tmp_path, held='{"branch": "task/12-cart", "started": "2026-11-25T14:30:00"}',
                                 procs=[{"pid": 11, "ppid": 1, "since": "2026-11-25T14:10:05", "cmd": "python -m pytest -q"}])
    folder = tmp_path / queue.QUEUE_DIR
    folder.mkdir()
    (folder / queue.RUNNER).write_text('{"id": "a", "pid": 999999999, "kind": "full", "worktree": "/wt/13-search"}')
    assert core.cmd_status(Args()) == 0
    out = capsys.readouterr().out
    assert "#12 landing: running since" in out and "tests outside the queue" in out and "pid 11: python ... pytest" in out
    assert "#13 tests" not in out and activity.IDLE not in out                      # the runner's process is gone


def test_status_says_when_the_process_list_cannot_be_read(monkeypatch, tmp_path, capsys):
    core, _ = fake_machinery(monkeypatch, tmp_path, procs=None)
    core.cmd_status(Args())
    assert activity.OUTSIDE_NOTE in capsys.readouterr().out


def test_status_stats_prints_the_statistics_and_reads_no_process_list(monkeypatch, tmp_path, capsys):
    core, _ = fake_machinery(monkeypatch, tmp_path)
    monkeypatch.setattr(activity, "processes", lambda: (_ for _ in ()).throw(AssertionError("no process list under --stats")))
    args = Args()
    args.stats = True
    assert core.cmd_status(args) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("warning:") and out[1] == "statistics:"
    assert any(line.startswith("landings: 0") for line in out) and activity.IDLE not in out


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
