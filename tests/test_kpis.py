"""saani/kpis.py: issues landed per day from `refs #n`, the landings and their conflicts by file, the test queue's waits and runs,
and a baseline window."""
import datetime
import pathlib

import pytest

from saani import kpis as fk



D = datetime.date
SINCE, UNTIL = D(2026, 11, 21), D(2026, 11, 22)


def test_a_commit_names_its_issues_with_refs_in_any_list_form():
    assert fk.issues_in("Test queue: one job at a time (refs #18, #15)") == [18, 15]
    assert fk.issues_in("Docs review (refs #530, #538 and #471)") == [530, 538, 471]
    assert fk.issues_in("Fix\n\nrefs #12\nWhy: x\nUndo: y") == [12]
    assert fk.issues_in("Mentions #99 without refs") == []


def test_issues_landed_per_day_counts_each_issue_once_a_day_and_skips_generated_commits():
    commits = [{"date": "2026-11-21", "subject": "A (refs #1)", "body": ""},
               {"date": "2026-11-21", "subject": "B (refs #1, #2)", "body": ""},
               {"date": "2026-11-22", "subject": "C (refs #1)", "body": ""},
               {"date": "2026-11-22", "subject": "Generated files rebuilt at landing (refs #3)", "body": ""},
               {"date": "2026-11-20", "subject": "Before (refs #4)", "body": ""}]
    k = fk.commit_kpis(commits, SINCE, UNTIL)
    assert k["commits"] == 3 and k["issues"] == 2 and k["days"] == 2
    assert k["per_day"] == {"2026-11-21": 2, "2026-11-22": 1}
    assert k["issues_per_day"] == 1.5


def test_a_window_still_running_counts_only_the_days_elapsed():
    commits = [{"date": "2026-11-21", "subject": "A (refs #1, #2)", "body": ""}]
    k = fk.commit_kpis(commits, SINCE, D(2026, 12, 4), today=D(2026, 11, 22))
    assert k["days"] == 2 and k["issues_per_day"] == 1.0
    assert fk.commit_kpis(commits, SINCE, UNTIL, today=D(2026, 12, 31))["days"] == 2      # a past window keeps its length


def test_landings_give_conflicts_per_landing_and_by_file_inside_the_window_only():
    rows = [{"time": "2026-11-21T10:00:00", "result": "landed", "conflicts": ["docs/RUNBOOK.md"], "seconds": 300},
            {"time": "2026-11-21T12:00:00", "result": "landed", "conflicts": [], "auto_resolved": ["docs/SPEC.md"], "seconds": 500},
            {"time": "2026-11-22T09:00:00", "result": "landed", "conflicts": ["docs/RUNBOOK.md", "ui/app/index.html"], "seconds": 400},
            {"time": "2026-11-22T09:30:00", "result": "stopped", "conflicts": ["docs/SAANI.md"]},
            {"time": "2026-11-23T09:00:00", "result": "landed", "conflicts": ["x"], "seconds": 1}]
    k = fk.landing_kpis(rows, SINCE, UNTIL)
    assert (k["landed"], k["stopped"], k["with_conflicts"]) == (3, 1, 2)
    assert k["conflicts_per_landing"] == 1.0
    assert k["by_file"] == {"docs/RUNBOOK.md": 2, "ui/app/index.html": 1}     # a stopped landing's conflicts are not counted
    assert k["auto_resolved"] == 1 and k["median_seconds"] == 400


def test_no_landing_and_no_test_job_give_zeros_not_an_error():
    assert fk.landing_kpis([], SINCE, UNTIL) == {"landed": 0, "stopped": 0, "with_conflicts": 0, "conflicts_per_landing": 0.0,
                                                 "by_file": {}, "auto_resolved": 0, "median_seconds": None}
    assert fk.test_kpis([], SINCE, UNTIL) == {}


def test_test_jobs_give_per_kind_medians_and_failures():
    rows = [{"time": "2026-11-21T10:00:00", "kind": "tier1", "wait": 0, "run": 240, "code": 0},
            {"time": "2026-11-21T11:00:00", "kind": "tier1", "wait": 60, "run": 300, "code": 1},
            {"time": "2026-11-22T11:00:00", "kind": "tier1", "wait": 30, "run": 200, "code": 0},
            {"time": "2026-11-22T12:00:00", "kind": "full", "wait": 10, "run": 900, "code": 0},
            {"time": "bad", "kind": "full", "wait": 0, "run": 0, "code": 0}]
    k = fk.test_kpis(rows, SINCE, UNTIL)
    assert k["tier1"] == {"jobs": 3, "failed": 1, "median_wait": 30, "median_run": 240}
    assert k["full"] == {"jobs": 1, "failed": 0, "median_wait": 10, "median_run": 900}


def test_the_baseline_is_the_window_of_the_same_length_just_before():
    assert fk.baseline_window(D(2026, 11, 21), D(2026, 12, 4)) == (D(2026, 11, 7), D(2026, 11, 20))
    assert fk.baseline_window(SINCE, SINCE) == (D(2026, 11, 20), D(2026, 11, 20))


def test_the_report_says_what_it_cannot_measure_and_adds_a_git_baseline():
    r = fk.report([], [], [], SINCE, UNTIL, baseline=True)
    assert set(r["not_measured"]) == {"owner_waiting_time", "owner_interventions"}
    assert r["baseline"]["window"] == {"since": "2026-11-19", "until": "2026-11-20"} and "git" in r["baseline"]
