"""saani/auto.py: which issues an auto run may take (gh, git and the machine faked), the run's size, the overlap rule, the command line."""
import json
import pathlib
import subprocess

import pytest

from saani import auto, config, core
from saani.core import cmd_auto  # noqa: F401  (the command stays where it was)


def body(fit="✅ Yes", owner="No", blocked="—", extra=""):
    return (f"## At a glance\n| Field | Value |\n|---|---|\n| Auto | {fit} |\n| Needs the owner | {owner} |\n| Blocked by | {blocked} |\n\n"
            f"## Context\n{extra}\n")


def issue(n, milestone=None, type="Task", labels=(), text=None, title=None):
    return {"number": n, "title": title or f"issue {n}", "labels": list(labels), "milestone": milestone,
            "body": body() if text is None else text, "type": type}


def choose(issues, started=(), running=None, today="2026-11-22"):
    eligible, skipped = auto.choose(issues, set(started), running or {}, today)
    return [e["number"] for e in eligible], {s["number"]: s["reason"] for s in skipped}


# ---------------------------------------------------------------- sizing

@pytest.mark.parametrize("configured,cpus,gb,want", [
    (0, 16, 32, 3), (0, 16, 100, 3),            # capped at 3
    (0, 8, 64, 2), (0, 4, 64, 1), (0, 2, 64, 1),        # the cores decide, at least 1
    (0, 32, 9, 2), (0, 32, 3, 1), (0, 32, 0.5, 1),      # the free RAM decides, at least 1
    (0, 16, None, 3), (0, 8, None, 2),                  # RAM unknown: the cores alone
    (0, None, 32, 1),
    (2, 2, 1, 2), (1, 64, 64, 1), (5, 64, 64, 3), (3, 4, 2, 3),   # fixed, capped at 3, whatever the machine says
])
def test_the_size_of_the_run(configured, cpus, gb, want):
    assert auto.parallel_size(configured, cpus, gb) == want


def test_slots_are_the_size_less_the_running_issues_never_below_zero():
    assert auto.free_slots(3, 1) == 2
    assert auto.free_slots(2, 2) == 0
    assert auto.free_slots(1, 4) == 0


def test_free_ram_is_a_number_or_none():
    got = auto.free_ram_gb()
    assert got is None or got > 0


def test_parallel_is_a_setting():
    assert config.Config().parallel == 0
    assert config.from_toml({"auto": {"parallel": 2}}, "x").parallel == 2
    with pytest.raises(SystemExit):
        config.from_toml({"auto": {"parallel": -1}}, "x")
    with pytest.raises(SystemExit):
        config.from_toml({"auto": {"parallel": "many"}}, "x")


# ---------------------------------------------------------------- eligibility and order

def test_a_plain_issue_is_eligible_and_flagged_when_it_has_no_table():
    eligible, skipped = auto.choose([issue(1), issue(2, text="just words")], set(), {}, "2026-11-22")
    assert [e["number"] for e in eligible] == [1, 2] and skipped == []
    assert eligible[0]["flags"] == [] and eligible[1]["flags"] == ["no At a glance"]


def test_partly_is_eligible_no_is_not():
    got, why = choose([issue(1, text=body("🟡 Partly: the data model")), issue(2, text=body("⛔ No: the guard"))])
    assert got == [1] and why == {2: "Auto: no"}


def test_blocked_by_an_open_issue_or_a_future_date_is_left():
    got, why = choose([issue(1), issue(2, text=body(blocked="#1")), issue(3, text=body(blocked="#99")),
                       issue(4, text=body(blocked="2026-12-01")), issue(5, text=body(blocked="2026-11-01"))])
    assert got == [1, 3, 5]                       # #99 is closed: it blocks nothing
    assert why == {2: "blocked by #1", 4: "blocked by a date"}


