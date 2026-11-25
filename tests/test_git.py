"""Against a real, temporary git repository (no network, no gh): the frozen-file rule, the worktree list, the commit-msg hook."""
import argparse

import pytest

from saani import core


@pytest.fixture
def repo(repo_copy, cfg):
    cfg.root = repo_copy
    return repo_copy


@pytest.mark.parametrize("mark,changed,violation", [
    ("FROZEN v1", {"SPEC.md", "app.py"}, "SPEC.md"),                                     # the mark is on the integration branch: frozen
    ("FROZEN v1", {"app.py"}, None),                                                     # another file may change
    ("FROZEN v2", {"SPEC.md"}, None)])                                                   # without its mark yet, the file may change
def test_a_frozen_file_may_not_change_once_its_mark_is_on_the_integration_branch(repo, cfg, mark, changed, violation):
    cfg.frozen = {"SPEC.md": mark}
    assert core.frozen_violation(changed, repo, "dev") == violation


def test_the_worktrees_are_listed_main_checkout_first(repo, tmp_path, git):
    wt = tmp_path / "webshop-wt" / "12-login"
    git("worktree", "add", "-q", "-b", "feat/12-login", str(wt), cwd=repo)
    listed = core.worktrees(repo)
    assert [b for _, b in listed] == ["dev", "feat/12-login"]
    assert listed[0][0].resolve() == repo.resolve()


def test_the_hook_refuses_a_commit_without_why_and_undo_on_a_worker_branch(repo, tmp_path, monkeypatch, capsys, git):
    git("checkout", "-q", "-b", "feat/12-login", cwd=repo)
    monkeypatch.chdir(repo)                                                              # the hook runs in the repository of the commit
    msg = tmp_path / "msg.txt"

    def check(text):
        msg.write_text(text, encoding="utf-8")
        return core.cmd_check_msg(argparse.Namespace(file=str(msg)))

    assert check("Login form refs #12\n") == 1 and "Why:" in capsys.readouterr().err
    assert check("Login form refs #12\n\nWhy: users ask.\nUndo: git revert.\n") == 0
    git("checkout", "-q", "dev", cwd=repo)
    assert check("A note on the integration branch\n") == 1                              # the integration branch needs them too
