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

    def json(self, *cmd):
        if cmd[:3] == ("gh", "pr", "list"):
            return self.prs
        if cmd[:3] == ("gh", "pr", "view"):
            return self.pr
        if cmd[-1].endswith("/status"):
            return self.status
        if cmd[-1].endswith("/check-runs"):
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
    assert world.gh.written == [("gh", "pr", "merge", "7", "--repo", "acme/webshop", "--merge")]
    assert world.merged == [(7, world.gh.written)]                      # ci merged ran after the merge
    flat = " ".join(world.gh.written[0])
    assert "--squash" not in flat and "--rebase" not in flat and "--delete-branch" not in flat


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