def test_the_owner_labels_and_the_needs_the_owner_row_leave_an_issue():
    got, why = choose([issue(1, labels=["help wanted"]), issue(2, labels=["owner-action"]), issue(3, labels=["idea"]),
                       issue(4, text=body(owner="Answer: which one?")), issue(5, text=body(owner="Act: pay"))])
    assert got == []
    assert set(why) == {1, 2, 3, 4, 5} and why[4] == "needs the owner: answer" and why[5] == "needs the owner: act"


def test_labels_come_from_the_settings():
    config.use(config.Config(repo="acme/webshop", labels={**config.Config().labels, "idea": "someday"}))
    got, _ = choose([issue(1, labels=["idea"]), issue(2, labels=["someday"])])
    assert got == [1]


def test_issues_on_dev_or_main_are_left():
    got, why = choose([issue(1, labels=["in-dev"]), issue(2, labels=["in-main"]), issue(3)])
    assert got == [3] and why == {1: "already on dev", 2: "already on main"}


def test_an_issue_with_a_worktree_or_a_branch_is_left():
    got, why = choose([issue(1), issue(2), issue(3)], started={1}, running={2: []})
    assert got == [3] and why == {1: "already in a worktree", 2: "already in a worktree"}


def test_an_issue_without_a_known_type_is_listed_as_skipped():
    got, why = choose([issue(1, type=None), issue(2, type="Epic"), issue(3, type="Bug")])
    assert got == [3] and why == {1: "no Type", 2: "no Type"}


def test_the_order_is_milestone_by_version_then_bugs_then_number():
    got, _ = choose([issue(9), issue(8, "v1.10.0"), issue(7, "v1.2.0", "Feature"), issue(6, "v1.2.0", "Bug"),
                     issue(5, "v1.2.0"), issue(10, "v0.9.0"), issue(4, None, "Bug")])
    assert got == [10, 6, 5, 7, 8, 4, 9]          # v0.9.0, v1.2.0 (bug, 5, 7), v1.10.0, then none (bug first)


def test_started_issues_come_from_folders_and_branches_local_or_remote():
    got = auto.started_issues(["12-fix-login", "auto-2026-11-22_0900", "notes"],
                              ["dev", "* task/7-thing", "origin/feat/8-other", "remotes/origin/bug/9-x", "auto/2026-11-22_0900", "wip/10-no"])
    assert got == {12, 7, 8, 9}


# ---------------------------------------------------------------- overlap

def test_paths_are_the_backticked_tokens_with_a_slash_or_an_extension():
    text = "Change `saani/core.py` and `docs/` and `saani.toml`; run `saani auto pick`, not `--json`, see `v1.0.0` and `https://x.y/z.md` and `#12`."
    assert auto.issue_paths(text) == ["saani/core.py", "docs/", "saani.toml"]


def test_paths_overlap_by_file_folder_or_bare_name():
    assert auto.paths_overlap("saani/core.py", "saani/core.py")
    assert auto.paths_overlap("saani/", "saani/core.py")
    assert auto.paths_overlap("core.py", "saani/core.py")
    assert not auto.paths_overlap("saani/core.py", "saani/config.py")
    assert not auto.paths_overlap("docs/", "docs2/a.md")


def test_an_issue_overlapping_a_running_one_is_left_with_the_path():
    running = {5: ["saani/core.py", "tests/test_core.py"]}
    got, why = choose([issue(1, text=body(extra="Edit `saani/core.py`.")), issue(2, text=body(extra="Edit `saani/ci.py`."))], running=running)
    assert got == [2] and why == {1: "overlaps #5 (saani/core.py)"}


def test_an_issue_overlapping_a_picked_one_waits_for_the_next_round():
    got, why = choose([issue(1, text=body(extra="`docs/SAANI.md`")), issue(2, text=body(extra="`docs/SAANI.md` and `saani/ci.py`")),
                       issue(3, text=body(extra="`saani/ci.py`")), issue(4, text="no paths")])
    assert got == [1, 3, 4] and why == {2: "overlaps #1 (docs/SAANI.md)"}      # #3 is free: #2 never was picked


