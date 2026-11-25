"""saani/guard.py, the deadline: a decision is bounded in time and in size; what runs out of either is denied, never allowed."""
import json
import pathlib
import threading
import time

import pytest

from saani import guard
from guard_helpers import fake_repo, sh, tool


def test_grep_and_rm_refuse_a_tree_too_big_to_look_through(ctx, monkeypatch):
    monkeypatch.setattr(guard, "WALK_CAP", 3)
    big = pathlib.Path(ctx.places["work"]) / "big"
    big.mkdir()
    for k in range(5):
        (big / f"f{k}.txt").write_text("x")
    assert tool(ctx, "Grep", pattern="x", path="{work}/big")[2] == "secret"
    assert tool(ctx, "Grep", pattern="x", path="{work}/big", glob="*.py")[2] == "secret"    # past the cap even with a glob
    assert tool(ctx, "Grep", pattern="x", path="{work}/big/f1.txt")[0] == "allow"
    assert sh(ctx, "rm -rf {work}/big")[2] == "guard file"


def test_a_walk_past_the_guard_s_time_budget_is_refused(ctx, monkeypatch):
    (pathlib.Path(ctx.places["work"]) / "src" / "app.py").write_text("x")
    assert tool(ctx, "Grep", pattern="x", path="{work}")[0] == "allow"
    assert sh(ctx, "rm -rf {work}/src")[0] == "allow"
    monkeypatch.setattr(guard, "BUDGET_SECONDS", 0)
    assert tool(ctx, "Grep", pattern="x", path="{work}")[2] == "too big"                 # the walk ran out of time: refused
    assert sh(ctx, "rm -rf {work}/src")[2] == "too big"


def test_one_deadline_bounds_the_whole_decision_not_each_word(ctx, monkeypatch):
    work = pathlib.Path(ctx.places["work"])
    for k in range(20):
        (work / f"f{k}.txt").write_text("x")
    words = " ".join(f"f{k}.txt" for k in range(20))
    assert sh(ctx, f"cat {words}")[0] == "allow"
    clock = [time.monotonic()]

    def tick():                                                                          # every look at the clock: one second later
        clock[0] += 1
        return clock[0]
    monkeypatch.setattr(time, "monotonic", tick)
    assert sh(ctx, f"cat {words}")[2] == "too big"                                        # 20 words, each quick, too long together


def test_a_decision_that_blocks_is_cut_off_with_a_deny_within_the_budget(tmp_path, home, monkeypatch, capsys):
    main, work = fake_repo(tmp_path)
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt")
    monkeypatch.setattr(guard, "HARD_STOP_SECONDS", 0.3)
    release = threading.Event()
    monkeypatch.setattr(guard.os.path, "realpath", lambda p, *a, **k: release.wait(5) or p)   # a disconnected drive: every lookup blocks
    monkeypatch.setattr(guard, "log_refusal", lambda *a: None)                            # (its own lookup would block too)
    exits = []
    monkeypatch.setattr(guard, "hard_exit", exits.append)
    raw = json.dumps({"session_id": "s-1", "cwd": str(work), "hook_event_name": "PreToolUse", "tool_name": "Read",
                      "tool_input": {"file_path": str(work / "x.txt")}}).encode()
    began = time.monotonic()
    try:
        assert guard.main(raw) == 0
        cut_off_after = time.monotonic() - began
    finally:
        release.set()                                                                    # the blocked lookup ends with the test
    assert cut_off_after < 4 and exits == [0]                                            # cut off at 0.3 s, not after the 5 s the lookup waits
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "could not decide" in out["permissionDecisionReason"]


def test_a_long_glued_message_ending_in_a_newline_is_decided_fast(ctx):
    word = "-" + "m" * 32_000 + "\n"
    began = time.monotonic()
    assert not guard.glued_message(word)
    sh(ctx, "git commit '" + word + "'")
    assert time.monotonic() - began < 3                                                  # linear (quadratic: minutes); room for a busy machine
    for w, glued in (("-mx", True), ("-amx", True), ("-qam x", True), ("-m", False), ("-mx\ny", False), ("-1mx", False), ("x", False)):
        assert guard.glued_message(w) is glued, w


def test_a_huge_revision_word_is_decided_fast(ctx):
    began = time.monotonic()
    for word in ("HEAD" + "~" * 200_000 + "x", "HEAD" + "~" * 200_000, "a.." * 70_000, "-" + "f" * 200_000 + "1"):
        guard.allowed_rev(word, ctx)
        guard.flag_cluster(word, "f")
    assert sh(ctx, "git fetch -" + "f" * 50_000 + "1 origin")[0] == "deny"
    assert time.monotonic() - began < 3                                                  # linear: no backtracking (quadratic: minutes)


def test_an_oversize_input_is_refused_before_any_work(tmp_path, home, monkeypatch, capsys):
    main, work = fake_repo(tmp_path)
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt")
    monkeypatch.setattr(guard, "decide", lambda *a: pytest.fail("an oversize input reached the decision"))
    raw = json.dumps({"session_id": "s-1", "cwd": str(work), "hook_event_name": "PreToolUse", "tool_name": "Bash",
                      "tool_input": {"command": "git log HEAD" + "~" * 40_000}}).encode()
    assert guard.main(raw) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "32 KB" in out["permissionDecisionReason"]
    assert not guard.too_big({"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "x" * 100_000}})   # content: not judged


def test_a_big_walk_says_too_big_before_the_hard_stop_says_too_slow(tmp_path, home, monkeypatch, capsys):
    assert guard.BUDGET_SECONDS < guard.HARD_STOP_SECONDS < 15                          # the inner deadline answers first
    main, work = fake_repo(tmp_path)
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt")
    monkeypatch.setattr(guard, "BUDGET_SECONDS", 0)
    monkeypatch.setattr(guard, "context", lambda cwd, st, sid: guard.Context(work, wt_dir=tmp_path / "webshop-wt", session=sid))
    raw = json.dumps({"session_id": "s-1", "cwd": str(work), "hook_event_name": "PreToolUse", "tool_name": "Grep",
                      "tool_input": {"pattern": "x", "path": str(work)}}).encode()
    (work / "a.txt").write_text("x")
    guard.main(raw)
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "more to look through" in out["permissionDecisionReason"]
