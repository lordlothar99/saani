"""saani/core.py: one worktree per task on a branch named from the issue's Type, landing one at a time behind a lock, generated
files rebuilt at landing, and the record of a change in its commit message (trailers Why: and Undo:). The pure functions and the lock
only: no gh call, no push."""
import datetime
import json
import pathlib

import pytest

from saani import core



NOW = datetime.datetime(2026, 11, 21, 14, 0, 0)


def test_the_branch_prefix_comes_from_the_issue_type_and_an_issue_without_one_is_refused():
    assert core.branch_type("Task") == "task"
    assert core.branch_type("Feature") == "feat"
    assert core.branch_type("Bug") == "bug"
    assert core.branch_type(None) is None
    assert core.branch_type("Epic") is None


def test_the_branch_name_is_type_number_and_a_short_slug_of_the_title():
    s = core.slug("Step 2: one worktree per task, landing on dev one at a time; old log retired")
    assert s == "one-worktree-per-task"
    assert core.branch_name("task", 517, s) == "task/517-one-worktree-per-task"
    assert core.cfg().worker_branch.match(core.branch_name("feat", 9, core.slug("Ça marche: élan vital")))
    assert core.slug("!!!") == "work"
    assert len(core.slug("word " * 40, words=40)) <= core.SLUG_MAX


def test_an_existing_branch_for_the_issue_is_reused_so_its_prefix_is_never_renamed():
    branches = ["main", "dev", "origin/dev", "origin/task/517-old-name", "feat/51-other"]
    assert core.existing_branch(517, branches) == "task/517-old-name"
    assert core.existing_branch(51, branches) == "feat/51-other"
    assert core.existing_branch(5, branches) is None


def test_a_commit_on_a_worker_branch_or_dev_needs_why_and_undo():
    ok = "Do the thing (refs #517)\n\nBody.\n\nWhy: conflicts on LOG.md\nUndo: git revert abc\n"
    assert core.missing_trailers(ok) == []
    assert core.missing_trailers("Do it\n\nWhy: because\n") == ["Undo"]
    assert core.missing_trailers("Do it\n\nWhy:\nUndo: revert\n") == ["Why"]      # a trailer needs some text
    assert core.needs_trailers("dev") and core.needs_trailers("bug/12-fix-x")
    assert not core.needs_trailers("main") and not core.needs_trailers("auto/2026-11-21_2300")


def test_conflicts_in_generated_files_are_solved_by_the_landing_and_the_others_by_the_worker():
    generated, others = core.split_conflicts(["docs/SPEC.md", "docs/RUNBOOK.md", "tests/support/browser_groups.json"])
    assert generated == ["docs/SPEC.md", "tests/support/browser_groups.json"]
    assert others == ["docs/RUNBOOK.md"]


def test_when_dev_moves_under_a_landing_its_tests_run_again_only_for_files_in_common():
    """Two landings in a row were once refused because other sessions pushed to dev during tier 1."""
    mine = ["saani/core.py", "docs/SAANI.md", "docs/SPEC.md"]
    assert not core.rerun_landing(["infra/diag_x.sh", "docs/SPEC.md"], mine)      # the generated file does not count
    assert core.rerun_landing(["docs/SAANI.md"], mine)
    assert core.cfg().push_attempts >= 2


def test_one_landing_at_a_time_and_a_stale_lock_is_taken_over(tmp_path):
    lock = tmp_path / core.LOCK_NAME
    assert core.take_lock(lock, "task/1-a", NOW)
    assert not core.take_lock(lock, "feat/2-b", NOW + datetime.timedelta(minutes=30))
    core.release_lock(lock, "feat/2-b")                       # only its holder releases it
    assert lock.exists()
    assert core.take_lock(lock, "feat/2-b", NOW + datetime.timedelta(hours=core.cfg().lock_stale_hours, minutes=1))
    assert core.read_lock(lock)["branch"] == "feat/2-b"
    core.release_lock(lock, "feat/2-b")
    assert not lock.exists()


def test_a_lock_being_written_is_never_taken_over(tmp_path):
    lock = tmp_path / core.LOCK_NAME
    lock.write_text("", encoding="utf-8")
    assert not core.take_lock(lock, "task/1-a", NOW)


