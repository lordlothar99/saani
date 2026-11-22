"""saani/core.py: one worktree per task on a branch named from the issue's Type, landing one at a time behind a lock, generated
files rebuilt at landing, and the record of a change in its commit message (trailers Why: and Undo:). The pure functions and the lock
only: no gh call, no push."""
import datetime
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
    assert not core.needs_trailers("main") and not core.needs_trailers("longrun/2026-11-21_2300")


def test_conflicts_in_generated_files_are_solved_by_the_landing_and_the_others_by_the_worker():
    generated, others = core.split_conflicts(["docs/SPEC.md", "docs/RUNBOOK.md", "tests/support/browser_groups.json"])
    assert generated == ["docs/SPEC.md", "tests/support/browser_groups.json"]
    assert others == ["docs/RUNBOOK.md"]


def test_when_dev_moves_under_a_landing_tier1_runs_again_only_for_files_in_common():
    """Two landings in a row were once refused because other sessions pushed to dev during tier 1."""
    mine = ["saani/core.py", "docs/SAANI.md", "docs/SPEC.md"]
    assert not core.rerun_tier1(["infra/diag_x.sh", "docs/SPEC.md"], mine)      # the generated file does not count
    assert core.rerun_tier1(["docs/SAANI.md"], mine)
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
    """A long run cannot change folder, so it names the worktree; the main checkout and foreign folders are refused."""
    main, wt, other = tmp_path / "webshop", tmp_path / "webshop-wt" / "41-x", tmp_path / "elsewhere"
    for p in (main, wt, other):
        p.mkdir(parents=True)
    known = [str(main), str(wt)]
    assert core.known_worktree(str(wt), known) == wt
    assert core.known_worktree(str(main), known) is None
    assert core.known_worktree(str(other), known) is None


def test_prune_deletes_merged_branches_but_never_dev_main_a_long_run_or_a_branch_in_a_worktree():
    merged = ["dev", "main", "master", "* dev", "+ task/523-local-status-page", "feat/411-powers", "origin/feat/471-own-currency",
              "origin/HEAD -> origin/main", "longrun/2026-11-22_0044", "bug/12-hotfix-login", "feat/411-powers", ""]
    held = {"task/523-local-status-page", "dev"}
    assert core.prunable(merged, held) == ["bug/12-hotfix-login", "feat/411-powers", "feat/471-own-currency"]
    assert core.prunable([], held) == []


def test_saani_new_warns_about_an_issue_without_a_milestone_unless_it_is_an_idea():
    assert core.milestone_warning({"milestone": "v1.1.0", "labels": []}, 7) is None
    assert core.milestone_warning({"milestone": None, "labels": ["idea", "ui"]}, 7) is None
    text = core.milestone_warning({"milestone": None, "labels": ["ui"]}, 7)
    assert text.startswith("WARNING: issue #7 has no milestone") and "--milestone" in text
    assert core.milestone_warning({}, 8).startswith("WARNING: issue #8")
