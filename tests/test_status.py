"""GitHub's issue statuses (saani/status.py, #72): read the repo's list, move an issue only forward, the steps that set them, auto pick on
Todo, the map's cards, `saani status sync` and the `[status]` settings. Everything on the invented project acme/webshop, gh faked."""
import json

import pytest

from saani import auto, board_server, comments, config, status

STATUSES = [{"id": "S1", "name": "Backlog", "category": {"key": "backlog"}}, {"id": "S2", "name": "Todo", "category": {"key": "planned"}},
            {"id": "S3", "name": "In progress", "category": {"key": "started"}},
            {"id": "S4", "name": "In review", "category": {"key": "started"}},
            {"id": "S5", "name": "Done", "category": {"key": "completed"}},
            {"id": "S6", "name": "Not planned", "category": {"key": "not_planned"}},
            {"id": "S7", "name": "Duplicate", "category": {"key": "duplicate"}}]
FLAT = [{"id": s["id"], "name": s["name"], "category": s["category"]["key"]} for s in STATUSES]


class FakeGh:
    """gh as an `io`: the repo's statuses, one issue at a given status; the writes are recorded."""

    def __init__(self, current=None, statuses=STATUSES, dry=False, fail=False):
        self.current, self.statuses, self.dry, self.fail = current, statuses, dry, fail
        self.reads, self.writes = [], []

    def json(self, *cmd):
        self.reads.append(cmd)
        if self.fail:
            raise SystemExit("failed: gh api graphql\nHTTP 502")
        query = next(a for a in cmd if a.startswith("query="))
        if "planningStatuses" in query:
            return {"data": {"repository": {"planningStatuses": {"nodes": self.statuses} if self.statuses is not None else None}}}
        cur = next((s for s in STATUSES if s["name"] == self.current), None)
        return {"data": {"repository": {"issue": {"id": "I_72", "state": "OPEN", "status": cur}}}}

    def write(self, *cmd):
        self.writes.append(cmd)
        return ""


@pytest.fixture
def fresh():
    status._cache.clear()


def set_to(gh):
    """The status id each setIssueStatus write asked for."""
    return [next(a[2:] for a in w if a.startswith("s=")) for w in gh.writes if "setIssueStatus" in " ".join(w)]


# ---------------------------------------------------------------- settings

def test_the_status_settings_default_to_auto_and_github_s_names():
    c = config.Config()
    assert c.status_enabled == "auto"
    assert c.status_names == {"backlog": "Backlog", "todo": "Todo", "started": "In progress", "review": "In review", "done": "Done"}


def test_status_names_merge_with_the_defaults_and_bad_values_are_refused(tmp_path):
    c = config.from_toml({"project": {"repo": "acme/webshop"}, "status": {"enabled": "off", "names": {"review": "Reviewing"}}}, tmp_path)
    assert c.status_enabled == "off" and c.status_names["review"] == "Reviewing" and c.status_names["todo"] == "Todo"
    with pytest.raises(SystemExit, match="enabled"):
        config.from_toml({"project": {"repo": "acme/webshop"}, "status": {"enabled": "yes"}}, tmp_path)
    with pytest.raises(SystemExit, match="unknown: blocked"):
        config.from_toml({"project": {"repo": "acme/webshop"}, "status": {"names": {"blocked": "Blocked"}}}, tmp_path)


# ---------------------------------------------------------------- reading the repo's statuses

def test_the_repo_s_statuses_are_read_once(fresh):
    gh = FakeGh()
    assert status.available(gh) == FLAT
    assert status.available(gh) == FLAT and len(gh.reads) == 1


@pytest.mark.parametrize("gh", [FakeGh(statuses=None), FakeGh(fail=True)], ids=["not available", "gh refused"])
def test_no_statuses_when_the_repo_has_none_or_gh_refuses(fresh, gh):
    assert status.available(gh) is None


