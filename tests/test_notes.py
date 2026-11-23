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
