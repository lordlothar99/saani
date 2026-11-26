"""saani/deploy.py: the deployer's steps with git and gh faked; the checks and the deploy are real tiny Python commands. No network."""
import argparse
import json
import sys
import types
from pathlib import Path

import pytest

from saani import ci, config, deploy, merge

SHA = "c" * 40
PY = sys.executable
BODY_OK = "## At a glance\n| Field | Value |\n|---|---|\n| Deploy | Server: restart the web app |\n"
BODY_NO = "## At a glance\n| Field | Value |\n|---|---|\n| Deploy | ⛔ Do not deploy before the migration is run |\n"
BODY_NONE = "## At a glance\n| Field | Value |\n|---|---|\n| Deploy | None (docs only) |\n"
GREEN = {"statuses": [{"context": "saani/ci", "state": "success"}]}
CHECKS = {"check_runs": [{"name": "build", "status": "completed", "conclusion": "success"}]}


LOG = "fix a\n\nrefs #1\n\x1e\nfix b\n\nrefs #2\n\x1e\nfix c\n\nRefs #2\n\x1e"


class FakeGh:
    """git and gh as the deployer asks them. `status` and `checks` are the head's; `by_sha` has those of other commits."""

    def __init__(self, status=GREEN, log=LOG, tag=True):
        self.status, self.log, self.tag, self.checks = status, log, tag, CHECKS
        self.by_sha = {}                          # sha -> (status, checks)
        self.parents = []                         # the head's parents: [] is a plain commit
        self.trees = {}                           # sha -> tree (default: its own)
        self.issues = {1: {"body": BODY_OK, "labels": [{"name": "in-main"}], "comments": []},
                       2: {"body": BODY_NONE, "labels": [{"name": "in-main"}], "comments": []}}
        self.open_in_main = []                    # for a first deploy
        self.heads = []                           # successive answers for origin/main after the first (a moving production)
        self.calls = []
        self.fail_push = False
        self.no_deployment_id = False             # GitHub answers the deployment without an id
        self.deployment_body = None               # the JSON the deployment was created with
        self.annotations = {}                     # check run id -> its annotations
        self.jobs = {}                            # Actions job id (= check run id) -> the job

    def read(self, *cmd, check=True):
        self.calls.append(cmd)
        if cmd[:2] == ("git", "rev-parse") and cmd[2] == "origin/main":
            return self.heads.pop(0) if self.heads and len([c for c in self.calls if c[:3] == cmd[:3]]) > 1 else SHA
        if cmd[:2] == ("git", "rev-parse") and cmd[2].endswith("^{tree}"):
            rev = cmd[2][:-7]
            return self.trees.get(rev, "tree-" + rev)
        if cmd[:3] == ("git", "rev-list", "--parents"):
            return " ".join([SHA, *self.parents])
        if cmd[:2] == ("git", "log"):
            assert cmd[-1] == f"prod..{SHA}"
            return self.log
        if cmd[:2] == ("git", "push") and self.fail_push:
            raise SystemExit("push refused")
        return ""

    def ok(self, *cmd):
        self.calls.append(cmd)
        return self.tag

    def json(self, *cmd):
        self.calls.append(cmd)
        if cmd[:2] == ("gh", "api") and cmd[2].endswith("/annotations"):
            return self.annotations.get(int(cmd[2].split("/check-runs/")[1].split("/")[0]), [])
        if cmd[:2] == ("gh", "api") and "/actions/jobs/" in cmd[2]:
            return self.jobs[int(cmd[2].rsplit("/", 1)[1])]
        if cmd[:2] == ("gh", "api"):
            sha = cmd[2].split("/commits/")[1].split("/")[0]
            status, checks = self.by_sha.get(sha, (self.status, self.checks) if sha == SHA else ({"statuses": []}, CHECKS))
            return status if cmd[2].endswith("/status") else checks
        if cmd[:3] == ("gh", "issue", "view"):
            return {"number": int(cmd[3]), **self.issues[int(cmd[3])]}
        if cmd[:3] == ("gh", "issue", "list"):
            return self.open_in_main
        raise AssertionError(cmd)

    def write(self, *cmd):
        self.calls.append(cmd)
        if cmd[:2] == ("gh", "api") and cmd[2].endswith("/deployments"):
            self.deployment_body = json.loads(Path(cmd[cmd.index("--input") + 1]).read_text(encoding="utf-8"))
            return "" if self.no_deployment_id else '{"id": 77}'
        return ""

    def wrote(self, *start):
        return [c for c in self.calls if c[:len(start)] == start]


