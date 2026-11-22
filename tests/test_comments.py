"""saani/comments.py: the status comment of an issue and its label change, posted by `comment`, `start`, `land`, `ci pr` and `ci merged`.
gh is faked: no network."""
import shutil

import pytest

from saani import ci as fci
from saani import comments, core


class FakeIo:
    """The io of comments.post: reads answered from `existing` (the issue's comments), writes recorded."""
    dry = False

    def __init__(self, existing=()):
        self.existing, self.written = [{"body": b} for b in existing], []

    def json(self, *cmd):
        return {"comments": self.existing}

    def write(self, *cmd):
        self.written.append(cmd)
        return ""


class Result:
    def __init__(self, code=0):
        self.returncode, self.stdout, self.stderr = code, "", "refused"


def test_a_comment_is_its_heading_then_the_fields_as_a_one_row_table_then_the_text():
    text = comments.render("started", {"Branch": "feat/5-x", "Worktree": "5-x"}, "Three bullets.")
    assert text.splitlines() == ["### ▶ Started", "| Branch | Worktree |", "|---|---|", "| feat/5-x | 5-x |", "Three bullets."]
    assert comments.render("blocked", None, "waits for #4") == "### ⛔ Blocked\nwaits for #4"
    assert "a\\|b" in comments.render("linked", {"Issue": "a|b"})


def test_every_kind_has_the_heading_of_the_comment_kinds_file():
    headings = {"started": "▶ Started", "review": "🔎 In review", "blocked": "⛔ Blocked", "question": "❓ Question", "progress": "⚙ Progress",
                "on-dev": "✅ On dev", "in-pr": "📦 In PR", "on-main": "🚀 On main", "deployed": "✔ Deployed", "linked": "🔗 Linked"}
    assert {k: v[0] for k, v in comments.KINDS.items()} == {k: "### " + h for k, h in headings.items()}


@pytest.mark.parametrize("kind, flags", [("started", []), ("review", []), ("blocked", []), ("progress", []), ("in-pr", []),
                                         ("deployed", []), ("linked", []), ("question", ["--add-label", "help wanted"]),
                                         ("on-dev", ["--add-label", "in-dev"]),
                                         ("on-main", ["--remove-label", "in-dev", "--add-label", "in-main"])])
def test_each_kind_sets_its_label_and_only_that(kind, flags):
    io = FakeIo()
    assert comments.post(5, kind, io=io) is True
    assert io.written[0][:6] == ("gh", "issue", "comment", "5", "--repo", "acme/webshop")
    assert io.written[0][-1].startswith(comments.KINDS[kind][0])
    assert [w[6:] for w in io.written[1:]] == ([tuple(flags)] if flags else [])


def test_the_labels_come_from_the_settings(cfg):
    cfg.labels = {**cfg.labels, "in_dev": "on-the-dev-branch"}
    io = FakeIo()
    comments.post(5, "on-dev", io=io)
    assert io.written[1][-2:] == ("--add-label", "on-the-dev-branch")


def test_a_pull_request_is_commented_once_per_issue():
    io = FakeIo(["### 📦 In PR\n| PR |\n|---|\n| #7 |"])
    assert comments.post(5, "in-pr", {"PR": "#7"}, io=io, once="#7") is False and io.written == []
    assert comments.post(5, "in-pr", {"PR": "#70"}, io=FakeIo(["### 📦 In PR\n| #7 |"]), once="#70") is True      # #7 is not #70
    assert comments.post(5, "in-pr", {"PR": "#8"}, io=io, once="#8") is True
    assert comments.post(5, "in-pr", {"PR": "#7"}, io=FakeIo(["### 🚀 On main\n| #7 |"]), once="#7") is True     # another kind


