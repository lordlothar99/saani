"""saani auto on|off|status|report: the run's record, the stop rules, the report and the journal. git and gh are faked; the guard's registry
(~/.saani/auto) lives under a temporary HOME; the project is the invented acme/webshop."""
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from saani import auto, comments, config, core, guard

START = datetime.datetime(2026, 11, 22, 8, 0, tzinfo=datetime.timezone.utc)


def worktree(root, folder):
    """A folder that looks like a git worktree of `root` (a .git file, the common dir), without running git."""
    folder.mkdir(parents=True)
    admin = root / ".git" / "worktrees" / folder.name
    admin.mkdir(parents=True)
    (admin / "commondir").write_text("../..", encoding="utf-8")
    (folder / ".git").write_text(f"gitdir: {admin}", encoding="utf-8")
    return folder


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A main checkout with a .git folder, a worktrees folder with the run's folder, an empty guard registry."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    root = tmp_path / "webshop"
    (root / ".git").mkdir(parents=True)
    (root / "saani.toml").write_text('[project]\nrepo = "acme/webshop"\n', encoding="utf-8")
    wt = tmp_path / "webshop-wt"
    run = worktree(root, wt / "auto-2026-11-22_0800")
    config.use(config.Config(repo="acme/webshop", owner="someone", root=root, landing=["{python}", "-c", "pass"]))
    monkeypatch.chdir(run)
    return type("World", (), {"root": root, "wt": wt, "run": run, "home": home, "git": root / ".git"})


def args(**kw):
    base = {"id": None, "run_id": None, "folder": None, "deploy": False, "here": False, "check": False, "pr": None, "post": False}
    return type("A", (), {**base, **kw})()


def log_refusals(reasons, run="r1"):
    for reason in reasons:
        guard.log_refusal({"id": run}, "Bash", reason, "git")


def record(world, started=START, **kw):
    rec = {"id": "r1", "started": auto.stamp(started), "folder": str(world.run), "deploy": False, "limits": dict(auto.LIMITS), **kw}
    (world.git / auto.RECORD).write_text(json.dumps(rec), encoding="utf-8")
    return rec


# ---------------------------------------------------------------- on and off

def test_on_writes_the_record_in_the_shared_git_folder(world, capsys):
    assert auto.cmd_on(args()) == 0
    rec = json.loads((world.git / auto.RECORD).read_text(encoding="utf-8"))
    assert rec["id"] == "2026-11-22_0800" and rec["deploy"] is False and rec["limits"] == auto.LIMITS
    assert rec["folder"] == guard.norm(world.run) and rec["started"].endswith("Z")
    out = capsys.readouterr().out
    assert out == f"saani auto: on 2026-11-22_0800 folder={guard.norm(world.run)}\n"      # one line, the guard's cue


def test_on_with_id_folder_and_deploy(world, capsys, tmp_path):
    other = worktree(world.root, world.wt / "auto-other")
    os.chdir(tmp_path)
    assert auto.cmd_on(args(id="mine", folder=str(other), deploy=True)) == 0
    rec = auto.read_record()
    assert rec["id"] == "mine" and rec["deploy"] is True and rec["folder"] == guard.norm(other)
    assert capsys.readouterr().out == f"saani auto: on mine folder={guard.norm(other)}\n"


@pytest.mark.parametrize("bad", [{"id": "a b"}, {"id": "x" * 65}, {"folder": "elsewhere"}])
def test_on_refuses_a_bad_id_or_a_folder_outside_the_worktrees(world, bad):
    with pytest.raises(SystemExit):
        auto.cmd_on(args(**bad))
    assert auto.read_record() is None


def test_on_outside_the_worktrees_folder_is_refused(world, tmp_path):
    os.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        auto.cmd_on(args())
    assert "inside" in str(e.value)


@pytest.mark.parametrize("folder", ["\\\\host\\x", "//host/x", "/dev/clipboard"])
def test_on_with_a_network_or_device_folder_stops_with_a_message(world, folder):
    with pytest.raises(SystemExit) as e:
        auto.cmd_on(args(folder=folder))
    assert str(e.value).startswith(f"saani auto on: --folder {folder}: a ")


def test_a_second_run_is_refused_until_the_first_is_off_or_stale(world):
    auto.cmd_on(args())
    with pytest.raises(SystemExit) as e:
        auto.cmd_on(args(id="again"))
    assert "off" in str(e.value)
    record(world, started=auto.utc_now() - datetime.timedelta(hours=guard.MAX_HOURS + 1))      # a crashed run's leftover
    assert auto.cmd_on(args(id="again")) == 0 and auto.read_record()["id"] == "again"


def test_off_removes_the_record_and_prints_the_summary(world, capsys):
    record(world)
    log_refusals(["not on the list", "not on the list", "push"])
    guard.turn_on("sess-1", "r1", world.root, world.wt, folder=world.run)
    guard.turn_on("sess-2", "r1", world.root, world.wt, folder=world.run)          # a subagent session of the same run
    guard.turn_on("sess-3", "other", world.root, world.wt, folder=world.run)
    assert auto.cmd_off(args(run_id=None)) == 0
    out = capsys.readouterr().out
    assert "auto run r1" in out and "refusals: 3" in out and "not on the list 2" in out and "is off (2 guard entries removed" in out
    assert guard.state("sess-1") is None and guard.state("sess-2") is None and guard.state("sess-3")["id"] == "other"
    assert auto.read_record() is None and not (world.git / auto.RECORD).exists()
    assert auto.cmd_off(args(run_id=None)) == 0 and "no auto run is on" in capsys.readouterr().out


def test_off_run_ends_a_run_without_a_record(world, capsys):
    guard.turn_on("sess-1", "gone", world.root, world.wt, folder=world.run)
    assert auto.cmd_off(args(run_id="gone")) == 0
    assert "(1 guard entry removed; no record of this run here)" in capsys.readouterr().out
    assert guard.state("sess-1") is None
    with pytest.raises(SystemExit):
        auto.cmd_off(args(run_id="a b"))


def test_off_run_removes_an_unreadable_over_mark_and_says_so(world, capsys):
    os.makedirs(guard.registry(), exist_ok=True)
    pathlib.Path(guard.registry(), "sess-9.over").write_text("{broken", encoding="utf-8")
    assert guard.state("sess-9") == {"over": True}                                       # still refused until the owner clears it
    assert guard.turn_off_run("gone") == 0                                               # no run id to match it by
    assert auto.cmd_off(args(run_id="gone")) == 0
    assert "removed 1 unreadable over mark of the guard (no run id to match): sess-9" in capsys.readouterr().out
    assert guard.state("sess-9") is None and not os.listdir(guard.registry())


def test_off_run_removes_an_over_mark_too(world, capsys):
    old = (datetime.datetime.now() - datetime.timedelta(hours=guard.MAX_HOURS + 1)).isoformat()
    guard.turn_on("sess-1", "gone", world.root, world.wt, folder=world.run, started=old)
    assert guard.state("sess-1")["over"] is True                                         # the session is over: every call refused
    assert auto.cmd_off(args(run_id="gone")) == 0
    assert "(1 guard entry removed" in capsys.readouterr().out
    assert guard.state("sess-1") is None and not os.listdir(guard.registry())