def test_the_measure_counts_conflicts_per_landing():
    rows = [{"result": "landed", "conflicts": []}, {"result": "landed", "conflicts": ["docs/SPEC.md", "docs/RUNBOOK.md"]},
            {"result": "stopped", "conflicts": ["docs/RUNBOOK.md"]}]
    assert core.landing_summary(rows) == {"landed": 2, "with_conflicts": 1, "conflicts_per_landing": 1.0, "stopped": 1}
    assert core.landing_summary([])["conflicts_per_landing"] == 0.0


def test_the_hook_is_in_the_repo_and_calls_check_msg():
    hook = (pathlib.Path(__file__).resolve().parents[1] / ".githooks" / "commit-msg").read_text(encoding="utf-8")
    assert "saani.py check-msg" in hook


def test_the_settings_shape_the_branch_names_and_the_trailers(cfg):
    cfg.types = {"Story": "story", "Bug": "fix"}
    cfg.trailers = ("Why",)
    assert core.branch_type("Story") == "story" and core.branch_type("Task") is None
    assert core.cfg().worker_branch.match("fix/3-a") and not core.cfg().worker_branch.match("task/3-a")
    assert core.missing_trailers(chr(10).join(["x", "", "Why: y"])) == []


def test_land_with_worktree_takes_only_a_workers_worktree_of_this_repository(tmp_path):
    """An auto run cannot change folder, so it names the worktree; the main checkout and foreign folders are refused."""
    main, wt, other = tmp_path / "webshop", tmp_path / "webshop-wt" / "41-x", tmp_path / "elsewhere"
    for p in (main, wt, other):
        p.mkdir(parents=True)
    known = [str(main), str(wt)]
    assert core.known_worktree(str(wt), known) == wt
    assert core.known_worktree(str(main), known) is None
    assert core.known_worktree(str(other), known) is None


def test_prune_deletes_merged_branches_but_never_dev_main_an_auto_run_or_a_branch_in_a_worktree():
    merged = ["dev", "main", "master", "* dev", "+ task/523-local-status-page", "feat/411-powers", "origin/feat/471-own-currency",
              "origin/HEAD -> origin/main", "auto/2026-11-22_0044", "longrun/2026-11-21_2300", "bug/12-hotfix-login", "feat/411-powers", ""]
    held = {"task/523-local-status-page", "dev"}
    assert core.prunable(merged, held) == ["bug/12-hotfix-login", "feat/411-powers", "feat/471-own-currency"]
    assert core.prunable([], held) == []


def test_saani_start_warns_about_an_issue_without_a_milestone_unless_it_is_an_idea():
    assert core.milestone_warning({"milestone": "v1.1.0", "labels": []}, 7) is None
    assert core.milestone_warning({"milestone": None, "labels": ["idea", "ui"]}, 7) is None
    text = core.milestone_warning({"milestone": None, "labels": ["ui"]}, 7)
    assert text.startswith("WARNING: issue #7 has no milestone") and "--milestone" in text
    assert core.milestone_warning({}, 8).startswith("WARNING: issue #8")


def test_saani_auto_makes_a_throwaway_worktree_named_with_the_run_id(origin_clone, tmp_path, monkeypatch, capsys):
    """auto-<id> beside the other worktrees, on the branch auto/<id> from the integration branch (prune keeps it); an id twice is refused."""
    from saani import config
    origin, main = origin_clone
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    core.cmd_auto(type("A", (), {"id": "2026-11-22_1500"})())
    wt = tmp_path / "webshop-wt" / "auto-2026-11-22_1500"
    assert wt.is_dir() and (wt / "build").is_dir()
    out = capsys.readouterr().out
    assert "auto/2026-11-22_1500" in out and str(wt) in out
    assert core.git("branch", "--show-current", cwd=wt) == "auto/2026-11-22_1500"
    with pytest.raises(SystemExit, match="already exists"):
        core.cmd_auto(type("A", (), {"id": "2026-11-22_1500"})())
    with pytest.raises(SystemExit, match="letters, digits"):
        core.cmd_auto(type("A", (), {"id": "a b"})())


