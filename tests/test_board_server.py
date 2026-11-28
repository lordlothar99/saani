"""saani/board_server.py: the status page and the board, read-only, this PC only. The pure functions, the privacy of the session files
and the server's address check."""
import json
import pathlib
import re
import threading
import urllib.error
import urllib.request

import pytest

from saani import board_server as fp




def test_an_agent_is_working_idle_or_asleep_by_its_last_write():
    assert fp.activity(30) == "working"
    assert fp.activity(10 * 60) == "idle"
    assert fp.activity(3 * 3600) == "asleep"


def test_only_the_folder_branch_and_time_of_a_session_are_kept_never_its_text():
    lines = [json.dumps({"type": "user", "cwd": "C:/x/webshop-wt/12-a", "gitBranch": "task/12-a", "timestamp": "2026-11-21T10:00:00Z",
                         "message": {"content": "a private sentence"}}),
             "not json", json.dumps(["a list"]),
             json.dumps({"type": "assistant", "timestamp": "2026-11-21T10:05:00Z", "message": {"content": "another"}})]
    meta = fp.session_meta(lines)
    assert meta == {"cwd": "C:/x/webshop-wt/12-a", "branch": "task/12-a", "last": "2026-11-21T10:05:00Z", "model": None, "focus": 12,
                    "turn_open": True}                                       # a yes or no, never text
    assert "private" not in json.dumps(meta)


def test_a_subagent_shows_its_type_description_model_and_state():
    row = fp.subagent_row({"agentType": "Explore", "description": "x" * 300, "model": "claude-haiku-5-5", "finished": False}, 20)
    assert row["state"] == "working" and row["type"] == "Explore" and len(row["description"]) == 120
    assert fp.subagent_row({"finished": True}, 20)["state"] == "done"


def test_a_worktree_row_names_its_issue_from_the_branch():
    assert fp.worktree_row("p", "feat/42-thing", 3, 0, "s", "now")["issue"] == 42
    assert fp.worktree_row("p", "dev", 0, 0, "s", "now")["issue"] is None


def test_a_tier1_run_is_green_only_when_every_part_is():
    green = fp.tier1_row("c", {"date": "d", "estimate": 70, "wall": 37.2, "parts": [{"code": 0}, {"code": 0}]})
    assert green["result"] == "passed" and green["seconds"] == 37.2
    assert fp.tier1_row("c", {"parts": [{"code": 0}, {"code": 1}]})["result"] == "failed"


def test_each_issue_goes_to_the_column_of_its_stage():
    i = lambda n, *labels, state="OPEN": {"number": n, "labels": list(labels), "state": state}
    busy = {"ahead": 2, "dirty": 0}
    assert fp.column_for(i(1, "owner-action")) == "you"
    assert fp.column_for(i(1, "help wanted")) == "you"
    assert fp.column_for(i(2)) == "ready"
    assert fp.column_for(i(3), busy) == "building"
    assert fp.column_for(i(3), {"ahead": 0, "dirty": 0}) == "building"          # a fresh worktree: being built
    assert fp.column_for(i(4, "in-dev"), busy, testing={4}) == "testing"
    assert fp.column_for(i(5, "in-dev"), {"ahead": 0, "dirty": 0}) == "dev"     # joined dev, its folder kept: on dev
    assert fp.column_for(i(6, "in-main")) == "main"
    assert fp.column_for(i(7, state="CLOSED")) == "live"
    assert fp.column_for(i(8, "idea")) is None


def test_a_card_waiting_for_the_owner_says_whether_it_needs_an_answer_or_an_action_and_gives_a_prompt():
    assert fp.need_of(["help wanted", "ui"]) == "answer" and fp.need_of(["owner-action"]) == "act" and fp.need_of([]) is None
    assert "options and your recommendation" in fp.prompt_for(490, "you", "answer")
    assert "step by step" in fp.prompt_for(422, "you", "act")
    assert fp.prompt_for(530, "ready").startswith("Start #530")
    assert fp.prompt_for(516, "main") is None and fp.prompt_for(516, "dev") is None   # one button per column there
    assert fp.prompt_for(9, "live") is None
    assert all(len(c) == 3 and c[2] for c in fp.COLUMNS)                        # every column explains itself


def test_merged_on_dev_and_to_deploy_have_one_prompt_for_the_whole_column():
    """one pull request takes all of dev, one deploy takes all of main, so one button in the column's title."""
    assert "#3, #12" in fp.column_prompt("dev", [12, 3]) and "pull request" in fp.column_prompt("dev", [12, 3])
    assert "deploy command" in fp.column_prompt("main", [516]) and "deploy warnings" in fp.column_prompt("main", [516])
    assert fp.column_prompt("main", []) is None and fp.column_prompt("ready", [1]) is None
    board = {c["id"]: c for c in fp.build_board([{"number": 7, "title": "x", "labels": ["in-main"], "state": "OPEN"}], [], set(), [], [])}
    assert "#7" in board["main"]["prompt"] and board["main"]["cards"][0]["prompt"] is None and board["dev"]["prompt"] is None
    assert "colcopy" in fp.page()


def test_testing_means_the_landing_or_a_live_test_run_in_the_worktree():
    trees = [{"issue": 12, "folder": "12-a"}, {"issue": 13, "folder": "13-b"}]
    runs = [{"checkout": "12-a", "live": True}, {"checkout": "13-b", "live": False}]
    assert fp.testing_issues(trees, runs, 99) == {12, 99}
    assert fp.testing_issues(trees, [], None) == set()


def test_the_roster_lists_defined_planned_and_built_in_subagents_with_what_runs_now():
    sessions = [{"name": "webshop", "subagents": [{"type": "Explore", "description": "find the guard", "state": "working"},
                                                  {"type": "web-fetch", "description": "read a page", "state": "done"}]}]
    rows = {r["name"]: r for r in fp.roster([{"name": "Coder", "description": "writes code", "model": "sonnet"}], sessions)}
    assert rows["Coder"]["kind"] == "defined"                                   # a defined role replaces the planned one
    assert rows["Reviewer"]["kind"] == "planned"
    assert rows["Explore"]["running"][0]["description"] == "find the guard" and rows["web-fetch"]["runs"] == 1
    order = [r["name"] for r in fp.roster([], sessions)]
    assert order[0] == "Explore"                                                # what runs now comes first
    assert fp.frontmatter("---\nname: Coder\nmodel: sonnet\n---\nbody") == {"name": "Coder", "model": "sonnet"}
    # each card says where the subagent is defined: Claude Code reads .claude/agents/<name>.md; built-in ones have no file in the repo
    assert rows["Coder"]["file"] == ".claude/agents/Coder.md"
    assert rows["Reviewer"]["file"] == "planned: .claude/agents/reviewer.md"
    assert "built into Claude Code" in rows["Explore"]["file"]


