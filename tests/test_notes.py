"""saani/notes.py: release notes from the issues of a milestone or a pull request; gh is faked."""
import argparse
import json

import pytest

from saani import notes

ROWS = [
    {"number": 3, "title": "Cart total", "state": "CLOSED", "labels": [], "body": "## Why\nx\n\n## Result\nThe cart shows the total. More words.\n"},
    {"number": 1, "title": "Wishlist", "state": "CLOSED", "labels": [], "body": "no sections here"},
    {"number": 2, "title": "Checkout crash", "state": "OPEN", "labels": [{"name": "in-main"}], "body": "## Result\n- Checkout no longer crashes! Done.\n"},
    {"number": 4, "title": "Still open", "state": "OPEN", "labels": [], "body": "## Result\nNot yet."},
    {"number": 5, "title": "Untyped chore", "state": "CLOSED", "labels": [], "body": ""},
]
TYPES = "1\tFeature\n2\tBug\n3\tFeature\n4\tTask\n"


class FakeGh:
    def __init__(self, rows=ROWS, fail=None, pr=None, releases=()):
        self.rows, self.fail, self.pr, self.releases = rows, fail, pr, releases
        self.calls = []
        self.edited = None
        self.release_error = None
        self.broken = set()                                              # issue numbers whose `gh issue view` fails
        self.created = []
        self.head = "m" * 40                                             # the production branch's head
        self.tags = {}                                                   # tag -> (object type, sha)
        self.annotated = {}                                              # annotated tag object sha -> the commit it points at
        self.files = {}                                                  # ref -> the version file's text
        self.parents = {}                                                # merge sha -> its first parent
        self.remote_messages = {}                                        # sha -> commit message, for commits git does not have

    def __call__(self, *args):
        self.calls.append(args)
        if self.fail:
            raise SystemExit(f"saani release notes: gh {' '.join(args[:2])} failed: {self.fail}")
        if args[:2] == ("issue", "list"):
            return json.dumps(self.rows)
        if args[:2] == ("issue", "view"):
            if int(args[2]) in self.broken:
                raise notes.GhFailed(args, "Could not resolve to an Issue")
            return json.dumps({"url": f"https://github.com/acme/webshop/{'pull' if int(args[2]) == 6 else 'issues'}/{args[2]}",
                               **next(r for r in self.rows if r["number"] == int(args[2]))})
        if args[:2] == ("pr", "view"):
            return json.dumps(self.pr)
        if args[:2] == ("api", "--paginate"):
            return TYPES
        if args[0] == "api" and args[1] == "-H":                         # a file of the repository at a commit
            ref = args[3].rsplit("ref=", 1)[1]
            if ref not in self.files:
                raise notes.GhFailed(args, "HTTP 404: Not Found")
            return self.files[ref]
        if args[0] == "api" and "/git/ref/tags/" in args[1]:
            tag = args[1].rsplit("/", 1)[1]
            if tag not in self.tags:
                raise notes.GhFailed(args, "HTTP 404: Not Found")
            kind, sha = self.tags[tag]
            return json.dumps({"object": {"type": kind, "sha": sha}})
        if args[0] == "api" and "/git/tags/" in args[1]:
            return json.dumps({"object": {"type": "commit", "sha": self.annotated[args[1].rsplit("/", 1)[1]]}})
        if args[0] == "api" and args[1].endswith("/commits/main") and args[-1] == ".sha":
            return self.head + "\n"
        if args[0] == "api" and "/commits/" in args[1]:
            sha = args[1].rsplit("/", 1)[1]
            if args[-1] == ".parents[0].sha":
                return self.parents[sha] + "\n"
            if sha not in self.remote_messages:
                raise notes.GhFailed(args, "HTTP 404: Not Found")
            return self.remote_messages[sha]
        if args[:2] == ("release", "create"):
            self.created.append((args, open(args[args.index("--notes-file") + 1], encoding="utf-8").read()))
            return ""
        if args[:2] == ("release", "view"):
            if self.release_error:
                raise notes.GhFailed(args, self.release_error)
            if args[2] not in self.releases:
                raise notes.GhFailed(args, "release not found")
            return json.dumps({"tagName": args[2]})
        if args[:2] in (("pr", "edit"), ("release", "edit")):
            self.edited = (args, open(args[args.index("--body-file" if "--body-file" in args else "--notes-file") + 1], encoding="utf-8").read())
            return ""
        raise AssertionError(args)


LOCAL = {}                                                               # sha -> subject of the commits git has