def test_off_run_of_another_run_keeps_the_record(world):
    record(world)
    assert auto.cmd_off(args(run_id="elsewhere")) == 0
    assert auto.read_record()["id"] == "r1"


# ---------------------------------------------------------------- status and the stop rules

def test_status_without_a_run(world, capsys):
    assert auto.cmd_status(args(check=True)) == 0
    assert "no auto run is on" in capsys.readouterr().out


def test_status_says_elapsed_refusals_and_how_close_each_limit_is(world, capsys):
    record(world, started=auto.utc_now() - datetime.timedelta(hours=2, minutes=5))
    log_refusals(["a", "b", "b", "b", "c"])
    log_refusals(["other-run"], run="r2")                     # another run's refusals are not this run's
    assert auto.cmd_status(args()) == 0
    out = capsys.readouterr().out
    assert "elapsed 2 h 05 min" in out and "refusals: 5: b 3, a 1, c 1" in out
    assert "refusals in total: 5 of 40" in out and "same reason in a row: 3 of 10" in out and "hours: 2.1 of 10" in out
    assert "REACHED" not in out


def test_check_exits_0_below_every_limit(world):
    record(world, started=auto.utc_now())
    log_refusals(["a", "b"] * 10)                             # 20 in all, never twice in a row
    assert auto.cmd_status(args(check=True)) == 0


def test_check_exits_3_at_40_refusals_and_says_which(world, capsys):
    record(world, started=auto.utc_now())
    log_refusals([f"reason {i % 7}" for i in range(39)])
    assert auto.cmd_status(args(check=True)) == 0
    log_refusals(["one more"])
    assert auto.cmd_status(args(check=True)) == auto.EXIT_LIMIT == 3
    assert "STOP: refusals in total reached (40 of 40)" in capsys.readouterr().out


def test_check_exits_3_when_one_reason_repeats_10_times_in_a_row(world, capsys):
    record(world, started=auto.utc_now())
    log_refusals(["push"] * 9 + ["merge"] + ["push"] * 9)       # 19 in all, the longest run is 9
    assert auto.cmd_status(args(check=True)) == 0
    log_refusals(["push"])
    assert auto.cmd_status(args(check=True)) == 3
    assert "same reason in a row reached (10 of 10)" in capsys.readouterr().out


def test_check_exits_3_after_10_hours(world, capsys):
    record(world, started=auto.utc_now() - datetime.timedelta(hours=9, minutes=59))
    assert auto.cmd_status(args(check=True)) == 0
    record(world, started=auto.utc_now() - datetime.timedelta(hours=10, minutes=1))
    assert auto.cmd_status(args(check=True)) == 3
    assert "hours reached" in capsys.readouterr().out


def test_the_limits_in_the_record_are_the_ones_that_count(world):
    record(world, started=auto.utc_now(), limits={"refusals": 2, "same_reason": 10, "hours": 10})
    log_refusals(["a", "b"])
    assert [k for k, _, _ in auto.reached(auto.read_record())] == ["refusals"]


def test_longest_run():
    assert auto.longest_run([]) == 0 and auto.longest_run(["a"]) == 1
    assert auto.longest_run(["a", "a", "b", "b", "b", "a"]) == 3


def test_the_limits_are_in_one_place():
    assert auto.LIMITS == {"refusals": 40, "same_reason": 10, "hours": 10}
    assert guard.MAX_HOURS >= auto.LIMITS["hours"]


# ---------------------------------------------------------------- the report

def fake_world(monkeypatch, commits="", titles=None, listing=None, posted=None):
    def git(*a, cwd=None, check=True):
        assert a[0] == "log"
        return subprocess.CompletedProcess(a, 0, stdout=commits, stderr="")
    monkeypatch.setattr(core, "git", git)

    def gh(*a):
        if a[:2] == ("issue", "view"):
            return (titles or {}).get(int(a[2]), "") + "\n"
        if a[:2] == ("issue", "list"):
            return json.dumps(listing or [])
        if a[:2] == ("pr", "comment"):
            posted.append(a)
            return ""
        raise AssertionError(a)
    monkeypatch.setattr(auto, "gh", gh)
    monkeypatch.setattr(auto, "cost_text", lambda rec, now: "total: $1.00")


def commit(subject, *refs):
    return subject + "\x1f" + subject + "\n\n" + "\n".join(f"refs #{n}" for n in refs) + "\nWhy: x\nUndo: y\n\x1e"


def test_the_report_has_every_section(world, monkeypatch, capsys):
    record(world, started=auto.utc_now() - datetime.timedelta(hours=3))
    start = auto.utc_now() - datetime.timedelta(hours=1)
    log_refusals(["push", "push", "secrets"])
    fake_world(monkeypatch, commits=commit("Add the cart", 12) + commit("Fix the total", 15, 12), titles={12: "Cart page", 15: "Total is wrong", 20: "Mail"},
               listing=[{"number": 20, "title": "Mail", "comments": [
                   {"body": "### ❓ Question\nwhich?", "createdAt": auto.stamp(start)},
                   {"body": "### ❓ Question\nold", "createdAt": "2020-01-01T00:00:00Z"}]},
                        {"number": 21, "title": "Pay", "comments": [{"body": "### ⛔ Blocked\nwaits", "createdAt": auto.stamp(start)}]},
                        {"number": 22, "title": "Other", "comments": [{"body": "### ▶ Started", "createdAt": auto.stamp(start)}]}])
    assert auto.cmd_report(args(pr=7)) == 0
    text = (world.root / "build" / "saani-auto-r1.md").read_text(encoding="utf-8")
    assert capsys.readouterr().out.startswith(text)
    assert "# Auto run r1" in text and "| Id | r1 |" in text and "| Duration | 3 h 00 min |" in text and "| Start |" in text and "| End |" in text
    assert "## Issues landed on the integration branch (2)" in text
    assert "- #12 Cart page" in text and "- #15 Total is wrong" in text
    assert "| #20 | question | Mail |" in text and "| #21 | blocked | Pay |" in text and "| #22 |" not in text and text.count("| #20 |") == 1
    assert "## Refusals by the guard (3)" in text and "- push: 2" in text and "- secrets: 1" in text
    assert "## Cost" in text and "total: $1.00" in text
    assert "`/saani:merge 7`" in text and "`/saani:merge 7 --deploy`" in text


def test_status_and_report_name_each_refusal_by_its_own_reason(world, monkeypatch):
    """The guard's precise codes (#64) reach saani auto status and the report's refusals by reason as they are, never merged."""
    rec = record(world, started=auto.utc_now() - datetime.timedelta(hours=1))
    log_refusals(["glob", "glob", "redirect", "every ref", "revision", "unsure"])
    status = auto.status_text(rec)
    assert "refusals: 6: glob 2, " in status and "redirect 1" in status and "every ref 1" in status and "unsure 1" in status
    fake_world(monkeypatch)
    assert auto.cmd_report(args()) == 0
    text = (world.root / "build" / "saani-auto-r1.md").read_text(encoding="utf-8")
    assert "## Refusals by the guard (6)" in text and "- glob: 2" in text and "- redirect: 1" in text and "- every ref: 1" in text


