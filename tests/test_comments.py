"""saani/comments.py: the status comment of an issue and its label change, posted by `comment`, `start`, `land`, `ci pr` and `ci merged`.
gh is faked: no network."""
import shutil

import pytest

from saani import ci as fci
from saani import comments, core


class FakeIo:
    """The io of comments.post: reads answered from `existing` (the issue's comments), writes recorded."""
    dry = False

    def __init__(self, existing=(), labels=("in-dev",)):
        self.existing, self.written = [{"body": b} for b in existing], []
        self.labels = {n: [{"name": x} for x in ls] for n, ls in labels.items()} if isinstance(labels, dict) else None
        self.default = [] if isinstance(labels, dict) else [{"name": x} for x in labels]

    def json(self, *cmd):
        n = int(cmd[3]) if cmd[:3] == ("gh", "issue", "view") else 0
        return {"comments": self.existing, "labels": (self.labels or {}).get(n, self.default)}

    def write(self, *cmd):
        self.written.append(cmd)
        return ""


class Result:
    def __init__(self, code=0):
        self.returncode, self.stdout, self.stderr = code, "", "refused"


def test_a_comment_is_its_heading_then_the_fields_as_a_one_row_table_then_the_text():
    text = comments.render("started", {"Branch": "feat/5-x", "Worktree": "5-x"}, "Three bullets.")
    assert text.splitlines() == ["### ▶ Started", "| Branch | Worktree |", "|---|---|", "| feat/5-x | 5-x |", "", "Three bullets."]
    assert comments.render("blocked", None, "waits for #4") == "### ⛔ Blocked\nwaits for #4"
    assert "a\\|b" in comments.render("linked", {"Issue": "a|b"})


def test_a_decision_is_a_progress_comment_with_a_decision_field_and_the_latest_one_wins():
    first = comments.render("progress", {"Decision": "keep a | pipe", "Date": "2026-11-25"}, "context")
    second = comments.render("progress", {"Decision": "second"})
    assert comments.decision_of([first]) == "keep a | pipe"
    assert comments.decision_of(["hello", first, second, "### ⚙ Progress\nplain text", comments.render("progress", {"Step": "x"})]) == "second"
    assert comments.decision_of([]) is None
    assert comments.decision_of([comments.render("progress", {"Step": "x"}), comments.render("blocked", {"Decision": "no"})]) is None
    assert comments.decision_of([comments.render("progress", {"Decision": ""})]) is None


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


def test_nothing_is_posted_on_an_issue_without_the_needed_label():
    io = FakeIo(labels=["ui"])
    assert comments.post(5, "in-pr", io=io, needs={"in-dev"}) is False and io.written == []
    assert comments.post(5, "in-pr", io=FakeIo(labels=["in-dev"]), needs={"in-dev"}) is True


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
                                                      "| abc1234 | landing tests green, landed in 18 s | goes to main with the next pull request |"]


def test_start_posts_started_with_the_folder_name_only_unless_no_comment(monkeypatch, tmp_path):
    posted = []
    monkeypatch.setattr(core.comments, "try_post", lambda n, kind, fields=None, **k: posted.append((n, kind, fields)))
    monkeypatch.setattr(core, "issue_info", lambda n: {"title": "Add a thing", "type": "Task", "milestone": "v1"})
    monkeypatch.setattr(core, "latest_timing_report", lambda cwd=None: None)
    folder = tmp_path / "wt" / "5-add-thing"

    def git(*args, cwd=None, check=True):
        if args[:2] == ("worktree", "add"):
            folder.mkdir(parents=True, exist_ok=True)
        return "" if check else Result(0)
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


def test_start_journals_started_itself_only_when_the_comment_did_not(monkeypatch, tmp_path):
    from saani import auto
    journaled, posted = [], []
    monkeypatch.setattr(auto, "journal", lambda issue, kind, **k: journaled.append((issue, kind)))
    monkeypatch.setattr(core, "issue_info", lambda n: {"title": "Add a thing", "type": "Task", "milestone": "v1"})
    monkeypatch.setattr(core, "latest_timing_report", lambda cwd=None: None)
    folder = tmp_path / "wt" / "5-add-thing"

    def git(*args, cwd=None, check=True):
        if args[:2] == ("worktree", "add"):
            folder.mkdir(parents=True, exist_ok=True)
        return "" if check else Result(0)
    monkeypatch.setattr(core, "git", git)
    core.cfg().worktree_folder = str(tmp_path / "wt")
    core.cfg().owner = ""
    ns = type("A", (), {"issue": 5, "hotfix": False, "slug": None, "no_assign": True, "no_comment": True})()
    assert core.cmd_start(ns) == 0 and journaled == [(5, "started")]            # --no-comment: the run's marker is still written
    shutil.rmtree(folder)
    journaled.clear()
    ns.no_comment = False
    monkeypatch.setattr(core.comments, "try_post", lambda *a, **k: False)          # the comment was refused
    assert core.cmd_start(ns) == 0 and journaled == [(5, "started")]
    shutil.rmtree(folder)
    journaled.clear()
    monkeypatch.setattr(core.comments, "try_post", lambda *a, **k: posted.append(a) or True)   # posted: the comment journals it, not start
    assert core.cmd_start(ns) == 0 and journaled == [] and len(posted) == 1