def test_off_in_the_settings_never_asks_github(fresh, cfg):
    cfg.status_enabled = "off"
    gh = FakeGh()
    assert status.available(gh) is None and gh.reads == []


def test_a_step_is_matched_by_name_then_by_category(cfg):
    assert status.resolve("started", FLAT)["name"] == "In progress"
    assert status.resolve("review", FLAT)["name"] == "In review"
    renamed = [dict(s, name={"In progress": "Doing", "In review": "Checking"}.get(s["name"], s["name"])) for s in FLAT]
    assert status.resolve("started", renamed)["name"] == "Doing"           # the first started status
    assert status.resolve("review", renamed)["name"] == "Checking"         # the last one
    cfg.status_names = {**cfg.status_names, "review": "Checking"}
    assert status.step_of({"name": "Checking", "category": "started"}, renamed) == "review"


def test_the_step_of_a_status():
    assert [status.step_of({"name": s["name"], "category": s["category"]}, FLAT) for s in FLAT] == \
        ["backlog", "todo", "started", "review", "done", "closed", "closed"]
    assert status.step_of(None, FLAT) is None


# ---------------------------------------------------------------- moving an issue, only forward

@pytest.mark.parametrize("current, step, expected", [
    (None, "started", "S3"), ("Todo", "started", "S3"), ("Backlog", "review", "S4"), ("In progress", "review", "S4"),
    ("In review", "started", None),            # never back to In progress
    ("In review", "review", None), ("Done", "review", None), ("Not planned", "started", None)])
def test_a_status_only_moves_forward(fresh, current, step, expected):
    gh = FakeGh(current)
    assert status.advance(72, step, gh) == (next(s["name"] for s in FLAT if s["id"] == expected) if expected else None)
    assert set_to(gh) == ([expected] if expected else [])


def test_a_dry_run_says_what_it_would_set(fresh, capsys):
    gh = FakeGh("Todo", dry=True)
    status.advance(72, "started", gh)
    assert "#72 status -> In progress" in capsys.readouterr().out


def test_a_refusal_is_printed_and_never_fails_the_command(fresh, capsys):
    status._cache["acme/webshop"] = FLAT
    assert status.advance(72, "started", FakeGh(fail=True)) is None
    assert "could not set the status of #72" in capsys.readouterr().out


def test_nothing_happens_on_a_repo_without_statuses():
    gh = FakeGh("Todo")                                     # conftest: acme/webshop has none
    assert status.advance(72, "started", gh) is None and gh.writes == []


# ---------------------------------------------------------------- the steps that set it

@pytest.mark.parametrize("kind, expected", [("started", ["S3"]), ("review", ["S4"]), ("on-dev", ["S4"]), ("on-main", ["S4"]),
                                            ("question", []), ("progress", []), ("deployed", [])])
def test_status_comments_move_the_status(fresh, monkeypatch, kind, expected):
    monkeypatch.setattr(auto, "journal", lambda *a: None)
    gh = FakeGh("Todo")
    comments.post(72, kind, {"Commit": "abc1234"}, io=gh)
    assert set_to(gh) == expected


def test_a_comment_whose_label_the_caller_sets_still_moves_the_status(fresh, monkeypatch):
    monkeypatch.setattr(auto, "journal", lambda *a: None)
    gh = FakeGh("In progress")
    comments.post(72, "on-main", {"Pull request": "#9"}, io=gh, labels=False)
    assert set_to(gh) == ["S4"]


# ---------------------------------------------------------------- auto pick: Todo only

def issue(n, status_name=None, **kw):
    cat = next((s["category"] for s in FLAT if s["name"] == status_name), None)
    return {"number": n, "title": f"issue {n}", "labels": [], "milestone": "v1.0.0", "body": "", "type": "Task",
            "status": {"name": status_name, "category": cat} if status_name else None, **kw}


