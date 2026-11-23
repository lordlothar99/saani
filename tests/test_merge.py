"""saani/merge.py: the green integration -> production pull request is merged with a merge commit, then relabelled and cleaned up; gh is faked."""
import argparse
import sys
import types

import pytest

from saani import ci, merge

SHA = "a" * 40
PR = {"number": 7, "title": "Release", "state": "OPEN", "baseRefName": "main", "headRefName": "dev", "headRefOid": SHA,
      "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "url": "https://github.com/acme/webshop/pull/7"}
GREEN = {"statuses": [{"context": "saani/ci", "state": "success"}]}
CHECKS = {"check_runs": [{"name": "build", "status": "completed", "conclusion": "success"}]}


class FakeGh:
    def __init__(self, pr=None, status=GREEN, checks=CHECKS, prs=None):
        self.dry = False
        self.pr, self.status, self.checks = {**PR, **(pr or {})}, status, checks
        self.prs = [{"number": 7}] if prs is None else prs
        self.written = []
        self.after = "MERGED"                                            # the state read back after gh pr merge

    def json(self, *cmd):
        if cmd[:3] == ("gh", "pr", "list"):
            return self.prs
        if cmd[:3] == ("gh", "pr", "view"):
            return {"state": self.after, "mergeCommit": {"oid": "m" * 40}} if cmd[-1] == "state,mergeCommit" else self.pr
        if cmd[2].endswith("/status"):
            return self.status
        if "/check-runs" in cmd[2]:
            return self.checks
        raise AssertionError(cmd)

    def write(self, *cmd):
        self.written.append(cmd)
        return ""


@pytest.fixture
def world(monkeypatch):
    """merge.run_command with a fake gh; `ci merged` calls are recorded; no deployer."""
    state = types.SimpleNamespace(gh=FakeGh(), merged=[])

    def make(dry):
        state.gh.dry = dry
        return state.gh

    monkeypatch.setattr(ci, "Run", make)
    monkeypatch.setattr(ci, "cmd_merged", lambda run, n: (state.merged.append((n, list(state.gh.written))), 0)[1])
    monkeypatch.setattr(merge, "deployer", lambda: None)
    return state


def args(pr=None, deploy=False, dry=False):
    return argparse.Namespace(pr=pr, deploy=deploy, dry_run=dry)


def test_the_one_open_integration_to_production_pull_request_is_found(world):
    assert merge.find_pr(world.gh) == 7
    assert merge.find_pr(world.gh, 12) == 12


def test_no_pull_request_is_refused(world):
    world.gh.prs = []
    with pytest.raises(SystemExit, match="no open dev -> main pull request"):
        merge.find_pr(world.gh)


def test_several_pull_requests_are_refused_and_named(world):
    world.gh.prs = [{"number": 7}, {"number": 9}]
    with pytest.raises(SystemExit, match="#7, #9"):
        merge.find_pr(world.gh)


@pytest.mark.parametrize("pr, status, checks, words", [
    ({"state": "MERGED"}, GREEN, CHECKS, "not OPEN"),
    ({"baseRefName": "dev"}, GREEN, CHECKS, "not dev -> main"),
    ({"headRefName": "hotfix/3"}, GREEN, CHECKS, "not dev -> main"),
    ({"mergeable": "CONFLICTING"}, GREEN, CHECKS, "not mergeable"),
    ({"mergeable": "UNKNOWN"}, GREEN, CHECKS, "still working out"),
    ({}, {"statuses": []}, CHECKS, "saani ci pr"),
    ({}, {"statuses": [{"context": "other", "state": "success"}]}, CHECKS, "saani ci pr"),
    ({}, {"statuses": [{"context": "saani/ci", "state": "pending"}]}, CHECKS, "CI still running"),
    ({}, {"statuses": [{"context": "saani/ci", "state": "failure"}]}, CHECKS, "CI is red"),
    ({}, GREEN, {"check_runs": [{"name": "lint", "status": "completed", "conclusion": "failure"}]}, "lint"),
    ({}, GREEN, {"check_runs": [{"name": "lint", "status": "completed", "conclusion": "cancelled"}]}, "lint"),
    ({}, GREEN, {"check_runs": [{"name": "lint", "status": "completed", "conclusion": "timed_out"}]}, "lint"),
    ({}, GREEN, {"check_runs": [{"name": "lint", "status": "in_progress", "conclusion": None}]}, "CI still running"),
])
def test_each_refusal_merges_nothing_and_says_what_to_do(world, capsys, pr, status, checks, words):
    world.gh = FakeGh(pr=pr, status=status, checks=checks)
    assert merge.run_command(args()) == 1
    assert words in capsys.readouterr().out
    assert world.gh.written == [] and world.merged == []


def test_a_named_pull_request_is_still_checked_for_its_branches(world, capsys):
    world.gh = FakeGh(pr={"number": 12, "headRefName": "feat/1-x"})
    assert merge.run_command(args(12)) == 1
    assert "not dev -> main" in capsys.readouterr().out and world.gh.written == []


def test_a_green_pull_request_is_merged_with_a_merge_commit_then_ci_merged_runs(world):
    assert merge.run_command(args()) == 0
    assert world.gh.written == [("gh", "pr", "merge", "7", "--repo", "acme/webshop", "--merge", "--match-head-commit", SHA)]
    assert world.merged == [(7, world.gh.written)]                      # ci merged ran after the merge
    flat = " ".join(world.gh.written[0])
    assert "--squash" not in flat and "--rebase" not in flat and "--delete-branch" not in flat


def test_the_checked_head_is_the_one_merged(world):
    other = "b" * 40
    world.gh = FakeGh(pr={"headRefOid": other})
    assert merge.run_command(args()) == 0
    assert world.gh.written[0][-2:] == ("--match-head-commit", other)


def test_an_odd_check_conclusion_refuses_and_the_passing_ones_do_not(world):
    for conclusion in ("action_required", "stale", "startup_failure"):
        world.gh = FakeGh(checks={"check_runs": [{"name": "x", "status": "completed", "conclusion": conclusion}]})
        assert merge.run_command(args()) == 1
    ok = [{"name": n, "status": "completed", "conclusion": c} for n, c in (("a", "neutral"), ("b", "skipped"), ("c", "success"))]
    world.gh = FakeGh(checks={"check_runs": ok})
    assert merge.run_command(args()) == 0


def test_check_runs_are_read_whole(world, monkeypatch):
    seen = []
    real = world.gh.json
    monkeypatch.setattr(world.gh, "json", lambda *cmd: seen.append(cmd) or real(*cmd))
    merge.run_command(args())
    runs = [c for c in seen if c[2].endswith("check-runs?per_page=100")]
    assert runs and "--paginate" in runs[0]


def test_an_answer_that_is_not_json_refuses_cleanly(world, capsys):
    import json as _json

    def broken(*cmd):
        raise _json.JSONDecodeError("Expecting value", "", 0)
    world.gh.json = broken
    assert merge.run_command(args()) == 1
    assert "not JSON" in capsys.readouterr().out and world.gh.written == [] and world.merged == []


def test_a_pull_request_that_is_not_merged_after_the_command_stops_before_ci_merged(world, capsys):
    world.gh.after = "OPEN"                                              # a merge queue only enqueued it
    assert merge.run_command(args(deploy=True)) == 1
    assert "not MERGED" in capsys.readouterr().out and len(world.gh.written) == 1 and world.merged == []


def test_a_dry_run_checks_everything_and_merges_nothing(world, capsys):
    assert merge.run_command(args(dry=True, deploy=True)) == 0
    out = capsys.readouterr().out
    assert "dry-run: gh pr merge 7 --merge" in out and "saani ci merged 7" in out and "not available yet" in out
    assert world.gh.written == [] and world.merged == []


def test_a_dry_run_of_a_red_pull_request_is_refused_too(world):
    world.gh = FakeGh(status={"statuses": []})
    assert merge.run_command(args(dry=True)) == 1


def test_deploy_without_a_deployer_exits_2_after_the_merge(world, capsys):
    assert merge.run_command(args(deploy=True)) == 2
    assert len(world.gh.written) == 1 and world.merged                  # the merge itself stood
    assert "not available yet" in capsys.readouterr().out


def test_deploy_hands_over_to_the_deployer_when_there_is_one(world, monkeypatch):
    given = []
    monkeypatch.setattr(merge, "deployer", lambda: lambda a: given.append(a) or 0)
    a = args(deploy=True)
    assert merge.run_command(a) == 0 and given == [a] and world.merged


def test_the_deployer_lookup_is_none_while_there_is_no_deploy_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "saani.deploy", None)               # an import of it fails
    assert merge.deployer() is None