def test_the_command_posts_with_gh_and_a_dry_run_prints_instead(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(comments.subprocess, "run", lambda cmd, **k: calls.append(cmd) or Result())
    ns = type("A", (), {"issue": 9, "kind": "question", "field": ["Context=x", "Asks=y"], "text": "Which?", "dry_run": True})()
    assert comments.cmd_comment(ns) == 0 and calls == []
    assert "dry-run: gh issue comment 9" in capsys.readouterr().out
    ns.dry_run = False
    assert comments.cmd_comment(ns) == 0
    assert [tuple(c[:3]) for c in calls] == [("gh", "issue", "comment"), ("gh", "issue", "edit")]
    assert "| Context | Asks |" in calls[0][-1] and calls[0][-1].endswith("Which?")


def test_a_bad_field_is_refused():
    with pytest.raises(SystemExit):
        comments.parse_fields(["nokey"])
    assert comments.parse_fields(["a=1", "b=x=y"]) == {"a": "1", "b": "x=y"}


def test_a_gh_refusal_stops_post_but_only_warns_in_try_post(monkeypatch, capsys):
    monkeypatch.setattr(comments.subprocess, "run", lambda cmd, **k: Result(1))
    with pytest.raises(SystemExit):
        comments.post(5, "started")
    assert comments.try_post(5, "started") is False
    assert "could not comment #5" in capsys.readouterr().out


def test_landing_posts_on_dev_with_the_content_it_always_had():
    row = {"head": "abc1234", "seconds": 18}
    assert core.landing_comment(row).splitlines() == ["### ✅ On dev", "| Commit | Tests | Next |", "|---|---|---|",
                                                      "| abc1234 | tier 1 green, landed in 18 s | goes to main with the next pull request |"]


def test_start_posts_started_with_the_folder_name_only_unless_no_comment(monkeypatch, tmp_path):
    posted = []
    monkeypatch.setattr(core.comments, "try_post", lambda n, kind, fields=None, **k: posted.append((n, kind, fields)))
    monkeypatch.setattr(core, "issue_info", lambda n: {"title": "Add a thing", "type": "Task", "milestone": "v1"})
    monkeypatch.setattr(core, "latest_timing_report", lambda cwd=None: None)
    folder = tmp_path / "wt" / "5-add-thing"

    def git(*args, cwd=None, check=True):
        if args[:2] == ("worktree", "add"):
            folder.mkdir(parents=True, exist_ok=True)
        return ""
    monkeypatch.setattr(core, "git", git)
    core.cfg().worktree_folder = str(tmp_path / "wt")
    core.cfg().owner = ""
    ns = type("A", (), {"issue": 5, "hotfix": False, "slug": None, "no_assign": True, "no_comment": False})()
    assert core.cmd_start(ns) == 0
    assert posted == [(5, "started", {"Branch": "task/5-add-thing", "Worktree": "5-add-thing"})]
    shutil.rmtree(folder)
    posted.clear()
    ns.no_comment = True
    assert core.cmd_start(ns) == 0 and posted == []


class FakeCi(FakeIo):
    """ci.Run stand-in: `pr view` answers `pr`, issue views answer `existing` comments, writes recorded."""
    def __init__(self, pr, existing=()):
        super().__init__(existing)
        self.pr = pr

    def json(self, *cmd):
        if cmd[:3] == ("gh", "pr", "view"):
            return self.pr
        return super().json(*cmd)


def test_ci_pr_comments_in_pr_on_each_issue_the_pull_request_names_once():
    pr = {"title": "Release (refs #2)", "body": "#3", "commits": [{"messageHeadline": "x (refs #2)", "messageBody": ""}]}
    run = FakeCi(pr)
    fci.comment_in_pr(run, 7, "https://github.com/acme/webshop/pull/7")
    said = [w for w in run.written if w[2] == "comment"]
    assert sorted(w[3] for w in said) == ["2", "3"]
    assert "| PR | URL |" in said[0][-1] and "| #7 | https://github.com/acme/webshop/pull/7 |" in said[0][-1]
    again = FakeCi(pr, existing=["### 📦 In PR\n| #7 |"])
    fci.comment_in_pr(again, 7)
    assert again.written == []


def test_ci_merged_comments_on_main_on_each_relabelled_issue_once(cfg, monkeypatch):
    monkeypatch.setattr(core, "finished_auto_runs", lambda before=None, cwd=None: [])
    monkeypatch.setattr(core, "remove_auto_runs", lambda found, dry=False, cwd=None: None)
    monkeypatch.setattr(fci, "log", lambda entry: None)
    cfg.relabel = None
    cfg.ci_jobs = {}
    merged = {"state": "MERGED", "mergedAt": "2026-11-22T14:26:00Z", "title": "Release (refs #2, #4)", "body": "",
              "commits": [], "mergeCommit": {"oid": "abcdef0123456789"}}

    class Gh(FakeIo):
        def json(self, *cmd):
            if cmd[:3] == ("gh", "pr", "view"):
                return merged
            if cmd[:3] == ("gh", "workflow", "list"):
                return []
            if cmd[3] == "4":      # already told
                return {"labels": [{"name": "in-dev"}], "comments": [{"body": "### 🚀 On main\n| #9 | abcdef0 |"}]}
            return {"labels": [{"name": "in-dev"}], "comments": []}
    gh = Gh()
    assert fci.cmd_merged(gh, 9) == 0
    said = [w for w in gh.written if w[2] == "comment"]
    assert [w[3] for w in said] == ["2"] and "| PR | Merge commit |" in said[0][-1] and "| #9 | abcdef0 |" in said[0][-1]
    edits = [w for w in gh.written if w[2] == "edit"]
    assert sorted(w[3] for w in edits) == ["2", "4"] and all("--remove-label" in w and "--add-label" in w for w in edits)
    assert len(edits) == 2           # the comment adds no second label edit