@pytest.fixture
def world(monkeypatch, cfg):
    state = types.SimpleNamespace(gh=FakeGh())
    monkeypatch.delenv(deploy.AUTO_VAR, raising=False)   # the tests run inside an auto run too, which sets it
    monkeypatch.setattr(ci, "Run", lambda dry: state.gh)
    cfg.deploy_command = [PY, "-c", "print('deployed ok')", "{key}"]
    cfg.deploy_key = "/keys/deploy_ed25519"
    cfg.deploy_timeout_minutes = 1
    cfg.root.mkdir(parents=True)
    return state


def args(dry=False, skip_ci=None):
    return argparse.Namespace(dry_run=dry, skip_ci=skip_ci)


def test_an_empty_command_is_refused(world, cfg, capsys):
    cfg.deploy_command = []
    assert deploy.run(args()) == 1
    assert "no [deploy] command in saani.toml" in capsys.readouterr().out
    assert world.gh.calls == []


@pytest.mark.parametrize("status, words", [
    ({"statuses": []}, "no saani/ci status"),
    ({"statuses": [{"context": "other", "state": "success"}]}, "no saani/ci status"),
    ({"statuses": [{"context": "saani/ci", "state": "pending"}]}, "is pending"),
    ({"statuses": [{"context": "saani/ci", "state": "failure"}]}, "is failure"),
])
def test_a_head_without_green_ci_is_refused(world, capsys, status, words):
    world.gh.status = status
    assert deploy.run(args()) == 1
    assert words in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")


def test_the_key_is_required_when_a_command_uses_it(world, cfg, capsys):
    cfg.deploy_key = ""
    assert deploy.run(args()) == 1
    assert "[deploy] key is empty" in capsys.readouterr().out


@pytest.mark.parametrize("row", ["⛔ wait", "⛔ Do not deploy: migration", "Do not deploy: migration", "do not deploy yet", "Don't deploy", "DON’T DEPLOY"])
def test_an_issue_that_says_not_to_deploy_is_listed_and_refused(world, capsys, row):
    world.gh.issues[2]["body"] = f"## At a glance\n| Field | Value |\n|---|---|\n| Deploy | {row} |\n"
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "say not to deploy" in out and "#2: Deploy: " + row in out and "#1:" not in out
    assert not world.gh.wrote("git", "tag") and not world.gh.wrote("gh", "issue", "close")


def row_body(row):
    return "## At a glance\n| Field | Value |\n|---|---|\n| Deploy | " + row + " |\n"


def test_none_and_a_normal_row_are_not_a_no():
    assert deploy.says_do_not_deploy(BODY_NONE) is None
    for row in ("No", "None", "—", "no change", "Do nothing", "Nothing to do"):
        assert deploy.says_do_not_deploy(row_body(row)) is None, row
    assert deploy.says_do_not_deploy(BODY_OK) is None
    assert deploy.says_do_not_deploy("no table here") is None
    assert deploy.says_do_not_deploy(BODY_NO).startswith("⛔")


def test_issues_in_the_range_are_read_from_refs_once_each():
    assert deploy.issues_in("a\nrefs #3\n\x1e b refs #12 and Refs #3\n") == [3, 12]
    assert deploy.issues_in("no issue here, see #9") == []


def test_every_issue_of_a_refs_list_is_read(world, capsys):
    world.gh.log = "big change\n\nrefs #1, #2 and #3\n\x1e"
    world.gh.issues[3] = {"body": BODY_NO, "labels": [], "comments": []}
    assert deploy.issues_in(world.gh.log) == [1, 2, 3]
    assert deploy.run(args()) == 1
    assert "#3: Deploy: ⛔" in capsys.readouterr().out
    assert [c[3] for c in world.gh.wrote("gh", "issue", "view")] == ["1", "2", "3"]


