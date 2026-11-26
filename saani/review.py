"""The review cache (docs/SAANI.md, Review cache): a review records the commit and the hash of the diff it read, and the next review of
the same branch reads only the commits after it.

  saani review diff <n> [--worktree PATH] [--base REF]   the diff to review: since the last recorded review of issue <n>, or all of it

`saani comment <n> review` writes the record as two fields of the `In review` comment (`Reviewed commit`, `Diff hash`). The record is on
the issue (GitHub is the one record); nothing is kept locally. Standard library only.
"""
import hashlib
import re

from . import comments, config, core

COMMIT, DIGEST = "Reviewed commit", "Diff hash"
# Code that is always reviewed whole, however little changed since the last review: the guard, landing, CI, merge and deploy.
ALWAYS_WHOLE = re.compile(r"(guard|land|deploy|merge|\.github/workflows)|(^|[/._-])ci([/._-]|$)", re.I)


def cfg():
    return config.current()


def base_ref():
    return f"origin/{cfg().integration}"


def worktree_of(issue, path=None):
    """The worktree to read: the one `--worktree PATH` names, else the worktree of issue `issue`'s branch (<type>/<n>-<slug>), else None."""
    if path:
        return core.named_worktree(path)
    for folder, branch in core.worktrees():
        m = cfg().worker_branch.match(branch or "")
        if m and m.group(2) == str(issue):
            return folder
    return None


def whole_diff(cwd, base):
    """The whole diff of the branch against its base, the text `Diff hash` hashes."""
    return core.git("diff", f"{base}...HEAD", cwd=cwd)


def diff_hash(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def record_fields(cwd, base=None):
    """The fields a review comment carries: the commit at HEAD and the hash of the whole diff."""
    return {COMMIT: core.git("rev-parse", "--short=12", "HEAD", cwd=cwd), DIGEST: diff_hash(whole_diff(cwd, base or base_ref()))}


def last_record(bodies):
    """The commit of the latest `In review` comment that has one, or None (no review recorded)."""
    heading = comments.KINDS["review"][0]
    found = None
    for body in bodies:
        lines = body.splitlines()
        if not lines or not lines[0].startswith(heading) or len(lines) < 4:
            continue
        row = lambda s: [x.strip() for x in re.split(r"(?<!\\)\|", s.strip().strip("|"))]  # noqa: E731
        fields = dict(zip(row(lines[1]), row(lines[3])))
        if re.fullmatch(r"[0-9a-f]{7,40}", fields.get(COMMIT, "")):
            found = fields[COMMIT]
    return found


def plan(cwd, bodies, base=None):
    """What to review. {"mode": "whole" | "since" | "none", "since": the commit or None, "why": a sentence, "diff": the text, "whole": [files
    that are always reviewed whole], "whole_diff": their diff}. `whole`: no record, or its commit is no longer in the branch (rebased
    away, unknown); `since`: the commits after the recorded one; `none`: HEAD is the recorded commit."""
    base = base or base_ref()
    sha = last_record(bodies)
    full = whole_diff(cwd, base)
    if not sha:
        mode, since, why, text = "whole", None, "no earlier review is recorded on the issue", full
    elif core.git("merge-base", "--is-ancestor", sha, "HEAD", cwd=cwd, check=False).returncode != 0:
        mode, since, why, text = "whole", None, f"the reviewed commit {sha} is no longer in the branch (rebased away)", full
    elif core.git("rev-parse", "HEAD", cwd=cwd) == core.git("rev-parse", sha, cwd=cwd):
        mode, since, why, text = "none", sha, f"nothing new since the review at {sha}", ""
    else:
        mode, since, why = "since", sha, f"only the commits after the review at {sha} (commits {sha}..HEAD)"
        text = core.git("diff", f"{sha}..HEAD", cwd=cwd)
    files = core.git("diff", "--name-only", f"{base}...HEAD", cwd=cwd).splitlines()
    always = [f for f in files if ALWAYS_WHOLE.search(f)] if mode != "whole" else []
    extra = core.git("diff", f"{base}...HEAD", "--", *always, cwd=cwd) if always else ""
    return {"mode": mode, "since": since, "why": why, "diff": text, "whole": always, "whole_diff": extra, "hash": diff_hash(full)}


def render(p):
    """The text the reviewer reads: the range first (to name it in the review), then the diff."""
    out = [f"# Review range: {p['mode']}: {p['why']}.",
           f"# Whole diff hash: {p['hash']}"]
    if p["mode"] == "since":
        out.append("# Not re-read: the commits up to and including the earlier review; re-read in full a file they covered that these commits touch.")
    out += ["", p["diff"]]
    if p["whole"]:
        out += ["", "# Always reviewed whole (guard, landing, CI, merge, deploy): " + ", ".join(p["whole"]), "", p["whole_diff"]]
    return "\n".join(out).rstrip() + "\n"


def cmd_review_diff(args):
    cwd = worktree_of(args.issue, args.worktree) or core.named_worktree(None)
    _, bodies = comments.issue_state(args.issue)
    print(render(plan(cwd, bodies, args.base)), end="")
    return 0


def add_parsers(sub):
    p = sub.add_parser("review", help="the review cache: the diff a review reads (since the last recorded review of the issue)")
    rsub = p.add_subparsers(dest="review_cmd", required=True)
    d = rsub.add_parser("diff", help="print the diff to review: the commits after the issue's last recorded review, or the whole diff")
    d.add_argument("issue", type=int)
    d.add_argument("--worktree", help="the issue's worktree instead of the current folder's (a subagent's cd does not last)")
    d.add_argument("--base", help="the base to diff against (default origin/<integration branch>)")
    d.set_defaults(run=cmd_review_diff)
