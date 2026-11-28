"""saani/profile.py (#123): the profile of each place of the map, the one resolver and the order of trust chosen per place. The invented
project acme/webshop; no gh, no git."""
import pathlib

import pytest

from saani import board_server, config, profile

ROOT = pathlib.Path(__file__).resolve().parents[1]

BASE = {"state": "open", "worktree": "none", "checks": "no", "need": "no", "status": "todo", "git": "none", "labels": "none", "pr": "not",
        "deploy": "to_deploy", "milestone": "yes"}
# one issue per place whose every signal agrees with the place's profile
CLEAN = {
    "backlog": {**BASE, "status": "backlog", "milestone": "no"},
    "todo": dict(BASE),
    "you": {**BASE, "need": "yes"},
    "works": {**BASE, "worktree": "work", "status": "started"},
    "proving": {**BASE, "worktree": "testing", "status": "started"},
    "yard": {**BASE, "worktree": "green", "status": "review"},
    "depot": {**BASE, "status": "review", "git": "integration", "labels": "in_dev"},
    "tower": {**BASE, "status": "review", "git": "integration", "labels": "in_dev", "pr": "carried"},
    "pad": {**BASE, "status": "review", "git": "production", "labels": "in_main"},
    "archive": {**BASE, "status": "review", "git": "production", "labels": "in_main", "deploy": "to_close"},
    "outpost": {**BASE, "checks": "released", "status": "review", "labels": "in_main"},
    "citadel": {**BASE, "state": "completed", "status": "done", "git": "production"},
}


def resolve(signals, **kw):
    return profile.resolve(signals, **kw)


def outcome(result):
    """(place, confidence, contradicting signals)."""
    return result.place, result.confidence, result.contradict


# ---------------------------------------------------------------- the table

def test_the_table_has_one_row_per_place_and_one_cell_per_signal():
    assert list(profile.PROFILE) == list(profile.IDS) == [p for p in CLEAN]
    for place, cells in profile.PROFILE.items():
        assert set(cells) <= set(profile.SIGNAL_IDS), place
        for signal, cell in cells.items():
            values = [v for c in (cell.values() if isinstance(cell, dict) else [cell]) if c for v in c]
            assert set(values) <= set(profile.WORDS[signal]), (place, signal)


def test_the_kanban_columns_and_the_map_zones_are_the_profile_s_places():
    assert [(k, t, s) for k, t, s, _ in board_server.KANBAN_COLUMNS] == list(profile.PLACES)
    assert sorted(set(profile.PRECEDENCE)) == sorted(profile.IDS)


def test_the_table_is_in_the_docs():
    """docs/SAANI.md holds the table as `saani ui --profile` prints it: a change to the profile is a change to the docs."""
    assert profile.markdown() in (ROOT / "docs" / "SAANI.md").read_text(encoding="utf-8")


def test_any_is_said_where_a_signal_does_not_matter_and_the_log_is_not_read_yet():
    table = profile.markdown()
    assert "| any |" in table and all(row.endswith("| any |") for row in table.splitlines()[2:])     # Saani's log: every place, any


# ---------------------------------------------------------------- the resolver

@pytest.mark.parametrize("place", list(CLEAN))
def test_every_place_with_a_clean_profile_is_sure(place):
    got = resolve(CLEAN[place])
    assert got.place == place and got.confidence == "sure" and got.contradict == ()
    assert set(got.agree) == {s for s in profile.SIGNAL_IDS if profile.cell(place, s) is not profile.ANY}


def test_a_closed_issue_waiting_for_the_deploy_is_a_clean_launch_pad_only_with_a_deploy_command(cfg):
    waiting = {**CLEAN["pad"], "state": "completed", "status": "done"}
    assert resolve(waiting)[:2] == ("citadel", "probable")                       # no deploy: the close is the end, in-main is left over
    cfg.deploy_command = ["deploy.sh"]
    assert resolve(waiting)[:2] == ("pad", "sure")


def rejected(place, signal):
    """A value of `signal` the place does not accept."""
    return next(v for v in profile.WORDS[signal] if v not in profile.cell(place, signal))


READERS = [(p, s) for p in profile.IDS for s in profile.SIGNAL_IDS if profile.cell(p, s) is not profile.ANY]


def test_every_signal_but_the_log_is_read_by_some_place():
    assert {s for _, s in READERS} == set(profile.SIGNAL_IDS) - {"log", "milestone"}           # the milestone: only without statuses
    assert ("todo", "milestone") in [(p, s) for p in profile.IDS for s in profile.SIGNAL_IDS
                                     if profile.cell(p, s, statuses=False) is not profile.ANY]