def test_a_failing_check_stops_before_the_deploy(world, cfg, capsys):
    cfg.deploy_checks = [[PY, "-c", "print('disk full'); raise SystemExit(3)"]]
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "check 1 failed" in out and "exit 3" in out and "disk full" in out
    assert "deploying" not in out and not world.gh.wrote("git", "tag")


def test_a_failing_dry_run_stops_before_the_deploy(world, cfg, capsys):
    cfg.deploy_dry_run = [PY, "-c", "raise SystemExit(1)"]
    assert deploy.run(args()) == 1
    assert "the dry run failed" in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")


def test_dry_run_runs_steps_one_to_three_and_hides_the_key(world, cfg, capsys, tmp_path):
    marker = tmp_path / "ran"
    cfg.deploy_checks = [[PY, "-c", "print('checked')"]]
    cfg.deploy_dry_run = [PY, "-c", "print('would restart')"]
    cfg.deploy_command = [PY, "-c", f"open({str(marker)!r}, 'w')", "{key}"]
    assert deploy.run(args(dry=True)) == 0
    out = capsys.readouterr().out
    assert "3. check 1 ok" in out and "3. dry run ok" in out and "would restart" in out
    assert "<key>" in out and "deploy_ed25519" not in out
    assert not marker.exists()
    assert not world.gh.wrote("git", "tag") and not world.gh.wrote("git", "push") and not world.gh.wrote("gh", "issue", "close")


def test_dry_run_prints_the_comments_the_label_changes_and_the_deployment_and_writes_nothing(world, capsys):
    world.gh.issues[2]["labels"] = []
    assert deploy.run(args(dry=True)) == 0
    out = capsys.readouterr().out
    assert "dry-run: #1: comment ### ✔ Deployed (ccccccc); remove in-main; the issue is not closed here" in out
    assert "dry-run: #2: comment ### ✔ Deployed (ccccccc); the issue is not closed here" in out
    assert "dry-run: GitHub Deployment of ccccccc to production, then its success status" in out
    assert not world.gh.wrote("gh", "issue", "comment") and not world.gh.wrote("gh", "issue", "edit") and not world.gh.wrote("gh", "api", "repos/acme/webshop/deployments")


def test_a_deploy_that_succeeds_logs_moves_the_tag_and_comments_the_issues(world, cfg, capsys):
    assert deploy.run(args()) == 0
    out = capsys.readouterr().out
    assert "deploy ok" in out and "deploy_ed25519" not in out
    logs = list((cfg.root / "build").glob("saani-deploy-*.log"))
    assert len(logs) == 1 and "deployed ok" in logs[0].read_text(encoding="utf-8")
    assert ("git", "tag", "-f", "prod", SHA) in world.gh.calls
    assert ("git", "push", "origin", "refs/tags/prod", "--force") in world.gh.calls
    comments = world.gh.wrote("gh", "issue", "comment")
    assert [c[3] for c in comments] == ["1", "2"]
    body = comments[0][comments[0].index("--body") + 1]
    assert body.startswith("### ✔ Deployed") and SHA[:7] in body and logs[0].name in body
    assert not world.gh.wrote("gh", "issue", "close")                       # the merge closed them: the deploy closes nothing
    edits = world.gh.wrote("gh", "issue", "edit")
    assert [(c[3], c[c.index("--remove-label") + 1]) for c in edits] == [("1", "in-main"), ("2", "in-main")]
    # the tag moves after the deploy, the issues after the tag, the Deployment last
    order = [c[:3] for c in world.gh.calls]
    assert order.index(("git", "tag", "-f")) < order.index(("gh", "issue", "comment"))
    assert [c[2] for c in world.gh.wrote("gh", "api")][-2:] == ["repos/acme/webshop/deployments", "repos/acme/webshop/deployments/77/statuses"]


