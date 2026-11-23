"""saani release notes <milestone> | --pr N [--post]: the release notes, written from the GitHub issues (docs/SAANI.md, "Release notes").

The issues of a milestone (closed, or labelled `[labels] in_main`), or those a pull request names (`refs #n` in its body and commits),
grouped by their GitHub issue Type (REST `type.name`, in the order of `[issues] types`), one line each: `#n Title: first sentence of the
issue's `## Result``, or the title alone without one. Nothing is written to GitHub without `--post`. A `gh` failure stops with gh's own
message, never an empty list. Standard library only; the pure functions are tested (tests/test_notes.py).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

from . import config, kpis

GH_TIMEOUT = 60
ISSUE_LIMIT = 1000
OTHER = "Other"
SECTION = "## Release notes"


def cfg():
    return config.current()


class GhFailed(SystemExit):
    """A `gh` call that ended with an error; `.text` is gh's message."""
    def __init__(self, args, text):
        super().__init__(f"saani release notes: gh {' '.join(args[:2])} failed: {text}")
        self.text = text


def gh(*args):
    """The output of `gh`; a failure (not installed, timeout, refused) stops the command with gh's own message."""
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GH_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise SystemExit("saani release notes: gh is not installed or not on the PATH")
    except subprocess.TimeoutExpired:
        raise SystemExit(f"saani release notes: gh {' '.join(args[:2])} took more than {GH_TIMEOUT} s")
    if r.returncode != 0:
        raise GhFailed(args, (r.stderr or r.stdout).strip())
    return r.stdout


def gh_json(*args):
    out = gh(*args)
    try:
        return json.loads(out or "null")
    except json.JSONDecodeError:
        raise SystemExit(f"saani release notes: gh {' '.join(args[:2])} gave an answer that is not JSON")


# ---------------------------------------------------------------- pure functions (tested)

RESULT_HEADING = re.compile(r"^(#{1,6})\s+Results?\s*:?\s*$", re.I)
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s")
ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "incl.", "no.")


def first_sentence(paragraph):
    """Up to the first `.`, `!` or `?` that is followed by a capital letter, a digit or a quote and does not end an abbreviation."""
    for m in re.finditer(r"[.!?]\s+(?=[A-Z0-9\"'(\[`])", paragraph):
        if not paragraph[:m.start() + 1].lower().endswith(ABBREVIATIONS):
            return paragraph[:m.start() + 1].strip()
    return paragraph.strip()