@pytest.fixture(autouse=True)
def local_git(monkeypatch):
    LOCAL.clear()
    monkeypatch.setattr(notes, "git", lambda *a: LOCAL.get(a[-1]))


def on_dev(sha):
    return {"body": f"### ✅ On dev\n| Commit | Tests | Next |\n|---|---|---|\n| {sha} | landing tests green, landed in 3 s | goes to main with the next pull request |"}


@pytest.fixture
def fake(monkeypatch):
    f = FakeGh()
    monkeypatch.setattr(notes, "gh", f)
    return f


def run(*argv):
    p = argparse.ArgumentParser()
    notes.add_parsers(p.add_subparsers(dest="cmd"))
    args = p.parse_args(["release", "notes", *argv])
    return args.run(args)


def test_result_sentence():
    assert notes.result_sentence("## Result\nOne. Two.") == "One."
    assert notes.result_sentence("## Result\n\n- A bullet! Next.\n\n## After") == "A bullet!"
    assert notes.result_sentence("## Result\n## Next\ntext") == ""
    assert notes.result_sentence("nothing") == ""


def test_notes_group_by_type_with_the_result_line_or_the_title_alone(fake, capsys):
    assert run("1.0") == 0
    out = capsys.readouterr().out
    assert out.startswith("## Release notes: 1.0\n")
    assert out.index("### Feature") < out.index("### Bug") < out.index("### Other")
    assert "- #1 Wishlist\n" in out                                          # no Result: the title alone
    assert "- #3 Cart total: The cart shows the total.\n" in out
    assert "- #2 Checkout crash: Checkout no longer crashes!\n" in out       # open but in-main
    assert "#4" not in out                                                   # open, not in main
    assert "- #5 Untyped chore\n" in out
    assert "### Task" not in out
    assert not any("--post" in c for c in fake.calls)


def test_an_empty_milestone_says_so(monkeypatch, capsys):
    monkeypatch.setattr(notes, "gh", FakeGh(rows=[]))
    assert run("9.9") == 0
    assert "No closed or in-main issue in this milestone." in capsys.readouterr().out


def test_a_gh_failure_stops_with_ghs_message(monkeypatch, capsys):
    monkeypatch.setattr(notes, "gh", FakeGh(fail="milestone not found"))
    with pytest.raises(SystemExit) as e:
        run("nope")
    assert "milestone not found" in str(e.value)
    assert capsys.readouterr().out == ""


def test_pr_takes_the_issues_its_body_and_commits_name(monkeypatch, capsys):
    pr = {"body": "Lands the cart, refs #3.", "commits": [{"messageHeadline": "Fix it (refs #2)", "messageBody": "Why: x"},
                                                           {"messageHeadline": "Other", "messageBody": "refs #1, #5"}]}
    monkeypatch.setattr(notes, "gh", FakeGh(pr=pr))
    assert run("--pr", "12") == 0
    out = capsys.readouterr().out
    assert out.startswith("## Release notes: pull request #12\n")
    for n in ("#1 ", "#2 ", "#3 ", "#5 "):
        assert f"- {n}" in out
    assert "#4" not in out


def test_post_sets_the_pr_body_section(monkeypatch):
    pr = {"body": "Intro\n\n## Release notes\n\nold\n\n## Other\nkeep", "commits": [{"messageHeadline": "x refs #1", "messageBody": ""}]}
    f = FakeGh(pr=pr)
    monkeypatch.setattr(notes, "gh", f)
    assert run("--pr", "12", "--post") == 0
    body = f.edited[1]
    assert "old" not in body and "- #1 Wishlist" in body and "## Other\nkeep" in body and body.startswith("Intro")


def test_post_edits_an_existing_release_and_refuses_a_missing_one(monkeypatch, capsys):
    f = FakeGh(releases=["1.0"])
    monkeypatch.setattr(notes, "gh", f)
    assert run("1.0", "--post") == 0
    assert f.edited[0][:3] == ("release", "edit", "1.0") and "### Feature" in f.edited[1]
    f = FakeGh(releases=["0.9"])
    monkeypatch.setattr(notes, "gh", f)
    assert run("1.0", "--post") == 1
    assert f.edited is None
    assert "no GitHub release with the tag 1.0" in capsys.readouterr().err


def test_the_sentence_does_not_end_at_an_abbreviation():
    assert notes.result_sentence("## Result\nFor guests, e.g. with no account. Next one.") == "For guests, e.g. with no account."
    assert notes.result_sentence("## Result\nBags, bins, etc. are listed. Done.") == "Bags, bins, etc. are listed."
    assert notes.result_sentence("## Result\nVersion 1.2 is out. Yes.") == "Version 1.2 is out."


