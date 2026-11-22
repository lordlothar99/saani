"""Against a real, temporary git repository (no network, no gh): the frozen-file rule, the worktree list, the commit-msg hook."""
import shutil
import subprocess
import sys

import pytest

from saani import config, core

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def run(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, cfg):
    root = tmp_path / "webshop"
    root.mkdir()
    run("init", "-q", "-b", "dev", cwd=root)
    run("config", "user.email", "someone@example.com", cwd=root)
    run("config", "user.name", "Someone", cwd=root)
    run("config", "commit.gpgsign", "false", cwd=root)
    (root / "SPEC.md").write_text("# Spec\nFROZEN v1\n", encoding="utf-8")
    (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
    run("add", ".", cwd=root)
    run("commit", "-q", "-m", "start", cwd=root)
    cfg.root = root
    return root


def test_a_frozen_file_may_not_change_once_its_mark_is_on_the_integration_branch(repo, cfg):
    cfg.frozen = {"SPEC.md": "FROZEN v1"}
    assert core.frozen_violation({"SPEC.md", "app.py"}, repo, "dev") == "SPEC.md"
    assert core.frozen_violation({"app.py"}, repo, "dev") is None


def test_a_frozen_file_without_its_mark_yet_may_change(repo, cfg):
    cfg.frozen = {"SPEC.md": "FROZEN v2"}
    assert core.frozen_violation({"SPEC.md"}, repo, "dev") is None


def test_the_worktrees_are_listed_main_checkout_first(repo, tmp_path):
    wt = tmp_path / "webshop-wt" / "12-login"
    run("worktree", "add", "-q", "-b", "feat/12-login", str(wt), cwd=repo)
    listed = core.worktrees(repo)
    assert [b for _, b in listed] == ["dev", "feat/12-login"]
    assert listed[0][0].resolve() == repo.resolve()


def test_the_hook_refuses_a_commit_without_why_and_undo_on_a_worker_branch(repo, tmp_path):
    run("checkout", "-q", "-b", "feat/12-login", cwd=repo)
    (repo / "saani.toml").write_text('[project]\nrepo = "acme/webshop"\n', encoding="utf-8")
    saani = core.pathlib.Path(__file__).resolve().parents[1] / "saani.py"
    msg = tmp_path / "msg.txt"

    def check(text):
        msg.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", str(saani), "check-msg", str(msg)], cwd=repo, capture_output=True, text=True)

    refused = check("Login form refs #12\n")
    assert refused.returncode != 0 and "Why:" in refused.stdout + refused.stderr
    assert check("Login form refs #12\n\nWhy: users ask.\nUndo: git revert.\n").returncode == 0