# ---------------------------------------------------------------- the GitHub release after the merge (#55)

def test_no_version_file_means_no_release_step(world, monkeypatch):
    monkeypatch.setattr(merge.notes, "release_after_merge", lambda *a: pytest.fail("called"))
    assert merge.run_command(args()) == 0


def test_the_release_step_gets_the_pull_request_and_its_merge_commit(world, monkeypatch, cfg):
    cfg.version_file = "pkg/__init__.py"
    given = []
    monkeypatch.setattr(merge.notes, "release_after_merge", lambda n, sha: given.append((n, sha)) or "v1.0.0")
    assert merge.run_command(args()) == 0
    assert given == [(7, "m" * 40)]


def test_a_failing_release_step_leaves_the_merge_done_and_prints_the_next_command(world, monkeypatch, cfg, capsys):
    cfg.version_file = "pkg/__init__.py"

    def boom(n, sha):
        raise merge.notes.ReleaseFailed("gh release create failed: HTTP 502", "1.2.0")
    monkeypatch.setattr(merge.notes, "release_after_merge", boom)
    assert merge.run_command(args()) == 1
    out = capsys.readouterr().out
    assert world.merged and ("gh", "pr", "merge", "7", "--repo", "acme/webshop", "--merge", "--match-head-commit", SHA) in world.gh.written
    assert f"next: saani release create 1.2.0 --target {'m' * 40} --from-pr 7" in out


