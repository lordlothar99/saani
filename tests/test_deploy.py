"""saani/deploy.py: the deployer's steps with git and gh faked; the checks and the deploy are real tiny Python commands. No network."""
import argparse
import sys
import types

import pytest

from saani import ci, config, deploy

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
        return ""

    def wrote(self, *start):
        return [c for c in self.calls if c[:len(start)] == start]


@pytest.fixture
def world(monkeypatch, cfg):
    state = types.SimpleNamespace(gh=FakeGh())
    monkeypatch.setattr(ci, "Run", lambda dry: state.gh)
    cfg.deploy_command = [PY, "-c", "print('deployed ok')", "{key}"]
    cfg.deploy_key = "/keys/deploy_ed25519"
    cfg.deploy_timeout_minutes = 1
    cfg.root.mkdir(parents=True)
    return state


def args(dry=False):
    return argparse.Namespace(dry_run=dry)


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


def test_a_deploy_that_succeeds_logs_moves_the_tag_and_closes_the_issues(world, cfg, capsys):
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
    assert not world.gh.wrote("gh", "issue", "edit")                        # no label change: in-main stays until the issue closes
    closes = world.gh.wrote("gh", "issue", "close")
    assert [(c[3], c[c.index("--reason") + 1]) for c in closes] == [("1", "completed"), ("2", "completed")]
    # the tag moves after the deploy, the issues after the tag
    order = [c[:3] for c in world.gh.calls]
    assert order.index(("git", "tag", "-f")) < order.index(("gh", "issue", "comment"))


def test_only_issues_labelled_in_main_are_closed(world):
    world.gh.issues[2]["labels"] = [{"name": "in-dev"}]
    assert deploy.run(args()) == 0
    assert [c[3] for c in world.gh.wrote("gh", "issue", "close")] == ["1"]


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


def test_a_refused_tag_push_is_reported_and_the_issues_are_still_closed(world, capsys):
    world.gh.fail_push = True
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "tag was not moved" in out and "git push origin refs/tags/prod --force" in out
    assert len(world.gh.wrote("gh", "issue", "close")) == 2


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


def test_an_issue_already_commented_for_this_commit_is_only_closed(world):
    world.gh.issues[1]["comments"] = [{"body": "### ✔ Deployed | Commit | " + SHA[:7]}]
    assert deploy.run(args()) == 0
    assert [c[3] for c in world.gh.wrote("gh", "issue", "comment")] == ["2"]
    assert [c[3] for c in world.gh.wrote("gh", "issue", "close")] == ["1", "2"]


def test_a_timeout_keeps_stderr_and_the_output_is_shown_and_logged_as_it_comes(world, cfg, capsys):
    cfg.deploy_command = [PY, "-c", "import sys, time; print('out', flush=True); print('err', file=sys.stderr, flush=True); time.sleep(30)"]
    cfg.deploy_timeout_minutes = 0.02                                                    # 1.2 s: the child has printed by then
    assert deploy.run(args()) == 1
    out = capsys.readouterr().out
    assert "  | out" in out and "  | err" in out
    log = next((cfg.root / "build").glob("saani-deploy-*.log")).read_text(encoding="utf-8")
    assert "out" in log and "err" in log and "# exit: timed out" in log
