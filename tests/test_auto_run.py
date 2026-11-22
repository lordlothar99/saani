"""saani auto on|off|status|report: the run's record, the stop rules, the report and the journal. git and gh are faked; the guard's registry
(~/.saani/auto) lives under a temporary HOME; the project is the invented acme/webshop."""
import datetime
import json
import os
import pathlib
import subprocess

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
    assert argv[0] == "/bin/claude" and argv[1] == "-p" and argv[2].startswith("/saani:auto run 2026-11-22_0800 --folder ")
    assert argv[2].endswith(" 12") and argv[3:] == ["--permission-mode", "auto", "--permission-prompts", "none"]
    assert pathlib.Path(kw["cwd"]).resolve() == world.root.resolve()          # the main folder: the committed relative settings apply
    assert kw["stdin"] is subprocess.DEVNULL and kw["stderr"] is subprocess.STDOUT
    assert ("creationflags" in kw) == (os.name == "nt") and kw.get("start_new_session", os.name == "nt")
    out = capsys.readouterr().out
    log = world.root / "build" / "saani-auto-2026-11-22_0800.log"
    assert "saani auto: away 2026-11-22_0800 started (process 4242)" in out and str(log) in out and log.exists()
    assert "saani auto off --run 2026-11-22_0800" in out and not (world.git / auto.RECORD).exists()   # `on` is the headless session's


def test_without_claude_on_the_path_away_prints_the_command_to_start_by_hand(world, capsys):
    called = []
    assert auto.cmd_away(args(only=None), which=lambda name: None, popen=lambda *a, **k: called.append(a)) == 2
    out = capsys.readouterr().out
    assert not called and "Start it by hand" in out and '"/saani:auto run 2026-11-22_0800 --folder ' in out
    assert "--permission-prompts none" in out


def test_a_session_that_cannot_start_falls_back_to_the_command_by_hand(world, capsys):
    def popen(argv, **kw):
        raise OSError("policy")
    assert auto.cmd_away(args(only=None), which=lambda name: "claude", popen=popen) == 2
    assert "could not start (policy)" in capsys.readouterr().out


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