def test_a_deploy_records_a_github_deployment_with_a_success_status(world, cfg, capsys):
    assert deploy.run(args()) == 0
    assert "GitHub Deployment recorded: ccccccc to production" in capsys.readouterr().out
    body = world.gh.deployment_body
    assert body["ref"] == SHA and body["environment"] == "production" and body["required_contexts"] == [] and body["auto_merge"] is False
    status = world.gh.wrote("gh", "api", "repos/acme/webshop/deployments/77/statuses")[0]
    assert "state=success" in status and "environment=production" in status and "POST" in status
    cfg.deploy_environment = "staging"
    assert deploy.run(args()) == 0
    assert world.gh.deployment_body["environment"] == "staging" and world.gh.deployment_body["production_environment"] is False


def test_a_deployment_github_refuses_is_reported_and_the_deploy_stays_done(world, capsys):
    world.gh.no_deployment_id = True
    assert deploy.run(args()) == 1
    assert "GitHub Deployment was not recorded" in capsys.readouterr().out
    assert world.gh.wrote("git", "tag") and len(world.gh.wrote("gh", "issue", "comment")) == 2


def test_every_issue_of_the_range_is_commented_open_or_closed_and_both_labels_come_off(world, capsys):
    world.gh.issues[1]["labels"] = [{"name": "in-dev"}, {"name": "in-main"}]
    world.gh.issues[1]["state"] = "CLOSED"
    world.gh.issues[2]["labels"] = []                                       # no label: still commented, nothing removed
    assert deploy.run(args()) == 0
    assert [c[3] for c in world.gh.wrote("gh", "issue", "comment")] == ["1", "2"]
    edits = world.gh.wrote("gh", "issue", "edit")
    assert len(edits) == 1 and edits[0][3] == "1" and edits[0].count("--remove-label") == 2 and "in-main" in edits[0] and "in-dev" in edits[0]
    assert not world.gh.wrote("gh", "issue", "close")


def test_no_prod_tag_moves_the_tag_and_closes_nothing(world, capsys):
    world.gh.tag = False
    assert deploy.run(args()) == 0
    assert "a first deploy" in capsys.readouterr().out
    assert ("git", "tag", "-f", "prod", SHA) in world.gh.calls
    assert not world.gh.wrote("gh", "issue", "close")


def test_a_failed_exit_keeps_the_log_and_moves_nothing(world, cfg, capsys):
    cfg.deploy_command = [PY, "-c", "print('boom'); raise SystemExit(2)"]
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "the deploy failed (exit 2)" in out and "boom" in out
    assert len(list((cfg.root / "build").glob("saani-deploy-*.log"))) == 1
    assert not world.gh.wrote("git", "tag") and not world.gh.wrote("gh", "issue", "close")


def test_log_ok_must_match_even_when_the_exit_is_zero(world, cfg, capsys):
    cfg.deploy_command = [PY, "-c", "print('restarting...')"]
    cfg.deploy_log_ok = r"deploy finished: \d+ steps"
    assert deploy.run(args()) == 1
    assert "does not match log_ok" in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")
    cfg.deploy_command = [PY, "-c", "print('deploy finished: 4 steps')"]
    assert deploy.run(args()) == 0
    assert world.gh.wrote("git", "tag")


def test_succeeded_needs_exit_zero_and_the_match():
    assert deploy.succeeded(0, "x", "")
    assert deploy.succeeded(0, "all ok", "ok$")
    assert not deploy.succeeded(0, "nope", "ok$")
    assert not deploy.succeeded(1, "ok", "ok")
    assert not deploy.succeeded(None, "ok", "")


def test_a_deploy_that_times_out_is_stopped_and_moves_nothing(world, cfg, capsys):
    cfg.deploy_command = [PY, "-c", "import time; print('start', flush=True); time.sleep(30)"]
    cfg.deploy_timeout_minutes = 0.01            # 0.6 seconds
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "timed out" in out and "half-done" in out
    assert len(list((cfg.root / "build").glob("saani-deploy-*.log"))) == 1
    assert not world.gh.wrote("git", "tag") and not world.gh.wrote("gh", "issue", "close")


