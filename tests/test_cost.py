"""saani/cost.py: tokens and estimated cost per issue, role and model, from synthetic Claude Code transcripts."""
import datetime
import json
import pathlib

from saani import cost, kpis

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
    assert cost.slug(r"c:\Users\x\projects\webshop") == "c--Users-x-projects-webshop"


def test_price_by_family_prefix_and_unknown_model_has_no_cost():
    assert cost.cost_of("claude-sonnet-5-5", {"input": 1_000_000, "output": 1_000_000}) == 3.0 + 15.0
    assert cost.cost_of("claude-opus-9", {"cache_read": 1_000_000, "cache_write": 1_000_000}) == 1.5 + 18.75
    assert cost.cost_of("gpt-x", {"input": 5}) is None


def test_issue_by_branch_then_worktree_folder_then_description_else_unattributed():
    assert cost.issue_of("feat/42-slug", "/x/webshop-wt/7-y", "webshop-wt", "#9") == 42
    assert cost.issue_of("dev", r"C:\p\webshop-wt\7-y", "webshop-wt", "#9") == 7
    assert cost.issue_of("dev", "/p/webshop", "webshop-wt", "#9 rename") == 9
    assert cost.issue_of("dev", "/p/webshop", "webshop-wt") == cost.UNATTRIBUTED


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
    return type("C", (), {"root": root, "wt_dir": wt})()


def test_read_attributes_dedupes_windows_and_prices(tmp_path, monkeypatch):
    c = project(tmp_path, monkeypatch)
    r = cost.read(c, SINCE, UNTIL)
    assert r["total"]["messages"] == 4 and r["total"]["input"] == 100 + 1_000_000 + 50
    issues = {e["issue"]: e for e in r["per_issue"]}
    assert set(issues) == {"unattributed", 42, 7}
    assert issues[42]["cost"] == 15.0 and issues[42]["unpriced"] is True       # opus input, plus a model with no price
    assert issues[7]["cost"] == 15.0 and issues[7]["unpriced"] is False         # coder, 1M sonnet output
    assert [e["issue"] for e in r["per_issue"]][:2] in ([42, 7], [7, 42])
    roles = {e["role"]: e for e in r["per_role"]}
    assert set(roles) == {"architect", "coder"} and roles["coder"]["output"] == 1_000_000
    assert {e["model"] for e in r["per_model"]} == {"claude-sonnet-5-5", "claude-opus-9", "mystery-1"}
    assert abs(r["total"]["cost"] - (30.0 + 100 * 3 / 1e6 + 10 * 15 / 1e6)) < 1e-4


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
    c = type("C", (), {"root": tmp_path / "webshop", "wt_dir": tmp_path / "webshop-wt"})()
    assert cost.read(c, SINCE, UNTIL)["total"]["messages"] == 0


def test_kpis_report_carries_the_tokens():
    r = kpis.report([], [], [], SINCE, UNTIL, tokens=cost.aggregate([]))
    assert r["tokens"]["total"]["messages"] == 0 and "Tokens and cost" in kpis.text(r)


def test_folders_match_whatever_the_case_of_the_drive_letter(tmp_path):
    (tmp_path / "c--Users-x-webshop").mkdir()
    (tmp_path / "c--Users-x-webshop-wt-42-slug").mkdir()
    (tmp_path / "c--Users-x-other").mkdir()
    found = cost.project_folders(tmp_path, r"C:\Users\x\webshop", r"C:\Users\x\webshop-wt")
    assert [d.name for d in found] == ["c--Users-x-webshop", "c--Users-x-webshop-wt-42-slug"]
