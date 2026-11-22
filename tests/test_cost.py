"""saani/cost.py: tokens and estimated cost per issue, role and model, from synthetic Claude Code transcripts."""
import datetime
import json
import pathlib

from saani import config, cost, kpis

D = datetime.date
SINCE, UNTIL = D(2026, 11, 21), D(2026, 11, 22)


def msg(mid, model="claude-sonnet-5-5", day="2026-11-21", branch="", cwd="", i=0, o=0, cr=0, cw=0, text="SECRET TEXT"):
    return {"type": "assistant", "timestamp": day + "T10:00:00Z", "gitBranch": branch, "cwd": cwd,
            "message": {"id": mid, "model": model, "content": text,
                        "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}


def write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines) + "\n", encoding="utf-8")


def test_slug_replaces_every_char_outside_letters_and_digits():
    assert cost.slug(r"D:\work\acme\webshop") == "D--work-acme-webshop"


def test_price_by_family_prefix_and_unknown_model_has_no_cost():
    assert cost.cost_of("claude-sonnet-5-5", {"input": 1_000_000, "output": 1_000_000}) == 2.0 + 10.0
    assert cost.cost_of("claude-opus-9", {"cache_read": 1_000_000, "cache_write": 1_000_000}) == 0.2 + 4.0 * 1.25      # family fallback, 5m
    assert cost.cost_of("claude-opus-5-5-2027", {"cache_write": 1_000_000, "cache_write_1h": 1_000_000}) == 8.0         # all 1-hour
    assert cost.cost_of("claude-fable-5-2", {"input": 1_000_000}) == 8.0
    assert cost.cost_of("gpt-x", {"input": 5}) is None


def test_issue_by_branch_then_worktree_folder_then_description_else_unattributed():
    wb = config.Config(repo="acme/webshop").worker_branch
    assert cost.issue_of("feat/42-slug", "/x/webshop-wt/7-y", "webshop-wt", "#9", wb) == 42
    assert cost.issue_of("dev", r"D:\p\webshop-wt\7-y", "webshop-wt", "#9", wb) == 7
    assert cost.issue_of("dev", "/p/webshop", "webshop-wt", "#9 rename", wb) == 9
    assert cost.issue_of("dev", "/p/webshop", "webshop-wt", "", wb) == cost.UNATTRIBUTED
    assert cost.issue_of("release/2026-11-22", "/p/webshop", "webshop-wt", "", wb) == cost.UNATTRIBUTED


def test_roles():
    assert cost.role_of(None) == "architect"
    assert cost.role_of("saani:coder") == "coder"
    assert cost.role_of("general-purpose") == "general-purpose"


def project(tmp_path, monkeypatch):
    root, wt = tmp_path / "webshop", tmp_path / "webshop-wt"
    projects = tmp_path / "claude" / "projects"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    main = projects / cost.slug(root)
    work = projects / cost.slug(wt / "42-slug")
    other = projects / cost.slug(tmp_path / "other")
    write(main / "s1.jsonl", [
        msg("m1", i=100, o=10),                                           # architect, unattributed
        msg("m1", i=100, o=10),                                           # same id again: counted once
        msg("m2", model="claude-opus-9", branch="feat/42-slug", i=1_000_000),
        "not json",
        msg("old", day="2026-11-01", i=999),                              # outside the window
        {"type": "user", "message": {"content": "hello"}}])
    write(main / "s1" / "subagents" / "agent-a1.jsonl", [msg("m3", o=1_000_000)])
    (main / "s1" / "subagents" / "agent-a1.meta.json").write_text(
        json.dumps({"agentType": "saani:coder", "description": "#7 rename"}), encoding="utf-8")
    write(work / "s2.jsonl", [msg("m4", cwd=str(wt / "42-slug"), model="mystery-1", i=50)])
    write(other / "s3.jsonl", [msg("m5", i=777)])
    return config.Config(repo="acme/webshop", root=root)


def test_read_attributes_dedupes_windows_and_prices(tmp_path, monkeypatch):
    c = project(tmp_path, monkeypatch)
    r = cost.read(c, SINCE, UNTIL)
    assert r["total"]["messages"] == 4 and r["total"]["input"] == 100 + 1_000_000 + 50
    issues = {e["issue"]: e for e in r["per_issue"]}
    assert set(issues) == {"unattributed", 42, 7}
    assert issues[42]["cost"] == 4.0 and issues[42]["unpriced"] is True        # opus input, plus a model with no price
    assert issues[7]["cost"] == 10.0 and issues[7]["unpriced"] is False         # coder, 1M sonnet output
    assert [e["issue"] for e in r["per_issue"]][:2] in ([42, 7], [7, 42])
    roles = {e["role"]: e for e in r["per_role"]}
    assert set(roles) == {"architect", "coder"} and roles["coder"]["output"] == 1_000_000
    assert {e["model"] for e in r["per_model"]} == {"claude-sonnet-5-5", "claude-opus-9", "mystery-1"}
    assert abs(r["total"]["cost"] - (14.0 + 100 * 2 / 1e6 + 10 * 10 / 1e6)) < 1e-4