def test_the_files_a_worktree_changed_are_read_from_its_diff(tmp_path, monkeypatch):
    c = config.current()
    wt = c.wt_dir / "5-thing"
    monkeypatch.setattr(core, "worktrees", lambda cwd=None: [(c.root, "dev"), (wt, "task/5-thing"), (c.wt_dir / "auto-x", "auto/x")])
    calls = []

    def fake_git(*args, cwd=None, check=True):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="saani/core.py\ndocs/A.md\n", stderr="")
    monkeypatch.setattr(core, "git", fake_git)
    assert auto.running_issues() == {5: ["saani/core.py", "docs/A.md"]}
    assert calls == [("diff", "--name-only", "origin/dev...task/5-thing")]


# ---------------------------------------------------------------- pick and the command line

def fake_world(monkeypatch, issues, running=None, cpus=16, gb=32):
    monkeypatch.setattr(auto, "read_issues", lambda: issues)
    monkeypatch.setattr(auto, "running_issues", lambda: dict(running or {}))
    monkeypatch.setattr(auto, "free_ram_gb", lambda: gb)
    monkeypatch.setattr(auto.os, "cpu_count", lambda: cpus)
    monkeypatch.setattr(core, "git", lambda *a, cwd=None, check=True: subprocess.CompletedProcess(a, 0, stdout="dev\n", stderr=""))


def test_pick_says_the_slots_the_running_issues_and_the_rest(monkeypatch):
    fake_world(monkeypatch, [issue(1), issue(2, "v1.0.0", "Bug"), issue(3, labels=["in-dev"]), issue(5)], running={5: ["x/y.py"]})
    got = auto.pick("2026-11-22")
    assert got["parallel"] == 3 and got["slots"] == 2 and got["running"] == [5]
    assert [e["number"] for e in got["eligible"]] == [2, 1]
    assert got["skipped"] == [{"number": 3, "reason": "already on dev"}, {"number": 5, "reason": "already in a worktree"}]


def test_a_fixed_parallel_setting_wins_over_the_machine(monkeypatch):
    config.use(config.Config(repo="acme/webshop", parallel=1))
    fake_world(monkeypatch, [issue(1)], running={9: []})
    got = auto.pick("2026-11-22")
    assert got["parallel"] == 1 and got["slots"] == 0


def run_saani(monkeypatch, argv):
    import saani
    import importlib.util
    spec = importlib.util.spec_from_file_location("saani_cli", pathlib.Path(saani.__file__).resolve().parents[1] / "saani.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli.main(argv)


def test_saani_auto_pick_prints_and_saani_auto_still_makes_the_worktree(monkeypatch, capsys):
    fake_world(monkeypatch, [issue(1, title="First"), issue(2, text=body("⛔ No"))])
    assert run_saani(monkeypatch, ["auto", "pick"]) == 0
    out = capsys.readouterr().out
    assert "slots: 3 free of 3" in out and "#1 First [Task; no milestone]" in out and "#2 Auto: no" in out

    assert run_saani(monkeypatch, ["auto", "pick", "--json", "--limit", "0"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"slots", "parallel", "running", "eligible", "skipped"} and data["eligible"] == []

    made = []

    def fake_git(*a, cwd=None, check=True):
        made.append(a)
        if a[:2] == ("worktree", "add"):
            pathlib.Path(a[5]).mkdir(parents=True)          # git makes the folder
        return ""
    monkeypatch.setattr(core, "git", fake_git)
    monkeypatch.setattr(auto, "pick", lambda: pytest.fail("saani auto alone must not pick"))
    assert run_saani(monkeypatch, ["auto", "--id", "2026-11-22_0900"]) is None
    assert any(a[:3] == ("worktree", "add", "--no-track") and "auto/2026-11-22_0900" in a for a in made)


def test_an_unknown_action_is_refused():
    with pytest.raises(SystemExit):
        run_saani(None, ["auto", "stop"])
