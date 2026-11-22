"""saani/ci.py: GitHub Actions within the free minutes, the PC for the rest; a refused run falls back to the PC; the PC tests the pull
request's head in its own worktree, through the test queue."""
import datetime
import pathlib

import pytest

from saani import ci as fci


@pytest.fixture(autouse=True)
def jobs(cfg):
    """A project with one heavy job and two small automatic workflows, and its PC checks."""
    cfg.ci_jobs = {"pr-ci": {"minutes": 24, "heavy": True, "what": "the pull request CI"},
                   "main-ci": {"minutes": 5, "heavy": False, "what": "CI on a push to production"},
                   "relabel": {"minutes": 1, "heavy": False, "what": "in-dev -> in-main after a merge", "workflow": "Relabel merged issues"},
                   "watch": {"minutes": 1, "heavy": False, "what": "a weekly dependency watch", "workflow": "Release watch"}}
    cfg.check = ["{python}", "-I", "scripts/check_repo.py"]
    cfg.full = ["{python}", "scripts/test_all.py"]
    return cfg

DAY = datetime.date(2026, 12, 10)          # day 10 of 31


def b(used, today=DAY):
    return fci.budget(used, today)


def test_only_this_months_actions_minutes_count_and_storage_never_does():
    """Storage gigabyte-hours once added to the minutes gave 2,372 instead of 2,000; only unitType Minutes counts."""
    items = [{"date": "2026-12-01T00:00:00Z", "product": "actions", "sku": "Actions Linux", "quantity": 300, "unitType": "Minutes"},
             {"date": "2026-12-01T00:00:00Z", "product": "actions", "sku": "Actions storage", "quantity": 372, "unitType": "GigabyteHours"},
             {"date": "2026-12-01T00:00:00Z", "product": "actions", "sku": "Actions Linux", "quantity": 20, "unitType": "Minutes",
              "repositoryName": "another-repo"},
             {"date": "2026-11-01T00:00:00Z", "product": "actions", "sku": "Actions Linux", "quantity": 2000, "unitType": "Minutes"},
             {"date": "2026-12-01T00:00:00Z", "product": "packages", "quantity": 99, "unitType": "Minutes"}]
    assert fci.month_used(items, DAY) == 320                  # every repo of the account: the free minutes are one pool


def test_the_budget_knows_what_is_left_and_how_much_of_the_month_passed():
    x = b(500)
    assert x["left"] == 1500 and x["days_left"] == 21
    assert x["share_passed"] == pytest.approx(10 / 31, abs=0.001) and x["share_used"] == 0.25


def test_the_pull_request_ci_runs_on_github_when_minutes_are_left_and_the_month_is_on_pace():
    where, why = fci.place("pr-ci", b(300))
    assert where == "github" and "1700 minutes left" in why


def test_the_pull_request_ci_goes_to_the_pc_when_the_reserve_would_be_touched():
    where, why = fci.place("pr-ci", b(fci.cfg().ci_free_minutes - fci.cfg().ci_reserve_minutes - 10))
    assert where == "pc" and "reserve" in why


def test_the_pull_request_ci_goes_to_the_pc_when_the_month_spends_ahead_of_time():
    where, why = fci.place("pr-ci", b(1200))                  # 60% used on day 10 of 31
    assert where == "pc" and "ahead" in why


def test_a_small_job_still_runs_on_github_when_a_heavy_one_would_not():
    x = b(1200)
    assert fci.place("relabel", x)[0] == "github" and fci.place("pr-ci", x)[0] == "pc"


def test_everything_goes_to_the_pc_when_github_refuses_or_nothing_is_left():
    for x, refused in ((b(300), True), (b(fci.cfg().ci_free_minutes), False)):
        assert all(fci.place(job, x, refused)[0] == "pc" for job in fci.cfg().ci_jobs)


def test_the_small_automatic_workflows_are_switched_off_when_the_month_is_spent():
    assert all(fci.wanted_workflows(b(300)).values())
    assert not any(fci.wanted_workflows(b(fci.cfg().ci_free_minutes - 5)).values())
    assert not any(fci.wanted_workflows(b(300), refused=True).values())
    assert set(fci.wanted_workflows(b(0))) == {"Relabel merged issues", "Release watch"}


def test_a_refused_run_has_no_step_and_a_failed_run_has_some():
    refused = [{"name": "Repo checks", "conclusion": "failure", "steps": []}]
    failed = [{"name": "Unit tests", "conclusion": "failure", "steps": [{"name": "pytest", "conclusion": "failure"}]}]
    assert fci.refused_run(refused) and fci.refused_run([]) and not fci.refused_run(failed)


def test_the_dispatched_run_is_found_by_its_commit_event_and_time():
    runs = [{"databaseId": 1, "headSha": "abc", "event": "workflow_dispatch", "createdAt": "2026-12-10T08:00:00Z"},
            {"databaseId": 2, "headSha": "abc", "event": "push", "createdAt": "2026-12-10T09:00:00Z"},
            {"databaseId": 3, "headSha": "abc", "event": "workflow_dispatch", "createdAt": "2026-12-10T09:01:00Z"},
            {"databaseId": 4, "headSha": "zzz", "event": "workflow_dispatch", "createdAt": "2026-12-10T09:02:00Z"}]
    assert fci.pick_run(runs, "abc", "2026-12-10T09:00:30Z")["databaseId"] == 3
    assert fci.pick_run(runs, "abc", "2026-12-10T10:00:00Z") is None


