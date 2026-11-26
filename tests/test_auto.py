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
    (0, 16, 32, 2), (0, 16, 100, 2),            # capped at 2 per project
    (0, 8, 64, 2), (0, 4, 64, 1), (0, 2, 64, 1),        # the cores decide, at least 1
    (0, 32, 9, 2), (0, 32, 4, 2), (0, 32, 3, 1), (0, 32, 0.5, 1),   # RAM that cannot hold one issue: 1
    (0, 16, None, 2), (0, 8, None, 2),                  # RAM unknown: the cores alone
    (0, None, 32, 1),
    (0, 12, 16, 2),                                     # the 12-core, 16 GB machine
    (0, 12, 4, 2),                                      # other projects hold the rest: still its 2
    (2, 2, 1, 2), (1, 64, 64, 1), (5, 64, 64, 5), (3, 4, 2, 3),   # fixed, whatever the machine says
    (6, 4, 1, 6), (10, 2, 1, 10), (11, 64, 64, 10),             # fixed up to 10 (the config refuses more)
])
def test_the_size_of_the_run(configured, cpus, gb, want):
    assert auto.parallel_size(configured, cpus, gb) == want


def test_the_size_does_not_shrink_for_the_memory_running_issues_already_use():
    # 12 cores: 2 issues running leave 0.5 GB free, and the size is still 2 (not 1)
    assert auto.parallel_size(0, 12, 0.5, running=2) == 2
    assert auto.parallel_size(0, 12, 0.5, running=0) == 1
    assert auto.parallel_size(0, 12, 16, running=0) == 2


def test_a_second_project_gets_its_two_slots_while_another_has_two_in_flight(monkeypatch):
    # this project runs nothing; another project's 2 issues hold the memory, 4 GB free is left
    fake_world(monkeypatch, [issue(1), issue(2)], running={}, cpus=12, gb=4)
    got = auto.pick("2026-11-22")
    assert got["parallel"] == 2 and got["slots"] == 2


def test_slots_are_the_size_less_the_running_issues_never_below_zero():
    assert auto.free_slots(2, 1) == 1
    assert auto.free_slots(2, 2) == 0
    assert auto.free_slots(1, 4) == 0


def test_free_ram_is_a_number_or_none():
    got = auto.free_ram_gb()
    assert got is None or got > 0


def test_parallel_is_a_setting():
    assert config.Config().parallel == 0
    assert config.from_toml({"auto": {"parallel": 2}}, "x").parallel == 2
    for ok in (0, 1, 5, 6, 10):
        assert config.from_toml({"auto": {"parallel": ok}}, "x").parallel == ok
    for bad in (-1, 11, 2.5, "many", True):
        with pytest.raises(SystemExit, match="from 0 to 10"):
            config.from_toml({"auto": {"parallel": bad}}, "x")


@pytest.mark.parametrize("configured,slots,warned", [(0, 2, False), (1, 1, False), (2, 2, False), (5, 5, False), (6, 6, True), (10, 10, True)])
def test_pick_says_the_slots_and_warns_above_five(monkeypatch, capsys, configured, slots, warned):
    config.use(config.Config(repo="acme/webshop", parallel=configured))
    fake_world(monkeypatch, [issue(1)], cpus=16, gb=32)
    got = auto.pick("2026-11-22")
    assert got["parallel"] == slots and got["slots"] == slots
    assert ("warning" in got) == warned
    if warned:
        assert "coder, a reviewer and a test run" in got["warning"]
    args = type("A", (), {"wait": None, "only": None, "limit": None, "json": False})()
    assert auto.cmd_pick(args) == 0
    assert (f"{slots} slots" in capsys.readouterr().out) == warned


def test_the_warning_is_not_printed_by_reading_the_config(capsys):
    config.from_toml({"auto": {"parallel": 10}}, "x")
    assert capsys.readouterr().out == ""


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
    assert got == ({12}, {7, 8, 9})


# ---------------------------------------------------------------- overlap

def test_paths_are_the_backticked_tokens_with_a_slash_or_an_extension():
    text = "Change `saani/core.py` and `docs/` and `saani.toml`; run `saani auto pick`, not `--json`, see `v1.0.0` and `https://x.y/z.md` and `#12`."
    assert auto.issue_paths(text) == ["saani/core.py", "saani.toml"]       # a folder (`docs/`) is ignored