def test_finished_auto_runs_and_old_longrun_ones_are_removed_but_never_a_dirty_or_a_recent_one(origin_clone, git, tmp_path, monkeypatch):
    """An auto run's folder holds no commits, so a finished run is told by its age: older than `before` (or 12 hours), clean and merged (#28)."""
    import datetime
    from saani import config
    origin, main = origin_clone
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    now = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")                                  # a run of this minute is recent, on any clock
    for stamp in ("2026-11-01_1000", "2026-11-01_1100", now):
        core.cmd_auto(type("A", (), {"id": stamp})())
    wt = tmp_path / "webshop-wt"
    git("worktree", "add", "--no-track", "-b", "longrun/2026-11-01_0900", str(wt / "longrun-2026-11-01_0900"), "origin/dev", cwd=main)   # made before the rename
    (wt / "auto-2026-11-01_1100" / "notes.txt").write_text("not committed", encoding="utf-8")        # a dirty one
    found = core.finished_auto_runs(cwd=main)
    assert [b for _, b in found] == ["auto/2026-11-01_1000", "longrun/2026-11-01_0900"]                                       # not the dirty one, not today's
    assert [b for _, b in core.finished_auto_runs(before=datetime.datetime(2099, 1, 1), cwd=main)] == ["auto/2026-11-01_1000", f"auto/{now}", "longrun/2026-11-01_0900"]
    assert core.remove_auto_runs(found, cwd=main) == 2
    assert not (wt / "auto-2026-11-01_1000").exists()
    assert "auto/2026-11-01_1000" not in core.git("branch", "--format=%(refname:short)", cwd=main).splitlines()
    assert not (wt / "longrun-2026-11-01_0900").exists()
    assert (wt / "auto-2026-11-01_1100").is_dir()


def test_an_auto_run_that_is_off_and_has_no_commit_of_its_own_is_removed_but_not_one_that_is_on_or_has_commits(origin_clone, git, tmp_path, monkeypatch):
    """A run that finished with nothing landed has no pull request, so only `saani prune` can remove it (#75)."""
    from saani import auto, config
    origin, main = origin_clone
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    wt = tmp_path / "webshop-wt"
    for stamp in ("2026-11-01_0100", "2026-11-01_0200", "2026-11-01_0300"):
        core.cmd_auto(type("A", (), {"id": stamp})())
    (wt / "auto-2026-11-01_0200" / "work.txt").write_text("mine", encoding="utf-8")
    git("add", "work.txt", cwd=wt / "auto-2026-11-01_0200")
    git("-c", "user.name=Someone", "-c", "user.email=someone@example.com", "commit", "-q", "-m", "own work", cwd=wt / "auto-2026-11-01_0200")
    auto.record_path().write_text(json.dumps({"id": "2026-11-01_0300", "started": "2026-11-01T03:00:00Z"}), encoding="utf-8")   # this one is on
    found, kept = core.unused_auto_runs(cwd=main)
    assert [b for _, b in found] == ["auto/2026-11-01_0100"]                                # finished, nothing of its own: removed
    assert dict(kept) == {"auto/2026-11-01_0200": "it has commits that are not on dev", "auto/2026-11-01_0300": "the run is still on"}
    assert core.remove_auto_runs(found, cwd=main) == 1
    assert not (wt / "auto-2026-11-01_0100").exists()
    assert (wt / "auto-2026-11-01_0200").is_dir() and (wt / "auto-2026-11-01_0300").is_dir()
    auto.record_path().unlink()                                                             # the run is off: its folder goes too
    assert [b for _, b in core.unused_auto_runs(cwd=main)[0]] == ["auto/2026-11-01_0300"]


def _auto_setup(origin_clone, monkeypatch, *stamps):
    from saani import config
    origin, main = origin_clone
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    for stamp in stamps:
        core.cmd_auto(type("A", (), {"id": stamp})())
    return main, main.parent / "webshop-wt"


def test_an_auto_run_with_an_untracked_file_a_corrupt_record_or_a_young_start_is_kept(origin_clone, monkeypatch):
    import datetime
    from saani import auto
    now = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    main, wt = _auto_setup(origin_clone, monkeypatch, "2026-11-01_0100", "2026-11-01_0200", now)
    (wt / "auto-2026-11-01_0200" / "notes.txt").write_text("untracked", encoding="utf-8")
    found, kept = core.unused_auto_runs(cwd=main)
    assert [b for _, b in found] == ["auto/2026-11-01_0100"]
    assert dict(kept) == {"auto/2026-11-01_0200": "its folder has uncommitted changes (or its state cannot be read)", f"auto/{now}": "started less than 2 h ago"}
    for bad in ("{half a record", "[]", "{}", ""):                                          # a record that cannot be read keeps every run
        auto.record_path().write_text(bad, encoding="utf-8")
        found, kept = core.unused_auto_runs(cwd=main)
        assert found == [] and {why for _, why in kept} == {"run record unreadable"} and len(kept) == 3


