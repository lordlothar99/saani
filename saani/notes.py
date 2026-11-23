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

import argparse

from . import comments, config, kpis

VERSION = re.compile(r"""^\s*["']?(?:__version__|version)["']?\s*[:=]\s*["']([^"']+)["']""", re.I | re.M)
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
            out.extend(f"  - {s}" for s in i.get("commits", []))
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


def commit_shas(issue_comments):
    """The commits the `On dev` comments (saani/comments.py) name, oldest first, without repeats: the `Commit` cell of their table."""
    heading = comments.KINDS["on-dev"][0]
    shas = []
    for body in issue_comments or []:
        text = (body.get("body") if isinstance(body, dict) else body) or ""
        if heading not in text:
            continue
        rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in text.splitlines() if line.strip().startswith("|")]
        if len(rows) < 3 or "Commit" not in rows[0]:
            continue
        cell = rows[2][rows[0].index("Commit")] if rows[0].index("Commit") < len(rows[2]) else ""
        for sha in re.findall(r"\b[0-9a-f]{7,40}\b", cell):
            if sha not in shas:
                shas.append(sha)
    return shas


def git(*args):
    """The output of a git command in the main checkout, or None when it fails (a commit that is not local)."""
    try:
        r = subprocess.run(["git", *args], cwd=cfg().root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=GH_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def commit_subject(sha):
    """The first line of a commit's message (never the trailers): from git, else from GitHub; None when neither knows it."""
    line = git("log", "-1", "--format=%s", sha)
    if line:
        return line
    try:
        message = gh("api", f"repos/{cfg().repo}/commits/{sha}", "--jq", ".commit.message")
    except GhFailed:
        return None
    return message.strip().splitlines()[0].strip() if message.strip() else None


def is_merge_subject(subject):
    return (subject or "").startswith("Merge ")


def pr_commits(pr):
    """The non-merge commits of a pull request (`gh pr view --json commits`), oldest first: [{"sha", "subject", "message"}].
    A merge is known by git when the commit is local, else by git's own subject ("Merge ...")."""
    commits = [{"sha": m.get("oid") or "", "subject": m.get("messageHeadline", ""), "message": f"{m.get('messageHeadline', '')}\n{m.get('messageBody', '')}"}
               for m in pr.get("commits", [])]
    merges = set()
    shas = [c["sha"] for c in commits if c["sha"]]
    for i in range(0, len(shas), 50):                                # in chunks: a long command line fails on Windows
        out = git("rev-list", "--merges", "--no-walk=unsorted", *shas[i:i + 50])
        merges.update((out or "").split())
    return [c for c in commits if c["sha"] not in merges and not is_merge_subject(c["subject"])]


def git_commits(number):
    """The non-merge commits on origin/<production> and origin/<integration> whose message refs the issue, oldest first. None: git failed."""
    c = cfg()
    out = git("log", "--no-merges", "--reverse", "--format=%H%x1f%s%x1f%B%x1e", "-E", f"--grep=#{number}([^0-9]|$)",
              f"origin/{c.production}", f"origin/{c.integration}")
    if out is None:
        return None
    found = []
    for record in out.split("\x1e"):
        parts = record.strip("\n").split("\x1f", 2)
        if len(parts) == 3 and number in kpis.issues_in(parts[2]):
            found.append({"sha": parts[0], "subject": parts[1], "message": parts[2]})
    return found


def commit_lines(row, pool=None):
    """The subjects of the non-merge commits that ref the issue (`refs #n`, as kpis.issues_in reads them), oldest first, without repeats
    and without the one that repeats the title. `pool`: the pull request's commits (`pr_commits`); else git's log of the production and
    integration branches. Only when that finds nothing, the commits named by the issue's `On dev` comments (a squash-merged repository);
    a merge commit is never shown."""
    number = row.get("number")
    found = [c for c in pool if number in kpis.issues_in(c["message"])] if pool is not None else git_commits(number)
    subjects = [c["subject"] for c in found or []]
    if not subjects:
        for sha in commit_shas(row.get("comments")):
            subject = commit_subject(sha)
            if subject is None:
                print(f"warning: #{number}: commit {sha} not found, left out", file=sys.stderr)
            elif not is_merge_subject(subject):
                subjects.append(subject)
    title = (row.get("title") or "").strip().casefold()
    out, seen = [], {title}
    for subject in subjects:
        if subject.casefold() not in seen:
            seen.add(subject.casefold())
            out.append(subject)
    return out


def issue_rows(numbers=None, milestone=None, short=False):
    c = cfg()
    fields = "number,title,body,state,labels" + ("" if short else ",comments")
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
    """(heading, issues, the pull request's data or None). `short`: no commit lines (and the comments are not read)."""
    c = cfg()
    short = getattr(args, "short", False)
    pr = None
    if args.pr is not None:
        pr = gh_json("pr", "view", str(args.pr), "--repo", c.repo, "--json", "body,commits") or {}
        text = [pr.get("body") or ""] + [f"{m.get('messageHeadline', '')}\n{m.get('messageBody', '')}" for m in pr.get("commits", [])]
        numbers = sorted({n for t in text for n in kpis.issues_in(t)})
        rows = issue_rows(numbers=numbers, short=short)
        heading = f"Release notes: pull request #{args.pr}"
    else:
        rows = issue_rows(milestone=args.milestone, short=short)
        heading = f"Release notes: {args.milestone}"
    if not rows:
        return heading, [], pr
    types = read_types()
    pool = None if pr is None or short else pr_commits(pr)
    issues = [{"number": r["number"], "title": r.get("title", ""), "body": r.get("body") or "", "type": types.get(r["number"]),
               "commits": [] if short else commit_lines(r, pool)} for r in rows]
    return heading, issues, pr


def via_file(text, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def release_exists(tag):
    """True when the GitHub release with exactly this tag exists; any gh error but "release not found" stops."""
    try:
        gh("release", "view", tag, "--repo", cfg().repo, "--json", "tagName")
    except GhFailed as e:
        if "release not found" not in e.text.lower():
            raise
        return False
    return True


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
    if not release_exists(args.milestone):
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


# ---------------------------------------------------------------- the GitHub release

class ReleaseFailed(SystemExit):
    """The release step failed; `.version` is the version being released (None when not yet known)."""
    def __init__(self, message, version=None):
        super().__init__(message)
        self.version = version


def tag_of(version):
    return version if version.startswith("v") else f"v{version}"


def version_of(text):
    """The version a version file holds (`__version__ = "1.2.3"`, `version = "1.2.3"`, `"version": "1.2.3"`), or None."""
    m = VERSION.search(text or "")
    return m.group(1) if m else None


def file_at(ref, path):
    """The text of a file of the repository at a commit, from GitHub; None when the file is not there."""
    try:
        return gh("api", "-H", "Accept: application/vnd.github.raw", f"repos/{cfg().repo}/contents/{path}?ref={ref}")
    except GhFailed as e:
        if "not found" in e.text.lower() or "404" in e.text:
            return None
        raise


def not_found(e):
    return "not found" in e.text.lower() or "404" in e.text


def production_head():
    """The commit the production branch points at now (a full SHA)."""
    return gh("api", f"repos/{cfg().repo}/commits/{cfg().production}", "--jq", ".sha").strip()


def tag_commit(tag):
    """The commit an existing tag points at (an annotated tag is peeled), or None when there is no such tag."""
    repo = cfg().repo
    try:
        obj = (gh_json("api", f"repos/{repo}/git/ref/tags/{tag}") or {}).get("object") or {}
        for _ in range(5):                                           # an annotated tag may point at another tag
            if obj.get("type") != "tag":
                break
            obj = (gh_json("api", f"repos/{repo}/git/tags/{obj.get('sha')}") or {}).get("object") or {}
    except GhFailed as e:
        if not_found(e):
            return None
        raise
    return obj.get("sha") or None


def same_commit(a, b):
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def create_release(version, target, notes):
    """The GitHub release `v<version>` with `notes` as its body. A release that exists: only its notes are edited. A tag without a release
    stays where it is: the release is made on the tag's commit, and a different `target` is refused. Otherwise it is made on `target`
    (default: the production branch's head, a SHA). A release is "latest" only when it is on the production head. A tag is never moved
    or deleted. Returns what it did."""
    tag, c = tag_of(version), cfg()
    path = via_file(notes, ".md")
    try:
        if release_exists(tag):
            gh("release", "edit", tag, "--repo", c.repo, "--notes-file", path)
            return f"the release {tag} exists: its notes are edited, its tag stays where it is"
        head = production_head()
        existing = tag_commit(tag)
        extra = []
        if existing:
            if target and not same_commit(existing, target):
                raise SystemExit(f"saani release create: the tag {tag} exists and points at {existing[:7]}, not at {target[:7]}: a tag is never moved; "
                                 f"leave out --target to make the release on {existing[:7]}, or delete the tag yourself")
            commit, where = existing, f"the existing tag's commit {existing[:7]}"
        else:
            commit = target or head
            extra = ["--target", commit]
            where = f"{commit[:7]}" + ("" if target else f" (the head of {c.production})")
        if not same_commit(commit, head):
            extra.append("--latest=false")
        gh("release", "create", tag, "--repo", c.repo, "--title", tag, "--notes-file", path, *extra)
        return f"created the release {tag} on {where}" + ("" if same_commit(commit, head) else " (not the latest: it is not the head of production)")
    finally:
        os.unlink(path)


def pr_notes(number):
    """The notes of the issues a pull request names, as the body of a release."""
    heading, issues, _ = collect(argparse.Namespace(pr=number, milestone=None, short=False))
    return render(heading, issues, list(cfg().types)) if issues else f"## {heading}\n\nThe pull request names no issue.\n"


def release_after_merge(number, merge_sha):
    """After the merge of pull request `number` into production (merge commit `merge_sha`): when `[release] version_file` changed its
    version, the GitHub release v<version> on the merge commit, with the notes of the pull request's issues. Prints what it did, returns
    the release's tag or None (nothing to release). A failure raises ReleaseFailed."""
    c = cfg()
    if not c.version_file:
        print("no release: [release] version_file is not set")
        return None
    version = None
    try:
        parent = gh("api", f"repos/{c.repo}/commits/{merge_sha}", "--jq", ".parents[0].sha").strip()
        before, after = version_of(file_at(parent, c.version_file)), version_of(file_at(merge_sha, c.version_file))
        if after is None:
            print(f"no release: no version found in {c.version_file}")
            return None
        if before == after:
            print(f"no release: the pull request does not change the version ({after}, {c.version_file})")
            return None
        version = after
        if release_exists(tag_of(version)):
            print(f"no release: {tag_of(version)} already exists (a revert?); its notes are left as they are")
            return None
        print(create_release(version, merge_sha, pr_notes(number)))
    except ReleaseFailed:
        raise
    except SystemExit as e:
        raise ReleaseFailed(str(e.code or e), version)
    except Exception as e:                                           # OSError from a temp file, gh data of an odd shape
        raise ReleaseFailed(f"{type(e).__name__}: {e}", version)
    return tag_of(version)


def cmd_create(args):
    sources = [x for x in (args.from_pr, args.milestone, args.notes_file) if x is not None]
    if len(sources) != 1:
        raise SystemExit("saani release create: name the notes with one of --from-pr N, --milestone TITLE, --notes-file FILE")
    if "" in (args.notes_file, args.milestone):
        raise SystemExit("saani release create: --notes-file and --milestone need a value, not an empty one")
    if args.notes_file is not None:
        try:
            with open(args.notes_file, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            raise SystemExit(f"saani release create: cannot read {args.notes_file}: {e}")
    elif args.from_pr is not None:
        text = pr_notes(args.from_pr)
    else:
        heading, issues, _ = collect(argparse.Namespace(pr=None, milestone=args.milestone, short=False))
        text = render(heading, issues, list(cfg().types)) if issues else f"## {heading}\n\nNo closed or in-main issue in this milestone.\n"
    print(create_release(args.version, args.target, text))
    return 0


def add_parsers(sub):
    p = sub.add_parser("release", help="release notes from the GitHub issues (`release notes`) and the GitHub releases (`release create`)")
    rs = p.add_subparsers(dest="what", required=True)
    n = rs.add_parser("notes", help="the notes of a milestone's issues, or of a pull request's (`--pr N`)")
    n.add_argument("milestone", nargs="?", help="the milestone's title")
    n.add_argument("--pr", type=int, help="the issues this pull request names with `refs #n` (body and commits) instead of a milestone")
    n.add_argument("--short", action="store_true", help="one line per issue: no commit lines under it")
    n.add_argument("--post", action="store_true",
                   help="write the notes to GitHub: with --pr, the pull request body's `## Release notes` section; with a milestone, the notes "
                        "of the release tagged with its title (must exist). Without it nothing is written")
    n.set_defaults(run=cmd_notes)
    c = rs.add_parser("create", help="create the GitHub release v<version> (or edit its notes when it exists; its tag is never moved)")
    c.add_argument("version", help="1.2.3 or v1.2.3: the tag is v1.2.3")
    c.add_argument("--target", help="the commit to tag (default: the head of the production branch, resolved to its SHA and printed); "
                                    "an existing tag is never moved: at another commit, it is refused")
    c.add_argument("--from-pr", type=int, help="notes: the issues of this pull request")
    c.add_argument("--milestone", help="notes: the issues of this milestone")
    c.add_argument("--notes-file", help="notes: this Markdown file")
    c.set_defaults(run=cmd_create)
