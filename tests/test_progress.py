"""The progress bar of a background test run: pytest's output read, the remaining time estimated from earlier durations. No subprocess."""
import json

from saani import progress


def test_pytest_quiet_output_is_counted_with_its_failures():
    text = "..F.s\n..E\n"
    assert progress.parse(text) == (8, 2, False, "")


def test_the_last_line_ends_the_run():
    text = "....\n=== FAILURES ===\n_ test_x _\nboom\n2 failed, 2 passed in 1.23s\n"
    done, failed, finished, summary = progress.parse(text)
    assert (done, finished) == (4, True)
    assert summary == "2 failed, 2 passed in 1.23s"


def test_verbose_output_with_file_names_is_counted():
    text = "=== test session starts ===\ncollected 3 items\n\ntests/test_a.py ..F    [100%]\n"
    assert progress.parse(text)[:2] == (3, 1)


def test_a_test_without_history_counts_as_its_files_average_else_the_median():
    history = {"tests/a.py::t1": 2.0, "tests/a.py::t2": 4.0, "tests/b.py::t1": 10.0}
    est = progress.estimates(["tests/a.py::t1", "tests/a.py::new", "tests/c.py::new"], history)
    assert est == [2.0, 3.0, 4.0]


def test_one_freak_duration_is_clipped():
    history = {f"t{i}": 1.0 for i in range(30)} | {"freak": 4220.0}
    assert progress.clip(history)["freak"] == 30.0
    assert progress.clip({"a": 4220.0}) == {"a": 4220.0}           # too few tests to tell a freak


def test_the_remaining_time_follows_this_runs_speed():
    est = [10.0] * 100
    assert progress.remaining(est, 0, 0, prior=1.5) == 1500.0          # nothing run yet: earlier runs' overhead
    slow = progress.remaining(est, 60, elapsed=1200, prior=1.0)          # 600 s of estimate took 1200 s
    assert slow == 400 * 2.0


def test_the_overhead_is_the_median_of_the_last_runs(tmp_path):
    runs = tmp_path / "runs.json"
    assert progress.overhead(runs) == progress.DEFAULT_OVERHEAD
    runs.write_text(json.dumps([{"wall": 12, "sum": 10}, {"wall": 15, "sum": 10}, {"wall": 30, "sum": 10}]), encoding="utf-8")
    assert progress.overhead(runs) == 1.5


def test_only_the_pytest_arguments_reach_the_collection():
    assert progress.pytest_args(["python", "-m", "pytest", "-q", "tests/x.py", "--durations=5", "-k", "y"]) == ["tests/x.py", "-k", "y"]
    assert progress.pytest_args(["python", "build.py"]) == []


def test_bar_and_minutes():
    assert progress.bar(0.5, width=4) == "██░░"
    assert progress.bar(2.0, width=2) == "██"
    assert progress.fmt_minutes(30) == "<1 min"
    assert progress.fmt_minutes(150) == "2 min"