def test_an_auto_run_is_on_by_its_folder_when_its_id_differs_and_an_old_longrun_branch_is_pruned(origin_clone, git, monkeypatch):
    from saani import auto
    main, wt = _auto_setup(origin_clone, monkeypatch, "2026-11-01_0100")
    git("worktree", "add", "--no-track", "-b", "longrun/2026-11-01_0900", str(wt / "longrun-2026-11-01_0900"), "origin/dev", cwd=main)
    rec = {"id": "other-name", "started": "2026-11-01T01:00:00Z", "folder": str(wt / "auto-2026-11-01_0100").replace("\\", "/").upper() + "/"}
    auto.record_path().write_text(json.dumps(rec), encoding="utf-8")
    found, kept = core.unused_auto_runs(cwd=main)
    assert [b for _, b in found] == ["longrun/2026-11-01_0900"]
    assert dict(kept) == {"auto/2026-11-01_0100": "the run is still on"}


def test_an_auto_run_is_kept_when_origin_cannot_be_compared_and_a_dry_run_removes_nothing(origin_clone, git, monkeypatch, capsys):
    main, wt = _auto_setup(origin_clone, monkeypatch, "2026-11-01_0100")
    git("update-ref", "-d", "refs/remotes/origin/dev", cwd=main)
    found, kept = core.unused_auto_runs(cwd=main)
    assert found == [] and kept == [("auto/2026-11-01_0100", "cannot compare with origin/dev")]
    git("fetch", "-q", "origin", cwd=main)
    capsys.readouterr()
    assert core.cmd_prune(type("A", (), {"dry_run": True, "local_only": True})()) == 0
    assert "would remove the auto run auto/2026-11-01_0100" in capsys.readouterr().out
    assert (wt / "auto-2026-11-01_0100").is_dir()
    assert core.cmd_prune(type("A", (), {"dry_run": False, "local_only": True})()) == 0
    assert not (wt / "auto-2026-11-01_0100").exists()


def test_an_auto_run_starts_at_its_id_or_at_its_folders_creation(tmp_path):
    import datetime
    assert core.auto_started("auto/2026-11-22_1513") == datetime.datetime(2026, 11, 22, 15, 13)
    assert core.auto_started("longrun/2026-11-22_1513") == datetime.datetime(2026, 11, 22, 15, 13)      # the old name
    assert core.auto_started("auto/nightly") is None
    assert core.auto_started("auto/nightly", tmp_path) is not None


# ---------------------------------------------------------------- the worktrees folder and Claude Code's working directories (#46)

def _settings(path, dirs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps({"permissions": {"allow": ["Bash(saani *)"], "additionalDirectories": dirs}}), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    from saani import config
    main = tmp_path / "webshop"
    main.mkdir()
    (tmp_path / "webshop-wt").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "user-claude"))
    return config.Config(repo="acme/webshop", root=main)


def test_status_warns_when_the_worktrees_folder_is_outside_every_working_directory(project):
    line = core.worktrees_warning(project)
    wt = str((project.root.parent / "webshop-wt").resolve()).replace("\\", "/")
    assert line.startswith(f"warning: worktrees folder {wt} is not in permissions.additionalDirectories")
    assert '"additionalDirectories": ["../webshop-wt"]' in line                       # relative: one committed line for every developer
    assert "the project's .claude/settings.json" in line


def test_a_worktrees_folder_on_another_drive_is_given_as_an_absolute_path(project, monkeypatch):
    def other_drive(path, start):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")
    monkeypatch.setattr(core.os.path, "relpath", other_drive)
    wt = str((project.root.parent / "webshop-wt").resolve()).replace("\\", "/")
    assert f'"additionalDirectories": ["{wt}"]' in core.worktrees_warning(project)


@pytest.mark.parametrize("where", ["project", "local", "user"])
def test_an_entry_in_any_of_the_three_settings_files_covers_it(project, where, tmp_path):
    file = {"project": project.root / ".claude" / "settings.json", "local": project.root / ".claude" / "settings.local.json",
            "user": tmp_path / "user-claude" / "settings.json"}[where]
    _settings(file, [str(tmp_path / "webshop-wt")])
    assert core.worktrees_warning(project) is None


