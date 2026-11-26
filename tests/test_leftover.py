"""#90: a leftover worktree folder (git no longer knows it, only git-ignored generated files in it) is removed by `saani prune` and by
`start`, `land` and `status` on their own; never a registered worktree, the run's own folder or a folder with a file git does not ignore."""
import shutil

import pytest

from saani import config, core


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


def test_a_leftover_with_build_and_pycache_is_removed_and_a_dry_run_only_lists_it(world, capsys):
    _, wt = world
    d = leftover(wt, "old-1")
    empty = wt / "old-2"
    empty.mkdir()
    assert core.remove_leftover_folders(dry=True) == 2 and d.exists() and "would remove the leftover folder" in capsys.readouterr().out
    assert core.remove_leftover_folders() == 2 and not d.exists() and not empty.exists()
    assert (wt / "12-cart").is_dir()                                                  # the registered worktree is never touched


def test_a_folder_with_a_file_git_does_not_ignore_is_listed_and_left_alone(world, capsys):
    _, wt = world
    d = leftover(wt, "old-3", files=("build/out.txt", "notes.txt"))
    assert core.remove_leftover_folders() == 0 and (d / "notes.txt").exists() and (d / "build" / "out.txt").exists()
    out = capsys.readouterr().out
    assert "left alone" in out and "notes.txt" in out


def test_a_folder_with_a_tracked_looking_checkout_is_left_alone(world):
    _, wt = world
    d = leftover(wt, "old-4", files=(".git", "app.py"))
    assert core.remove_leftover_folders() == 0 and d.exists()


def test_a_registered_worktree_is_never_removed_even_when_it_holds_only_ignored_files(world, git):
    main, wt = world
    reg = wt / "12-cart"
    for p in reg.iterdir():
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    (reg / "build").mkdir()
    assert core.remove_leftover_folders() == 0 and reg.exists()


def test_the_folder_the_run_is_in_is_never_removed(world, monkeypatch):
    _, wt = world
    d = leftover(wt, "old-5")
    monkeypatch.chdir(d)
    assert core.remove_leftover_folders() == 0 and d.exists()
    other = leftover(wt, "old-6")
    monkeypatch.chdir(wt.parent)
    assert core.remove_leftover_folders(own=[other]) == 1 and other.exists() and not d.exists()


def test_a_folder_in_use_is_not_an_error_and_is_tried_again_at_the_next_call(world, monkeypatch, capsys):
    _, wt = world
    d = leftover(wt, "old-7")
    monkeypatch.setattr(core, "force_remove_folder", lambda *a, **k: False)
    assert core.remove_leftover_folders() == 0 and d.exists() and "is in use" in capsys.readouterr().out
    monkeypatch.undo()
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=world[0]))
    assert core.remove_leftover_folders() == 1 and not d.exists()


def test_status_start_and_land_clean_up_by_themselves_and_never_fail_on_it(world, monkeypatch, capsys):
    _, wt = world
    d = leftover(wt, "old-8")
    core.clean_leftovers()
    assert not d.exists() and "removed the leftover folder" in capsys.readouterr().out
    monkeypatch.setattr(core, "remove_leftover_folders", lambda **k: (_ for _ in ()).throw(SystemExit("git broke")))
    core.clean_leftovers()
    assert "not checked: git broke" in capsys.readouterr().out