def test_no_message_text_is_kept_and_the_top_limits_the_issues(tmp_path, monkeypatch):
    c = project(tmp_path, monkeypatch)
    r = cost.read(c, SINCE, UNTIL, top=1)
    assert "SECRET TEXT" not in json.dumps(r) and len(r["per_issue"]) == 1 and r["issues_total"] == 3


def test_text_shows_the_estimate_and_a_question_mark_for_unknown_models(tmp_path, monkeypatch):
    c = project(tmp_path, monkeypatch)
    t = cost.text(cost.read(c, SINCE, UNTIL))
    assert "estimate" in t and "#42" in t and "per role" in t and "per model" in t and "mystery-1: ?" in t
    assert "no Claude Code transcript" in cost.text(cost.aggregate([]))


def test_missing_transcripts_give_an_empty_report(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nothing"))
    c = config.Config(repo="acme/webshop", root=tmp_path / "webshop")
    assert cost.read(c, SINCE, UNTIL)["total"]["messages"] == 0


def test_kpis_report_carries_the_tokens():
    r = kpis.report([], [], [], SINCE, UNTIL, tokens=cost.aggregate([]))
    assert r["tokens"]["total"]["messages"] == 0 and "Tokens and cost" in kpis.text(r)


def test_folders_are_the_main_checkouts_and_the_worktrees_never_a_sibling_project(tmp_path):
    for name in ("d--work-webshop", "d--work-webshop-wt", "d--work-webshop-wt-42-slug", "d--work-webshop-admin",
                 "d--work-webshop-wtools", "d--work-other"):
        (tmp_path / name).mkdir()
    found = cost.project_folders(tmp_path, r"D:\work\webshop", r"d:\work\webshop-wt")
    assert [d.name for d in found] == ["d--work-webshop", "d--work-webshop-wt", "d--work-webshop-wt-42-slug"]


def test_odd_lines_do_not_crash_and_empty_rows_are_dropped(tmp_path):
    f = tmp_path / "s.jsonl"
    odd = [{"type": "assistant", "timestamp": "2026-11-21T10:00:00Z", "gitBranch": 5, "cwd": ["x"],
            "message": {"id": 7, "model": "claude-sonnet-5-5", "usage": {"output_tokens": 5}}},                  # id not a string
           {"type": "assistant", "timestamp": "2026-11-21T10:00:00Z", "gitBranch": 5, "cwd": ["x"],
            "message": {"id": "a", "model": None, "usage": {"output_tokens": 5, "input_tokens": "x"}}},
           msg("synthetic", model="<synthetic>"),                                                                   # no token at all
           [1, 2], "3"]
    write(f, odd)
    rows = cost.usage_rows([(f, False)], "webshop-wt", SINCE, UNTIL)
    assert len(rows) == 1 and rows[0]["model"] == "?" and rows[0]["issue"] == cost.UNATTRIBUTED and rows[0]["output"] == 5


def test_a_repeated_message_keeps_its_largest_usage(tmp_path):
    f = tmp_path / "s.jsonl"
    write(f, [msg("m", o=3), msg("m", o=40), msg("m", o=9)])
    rows = cost.usage_rows([(f, False)], "webshop-wt", SINCE, UNTIL)
    assert [r["output"] for r in rows] == [40]


def test_cache_writes_are_priced_by_their_lifetime(tmp_path):
    f = tmp_path / "s.jsonl"
    line = msg("m", model="claude-opus-5-5", cw=2_000_000)
    line["message"]["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}
    write(f, [line])
    row = cost.usage_rows([(f, False)], "webshop-wt", SINCE, UNTIL)[0]
    assert row["cache_write"] == 2_000_000 and row["cache_write_1h"] == 1_000_000
    assert cost.cost_of(row["model"], row) == 4.0 * 1.25 + 4.0 * 2.0


def test_a_file_last_written_before_the_window_is_not_read(tmp_path):
    import os
    f = tmp_path / "s.jsonl"
    write(f, [msg("m", i=5)])
    stamp = datetime.datetime(2026, 11, 1).timestamp()
    os.utime(f, (stamp, stamp))
    assert cost.usage_rows([(f, False)], "webshop-wt", SINCE, UNTIL) == []
