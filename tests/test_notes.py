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

    def __call__(self, *args):
        self.calls.append(args)
        if self.fail:
            raise SystemExit(f"saani release notes: gh {' '.join(args[:2])} failed: {self.fail}")
        if args[:2] == ("issue", "list"):
            return json.dumps(self.rows)
        if args[:2] == ("issue", "view"):
            return json.dumps(next(r for r in self.rows if r["number"] == int(args[2])))
        if args[:2] == ("pr", "view"):
            return json.dumps(self.pr)
        if args[:2] == ("api", "--paginate"):
            return TYPES
        if args[:2] == ("release", "list"):
            return json.dumps([{"tagName": t} for t in self.releases])
        if args[:2] in (("pr", "edit"), ("release", "edit")):
            self.edited = (args, open(args[args.index("--body-file" if "--body-file" in args else "--notes-file") + 1], encoding="utf-8").read())
            return ""
        raise AssertionError(args)


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


def test_with_section_appends_when_there_is_none():
    assert notes.with_section("Body", "## T\n\n- a\n") == "Body\n\n## Release notes\n\n- a\n"
    assert notes.with_section("", "## T\n\n- a\n") == "## Release notes\n\n- a\n"