def test_every_job_has_a_measured_cost_and_the_heavy_one_is_the_pull_request_ci():
    assert {j for j, s in fci.cfg().ci_jobs.items() if s["heavy"]} == {"pr-ci"}
    assert all(s["minutes"] >= 1 for s in fci.cfg().ci_jobs.values())


class FakeProcess:
    def __init__(self, returncode):
        self.returncode = returncode


def pc_run(monkeypatch, tmp_path, check_code=0, full_code=0):
    """on_pc with git, the queue and check_repo replaced: what ran where, in order."""
    calls = []
    main = tmp_path / "webshop"
    fci.cfg().root = main                  # the worktrees sit beside it

    def git(*args, cwd=None, check=True):
        calls.append(("git",) + args)
        if args[:2] == ("worktree", "add"):
            pathlib.Path(args[3]).mkdir(parents=True)
        return ""

    monkeypatch.setattr(fci.core, "main_checkout", lambda cwd=None: main)
    monkeypatch.setattr(fci.core, "latest_timing_report", lambda cwd=None: None)
    monkeypatch.setattr(fci.core, "git", git)
    monkeypatch.setattr(fci.core, "queued_run", lambda kind, command, cwd: calls.append(("queue", kind, command[-1], cwd)) or full_code)
    monkeypatch.setattr(fci.subprocess, "run", lambda cmd, cwd=None: calls.append(("run", cmd[-1], cwd)) or FakeProcess(check_code))
    return fci.on_pc("ab432e0da646abf025f055f1b8f0e920abd2a22b")[0], calls, fci.cfg().wt_dir / "ci-ab432e0da646"


def test_the_pc_tests_the_pull_requests_head_in_its_own_worktree_through_the_queue(monkeypatch, tmp_path):
    """Run from a checkout on another branch, the PC's CI once tested that branch and posted the result on the pull request's commit."""
    result, calls, folder = pc_run(monkeypatch, tmp_path)
    assert result == "passed"
    assert calls[0] == ("git", "worktree", "add", "--detach", str(folder), "ab432e0da646abf025f055f1b8f0e920abd2a22b")
    assert ("run", "scripts/check_repo.py", folder) in calls
    assert ("queue", "full", "scripts/test_all.py", folder) in calls        # one test job at a time on the PC
    assert calls[-1] == ("git", "worktree", "remove", "--force", str(folder))


def test_a_failed_check_skips_the_full_run_and_the_worktree_is_still_removed(monkeypatch, tmp_path):
    result, calls, folder = pc_run(monkeypatch, tmp_path, check_code=1)
    assert result == "failed" and not [c for c in calls if c[0] == "queue"]
    assert calls[-1] == ("git", "worktree", "remove", "--force", str(folder))
    result, calls, folder = pc_run(monkeypatch, tmp_path / "again", full_code=1)
    assert result == "failed" and calls[-1][:3] == ("git", "worktree", "remove")


class FakeGh:
    """A Run whose gh calls are answered from a table: no network. `workflow` False: GitHub answers 404 for the workflow file."""
    dry = False

    def __init__(self, workflow):
        self.workflow, self.calls = workflow, []

    def ok(self, *cmd):
        self.calls.append(cmd)
        return self.workflow

    def read(self, *cmd, check=True):
        self.calls.append(cmd)
        return "abc123"

    def json(self, *cmd):
        self.calls.append(cmd)
        return [{"number": 7, "headRefOid": "abc123"}] if cmd[:3] == ("gh", "pr", "list") else None

    def write(self, *cmd):
        self.calls.append(cmd)
        return ""


def pr_run(monkeypatch, workflow, **kw):
    run = FakeGh(workflow)
    statuses, logged = [], []
    monkeypatch.setattr(fci, "current_budget", lambda r: {"left": 1500, "share_used": .1, "share_passed": .5})
    monkeypatch.setattr(fci, "refused_this_month", lambda r: pytest.fail("asks GitHub's runs of a workflow that is not there"))
    monkeypatch.setattr(fci, "on_pc", lambda sha: ("passed", 4))
    monkeypatch.setattr(fci, "on_github", lambda *a: pytest.fail("starts a GitHub run"))
    monkeypatch.setattr(fci, "set_status", lambda r, sha, state, text, url=None: statuses.append((state, text)))
    monkeypatch.setattr(fci, "log", logged.append)
    return fci.cmd_pr(run, **kw), statuses, logged