def test_a_refused_tag_push_is_reported_and_the_issues_are_still_commented(world, capsys):
    world.gh.fail_push = True
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "tag was not moved" in out and "git push origin refs/tags/prod --force" in out
    assert len(world.gh.wrote("gh", "issue", "comment")) == 2 and world.gh.deployment_body["ref"] == SHA


def test_the_expanded_command_has_python_and_the_key(cfg):
    cfg.deploy_key = "/keys/k"
    assert deploy.expand(["ssh", "-i", "{key}", "{python}"]) == ["ssh", "-i", "/keys/k", PY]
    assert deploy.expand(["ssh", "-i", "{key}"], show_key=True) == ["ssh", "-i", "<key>"]


def test_the_deploy_keys_are_read_from_saani_toml():
    cfg = config.from_toml({"project": {"repo": "acme/webshop"}, "deploy": {
        "command": ["ssh", "{key}"], "dry_run": ["ssh", "{key}", "--dry-run"], "checks": [["df"]], "log_ok": "ok", "key": "/k",
        "timeout_minutes": 5, "paths": ["app/"]}}, ".")
    assert (cfg.deploy_command, cfg.deploy_dry_run, cfg.deploy_checks, cfg.deploy_log_ok, cfg.deploy_key, cfg.deploy_timeout_minutes,
            cfg.deploy_paths) == (["ssh", "{key}"], ["ssh", "{key}", "--dry-run"], [["df"]], "ok", "/k", 5, ("app/",))
    assert config.Config().deploy_timeout_minutes == 30


def merge_commit_head(world, parent_status=GREEN, parent_checks=CHECKS, same_tree=True):
    """production's head is a merge commit without a status; its second parent (the tested integration head) has them."""
    world.gh.status = {"statuses": []}
    world.gh.parents = ["1" * 40, "2" * 40]
    world.gh.trees = {SHA: "T", "1" * 40: "T-old", "2" * 40: "T" if same_tree else "T-other"}
    world.gh.by_sha["2" * 40] = (parent_status, parent_checks)


def test_a_merge_commit_of_a_green_parent_with_the_same_tree_is_deployable(world, capsys):
    merge_commit_head(world)
    assert deploy.run(args()) == 0
    assert "merge commit with the tree of 2222222" in capsys.readouterr().out
    assert world.gh.wrote("git", "tag")


@pytest.mark.parametrize("kw, words", [
    ({"same_tree": False}, "not a merge of a green commit with the same content"),
    ({"parent_status": {"statuses": []}}, "not a merge of a green commit"),
    ({"parent_status": {"statuses": [{"context": "saani/ci", "state": "failure"}]}}, "not a merge of a green commit"),
    ({"parent_checks": {"check_runs": [{"name": "lint", "status": "completed", "conclusion": "failure"}]}}, "not a merge of a green commit"),
    ({"parent_checks": {"check_runs": [{"name": "lint", "status": "in_progress"}]}}, "not a merge of a green commit"),
])
def test_a_merge_commit_is_refused_unless_its_parent_is_green_and_has_the_same_content(world, capsys, kw, words):
    merge_commit_head(world, **kw)
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert words in out and "saani merge" in out
    assert not world.gh.wrote("git", "tag")


def test_a_plain_commit_without_a_status_is_refused(world, capsys):
    world.gh.status = {"statuses": []}
    world.gh.parents = ["1" * 40]
    assert deploy.run(args()) == 1
    assert "no saani/ci status" in capsys.readouterr().out


def test_a_green_status_with_a_failed_or_pending_check_run_is_refused(world, capsys):
    world.gh.checks = {"check_runs": [{"name": "lint", "status": "completed", "conclusion": "failure"}]}
    assert deploy.run(args()) == 1
    assert "a check run did not pass" in capsys.readouterr().out
    world.gh.checks = {"check_runs": [{"name": "lint", "status": "queued"}]}
    assert deploy.run(args()) == 1
    assert "has not finished" in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")


