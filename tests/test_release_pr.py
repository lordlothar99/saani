"""saani release pr (saani/notes.py): the integration -> production pull request, made by a script; gh and git are faked."""
import argparse
import dataclasses
import json

import pytest

from saani import config, notes

GLANCE_YES = "## At a glance\n| Field | Value |\n|---|---|\n| Auto | ✅ Yes (small) |\n\n## Result\nThe cart shows the total. More."
GLANCE_PARTLY = "## At a glance\n| Field | Value |\n|---|---|\n| Auto | 🟡 Partly: the owner checks it |\n\n## Result\nThe mail is sent."


class Fake:
    def __init__(self, open_pr=None, versions=("1.3.0", "1.2.0"), milestones=("v1.3", "v1.3")):
        self.open_pr, self.versions, self.calls, self.body = open_pr, versions, [], None
        self.milestones = milestones
        self.issues = {1: ("OPEN", GLANCE_YES, milestones[0]), 2: ("OPEN", GLANCE_PARTLY, milestones[1]), 3: ("CLOSED", GLANCE_YES, None)}

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "api" and args[1] == "-H":
            ref = args[3].rsplit("ref=", 1)[1]
            version = self.versions[0] if ref == "dev" else self.versions[1]
            return "" if version is None else f'version = "{version}"\n'
        if args[:2] == ("issue", "view"):
            state, body, milestone = self.issues[int(args[2])]
            return json.dumps({"number": int(args[2]), "title": f"Issue {args[2]}", "state": state, "body": body, "labels": [], "comments": [],
                               "milestone": {"title": milestone} if milestone else None, "url": f"https://github.com/acme/webshop/issues/{args[2]}"})
        if args[:2] == ("api", "--paginate"):
            return "1\tFeature\n2\tFeature\n3\tBug\n"
        if args[:2] == ("pr", "list"):
            return json.dumps([{"number": self.open_pr, "url": "u"}] if self.open_pr else [])
        if args[:2] in (("pr", "create"), ("pr", "edit")):
            self.body = open(args[args.index("--body-file") + 1], encoding="utf-8").read()
            return "https://github.com/acme/webshop/pull/9\n"
        raise AssertionError(args)


LOG = ("a" * 40 + "\x1fCart total\x1fCart total\n\nrefs #1\x1e" + "b" * 40 + "\x1fMail\x1fMail\n\nrefs #2\x1e"
       + "c" * 40 + "\x1fDone one\x1fDone one\n\nrefs #3\x1e")


@pytest.fixture
def world(monkeypatch):
    def make(**kw):
        fake = Fake(**kw)
        monkeypatch.setattr(notes, "gh", fake)
        monkeypatch.setattr(notes, "git", lambda *a: "" if a[0] == "fetch" else LOG)
        return fake
    config.use(dataclasses.replace(config.current(), version_file="saani/__init__.py"))
    return make


def run(*argv):
    p = argparse.ArgumentParser()
    notes.add_parsers(p.add_subparsers(dest="cmd"))
    args = p.parse_args(["release", "pr", *argv])
    return args.run(args)


def test_opens_the_pull_request_with_notes_and_closes_and_refs_lines(world, capsys):
    fake = world()
    assert run() == 0
    create = next(c for c in fake.calls if c[:2] == ("pr", "create"))
    assert create[create.index("--title") + 1] == "Release v1.3.0"
    assert create[create.index("--milestone") + 1] == "v1.3"
    assert create[create.index("--base") + 1] == "main" and create[create.index("--head") + 1] == "dev"
    assert fake.body.startswith("## Release notes\n")
    assert "- #1 Issue 1: The cart shows the total." in fake.body
    assert fake.body.rstrip().endswith("Closes #1\nrefs #2")                  # the closed #3 is left out; Partly: refs only
    assert "#3" not in fake.body
    assert "opened https://github.com/acme/webshop/pull/9" in capsys.readouterr().out


def test_updates_the_open_pull_request_and_never_opens_a_second(world):
    fake = world(open_pr=7)
    assert run() == 0
    assert not any(c[:2] == ("pr", "create") for c in fake.calls)
    edit = next(c for c in fake.calls if c[:2] == ("pr", "edit"))
    assert edit[2] == "7" and edit[edit.index("--title") + 1] == "Release v1.3.0"


def test_no_milestone_when_the_issues_do_not_share_one(world):
    fake = world(milestones=("v1.3", "v1.4"))
    run()
    assert "--milestone" not in next(c for c in fake.calls if c[:2] == ("pr", "create"))


@pytest.mark.parametrize("versions", [("1.2.0", "1.2.0"), ("1.2.0", "1.10.0"), (None, "1.2.0")])
def test_refuses_unless_the_version_is_higher_than_productions(world, versions):
    fake = world(versions=versions)
    with pytest.raises(SystemExit) as e:
        run()
    assert "next:" in str(e.value)
    assert not any(c[:2] in (("pr", "create"), ("pr", "edit")) for c in fake.calls)


def test_a_version_compares_by_its_numbers(world):
    world(versions=("1.10.0", "1.9.0"))
    assert run("--dry-run") == 0


def test_without_version_file_there_is_no_check_and_it_says_so(world, capsys):
    world(versions=("1.2.0", "1.2.0"))
    config.use(dataclasses.replace(config.current(), version_file=""))
    assert run("--dry-run") == 0
    out = capsys.readouterr().out
    assert "no version check: [release] version_file is not set" in out
    assert "title: Release: dev to main" in out


def test_dry_run_prints_and_writes_nothing(world, capsys):
    fake = world(open_pr=7)
    assert run("--dry-run") == 0
    out = capsys.readouterr().out
    assert "title: Release v1.3.0" in out and "issues: #1, #2" in out and "Closes #1" in out and "refs #2" in out
    assert not any(c[:2] in (("pr", "create"), ("pr", "edit")) for c in fake.calls)


def test_nothing_to_release_is_refused(world, monkeypatch):
    world()
    monkeypatch.setattr(notes, "git", lambda *a: "")
    with pytest.raises(SystemExit) as e:
        run()
    assert "nothing to release" in str(e.value)
