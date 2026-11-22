"""saani/board_server.py: the status page and the board, read-only, this PC only. The pure functions, the privacy of the session files
and the server's address check."""
import json
import pathlib
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


def test_the_columns_have_short_names_and_their_explanations_are_tooltips():
    """Ready, Building, Testing, Merged on dev, To deploy, To close, Deployed; the explanations as tooltips."""
    assert [t for _, t, _ in fp.COLUMNS] == ["Waiting for you", "Ready", "Building", "Testing", "Merged", "To deploy", "To close", "Deployed"]
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
    assert fp.agent_issue({"branch": "longrun/2026-11-21_2300", "folder": "c:/x/webshop-longrun"}, trees) is None


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
    assert closed and f'FOLDED_BY_DEFAULT=["{closed[0]}"]' in fp.page()
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
    """the Architect sessions in the top left, in place of the explanations."""
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


STEPS = {"inbox", "backlog", "you", "started", "building", "testing", "landing", "dev", "pr", "deploy", "close", "checks", "done"}


def test_the_board_is_served_with_its_skeleton_and_its_sample(monkeypatch):
    """/board is saani/board.html inside a page skeleton (the file has none: the artifact host adds it), and the sample data
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
    source = fp.BOARD_FILE.read_text(encoding="utf-8")
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


def test_a_ready_issue_with_no_agent_is_in_the_backlog_not_the_workshops():
    """the board page puts a Ready card in the backlog until an agent works on it, then in the Workshops."""
    source = fp.BOARD_FILE.read_text(encoding="utf-8")
    assert 'col.id === "ready" && (c.agents || []).length ? "started"' in source and 'ready: "backlog"' in source


def test_the_sample_shows_every_zone_and_keeps_out_private_text():
    """The sample is synthetic, every step is a known one, and it holds no session text or path."""
    mock = json.loads(fp.MOCK_FILE.read_text(encoding="utf-8"))
    steps = {c["step"] for c in mock["cards"]}
    assert steps <= STEPS and {"backlog", "you", "started", "testing", "pr", "deploy", "close", "checks", "done"} <= steps
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
    """/ and the old /board are the board, /kanban the kanban page; both pages have the page switch in their top bar."""
    server = fp.ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    get = lambda p: urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}{p}").read().decode()
    try:
        board, kanban = get("/"), get("/kanban")
        assert board == get("/board") == get("/index.html") and board.count('id="demo"') == 1
        assert 'class="pages"' in kanban and 'href="/kanban" aria-current="page"' in kanban and 'href="/">Board</a>' in kanban
        assert 'id="pages"' in board and 'href="/" aria-current="page">Board</a>' in board and 'href="/kanban">Kanban</a>' in board
        assert 'id="refresh"' in board and 'id="refresh"' in kanban and 'id="proj"' in board
        with pytest.raises(urllib.error.HTTPError) as e:
            get("/nowhere")
        assert e.value.code == 404
    finally:
        server.shutdown()
    assert '$("#pages").hidden = !live' in fp.BOARD_FILE.read_text(encoding="utf-8")     # the artifact build has no server: no page switch


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
    assert fp.registry_path().read_text(encoding="utf-8").startswith("# The projects the Saani board offers")
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
    for source in (fp.BOARD_FILE.read_text(encoding="utf-8"), fp.PAGE_FILE.read_text(encoding="utf-8")):
        flat = source.replace(" ", "")
        assert "location.hash.slice(1)" in source and "history.replaceState(null" in source
        assert 'p.waiting?"●"' in flat.replace("●", "●") or "p.waiting?\"●" in flat
        assert 'localStorage.setItem("saani-project"' in source and "status===404" in flat


GLANCE = {"long_run": {"fit": "partly", "why": "the data model yes"}, "blocked_by": {"issues": [13], "date": None, "text": "#13"}}


def test_a_card_carries_what_its_at_a_glance_table_says():
    """the long-run fit read from the table (source: table), blocked while a named issue is open and not yet on dev, an epic is an anchor."""
    issue = {"number": 14, "title": "Board data", "labels": [], "state": "OPEN", "glance": GLANCE}
    assert fp.lifecycle(issue, {13, 14}) == {"blocked": True, "anchor": False, "blocked_by": [13],
                                            "longrun": {"fit": "partly", "why": "the data model yes", "source": "table"}}
    assert fp.lifecycle(issue, {14})["blocked"] is False
    assert fp.lifecycle({"title": "Saani v1 (epic)"}, set())["anchor"] and fp.lifecycle({"title": "Live checks v0.1"}, set())["anchor"]
    assert "longrun" not in fp.lifecycle({"title": "x"}, set())
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
    page = fp.BOARD_FILE.read_text(encoding="utf-8")
    assert '"/api/issue?n="' in page and "DETAILS[c.number]" in page and "DATA.milestones" in page and "DATA.hygiene" in page


def test_landing_comments_the_issue_and_labels_it_and_says_so_when_gh_refuses(monkeypatch):
    """the On dev comment (commit, tests, what next) and the in-dev label; a refusal leaves the manual step, never an undone landing."""
    from saani import core
    calls = []
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0})())
    text = core.landing_comment({"head": "abc1234", "seconds": 18})
    assert text.splitlines()[0] == "### ✅ On dev" and "abc1234" in text and "18 s" in text and "with the next pull request" in text
    assert core.mark_on_dev("14", text) is True
    assert calls[0][:4] == ["gh", "issue", "comment", "14"] and calls[1][:4] == ["gh", "issue", "edit", "14"] and calls[1][-2:] == ["--add-label", "in-dev"]
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 1})())
    assert core.mark_on_dev("14", text) is False

    def missing(cmd, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(core.subprocess, "run", missing)
    assert core.mark_on_dev("14", text) is False
