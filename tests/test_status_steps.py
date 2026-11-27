"""The GitHub status each step sets, command by command, in a dry run too (saani/status.py, #84): start, start --no-comment, land, land
--no-comment, merge, ci merged and deploy, and the no-op messages of advance(). gh faked, the invented project acme/webshop."""
import shutil

import pytest

from saani import auto, ci, comments, core, status
from test_auto_run import land_args, stub_landing, world  # noqa: F401  (world is a fixture)
from test_comments import MergeGh, Result, merged_env  # noqa: F401  (merged_env is a fixture)
from test_deploy import FakeGh as DeployGh
from test_deploy import world as deploy_world  # noqa: F401  (a fixture)
from test_merge import FakeGh as MergeFake
from test_merge import world as merge_world  # noqa: F401  (a fixture)
from test_status import FLAT, FakeGh


def ids(writes):
    """The status id each setIssueStatus write asked for."""
    return [next(a[2:] for a in w if a.startswith("s=")) for w in writes if "setIssueStatus" in " ".join(w)]


@pytest.fixture
def gh(monkeypatch):
    """Every direct gh call of the commands goes to one fake: the repo has the statuses, issue #5 / #12 is in Todo."""
    status._cache.clear()
    status._cache["acme/webshop"] = FLAT
    fake = FakeGh("Todo")
    monkeypatch.setattr(comments, "Direct", lambda dry=False: setattr(fake, "dry", dry) or fake)
    monkeypatch.setattr(status, "io_default", lambda: fake)
    monkeypatch.setattr(auto, "journal", lambda *a, **k: None)
    return fake


@pytest.fixture
def statuses():
    status._cache.clear()
    status._cache["acme/webshop"] = FLAT


# ---------------------------------------------------------------- start

def start(monkeypatch, tmp_path, no_comment):
    monkeypatch.setattr(core, "issue_info", lambda n: {"title": "Add a thing", "type": "Task", "milestone": "v1"})
    monkeypatch.setattr(core, "latest_timing_report", lambda cwd=None: None)
    folder = tmp_path / "wt" / "5-add-thing"

    def git(*args, cwd=None, check=True):
        if args[:2] == ("worktree", "add"):
            folder.mkdir(parents=True, exist_ok=True)
        return "" if check else Result(0)
    monkeypatch.setattr(core, "git", git)
    core.cfg().worktree_folder = str(tmp_path / "wt")
    core.cfg().owner = ""
    ns = type("A", (), {"issue": 5, "hotfix": False, "slug": None, "no_assign": True, "no_comment": no_comment})()
    assert core.cmd_start(ns) == 0
    shutil.rmtree(folder)


@pytest.mark.parametrize("no_comment", [False, True], ids=["with the comment", "--no-comment"])
def test_start_sets_in_progress(gh, monkeypatch, tmp_path, no_comment):
    start(monkeypatch, tmp_path, no_comment)
    assert ids(gh.writes) == ["S3"]                       # once: the comment sets it, or start itself without one


# ---------------------------------------------------------------- land

def test_land_sets_in_review_through_its_comment(gh, world, monkeypatch):  # noqa: F811
    stub_landing(world, monkeypatch)
    assert core.cmd_land(land_args(keep=True)) == 0
    assert ids(gh.writes) == ["S4"]


def test_land_no_comment_still_sets_in_review(gh, world, monkeypatch, capsys):  # noqa: F811
    stub_landing(world, monkeypatch)
    assert core.cmd_land(land_args(keep=True, no_comment=True)) == 0
    assert ids(gh.writes) == ["S4"]
    assert not [w for w in gh.writes if "comment" in w[:3]]          # no comment went out
    assert "next: comment the commit on #12" in capsys.readouterr().out


def test_land_dry_run_prints_the_status_and_sets_nothing(gh, world, monkeypatch, capsys):  # noqa: F811
    stub_landing(world, monkeypatch)
    assert core.cmd_land(land_args(dry_run=True)) == 0
    assert "dry-run: #12 status -> In review" in capsys.readouterr().out
    assert gh.writes == []


# ---------------------------------------------------------------- merge

class MergeDryGh(MergeFake):
    """test_merge's gh, plus the pull request's issues (#12 in dev, #13 not) and the repo's statuses."""

    def __init__(self):
        super().__init__()
        self.statuses = FakeGh("Todo")

    def json(self, *cmd):
        if cmd[:3] == ("gh", "api", "graphql"):
            return self.statuses.json(*cmd)
        if cmd[:3] == ("gh", "pr", "view") and cmd[-1] == "title,body,commits":
            return {"title": "Release", "body": "", "commits": [{"messageHeadline": "x (refs #12)", "messageBody": "refs #13"}]}
        if cmd[:3] == ("gh", "issue", "view"):
            return {"labels": [{"name": "in-dev" if cmd[3] == "12" else "ui"}]}
        return super().json(*cmd)