def test_a_missing_workflow_file_sends_the_pull_request_ci_to_the_pc_and_says_why(monkeypatch):
    """`gh run list` answered HTTP 404 and the command stopped; now the PC runs it, the reason is in the status and in the log."""
    code, statuses, logged = pr_run(monkeypatch, workflow=False)
    assert code == 0 and statuses[-1][0] == "success"
    assert "ci.yml is not on" in statuses[-1][1] and "default branch" in statuses[-1][1]
    assert logged[0]["place"] == "pc" and "ci.yml" in logged[0]["why"]


def test_pc_forces_the_pull_request_ci_onto_the_pc_whatever_the_budget_says(monkeypatch):
    code, statuses, logged = pr_run(monkeypatch, workflow=True, force_pc=True)
    assert code == 0 and logged[0]["place"] == "pc" and logged[0]["why"] == "forced with --pc"


class FakeMergeGh:
    """A stand-in for ci.Run: answers the gh reads from `answers` (by the command's words) and records the writes."""
    def __init__(self, answers, dry=False):
        self.answers, self.dry, self.written = answers, dry, []

    def json(self, *cmd):
        for words, answer in self.answers.items():
            if all(w in cmd for w in words):
                return answer
        return None

    def write(self, *cmd):
        self.written.append(cmd)
        return ""


MERGED = {"state": "MERGED", "mergedAt": "2026-11-22T14:26:00Z", "title": "Release (refs #2, #12)", "body": "#15 and the pull request #26",
          "commits": [{"messageHeadline": "Board (refs #27)", "messageBody": "Why: x\nUndo: y"}]}


def test_the_issues_a_pull_request_names_come_from_its_title_body_and_commits():
    assert fci.issues_named(MERGED) == [2, 12, 15, 26, 27]
    assert fci.issues_named({}) == []


def test_after_a_merge_the_issues_in_dev_swap_to_in_main_by_gh_when_no_relabel_command_is_set(jobs, monkeypatch, capsys):
    from saani import core
    cleaned = []
    monkeypatch.setattr(core, "finished_auto_runs", lambda before=None, cwd=None: [(None, "auto/2026-11-01_1000")])
    monkeypatch.setattr(core, "remove_auto_runs", lambda found, dry=False, cwd=None: cleaned.append(found))
    monkeypatch.setattr(fci, "log", lambda entry: None)
    jobs.relabel = None
    gh = FakeMergeGh({("pr", "view"): MERGED, ("workflow", "list"): [],
                 ("issue", "view", "2"): {"labels": [{"name": "in-dev"}], "state": "OPEN"},
                 ("issue", "view", "12"): {"labels": [{"name": "in-dev"}], "state": "OPEN"},
                 ("issue", "view", "15"): {"labels": [{"name": "ui"}], "state": "OPEN"},      # not in dev: left alone
                 ("issue", "view", "26"): {"labels": [], "state": "OPEN"}})
    assert fci.cmd_merged(gh, 26) == 0
    edited = sorted(int(w[3]) for w in gh.written if w[:3] == ("gh", "issue", "edit"))
    assert edited == [2, 12] and all("--add-label" in w and "in-main" in w for w in gh.written)
    assert cleaned == [[(None, "auto/2026-11-01_1000")]]                                        # the finished auto run goes too


RELABEL = "Relabel merged issues"


def relabel_world(jobs, monkeypatch, runs, slept):
    from saani import core
    monkeypatch.setattr(core, "finished_auto_runs", lambda before=None, cwd=None: [])
    monkeypatch.setattr(core, "remove_auto_runs", lambda found, dry=False, cwd=None: None)
    monkeypatch.setattr(fci, "log", lambda entry: None)
    monkeypatch.setattr(fci.time, "sleep", lambda s: slept.append(s))
    jobs.relabel = None
    return FakeMergeGh({("pr", "view"): {**MERGED, "title": "x", "body": "", "commits": []},
                        ("workflow", "list"): [{"name": RELABEL, "state": "active"}], ("run", "list"): runs})


def test_a_relabel_run_from_before_the_merge_does_not_count_and_gh_relabels_itself(jobs, monkeypatch):
    slept = []
    old = [{"databaseId": 1, "conclusion": "success", "createdAt": "2026-11-22T13:00:00Z"}]    # MERGED merged at 14:26
    gh = relabel_world(jobs, monkeypatch, old, slept)
    assert fci.cmd_merged(gh, 26) == 0
    assert len(slept) == 11                                                  # it waited about a minute for a run that never came


def test_a_relabel_run_created_after_the_merge_counts(jobs, monkeypatch, capsys):
    slept = []
    new = [{"databaseId": 2, "conclusion": "success", "createdAt": "2026-11-22T14:27:00Z"}]
    gh = relabel_world(jobs, monkeypatch, new, slept)
    assert fci.cmd_merged(gh, 26) == 0 and slept == []
    assert "ran on GitHub (run 2)" in capsys.readouterr().out


def test_a_pull_request_that_is_not_merged_relabels_nothing(jobs, capsys):
    jobs.relabel = None
    gh = FakeMergeGh({("pr", "view"): {"state": "OPEN"}, ("workflow", "list"): []})
    assert fci.cmd_merged(gh, 30) == 1 and gh.written == []
    assert "not merged" in capsys.readouterr().out