def test_a_result_that_starts_with_a_table_or_a_fence_gives_the_title_alone():
    assert notes.result_sentence("## Result\n| a | b |\n|---|---|\n| 1 | 2 |") == ""
    assert notes.result_sentence("## Result\n```\ncode\n```\nText.") == ""


def test_a_subheading_under_result_is_passed_over():
    assert notes.result_sentence("## Result\n### Cart\nThe cart is fast. Really.\n## Next\nx") == "The cart is fast."
    assert notes.result_sentence("## Result\n## Next\nx") == ""


@pytest.mark.parametrize("heading", ["## Result", "## Results", "## Result:", "## RESULT", "### result"])
def test_the_result_heading_variants(heading):
    assert notes.result_sentence(f"{heading}\nIt works.") == "It works."


def test_a_pr_that_names_no_issue_says_so(monkeypatch, capsys):
    monkeypatch.setattr(notes, "gh", FakeGh(pr={"body": "nothing", "commits": []}))
    assert run("--pr", "12") == 0
    assert "The pull request names no issue (refs #n)." in capsys.readouterr().out


def test_pr_skips_a_ref_that_is_a_pull_request_or_unreadable_and_warns(monkeypatch, capsys):
    rows = ROWS + [{"number": 6, "title": "A PR", "state": "CLOSED", "labels": [], "body": ""}]
    f = FakeGh(rows=rows, pr={"body": "refs #1, #6, #7", "commits": []})
    f.broken = {7}
    monkeypatch.setattr(notes, "gh", f)
    assert run("--pr", "12") == 0
    cap = capsys.readouterr()
    assert "- #1 Wishlist" in cap.out and "#6" not in cap.out and "#7" not in cap.out
    assert "#6 skipped" in cap.err and "#7 skipped" in cap.err


def test_a_gh_error_other_than_release_not_found_stops(monkeypatch):
    f = FakeGh(releases=["1.0"])
    f.release_error = "HTTP 502"
    monkeypatch.setattr(notes, "gh", f)
    with pytest.raises(SystemExit) as e:
        run("1.0", "--post")
    assert "HTTP 502" in str(e.value)


def test_with_section_appends_when_there_is_none():
    assert notes.with_section("Body", "## T\n\n- a\n") == "Body\n\n## Release notes\n\n- a\n"
    assert notes.with_section("", "## T\n\n- a\n") == "## Release notes\n\n- a\n"


# ---------------------------------------------------------------- the commits under each issue, the releases (#55)

def with_comments(rows_comments):
    return [{**r, "comments": rows_comments.get(r["number"], [])} for r in ROWS]


def test_commit_shas_come_from_the_commit_cell_of_the_on_dev_comments():
    comments = [{"body": "### ▶ Started\n| Commit |\n|---|\n| aaaaaaa |"}, on_dev("abc1234"), on_dev("abc1234"), on_dev("def5678 and 0123456789abcdef"),
                {"body": "just text abc1234"}]
    assert notes.commit_shas(comments) == ["abc1234", "def5678", "0123456789abcdef"]


def test_the_commit_subjects_go_under_the_issue_line(monkeypatch, capsys):
    LOCAL.update({"abc1234": "Cart: show the total", "def5678": "Cart total"})            # the second repeats the issue title
    f = FakeGh(rows=with_comments({3: [on_dev("abc1234"), on_dev("def5678"), on_dev("abc1234")], 1: [on_dev("9999999")]}))
    f.remote_messages["9999999"] = "Wishlist: first cut\n\nWhy: x\nUndo: y"                 # not local: GitHub's first line, no trailers
    monkeypatch.setattr(notes, "gh", f)
    assert run("1.0") == 0
    out = capsys.readouterr().out
    assert "- #3 Cart total: The cart shows the total.\n  - Cart: show the total\n" in out
    assert out.count("Cart: show the total") == 1 and "  - Cart total" not in out
    assert "- #1 Wishlist\n  - Wishlist: first cut\n" in out and "Why:" not in out


def test_an_unknown_commit_is_left_out_with_a_warning(monkeypatch, capsys):
    monkeypatch.setattr(notes, "gh", FakeGh(rows=with_comments({3: [on_dev("abc1234")]})))
    assert run("1.0") == 0
    cap = capsys.readouterr()
    assert "abc1234" not in cap.out and "commit abc1234 not found" in cap.err