@pytest.mark.parametrize("place,signal", READERS)
def test_each_signal_contradicting_each_place_that_reads_it_is_probable(place, signal):
    """The place stays, probable, and the signal contradicts it; or the signal is a fact (the worktree, the live checks, the state, the
    owner labels, the pull request, the deploy) or is trusted more than the ones that put the issue there, and the issue moves to a place
    that signal fits (sure when that place fits every signal: a Todo issue set to Backlog is a clean Backlog one)."""
    signals = {**CLEAN[place], signal: rejected(place, signal)}
    got = resolve(signals)
    if got.place == place:
        assert got.confidence == "probable" and signal in got.contradict
    else:
        assert profile.accepts(got.place, signal, signals[signal]) and signal not in got.contradict
        if signal in profile.TRUSTED:
            read = [s for s in profile.TRUSTED if profile.cell(place, s) is not profile.ANY]
            assert profile.TRUSTED.index(signal) < max(profile.TRUSTED.index(s) for s in read)
    assert (got.confidence == "sure") == (got.contradict == ())


@pytest.mark.parametrize("place", ["backlog", "todo"])
def test_the_milestone_contradicting_a_queue_without_statuses(place):
    signals = {**CLEAN[place], "status": None, "milestone": "yes" if place == "backlog" else "no"}
    assert resolve(signals).place == ("todo" if place == "backlog" else "backlog")


# (place, signal, value) -> the place with the default order (status, git, labels) and with the labels first
MOVES = [
    ("depot", "status", "todo", "todo", "depot"),
    ("depot", "status", "backlog", "backlog", "depot"),
    ("depot", "git", "production", "pad", "depot"),
    ("depot", "labels", "none", "depot", "todo"),
    ("depot", "labels", "in_main", "depot", "pad"),
    ("pad", "git", "integration", "depot", "pad"),
    ("pad", "labels", "in_dev", "pad", "depot"),
    ("todo", "labels", "in_dev", "todo", "depot"),
    ("todo", "git", "integration", "todo", "todo"),
    ("todo", "status", "backlog", "backlog", "backlog"),
    ("backlog", "labels", "in_main", "backlog", "pad"),
    ("works", "labels", "in_dev", "works", "works"),               # a worktree with work in it is a fact: it wins over every signal
    ("citadel", "labels", "in_main", "citadel", "citadel"),        # closed, no deploy command: the state is a fact
    ("citadel", "status", "started", "citadel", "citadel"),
]


@pytest.mark.parametrize("place,signal,value,by_default,labels_first", MOVES)
def test_the_order_of_trust_gives_the_place(cfg, place, signal, value, by_default, labels_first):
    signals = {**CLEAN[place], signal: value}
    assert resolve(signals).place == by_default
    cfg.trust = ("labels", "status", "git")
    assert resolve(signals).place == labels_first


def test_the_default_order_is_status_then_git_then_labels():
    assert config.Config().trust == ("status", "git", "labels") == profile.DEFAULT_TRUST
    assert profile.trust_of("depot", config.Config()) == ("status", "git", "labels")


def test_a_project_that_puts_labels_first_keeps_today_s_places(tmp_path):
    """`[pipeline] trust = ["labels"]`: the labels first, the others after in the default order; a stale status no longer moves an issue."""
    c = config.from_toml({"project": {"repo": "acme/webshop"}, "pipeline": {"trust": ["labels"]}}, tmp_path)
    assert c.trust == ("labels", "status", "git")
    stale = {"number": 2, "labels": ["in-dev"], "state": "OPEN", "status": {"name": "Todo", "category": "planned"}}
    assert outcome(resolve(profile.read(stale, statuses_on=True, c=c), c=c)) == ("depot", "probable", ("status",))
    assert resolve(profile.read(stale, statuses_on=True))[:2] == ("todo", "probable")


