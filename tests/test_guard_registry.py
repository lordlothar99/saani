"""saani/guard.py, the registry, the switch, the hook and the refusal log: present mode, `saani auto on` and off, expiry, failing closed."""
import datetime
import json
import os

import pytest

from saani import guard
from guard_helpers import AUTO_ON, fake_repo, fwd, hook, marker, post, ran, run_folder


def test_the_shared_git_folder_is_found_from_a_worktree_without_git(tmp_path):
    main, work = fake_repo(tmp_path)
    assert guard.git_dirs(work) == (str(work), os.path.normpath(str(main / ".git")))
    (work / "deeper").mkdir()
    assert guard.git_dirs(work / "deeper") == (str(work), os.path.normpath(str(main / ".git")))
    assert guard.git_dirs(main) == (str(main), str(main / ".git"))


def test_turn_on_and_off_by_session(tmp_path, home):
    st = guard.turn_on("s-1", "2026-01-02_0300", tmp_path / "webshop", tmp_path / "webshop-wt", tmp_path / "webshop-wt" / "auto-x")
    assert st["deploy"] is False and st["id"] == "2026-01-02_0300"
    assert json.loads((home / ".saani" / "auto" / "s-1.json").read_text()) == st
    assert guard.state("s-1") == st and guard.state("s-2") is None
    assert guard.state("../x") is None and guard.state(None) is None
    assert guard.turn_off("s-1") == st
    assert guard.state("s-1") is None and guard.turn_off("s-1") is None
    with pytest.raises(ValueError):
        guard.turn_on("../evil", "x", "m", "w")
    with pytest.raises(ValueError):
        guard.turn_on("s-1", "a b", "m", "w")


def test_a_broken_entry_fails_closed_and_an_old_one_is_over(tmp_path, home):
    guard.turn_on("s-1", "r", "m", "w")
    (home / ".saani" / "auto" / "s-1.json").write_text("not json")
    with pytest.raises(guard.RegistryError):
        guard.state("s-1")
    old = (datetime.datetime.now() - datetime.timedelta(hours=guard.MAX_HOURS + 1)).isoformat()
    guard.turn_on("s-2", "r", "m", "w", started=old)
    assert guard.state("s-2")["over"] is True                                            # over, never present mode again
    assert guard.state("s-2")["over"] is True


def test_an_entry_renamed_over_between_the_two_looks_is_still_over(tmp_path, home, monkeypatch):
    old = (datetime.datetime.now() - datetime.timedelta(hours=guard.MAX_HOURS + 1)).isoformat()
    guard.turn_on("s-1", "r", "m", "w", started=old)
    entry = home / ".saani" / "auto" / "s-1.json"
    real, looks = open, []

    def racing(path, *a, **k):                       # another call expires the entry right after this one looked for the mark
        looks.append(os.path.basename(path))
        if looks == ["s-1.over"]:
            os.replace(entry, entry.with_suffix(".over"))
            raise FileNotFoundError(path)
        return real(path, *a, **k)
    monkeypatch.setattr(guard, "open", racing, raising=False)
    assert guard.state("s-1")["over"] is True and looks == ["s-1.over", "s-1.json", "s-1.over"]


def test_present_mode_prints_nothing(tmp_path, home):
    main, work = fake_repo(tmp_path)
    assert hook(home, work, "Bash", command="git push --force") is None
    assert hook(home, work, "Write", file_path=str(home / ".bashrc"), content="x") is None
    assert hook(home, work, "Bash", raw=b"not json") is None
    assert hook(home, work, "Bash", session=None, command="git push") is None
    assert not (home / ".saani" / "auto" / guard.REFUSALS).exists()