def test_the_report_without_anything(world, monkeypatch):
    record(world, started=auto.utc_now())
    fake_world(monkeypatch)
    auto.cmd_report(args())
    text = (world.root / "build" / "saani-auto-r1.md").read_text(encoding="utf-8")
    assert text.count("- none") == 3 and "(0)" in text and "`/saani:merge`" in text


def test_the_git_log_is_asked_for_the_integration_branch_since_the_start(world, monkeypatch):
    rec = record(world)
    seen = []

    def git(*a, cwd=None, check=True):
        seen.append(a)
        return subprocess.CompletedProcess(a, 0, stdout="", stderr="")
    monkeypatch.setattr(core, "git", git)
    auto.landed_issues(rec)
    assert seen[0][:3] == ("log", "origin/dev", f"--since={rec['started']}")


def test_post_comments_the_report_on_the_pull_request(world, monkeypatch):
    record(world, started=auto.utc_now())
    posted = []
    fake_world(monkeypatch, posted=posted)
    auto.cmd_report(args(pr=7, post=True))
    assert posted == [("pr", "comment", "7", "--repo", "acme/webshop", "--body-file", str(world.root / "build" / "saani-auto-r1.md"))]


def test_post_needs_the_pull_request_and_a_run(world, monkeypatch):
    with pytest.raises(SystemExit):
        auto.cmd_report(args())                                  # no run
    record(world)
    fake_world(monkeypatch)
    with pytest.raises(SystemExit) as e:
        auto.cmd_report(args(post=True))
    assert "--pr" in str(e.value)


def test_a_left_issue_the_journal_knows_is_reported_when_github_cannot_be_read(world, monkeypatch):
    rec = record(world, started=auto.utc_now())
    auto.journal(30, "blocked")
    fake_world(monkeypatch, titles={30: "Dns"})
    monkeypatch.setattr(auto, "gh", lambda *a: (_ for _ in ()).throw(SystemExit("gh down")) if a[:2] == ("issue", "list") else "Dns\n")
    assert auto.left_issues(rec, auto.read_journal("r1")) == {30: ("blocked", "Dns")}


# ---------------------------------------------------------------- the journal

def test_no_journal_without_a_run(world):
    auto.journal(5, "landed")
    assert not (world.root / "build").exists()