def test_an_order_of_trust_chosen_for_one_place(tmp_path):
    """Only the Caravan Depot trusts the labels first: an issue its labels put there is there, the others keep the default order."""
    c = config.from_toml({"project": {"repo": "acme/webshop"}, "pipeline": {"place": {"depot": ["labels", "git", "status"]}}}, tmp_path)
    assert c.trust_by_place == {"depot": ("labels", "git", "status")} and c.trust == ("status", "git", "labels")
    assert resolve({**CLEAN["todo"], "labels": "in_dev"}, c=c)[:2] == ("depot", "probable")
    assert resolve({**CLEAN["pad"], "labels": "in_dev"}, c=c).place == "pad"                  # the Launch Pad keeps the status, then git
    assert resolve({**CLEAN["backlog"], "labels": "in_main"}, c=c).place == "backlog"          # in-main is not the depot's: the default order


@pytest.mark.parametrize("pipeline,message", [
    ({"trust": ["status", "deploy"]}, "unknown signal `deploy` (allowed: status, git, labels)"),
    ({"trust": ["log"]}, "unknown signal `log` (allowed: status, git, labels) (the owner's latest manual change"),
    ({"trust": ["git", "git"]}, "names a signal twice"),
    ({"trust": "labels"}, "must be a list of signals"),
    ({"place": {"harbour": ["labels"]}}, "unknown place `harbour` (allowed: backlog, todo, you, works, proving, yard, depot, tower, pad, archive, outpost, citadel)"),
    ({"place": {"depot": ["labels", "branch"]}}, "[pipeline] place depot: unknown signal `branch`"),
    ({"order": ["labels"]}, "unknown setting [pipeline] order"),
])
def test_an_unknown_signal_or_place_is_refused_with_the_allowed_ones(tmp_path, pipeline, message):
    with pytest.raises(SystemExit) as e:
        config.from_toml({"project": {"repo": "acme/webshop"}, "pipeline": pipeline}, tmp_path)
    assert message in str(e.value)


def test_a_repo_without_statuses_reads_no_status_and_the_milestone_splits_the_queue():
    for place in CLEAN:
        signals = {**CLEAN[place], "status": None}
        got = resolve(signals)
        assert got.place == place and got.confidence == "sure" and "status" not in got.agree + got.contradict, place
    assert resolve({**CLEAN["todo"], "status": None, "milestone": "no"}).place == "backlog"
    assert resolve({**CLEAN["backlog"], "status": None, "milestone": "yes"}).place == "todo"
    assert resolve({**CLEAN["todo"], "milestone": "no"})[:2] == ("todo", "sure")              # with statuses the milestone does not matter
    issue = {"number": 1, "labels": [], "state": "OPEN", "milestone": "v1.0.0", "status": {"name": "Backlog", "category": "backlog"}}
    assert profile.read(issue, statuses_on=False)["status"] is None and board_server.queue_for(issue, False) == "todo"


def test_a_project_without_a_deploy_command(cfg):
    """No deploy ever removes in-main: a closed issue is in the Citadel, and a leftover in-main contradicts it; an open in-main issue is on
    the Launch Pad, or in the Archive when nothing of it is to deploy."""
    assert not cfg.deploy_command
    closed = profile.read({"number": 1, "labels": ["in-main"], "state": "CLOSED"})
    assert outcome(resolve(closed)) == ("citadel", "probable", ("labels",))
    assert profile.cell("pad", "state") == ("open",)
    assert resolve(profile.read({"number": 2, "labels": ["in-main"], "state": "OPEN"})).place == "pad"
    assert resolve(profile.read({"number": 3, "labels": ["in-main"], "state": "OPEN"}, closing=True)).place == "archive"
    cfg.deploy_command = ["deploy.sh"]
    assert resolve(closed)[:2] == ("pad", "sure") and profile.cell("pad", "state") == ("open", "completed")


@pytest.mark.parametrize("status", ["started", "review", "done", "none"])
def test_a_closed_issue_without_in_main_is_in_the_citadel_whatever_its_status(cfg, status):
    """With a deploy, the Launch Pad holds a closed issue only while in-main says it waits for the deploy: a status left In review on a
    closed issue does not send it there (`saani deploy` would never move it on)."""
    cfg.deploy_command = ["deploy.sh"]
    closed = {**CLEAN["citadel"], "status": status, "git": None}
    assert outcome(resolve(closed)) == ("citadel", "sure", ())
    assert resolve({**closed, "labels": "in_main"}).place == "pad"
    for order in (("labels", "status", "git"), ("git", "labels", "status")):
        cfg.trust = order
        assert resolve(closed).place == "citadel"