def test_a_plugin_agent_is_one_roster_row_with_its_role_and_the_page_gives_it_the_look_of_that_role(tmp_path, monkeypatch):
    """`saani:coder` and `saani:reviewer` were two grey figures with an "o" (#42): the roster listed them as built-in next to a planned Coder and
    Reviewer, and the page looked the look up by the exact name. Now the plugin's agents are defined roles, their runs count on the role's row,
    and the page strips the plugin prefix."""
    (tmp_path / "coder.md").write_text("---\nname: coder\ndescription: writes the change\nmodel: sonnet\n---\nbody", encoding="utf-8")
    (tmp_path / "reviewer.md").write_text("---\nname: reviewer\nmodel: opus\n---\nbody", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(fp, "cfg", lambda: type("Cfg", (), {"root": project})())          # no .claude/agents in this project
    defined = fp.defined_agents(tmp_path)
    assert [(d["name"], d["file"]) for d in defined] == [("coder", "the saani plugin: agents/coder.md"), ("reviewer", "the saani plugin: agents/reviewer.md")]
    sessions = [{"name": "saani", "subagents": [{"type": "saani:coder", "description": "#3 guard", "state": "working"},
                                                {"type": "saani:coder", "description": "#33 comments", "state": "working"},
                                                {"type": "saani:reviewer", "description": "Review #33", "state": "done"}]}]
    rows = {r["name"]: r for r in fp.roster(defined, sessions)}
    assert "saani:coder" not in rows and "saani:reviewer" not in rows and "Coder" not in rows and "Reviewer" not in rows
    assert rows["coder"]["kind"] == "defined" and rows["coder"]["runs"] == 2 and len(rows["coder"]["running"]) == 2
    assert rows["reviewer"]["runs"] == 1 and rows["reviewer"]["running"] == []
    assert rows["Tester"]["kind"] == "planned"                                 # a role nobody defined yet stays planned
    assert fp.agent_key("saani:Coder") == fp.agent_key("coder") == "coder"
    page = fp.MAP_FILE.read_text(encoding="utf-8")
    assert 'String(name || "").split(":").pop().toLowerCase()' in page and "ROLE[roleOf(w.role)]" in page


def test_two_running_subagents_of_one_role_keep_both_runs_with_their_issues_and_the_map_draws_a_figure_each():
    """#53: the map showed one Coder on the last run's issue. The server keeps every run, with its description and its issue; the page makes
    one figure per run and relies on `issue`, `description` and the pull request's number, title, url and ci."""
    sessions = [{"name": "webshop", "subagents": [{"type": "coder", "description": "#3 guard", "state": "working"},
                                                  {"type": "coder", "description": "#33 comments", "state": "working"},
                                                  {"type": "coder", "description": "tidy up", "state": "working"},
                                                  {"type": "coder", "description": "#9 old", "state": "done"}]}]
    row = {r["name"]: r for r in fp.roster([{"name": "coder", "description": "writes code"}], sessions)}["coder"]
    assert [(x["description"], x["issue"]) for x in row["running"]] == [("#3 guard", 3), ("#33 comments", 33), ("tidy up", None)]
    assert row["runs"] == 4 and all(x["session"] == "webshop" and x["state"] == "working" for x in row["running"])
    pr = fp.pr_row({"number": 7, "title": "dev into main", "url": "https://example.test/pull/7", "headRefOid": "x", "statusCheckRollup": []}, {})
    assert {"number", "title", "url", "ci"} <= set(pr) and {"state", "text"} <= set(pr["ci"])
    page = fp.MAP_FILE.read_text(encoding="utf-8")
    assert "running.forEach((x, k) => {" in page and "function markLive(" in page and "All ${w.name} runs" in page and "function prPanel()" in page
    assert json.loads(fp.MOCK_FILE.read_text(encoding="utf-8"))["cards"]                  # the map's fallback sample still loads


def test_the_columns_have_short_names_and_their_explanations_are_tooltips():
    """Ready, Building, Testing, Ready to land, Merged on dev, To deploy, To close, Deployed; the explanations as tooltips."""
    assert [t for _, t, _ in fp.COLUMNS] == ["Waiting for you", "Ready", "Building", "Testing", "Ready to land", "Merged", "To deploy", "To close", "Live checks", "Deployed"]
    assert "Last 7 days" in fp.COLUMNS[-1][2]
    assert '"data-tip":c.hint' in fp.page() and "content:attr(data-tip)" in fp.page()


def test_the_pc_sample_reads_cpu_memory_disk_and_the_heaviest_programs():
    raw = json.dumps({"cpu": 21, "free": 15286968, "total": 33229680, "procs": [{"n": "python", "c": 9, "m": 2100}, {"n": "Weird", "c": 1, "m": 5}]})
    r = fp.parse_resources(raw, 1000e9, 400e9, 16)
    assert r["cpu"] == 21 and r["cores"] == 16 and r["mem_total"] == 31.7 and r["mem_used"] == 17.1
    assert r["disk_free"] == 400 and r["top"][0] == {"name": "Python · tests, scripts, this page", "count": 9, "mb": 2100}
    assert r["top"][1]["name"] == "Weird"
    assert fp.parse_resources(json.dumps({"cpu": 5, "free": 1, "total": 2, "procs": {"n": "code", "c": 1, "m": 9}}), 1, 1, 1)["top"][0]["name"] == "VS Code"


def test_an_agent_goes_on_the_card_its_branch_folder_or_last_worktree_names():
    trees = [{"issue": 523, "path": "C:\\x\\webshop-wt\\523-page"}]
    assert fp.agent_issue({"branch": "feat/411-powers"}, trees) == 411
    assert fp.agent_issue({"branch": "dev", "folder": "c:/x/webshop-wt/523-page/"}, trees) == 523
    assert fp.agent_issue({"branch": "main", "folder": "c:/x/webshop", "focus": 517}, trees) == 517
    assert fp.agent_issue({"branch": "auto/2026-11-21_2300", "folder": "c:/x/webshop-auto"}, trees) is None


def test_a_session_keeps_only_the_issue_numbers_of_the_worktrees_its_commands_name():
    lines = [json.dumps({"type": "assistant", "message": {"content": "cd /c/x/webshop-wt/517-board && secret words"}}),
             json.dumps({"type": "assistant", "requestedModel": "claude-opus-5-5", "message": {"content": "C:\\\\x\\\\webshop-wt\\\\523-page"}})]
    meta = fp.session_meta(lines)
    assert meta["focus"] == 523 and meta["model"] == "claude-opus-5-5"
    assert "secret" not in json.dumps(meta)


def test_the_board_shows_each_issue_once_with_its_worktree_agents_and_runs():
    issues = [{"number": 523, "title": "page", "labels": ["ui"], "state": "OPEN"}, {"number": 523, "title": "dup", "labels": [], "state": "OPEN"},
              {"number": 9, "title": "old", "labels": [], "state": "CLOSED"}]
    trees = [{"issue": 523, "path": "C:/wt/523-page", "folder": "523-page", "branch": "task/523-page", "ahead": 1, "dirty": 0},
             {"issue": 600, "path": "C:/wt/600-new-thing", "folder": "600-new-thing", "branch": "task/600-new-thing", "ahead": 0, "dirty": 1}]
    sessions = [{"branch": "main", "folder": "C:/x/webshop", "focus": 523, "subagents": []}]
    runs = [{"checkout": "523-page", "line": "Tests [###] 50%"}]
    board = {c["id"]: c["cards"] for c in fp.build_board(issues, trees, set(), sessions, runs)}
    assert [c["id"] for c in fp.build_board([], [], set(), [], [])] == [k for k, _, _ in fp.COLUMNS]
    building_cards = {c["number"]: c for c in board["building"]}
    assert set(building_cards) == {523, 600} and building_cards[523]["title"] == "page"
    assert building_cards[523]["agents"] and building_cards[523]["runs"] == runs
    assert building_cards[600]["title"] == "new thing"                               # a worktree whose issue gh did not list
    assert [c["number"] for c in board["live"]] == [9]


def test_every_open_issue_is_on_the_board_assigned_to_the_owner_or_not(monkeypatch):
    """The Backlog (the Ready column) counted only the issues assigned to the owner: the unassigned open ones never appeared (#27)."""
    def listed(*filters):
        if "--assignee" in filters:
            return []                                      # nobody is assigned
        if "closed" in filters:
            return [{"number": 9, "title": "old", "labels": [], "state": "CLOSED"}]
        return [{"number": n, "title": f"t{n}", "labels": ["idea"] if n == 4 else [], "state": "OPEN"} for n in (1, 2, 3, 4)]
    monkeypatch.setattr(fp, "gh_issues", listed)
    board = {c["id"]: [i["number"] for i in c["cards"]] for c in fp.build_board(fp.issues()["board"], [], set(), [], [])}
    assert sorted(board["ready"]) == [1, 2, 3]            # none of them assigned; the idea stays off the board
    assert board["live"] == [9]


def test_the_column_of_closed_tasks_starts_folded():
    """the columns showing closed tasks are folded by default; a click on the header unfolds them."""
    closed = [key for key, _, _ in fp.COLUMNS if key == "live"]
    assert closed and 'FOLDED_BY_DEFAULT=["live","citadel"]' in fp.page()          # the board's closed column, and the kanban's Citadel
    assert "aria-expanded" in fp.page()


def test_the_board_has_a_search_field_reachable_with_the_slash_key():
    """a search field over the cards."""
    assert 'id="q" type="search"' in fp.page() and 'e.key==="/"' in fp.page() and "function matches(c)" in fp.page()


def test_the_page_answers_only_its_own_address_and_only_reads(monkeypatch):
    monkeypatch.setattr(fp, "state", lambda: {"time": "now"})
    server = fp.ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert "<title>Saani</title>" in urllib.request.urlopen(base + "/").read().decode()
        assert json.loads(urllib.request.urlopen(base + "/api/state").read()) == {"time": "now"}
        rebound = urllib.request.Request(base + "/api/state", headers={"Host": f"evil.example:{port}"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(rebound)
        assert e.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base + "/api/state", data=b"x", method="POST"))
        assert e.value.code == 501
    finally:
        server.shutdown()
    assert server.server_address[0] == "127.0.0.1"


def test_cards_sort_by_milestone_then_number():
    """each column by milestone (ascending), then id (ascending); the version sits in the card's top right corner."""
    issues = [{"number": n, "title": "t", "labels": [], "state": "OPEN", "milestone": m, "assignees": ["x"]}
              for n, m in ((9, "v1.1.0"), (5, None), (30, "v0.10.0"), (4, "v0.2.0"), (2, "v0.2.0"))]
    cols = fp.build_board(issues, [], set(), [], [])
    cards = [c["number"] for col in cols for c in col["cards"]]
    assert cards == [2, 4, 30, 9, 5]                                            # v0.10.0 after v0.2.0, no milestone last
    assert 'el("div",{class:"num"},link(c.number),c.milestone?el("span",{class:"chip ms"}' in fp.page()


def test_the_pull_request_ci_is_none_running_green_red_or_to_run_again():
    """see that a dev -> main pull request exists, that its CI runs, and whether it is green or red."""
    status = lambda st, why="": {"__typename": "StatusContext", "context": "saani/ci", "state": st, "description": why}
    assert fp.pr_ci([], "abc")["state"] == "none"
    assert fp.pr_ci([status("PENDING", "running on the owner's PC")], "abc") == {"state": "running", "text": "saani/ci: running on the owner's PC"}
    assert fp.pr_ci([status("SUCCESS")], "abc")["state"] == "green"
    assert fp.pr_ci([status("FAILURE")], "abc")["state"] == "red" and fp.pr_ci([status("ERROR")], "abc")["state"] == "red"
    run = {"__typename": "CheckRun", "name": "tests", "status": "IN_PROGRESS", "conclusion": None}
    assert fp.pr_ci([status("SUCCESS"), run], "abc")["state"] == "running"            # one check still running: not green yet
    assert fp.pr_ci([status("SUCCESS"), {**run, "status": "COMPLETED", "conclusion": "FAILURE"}], "abc")["state"] == "red"
    old = fp.pr_ci([], "new1234", {"sha": "old9876543", "result": "passed"})          # an old green is not the current one
    assert old["state"] == "stale" and "old9876" in old["text"]
    counted = fp.pr_ci([], "c" * 40, {"sha": "a" * 40, "result": "passed"}, ["a" * 40, "b" * 40, "c" * 40])
    assert "2 commits came after it" in counted["text"] and "ccccccc" in counted["text"]
    assert fp.pr_ci([], "same", {"sha": "same", "result": "passed"})["state"] == "none"


def test_the_pull_request_shows_what_it_carries_grouped_by_issue():
    commits = [{"oid": "a" * 40, "messageHeadline": "Board: search (refs #523)"},
               {"oid": "b" * 40, "messageHeadline": "Merge branch 'dev'"},
               {"oid": "c" * 40, "messageHeadline": "Generated files rebuilt at landing (refs #523)"},
               {"oid": "d" * 40, "messageHeadline": "Weekly rate", "messageBody": "refs #538"},
               {"oid": "e" * 40, "messageHeadline": "Board: column button (refs #523)"},
               {"oid": "f" * 40, "messageHeadline": "Typo"}]
    got = fp.pr_contents(commits, {523: "Board page"})
    assert [g["issue"] for g in got] == [538, 523, None]
    assert got[1]["title"] == "Board page" and [c["sha"] for c in got[1]["commits"]] == ["aaaaaaa", "eeeeeee"]
    row = fp.pr_row({"number": 539, "title": "dev into main", "headRefOid": "x", "commits": commits, "statusCheckRollup": []}, {})
    assert row["commits"] == 6 and row["ci"]["state"] == "none"
    assert "function prCard(c,hits)" in fp.page() and 'id="pr"' not in fp.page()


def test_the_pull_request_shows_green_yellow_or_red_and_only_its_tasks():
    """the status in three colours, no text in the card but the list of tasks."""
    assert 'CI_COLOR={green:"green",red:"red",running:"yellow",stale:"yellow",none:"yellow"}' in fp.page()
    assert "Commits by issue" not in fp.page() and "prtitle" not in fp.page()


def test_the_top_left_shows_the_architect_sessions():
    """the Claude Code sessions in the top left, in place of the explanations."""
    top = fp.page()[fp.page().index('<div class="top">'):fp.page().index('<div class="slab pc"')]
    assert 'id="crew"' in top and "How to work" not in fp.page() and fp.page().count('id="crew"') == 1


def test_the_pull_request_is_a_task_of_merged_on_dev_wrapping_the_cards_it_carries():
    """the pull request is a task in Merged on dev that wraps the relevant tasks, not a banner."""
    issues = [{"number": n, "title": "t", "labels": ["in-dev"], "state": "OPEN"} for n in (530, 538, 540)]
    pr = fp.pr_row({"number": 539, "title": "dev into main", "headRefOid": "x", "statusCheckRollup": [],
                    "commits": [{"oid": "a" * 40, "messageHeadline": "x (refs #530)"}, {"oid": "b" * 40, "messageHeadline": "y (refs #538)"},
                                {"oid": "c" * 40, "messageHeadline": "z (refs #523)"}]}, {})
    cols = {c["id"]: c for c in fp.build_board(issues, [], set(), [], [], pr)}
    assert cols["dev"]["pr"]["number"] == 539 and cols["dev"]["pr"]["inside"] == [530, 538]    # #540 joined without a commit in it
    assert all(c["pr"] is None for k, c in cols.items() if k != "dev")
    assert fp.build_board(issues, [], set(), [], [], None)[4]["pr"] is None


def test_merged_on_dev_prompt_follows_the_pull_request():
    pr = lambda st: {"number": 539, "ci": {"state": st}}
    assert "saani ci pr" in fp.column_prompt("dev", [1], pr("none")) and "saani ci pr" in fp.column_prompt("dev", [1], pr("stale"))
    assert "is green" in fp.column_prompt("dev", [1], pr("green")) and "is red" in fp.column_prompt("dev", [1], pr("red"))
    assert "going" in fp.column_prompt("dev", [1], pr("running"))
    assert "Prepare the dev into main pull request" in fp.column_prompt("dev", [1], None)


def test_a_chat_that_answered_is_idle_not_working():
    """A working agent once showed while nothing was in progress: the chat had answered and waited for the owner."""
    row = lambda kind, **m: json.dumps({"type": kind, "timestamp": "t", "cwd": "C:/x/webshop", "message": m})
    ended = fp.session_meta([row("user", content="do it"), row("assistant", stop_reason="end_turn", content=[{"type": "text", "text": "done"}])])
    busy = fp.session_meta([row("user", content="do it"), row("assistant", stop_reason="tool_use", content=[{"type": "tool_use"}])])
    result = fp.session_meta([row("assistant", stop_reason="tool_use"), row("user", content=[{"type": "tool_result"}])])
    stopped = fp.session_meta([row("assistant", stop_reason="tool_use"), row("user", content=[{"type": "text", "text": "[Request interrupted by user]"}])])
    assert ended["turn_open"] is False and busy["turn_open"] is True and result["turn_open"] is True and stopped["turn_open"] is False
    assert fp.activity(10, False) == "idle" and fp.activity(10, True) == "working" and fp.activity(600, True) == "working"
    assert fp.activity(3 * 3600, True) == "asleep" and fp.activity(10, None) == "working"     # old files without the field: by age


def test_the_page_is_read_from_its_html_file_at_every_request(tmp_path, monkeypatch):
    """adjusting the page was too slow; its look lives in saani/status.html, shown at the next refresh."""
    f = tmp_path / "status.html"
    f.write_text("<p>__REPO__ one</p>", encoding="utf-8")
    monkeypatch.setattr(fp, "PAGE_FILE", f)
    assert fp.page() == f"<p>{fp.cfg().repo} one</p>"
    f.write_text("<p>two</p>", encoding="utf-8")
    assert fp.page() == "<p>two</p>"                                          # no restart needed
    assert (fp.HERE / "status.html").read_text(encoding="utf-8").startswith("<!doctype html>")


def test_a_refresh_button_asks_everything_again_and_the_pull_request_tasks_start_folded(monkeypatch):
    """a refresh button, and the tasks inside a pull request folded by default."""
    assert 'id="refresh"' in fp.page() and "every 5 s" in fp.page() and 'stateUrl(fresh)' in fp.page() and '"fresh=1"' in fp.page()
    assert 'localStorage.getItem("saani.pr.kids")==="open"' in fp.page()               # folded unless the owner unfolded them
    monkeypatch.setattr(fp, "state", lambda: {"time": "now"})
    fp._cache["issues"] = (0, "old")
    server = fp.ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    port = server.server_address[1]
    fp.threading.Thread(target=server.handle_request, daemon=True).start()
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state?fresh=1", timeout=10) as r:
        assert json.loads(r.read()) == {"time": "now"}
    server.server_close()
    assert "issues" not in fp._cache


STEPS = {"backlog", "todo", "you", "started", "building", "testing", "tested", "landing", "dev", "pr", "deploy", "close", "checks", "done"}


def test_the_board_is_served_with_its_skeleton_and_its_sample(monkeypatch):
    """/map is saani/map.html inside a page skeleton (the file has none: the artifact host adds it), and the sample data
    it falls back on is served beside it."""
    monkeypatch.setattr(fp, "state", lambda: {"time": "now"})
    server = fp.ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        html = urllib.request.urlopen(base + "/board").read().decode()
        assert html.startswith("<!doctype html>") and "<title>Saani</title>" in html and html.endswith("</body></html>")
        assert json.loads(urllib.request.urlopen(base + "/mock-state.json").read())["cards"]
    finally:
        server.shutdown()
    source = fp.MAP_FILE.read_text(encoding="utf-8")
    assert "<html" not in source and "<body" not in source and "<!doctype" not in source.lower()
    assert '"/api/state"' in source and "fetch(stateUrl()" in source and 'fetch("mock-state.json"' in source


def test_one_board_shows_the_listed_projects_and_the_selector_picks_one(monkeypatch, tmp_path):
    """one address for every listed project: /api/state?project=owner/name answers for that project, the home one without it,
    and an unlisted one is a 404; a project is listed once, whatever the folder, and one that no longer loads is left out."""
    from saani import config
    monkeypatch.setenv("SAANI_HOME", str(tmp_path / "home"))
    home, other = config.Config(repo="acme/webshop", root=tmp_path / "webshop"), config.Config(repo="acme/api", root=tmp_path / "api")
    monkeypatch.setattr(fp, "_loaded", {str(home.root): home, str(other.root): other})
    monkeypatch.setattr(fp, "_home", home)
    monkeypatch.setattr(config, "_current", home)
    assert fp.register(other.root) == str(other.root)
    fp.register(other.root)
    fp.register(tmp_path / "gone")
    monkeypatch.setattr(fp, "load_project", lambda folder: fp._loaded.get(folder))
    assert [c.repo for c in fp.projects()] == ["acme/webshop", "acme/api"]
    monkeypatch.setattr(fp, "state", lambda: {"repo": fp.cfg().repo})
    server = fp.ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api/state"
    try:
        assert json.loads(urllib.request.urlopen(base).read()) == {"repo": "acme/webshop"}
        assert json.loads(urllib.request.urlopen(base + "?project=acme%2Fapi").read()) == {"repo": "acme/api"}
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "?project=acme%2Fnone")
        assert err.value.code == 404
    finally:
        server.shutdown()
    assert fp.cfg() is home                                   # the settings are put back after every request


