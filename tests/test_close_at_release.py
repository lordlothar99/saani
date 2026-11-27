"""Close at release: the merge of the release pull request closes the `Closes #n` issues (GitHub does it), `saani ci merged` relabels and
tells which closed and which stay open, `saani deploy` closes nothing, and the board shows a closed issue still labelled in-main as To
deploy. gh faked, the invented project acme/webshop."""
import pytest

from saani import ci
from saani import board_server as fp
from test_comments import FakeIo

BODY = "### Release v1.3.0\n\nCloses #1\nCloses #3\nrefs #2"


class MergedIo(FakeIo):
    """ci.Run for `ci merged` of a release pull request: #1 and #3 are closed by it, #2 stays open; all three are in-dev."""

    def __init__(self, dry=False):
        super().__init__(labels={1: ["in-dev"], 2: ["in-dev"], 3: ["in-dev"]})
        self.dry = dry

    def json(self, *cmd):
        if cmd[:3] == ("gh", "pr", "view"):
            return {"state": "MERGED", "mergedAt": "2026-11-22T14:26:00Z", "title": "Release v1.3.0", "body": BODY,
                    "commits": [{"messageHeadline": "x (refs #1)", "messageBody": ""}], "mergeCommit": {"oid": "abcdef0123456789"}}
        if cmd[:3] == ("gh", "workflow", "list"):
            return []
        return super().json(*cmd)

    def write(self, *cmd):
        if self.dry:
            print("dry-run: " + " ".join(cmd))
            return ""
        return super().write(*cmd)


@pytest.fixture
def merged(cfg, monkeypatch):
    from saani import core
    monkeypatch.setattr(core, "finished_auto_runs", lambda before=None, cwd=None: [])
    monkeypatch.setattr(core, "remove_auto_runs", lambda found, dry=False, cwd=None: None)
    monkeypatch.setattr(ci, "log", lambda entry: None)
    cfg.relabel = None
    return cfg


def edits(io):
    return {w[3]: w[6:] for w in io.written if w[2] == "edit"}               # after `--repo acme/webshop`


def test_the_keywords_a_pull_request_closes_with_are_read_from_its_body_only():
    assert ci.closes_named({"title": "x", "body": BODY}) == [1, 3]
    assert ci.closes_named({"title": "Fixes #8", "body": "closed #9, resolves: #10, closes the door, refs #11"}) == [9, 10]   # not the title
    assert ci.closes_named({"body": None}) == []


def test_a_relabel_that_ran_outside_takes_in_main_off_the_closed_issues_of_a_project_without_a_deploy(merged, capsys, monkeypatch):
    class Io(MergedIo):
        def __init__(self):
            super().__init__()
            self.labels = {n: [{"name": "in-main"}] for n in (1, 2, 3)}      # the GitHub workflow already swapped them
        def json(self, *cmd):
            if cmd[:3] == ("gh", "workflow", "list"):
                return [{"name": "Relabel merged issues", "state": "active"}]
            if cmd[:3] == ("gh", "run", "list"):
                return [{"databaseId": 1, "conclusion": "success", "createdAt": "2026-11-22T14:27:00Z"}]
            return super().json(*cmd)
    merged.ci_jobs = {"relabel": {"minutes": 1, "heavy": False, "what": "x", "workflow": "Relabel merged issues"}}
    io = Io()
    assert ci.cmd_merged(io, 9) == 0
    assert edits(io) == {"1": ("--remove-label", "in-main"), "3": ("--remove-label", "in-main")}      # #2 stays open (refs): untouched
    io = Io()
    merged.deploy_command = ["deploy.sh"]
    assert ci.cmd_merged(io, 9) == 0 and edits(io) == {}                                              # with a deploy they wait as To deploy


def test_ci_merged_says_which_issues_closed_and_which_stay_open_and_closes_none_by_hand(merged, capsys):
    merged.deploy_command = ["deploy.sh"]
    io = MergedIo()
    assert ci.cmd_merged(io, 9) == 0
    out = capsys.readouterr().out
    assert "closed by GitHub at the merge (Closes): #1, #3; Saani does not close them by hand" in out
    assert "stay open for the owner (refs): #2" in out
    assert not [w for w in io.written if w[2] == "close"]


def test_with_a_deploy_every_merged_issue_goes_to_in_main_the_closed_ones_wait_there_as_to_deploy(merged):
    merged.deploy_command = ["deploy.sh"]
    io = MergedIo()
    assert ci.cmd_merged(io, 9) == 0
    assert edits(io) == {n: ("--remove-label", "in-dev", "--add-label", "in-main") for n in ("1", "2", "3")}