def test_short_gives_one_line_per_issue_and_reads_no_comments(monkeypatch, capsys):
    LOCAL["abc1234"] = "Cart: show the total"
    f = FakeGh(rows=with_comments({3: [on_dev("abc1234")]}))
    monkeypatch.setattr(notes, "gh", f)
    assert run("1.0", "--short") == 0
    assert "Cart: show the total" not in capsys.readouterr().out
    assert all("comments" not in c[-1] for c in f.calls if c[:2] == ("issue", "list"))


def version_world(before, after):
    f = FakeGh(pr={"body": "refs #1", "commits": []})
    f.parents = {"m" * 40: "p" * 40}
    f.files = {"p" * 40: f'__version__ = "{before}"\n', "m" * 40: f'__version__ = "{after}"\n'}
    return f


def test_a_version_change_creates_the_release_on_the_merge_commit(monkeypatch, cfg, capsys):
    cfg.version_file = "pkg/__init__.py"
    f = version_world("1.0.0", "1.1.0")
    monkeypatch.setattr(notes, "gh", f)
    assert notes.release_after_merge(7, "m" * 40) == "v1.1.0"
    args, body = f.created[0]
    assert args[:3] == ("release", "create", "v1.1.0") and args[args.index("--target") + 1] == "m" * 40
    assert args[args.index("--title") + 1] == "v1.1.0" and "- #1 Wishlist" in body
    assert "created the release v1.1.0" in capsys.readouterr().out


def test_no_version_change_creates_nothing_and_says_so(monkeypatch, cfg, capsys):
    cfg.version_file = "pkg/__init__.py"
    f = version_world("1.0.0", "1.0.0")
    monkeypatch.setattr(notes, "gh", f)
    assert notes.release_after_merge(7, "m" * 40) is None
    assert f.created == [] and "does not change the version" in capsys.readouterr().out


def test_without_a_version_file_nothing_is_read(monkeypatch, capsys):
    monkeypatch.setattr(notes, "gh", lambda *a: pytest.fail("gh called"))
    assert notes.release_after_merge(7, "m" * 40) is None
    assert "version_file is not set" in capsys.readouterr().out


def test_a_failing_release_raises_with_the_version(monkeypatch, cfg):
    cfg.version_file = "pkg/__init__.py"
    f = version_world("1.0.0", "1.1.0")
    f.release_error = "HTTP 502"
    monkeypatch.setattr(notes, "gh", f)
    with pytest.raises(notes.ReleaseFailed) as e:
        notes.release_after_merge(7, "m" * 40)
    assert e.value.version == "1.1.0" and "HTTP 502" in str(e.value)


PACKAGE_JSON = '{\n  "name": "webshop",\n  "version": "3.0.1",\n  "engines": {\n    "version": "18"\n  }\n}\n'
PYPROJECT_DYNAMIC = '[project]\nname = "webshop"\ndynamic = ["version"]\nrequires-python = ">=3.11"\n\n[tool.black]\ntarget-version = ["py311"]\npython_version = "3.11"\n'
PYPROJECT_STATIC = '[tool.mypy]\npython_version = "3.11"\n\n[project]\n# version = "9.9.9"\nversion = "2.0.0"\n'


@pytest.mark.parametrize("text, version", [
    ('__version__ = "0.5.0"', "0.5.0"), ('version = "1.2"', "1.2"), ("nothing", None),
    (PACKAGE_JSON, "3.0.1"), (PYPROJECT_DYNAMIC, None), (PYPROJECT_STATIC, "2.0.0"),
    ('python_version = "3.11"', None), ('target-version = "py311"', None), ('# version = "1.0"', None), ('  version: "4.1"', "4.1"),
])
def test_version_of(text, version):
    assert notes.version_of(text) == version


def create(*argv):
    p = argparse.ArgumentParser()
    notes.add_parsers(p.add_subparsers(dest="cmd"))
    args = p.parse_args(["release", "create", *argv])
    return args.run(args)


def test_release_create_by_hand_from_a_pr(monkeypatch, capsys):
    f = FakeGh(pr={"body": "refs #1", "commits": []})
    monkeypatch.setattr(notes, "gh", f)
    assert create("0.4.0", "--target", "abcdef1234567", "--from-pr", "9") == 0
    args, body = f.created[0]
    assert args[:3] == ("release", "create", "v0.4.0") and "abcdef1234567" in args and "- #1 Wishlist" in body
    assert "--latest=false" in args                                      # a past version is not the latest release
    assert "created the release v0.4.0 on abcdef1" in capsys.readouterr().out