class FakeCi(FakeIo):
    """ci.Run stand-in: `pr view` answers `pr`, issue views answer `existing` comments, writes recorded."""
    def __init__(self, pr, existing=(), labels=("in-dev",)):
        super().__init__(existing, labels)
        self.pr = pr

    def json(self, *cmd):
        if cmd[:3] == ("gh", "pr", "view"):
            return self.pr
        return super().json(*cmd)


def test_ci_pr_comments_in_pr_on_each_issue_the_pull_request_names_once():
    pr = {"title": "Release (refs #2)", "body": "#3", "commits": [{"messageHeadline": "x (refs #2)", "messageBody": ""}]}
    run = FakeCi(pr, labels={2: ["in-dev"], 3: ["ui"]})                # #3 is not on dev: left alone
    fci.comment_in_pr(run, 7, "https://github.com/acme/webshop/pull/7")
    said = [w for w in run.written if w[2] == "comment"]
    assert sorted(w[3] for w in said) == ["2"]
    assert "| PR | URL |" in said[0][-1] and "| #7 | https://github.com/acme/webshop/pull/7 |" in said[0][-1]
    again = FakeCi(pr, existing=["### 📦 In PR\n| #7 |"], labels={2: ["in-dev"]})
    fci.comment_in_pr(again, 7)
    assert again.written == []


MERGED = {"state": "MERGED", "mergedAt": "2026-11-22T14:26:00Z", "title": "Release (refs #2, #4, #5)", "body": "", "commits": [],
          "mergeCommit": {"oid": "abcdef0123456789"}}


class MergeGh(FakeIo):
    """ci.Run for `ci merged`: #2 in dev, #4 in main and already told, #5 neither; a relabel workflow that may have run."""
    def __init__(self, workflow_ran=False):
        super().__init__()
        self.workflow_ran = workflow_ran

    def json(self, *cmd):
        if cmd[:3] == ("gh", "pr", "view"):
            return MERGED
        if cmd[:3] == ("gh", "workflow", "list"):
            return [{"name": "Relabel merged issues", "state": "active"}] if self.workflow_ran else []
        if cmd[:3] == ("gh", "run", "list"):
            return [{"databaseId": 1, "conclusion": "success", "createdAt": "2026-11-22T14:27:00Z"}]
        n = cmd[3]
        if n == "2":
            return {"labels": [{"name": "in-dev"}], "comments": []}
        if n == "4":
            return {"labels": [{"name": "in-main"}], "comments": [{"body": "### 🚀 On main\n| #9 | abcdef0 |"}]}
        return {"labels": [{"name": "ui"}], "comments": []}


@pytest.fixture
def merged_env(cfg, monkeypatch):
    monkeypatch.setattr(core, "finished_auto_runs", lambda before=None, cwd=None: [])
    monkeypatch.setattr(core, "remove_auto_runs", lambda found, dry=False, cwd=None: None)
    monkeypatch.setattr(fci, "log", lambda entry: None)
    cfg.relabel = None
    cfg.ci_jobs = {"relabel": {"minutes": 1, "heavy": False, "what": "x", "workflow": "Relabel merged issues"}}
    return cfg


def on_main_said(gh):
    said = [w for w in gh.written if w[2] == "comment"]
    return [w[3] for w in said], said


def test_ci_merged_comments_on_main_when_it_relabels_itself_once(merged_env):
    gh = MergeGh()
    assert fci.cmd_merged(gh, 9) == 0
    who, said = on_main_said(gh)
    assert who == ["2"] and "| PR | Merge commit |" in said[0][-1] and "| #9 | abcdef0 |" in said[0][-1]
    edits = [w for w in gh.written if w[2] == "edit"]
    assert [w[3] for w in edits] == ["2"] and "--remove-label" in edits[0]       # the comment adds no second label edit