def test_saani_auto_on_is_asked_then_registered_after_it_ran_and_only_the_owner_ends_it(tmp_path, home):
    main, work = fake_repo(tmp_path)
    run = run_folder(tmp_path, main)
    assert hook(home, run, "Bash", command=AUTO_ON) is None                             # present: the owner's permissions decide
    assert guard.state("s-1") is None
    assert post(home, run, AUTO_ON, ran(marker("run-1", run))) is None                  # PostToolUse prints nothing
    st = guard.state("s-1")
    assert st["id"] == "run-1" and st["main"] == str(main) and st["repo"] == "acme/webshop"
    assert guard.norm(st["folder"]) == guard.norm(run) and guard.norm(st["wt_dir"]) == guard.norm(tmp_path / "webshop-wt")
    assert st["tests"][0] == ["{python}", "-m", "pytest", "-q"]
    assert hook(home, work, "Bash", session="s-2", command="git push") is None          # another session stays present
    assert hook(home, work, "Bash", command="git status && git diff")["permissionDecision"] == "allow"
    out = hook(home, work, "Bash", command="git push origin HEAD:dev --secret-words-here")
    assert out["permissionDecision"] == "deny" and "saani land" in out["permissionDecisionReason"]
    assert hook(home, work, "Read", file_path=str(work / ".env"))["permissionDecision"] == "deny"
    assert hook(home, work, "Write", file_path=str(home / ".saani" / "x.json"))["permissionDecision"] == "deny"
    assert hook(home, work, "Bash", command="python -m pytest -q")["permissionDecision"] == "allow"
    sub = {"agent_id": "a-1", "agent_type": "saani:coder"}                              # a subagent's call: same session
    event = {"session_id": "s-1", "cwd": str(work), "tool_name": "Bash", "tool_input": {"command": "git push"}, **sub}
    assert hook(home, work, "Bash", raw=json.dumps(event).encode())["permissionDecision"] == "deny"
    for switch in (AUTO_ON, AUTO_ON + " --deploy", "saani auto off", "saani auto off --run run-1"):
        out = hook(home, run, "Bash", command=switch)
        assert out["permissionDecision"] == "deny" and "saani auto off --run" in out["permissionDecisionReason"], switch
    started = guard.state("s-1")["started"]
    post(home, run, AUTO_ON + " --deploy", ran(marker("run-1", run)))                  # never re-armed
    assert guard.state("s-1")["started"] == started and guard.state("s-1")["deploy"] is False
    log = (home / ".saani" / "auto" / guard.REFUSALS).read_text().splitlines()
    rows = [json.loads(line) for line in log]
    assert [(r["tool"], r["reason"], r["word"], r["run"]) for r in rows][:4] == [
        ("Bash", "git push", "git", "run-1"), ("Read", "secret", "", "run-1"), ("Write", "guard file", "", "run-1"),
        ("Bash", "git push", "git", "run-1")]
    assert "secret-words-here" not in "".join(log) and "HEAD:dev" not in "".join(log)
    assert {r["project"] for r in rows} == {guard.norm(main)}
    assert guard.refusals("run-1", project=main)["total"] == 8 and guard.refusals("run-1", project=tmp_path)["total"] == 0
    assert guard.refusals("run-1") == {"total": 8, "by_reason": {"git push": 2, "secret": 1, "guard file": 1, "saani auto switch": 4}}
    assert guard.refusals("other")["total"] == 0
    assert guard.turn_off_run("run-1") == 1 and guard.turn_off_run("run-1") == 0       # the owner, from a terminal
    assert guard.state("s-1") is None
    assert hook(home, work, "Bash", command="git push") is None
    assert guard.refusals()["total"] == 8


@pytest.mark.parametrize("command,response,why", [
    (AUTO_ON, {"stdout": "auto run run-1 is on\n", "interrupted": False}, "no marker"),
    (AUTO_ON, "MARKER", "a plain text response with the marker"),
    (AUTO_ON, {"stdout": "MARKER", "interrupted": True}, "interrupted"),
    (AUTO_ON, {"stdout": "MARKER", "interrupted": False, "exit_code": 1}, "a non-zero exit code"),
    (AUTO_ON, {"stdout": "MARKER" + "MARKER", "interrupted": False}, "the marker twice"),
    (AUTO_ON, {"stdout": "  MARKER", "interrupted": False}, "the marker not at the start of a line"),
    (AUTO_ON, {"stderr": "MARKER", "interrupted": False}, "the marker on stderr"),
    (AUTO_ON + " --id other", {"stdout": "MARKER", "interrupted": False}, "another id than the marker's"),
    (AUTO_ON + " --bogus", {"stdout": "MARKER", "interrupted": False}, "an unknown option"),
    ("cd . && " + AUTO_ON, {"stdout": "MARKER", "interrupted": False}, "a chained command"),
    ("echo 'saani auto: on run-1 folder=x'", {"stdout": "MARKER", "interrupted": False}, "not saani auto on"),
    ("python -I saani.py auto on", {"stdout": "MARKER", "interrupted": False}, "not the plugin's saani.py"),
])
def test_registration_needs_the_whole_command_a_normal_end_and_one_marker(tmp_path, home, command, response, why):
    main, work = fake_repo(tmp_path)
    run = run_folder(tmp_path, main)
    text = marker("run-1", run).split("\n", 1)[1]
    response = response.replace("MARKER", text) if isinstance(response, str) else {
        k: v.replace("MARKER", text) if isinstance(v, str) else v for k, v in response.items()}
    post(home, run, command, response)
    if why == "a plain text response with the marker":
        assert guard.state("s-1")["id"] == "run-1"                                       # a string response is the output itself
    else:
        assert guard.state("s-1") is None, why