def test_without_a_deploy_the_merge_s_close_is_the_end_in_main_is_not_set_on_the_closed_issues(merged):
    assert not merged.deploy_command
    io = MergedIo()
    assert ci.cmd_merged(io, 9) == 0
    got = edits(io)
    assert got["1"] == got["3"] == ("--remove-label", "in-dev")                          # closed: in-dev off, no in-main
    assert got["2"] == ("--remove-label", "in-dev", "--add-label", "in-main")            # refs: stays open, relabelled as before


def test_the_dry_run_of_the_merge_prints_the_closes_the_label_changes_and_writes_nothing(merged, capsys):
    merged.deploy_command = ["deploy.sh"]
    io = MergedIo(dry=True)
    ci.preview_status(io, 9)
    out = capsys.readouterr().out
    assert "dry-run: closed by GitHub at the merge (Closes): #1, #3" in out and "dry-run: stay open for the owner (refs): #2" in out
    assert "dry-run: #1: in-dev -> in-main" in out and "dry-run: #2: in-dev -> in-main" in out
    assert io.written == []
    merged.deploy_command = []
    ci.preview_status(io, 9)
    assert "dry-run: #3: in-dev removed (nothing to deploy)" in capsys.readouterr().out


# ---------------------------------------------------------------- the board

def card(n, labels, state="CLOSED", **extra):
    return {"number": n, "title": f"t{n}", "labels": labels, "state": state, "milestone": None, **extra}


def test_a_closed_issue_that_still_carries_in_main_is_to_deploy_not_deployed():
    assert fp.column_for(card(1, ["in-main"])) == "main"
    assert fp.column_for(card(2, [])) == "live" and fp.column_for(card(3, ["in-main"], "OPEN")) == "main"
    assert fp.column_for(card(4, ["in-dev"])) == "live"


def test_the_kanban_puts_it_on_the_launch_pad_until_the_deploy_removes_the_label():
    board = fp.build_board([card(1, ["in-main"]), card(2, []), card(3, ["in-main"], "OPEN")], [], set(), [], [])
    kanban = {c["id"]: c for c in fp.build_kanban(board)["columns"]}
    assert sorted(c["number"] for c in kanban["pad"]["cards"]) == [1, 3]                  # closed by the merge but not deployed, and the open one
    assert "#1" in kanban["pad"]["prompt"]
    assert [c["number"] for c in kanban["citadel"]["cards"]] == [2]


def test_a_closed_issue_marked_not_planned_or_duplicate_stays_under_not_planned_in_the_citadel_and_is_flagged(cfg):
    cfg.deploy_command = ["deploy.sh"]
    dropped = [card(1, ["in-main"], state_reason="NOT_PLANNED"), card(2, ["in-main"], state_reason="DUPLICATE"),
               card(3, ["in-main"], status={"name": "Not planned", "category": "not_planned"}), card(4, ["in-main"], state_reason="COMPLETED")]
    assert [fp.column_for(i) for i in dropped] == ["live", "live", "live", "main"]
    board = fp.build_board(dropped, [], set(), [], [])
    kanban = {c["id"]: c for c in fp.build_kanban(board)["columns"]}
    assert sorted(c["number"] for c in kanban["citadel"]["cards"] if c["section"] == "notplanned") == [1, 2, 3]
    assert [c["number"] for c in kanban["pad"]["cards"]] == [4]
    assert fp.hygiene([], dropped, set(), cfg.labels) == [{"kind": "closed_labelled", "issues": [1, 2, 3]}]


def test_closed_in_main_issues_are_fetched_without_a_date_limit(cfg, monkeypatch):
    calls = []

    def fake_run(*cmd, **k):
        if cmd[:3] == ("gh", "issue", "list"):
            calls.append(cmd)
            state = cmd[cmd.index("--state") + 1]
            if "--label" in cmd:                                         # closed long ago, still in-main
                return '[{"number": 5, "title": "old", "labels": [{"name": "in-main"}], "state": "CLOSED", "stateReason": "COMPLETED"}]'
            return '[]' if state == "open" else '[{"number": 6, "title": "new", "labels": [], "state": "CLOSED"}]'
        return ""
    monkeypatch.setattr(fp, "run", fake_run)
    got = fp.issues()
    waiting = [c for c in calls if "--label" in c][0]
    assert "in-main" in waiting and "--search" not in waiting
    assert sorted(i["number"] for i in got["closed"]) == [5, 6] and sorted(i["number"] for i in got["board"]) == [5, 6]


def test_a_closed_issue_labelled_in_main_is_not_untidy_with_a_deploy_but_is_without_one(cfg):
    lab = cfg.labels
    closed = [card(7, ["in-main"]), card(8, ["in-dev"])]
    assert fp.hygiene([], closed, set(), lab) == [{"kind": "closed_labelled", "issues": [7, 8]}]
    cfg.deploy_command = ["deploy.sh"]
    assert fp.hygiene([], closed, set(), lab) == [{"kind": "closed_labelled", "issues": [8]}]