def test_paths_overlap_by_file_or_by_a_named_folder_and_a_bare_name_is_a_root_file():
    assert auto.paths_overlap("saani/core.py", "saani/core.py")
    assert auto.paths_overlap("saani", "saani/core.py")
    assert auto.paths_overlap("saani.toml", "saani.toml")
    assert not auto.paths_overlap("saani.toml", "examples/saani.toml")
    assert not auto.paths_overlap("core.py", "saani/core.py")
    assert not auto.paths_overlap("saani/core.py", "saani/config.py")
    assert not auto.paths_overlap("docs", "docs2/a.md")


def test_an_issue_naming_no_path_never_waits_on_overlap():
    got, why = choose([issue(1, text=body(extra="Fix the thing in the core.")), issue(2, text=body(extra="`saani.toml`"))],
                      running={5: ["saani.toml", "examples/saani.toml"]})
    assert got == [1] and why == {2: "overlaps #5 (saani.toml)"}
    got, _ = choose([issue(3, text=body(extra="`saani.toml`"))], running={5: ["examples/saani.toml"]})
    assert got == [3]


def test_only_limits_the_choice_before_the_overlap_pass():
    issues = [issue(1, text=body(extra="`a.py`")), issue(2, text=body(extra="`a.py`")), issue(3, text=body(extra="`a.py`"))]
    eligible, skipped = auto.choose(issues, set(), {}, "2026-11-22", only={2, 3})
    assert [e["number"] for e in eligible] == [2] and skipped == [{"number": 3, "reason": "overlaps #2 (a.py)"}]


def test_blocked_by_names_only_the_open_blockers_and_a_branch_is_not_a_worktree():
    got, why = choose([issue(1), issue(2, text=body(blocked="#1, #77")), issue(3), issue(4)], started={3})
    assert why[2] == "blocked by #1" and why[3] == "already in a worktree"
    eligible, skipped = auto.choose([issue(4)], set(), {}, "2026-11-22", branched={4})
    assert eligible == [] and skipped == [{"number": 4, "reason": "has a worker branch"}]


def test_named_milestones_sort_alphabetically_between_versions_and_none():
    got, _ = choose([issue(1), issue(2, "Later"), issue(3, "Backlog"), issue(4, "v1.10.0"), issue(5, "v1.9.0")])
    assert got == [5, 4, 3, 2, 1]


def test_bugs_first_uses_the_type_whose_prefix_is_bug():
    config.use(config.Config(repo="acme/webshop", types={"Task": "task", "Defect": "bug"}))
    got, _ = choose([issue(1, type="Task"), issue(2, type="Defect")])
    assert got == [2, 1]


# ---------------------------------------------------------------- gh and git failing