def test_the_journal_has_a_line_per_comment_and_landing(world, monkeypatch):
    record(world)
    posted = []

    class Io:
        dry = False

        def write(self, *cmd):
            posted.append(cmd)
    comments.post(12, "started", io=Io())
    comments.post(12, "question", io=Io())
    comments.post(12, "on-dev", io=Io(), labels=False)           # a landing is journaled by saani land, not twice
    auto.journal(12, "landed")
    rows = [json.loads(x) for x in (world.root / "build" / "saani-auto-r1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["issue"], r["kind"]) for r in rows] == [(12, "started"), (12, "question"), (12, "landed")]
    assert all(r["time"].endswith("Z") for r in rows)
    assert auto.read_journal("r1") == rows


def test_a_dry_run_comment_is_not_journaled(world):
    record(world)
    comments.post(12, "started", io=comments.Direct(dry=True))
    assert not (world.root / "build" / "saani-auto-r1.jsonl").exists()


def test_a_journal_that_cannot_be_written_never_stops_the_work(world, monkeypatch):
    record(world)
    monkeypatch.setattr(auto, "journal_path", lambda run: (_ for _ in ()).throw(OSError("disk full")))
    auto.journal(1, "landed")


# ---------------------------------------------------------------- the command line

def cli(argv):
    import importlib.util
    import saani
    spec = importlib.util.spec_from_file_location("saani_cli", pathlib.Path(saani.__file__).resolve().parents[1] / "saani.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(argv)


def test_the_actions_are_reached_through_saani_auto(world, capsys):
    assert cli(["auto", "on", "--id", "cli1", "--deploy"]) == 0
    assert auto.read_record()["id"] == "cli1" and auto.read_record()["deploy"] is True
    assert cli(["auto", "status", "--check"]) == 0
    assert "auto run cli1" in capsys.readouterr().out
    assert cli(["auto", "off", "--run", "cli1"]) == 0 and auto.read_record() is None


@pytest.mark.parametrize("argv", [["auto", "--check"], ["auto", "status", "--post"], ["auto", "on", "--pr", "3"], ["auto", "pick", "--folder", "x"],
                                  ["auto", "report", "--deploy"], ["auto", "off", "--id", "x"], ["auto", "on", "--run", "x"], ["auto", "status", "--json"]])
def test_an_option_of_another_action_is_refused(world, argv):
    with pytest.raises(SystemExit) as e:
        cli(argv)
    assert isinstance(e.value.code, str) and "saani auto" in e.value.code


def test_the_config_setting_ci_attempts():
    assert config.Config().ci_attempts == 3
    assert config.from_toml({"auto": {"ci_attempts": 5}}, "x").ci_attempts == 5
    for bad in (0, -1, "3", True):
        with pytest.raises(SystemExit):
            config.from_toml({"auto": {"ci_attempts": bad}}, "x")


# ---------------------------------------------------------------- review fixes

def test_the_posted_report_holds_no_local_path(world, monkeypatch):
    record(world, started=auto.utc_now())
    posted = {}
    fake_world(monkeypatch, commits=commit("Add the cart", 12), titles={12: "Cart page"}, posted=[])

    def gh(*a):
        if a[:2] == ("pr", "comment"):
            posted["text"] = pathlib.Path(a[a.index("--body-file") + 1]).read_text(encoding="utf-8")
            return ""
        return "Cart page\n" if a[:2] == ("issue", "view") else "[]"
    monkeypatch.setattr(auto, "gh", gh)
    auto.cmd_report(args(pr=7, post=True))
    text = posted["text"]
    assert "| Folder | auto-2026-11-22_0800 |" in text
    root = world.root.parent
    for path in (root, world.root, world.wt, world.run, world.home, pathlib.Path.home()):
        assert str(path) not in text and str(path).replace("\\", "/") not in text
    assert "\\" not in text.replace("\\|", "")


def test_a_report_row_for_why_the_run_stopped(world, monkeypatch):
    record(world, started=auto.utc_now())
    fake_world(monkeypatch)
    auto.cmd_report(args())
    assert "| Stopped by | finished the list |" in (world.root / "build" / "saani-auto-r1.md").read_text(encoding="utf-8")
    log_refusals(["a"] * 10)
    auto.cmd_report(args())
    text = (world.root / "build" / "saani-auto-r1.md").read_text(encoding="utf-8")
    assert "| Stopped by | refusals for the same reason in a row (10 of 10) |" in text


def test_check_at_a_limit_writes_a_journal_line_the_report_shows(world, monkeypatch):
    record(world, started=auto.utc_now())
    log_refusals(["x"] * 10)
    assert auto.cmd_status(args(check=True)) == 3
    rows = auto.read_journal("r1")
    assert [r["kind"] for r in rows if r["kind"] == "stop"] == ["stop"] and rows[-1]["issue"] is None and "same reason" in rows[-1]["note"]
    fake_world(monkeypatch)
    auto.cmd_report(args())
    assert "stop: refusals for the same reason in a row reached" in (world.root / "build" / "saani-auto-r1.md").read_text(encoding="utf-8")
    record(world, started=auto.utc_now())
    (world.root / "build" / "saani-auto-r1.jsonl").unlink()
    assert auto.cmd_status(args(check=False)) == 0                       # a plain status writes nothing
    assert not (world.root / "build" / "saani-auto-r1.jsonl").exists()


def other_project(world, session="sess-x", run="r1"):
    guard.turn_on(session, run, world.root.parent / "other", world.wt, folder=world.run)


def test_off_run_removes_only_this_projects_entries(world, capsys):
    guard.turn_on("mine", "r1", world.root, world.wt, folder=world.run)
    other_project(world)                                                      # the same run id, another repository
    assert auto.cmd_off(args(run_id="r1")) == 0
    assert guard.state("mine") is None and guard.state("sess-x")["id"] == "r1"
    assert "(1 guard entry removed" in capsys.readouterr().out


def test_the_project_is_compared_without_case_on_windows(world):
    guard.turn_on("mine", "r1", str(world.root).swapcase() if os.name == "nt" else world.root, world.wt, folder=world.run)
    assert auto.end_guard_entries("r1") == 1


def test_refusals_of_another_projects_run_with_the_same_id_do_not_count(world):
    record(world, started=auto.utc_now())
    other_project(world)
    log_refusals(["a"] * 12)
    assert auto.counts(auto.read_record())["refusals"] == 0
    guard.turn_on("mine", "r1", world.root, world.wt, folder=world.run)      # this project holds the run id too
    assert auto.counts(auto.read_record())["refusals"] == 12


def test_a_refusal_line_with_a_project_key_counts_for_that_project_only(world):
    record(world, started=auto.utc_now())
    path = pathlib.Path(guard.registry()) / guard.REFUSALS
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"run": "r1", "reason": "a", "project": str(world.root)}, {"run": "r1", "reason": "a", "project": str(world.root.parent / "other")},
            {"run": "r1", "reason": "b"}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert auto.refusal_reasons("r1") == ["a", "b"]


def test_check_without_a_record_but_a_guard_entry_for_this_repo_exits_3(world, capsys):
    assert auto.cmd_status(args(check=True)) == 0
    other_project(world)
    assert auto.cmd_status(args(check=True)) == 0                              # another repository's run is none of ours
    guard.turn_on("mine", "r1", world.root, world.wt, folder=world.run)
    assert auto.cmd_status(args(check=True)) == 3
    assert "the run record is missing" in capsys.readouterr().out
    assert auto.cmd_status(args(check=False)) == 0                             # a plain status only says no run is on


def test_an_issue_that_landed_is_not_left(world, monkeypatch):
    rec = record(world, started=auto.utc_now() - datetime.timedelta(hours=1))
    now = auto.stamp(auto.utc_now())
    listing = [{"number": 1, "title": "Landed after a question", "comments": [{"body": "### ❓ Question", "createdAt": now}]},
               {"number": 2, "title": "Still waiting", "comments": [{"body": "### ❓ Question", "createdAt": now}]}]
    monkeypatch.setattr(auto, "gh", lambda *a: json.dumps(listing))
    assert set(auto.left_issues(rec, [], landed=[1])) == {2}
    rows = [{"time": "t", "issue": 1, "kind": "question"}]
    assert auto.left_issues(rec, rows, landed=[1]) == {2: ("question", "Still waiting")}


def test_only_the_latest_status_comment_decides_whether_an_issue_is_left(world, monkeypatch):
    rec = record(world, started=START)
    c = lambda body, when: {"body": body, "createdAt": when}  # noqa: E731
    listing = [{"number": 1, "title": "Answered", "comments": [c("### ❓ Question", "2026-11-22T09:00:00Z"),
                                                              c("### ▶ Started", "2026-11-22T10:00:00Z"),
                                                              c("an owner's free comment", "2026-11-22T11:00:00Z")]},
               {"number": 2, "title": "Blocked later", "comments": [c("### ▶ Started", "2026-11-22T09:00:00Z"),
                                                                    c("### ⛔ Blocked", "2026-11-22T10:00:00Z")]},
               {"number": 3, "title": "Question before the run", "comments": [c("### ❓ Question", "2026-11-20T10:00:00Z")]}]
    monkeypatch.setattr(auto, "gh", lambda *a: json.dumps(listing))
    assert auto.left_issues(rec, []) == {2: ("blocked", "Blocked later")}


def test_the_journal_answers_for_an_issue_github_does_not_list_by_its_latest_line(world, monkeypatch):
    rec = record(world, started=START)
    monkeypatch.setattr(auto, "gh", lambda *a: "[]" if a[:2] == ("issue", "list") else "T\n")
    rows = [{"time": "t", "issue": 5, "kind": "question"}, {"time": "t", "issue": 5, "kind": "progress"},
            {"time": "t", "issue": 6, "kind": "blocked"}]
    assert auto.left_issues(rec, rows) == {6: ("blocked", "T")}


def test_pr_zero_is_not_silently_ignored(world, monkeypatch):
    record(world)
    fake_world(monkeypatch)
    with pytest.raises(SystemExit):
        auto.cmd_report(args(pr=0))
    with pytest.raises(SystemExit):
        auto.cmd_report(args(pr=0, post=True))
    with pytest.raises(SystemExit) as e:
        cli(["auto", "status", "--pr", "0"])
    assert "belongs to `saani auto report`" in e.value.code


def test_saani_land_journals_the_landing(world, monkeypatch, capsys):
    record(world)
    root = world.root

    def git(*a, cwd=None, check=True):
        out = {("rev-parse", "--show-toplevel"): str(world.run), ("rev-parse", "--abbrev-ref"): "task/12-cart",
               ("rev-parse", "--git-common-dir"): str(root / ".git"), ("rev-parse", "--short"): "abc1234"}.get(a[:2], "")
        return subprocess.CompletedProcess(a, 0, stdout=out, stderr="") if not check else out
    monkeypatch.setattr(core, "git", git)
    monkeypatch.setattr(core, "take_lock", lambda *a, **k: True)
    monkeypatch.setattr(core, "release_lock", lambda *a, **k: None)
    monkeypatch.setattr(core, "merge_and_rebuild", lambda *a, **k: None)
    monkeypatch.setattr(core, "queued_run", lambda *a, **k: 0)
    monkeypatch.setattr(core, "record", lambda *a, **k: None)
    monkeypatch.setattr(core, "restore_generated", lambda *a, **k: None)
    monkeypatch.setattr(core, "mark_on_dev", lambda *a, **k: True)
    land = type("A", (), {"worktree": None, "keep": True, "dry_run": False, "wait": 1, "no_comment": False})()
    monkeypatch.chdir(world.run)
    assert core.cmd_land(land) == 0
    assert "LANDED: task/12-cart" in capsys.readouterr().out
    assert [(r["issue"], r["kind"]) for r in auto.read_journal("r1")] == [(12, "landed")]


def land_args(**kw):
    return type("A", (), {"worktree": None, "keep": False, "dry_run": False, "wait": 1, "no_comment": False, **kw})()


def stub_landing(world, monkeypatch, on_git=None):
    root = world.root
    calls = []

    def git(*a, cwd=None, check=True):
        calls.append(a)
        if on_git:
            r = on_git(a)
            if r is not None:
                return r
        out = {("rev-parse", "--show-toplevel"): str(world.run), ("rev-parse", "--abbrev-ref"): "task/12-cart",
               ("rev-parse", "--git-common-dir"): str(root / ".git"), ("rev-parse", "--short"): "abc1234"}.get(a[:2], "")
        return subprocess.CompletedProcess(a, 0, stdout=out, stderr="") if not check else out
    monkeypatch.setattr(core, "git", git)
    monkeypatch.setattr(core, "take_lock", lambda *a, **k: True)
    monkeypatch.setattr(core, "release_lock", lambda *a, **k: None)
    monkeypatch.setattr(core, "merge_and_rebuild", lambda *a, **k: None)
    monkeypatch.setattr(core, "queued_run", lambda *a, **k: 0)
    monkeypatch.setattr(core, "record", lambda *a, **k: None)
    monkeypatch.setattr(core, "restore_generated", lambda *a, **k: None)
    monkeypatch.setattr(core, "main_checkout", lambda *a, **k: world.root)
    monkeypatch.chdir(world.run)
    return calls


def test_landing_does_its_post_push_steps_before_the_worktree_is_removed(world, monkeypatch):
    record(world)
    order = []
    stub_landing(world, monkeypatch)
    monkeypatch.setattr(core, "mark_on_dev", lambda *a, **k: order.append("comment") or True)
    monkeypatch.setattr(auto, "journal", lambda *a, **k: order.append("journal"))
    monkeypatch.setattr(core, "remove_landed_worktree", lambda *a, **k: order.append("remove"))
    assert core.cmd_land(land_args()) == 0
    assert order == ["journal", "comment", "remove"]


def test_a_failed_post_push_step_prints_its_next_step_and_the_others_still_run(world, monkeypatch, capsys):
    record(world)
    order = []
    stub_landing(world, monkeypatch)

    def boom(*a, **k):
        raise ImportError("cannot import name 'auto' from 'saani'")
    monkeypatch.setattr(auto, "journal", boom)
    monkeypatch.setattr(core, "mark_on_dev", boom)
    monkeypatch.setattr(core, "remove_landed_worktree", lambda *a, **k: order.append("remove"))
    assert core.cmd_land(land_args()) == 0
    out = capsys.readouterr().out
    assert "next: journal the landing of #12" in out and "next: comment the commit on #12" in out and order == ["remove"]


def failing_removal(world, monkeypatch, listed=None, name="task-12-cart", error="in use"):
    """A landed worktree's folder whose `git worktree remove` fails; `listed` is how `git worktree list` spells that folder (None: it is
    not registered any more). Returns (folder, the git calls)."""
    folder = world.wt / name
    folder.mkdir()
    listing = f"worktree {world.root}\nbranch refs/heads/dev\n"
    if listed:
        listing += f"\nworktree {listed(folder)}\nbranch refs/heads/task/12-cart\n"

    def on_git(a):
        if a[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(a, 1, stdout="", stderr=error)
        if a[:2] == ("worktree", "list"):
            return listing
        return None
    return folder, stub_landing(world, monkeypatch, on_git)


def test_a_removal_that_half_fails_deletes_the_branch_and_names_the_folder_left(world, monkeypatch, capsys):
    folder, calls = failing_removal(world, monkeypatch, error="Directory not empty")
    monkeypatch.setattr(core, "force_remove_folder", lambda p: False)          # something really keeps it locked
    core.remove_landed_worktree(folder, "task/12-cart")
    out = capsys.readouterr().out
    assert ("branch", "-D", "task/12-cart") in calls and str(folder) in out and "unregistered" in out


def test_a_folder_git_left_behind_is_deleted_by_saani_read_only_files_included(world, monkeypatch, capsys):
    folder, calls = failing_removal(world, monkeypatch, error="Permission denied")
    locked = folder / "sub" / "ro.txt"
    locked.parent.mkdir()
    locked.write_text("x")
    os.chmod(locked, 0o400)
    core.remove_landed_worktree(folder, "task/12-cart")
    out = capsys.readouterr().out
    assert not folder.exists() and ("branch", "-D", "task/12-cart") in calls and "removed:" in out and "unregistered" not in out


def test_a_transient_lock_is_retried_until_the_folder_goes(tmp_path, monkeypatch):
    folder = tmp_path / "wt"
    folder.mkdir()
    real, calls = shutil.rmtree, []

    def flaky(p, **kw):
        calls.append(p)
        if len(calls) < 3:
            return None                                                        # still locked: nothing deleted
        return real(p, **kw)
    monkeypatch.setattr(shutil, "rmtree", flaky)
    assert core.force_remove_folder(folder, tries=5, pause=0) is True and len(calls) == 3 and not folder.exists()


def test_a_persistent_lock_gives_up_after_the_tries(tmp_path, monkeypatch):
    folder = tmp_path / "wt"
    folder.mkdir()
    calls = []
    monkeypatch.setattr(shutil, "rmtree", lambda p, **kw: calls.append(p))
    assert core.force_remove_folder(folder, tries=4, pause=0) is False and len(calls) == 4 and folder.exists()


def test_the_main_checkout_is_never_deleted_by_the_leftover_cleanup(world, monkeypatch, capsys):
    folder, calls = failing_removal(world, monkeypatch, error="busy")
    seen = []
    monkeypatch.setattr(core, "force_remove_folder", lambda p: seen.append(p) or True)
    monkeypatch.setattr(core, "main_checkout", lambda cwd=None: folder)       # the landed folder IS the main checkout: refuse
    core.remove_landed_worktree(folder, "task/12-cart")
    assert seen == [] and folder.exists()


def test_a_removal_that_fails_with_the_worktree_still_registered_keeps_it_and_the_branch(world, monkeypatch, capsys):
    folder, calls = failing_removal(world, monkeypatch, listed=str)
    core.remove_landed_worktree(folder, "task/12-cart")
    assert ("branch", "-D", "task/12-cart") not in calls and "the worktree stays" in capsys.readouterr().out


def test_a_registered_worktree_listed_with_another_spelling_still_counts_as_registered(world, monkeypatch, capsys):
    other = lambda folder: str(folder).replace("\\", "/").upper() if os.name == "nt" else str(folder).replace("\\", "/")
    folder, calls = failing_removal(world, monkeypatch, listed=other)
    core.remove_landed_worktree(folder, "task/12-cart")
    assert ("branch", "-D", "task/12-cart") not in calls and "the worktree stays" in capsys.readouterr().out


def test_the_leftover_folder_command_quotes_the_path_literally(world, monkeypatch, capsys):
    folder, _ = failing_removal(world, monkeypatch, name="it's a folder", error="busy")
    monkeypatch.setattr(core, "force_remove_folder", lambda p: False)
    core.remove_landed_worktree(folder, "task/12-cart")
    assert f"Remove-Item -LiteralPath '{str(folder).replace(chr(39), chr(39) * 2)}' -Recurse -Force" in capsys.readouterr().out


def test_git_exiting_during_the_post_push_steps_still_prints_every_next_step(world, monkeypatch, capsys):
    record(world)
    stub_landing(world, monkeypatch)

    def exits(*a, **k):
        raise SystemExit("git worktree list failed")
    monkeypatch.setattr(auto, "journal", exits)
    monkeypatch.setattr(core, "mark_on_dev", exits)
    monkeypatch.setattr(core, "remove_landed_worktree", exits)
    assert core.cmd_land(land_args()) == 0
    out = capsys.readouterr().out
    assert "next: journal the landing of #12" in out and "next: remove the worktree" in out
    assert "next: comment the commit on #12" in out and "git worktree list failed" in out.split("next: comment")[1]


def test_the_marker_line_is_the_guards_contract(world, capsys, tmp_path):
    other = worktree(world.root, world.wt / "auto-x1")
    assert auto.cmd_on(args(id="x1", folder=str(other))) == 0
    out = capsys.readouterr().out
    found = list(guard.MARKER.finditer(out))
    assert len(found) == 1 and out.rstrip('\r\n ') .splitlines()[-1] == found[0].group(0)       # exactly one, and the last line
    assert found[0].group("id") == "x1" and found[0].group("folder") == guard.norm(other)


def test_on_refuses_a_folder_that_is_no_worktree_of_this_project_and_prints_no_marker(world, capsys):
    plain = world.wt / "auto-plain"
    plain.mkdir()
    with pytest.raises(SystemExit) as e:
        auto.cmd_on(args(folder=str(plain)))
    assert "not a worktree of this project" in str(e.value) and "saani auto: on" not in capsys.readouterr().out
    (world.root / "saani.toml").unlink()
    with pytest.raises(SystemExit):
        auto.cmd_on(args())
    assert auto.read_record() is None


def test_off_run_uses_the_guards_turn_off_run_when_no_other_project_holds_the_id(world, monkeypatch):
    seen = []
    real = guard.turn_off_run
    monkeypatch.setattr(guard, "turn_off_run", lambda run: seen.append(run) or real(run))
    guard.turn_on("mine", "r1", world.root, world.wt, folder=world.run)
    assert auto.end_guard_entries("r1") == 1 and seen == ["r1"]
    other_project(world)
    guard.turn_on("mine", "r1", world.root, world.wt, folder=world.run)
    assert auto.end_guard_entries("r1") == 1 and seen == ["r1"]               # another project holds the id: filtered here
    assert guard.state("sess-x") is not None


# ---------------------------------------------------------------- away: the run in a headless session started from the owner's (#50)

def test_the_away_command_runs_the_skill_s_away_part_with_no_prompt_that_can_wait():
    argv = auto.away_command("2026-11-22_0800", "/w/webshop-wt/auto-2026-11-22_0800", only=[12, 15], deploy=True)
    assert argv == ["claude", "-p", "/saani:auto run 2026-11-22_0800 --folder /w/webshop-wt/auto-2026-11-22_0800 --deploy 12 15",
                    "--permission-mode", "auto", "--permission-prompts", "none"]
    assert auto.away_command("r1", "/f")[2] == "/saani:auto run r1 --folder /f"


def test_away_starts_the_headless_session_detached_from_the_main_checkout_with_its_log(world, capsys):
    seen = []
    def popen(argv, **kw):
        seen.append((argv, kw))
        return type("P", (), {"pid": 4242})()
    assert auto.cmd_away(args(only=[12]), which=lambda name: "/bin/claude", popen=popen) == 0
    argv, kw = seen[0]
    assert argv[0] == sys.executable and argv[1] == "-I" and pathlib.Path(argv[2]).name == "saani.py"      # the watcher, not the session
    assert argv[3:7] == ["auto", "watch", "--id", "2026-11-22_0800"] and argv[7] == "--folder" and "--keep-on" not in argv
    assert argv[-2:] == ["--only", "12"]
    assert pathlib.Path(kw["cwd"]).resolve() == world.root.resolve()          # the main folder: the committed relative settings apply
    assert kw["stdin"] is subprocess.DEVNULL and kw["stderr"] is subprocess.STDOUT
    assert ("creationflags" in kw) == (os.name == "nt") and kw.get("start_new_session", os.name == "nt")
    out = capsys.readouterr().out
    log = world.root / "build" / "saani-auto-2026-11-22_0800.log"
    assert "saani auto: away 2026-11-22_0800 started (watcher process 4242)" in out and str(log) in out and log.exists()
    assert "switched off by itself" in out and not (world.git / auto.RECORD).exists()   # `on` is the headless session's
    assert not auto.pid_path("2026-11-22_0800").exists()                        # the watcher writes the session's id, not cmd_away


def test_without_claude_on_the_path_away_prints_the_command_to_start_by_hand(world, capsys):
    called = []
    assert auto.cmd_away(args(only=None), which=lambda name: None, popen=lambda *a, **k: called.append(a)) == 2
    out = capsys.readouterr().out
    assert not called and "Start it by hand" in out and '"/saani:auto run 2026-11-22_0800 --folder ' in out
    assert "--permission-prompts none" in out and "no watcher" in out and "/saani:auto off" in out


def test_a_session_that_cannot_start_falls_back_to_the_command_by_hand(world, capsys):
    def popen(argv, **kw):
        raise OSError("policy")
    assert auto.cmd_away(args(only=None), which=lambda name: "claude", popen=popen) == 2
    assert "could not start (policy)" in capsys.readouterr().out


def test_the_headless_session_waits_for_its_background_coders(world):
    seen = []
    def popen(argv, **kw):
        seen.append(kw)
        return type("P", (), {"pid": 4242})()
    auto.cmd_away(args(only=None), which=lambda name: "claude", popen=popen)
    assert seen[0]["env"][auto.WAIT_VAR] == "0" and seen[0]["env"]["PATH"] == os.environ["PATH"]      # the watcher and so its session
    assert auto.away_env({"A": "1"}) == {"A": "1", auto.WAIT_VAR: "0"}


def test_the_command_to_start_by_hand_carries_the_variable():
    argv = ["claude", "-p", "x"]
    assert auto.by_hand(argv, windows=True) == "$env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS='0'; claude -p x"
    assert auto.by_hand(argv, windows=False) == "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 claude -p x"


def test_away_warns_when_claude_code_does_not_trust_the_folder_but_still_starts(world, capsys):
    popen = lambda argv, **kw: type("P", (), {"pid": 1})()
    home = pathlib.Path(os.environ["USERPROFILE"])
    assert auto.folder_trusted(world.root, home) is False                      # no file
    assert auto.cmd_away(args(only=None), which=lambda name: "claude", popen=popen) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "trust dialog" in out and "started (watcher process 1)" in out
    key = str(world.root).replace("\\", "/").upper() if os.name == "nt" else str(world.root)
    (home / ".claude.json").write_text(json.dumps({"projects": {key: {"hasTrustDialogAccepted": False}}, "secret": "x"}), encoding="utf-8")
    assert auto.folder_trusted(world.root, home) is False
    (home / ".claude.json").write_text(json.dumps({"projects": {key: {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert auto.folder_trusted(world.root, home) is True
    assert auto.cmd_away(args(only=None, id="r2"), which=lambda name: "claude", popen=popen) == 0
    assert "WARNING" not in capsys.readouterr().out


def test_status_says_when_an_away_run_s_process_is_gone(world, capsys, monkeypatch):
    record(world, mode="away")
    assert auto.away_gone(auto.read_record()) is None                          # no pid recorded
    auto.pid_path("r1").write_text("4242", encoding="utf-8")
    assert auto.away_gone(auto.read_record(), alive=lambda pid: False).startswith("  WARNING")      # the injected check
    assert auto.away_gone(auto.read_record(), alive=lambda pid: True) is None
    monkeypatch.setattr(auto.queue, "alive", lambda pid: False)                # the default check is queue.alive
    assert auto.cmd_status(args()) == 0
    out = capsys.readouterr().out
    assert "process (4242) is gone" in out and "saani auto off --run r1" in out and "saani-auto-r1.log" in out
    monkeypatch.setattr(auto.queue, "alive", lambda pid: True)
    auto.cmd_status(args())
    assert "is gone" not in capsys.readouterr().out
    record(world, mode="here")                                                 # a here run has no process
    monkeypatch.setattr(auto.queue, "alive", lambda pid: False)
    auto.cmd_status(args())
    assert "is gone" not in capsys.readouterr().out


def test_trust_counts_for_a_parent_folder_and_any_spelling(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    def write(key, trusted=True, where=home):
        (where / ".claude.json").write_text(json.dumps({"projects": {key: {"hasTrustDialogAccepted": trusted}}}), encoding="utf-8")
    folder = tmp_path / "acme" / "webshop"
    posix = str(folder).replace("\\", "/")
    parent = posix.rsplit("/", 1)[0]
    write(parent)
    assert auto.folder_trusted(folder, home, {}) is True                         # a parent
    write(parent, trusted=False)
    assert auto.folder_trusted(folder, home, {}) is False
    write(posix + "/")
    assert auto.folder_trusted(folder, home, {}) is True                         # trailing slash
    write(str(folder).replace("/", "\\"))
    assert auto.folder_trusted(folder, home, {}) is True                         # backslashes
    write(str(tmp_path / "other"))
    assert auto.folder_trusted(folder, home, {}) is False                        # a sibling is not a parent


def test_trust_reads_claude_config_dir_when_set(tmp_path):
    home, cfgdir = tmp_path / "h", tmp_path / "c"
    home.mkdir()
    cfgdir.mkdir()
    folder = str(tmp_path / "webshop")
    (cfgdir / ".claude.json").write_text(json.dumps({"projects": {folder: {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert auto.folder_trusted(folder, home, {}) is False                        # home has no file
    assert auto.folder_trusted(folder, home, {"CLAUDE_CONFIG_DIR": str(cfgdir)}) is True


def test_a_hostile_config_file_is_not_trusted_and_does_not_crash(tmp_path):
    (tmp_path / ".claude.json").write_text("[" * 200000, encoding="utf-8")          # RecursionError in the parser
    assert auto.folder_trusted(tmp_path / "x", tmp_path, {}) is False
    (tmp_path / ".claude.json").write_text('{"projects": []}', encoding="utf-8")
    assert auto.folder_trusted(tmp_path / "x", tmp_path, {}) is False


def test_away_refuses_a_folder_outside_the_worktrees(world, tmp_path):
    with pytest.raises(SystemExit, match="saani auto away: the run's folder must be a folder inside"):
        auto.cmd_away(args(only=None, folder=str(tmp_path)), which=lambda name: "claude", popen=None)


@pytest.mark.skipif(os.name != "nt", reason="the job breakaway is Windows only")
def test_on_windows_a_job_without_breakaway_still_starts_the_session(tmp_path):
    flags = []
    def popen(argv, creationflags=0, **kw):
        flags.append(creationflags)
        if creationflags & 0x01000000:
            raise OSError("access denied")
        return type("P", (), {"pid": 7})()
    assert auto.start_detached(["claude"], tmp_path, tmp_path / "x.log", popen) == 7
    assert flags[0] & 0x01000000 and not flags[1] & 0x01000000


def test_the_away_options_belong_to_away_and_on_only(world):
    with pytest.raises(SystemExit, match="--folder belongs to `saani auto on` and `saani auto away`"):
        core.cmd_auto(type("A", (), {"action": "status", "folder": "x", "id": None, "json": False, "limit": None, "only": None,
                                    "deploy": False, "check": False, "pr": None, "post": False, "run_id": None})())
    with pytest.raises(SystemExit, match="--only to `pick` and `away`"):
        core.cmd_auto(type("A", (), {"action": "status", "folder": None, "id": None, "json": False, "limit": None, "only": [3],
                                    "deploy": False, "check": False, "pr": None, "post": False, "run_id": None})())


# ---------------------------------------------------------------- the watcher: the run is switched off when its session ends (#67)

FAKE_CLAUDE = "import sys\nprint('session works')\nsys.exit(int(sys.argv[-1]) if sys.argv[-1].isdigit() else 0)\n"


def fake_popen(tmp_path, code=0, during=None):
    """A popen that runs a tiny python script (exit code `code`) in place of `claude -p ...`; `during()` first, as the session's `on`."""
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")

    def popen(argv, **kw):
        if during:
            during(kw["env"])
        return subprocess.Popen([sys.executable, str(script), str(code)], **kw)
    return popen


def session_on(world, run="r1", session="sess"):
    """What the headless session does first: `saani auto on` (the record with the token its environment holds, and the guard's entry)."""
    def on(env):
        record(world, id=run, watch=env[auto.WATCH_VAR])
        guard.turn_on(session, run, world.root, world.wt, folder=world.run)
    return on


def watch_args(**kw):
    return args(**{"id": "r1", "folder": "/w/auto-r1", "only": None, "keep_on": False, **kw})


def test_the_watcher_switches_the_run_off_when_the_session_ends_and_logs_it(world, tmp_path, capsys):
    assert auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=fake_popen(tmp_path, during=session_on(world))) == 0
    assert auto.read_record() is None and guard.state("sess") is None
    out = capsys.readouterr().out
    assert "session ended with code 0; run switched off" in out and "auto run r1 is off" in out
    log = (world.root / "build" / "saani-auto-r1.log").read_text(encoding="utf-8")
    assert "session works" in log                                              # the session's own output goes to the log


def test_the_pid_file_holds_the_session_s_process_id(world, tmp_path):
    record(world)
    seen = []
    def popen(argv, **kw):
        proc = fake_popen(tmp_path)(argv, **kw)
        seen.append(proc.pid)
        return proc
    auto.cmd_watch(watch_args(keep_on=True), which=lambda n: "claude", popen=popen)
    assert auto.pid_path("r1").read_text(encoding="utf-8") == str(seen[0])


def test_the_watcher_runs_the_same_session_command_and_env(world, tmp_path):
    seen = []
    real = fake_popen(tmp_path)
    def popen(argv, **kw):
        seen.append((argv, kw))
        return real(argv, **kw)
    auto.cmd_watch(watch_args(only=[12], deploy=True), which=lambda n: "/bin/claude", popen=popen)
    argv, kw = seen[0]
    assert argv == auto.away_command("r1", "/w/auto-r1", [12], True, claude="/bin/claude")
    assert kw["env"][auto.WAIT_VAR] == "0" and pathlib.Path(kw["cwd"]).resolve() == world.root.resolve()


def test_keep_on_leaves_the_run_on(world, tmp_path, capsys):
    assert auto.cmd_watch(watch_args(keep_on=True), which=lambda n: "claude", popen=fake_popen(tmp_path, during=session_on(world))) == 0
    assert auto.read_record()["id"] == "r1"
    assert "session ended with code 0; kept on (--keep-on)" in capsys.readouterr().out


def test_a_failing_session_is_switched_off_too(world, tmp_path, capsys):
    assert auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=fake_popen(tmp_path, code=3, during=session_on(world))) == 0
    assert auto.read_record() is None
    assert "session ended with code 3; run switched off" in capsys.readouterr().out


def test_a_session_that_cannot_start_is_logged_and_the_run_of_this_start_switched_off(world, capsys):
    def popen(argv, **kw):
        raise OSError("policy")
    assert auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=popen) == 0
    out = capsys.readouterr().out
    assert "session could not start (policy)" in out and "nothing to switch off" in out          # no record at all
    assert auto.cmd_watch(watch_args(), which=lambda n: None, popen=popen) == 0                  # no claude on the PATH
    assert "`claude` is not on the PATH" in capsys.readouterr().out
    def start_then_fail(argv, **kw):                                                              # `on` ran, then the start failed
        session_on(world)(kw["env"])
        raise OSError("late")
    auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=start_then_fail)
    assert auto.read_record() is None and "session could not start (late); run switched off" in capsys.readouterr().out
    auto.cmd_watch(watch_args(keep_on=True), which=lambda n: "claude", popen=start_then_fail)
    assert auto.read_record() is not None and "could not start (late); kept on" in capsys.readouterr().out


def test_the_watcher_leaves_another_run_alone_and_survives_no_record(world, tmp_path, capsys):
    record(world, id="other")
    auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=fake_popen(tmp_path))
    assert auto.read_record()["id"] == "other" and "nothing to switch off" in capsys.readouterr().out


def test_a_record_with_another_token_is_never_ended_by_a_watcher(world, tmp_path, capsys):
    record(world, started=auto.utc_now(), watch="someone-else")                 # run r1 of another start, still going
    guard.turn_on("sess", "r1", world.root, world.wt, folder=world.run)
    assert auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=fake_popen(tmp_path, code=1)) == 0
    assert auto.read_record()["id"] == "r1" and guard.state("sess") is not None
    out = capsys.readouterr().out
    assert "not this session's record" in out and "someone-else" not in out
    record(world)                                                               # a record with no token (a session that never ran `on`)
    auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=fake_popen(tmp_path))
    assert auto.read_record()["id"] == "r1" and "not this session's record" in capsys.readouterr().out


def test_two_overlapping_starts_the_refused_one_never_ends_the_first_run(world, tmp_path, capsys):
    """Both starts passed cmd_away's check before any `on`: A's session runs `on`, B's is refused and exits. B's watcher leaves A alone."""
    ended = []
    def b_session(env):                                                         # B's `saani auto on`: refused, since A's run is on
        ended.append(auto.read_record()["watch"] != env[auto.WATCH_VAR])
    def a_session(env):
        session_on(world)(env)
        # A is still running while B's whole watcher runs
        assert auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=fake_popen(tmp_path, code=1, during=b_session)) == 0
        assert auto.read_record()["id"] == "r1" and guard.state("sess") is not None
    assert auto.cmd_watch(watch_args(), which=lambda n: "claude", popen=fake_popen(tmp_path, during=a_session)) == 0
    assert ended == [True]
    out = capsys.readouterr().out
    assert "not this session's record" in out and "token matched" in out and auto.read_record() is None   # A's own watcher ends A at last


def test_the_run_record_takes_the_watcher_s_token_from_the_environment_on_away_start_only(world, monkeypatch):
    monkeypatch.setenv(auto.WATCH_VAR, "tok")
    assert auto.cmd_on(args()) == 0
    assert auto.read_record()["watch"] == "tok"
    assert auto.away_env({"A": "1", auto.WATCH_VAR: "old"}) == {"A": "1", auto.WAIT_VAR: "0"}     # never one inherited
    assert auto.away_env({}, token="t")[auto.WATCH_VAR] == "t"


def test_away_refuses_while_a_run_is_on_and_leaves_the_pid_file_alone(world):
    record(world, started=auto.utc_now())
    auto.pid_path("r1").write_text("4242", encoding="utf-8")
    called = []
    with pytest.raises(SystemExit, match="saani auto away: run r1 is on"):
        auto.cmd_away(args(only=None, id="r1"), which=lambda n: "claude", popen=lambda *a, **k: called.append(a))
    assert not called and auto.pid_path("r1").read_text(encoding="utf-8") == "4242"
    record(world, started=auto.utc_now() - datetime.timedelta(hours=guard.MAX_HOURS + 1))      # a run past its 12 hours is over
    assert auto.cmd_away(args(only=None, id="r2"), which=lambda n: "claude", popen=lambda *a, **k: type("P", (), {"pid": 1})()) == 0


def test_the_watcher_is_for_away_and_asks_for_an_id_and_a_folder(world):
    with pytest.raises(SystemExit, match="--id"):
        auto.cmd_watch(args(only=None, folder=None))
    with pytest.raises(SystemExit, match="--keep-on belongs to `saani auto away` and `saani auto watch`"):
        core.cmd_auto(type("A", (), {"action": "off", "keep_on": True, "id": None, "folder": None, "deploy": False, "run_id": None})())


def test_away_passes_keep_on_to_the_watcher(world):
    seen = []
    def popen(argv, **kw):
        seen.append(argv)
        return type("P", (), {"pid": 5})()
    assert auto.cmd_away(args(only=None, keep_on=True), which=lambda n: "claude", popen=popen) == 0
    assert "--keep-on" in seen[0] and "--only" not in seen[0]


def test_on_here_writes_the_record_but_never_the_guard_s_marker(world, capsys):
    """#50, the interactive sub-mode: the stop rules and the report work, and the owner's session stays in present mode."""
    assert auto.cmd_on(args(here=True)) == 0
    out = capsys.readouterr().out
    assert not guard.MARKER.search(out) and out.startswith("saani auto: here 2026-11-22_0800 folder=")
    rec = json.loads((world.git / auto.RECORD).read_text(encoding="utf-8"))
    assert rec["mode"] == "here" and rec["id"] == "2026-11-22_0800"


def test_plain_on_is_the_away_mode_and_prints_the_marker(world, capsys):
    assert auto.cmd_on(args()) == 0
    assert guard.MARKER.search(capsys.readouterr().out)
    assert json.loads((world.git / auto.RECORD).read_text(encoding="utf-8"))["mode"] == "away"
