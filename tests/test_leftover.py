"""#90: a leftover worktree folder (named as Saani names them, git no longer knows it, only git-ignored generated files in it) is removed by
`saani prune` and by `start`, `land` and `status` on their own; never a registered worktree, the run's own folder, a folder with a file
git does not ignore, a folder with another name, or anything when the worktrees folder holds the main checkout."""
import argparse
import os
import shutil

import pytest

from saani import auto, config, core


class Called(Exception):
    pass


@pytest.fixture
def world(repo_copy, git, tmp_path, monkeypatch):
    main, wt = repo_copy, tmp_path / "webshop-wt"
    (main / ".gitignore").write_text("build/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    git("add", ".gitignore", cwd=main)
    git("commit", "-q", "-m", "ignore", cwd=main)
    wt.mkdir()
    git("worktree", "add", "-q", "-b", "feat/12-cart", str(wt / "12-cart"), cwd=main)
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    monkeypatch.chdir(main)
    return main, wt


def leftover(wt, name, files=("build/out.txt", "src/__pycache__/m.pyc")):
    d = wt / name
    for f in files:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text("x", encoding="utf-8")
    d.mkdir(exist_ok=True)
    return d


def old_empty(wt, name):
    d = wt / name
    d.mkdir()
    os.utime(d, (1, 1))
    return d


def test_a_leftover_with_build_and_pycache_is_removed_and_a_dry_run_only_lists_it(world, capsys):
    _, wt = world
    d = leftover(wt, "11-old")
    empty = old_empty(wt, "auto-2026-01-01_0000")
    assert core.remove_leftover_folders(dry=True) == 2 and d.exists() and "would remove the leftover folder" in capsys.readouterr().out
    assert core.remove_leftover_folders() == 2 and not d.exists() and not empty.exists()
    assert (wt / "12-cart").is_dir()                                                  # the registered worktree is never touched


def test_a_folder_with_a_file_git_does_not_ignore_is_listed_and_left_alone(world, capsys):
    _, wt = world
    d = leftover(wt, "13-notes", files=("build/out.txt", "notes.txt"))
    assert core.remove_leftover_folders() == 0 and (d / "notes.txt").exists() and (d / "build" / "out.txt").exists()
    out = capsys.readouterr().out
    assert "left alone" in out and "notes.txt" in out


def test_a_folder_that_looks_like_a_checkout_is_left_alone(world):
    _, wt = world
    d = leftover(wt, "14-checkout", files=(".git", "app.py"))
    assert core.remove_leftover_folders() == 0 and d.exists()


def test_a_folder_named_otherwise_is_never_touched(world):
    _, wt = world
    other = leftover(wt, "webshop-notes")
    ci = leftover(wt, "ci-abcdef123456")
    assert core.leftover_folders() == ([], []) and other.exists() and ci.exists()


def test_a_fresh_empty_folder_may_be_a_start_in_progress_and_stays(world):
    _, wt = world
    fresh = wt / "15-new"
    fresh.mkdir()
    assert core.leftover_folders() == ([], []) and fresh.exists()


def test_a_registered_worktree_is_never_removed_even_when_it_holds_only_ignored_files(world):
    _, wt = world
    reg = wt / "12-cart"
    for p in reg.iterdir():
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    (reg / "build").mkdir()
    assert reg not in core.leftover_folders()[0] and core.remove_leftover_folders() == 0 and reg.exists()


def test_the_folder_the_run_is_in_is_never_a_leftover(world, monkeypatch):
    _, wt = world
    d, other = leftover(wt, "16-here"), leftover(wt, "17-own")
    monkeypatch.chdir(d)
    assert core.leftover_folders()[0] == [other]
    monkeypatch.chdir(wt.parent)
    assert sorted(core.leftover_folders(own=[other])[0]) == [d]


def test_the_auto_runs_folder_from_its_record_is_never_a_leftover(world, monkeypatch):
    _, wt = world
    run, old = leftover(wt, "auto-2026-02-02_0200"), leftover(wt, "18-old")
    monkeypatch.setattr(auto, "read_record", lambda: {"id": "2026-02-02_0200", "started": "x", "folder": str(run)})
    assert core.leftover_folders()[0] == [old]


def test_nothing_is_touched_when_the_worktrees_folder_holds_the_main_checkout(world, monkeypatch):
    main, wt = world
    sibling = leftover(main.parent, "19-sibling")
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main, worktree_folder=str(main.parent)))
    assert core.leftover_folders() == ([], []) and sibling.exists()


def test_a_folder_in_use_is_not_an_error_and_is_tried_again_at_the_next_call(world, monkeypatch, capsys):
    _, wt = world
    d = leftover(wt, "20-busy")
    with monkeypatch.context() as m:
        m.setattr(core, "force_remove_folder", lambda *a, **k: False)
        assert core.remove_leftover_folders() == 0 and d.exists() and "is in use" in capsys.readouterr().out
    assert core.remove_leftover_folders() == 1 and not d.exists()


def test_the_automatic_cleanup_never_fails_the_command_it_runs_in(world, monkeypatch, capsys):
    _, wt = world
    d = leftover(wt, "21-old")
    core.clean_leftovers()
    assert not d.exists() and "removed the leftover folder" in capsys.readouterr().out
    monkeypatch.setattr(core, "remove_leftover_folders", lambda **k: (_ for _ in ()).throw(SystemExit("git broke")))
    core.clean_leftovers()
    assert "not checked: git broke" in capsys.readouterr().out


def stop_at_cleanup(monkeypatch):
    def seen(cwd=None):
        raise Called
    monkeypatch.setattr(core, "clean_leftovers", seen)


def test_status_cleans_up_leftovers(world, monkeypatch):
    stop_at_cleanup(monkeypatch)
    with pytest.raises(Called):
        core.cmd_status(argparse.Namespace(action=None, stats=False))


def test_start_cleans_up_leftovers(origin_clone, monkeypatch):
    _, main = origin_clone
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    monkeypatch.chdir(main)
    monkeypatch.setattr(core, "issue_info", lambda n: {"title": "Cart total", "type": "Task", "labels": [], "milestone": "v1"})
    stop_at_cleanup(monkeypatch)
    args = argparse.Namespace(issue=12, slug=None, hotfix=False, no_assign=True, no_comment=True)
    with pytest.raises(Called):
        core.cmd_start(args)


def test_land_and_hotfix_land_clean_up_leftovers(origin_clone, git, tmp_path, monkeypatch):
    _, main = origin_clone
    wt = tmp_path / "webshop-wt" / "12-cart"
    git("worktree", "add", "-q", "--no-track", "-b", "task/12-cart", str(wt), "origin/dev", cwd=main)
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    monkeypatch.chdir(main)
    stop_at_cleanup(monkeypatch)
    args = argparse.Namespace(worktree=str(wt), dry_run=False, wait=0, keep=False, no_comment=True)
    with pytest.raises(Called):
        core.cmd_land(args)
    with pytest.raises(Called):
        core.land_hotfix(args, wt, "task/12-hotfix-cart", "12")