def test_a_release_on_the_production_head_is_the_latest(monkeypatch):
    f = FakeGh(pr={"body": "refs #1", "commits": []})
    monkeypatch.setattr(notes, "gh", f)
    assert create("1.0.0", "--target", "m" * 40, "--from-pr", "9") == 0
    assert "--latest=false" not in f.created[0][0]


def test_without_target_the_release_goes_on_the_production_head_and_says_so(monkeypatch, capsys):
    f = FakeGh(pr={"body": "refs #1", "commits": []})
    f.head = "c" * 40
    monkeypatch.setattr(notes, "gh", f)
    assert create("1.0.0", "--from-pr", "9") == 0
    args = f.created[0][0]
    assert args[args.index("--target") + 1] == "c" * 40 and "--latest=false" not in args
    assert "ccccccc (the head of main)" in capsys.readouterr().out


def test_an_existing_tag_at_another_commit_is_refused(monkeypatch, tmp_path):
    f = FakeGh()
    f.tags["v1.0.0"] = ("commit", "a" * 40)
    monkeypatch.setattr(notes, "gh", f)
    text = tmp_path / "n.md"
    text.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="never moved"):
        create("1.0.0", "--target", "b" * 40, "--notes-file", str(text))
    assert f.created == []


def test_an_existing_tag_at_the_same_commit_or_without_target_makes_the_release_on_the_tags_commit(monkeypatch, tmp_path, capsys):
    f = FakeGh()
    f.tags["v1.0.0"] = ("commit", "a" * 40)
    f.tags["v1.1.0"] = ("tag", "t" * 40)                                 # annotated: peeled
    f.annotated["t" * 40] = "d" * 40
    monkeypatch.setattr(notes, "gh", f)
    text = tmp_path / "n.md"
    text.write_text("x", encoding="utf-8")
    assert create("1.0.0", "--target", "aaaaaaa", "--notes-file", str(text)) == 0
    assert create("1.1.0", "--notes-file", str(text)) == 0
    for args, _ in f.created:
        assert "--target" not in args and "--latest=false" in args
    out = capsys.readouterr().out
    assert "existing tag's commit aaaaaaa" in out and "existing tag's commit ddddddd" in out


def test_the_version_that_a_merge_changes_to_one_with_a_release_is_left_alone(monkeypatch, cfg, capsys):
    cfg.version_file = "pkg/__init__.py"
    f = version_world("1.1.0", "1.0.0")                                  # a revert
    f.releases = ["v1.0.0"]
    monkeypatch.setattr(notes, "gh", f)
    assert notes.release_after_merge(7, "m" * 40) is None
    assert f.created == [] and f.edited is None
    assert "no release: v1.0.0 already exists" in capsys.readouterr().out


def test_an_empty_notes_file_or_milestone_is_refused():
    with pytest.raises(SystemExit, match="empty"):
        create("1.0.0", "--notes-file", "")
    with pytest.raises(SystemExit, match="empty"):
        create("1.0.0", "--milestone", "")


def test_release_create_by_hand_from_a_milestone_and_a_file(monkeypatch, tmp_path, capsys):
    f = FakeGh()
    monkeypatch.setattr(notes, "gh", f)
    assert create("v0.3.0", "--milestone", "0.3.0") == 0
    assert f.created[0][0][2] == "v0.3.0" and f.created[0][0][f.created[0][0].index("--target") + 1] == "m" * 40 and "- #3 Cart total" in f.created[0][1]
    text = tmp_path / "notes.md"
    text.write_text("Hand written.", encoding="utf-8")
    assert create("0.2.0", "--notes-file", str(text)) == 0
    assert f.created[1][1] == "Hand written."


def test_release_create_on_an_existing_release_edits_it(monkeypatch, tmp_path, capsys):
    f = FakeGh(releases=["v0.3.0"])
    monkeypatch.setattr(notes, "gh", f)
    text = tmp_path / "n.md"
    text.write_text("New.", encoding="utf-8")
    assert create("0.3.0", "--notes-file", str(text)) == 0
    assert f.created == [] and f.edited[1] == "New."
    assert "its tag stays where it is" in capsys.readouterr().out


def test_release_create_needs_exactly_one_notes_source():
    with pytest.raises(SystemExit, match="one of"):
        create("0.3.0")
    with pytest.raises(SystemExit, match="one of"):
        create("0.3.0", "--milestone", "m", "--from-pr", "1")