@pytest.mark.parametrize("trust", [("status", "git", "labels"), ("labels", "status", "git")])
def test_an_open_in_main_issue_marked_done_with_nothing_to_deploy_is_to_close(cfg, trust):
    cfg.trust = trust
    issue = {"number": 4, "labels": ["in-main"], "state": "OPEN", "status": {"name": "Done", "category": "completed"}}
    assert outcome(resolve(profile.read(issue, closing=True, statuses_on=True))) == ("archive", "sure", ())
    assert resolve(profile.read(issue, statuses_on=True)).place == "pad"


def test_an_idea_with_no_worktree_is_off_the_map():
    assert resolve({**CLEAN["todo"], "idea": True}) is None
    assert resolve({**CLEAN["todo"], "idea": True, "worktree": "session"}) is None
    assert resolve({**CLEAN["works"], "idea": True}).place == "works"


def test_the_signals_are_read_from_the_issue_and_what_the_board_knows():
    issue = {"number": 7, "labels": ["in-dev", "help wanted"], "state": "OPEN", "milestone": "v1", "status": {"name": "In review", "category": "started"}}
    got = profile.read(issue, worktree={"ahead": 0, "dirty": 0}, git="integration", carried=True)
    assert got == {"state": "open", "worktree": "empty", "checks": "no", "need": "yes", "status": "review", "git": "integration",
                   "labels": "in_dev", "pr": "carried", "deploy": "to_deploy", "milestone": "yes", "idea": False}
    assert profile.read({**issue, "status": None}, statuses_on=True)["status"] == "none"
    assert profile.read({**issue, "state": "CLOSED", "state_reason": "DUPLICATE"})["state"] == "not_planned"
    assert profile.worktree_value({"ahead": 2, "dirty": 0, "green": True}, testing=True) == "testing"
    assert profile.worktree_value({"ahead": 2, "dirty": 0, "green": True}) == "green"
    assert profile.worktree_value({"ahead": 0, "dirty": 3}) == "work" and profile.worktree_value(None, session=True) == "session"


def test_the_board_carries_the_place_its_confidence_and_the_signals():
    issues = [{"number": 1, "title": "a", "labels": ["in-dev"], "state": "OPEN"}, {"number": 2, "title": "b", "labels": [], "state": "OPEN"}]
    board = board_server.build_board(issues, [], set(), [], [], landed={1, 2})
    cards = {c["number"]: c for col in board for c in col["cards"]}
    assert (cards[1]["place"], cards[1]["confidence"], cards[1]["contradict"]) == ("depot", "sure", [])
    assert "git" in cards[1]["agree"] and "labels" in cards[1]["agree"]
    # commits on dev name #2, yet it has no label: git is trusted before the labels by default
    assert (cards[2]["place"], cards[2]["confidence"], cards[2]["contradict"]) == ("depot", "probable", ["labels"])


# ---------------------------------------------------------------- what the other readers take from it

def test_the_labels_set_on_arrival_come_from_the_profile():
    assert profile.arrival("depot") == ("in_dev", [])                  # saani land: in-dev
    assert profile.arrival("pad") == ("in_main", ["in_dev"])           # saani merge / ci merged: in-dev -> in-main
    assert profile.arrival("citadel") == (None, ["in_dev", "in_main"])  # closed by the merge, no deploy
    with pytest.raises(ValueError):
        profile.arrival("works")


def test_auto_pick_reads_on_dev_or_main_from_the_place_or_a_contradicting_signal():
    assert profile.landed(resolve(CLEAN["depot"]), CLEAN["depot"]) == "dev"
    assert profile.landed(resolve(CLEAN["archive"]), CLEAN["archive"]) == "main"
    stale = {**CLEAN["todo"], "labels": "in_main"}
    assert resolve(stale).place == "todo" and profile.landed(resolve(stale), stale) == "main"
    assert profile.landed(resolve(CLEAN["todo"]), CLEAN["todo"]) is None and profile.landed(None, {}) is None


def test_the_map_rules_name_the_project_s_labels_and_branches(cfg):
    cfg.labels = {**cfg.labels, "in_dev": "on-staging"}
    cfg.integration = "staging"
    depot = next(p for p in profile.describe(cfg)["places"] if p["id"] == "depot")
    text = str(depot["rows"]) + depot["next"]
    assert "`on-staging`" in text and "`staging`" in text and "in-dev" not in text
    assert not any(t == "GitHub status" for t, _ in next(p for p in profile.describe(cfg, statuses_on=False)["places"] if p["id"] == "depot")["rows"])
    assert profile.describe(cfg)["wins"][2].startswith("Then the signals in the order of trust: GitHub status, then Git, then Labels")