def test_relative_parent_forward_slash_and_case_entries_cover_it(project, tmp_path):
    _settings(project.root / ".claude" / "settings.json", ["../webshop-wt"])            # relative: from the main checkout
    assert core.worktrees_warning(project) is None
    _settings(project.root / ".claude" / "settings.json", [str(tmp_path).replace("\\", "/")])    # a parent folder, written with /
    assert core.worktrees_warning(project) is None
    if core.os.name == "nt":
        _settings(project.root / ".claude" / "settings.json", [str(tmp_path / "WEBSHOP-WT").upper()])
        assert core.worktrees_warning(project) is None


def test_other_folders_broken_files_and_lookalike_names_do_not_cover_it(project, tmp_path):
    _settings(project.root / ".claude" / "settings.json", [str(tmp_path / "webshop-w"), "../docs", 42, ""])
    (project.root / ".claude" / "settings.local.json").write_text("{not json", encoding="utf-8")
    assert core.worktrees_warning(project) is not None


def test_a_worktrees_folder_inside_the_main_checkout_needs_nothing(project):
    project.worktree_folder = "wt"
    assert core.worktrees_warning(project) is None


def test_status_never_writes_the_settings(project):
    core.worktrees_warning(project)
    assert not (project.root / ".claude").exists()


def test_saani_test_kind_is_landing_or_full_tier1_points_to_landing_and_land_is_not_offered(capsys):
    """#48: --kind landing (the default) or full; the old tier1 runs as landing with a message; land is saani land's own run."""
    assert core.test_kind("landing") == "landing" and core.test_kind("full") == "full"
    assert core.test_kind("tier1") == "landing"
    assert "--kind tier1 is now --kind landing" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="use --kind landing"):
        core.test_kind("land")
    with pytest.raises(SystemExit, match="unknown --kind fast"):
        core.test_kind("fast")


# ---------------------------------------------------------------- naming a worktree instead of entering it (#46)

@pytest.fixture
def repo_with_worktree(repo_copy, git, tmp_path, monkeypatch):
    from saani import config
    main, wt = repo_copy, tmp_path / "webshop-wt" / "12-cart"
    git("worktree", "add", "-q", "-b", "feat/12-cart", str(wt), cwd=main)
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    monkeypatch.chdir(main)
    return main, wt


def test_a_worktree_is_named_by_its_path_and_anything_else_is_refused(repo_with_worktree, tmp_path):
    main, wt = repo_with_worktree
    assert core._within(core.named_worktree(str(wt)), wt) and core._within(wt, core.named_worktree(str(wt)))
    assert core._within(core.named_worktree(None), main)                              # no option: the current folder's checkout
    (tmp_path / "elsewhere").mkdir()
    for bad in (str(tmp_path / "elsewhere"), str(tmp_path / "missing")):
        with pytest.raises(SystemExit, match="not a worktree of this repository"):
            core.named_worktree(bad)


def test_saani_test_runs_in_the_named_worktree(repo_with_worktree, monkeypatch):
    main, wt = repo_with_worktree
    seen = []
    monkeypatch.setattr(core, "queued_run", lambda kind, command, cwd, fresh=False: seen.append((kind, command, cwd, fresh)) or 0)
    args = type("A", (), {"kind": "landing", "worktree": str(wt), "command": ["--", "python", "-m", "pytest", "-q"], "fresh": True})()
    assert core.cmd_test(args) == 0 and seen[0][3] is True                            # --fresh is passed on
    assert seen[0][1] == ["python", "-m", "pytest", "-q"] and core._within(seen[0][2], wt) and core._within(wt, seen[0][2])


def test_clean_tree_is_the_trees_id_only_for_a_clean_checkout(repo_with_worktree):
    """#58: a dirty checkout or an untracked file the tests could read never takes a stored result."""
    _, wt = repo_with_worktree
    tree = core.clean_tree(wt)
    assert tree and tree == core.git("rev-parse", "HEAD^{tree}", cwd=wt)
    (wt / "new-file.txt").write_text("x", encoding="utf-8")
    assert core.clean_tree(wt) is None                                           # untracked
    (wt / "new-file.txt").unlink()
    assert core.clean_tree(wt) == tree
    (wt / "a.txt").write_text("one\n", encoding="utf-8")
    core.git("add", "a.txt", cwd=wt)
    core.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "a", cwd=wt)
    tree2 = core.clean_tree(wt)
    assert tree2 and tree2 != tree                                               # another commit, another tree
    (wt / "a.txt").write_text("changed\n", encoding="utf-8")
    assert core.clean_tree(wt) is None                                           # modified