def test_a_ready_issue_with_no_agent_waits_in_the_tents_or_the_granary_not_the_workshops():
    """A Ready card waits in the Wayfarers' Tents or the Granary until an agent works on it, then it is in the Workshops (still in the Ready
    column: no worktree yet). The server's resolver says so (`place`); the map draws the place it gets, with no rule of its own (#123)."""
    session = {"branch": "task/2-x", "folder": "C:/x", "focus": None}
    board = {c["id"]: c for c in fp.build_board([listed(1, TODO), listed(2, TODO), listed(3, BACKLOG)], [], set(), [session], [])}
    assert {c["number"]: c["place"] for c in board["ready"]["cards"]} == {1: "todo", 2: "works", 3: "backlog"}
    source = fp.MAP_FILE.read_text(encoding="utf-8")
    assert 'step: c.place === "works" && col.id !== "building" ? "started" : STEP_OF_PLACE[c.place]' in source
    assert "c.queue" not in source.split("function normalize(d) {", 1)[1].split("\n}", 1)[0]


def test_the_sample_shows_every_zone_and_keeps_out_private_text():
    """The sample is synthetic, every step is a known one, and it holds no session text or path."""
    mock = json.loads(fp.MOCK_FILE.read_text(encoding="utf-8"))
    steps = {c["step"] for c in mock["cards"]}
    assert steps <= STEPS and {"backlog", "todo", "you", "started", "testing", "tested", "pr", "deploy", "close", "checks", "done"} <= steps
    assert set(mock["sample"]["staged"]) == {c["number"] for c in mock["cards"] if c.get("staged")}
    raw = json.dumps(mock)
    assert "C:\\Users" not in raw and "/Users/" not in raw and "@" not in raw.replace("@pytest", "")
    assert all(set(s) <= {"session", "name", "branch", "model", "state", "age_min", "issue"} for s in mock["crew"])