def test_production_moving_after_the_checks_stops_the_deploy(world, cfg, capsys, tmp_path):
    marker = tmp_path / "ran"
    cfg.deploy_command = [PY, "-c", f"open({str(marker)!r}, 'w')"]
    world.gh.heads = ["d" * 40]
    assert deploy.run(args()) == 1
    assert "moved from ccccccc to ddddddd" in capsys.readouterr().out
    assert not marker.exists() and not world.gh.wrote("git", "tag")


def test_the_sha_is_given_to_the_commands(world, cfg, capsys):
    cfg.deploy_command = [PY, "-c", "import sys; print('deploying', sys.argv[1])", "{sha}"]
    cfg.deploy_dry_run = [PY, "-c", "import sys; print('dry', sys.argv[1])", "{sha}"]
    assert deploy.run(args()) == 0
    out = capsys.readouterr().out
    assert f"dry {SHA}" in out and f"deploying {SHA}" in out
    assert deploy.expand(["x", "{sha}"], sha=SHA) == ["x", SHA]


def test_an_invalid_log_ok_is_refused_before_anything_runs(world, cfg, capsys):
    cfg.deploy_log_ok = "(unclosed"
    assert deploy.run(args()) == 1
    assert "log_ok is not a valid regex" in capsys.readouterr().out
    assert world.gh.calls == []


def test_an_answer_that_is_not_json_stops_cleanly(world, capsys):
    def broken(*cmd):
        raise __import__("json").JSONDecodeError("bad", "", 0)
    world.gh.json = broken
    assert deploy.run(args()) == 1
    assert "not JSON" in capsys.readouterr().out


def test_a_first_deploy_reads_the_open_in_main_issues_for_the_marker(world, capsys):
    world.gh.tag = False
    world.gh.open_in_main = [{"number": 8, "body": BODY_OK}, {"number": 9, "body": BODY_NO}]
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "#9: Deploy: ⛔" in out and "#8:" not in out
    assert not world.gh.wrote("git", "tag")


def test_an_issue_already_commented_for_this_commit_is_not_commented_again(world):
    world.gh.issues[1]["comments"] = [{"body": "### ✔ Deployed | Commit | " + SHA[:7]}]
    assert deploy.run(args()) == 0
    assert [c[3] for c in world.gh.wrote("gh", "issue", "comment")] == ["2"]
    assert [c[3] for c in world.gh.wrote("gh", "issue", "edit")] == ["1", "2"]      # the labels come off all the same
    assert not world.gh.wrote("gh", "issue", "close")


def test_a_timeout_keeps_stderr_and_the_output_is_shown_and_logged_as_it_comes(world, cfg, capsys):
    cfg.deploy_command = [PY, "-c", "import sys, time; print('out', flush=True); print('err', file=sys.stderr, flush=True); time.sleep(30)"]
    cfg.deploy_timeout_minutes = 0.05                                                    # 3 s: room for a slow start-up on a loaded machine
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "  | out" in out and "  | err" in out
    log = next((cfg.root / "build").glob("saani-deploy-*.log")).read_text(encoding="utf-8")
    assert "out" in log and "err" in log and "# exit: timed out" in log


# ---------------------------------------------------------------- check runs GitHub never started, and --skip-ci

BILLING = ("The job was not started because recent account payments have failed or your spending limit needs to be increased. "
           "Please check the 'Billing & plans' section of your settings")
BUILD_ID, SCAN_ID = 101, 102


def refused_runs(world):
    """Two Actions check runs that GitHub refused to start (billing), each with its one annotation."""
    runs = [{"id": i, "name": n, "status": "completed", "conclusion": "failure", "app": {"slug": "github-actions"},
             "output": {"annotations_count": 1}} for i, n in ((BUILD_ID, "Images build"), (SCAN_ID, "Repo checks"))]
    world.gh.annotations = {BUILD_ID: [{"message": BILLING}], SCAN_ID: [{"message": BILLING}]}
    return {"check_runs": runs}


def test_a_merge_commit_whose_only_runs_were_refused_falls_back_to_its_green_parent(world, capsys):
    merge_commit_head(world)
    world.gh.checks = refused_runs(world)
    assert deploy.run(args()) == 0
    out = capsys.readouterr().out
    assert "not run on ccccccc (Images build, Repo checks)" in out
    assert "merge commit with the tree of 2222222" in out and world.gh.wrote("git", "tag")