def test_merge_dry_run_prints_the_status_of_the_issues_it_would_move(merge_world, statuses, capsys):  # noqa: F811
    import test_merge
    merge_world.gh = MergeDryGh()
    assert test_merge.merge.run_command(test_merge.args(dry=True)) == 0
    out = capsys.readouterr().out
    assert "dry-run: #12 status -> In review" in out and "#13:" not in out and "#13 status" not in out    # #13 is named in the summary, but not on dev: no change
    assert merge_world.gh.written == []


# ---------------------------------------------------------------- ci merged

class MergedGh(MergeGh):
    """test_comments' ci.Run for `ci merged` (#2 in dev), plus the statuses: #2 is in Todo."""

    def __init__(self, dry=False):
        super().__init__()
        self.dry = dry
        self.statuses = FakeGh("Todo")

    def json(self, *cmd):
        if cmd[:3] == ("gh", "api", "graphql"):
            return self.statuses.json(*cmd)
        return super().json(*cmd)


def test_ci_merged_sets_in_review_through_the_on_main_comment(merged_env, statuses):  # noqa: F811
    run = MergedGh()
    assert ci.cmd_merged(run, 9) == 0
    assert ids(run.written) == ["S4"]


def test_ci_merged_dry_run_prints_the_status(merged_env, statuses, capsys):  # noqa: F811
    run = MergedGh(dry=True)
    assert ci.cmd_merged(run, 9) == 0
    assert "dry-run: #2 status -> In review" in capsys.readouterr().out
    assert ids(run.written) == []


# ---------------------------------------------------------------- deploy

class DeployStatusGh(DeployGh):
    def __init__(self):
        super().__init__()
        self.statuses = FakeGh("In review")
        self.dry = False
        self.graphql_dry = []                                               # the dry flag of the Run at each status read

    def json(self, *cmd):
        if cmd[:3] == ("gh", "api", "graphql"):
            self.graphql_dry.append(self.dry)
            return self.statuses.json(*cmd)
        return super().json(*cmd)


def test_deploy_dry_run_sets_no_status_and_closes_nothing(deploy_world, statuses, capsys, monkeypatch):  # noqa: F811
    import test_deploy
    deploy_world.gh = DeployStatusGh()
    assert test_deploy.deploy.run(test_deploy.args(dry=True)) == 0
    out = capsys.readouterr().out
    assert "status ->" not in out and "dry-run: GitHub Deployment of" in out
    assert not deploy_world.gh.wrote("gh", "issue", "close") and ids(deploy_world.gh.calls) == []


def test_deploy_leaves_Done_to_the_merge_s_close(deploy_world, statuses):  # noqa: F811
    import test_deploy
    deploy_world.gh = DeployStatusGh()
    assert test_deploy.deploy.run(test_deploy.args()) == 0
    assert ids(deploy_world.gh.calls) == []                                 # no status write: the merge closed the issues, which sets Done
    assert not deploy_world.gh.wrote("gh", "issue", "close")


# ---------------------------------------------------------------- a no-op says why

def say(capsys):
    return capsys.readouterr().out


def test_statuses_off_says_so(cfg, capsys):
    cfg.status_enabled = "off"
    assert status.advance(72, "started", FakeGh("Todo")) is None
    assert "status of #72 unchanged (started): issue statuses are off in saani.toml" in say(capsys)


def test_a_repo_without_statuses_says_so(capsys):
    assert status.advance(72, "started", FakeGh("Todo")) is None                   # conftest: acme/webshop has none
    assert "status of #72 unchanged (started): the repo has no issue statuses" in say(capsys)


def test_unreadable_statuses_say_so_and_never_fail(capsys):
    status._cache.clear()
    assert status.advance(72, "started", FakeGh(fail=True)) is None
    assert "status of #72 unchanged (started): the repo's issue statuses could not be read" in say(capsys)


def test_the_plan_s_reason_is_said(statuses, capsys):
    assert status.advance(72, "started", FakeGh("In review")) is None
    assert "status of #72 unchanged (started): already In review" in say(capsys)


def test_an_unknown_issue_is_said(statuses, capsys):
    class NoIssue(FakeGh):
        def json(self, *cmd):
            out = super().json(*cmd)
            if "planningStatuses" not in next(a for a in cmd if a.startswith("query=")):
                out["data"]["repository"]["issue"] = None
            return out
    assert status.advance(72, "started", NoIssue("Todo")) is None
    assert "status of #72 unchanged (started): the issue was not found" in say(capsys)