def test_the_status_page_hides_the_configured_labels_not_hardcoded_names(monkeypatch):
    """With act = "creator-action" the card shows the badge and no chip: the filter reads state.project.labels, which project_info sends."""
    html = fp.page()
    assert "/^in-|^help wanted$|^owner-action$/" not in html
    assert "LABELS=(s.project&&s.project.labels)||{}" in html and "LABELS.act||\"owner-action\"" in html and "lifecycleLabel(l)" in html
    from saani import config
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", labels={**config.Config().labels, "act": "creator-action"}))
    assert fp.project_info()["labels"]["act"] == "creator-action"


def test_an_issue_on_production_that_touches_nothing_deployed_is_to_close(monkeypatch):
    """[deploy] paths names what a served project deploys; an in-main issue whose commits touch none of it is "To close" with a close
    prompt, the others stay "To deploy"; without paths nothing changes."""
    from saani import config
    i = lambda n: {"number": n, "labels": ["in-main"], "state": "OPEN"}
    assert fp.column_for(i(6), closing={6}) == "close" and fp.column_for(i(7), closing={6}) == "main" and fp.column_for(i(6)) == "main"
    assert fp.touches_deployed(["server/app.py", "docs/a.md"], ("server/",)) and not fp.touches_deployed(["docs/a.md", "tests/t.py"], ("server/",))
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop", deploy_paths=("server/", "web/")))
    files = {6: "docs/a.md\ntests/t.py\n", 7: "docs/a.md\nserver/app.py\n", 8: ""}
    monkeypatch.setattr(fp, "run", lambda *a, **k: files[int(a[a.index("-E") + 1].split("#")[1].rstrip("\b\\"))])
    assert fp.closing_issues([6, 7, 8]) == {6}                                     # no commit found: not claimed to close
    monkeypatch.setattr(config, "_current", config.Config(repo="acme/webshop"))
    assert fp.closing_issues([6, 7]) == set()
    board = {c["id"]: c for c in fp.build_board([i(6)], [], set(), [], [], closing={6})}
    assert [c["number"] for c in board["close"]["cards"]] == [6] and board["main"]["cards"] == []
    assert "Close #6" in board["close"]["prompt"]
    assert fp.cfg().deploy_paths == ()


def test_the_deploy_paths_are_read_from_saani_toml():
    from saani import config
    cfg = config.from_toml({"project": {"repo": "acme/webshop"}, "deploy": {"paths": ["server/", "web/"]}}, "x")
    assert cfg.deploy_paths == ("server/", "web/")


def test_the_board_is_the_default_page_and_the_kanban_has_its_own_address(monkeypatch):
    """/, /map and the old /board are the map, /kanban the kanban page; both pages have the page switch in their top bar."""
    server = fp.ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    get = lambda p: urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}{p}").read().decode()
    try:
        board, kanban = get("/"), get("/kanban")
        assert board == get("/map") == get("/board") == get("/index.html") and board.count('id="demo"') == 1
        assert 'class="pages"' in kanban and 'href="/kanban" aria-current="page"' in kanban and 'href="/">Map</a>' in kanban
        assert 'id="pages"' in board and 'href="/" aria-current="page">Map</a>' in board and 'href="/kanban">Kanban</a>' in board
        assert 'id="refresh"' in board and 'id="refresh"' in kanban and 'id="proj"' in board
        with pytest.raises(urllib.error.HTTPError) as e:
            get("/nowhere")
        assert e.value.code == 404
    finally:
        server.shutdown()
    assert '$("#pages").hidden = false' in fp.MAP_FILE.read_text(encoding="utf-8")     # the artifact build shows the same menu, inert (no server)


def two_projects(monkeypatch, tmp_path):
    from saani import config
    monkeypatch.setenv("SAANI_HOME", str(tmp_path / "home"))
    home, other = config.Config(repo="acme/webshop", root=tmp_path / "webshop"), config.Config(repo="acme/web-api", root=tmp_path / "api")
    monkeypatch.setattr(fp, "_loaded", {str(home.root): home, str(other.root): other})
    monkeypatch.setattr(fp, "load_project", lambda folder: fp._loaded.get(folder))
    monkeypatch.setattr(fp, "_home", home)
    monkeypatch.setattr(config, "_current", home)
    fp.register(other.root)
    return home, other


def test_the_projects_list_is_a_toml_file_read_and_written_by_saani(monkeypatch, tmp_path):
    """~/.saani/projects.toml holds the checkouts' paths (Windows backslashes included); a missing or broken file is an empty list."""
    monkeypatch.setenv("SAANI_HOME", str(tmp_path / "home"))
    assert fp.read_registry() == [] and fp.registry_path().name == "projects.toml"
    bs = chr(92)
    folders = [f"D:{bs}work{bs}webshop", "/work/api", f"D:{bs}wéb{bs}ünï"]
    fp.write_registry(folders)
    assert fp.read_registry() == folders
    assert fp.registry_path().read_text(encoding="utf-8").startswith("# The projects the Saani map offers")
    fp.registry_path().write_text("projects = [oops", encoding="utf-8")
    assert fp.read_registry() == []


def test_a_project_is_found_by_owner_name_or_by_its_slug_and_the_data_stays_apart(monkeypatch, tmp_path):
    """acme-web-api is the address of acme/web-api; each project's issues are cached and counted on their own."""
    home, other = two_projects(monkeypatch, tmp_path)
    assert fp.slug("acme/web-api") == "acme-web-api"
    assert fp.find_project("acme-web-api") is other and fp.find_project("acme/web-api") is other and fp.find_project("acme-none") is None
    assert fp.in_project("acme-web-api", lambda: fp.cfg().repo) == "acme/web-api" and fp.in_project("", lambda: fp.cfg().repo) == "acme/webshop"
    rows = {"acme/webshop": [{"number": 1, "labels": ["help wanted"]}, {"number": 2, "labels": []}],
            "acme/web-api": [{"number": 9, "labels": ["in-dev"]}]}
    monkeypatch.setattr(fp, "issues", lambda: {"open": rows[fp.cfg().repo]})
    fp._cache.clear()
    assert fp.waiting(home) == 1 and fp.waiting(other) == 0
    assert fp.cached("issues", 60, fp.issues) == {"open": rows["acme/webshop"]}      # the current project's, not the last one counted
    assert fp.cfg() is home


def test_the_state_lists_every_project_with_its_slug_and_what_waits_for_the_owner(monkeypatch, tmp_path):
    home, other = two_projects(monkeypatch, tmp_path)
    for name in ("tasks", "agents", "defined_agents"):
        monkeypatch.setattr(fp, name, lambda *a, **k: [])
    for name in ("landing", "pull_request", "resources", "ci"):
        monkeypatch.setattr(fp, name, lambda *a, **k: {})
    monkeypatch.setattr(fp, "tests", lambda: {"runs": [], "tier1": []})
    monkeypatch.setattr(fp, "issues", lambda: {"board": [], "open": [{"number": 1, "title": "t", "labels": ["owner-action"]}], "closed": []})
    fp._cache.clear()
    got = fp.state()["projects"]
    assert [(p["repo"], p["slug"], p["waiting"]) for p in got] == [("acme/webshop", "acme-webshop", 1), ("acme/web-api", "acme-web-api", 1)]