def _land_world(origin_clone, git, tmp_path, monkeypatch, branch, base):
    """A worktree on `branch` made from origin/<base>, and a production commit without trailers (an edit made on main, as in GitHub)."""
    from saani import config
    origin, main = origin_clone
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main))
    ident = ("-c", "user.name=Someone", "-c", "user.email=someone@example.com")
    git("checkout", "-q", "main", cwd=main)
    (main / "TEMPLATE.md").write_text("fix\n", encoding="utf-8")
    git("add", ".", cwd=main)
    git(*ident, "commit", "-q", "-m", "Edit the template in the editor", cwd=main)
    git("push", "-q", "origin", "main", cwd=main)
    git("checkout", "-q", "dev", cwd=main)
    git("fetch", "-q", "origin", cwd=main)
    wt = tmp_path / "webshop-wt" / branch.split("/")[1]
    git("worktree", "add", "-q", "-b", branch, str(wt), f"origin/{base}", cwd=main)
    return main, wt, ident


def _commit(git, wt, ident, name, message):
    (wt / name).write_text(name, encoding="utf-8")
    git("add", name, cwd=wt)
    git(*ident, "commit", "-q", "-m", message, cwd=wt)


def _land(wt, dry_run=True):
    return core.cmd_land(type("A", (), {"worktree": str(wt), "keep": True, "dry_run": dry_run, "wait": 1, "no_comment": True})())


def test_land_ignores_commits_already_on_production_and_still_asks_the_workers_own_for_trailers(origin_clone, git, tmp_path, monkeypatch, capsys):
    main, wt, ident = _land_world(origin_clone, git, tmp_path, monkeypatch, "task/95-back", "dev")
    git(*ident, "merge", "-q", "--no-edit", "origin/main", cwd=wt)                    # production's commit has no trailers
    _commit(git, wt, ident, "a.txt", "Own work (refs #95)\n\nWhy: x\nUndo: y")
    assert _land(wt) == 0                                                            # accepted
    _commit(git, wt, ident, "b.txt", "Own work without trailers (refs #95)")
    with pytest.raises(SystemExit) as e:
        _land(wt)
    assert "Own work without trailers" in str(e.value) and "Edit the template" not in str(e.value)


def test_land_of_a_plain_worker_branch_still_refuses_a_commit_without_trailers(origin_clone, git, tmp_path, monkeypatch):
    main, wt, ident = _land_world(origin_clone, git, tmp_path, monkeypatch, "task/95-plain", "dev")
    _commit(git, wt, ident, "a.txt", "No trailers (refs #95)")
    with pytest.raises(SystemExit, match="commits without the trailers"):
        _land(wt)


def test_land_of_a_hotfix_still_refuses_its_own_commit_without_trailers(origin_clone, git, tmp_path, monkeypatch):
    main, wt, ident = _land_world(origin_clone, git, tmp_path, monkeypatch, "bug/95-hotfix-x", "main")
    _commit(git, wt, ident, "a.txt", "Hotfix without trailers (refs #95)")
    with pytest.raises(SystemExit) as e:
        _land(wt)
    assert "Hotfix without trailers" in str(e.value) and "Edit the template" not in str(e.value)


# ---------------------------------------------------------------- saani start claims the issue by pushing its branch (#100)

def _start_world(origin_clone, tmp_path, monkeypatch, issue=7):
    from saani import auto, config
    origin, main = origin_clone
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=main, worktree_folder=str(tmp_path / "wt"), owner=""))
    monkeypatch.setattr(core, "issue_info", lambda n: {"title": "Add a thing", "type": "Task", "milestone": "v1"})
    monkeypatch.setattr(core, "latest_timing_report", lambda cwd=None: None)
    monkeypatch.setattr(core.comments, "try_post", lambda *a, **k: True)
    monkeypatch.setattr(auto, "journal", lambda *a, **k: None)
    return origin, main, type("A", (), {"issue": issue, "hotfix": False, "slug": None, "no_assign": True, "no_comment": False})()