def test_refused_runs_do_not_pass_a_head_that_has_no_green_parent(world, capsys):
    merge_commit_head(world, same_tree=False)
    world.gh.checks = refused_runs(world)
    assert deploy.run(args()) == 1
    assert "not a merge of a green commit with the same content" in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")


def test_a_refused_run_beside_a_real_failure_still_refuses(world, capsys):
    world.gh.checks = refused_runs(world)
    world.gh.checks["check_runs"].append({"id": 103, "name": "lint", "status": "completed", "conclusion": "failure"})
    assert deploy.run(args()) == 1
    assert "a check run did not pass on ccccccc (lint)" in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")


@pytest.mark.parametrize("conclusion, annotations, job, refused", [
    ("failure", [{"message": BILLING}], None, False),                                       # the billing words
    ("failure", [{"message": "Process completed with exit code 1."}], None, True),         # a real failure's annotation
    ("failure", [], {"steps": []}, True),                                                   # a failure without the words is a failure
    ("startup_failure", [], {"steps": []}, False),                                          # never started
    ("cancelled", [], {"steps": []}, False),
    ("cancelled", [], {"steps": [{"name": "Set up job", "conclusion": "success"}]}, True),  # cancelled while running
    ("startup_failure", [], {}, True),                                                      # the steps unknown
    ("timed_out", [{"message": BILLING}], None, True),                                      # not a conclusion of a refused job
])
def test_only_a_run_github_never_started_is_not_run(world, conclusion, annotations, job, refused):
    run = {"id": 7, "name": "build", "status": "completed", "conclusion": conclusion, "app": {"slug": "github-actions"},
           "output": {"annotations_count": len(annotations)}}
    world.gh.checks = {"check_runs": [run]}
    world.gh.annotations = {7: annotations}
    if job is not None:
        world.gh.jobs = {7: job}
    assert (deploy.run(args()) == 1) is refused
    assert bool(world.gh.wrote("git", "tag")) is not refused


def test_a_failed_read_of_the_annotations_leaves_the_run_failed(world, capsys):
    world.gh.checks = refused_runs(world)
    real = world.gh.json

    def json(*cmd):
        if cmd[2].endswith("/annotations"):
            raise SystemExit("failed: gh api ... 502")
        return real(*cmd)
    world.gh.json = json
    assert deploy.run(args()) == 1
    assert "a check run did not pass" in capsys.readouterr().out


def test_never_started_is_a_pure_rule():
    run = {"status": "completed", "conclusion": "failure"}
    assert merge.never_started(run, [{"message": BILLING}])
    assert not merge.never_started(run, [{"message": "error"}])
    assert not merge.never_started({**run, "conclusion": "success"}, [{"message": BILLING}])
    assert not merge.never_started({"status": "in_progress"}, [{"message": BILLING}])
    assert merge.never_started({**run, "conclusion": "startup_failure"}, steps=[])
    assert not merge.never_started({**run, "conclusion": "startup_failure"}, steps=None)
    assert merge.check_runs_problem({"check_runs": [{"id": 1, **run}]}, "abc", {1}) is None
    assert "did not pass" in merge.check_runs_problem({"check_runs": [{"id": 1, **run}]}, "abc", set())


def red_head(world):
    world.gh.status = {"statuses": [{"context": "saani/ci", "state": "failure"}]}


def test_skip_ci_skips_only_the_gate_and_writes_the_reason_into_the_log(world, cfg, capsys):
    red_head(world)
    cfg.deploy_checks = [[PY, "-c", "print('checked')"]]
    cfg.deploy_dry_run = [PY, "-c", "print('dry ok')"]
    assert deploy.run(args(skip_ci="GitHub   refuses jobs: spending limit")) == 0
    out = capsys.readouterr().out
    assert "1. CI gate SKIPPED (--skip-ci): GitHub refuses jobs: spending limit" in out
    assert "the gate said: saani/ci is failure on ccccccc" in out
    assert "3. check 1 ok" in out and "3. dry run ok" in out and "deploy ok" in out
    assert "deployed ccccccc without the CI gate" in out
    log = next((cfg.root / "build").glob("saani-deploy-*.log")).read_text(encoding="utf-8")
    assert "# CI gate skipped (--skip-ci, the owner's): GitHub refuses jobs: spending limit" in log
    assert "# the gate said: saani/ci is failure on ccccccc" in log
    assert ("git", "tag", "-f", "prod", SHA) in world.gh.calls
    assert [c[3] for c in world.gh.wrote("gh", "issue", "comment")] == ["1", "2"] and world.gh.deployment_body["ref"] == SHA