def result_sentence(body):
    """The first sentence under the issue's `## Result` section (also `## Results`, `## Result:`, any case), or "" when there is none or
    it starts with a table or a code fence. A subheading under it is passed over: the prose below it counts."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        found = RESULT_HEADING.match(line.strip())
        if not found:
            continue
        level = len(found.group(1))
        text = []
        for nxt in lines[i + 1:]:
            heading = HEADING.match(nxt)
            if heading:
                if len(heading.group(1)) <= level:
                    break
                if text:
                    break
                continue
            stripped = nxt.strip()
            if not stripped:
                if text:
                    break
                continue
            if stripped.startswith(("|", "```", "~~~")):
                if text:
                    break
                return ""
            text.append(re.sub(r"^(?:[-*]|\d+\.)\s+", "", stripped) if not text else stripped)
        return first_sentence(" ".join(text))
    return ""


def render(heading, issues, types):
    """Markdown: `issues` is [{number, title, body, type}]; groups follow `types` (Type names), then the untyped ones as "Other"."""
    order = list(types) + [OTHER]
    groups = {}
    for i in sorted(issues, key=lambda i: i["number"]):
        t = i.get("type") if i.get("type") in types else OTHER
        groups.setdefault(t, []).append(i)
    out = [f"## {heading}", ""]
    for t in order:
        if t not in groups:
            continue
        out.append(f"### {t}")
        for i in groups[t]:
            line = f"- #{i['number']} {i['title']}"
            sentence = result_sentence(i.get("body"))
            out.append(f"{line}: {sentence}" if sentence else line)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def with_section(body, notes):
    """A pull request body with its `## Release notes` section set to `notes` (replaced when there, else appended)."""
    section = f"{SECTION}\n\n" + "\n".join(notes.splitlines()[1:]).strip() + "\n"     # the notes' own heading line gives way to ours
    body = (body or "").replace("\r\n", "\n")
    m = re.search(r"^## Release notes[ \t]*$.*?(?=^## |\Z)", body, re.S | re.M)
    if m:
        return body[:m.start()] + section + ("\n" + body[m.end():] if body[m.end():] else "")
    return (body.rstrip() + "\n\n" if body.strip() else "") + section


# ---------------------------------------------------------------- reading the world

def read_types():
    """{issue number: Type name} of all the issues, from the REST API (`gh issue list` has no such field).
    Costly on a big repository (every issue of every state, 100 per request); it runs only when there is something to write."""
    out = gh("api", "--paginate", f"repos/{cfg().repo}/issues?state=all&per_page=100", "--jq",
             r'.[] | select(.pull_request | not) | "\(.number)\t\(.type.name // "")"')
    pairs = (line.split("\t") for line in out.splitlines() if "\t" in line)
    return {int(n): t for n, t in pairs if t}


def issue_rows(numbers=None, milestone=None):
    c = cfg()
    fields = "number,title,body,state,labels"
    if numbers is not None:
        rows = []
        for n in numbers:
            try:
                row = gh_json("issue", "view", str(n), "--repo", c.repo, "--json", fields + ",url")
            except GhFailed as e:
                print(f"warning: #{n} skipped, gh issue view failed: {e.text}", file=sys.stderr)
                continue
            if "/pull/" in (row or {}).get("url", ""):
                print(f"warning: #{n} skipped, it is a pull request, not an issue", file=sys.stderr)
                continue
            rows.append(row)
        return rows
    if milestone is not None:
        rows = gh_json("issue", "list", "--repo", c.repo, "--milestone", milestone, "--state", "all", "--limit", str(ISSUE_LIMIT), "--json", fields)
        rows = [r for r in rows or []
                if r.get("state") == "CLOSED" or c.labels["in_main"] in [x.get("name") for x in r.get("labels", [])]]
    return rows


def collect(args):
    """(heading, issues, the pull request's data or None)."""
    c = cfg()
    pr = None
    if args.pr is not None:
        pr = gh_json("pr", "view", str(args.pr), "--repo", c.repo, "--json", "body,commits") or {}
        text = [pr.get("body") or ""] + [f"{m.get('messageHeadline', '')}\n{m.get('messageBody', '')}" for m in pr.get("commits", [])]
        numbers = sorted({n for t in text for n in kpis.issues_in(t)})
        rows = issue_rows(numbers=numbers)
        heading = f"Release notes: pull request #{args.pr}"
    else:
        rows = issue_rows(milestone=args.milestone)
        heading = f"Release notes: {args.milestone}"
    if not rows:
        return heading, [], pr
    types = read_types()
    issues = [{"number": r["number"], "title": r.get("title", ""), "body": r.get("body") or "", "type": types.get(r["number"])} for r in rows]
    return heading, issues, pr


def via_file(text, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def post(args, notes, pr):
    """`--pr N`: the pull request body gets the `## Release notes` section. A milestone: the notes of the release whose tag is the
    milestone's title (`gh release edit`); no such release is created here (it would tag a commit), the command says so."""
    c = cfg()
    if pr is not None:
        path = via_file(with_section(pr.get("body"), notes), ".md")
        try:
            gh("pr", "edit", str(args.pr), "--repo", c.repo, "--body-file", path)
        finally:
            os.unlink(path)
        print(f"posted: the {SECTION} section of pull request #{args.pr}")
        return 0
    try:
        gh("release", "view", args.milestone, "--repo", c.repo, "--json", "tagName")
        found = True
    except GhFailed as e:
        if "release not found" not in e.text.lower():
            raise
        found = False
    if not found:
        print(f"not posted: no GitHub release with the tag {args.milestone}; create it (the tag marks a commit), then run this again", file=sys.stderr)
        return 1
    path = via_file(notes, ".md")
    try:
        gh("release", "edit", args.milestone, "--repo", c.repo, "--notes-file", path)
    finally:
        os.unlink(path)
    print(f"posted: the notes of the release {args.milestone}")
    return 0


def cmd_notes(args):
    if args.pr is None and not args.milestone:
        raise SystemExit("saani release notes: name a milestone, or a pull request with --pr N")
    if args.pr is not None and args.milestone:
        raise SystemExit("saani release notes: a milestone or --pr, not both")
    heading, issues, pr = collect(args)
    if not issues:
        if pr is not None:
            print(f"## {heading}\n\nThe pull request names no issue (refs #n).")
        else:
            print(f"## {heading}\n\nNo closed or {cfg().labels['in_main']} issue in this milestone.")
        return 0
    notes = render(heading, issues, list(cfg().types))
    print(notes, end="")
    if args.post:
        return post(args, notes, pr)
    return 0


def add_parsers(sub):
    p = sub.add_parser("release", help="release notes from the GitHub issues: `release notes <milestone>` or `--pr N`")
    p.add_argument("what", choices=["notes"])
    p.add_argument("milestone", nargs="?", help="the milestone's title")
    p.add_argument("--pr", type=int, help="the issues this pull request names with `refs #n` (body and commits) instead of a milestone")
    p.add_argument("--post", action="store_true",
                   help="write the notes to GitHub: with --pr, the pull request body's `## Release notes` section; with a milestone, the notes "
                        "of the release tagged with its title (must exist). Without it nothing is written")
    p.set_defaults(run=cmd_notes)