def _heads(git, origin):
    import subprocess
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=origin, capture_output=True, text=True).stdout.split()


def test_start_pushes_the_new_branch_to_origin_and_says_so(origin_clone, git, tmp_path, monkeypatch, capsys):
    origin, main, ns = _start_world(origin_clone, tmp_path, monkeypatch)
    assert core.cmd_start(ns) == 0
    assert "task/7-add-thing" in _heads(git, origin) and (tmp_path / "wt" / "7-add-thing").is_dir()
    assert "pushed to origin: the issue is claimed" in capsys.readouterr().out


def test_a_second_start_of_the_same_issue_is_refused_naming_the_branch_and_the_worktree(origin_clone, git, tmp_path, monkeypatch):
    origin, main, ns = _start_world(origin_clone, tmp_path, monkeypatch)
    core.cmd_start(ns)
    with pytest.raises(SystemExit) as e:
        core.cmd_start(ns)
    assert "already claimed" in str(e.value) and "task/7-add-thing" in str(e.value) and "7-add-thing" in str(e.value)
    assert len(core.worktrees()) == 2                                  # no second worktree


def test_a_start_from_another_clone_finds_the_branch_on_origin_and_refuses(origin_clone, git, tmp_path, monkeypatch):
    origin, main, ns = _start_world(origin_clone, tmp_path, monkeypatch)
    core.cmd_start(ns)
    other = tmp_path / "other"
    git("clone", "-q", str(origin), str(other), cwd=tmp_path)
    from saani import config
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", root=other, worktree_folder=str(tmp_path / "wt2"), owner=""))
    with pytest.raises(SystemExit, match="another clone holds it"):
        core.cmd_start(ns)
    assert not (tmp_path / "wt2").exists()


def test_a_start_that_cannot_push_stops_with_the_reason_and_makes_no_worktree(origin_clone, git, tmp_path, monkeypatch):
    origin, main, ns = _start_world(origin_clone, tmp_path, monkeypatch)
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho no-pushes-today >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    with pytest.raises(SystemExit, match="cannot claim #7"):
        core.cmd_start(ns)
    assert not (tmp_path / "wt").exists() and "task/7-add-thing" not in _heads(git, main)


def test_a_start_with_origin_unreachable_stops_and_makes_no_worktree(origin_clone, git, tmp_path, monkeypatch):
    origin, main, ns = _start_world(origin_clone, tmp_path, monkeypatch)
    git("remote", "set-url", "origin", str(tmp_path / "gone.git"), cwd=main)
    with pytest.raises(SystemExit):
        core.cmd_start(ns)
    assert not (tmp_path / "wt").exists()


def test_a_start_whose_branch_appears_meanwhile_loses_the_race(origin_clone, git, tmp_path, monkeypatch):
    origin, main, ns = _start_world(origin_clone, tmp_path, monkeypatch)
    monkeypatch.setattr(core, "remote_branches", lambda: [])                       # the other start pushes after this one looked
    git("push", "-q", "origin", "dev:refs/heads/task/7-add-thing", cwd=main)       # same commit: only the empty lease tells it apart
    with pytest.raises(SystemExit, match="cannot claim #7"):
        core.cmd_start(ns)
    assert not (tmp_path / "wt").exists()


def test_land_after_a_start_removes_the_remote_branch_too(origin_clone, git, tmp_path, monkeypatch):
    origin, main, ns = _start_world(origin_clone, tmp_path, monkeypatch)
    core.cmd_start(ns)
    wt = tmp_path / "wt" / "7-add-thing"
    ident = ("-c", "user.name=Someone", "-c", "user.email=someone@example.com")
    (wt / "a.txt").write_text("a", encoding="utf-8")
    git("add", "a.txt", cwd=wt)
    git(*ident, "commit", "-q", "-m", "Add a (refs #7)", "-m", "Why: x.", "-m", "Undo: revert.", cwd=wt)
    monkeypatch.setattr(core, "queued_run", lambda *a, **k: 0)
    monkeypatch.setattr(core, "mark_on_dev", lambda *a, **k: True)
    monkeypatch.setattr(core.status, "advance", lambda *a, **k: None)
    assert core.cmd_land(type("A", (), {"worktree": str(wt), "keep": False, "dry_run": False, "wait": 1, "no_comment": True})()) == 0
    assert "task/7-add-thing" not in _heads(git, origin) and not wt.exists()
