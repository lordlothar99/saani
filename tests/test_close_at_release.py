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


def test_the_keywords_a_pull_request_closes_with_are_read_from_its_title_and_body():
    assert ci.closes_named({"title": "x", "body": BODY}) == [1, 3]
    assert ci.closes_named({"title": "Fixes #8", "body": "closed #9, resolves: #10, closes the door, refs #11"}) == [8, 9, 10]
    assert ci.closes_named({"body": None}) == []


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


def test_the_kanban_puts_it_under_done_in_the_section_to_deploy_until_the_deploy_removes_the_label():
    board = fp.build_board([card(1, ["in-main"]), card(2, []), card(3, ["in-main"], "OPEN")], [], set(), [], [])
    kanban = fp.build_kanban(board, True)
    done = next(c for c in kanban["columns"] if c["id"] == "done")
    sections = {s["id"]: s for s in done["sections"]}
    assert [c["number"] for c in done["cards"] if c["section"] == "todeploy"] == [1]
    assert [c["number"] for c in done["cards"] if c["section"] == "deployed"] == [2]
    assert sections["todeploy"]["count"] == 1 and "#1" in sections["todeploy"]["prompt"]
    review = next(c for c in kanban["columns"] if c["id"] == "review")
    assert [c["number"] for c in review["cards"] if c["section"] == "main"] == [3]        # the open one stays on production, to deploy


def test_a_closed_issue_labelled_in_main_is_not_untidy_with_a_deploy_but_is_without_one(cfg):
    lab = cfg.labels
    closed = [card(7, ["in-main"]), card(8, ["in-dev"])]
    assert fp.hygiene([], closed, set(), lab) == [{"kind": "closed_labelled", "issues": [7, 8]}]
    cfg.deploy_command = ["deploy.sh"]
    assert fp.hygiene([], closed, set(), lab) == [{"kind": "closed_labelled", "issues": [8]}]