@pytest.mark.parametrize("failure", ["code", "missing", "timeout"])
@pytest.mark.parametrize("which", ["issue", "api"])
def test_a_gh_failure_stops_the_command_and_is_never_an_empty_list(monkeypatch, which, failure):
    def fake_run(cmd, **kw):
        if cmd[1] != which and not (which == "api" and cmd[1] == "api"):
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        if failure == "missing":
            raise FileNotFoundError("gh")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 60)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP 502 bad gateway")
    monkeypatch.setattr(auto.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as e:
        auto.read_issues()
    assert isinstance(e.value.code, str) and "saani auto pick" in e.value.code
    if failure == "code":
        assert "bad gateway" in e.value.code


def test_the_types_come_from_the_api_and_issues_up_to_500_are_read(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        out = '[{"number": 4, "title": "t", "labels": [], "milestone": null, "body": ""}]' if cmd[1] == "issue" else "4\tBug\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    monkeypatch.setattr(auto.subprocess, "run", fake_run)
    assert auto.read_issues()[0]["type"] == "Bug"
    assert "500" in seen[0]


def test_the_fetch_prunes_without_a_prompt_and_a_timeout_only_warns(monkeypatch, capsys):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        raise subprocess.TimeoutExpired(cmd, 60)
    monkeypatch.setattr(auto.subprocess, "run", fake_run)
    auto.fetch()
    assert "--prune" in seen["cmd"] and seen["timeout"] == 60 and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "may be out of date" in capsys.readouterr().err


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

def fake_world(monkeypatch, issues, running=None, cpus=16, gb=32, mine=None, parked=()):
    """`mine`: the running issues this run started (default: all of `running`; the owner's own worktrees are the rest);
    `parked`: the run's issues that are blocked (#110)."""
    monkeypatch.setattr(auto, "run_parked_issues", lambda: set(parked))
    monkeypatch.setattr(auto, "read_issues", lambda: issues)
    monkeypatch.setattr(auto, "fetch", lambda: None)
    monkeypatch.setattr(auto, "running_issues", lambda: dict(running or {}))
    monkeypatch.setattr(auto, "run_started_issues", lambda: set(running or {}) if mine is None else set(mine))
    monkeypatch.setattr(auto, "free_ram_gb", lambda: gb)
    monkeypatch.setattr(auto.os, "cpu_count", lambda: cpus)
    monkeypatch.setattr(core, "git", lambda *a, cwd=None, check=True: subprocess.CompletedProcess(a, 0, stdout="dev\n", stderr=""))


def test_pick_says_the_slots_the_running_issues_and_the_rest(monkeypatch):
    fake_world(monkeypatch, [issue(1), issue(2, "v1.0.0", "Bug"), issue(3, labels=["in-dev"]), issue(5)], running={5: ["x/y.py"]})
    got = auto.pick("2026-11-22")
    assert got["parallel"] == 2 and got["slots"] == 1 and got["running"] == [5]
    assert [e["number"] for e in got["eligible"]] == [2, 1]
    assert got["skipped"] == [{"number": 3, "reason": "already on dev"}, {"number": 5, "reason": "already in a worktree"}]


def test_pick_shows_the_latest_decision_of_each_eligible_issue(monkeypatch, capsys):
    from saani import comments
    older = comments.render("progress", {"Decision": "use A"})
    newer = comments.render("progress", {"Decision": "use B instead"}, "why")
    with_decision = dict(issue(1), decision=comments.decision_of([older, "### ❓ Question\nx", newer]))
    fake_world(monkeypatch, [with_decision, issue(2)])
    got = auto.pick("2026-11-22")
    assert {e["number"]: e["decision"] for e in got["eligible"]} == {1: "use B instead", 2: None}
    args = type("A", (), {"wait": None, "only": None, "limit": None, "json": False})()
    assert auto.cmd_pick(args) == 0
    out = capsys.readouterr().out
    assert "    Decision: use B instead" in out and out.count("Decision:") == 1


def test_read_issues_takes_the_decision_from_the_comments(monkeypatch):
    from saani import comments
    rows = [{"number": 7, "title": "t", "labels": [], "milestone": None, "body": "",
             "comments": [{"body": comments.render("progress", {"Decision": "go left"})}]},
            {"number": 8, "title": "t", "labels": [], "milestone": None, "body": "", "comments": [{"body": "hello"}]}]
    monkeypatch.setattr(auto, "gh", lambda *a: json.dumps(rows))
    monkeypatch.setattr(auto, "read_types_and_statuses", lambda: ({}, {}))
    assert [i["decision"] for i in auto.read_issues()] == ["go left", None]


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
    assert "slots: 2 free of 2" in out and "#1 First [Task; no milestone]" in out and "#2 Auto: no" in out

    assert run_saani(monkeypatch, ["auto", "pick", "--json", "--limit", "0"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"slots", "parallel", "running", "owner_running", "parked", "eligible", "skipped"} and data["eligible"] == []

    made = []

    def fake_git(*a, cwd=None, check=True):
        made.append(a)
        if a[:2] == ("worktree", "add"):
            pathlib.Path(a[5]).mkdir(parents=True)          # git makes the folder
        return ""
    monkeypatch.setattr(core, "git", fake_git)
    monkeypatch.setattr(auto, "pick", lambda **k: pytest.fail("saani auto alone must not pick"))
    assert run_saani(monkeypatch, ["auto", "--id", "2026-11-22_0900"]) is None
    assert any(a[:3] == ("worktree", "add", "--no-track") and "auto/2026-11-22_0900" in a for a in made)


def test_pick_options_without_pick_and_id_with_pick_are_refused(monkeypatch):
    for argv in (["auto", "--json"], ["auto", "--limit", "2"], ["auto", "--only", "3"], ["auto", "pick", "--id", "x"]):
        with pytest.raises(SystemExit) as e:
            run_saani(monkeypatch, argv)
        assert isinstance(e.value.code, str) and "saani auto" in e.value.code


def test_pick_only_reaches_the_choice(monkeypatch, capsys):
    fake_world(monkeypatch, [issue(1), issue(2), issue(3)])
    assert run_saani(monkeypatch, ["auto", "pick", "--json", "--only", "3", "1"]) == 0
    assert [e["number"] for e in json.loads(capsys.readouterr().out)["eligible"]] == [1, 3]


def test_an_unknown_action_is_refused():
    with pytest.raises(SystemExit):
        run_saani(None, ["auto", "stop"])


# ---------------------------------------------------------------- the owner's worktrees take no slot (#56)

def test_the_owners_worktrees_take_no_slot_of_the_run(monkeypatch):
    fake_world(monkeypatch, [issue(1)], running={534: [], 540: [], 556: []}, mine=[])
    got = auto.pick("2026-11-22")
    assert got["slots"] == 2 and got["running"] == [] and got["owner_running"] == [534, 540, 556]
    assert [e["number"] for e in got["eligible"]] == [1]


def test_only_the_run_s_own_issues_count_against_the_slots(monkeypatch):
    fake_world(monkeypatch, [issue(1)], running={5: [], 534: []}, mine=[5])
    got = auto.pick("2026-11-22")
    assert got["slots"] == 1 and got["running"] == [5] and got["owner_running"] == [534]


def test_a_file_an_owner_worktree_changed_still_keeps_an_overlapping_issue_waiting(monkeypatch):
    fake_world(monkeypatch, [issue(1, text=body(extra="touches `saani/core.py`"))], running={534: ["saani/core.py"]}, mine=[])
    got = auto.pick("2026-11-22")
    assert got["eligible"] == [] and got["skipped"][0]["reason"].startswith("overlaps #534")


def test_pick_says_no_slot_and_names_the_owners_worktrees(monkeypatch, capsys):
    monkeypatch.setattr(auto, "journal_pick", lambda note: None)
    fake_world(monkeypatch, [issue(1)], running={5: [], 6: [], 12: [], 15: []}, mine=[5, 6])
    assert run_saani(monkeypatch, ["auto", "pick"]) == 0
    out = capsys.readouterr().out
    assert "slots: 0 free of 2 (running: #5, #6)" in out and "the owner's worktrees #12, #15 are in flight" in out
    assert "no slot: this run's issues #5, #6 are in flight (2 allowed)" in out


# ---------------------------------------------------------------- a parked issue takes no slot (#110)

def test_a_parked_issue_frees_its_slot_and_a_running_one_keeps_it(monkeypatch):
    fake_world(monkeypatch, [issue(1), issue(5), issue(6)], running={5: [], 6: []}, parked=[6])
    got = auto.pick("2026-11-22")
    assert got["running"] == [5] and got["parked"] == [6] and got["slots"] == 1
    assert [e["number"] for e in got["eligible"]] == [1]
    assert {"number": 6, "reason": "parked: blocked in this run"} in got["skipped"]


def test_a_blocked_issue_is_not_offered_again_even_without_its_worktree(monkeypatch):
    fake_world(monkeypatch, [issue(1), issue(7)], mine=[7], parked=[7])
    got = auto.pick("2026-11-22")
    assert [e["number"] for e in got["eligible"]] == [1] and got["slots"] == 2


def test_a_parked_issue_s_files_still_keep_an_overlapping_issue_waiting(monkeypatch):
    fake_world(monkeypatch, [issue(1, text=body(extra="touches `saani/core.py`")), issue(6)], running={6: ["saani/core.py"]}, parked=[6])
    got = auto.pick("2026-11-22")
    assert got["eligible"] == [] and got["slots"] == 2


def test_pick_says_which_issues_are_parked(monkeypatch, capsys):
    monkeypatch.setattr(auto, "journal_pick", lambda note: None)
    fake_world(monkeypatch, [issue(1), issue(6)], running={6: []}, parked=[6])
    assert run_saani(monkeypatch, ["auto", "pick"]) == 0
    assert "parked (blocked, worktree kept): #6" in capsys.readouterr().out


def test_pick_says_nothing_eligible_and_journals_it(monkeypatch, capsys):
    notes = []
    monkeypatch.setattr(auto, "journal_pick", notes.append)
    fake_world(monkeypatch, [issue(1, text=body("⛔ No"))])
    assert run_saani(monkeypatch, ["auto", "pick", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["note"] == "nothing eligible (1 issue skipped)" and notes == [got["note"]]


def test_the_nothing_eligible_note_has_the_right_plural():
    base = {"slots": 1, "parallel": 1, "running": [], "eligible": []}
    assert auto.pick_note({**base, "skipped": [{}, {}]}) == "nothing eligible (2 issues skipped)"
    assert auto.pick_note({**base, "skipped": []}) == "nothing eligible (0 issues skipped)"


def test_a_pick_with_something_to_start_writes_no_note(monkeypatch, capsys):
    monkeypatch.setattr(auto, "journal_pick", lambda note: pytest.fail("no note"))
    fake_world(monkeypatch, [issue(1)])
    assert run_saani(monkeypatch, ["auto", "pick", "--json"]) == 0
    assert "note" not in json.loads(capsys.readouterr().out)