def test_skip_ci_still_refuses_an_issue_that_says_not_to_deploy(world, capsys):
    red_head(world)
    world.gh.issues[2]["body"] = BODY_NO
    assert deploy.run(args(skip_ci="no minutes")) == 1
    assert "say not to deploy" in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")


def test_skip_ci_still_stops_on_a_failed_check_or_dry_run(world, cfg, capsys):
    red_head(world)
    cfg.deploy_checks = [[PY, "-c", "raise SystemExit(4)"]]
    assert deploy.run(args(skip_ci="no minutes")) == 1
    assert "check 1 failed" in capsys.readouterr().out
    cfg.deploy_checks = []
    cfg.deploy_dry_run = [PY, "-c", "raise SystemExit(1)"]
    assert deploy.run(args(skip_ci="no minutes")) == 1
    assert "the dry run failed" in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")


def test_skip_ci_still_stops_when_production_moves(world, capsys):
    red_head(world)
    world.gh.heads = ["d" * 40]
    assert deploy.run(args(skip_ci="no minutes")) == 1
    assert "moved from ccccccc to ddddddd" in capsys.readouterr().out
    assert not world.gh.wrote("git", "tag")


def test_skip_ci_with_a_dry_run_says_so_and_deploys_nothing(world, cfg, capsys, tmp_path):
    red_head(world)
    marker = tmp_path / "ran"
    cfg.deploy_command = [PY, "-c", f"open({str(marker)!r}, 'w')"]
    assert deploy.run(args(dry=True, skip_ci="no minutes")) == 0
    assert "(CI gate skipped: --skip-ci)" in capsys.readouterr().out
    assert not marker.exists() and not world.gh.wrote("git", "tag")


def test_skip_ci_on_a_green_head_skips_nothing(world, cfg, capsys):
    assert deploy.run(args(skip_ci="just in case")) == 0
    out = capsys.readouterr().out
    assert "--skip-ci not needed" in out and "SKIPPED" not in out and "without the CI gate" not in out
    assert "skipped" not in next((cfg.root / "build").glob("saani-deploy-*.log")).read_text(encoding="utf-8")


@pytest.mark.parametrize("why", ["", "   "])
def test_skip_ci_needs_a_reason(world, capsys, why):
    assert deploy.run(args(skip_ci=why)) == 1
    assert "--skip-ci needs the reason" in capsys.readouterr().out
    assert world.gh.calls == []


def test_skip_ci_is_refused_in_an_auto_run(world, monkeypatch, capsys):
    from saani import auto
    assert deploy.AUTO_VAR == auto.WATCH_VAR
    monkeypatch.setenv(deploy.AUTO_VAR, "token")
    red_head(world)
    assert deploy.run(args(skip_ci="no minutes")) == 1
    assert "--skip-ci in an auto run" in capsys.readouterr().out
    assert world.gh.calls == []


def test_without_skip_ci_the_red_gate_still_refuses_and_writes_no_log(world, cfg, capsys):
    red_head(world)
    assert deploy.run(args()) == 1
    assert "is failure" in capsys.readouterr().out
    assert not list((cfg.root / "build").glob("saani-deploy-*.log"))


def test_the_deploy_command_line_takes_skip_ci():
    parser = argparse.ArgumentParser()
    deploy.add_parsers(parser.add_subparsers())
    assert parser.parse_args(["deploy", "--skip-ci", "no minutes"]).skip_ci == "no minutes"
    assert parser.parse_args(["deploy"]).skip_ci is None