def test_registration_needs_a_run_folder_inside_the_worktrees_of_a_saani_project(tmp_path, home):
    main, work = fake_repo(tmp_path)
    run = run_folder(tmp_path, main)
    post(home, run, AUTO_ON, ran(marker("run-1", main)))                                 # the main checkout as the run's folder
    post(home, run, AUTO_ON, ran(marker("run-1", tmp_path / "webshop-wt")))              # the worktrees folder itself
    post(home, run, AUTO_ON, ran(marker("run-1", tmp_path / "webshop-wt" / "nope")))     # a folder that does not exist
    post(home, run, AUTO_ON + " --folder " + fwd(work), ran(marker("run-1", run)))      # --folder and the marker disagree
    post(home, tmp_path, AUTO_ON, ran(marker("run-1", tmp_path)))                        # not a git checkout
    assert guard.state("s-1") is None
    post(home, run, AUTO_ON + " --id run-1 --deploy", ran(marker("run-1", run)))
    assert guard.state("s-1")["deploy"] is True


def test_an_expired_entry_is_over_every_call_refused_until_the_owner_ends_the_run(tmp_path, home):
    main, work = fake_repo(tmp_path)
    old = (datetime.datetime.now() - datetime.timedelta(hours=13)).isoformat()
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt", started=old)
    for call in ({"command": "git status"}, {"command": "ls"}):                          # denied, and denied again: never present mode
        out = hook(home, work, "Bash", **call)
        assert out["permissionDecision"] == "deny" and "over" in out["permissionDecisionReason"], call
    assert hook(home, work, "Read", file_path=str(work / "README.md"))["permissionDecision"] == "deny"
    assert not (home / ".saani" / "auto" / "s-1.json").exists() and (home / ".saani" / "auto" / "s-1.over").exists()
    post(home, work, AUTO_ON, ran(marker("r", work)))                                    # never re-armed
    assert guard.state("s-1")["over"] is True
    assert guard.refusals("r")["by_reason"] == {"run over": 3}
    assert guard.turn_off_run("r") == 1                                                  # the owner, from a terminal: the mark goes too
    assert not (home / ".saani" / "auto" / "s-1.over").exists() and guard.state("s-1") is None
    assert hook(home, work, "Bash", command="git status") is None
    guard.turn_on("s-2", "r2", main, tmp_path / "webshop-wt", started=old)
    assert guard.state("s-2")["over"] is True and guard.turn_off("s-2")["over"] is True
    assert guard.state("s-2") is None


def test_post_tool_use_does_not_go_through_the_decision_thread(tmp_path, home, monkeypatch):
    monkeypatch.setattr(guard, "hard_exit", lambda code: pytest.fail("PostToolUse was cut off"))
    raw = json.dumps({"session_id": "s-1", "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
                      "cwd": str(tmp_path)}).encode()
    assert guard.main(raw) == 0 and guard.state("s-1") is None


def test_the_guard_fails_closed(tmp_path, home):
    main, work = fake_repo(tmp_path)
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt")
    (home / ".saani" / "auto" / "s-1.json").write_text("{broken")
    out = hook(home, work, "Bash", command="ls")
    assert out["permissionDecision"] == "deny" and "guard error: registry" in out["permissionDecisionReason"]
    out = hook(home, work, "Bash", raw=b"{broken")                                       # unreadable input while a run is registered
    assert out["permissionDecision"] == "deny" and "unreadable hook input" in out["permissionDecisionReason"]
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt")
    (home / ".saani" / "auto" / "s-1.json").write_text(json.dumps({**guard.state("s-1"), "worker_branch": "("}))
    out = hook(home, work, "Bash", command="ls")
    assert out["permissionDecision"] == "deny" and "guard error" in out["permissionDecisionReason"]