def test_a_dry_run_mentions_the_release(world, monkeypatch, cfg, capsys):
    cfg.version_file = "pkg/__init__.py"
    assert merge.run_command(args(dry=True)) == 0
    assert "GitHub release v<version>" in capsys.readouterr().out


def test_any_error_of_the_release_step_keeps_the_merge_and_prints_the_next_command(world, monkeypatch, cfg, capsys):
    cfg.version_file = "pkg/__init__.py"

    def boom(n, sha):
        raise OSError("no space left on device")
    monkeypatch.setattr(merge.notes, "release_after_merge", boom)
    assert merge.run_command(args()) == 1
    out = capsys.readouterr().out
    assert "nothing was merged" not in out and "no space left" in out
    assert f"next: saani release create <version> --target {'m' * 40} --from-pr 7" in out
    assert world.merged


def test_a_failed_release_does_not_stop_the_deploy_and_its_next_line_comes_last(world, monkeypatch, cfg, capsys):
    cfg.version_file = "pkg/__init__.py"

    def boom(n, sha):
        raise merge.notes.ReleaseFailed("HTTP 502", "1.2.0")

    def deploy(a):
        print("deploying...")
        return 0
    monkeypatch.setattr(merge.notes, "release_after_merge", boom)
    monkeypatch.setattr(merge, "deployer", lambda: deploy)
    assert merge.run_command(args(deploy=True)) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert "deploying..." in lines
    assert lines[-1] == f"next: saani release create 1.2.0 --target {'m' * 40} --from-pr 7"
    assert lines.index("deploying...") < len(lines) - 1