def test_a_second_board_finds_the_running_one(monkeypatch):
    """`saani ui` on a taken port that a board answers on says so instead of failing; a port nothing answers on is not a board."""
    monkeypatch.setattr(fp, "state", lambda: {"board": []})
    server = fp.ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert fp.already_running(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
    assert not fp.already_running(server.server_address[1])


def test_saani_projects_adds_lists_and_removes(monkeypatch, tmp_path, capsys):
    import subprocess
    from saani import config
    monkeypatch.setenv("SAANI_HOME", str(tmp_path / "home"))
    folder = tmp_path / "shop"
    folder.mkdir()
    (folder / "saani.toml").write_text('[project]\nrepo = "acme/shop"\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    monkeypatch.setattr(fp, "_loaded", {})
    monkeypatch.setattr(fp, "_home", None)
    args = lambda what, target=None: type("A", (), {"what": what, "target": target})()
    assert fp.projects_command(args("add", str(folder))) == 0
    assert "listed: acme/shop" in capsys.readouterr().out
    fp.projects_command(args("list"))
    assert "acme/shop" in capsys.readouterr().out
    assert fp.projects_command(args("add", str(tmp_path / "nothing"))) == 1
    assert fp.projects_command(args("remove", "acme-shop")) == 0 and fp.read_registry() == []
    assert fp.projects_command(args("remove")) == 1


def test_both_pages_keep_the_project_in_their_address_and_show_the_waiting_dot():
    for source in (fp.MAP_FILE.read_text(encoding="utf-8"), fp.PAGE_FILE.read_text(encoding="utf-8")):
        flat = source.replace(" ", "")
        assert "location.hash.slice(1)" in source and "history.replaceState(null" in source
        assert 'p.waiting?"●"' in flat.replace("●", "●") or "p.waiting?\"●" in flat
        assert 'localStorage.setItem("saani-project"' in source and "status===404" in flat


GLANCE = {"auto": {"fit": "partly", "why": "the data model yes"}, "blocked_by": {"issues": [13], "date": None, "text": "#13"}}


def test_a_card_carries_what_its_at_a_glance_table_says():
    """the auto fit read from the table (source: table), blocked while a named issue is open and not yet on dev, an epic is an anchor."""
    issue = {"number": 14, "title": "Board data", "labels": [], "state": "OPEN", "glance": GLANCE}
    assert fp.lifecycle(issue, {13, 14}) == {"blocked": True, "anchor": False, "blocked_by": [13],
                                            "auto": {"fit": "partly", "why": "the data model yes", "source": "table"}}
    assert fp.lifecycle(issue, {14})["blocked"] is False
    assert fp.lifecycle({"title": "Saani v1 (epic)"}, set())["anchor"] and fp.lifecycle({"title": "Live checks v0.1"}, set())["anchor"]
    assert "auto" not in fp.lifecycle({"title": "x"}, set())
    blocker = {"number": 13, "title": "b", "labels": ["in-dev"], "state": "OPEN"}                 # landed on dev: it blocks no more
    board = {c["id"]: c for c in fp.build_board([blocker, issue], [], set(), [], [])}
    assert [(c["number"], c["blocked"]) for c in board["dev"]["cards"] + board["ready"]["cards"]] == [(13, False), (14, False)]


def test_the_milestone_strip_counts_and_says_when_a_milestone_is_ready_to_release():
    rows = [{"title": "v0.2.0", "state": "open", "open_issues": 0, "closed_issues": 4}, {"title": "v0.1.0", "state": "open", "open_issues": 2, "closed_issues": 1},
            {"title": "v0.0.9", "state": "closed", "open_issues": 0, "closed_issues": 9}, {"title": "v1.0.0", "state": "open", "open_issues": 0, "closed_issues": 0}]
    got = fp.milestones(rows, [{"number": 30, "title": "Live checks v0.1.0", "milestone": "v0.1.0"}])
    assert [m["title"] for m in got] == ["v0.1.0", "v0.2.0", "v1.0.0"]
    assert got[0] == {"title": "v0.1.0", "open": 2, "closed": 1, "checks": 30, "ready": False}
    assert got[1]["ready"] and not got[2]["ready"]                                                 # nothing done yet is not ready


def test_the_closed_milestones_are_named_for_the_maps_citadel():
    rows = [{"title": "v0.2.0", "state": "open", "open_issues": 0, "closed_issues": 4}, {"title": "v0.0.9", "state": "closed", "open_issues": 0, "closed_issues": 9},
            {"title": "v0.0.8", "state": "closed", "open_issues": 0, "closed_issues": 2}]
    assert fp.closed_milestones(rows) == ["v0.0.9", "v0.0.8"]
    assert [m["title"] for m in fp.milestones(rows, [])] == ["v0.2.0"]                              # the strip still shows the open ones only
    assert fp.closed_milestones([]) == []


def test_the_state_names_the_closed_milestones_from_the_one_milestone_fetch(monkeypatch, tmp_path):
    monkeypatch.setenv("SAANI_HOME", str(tmp_path / "home"))
    listing = {"board": [], "open": [], "closed": []}
    fetches = []
    monkeypatch.setattr(fp, "cached", lambda key, seconds, fn: listing if key == "issues" else fn())
    monkeypatch.setattr(fp, "milestone_rows", lambda: fetches.append(1) or [{"title": "v1.0.0", "state": "closed", "open_issues": 0, "closed_issues": 3},
                                                                           {"title": "v1.1.0", "state": "open", "open_issues": 1, "closed_issues": 0}])
    monkeypatch.setattr(fp, "landed_refs", lambda: set())
    monkeypatch.setattr(fp, "tasks", lambda: [])
    monkeypatch.setattr(fp, "landing", lambda: None)
    monkeypatch.setattr(fp, "agents", lambda: [])
    monkeypatch.setattr(fp, "tests", lambda: {"runs": [], "tier1": []})
    state = fp.state()
    assert state["closed_milestones"] == ["v1.0.0"] and [m["title"] for m in state["milestones"]] == ["v1.1.0"]
    assert len(fetches) == 1


def test_the_state_has_no_closed_milestones_when_github_cannot_be_read(monkeypatch, tmp_path):
    monkeypatch.setenv("SAANI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(fp, "cached", lambda key, seconds, fn: {"board": [], "open": [], "closed": []} if key == "issues" else fn())
    monkeypatch.setattr(fp, "milestone_rows", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(fp, "tasks", lambda: [])
    monkeypatch.setattr(fp, "landing", lambda: None)
    monkeypatch.setattr(fp, "agents", lambda: [])
    monkeypatch.setattr(fp, "tests", lambda: {"runs": [], "tier1": []})
    assert fp.state()["closed_milestones"] == []


def test_the_map_reads_the_closed_milestones_of_the_state():
    page = (fp.HERE / "map.html").read_text(encoding="utf-8")
    assert "DATA.closed_milestones" in page


def test_the_hygiene_strip_lists_only_what_needs_tidying():
    lab = {"in_dev": "in-dev", "in_main": "in-main", "idea": "idea"}
    open_ = [{"number": 1, "labels": [], "milestone": None}, {"number": 2, "labels": ["idea"], "milestone": None},
             {"number": 3, "labels": [], "milestone": "v1"}, {"number": 4, "labels": ["in-dev"], "milestone": "v1"}]
    closed = [{"number": 7, "labels": ["in-main"]}, {"number": 8, "labels": []}]
    assert fp.hygiene(open_, closed, {3, 4}, lab) == [{"kind": "closed_labelled", "issues": [7]}, {"kind": "no_milestone", "issues": [1]},
                                                       {"kind": "landed_unlabelled", "issues": [3]}]
    assert fp.hygiene([], [], set(), lab) == []


def test_an_issues_context_criteria_and_history_are_read_from_its_markdown():
    body = "## Context\n- why\n\n## Acceptance criteria\n- [x] one\n- [ ] two **bold**\n\n## Scope\n- [ ] not a criterion\n"
    comments = [{"createdAt": "2026-11-22T10:00:00Z", "body": "### ✅ On dev\n| Commit |\n|---|"}, {"createdAt": "2026-11-23T10:00:00Z", "body": "just words"}]
    got = fp.issue_detail(body, comments, "https://github.com/acme/webshop/issues/9")
    assert got["summary"] == "- why" and got["url"].endswith("/9")
    assert got["criteria"] == [{"done": True, "text": "one"}, {"done": False, "text": "two **bold**"}]
    assert got["history"] == [{"when": "2026-11-22", "kind": "✅ On dev", "text": "| Commit |\n|---|"}, {"when": "2026-11-23", "kind": "Comment", "text": "just words"}]
    assert fp.issue_detail(None, None, None) == {"summary": "", "criteria": [], "history": [], "url": None}


def test_the_issue_detail_is_served_on_demand_and_the_board_asks_for_it(monkeypatch):
    monkeypatch.setattr(fp, "issue_page", lambda n: {"summary": f"about {n}", "criteria": [], "history": [], "url": None})
    server = fp.ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert json.loads(urllib.request.urlopen(base + "/api/issue?n=14").read())["summary"] == "about 14"
        for bad in ("/api/issue", "/api/issue?n=x"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad)
            assert e.value.code == 404
    finally:
        server.shutdown()
    page = fp.MAP_FILE.read_text(encoding="utf-8")
    assert '"/api/issue?n="' in page and "DETAILS[c.number]" in page and "DATA.milestones" in page and "DATA.hygiene" in page


def test_landing_comments_the_issue_and_labels_it_and_says_so_when_gh_refuses(monkeypatch):
    """the On dev comment (commit, tests, what next) and the in-dev label; a refusal leaves the manual step, never an undone landing."""
    from saani import core
    calls = []
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0})())
    row = {"head": "abc1234", "seconds": 18}
    text = core.landing_comment(row)
    assert text.splitlines()[0] == "### ✅ On dev" and "abc1234" in text and "18 s" in text and "with the next pull request" in text
    assert core.mark_on_dev("14", row) is True
    assert calls[0][:4] == ["gh", "issue", "comment", "14"] and calls[1][:4] == ["gh", "issue", "edit", "14"] and calls[1][-2:] == ["--add-label", "in-dev"]
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 1})())
    assert core.mark_on_dev("14", row) is False

    def missing(cmd, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(core.subprocess, "run", missing)
    assert core.mark_on_dev("14", row) is False


def test_a_card_carries_the_issue_type_github_holds(monkeypatch):
    """`gh issue list --json` has no Type field, so every card showed "No type" although GitHub had one (#38): the Type is read from the REST
    API, for the open issues (all pages) and the recently closed ones; an issue without a Type, or a refused call, has none."""
    calls = []

    def fake_run(*args, **kw):
        calls.append(args)
        if args[1] == "issue":
            return json.dumps([{"number": 1, "title": "a", "labels": [], "state": "OPEN"}, {"number": 2, "title": "b", "labels": [], "state": "OPEN"}])
        return "1\tBug\n3\tTask\n"                          # 2 has no Type; 3 is not in the list
    monkeypatch.setattr(fp, "run", fake_run)
    monkeypatch.setattr(fp, "cfg", lambda: type("Cfg", (), {"repo": "acme/webshop"})())
    rows = fp.gh_issues("--state", "open", "--limit", "300")
    assert [(r["number"], r["type"]) for r in rows] == [(1, "Bug"), (2, None)]
    api = next(c for c in calls if c[1] == "api")
    assert "--paginate" in api and "repos/acme/webshop/issues?state=open&per_page=100" in api
    fp.gh_issues("--state", "closed", "--search", "closed:>=2026-11-15", "--limit", "40")
    api = [c for c in calls if c[1] == "api"][-1]
    url = next(a for a in api if a.startswith("repos/"))
    assert "--paginate" not in api and "state=closed" in url and "since=2026-11-15T00:00:00Z" in url
    monkeypatch.setattr(fp, "run", lambda *a, **k: json.dumps([{"number": 1, "title": "a", "labels": [], "state": "OPEN"}]) if a[1] == "issue" else "")
    assert fp.gh_issues("--state", "open")[0]["type"] is None            # gh refused: no Type, the page falls back on the branch and the labels


def test_the_map_names_the_saani_it_runs_a_release_tag_a_commit_or_the_package_version(tmp_path):
    from saani import __version__
    code = tmp_path / "saani"
    code.mkdir()
    assert fp.saani_version(code, lambda *a: "") == {"label": "v" + __version__, "kind": "version"}      # not a git checkout
    (code / ".git").mkdir()
    answers = {"describe": "v1.0.0", "rev-parse": "abc1234"}
    assert fp.saani_version(code, lambda *a: answers.get(a[1], "")) == {"label": "v1.0.0", "kind": "tag"}
    answers["describe"] = ""                                                                             # between two releases
    assert fp.saani_version(code, lambda *a: answers.get(a[1], "")) == {"label": "abc1234", "kind": "commit"}
    page = (pathlib.Path(fp.__file__).parent / "map.html").read_text(encoding="utf-8")
    assert 'id="legend-ver"' in page and "DATA.saani" in page


# ---------------------------------------------------------------- the Wayfarers' Tents are Backlog, the Granary is Todo (#83)

def ready(issue_list):
    """{number: queue} of the Ready column of a board built from `issue_list`."""
    board = {c["id"]: c for c in fp.build_board(issue_list, [], set(), [], [])}
    return {c["number"]: c.get("queue") for c in board["ready"]["cards"]}


def listed(number, status=None, milestone=None, labels=()):
    return {"number": number, "title": f"t{number}", "labels": list(labels), "state": "OPEN", "milestone": milestone,
            "status": {"name": status[0], "category": status[1]} if status else None}


TODO, BACKLOG = ("Todo", "planned"), ("Backlog", "backlog")


def test_with_statuses_a_ready_card_waits_by_its_github_status():
    assert ready([listed(1, TODO), listed(2, BACKLOG), listed(3)]) == {1: "todo", 2: "backlog", 3: "backlog"}
    assert ready([listed(1, ("In progress", "started")), listed(2, ("In review", "started")), listed(3, TODO)]) == {1: "todo", 2: "todo", 3: "todo"}
    # an open issue marked Done, Not planned or Duplicate is not taken by a run: it waits with the Backlog
    assert ready([listed(1, ("Done", "completed")), listed(2, ("Not planned", "not_planned")), listed(3, ("Duplicate", "duplicate")),
                  listed(4, TODO)]) == {1: "backlog", 2: "backlog", 3: "backlog", 4: "todo"}


def test_with_statuses_the_milestone_does_not_decide():
    assert ready([listed(1, BACKLOG, "v1.0.0"), listed(2, TODO), listed(3, None, "v1.0.0"), listed(4, TODO, "v1.0.0")]) == {
        1: "backlog", 2: "todo", 3: "backlog", 4: "todo"}


def test_without_statuses_the_milestone_decides_as_status_sync_does():
    assert ready([listed(1, None, "v1.0.0"), listed(2), listed(3, None, "Later")]) == {1: "todo", 2: "backlog", 3: "todo"}


def test_a_repo_with_statuses_off_in_saani_toml_is_read_as_without(monkeypatch, cfg):
    issues = [listed(1, BACKLOG, "v1.0.0"), listed(2, TODO)]
    assert ready(issues) == {1: "backlog", 2: "todo"}
    cfg.status_enabled = "off"
    assert ready(issues) == {1: "todo", 2: "backlog"}


def test_a_renamed_status_is_still_read_by_its_category():
    assert ready([listed(1, ("Icebox", "backlog")), listed(2, ("Ready to go", "planned"))]) == {1: "backlog", 2: "todo"}


def test_only_cards_without_a_place_on_this_pc_carry_a_queue():
    issues = [listed(1, TODO), listed(2, ("In review", "started"), labels=["in-dev"]), listed(3, TODO, labels=["help wanted"])]
    board = {c["id"]: c for c in fp.build_board(issues, [], set(), [], [])}
    assert [c["number"] for c in board["ready"]["cards"]] == [1] and [c["number"] for c in board["you"]["cards"]] == [3]
    assert board["ready"]["cards"][0]["queue"] == "todo" and board["you"]["cards"][0]["queue"] == "todo"
    assert all("queue" not in c for c in board["dev"]["cards"])


def test_the_map_names_the_zones_and_their_subtitles_after_the_statuses():
    source = fp.MAP_FILE.read_text(encoding="utf-8")
    assert '{id: "backlog", stage: "Backlog",' in source and "name: \"Wayfarers' Tents\"" in source
    assert '{id: "todo",    cmd: "/saani:start <issue>", stage: "Todo",' in source and 'name: "The Granary"' in source
    assert 'id: "inbox"' not in source and "Inbox" not in source and "stage: \"Backlog\",         name: \"The Granary\"" not in source


def test_the_old_step_ids_are_gone_from_the_map_the_contract_and_the_sample():
    source = fp.MAP_FILE.read_text(encoding="utf-8")
    assert 'case "inbox"' not in source and "inbox:" not in source
    mock = json.loads(fp.MOCK_FILE.read_text(encoding="utf-8"))
    assert "inbox" not in {c["step"] for c in mock["cards"]}
    # the sample shows both zones, with the statuses that put a card there
    by_step = {step: {(c.get("status") or {}).get("name") for c in mock["cards"] if c["step"] == step} for step in ("backlog", "todo")}
    assert by_step["todo"] == {"Todo"} and by_step["backlog"] <= {None, "Backlog"} and by_step["backlog"]
    contract = (fp.MAP_FILE.parent.parent / "docs" / "map-data-contract.md").read_text(encoding="utf-8")
    assert "`backlog` (the Wayfarers' Tents" in contract and "`todo` (the Granary" in contract and "`queue`" in contract


# ---------------------------------------------------------------- the kanban: one column per place of the map (#118)

PROGRESS, REVIEW, DONE_ST = ("In progress", "started"), ("In review", "started"), ("Done", "completed")


def tree(n, ahead=1):
    return {"issue": n, "branch": f"feat/{n}-x", "folder": f"C:/wt/{n}-x", "ahead": ahead, "dirty": 0}


def kanban(issues, trees=(), testing=(), pr=None, landing=None, closed=(), left_out=()):
    """{column id: [(number, section)]} of the kanban built from `issues` (the open ones, then the `closed` ones)."""
    board = fp.build_board(list(issues) + list(closed), list(trees), set(testing), [], [], pr)
    built = fp.build_kanban(board, landing, left_out)
    return built, {c["id"]: sorted((x["number"], x["section"]) for x in c["cards"]) for c in built["columns"]}


def closed_issue(n, status):
    return {**listed(n, status), "state": "CLOSED"}


PLACES = ["backlog", "todo", "you", "works", "proving", "yard", "depot", "tower", "pad", "archive", "outpost", "citadel"]


def map_places():
    """[(id, name)] of the map's places in flow order, from ZONES in map.html."""
    block = fp.MAP_FILE.read_text(encoding="utf-8").split("const ZONES = [", 1)[1].split("\n];", 1)[0]
    return re.findall(r'\{id: "(\w+)",.*?name: "([^"]+)"', block)


def test_the_columns_are_the_maps_places_in_order():
    built, _ = kanban([listed(1, TODO)])
    assert [c["id"] for c in built["columns"]] == PLACES
    assert [c["title"] for c in built["columns"]][:4] == ["Wayfarers' Tents", "The Granary", "Council Rock", "The Workshops"]
    assert all(c["stage"] and c["hint"] for c in built["columns"])


def test_the_columns_are_the_places_of_the_map_that_hold_issues():
    """A place added to or renamed in ZONES shows up here: the Hollow never holds issues, the others each have a column."""
    never = {"camp"}
    zones = [(i, n) for i, n in map_places() if i not in never]
    assert zones == [(c["id"], c["title"]) for c in kanban([listed(1, TODO)])[0]["columns"]]
    assert {i for i, _ in map_places()} == set(PLACES) | never


# ---------------------------------------------------------------- the Far Outpost: the live checks of a released milestone (#120)

def outpost_issue(n, title=None, state="OPEN", labels=(), milestone="v1.0"):
    return {"number": n, "title": title or f"t{n}", "labels": list(labels), "state": state, "milestone": milestone, "state_reason": None}


def checks_column(issues, number):
    board = fp.build_board(issues, [], set(), [], [])
    return next((c["id"] for c in board if number in [x["number"] for x in c["cards"]]), None)


def test_the_live_checks_of_a_released_milestone_are_in_the_far_outpost(cfg):
    cfg.deploy_command = []
    checks = outpost_issue(9, "Live checks v1.0")
    assert checks_column([outpost_issue(1, state="CLOSED"), outpost_issue(2, labels=["in-main"]), checks], 9) == "checks"
    assert checks_column([outpost_issue(1, state="CLOSED"), checks], 9) == "checks"
    assert checks_column([outpost_issue(1), checks], 9) == "ready"                              # an open issue: not released yet
    assert checks_column([outpost_issue(1, labels=["in-dev"]), checks], 9) == "ready"           # on dev only: not released
    assert checks_column([outpost_issue(1, labels=["in-dev"]), outpost_issue(9, "Live checks v1.0", labels=["in-dev"])], 9) == "dev"


def test_a_closed_in_main_issue_holds_the_live_checks_back_only_with_a_deploy_command(cfg):
    waiting = outpost_issue(1, state="CLOSED", labels=["in-main"])
    checks = outpost_issue(9, "live checks v1.0")                                              # any case
    cfg.deploy_command = ["deploy.sh"]
    assert checks_column([waiting, checks], 9) == "ready"                                      # still To deploy
    assert checks_column([{**waiting, "state_reason": "NOT_PLANNED"}, checks], 9) == "checks"  # never deployed
    assert checks_column([{**waiting, "labels": []}, checks], 9) == "checks"                   # deployed
    cfg.deploy_command = []
    assert checks_column([waiting, checks], 9) == "checks"                                     # nothing would remove in-main


def test_only_an_open_live_checks_issue_with_a_milestone_goes_to_the_far_outpost(cfg):
    cfg.deploy_command = []
    done = outpost_issue(1, state="CLOSED")
    assert checks_column([done, outpost_issue(9, "Live checks v1.0", state="CLOSED")], 9) == "live"      # closed: the Citadel
    assert checks_column([done, outpost_issue(9, "Live checks v1.0", labels=["idea"])], 9) is None        # an idea stays off
    assert checks_column([done, outpost_issue(9, "Live checks v1.0", milestone=None)], 9) == "ready"       # no milestone
    assert checks_column([done, outpost_issue(9, "Checks of v1.0")], 9) == "ready"                         # another title
    assert checks_column([done, outpost_issue(9, "Live checks v1.0", milestone="v2.0")], 9) == "ready"     # another milestone, nothing in it
    assert checks_column([outpost_issue(2, milestone="v2.0"), done, outpost_issue(9, "Live checks v1.0")], 9) == "checks"   # another milestone's open issue


def test_a_waiting_for_you_label_does_not_keep_the_live_checks_out_of_the_far_outpost(cfg):
    cfg.deploy_command = []
    for label in ("help wanted", "owner-action"):
        assert checks_column([outpost_issue(1, state="CLOSED"), outpost_issue(9, "Live checks v1.0", labels=[label])], 9) == "checks"


def test_an_open_idea_issue_of_the_milestone_holds_the_release_back(cfg):
    cfg.deploy_command = []
    issues = [outpost_issue(1, state="CLOSED"), outpost_issue(2, labels=["idea"]), outpost_issue(9, "Live checks v1.0")]
    assert checks_column(issues, 9) == "ready"


def test_the_closed_issues_of_a_milestone_with_live_checks_are_fetched_whatever_their_age(cfg, monkeypatch):
    """All the milestone's issues closed 8+ days ago: the release is still seen; a milestone with only its Live checks issue is not released."""
    cfg.deploy_command = []
    calls = []

    def fake_gh_issues(*f):
        calls.append(f)
        if "--milestone" in f:
            return [outpost_issue(1, state="CLOSED")] if f[f.index("--milestone") + 1] == "v1.0" else []
        if "open" in f:
            return [outpost_issue(9, "Live checks v1.0"), outpost_issue(19, "Live checks v2.0", milestone="v2.0")]
        return []
    monkeypatch.setattr(fp, "gh_issues", fake_gh_issues)
    got = fp.issues()
    assert [i["number"] for i in got["settled"]] == [1] and not [i for i in got["closed"] if i["number"] == 1]    # not placed on the board
    assert sum("--milestone" in c for c in calls) == 2
    board = fp.build_board(got["board"], [], set(), [], [], None, (), got["settled"])
    placed = {c["id"]: [x["number"] for x in c["cards"]] for c in board}
    assert placed["checks"] == [9] and placed["ready"] == [19] and placed["live"] == []


def test_the_kanban_puts_the_live_checks_in_the_far_outpost_column(cfg):
    cfg.deploy_command = []
    _, got = kanban([outpost_issue(1, state="CLOSED"), outpost_issue(9, "Live checks v1.0")])
    assert got["outpost"] == [(9, None)] and got["citadel"] == [(1, None)] and got["todo"] == []


def test_each_status_puts_its_issue_in_its_place():
    _, got = kanban([listed(1, BACKLOG), listed(2), listed(3, TODO), listed(4, ("Done", "completed"))])
    assert got["backlog"] == [(1, None), (2, None), (4, None)] and got["todo"] == [(3, None)]     # an open issue marked Done is not taken: Backlog
    assert all(got[p] == [] for p in PLACES if p not in ("backlog", "todo"))


def test_a_worktree_puts_its_issue_in_the_workshops_whatever_its_status():
    issues = [listed(1, TODO), listed(2, PROGRESS), listed(3, REVIEW), listed(4, BACKLOG)]
    _, got = kanban(issues, trees=[tree(1), tree(2), tree(3)])
    assert got["works"] == [(1, None), (2, None), (3, None)]
    assert got["backlog"] == [(4, None)] and got["todo"] == []


def test_a_clean_worktree_with_green_landing_tests_is_ready_to_land():
    """Tested and committed but not landed: its own column (the Loading Yard on the map), never mixed with work still being written (#108)."""
    issue = listed(1, PROGRESS)
    green = {**tree(1), "green": True}
    assert fp.column_for(issue, green) == "tested"
    assert fp.column_for(issue, {**green, "dirty": 2}) == "building"             # an edit since the green run: back to the Workshops
    assert fp.column_for(issue, {**green, "ahead": 0}) == "building"             # nothing to land
    assert fp.column_for(issue, tree(1)) == "building"                           # no green result for this tree
    assert fp.column_for(issue, green, testing={1}) == "testing"                 # a live run wins
    assert fp.prompt_for(1, "tested").startswith("Land #1")


def test_a_ready_to_land_card_is_in_the_loading_yard_with_a_badge():
    built, got = kanban([listed(1, PROGRESS), listed(2, REVIEW)], trees=[{**tree(1), "green": True}, {**tree(2), "green": True}])
    assert got["yard"] == [(1, None), (2, None)] and got["works"] == []
    cards = [c for col in built["columns"] for c in col["cards"]]
    assert all({"text": "ready to land", "kind": "info"} in c["badges"] for c in cards)


def test_landing_green_reads_the_queue_result_of_the_worktree_tree(monkeypatch, tmp_path):
    from saani import queue
    command = ["python", "-m", "pytest", "-q"]
    cfg = type("Cfg", (), {"landing": command, "command": lambda self, name: list(command)})()
    monkeypatch.setattr(fp, "cfg", lambda: cfg)
    folder = tmp_path / queue.QUEUE_DIR
    folder.mkdir()
    queue.store_result(folder, {"kind": "land", "command": command, "id": "j1"}, "tree-a", 0)     # stored by saani land, kind landing
    git = {"status": "", "rev-parse HEAD^{tree}": "tree-a", "rev-parse --git-common-dir": str(tmp_path)}
    def run(*args, cwd=None, timeout=60):
        return git["status"] if args[1] == "--no-optional-locks" else git[" ".join(args[1:])]
    monkeypatch.setattr(fp, "run", run)
    assert fp.landing_green("C:/wt/1-x") is True
    git["status"] = "?? new.txt"
    assert fp.landing_green("C:/wt/1-x") is False                                # untracked files count: not the tested tree
    git["status"], git["rev-parse HEAD^{tree}"] = "", "tree-b"
    assert fp.landing_green("C:/wt/1-x") is False                                # a new commit
    git["rev-parse HEAD^{tree}"], cfg.landing = "tree-a", []
    assert fp.landing_green("C:/wt/1-x") is False                                # no landing tests set: nothing to know


def test_dev_and_production_have_their_own_places_and_prompts():
    on_dev = listed(1, REVIEW, labels=["in-dev"])
    # the status lags behind the label: the status is trusted before the labels by default (#123, the owner's decision of 2026-11-28),
    # so the issue waits in the Granary, probable (its label contradicts it); a project that trusts the labels first keeps it on dev
    stale = listed(2, TODO, labels=["in-dev"])
    to_deploy, to_close = listed(3, REVIEW, labels=["in-main"]), listed(4, REVIEW, labels=["in-main"])
    board = fp.build_board([on_dev, stale, to_deploy, to_close], [], set(), [], [], None, closing={4})
    built = fp.build_kanban(board)
    cols = {c["id"]: c for c in built["columns"]}
    assert [c["number"] for c in cols["depot"]["cards"]] == [1] and [c["number"] for c in cols["pad"]["cards"]] == [3]
    assert [(c["number"], c["confidence"], c["contradict"]) for c in cols["todo"]["cards"]] == [(2, "probable", ["labels"])]
    fp.cfg().trust = ("labels", "status", "git")
    labels_first = {c["id"]: c for c in fp.build_kanban(fp.build_board([on_dev, stale], [], set(), [], []))["columns"]}
    assert [(c["number"], c["confidence"]) for c in labels_first["depot"]["cards"]] == [(1, "sure"), (2, "probable")]
    assert [c["number"] for c in cols["archive"]["cards"]] == [4]
    assert all(cols[p]["prompt"] for p in ("depot", "pad", "archive"))          # one prompt per place, as the Merged / To deploy / To close columns had
    assert not cols["tower"]["prompt"] and cols["tower"]["pr"] is None


def test_the_release_pull_request_is_the_refinery_with_the_issues_it_carries():
    issues = [listed(n, REVIEW, labels=["in-dev"]) for n in (530, 538, 540)]
    pr = fp.pr_row({"number": 539, "title": "dev into main", "headRefOid": "x", "statusCheckRollup": [],
                    "commits": [{"oid": "a" * 40, "messageHeadline": "x (refs #530)"}, {"oid": "b" * 40, "messageHeadline": "y (refs #538)"}]}, {})
    built, got = kanban(issues, pr=pr)
    assert got["tower"] == [(530, None), (538, None)] and got["depot"] == [(540, None)]
    tower = next(c for c in built["columns"] if c["id"] == "tower")
    assert tower["pr"]["number"] == 539 and tower["pr"]["inside"] == [530, 538] and "539" in tower["prompt"]


def test_an_open_release_pull_request_with_no_card_still_shows_in_the_refinery():
    pr = {"number": 7, "url": "u", "title": "t", "ci": {"state": "green"}, "inside": []}
    built, _ = kanban([listed(1, TODO)], pr=None)
    assert all(c["pr"] is None for c in built["columns"])
    board = fp.build_board([listed(1, REVIEW, labels=["in-dev"])], [], set(), [], [], pr)
    cols = {c["id"]: c for c in fp.build_kanban(board)["columns"]}
    assert cols["tower"]["pr"]["number"] == 7 and cols["tower"]["cards"] == [] and [c["number"] for c in cols["depot"]["cards"]] == [1]


def test_closed_issues_are_in_the_citadel_and_not_planned_ones_are_apart():
    built, got = kanban([listed(1, TODO)], closed=[closed_issue(2, DONE_ST), closed_issue(3, ("Not planned", "not_planned")),
                                                   closed_issue(4, ("Duplicate", "duplicate")), closed_issue(5, None)])
    assert got["citadel"] == [(2, None), (3, "notplanned"), (4, "notplanned"), (5, None)] and got["todo"] == [(1, None)]
    citadel = next(c for c in built["columns"] if c["id"] == "citadel")
    assert [(s["id"], s["count"]) for s in citadel["sections"]] == [("notplanned", 2)]


def test_the_citadel_leaves_out_the_done_issues_of_closed_milestones_as_the_map_does():
    done = [{**closed_issue(n, DONE_ST), "milestone": m} for n, m in ((2, "v1.0.0"), (3, "v1.1.0"), (4, None))]
    _, got = kanban([listed(1, TODO)], closed=done, left_out=["v1.0.0"])
    assert [n for n, _ in got["citadel"]] == [3, 4]                             # a milestone the server does not name is kept
    assert [n for n, _ in kanban([listed(1, TODO)], closed=done)[1]["citadel"]] == [2, 3, 4]


def test_waiting_for_you_is_a_pinned_row_and_the_card_stays_in_its_place():
    issues = [listed(1, TODO, labels=["help wanted"]), listed(2, BACKLOG, labels=["owner-action"]), listed(3, REVIEW, labels=["in-dev", "help wanted"]),
              listed(4, TODO)]
    built, got = kanban(issues)
    assert [c["number"] for c in built["pinned"]] == [1, 2, 3]
    assert got["you"] == [(1, None), (2, None)] and got["depot"] == [(3, None)] and got["todo"] == [(4, None)] and got["backlog"] == []
    assert {c["number"]: c["need"] for c in built["pinned"]} == {1: "answer", 2: "act", 3: "answer"}
    assert built["pinned"] == sorted(built["pinned"], key=lambda c: c["number"])


def test_a_closed_issue_with_a_leftover_label_is_not_pinned():
    stale = {**listed(9, DONE_ST, labels=["help wanted"]), "state": "CLOSED"}
    built, got = kanban([listed(1, TODO, labels=["help wanted"])], closed=[stale])
    assert [c["number"] for c in built["pinned"]] == [1] and got["citadel"] == [(9, None)]


def test_a_backlog_card_is_prompted_to_get_ready_not_to_start():
    built, _ = kanban([listed(1, BACKLOG), listed(2, TODO), listed(3, BACKLOG, labels=["help wanted"])])
    prompts = {c["number"]: c["prompt"] for col in built["columns"] for c in col["cards"]}
    assert prompts[1].startswith("Get #1 ready") and "Todo" in prompts[1]
    assert prompts[2].startswith("Start #2") and "waits for my answer" in prompts[3]          # a card that waits for you keeps its own prompt


def glance_body(**rows):
    return {"glance": rows}


def test_a_todo_card_says_why_a_run_would_not_take_it():
    typed = {"type": "Task"}
    cards = [{**listed(1, TODO), "type": "Task", "glance": {"auto": {"fit": "no", "why": "x"}}},
             {**listed(2, TODO), "type": "Task", "glance": {"auto": {"fit": "partly", "why": "x"}}},
             {**listed(3, TODO), "type": "Task", "glance": {"blocked_by": {"issues": [61, 99], "date": None, "text": "#61, #99"}}},
             {**listed(4, TODO), "type": "Task", "glance": {"needs_owner": {"kind": "answer", "what": "x"}}},
             {**listed(5, TODO), "type": "Task", "glance": {"auto": {"fit": "yes", "why": ""}}},
             {**listed(6, TODO), "glance": {}}]
    for card in cards:
        card["labels"] = []
    board = fp.build_board(cards + [listed(61), listed(99)], [], set(), [], [])
    built = fp.build_kanban(board)
    todo = {c["number"]: [b["text"] for b in c["badges"]] for c in next(col for col in built["columns"] if col["id"] == "todo")["cards"]}
    assert todo == {1: ["Auto: no"], 2: ["Auto: partly"], 3: ["blocked by #61, #99"], 4: ["needs the owner"], 5: [], 6: ["no Type"]}
    assert all(b["kind"] == "warn" for col in built["columns"] for c in col["cards"] for b in c["badges"])


def test_in_progress_and_review_cards_carry_no_pick_badges_but_testing_and_landing_show():
    issues = [{**listed(1, PROGRESS), "glance": {"auto": {"fit": "no", "why": "x"}}}, listed(2, PROGRESS), listed(3, PROGRESS)]
    built, _ = kanban(issues, trees=[tree(1), tree(2), tree(3)], testing={2}, landing=3)
    works = {c["number"]: [b["text"] for b in c["badges"]] for c in next(col for col in built["columns"] if col["id"] == "works")["cards"]}
    assert works == {1: [], 3: ["landing"]}
    proving = {c["number"]: [b["text"] for b in c["badges"]] for c in next(col for col in built["columns"] if col["id"] == "proving")["cards"]}
    assert proving == {2: ["testing"]}


def test_without_statuses_the_kanban_still_has_the_places():
    """The columns are the map's places, not GitHub's statuses: a repo without statuses has them too (a milestone puts a Ready card in Todo)."""
    issues = [listed(1, None, "v1.0.0"), listed(2, None)]
    board = fp.build_board(issues, [], set(), [], [])
    got = {c["id"]: [x["number"] for x in c["cards"]] for c in fp.build_kanban(board)["columns"]}
    assert got["todo"] == [1] and got["backlog"] == [2]
    assert [c["id"] for c in board] == [k for k, _, _ in fp.COLUMNS]


def test_the_state_carries_the_kanban_beside_the_board(monkeypatch, tmp_path):
    monkeypatch.setenv("SAANI_HOME", str(tmp_path / "home"))
    listing = {"board": [listed(1, TODO)], "open": [listed(1, TODO)], "closed": []}
    monkeypatch.setattr(fp, "cached", lambda key, seconds, fn: listing if key == "issues" else fn())
    monkeypatch.setattr(fp, "tasks", lambda: [])
    monkeypatch.setattr(fp, "landing", lambda: None)
    monkeypatch.setattr(fp, "agents", lambda: [])
    monkeypatch.setattr(fp, "tests", lambda: {"runs": [], "tier1": []})
    state = fp.state()
    assert [c["id"] for c in state["board"]] == [k for k, _, _ in fp.COLUMNS]
    assert [c["id"] for c in state["kanban"]["columns"]] == PLACES
    assert [c["number"] for c in state["kanban"]["columns"][1]["cards"]] == [1]


def test_the_page_draws_the_kanban_with_a_pinned_row_sections_and_badges():
    page = fp.page()
    assert 'id="pinned"' in page and "function drawPinned()" in page and "function sectionBody(c,hits)" in page
    assert 's.kanban&&s.kanban.columns?s.kanban:null' in page and "lastPinned=k?k.pinned:[]" in page
    assert '(c.badges||[]).map(b=>el("span",{class:"chip badge "+b.kind},b.text))' in page and '"agent working"' in page
    assert 'FOLDED_BY_DEFAULT=["live","citadel"]' in page and 'class:"fold"' in page and "Waiting for you" in page
    assert 'c.stage?el("small",{class:"stage"},c.stage)' in page and "saani.folded.v3" in page and "saani.folded.v2" not in page
    for col in PLACES:
        assert f".c-{col}{{--accent:" in page