def test_ci_merged_comments_on_main_when_the_workflow_did_the_relabel(merged_env):
    gh = MergeGh(workflow_ran=True)
    assert fci.cmd_merged(gh, 9) == 0
    assert on_main_said(gh)[0] == ["2"] and not [w for w in gh.written if w[2] == "edit"]


def test_ci_merged_comments_on_main_when_the_relabel_command_did_it(merged_env, monkeypatch):
    merged_env.relabel = ["{python}", "relabel.py"]
    monkeypatch.setattr(fci.subprocess, "run", lambda cmd, **k: Result(0))
    gh = MergeGh()
    gh.read = lambda *cmd, **k: "token"
    assert fci.cmd_merged(gh, 9) == 0
    assert on_main_said(gh)[0] == ["2"]


class FakeCiPr(FakeIo):
    def __init__(self, fail_reads=False, fail_writes=False):
        super().__init__()
        self.fail_reads, self.fail_writes = fail_reads, fail_writes

    def json(self, *cmd):
        if cmd[:3] == ("gh", "pr", "list"):
            return [{"number": 7, "headRefOid": "abc123", "url": "https://github.com/acme/webshop/pull/7"}]
        if self.fail_reads:
            raise SystemExit("failed: gh pr view")
        if cmd[:3] == ("gh", "pr", "view"):
            return {"title": "Release (refs #2)", "body": "", "commits": []}
        return super().json(*cmd)

    def write(self, *cmd):
        if self.fail_writes:
            raise SystemExit("failed: gh issue comment")
        return super().write(*cmd)

    def read(self, *cmd, check=True):
        return "abc123"

    def ok(self, *cmd):
        return True


def test_ci_pr_comments_after_the_status_and_survives_a_gh_failure_there(monkeypatch, capsys):
    """cmd_pr calls comment_in_pr once the CI status is set; a gh failure in it (a read or a post) never fails the CI."""
    order = []
    monkeypatch.setattr(fci, "current_budget", lambda r: {"left": 1500, "share_used": .1, "share_passed": .5})
    monkeypatch.setattr(fci, "refused_this_month", lambda r: False)
    monkeypatch.setattr(fci, "on_pc", lambda sha: ("passed", 4))
    monkeypatch.setattr(fci, "set_status", lambda r, sha, state, text, url=None: order.append(("status", state)))
    monkeypatch.setattr(fci, "log", lambda entry: None)
    real = fci.comment_in_pr
    monkeypatch.setattr(fci, "comment_in_pr", lambda r, n, url=None: order.append(("comment", n, url)))
    assert fci.cmd_pr(FakeCiPr(), force_pc=True) == 0
    assert order[-1] == ("comment", 7, "https://github.com/acme/webshop/pull/7") and order[-2] == ("status", "success")
    monkeypatch.setattr(fci, "comment_in_pr", real)
    for run in (FakeCiPr(fail_reads=True), FakeCiPr(fail_writes=True)):
        assert fci.cmd_pr(run, force_pc=True) == 0                       # a warning, the CI result stands
    assert "could not" in capsys.readouterr().out


def test_a_landing_whose_label_edit_fails_prints_only_the_label_step(monkeypatch, capsys):
    row = {"head": "abc1234", "seconds": 18}
    monkeypatch.setattr(comments.subprocess, "run", lambda cmd, **k: Result(1 if cmd[2] == "edit" else 0))
    assert core.mark_on_dev("14", row) is True
    out = capsys.readouterr().out
    assert "label #14 in-dev" in out and "comment the commit" not in out
    monkeypatch.setattr(comments.subprocess, "run", lambda cmd, **k: Result(1))
    assert core.mark_on_dev("14", row) is False


def test_a_dry_run_with_stdout_as_a_cp1252_pipe_prints_the_heading_and_exits_0(tmp_path):
    """The wrapper runs `python -I`, which ignores PYTHONIOENCODING: the command must set UTF-8 itself, on every command."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    (tmp_path / "saani.toml").write_text('[project]\nrepo = "acme/webshop"\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    r = subprocess.run([sys.executable, "-I", str(root / "saani.py"), "comment", "5", "deployed", "--dry-run", "--field", "Commit=c1ecf63",
                        "--text", "- [x] test"], cwd=tmp_path, capture_output=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "### ✔ Deployed" in r.stdout.decode("utf-8")