def test_with_statuses_auto_pick_takes_only_todo_issues():
    issues = [issue(1, "Todo"), issue(2, "Backlog"), issue(3), issue(4, "In review")]
    eligible, skipped = auto.choose(issues, set(), {}, "2026-11-24", statuses=FLAT)
    assert [e["number"] for e in eligible] == [1]
    assert {s["number"]: s["reason"] for s in skipped} == {2: "not Todo (status: Backlog)", 3: "not Todo (status: none)",
                                                           4: "not Todo (status: In review)"}


def test_without_statuses_auto_pick_is_as_before():
    eligible, _ = auto.choose([issue(1, "Todo"), issue(2, "Backlog"), issue(3)], set(), {}, "2026-11-24", statuses=None)
    assert [e["number"] for e in eligible] == [1, 2, 3]


def test_the_older_reasons_still_come_first():
    _, skipped = auto.choose([issue(5, "Backlog", labels=["in-dev"])], set(), {}, "2026-11-24", statuses=FLAT)
    assert skipped == [{"number": 5, "reason": "already on dev"}]


def test_auto_reads_types_and_statuses_in_one_listing(monkeypatch):
    monkeypatch.setattr(auto, "gh", lambda *a: "4\tBug\tTodo\tplanned\n5\t\tBacklog\tbacklog\n6\tTask\t\t\n")
    types, statuses = auto.read_types_and_statuses()
    assert types == {4: "Bug", 6: "Task"}
    assert statuses == {4: {"name": "Todo", "category": "planned"}, 5: {"name": "Backlog", "category": "backlog"}}


# ---------------------------------------------------------------- the map

def test_the_map_s_cards_carry_the_status(monkeypatch):
    def fake_run(*args, **kw):
        if args[1] == "issue":
            return json.dumps([{"number": 1, "title": "a", "labels": [], "state": "OPEN"}, {"number": 2, "title": "b", "labels": [], "state": "OPEN"}])
        return "1\tBug\tIn review\tstarted\n2\tTask\t\t\n"
    monkeypatch.setattr(board_server, "run", fake_run)
    rows = board_server.gh_issues("--state", "open", "--limit", "300")
    assert [(r["number"], r["status"]) for r in rows] == [(1, {"name": "In review", "category": "started"}), (2, None)]


def test_the_map_shows_the_status_and_the_sample_has_both_cases():
    page = (board_server.HERE / "map.html").read_text(encoding="utf-8")
    assert "c.status.name" in page
    cards = json.loads(board_server.MOCK_FILE.read_text(encoding="utf-8"))["cards"]
    assert any(c.get("status") for c in cards) and any(not c.get("status") for c in cards)


# ---------------------------------------------------------------- saani status sync

def test_sync_plans_from_what_saani_knows():
    issues = [{"number": 1, "labels": ["in-dev"], "milestone": None, "status": None},
              {"number": 2, "labels": [], "milestone": None, "status": None},
              {"number": 3, "labels": [], "milestone": "v1.0.0", "status": None},
              {"number": 4, "labels": [], "milestone": None, "status": {"name": "Todo", "category": "planned"}},
              {"number": 5, "labels": ["in-main"], "milestone": None, "status": {"name": "Todo", "category": "planned"}},
              {"number": 6, "labels": [], "milestone": "v1.0.0", "status": {"name": "Backlog", "category": "backlog"}},
              {"number": 7, "labels": [], "milestone": None, "status": {"name": "In review", "category": "started"}},
              {"number": 8, "labels": [], "milestone": None, "status": None}]
    rows = status.sync_plan(issues, {8}, FLAT)
    assert [(n, old, new, step) for n, old, new, step, _ in rows] == [
        (1, None, "In review", "review"), (2, None, "Backlog", "backlog"), (3, None, "Todo", "todo"),
        (5, "Todo", "In review", "review"), (8, None, "In progress", "started")]   # 4, 6, 7: the owner's choice, left alone


def test_sync_on_a_repo_without_statuses_does_nothing(capsys):
    assert status.cmd_sync(type("Args", (), {"apply": True})()) == 0
    assert "not available" in capsys.readouterr().out
