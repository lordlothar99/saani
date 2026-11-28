"""Status comments (.github/COMMENT_KINDS.md): one comment per status change on an issue, and the label change that goes with it.

  saani comment <n> <kind> [--text TEXT] [--field KEY=VALUE ...] [--dry-run]

A comment of a kind in STATUS_STEP also moves the issue's GitHub status forward (saani/status.py). A comment is its heading, then the fields as a one-row Markdown table (the keys are the header), then the text. `start`, `land`,
`ci pr` and `ci merged` post theirs with `post()`. Standard library only; `gh` is the only thing it talks to, through `Direct` (or
the `io` a caller hands in: ci.py's `Run`, which has the same `json` and `write`).
"""
import json
import re
import subprocess

from . import config, profile, status

# kind: (heading, label key to add, label key to remove)
KINDS = {
    "started": ("### ▶ Started", None, None),
    "review": ("### 🔎 In review", None, None),
    "blocked": ("### ⛔ Blocked", None, None),
    "question": ("### ❓ Question", "answer", None),
    "progress": ("### ⚙ Progress", None, None),
    "on-dev": ("### ✅ On dev", None, None),
    "in-pr": ("### 📦 In PR", None, None),
    "on-main": ("### 🚀 On main", None, None),
    "deployed": ("### ✔ Deployed", None, None),
    "linked": ("### 🔗 Linked", None, None),
}
# kind: the place a comment of that kind says the issue reached; its label change is that place's arrival (saani/profile.py): On dev ->
# Caravan Depot (in-dev added), On main -> Launch Pad (in-main added, in-dev removed)
ARRIVES = {"on-dev": "depot", "on-main": "pad"}

# kind: the GitHub issue status step it moves the issue to (saani/status.py), only forward; the others leave the status alone.
# Deploy needs none: the merge of the release pull request closes the issue (`Closes #n`), which sets Done.
STATUS_STEP = {"started": "started", "review": "review", "on-dev": "review", "on-main": "review"}
GH_TIMEOUT = 120                     # seconds: a stalled gh never hangs a command


def cfg():
    return config.current()


def render(kind, fields=None, text=""):
    """The comment: heading, the fields as a one-row table, the text."""
    lines = [KINDS[kind][0]]
    if fields:
        cell = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
        lines += ["| " + " | ".join(cell(k) for k in fields) + " |", "|" + "---|" * len(fields),
                  "| " + " | ".join(cell(v) for v in fields.values()) + " |"]
    if text:
        lines += [""] if fields else []
        lines.append(text)
    return "\n".join(lines)


def label_changes(kind):
    """(label to add or None, [labels to remove]) for the kind, named as the project's `[issues] labels` has them: the arrival at the place
    the kind reports (ARRIVES), else the kind's own label."""
    labels = cfg().labels
    if kind in ARRIVES:
        add, remove = profile.arrival(ARRIVES[kind])
    else:
        _, add, one = KINDS[kind]
        remove = [one] if one else []
    return (labels.get(add) if add else None), [labels.get(k) for k in remove]


def parse_fields(pairs):
    """{key: value} from `KEY=VALUE` strings, in order."""
    fields = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"--field {pair!r}: use KEY=VALUE")
        fields[key.strip()] = value.strip()
    return fields


class Direct:
    """gh run for real (or printed, in a dry run). A failing or missing gh stops with a SystemExit that says why."""

    def __init__(self, dry=False):
        self.dry = dry

    def read(self, *cmd):
        try:
            r = subprocess.run(list(cmd), cwd=cfg().root, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                               timeout=GH_TIMEOUT)
        except OSError as e:
            raise SystemExit(f"cannot run {cmd[0]}: {e}")
        except subprocess.TimeoutExpired:
            raise SystemExit(f"{' '.join(cmd[:3])} took more than {GH_TIMEOUT} s")
        if r.returncode:
            raise SystemExit("failed: " + " ".join(cmd[:4]) + "\n" + (getattr(r, "stderr", "") or "").strip())
        return (getattr(r, "stdout", "") or "").strip()

    def json(self, *cmd):
        out = self.read(*cmd)
        return json.loads(out) if out else None

    def write(self, *cmd):
        if self.dry:
            print("dry-run: " + " ".join(cmd))
            return ""
        return self.read(*cmd)


def issue_state(number, io=None):
    """({labels}, [comment bodies]) of the issue, in one read."""
    io = io or Direct()
    data = io.json("gh", "issue", "view", str(number), "--repo", cfg().repo, "--json", "labels,comments") or {}
    return ({x.get("name") for x in data.get("labels") or []}, [c.get("body") or "" for c in data.get("comments") or []])


def has_comment(bodies, kind, needle):
    """True when one of the comment bodies has the kind's heading and `needle` (a pull request as `#12`, not as a prefix of `#120`)."""
    pattern = re.compile(re.escape(needle) + r"(?!\d)")
    return any(KINDS[kind][0] in b and pattern.search(b) for b in bodies)


TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")   # the authorAssociation of a comment a decision may come from


def trusted_bodies(rows):
    """The bodies of the comments (gh's `comments` rows, in posting order) written by the repo's owner or a collaborator: any GitHub user can
    comment a public repo, and a decision is passed on to coders, so the others' comments are never read as decisions."""
    return [r.get("body") or "" for r in rows or [] if r.get("authorAssociation") in TRUSTED]


def decision_of(bodies):
    """The text of the latest `Decision` of an issue (#102): the last Progress comment (bodies are in posting order) whose one-row table has
    a `Decision` column (`saani comment <n> progress --field Decision=<text>`), None when there is none or its cell is empty."""
    found = None
    heading = KINDS["progress"][0]
    for body in bodies:
        lines = body.splitlines()
        if not lines or lines[0].strip() != heading or len(lines) < 3:
            continue
        split = lambda line: [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]  # noqa: E731
        head, row = split(lines[1]), split(lines[3] if len(lines) > 3 else "")
        if "Decision" in head and len(row) == len(head) and row[head.index("Decision")]:
            found = row[head.index("Decision")]
    return found


def apply_labels(number, kind, io=None):
    """The kind's label change, one `gh issue edit` (nothing when the kind has none)."""
    add, remove = label_changes(kind)
    if add or remove:
        cmd = ["gh", "issue", "edit", str(number), "--repo", cfg().repo]
        for label in remove:
            cmd += ["--remove-label", label]
        if add:
            cmd += ["--add-label", add]
        (io or Direct()).write(*cmd)


def post(number, kind, fields=None, text="", io=None, labels=True, once=None, needs=None):
    """Comment issue `number` with a comment of `kind`, then apply the kind's label change (`labels=False`: the caller did).
    `once`: a needle; nothing is posted (False returned) when a comment of the issue already has the heading and the needle.
    `needs`: label names; nothing is posted unless the issue carries one of them. Both share one read of the issue.
    Returns True when posted. A refusal by gh raises SystemExit."""
    io = io or Direct()
    if once or needs:
        have, bodies = issue_state(number, io)
        if (needs and not have & set(needs)) or (once and has_comment(bodies, kind, once)):
            return False
    io.write("gh", "issue", "comment", str(number), "--repo", cfg().repo, "--body", render(kind, fields, text))
    if labels:
        apply_labels(number, kind, io)
    if kind in STATUS_STEP:
        status.advance(number, STATUS_STEP[kind], io)
    if kind != "on-dev" and not getattr(io, "dry", False):      # a landing is journaled by `saani land` itself
        from . import auto
        auto.journal(number, kind)
    return True


def try_post(number, kind, fields=None, text="", io=None, **kw):
    """`post`, but a refusal only prints a warning: a status comment never undoes the work it reports. True when posted."""
    try:
        return post(number, kind, fields, text, io, **kw)
    except SystemExit as e:
        print(f"could not comment #{number} ({KINDS[kind][0].split(' ', 1)[1]}): {e}")
        return False


def review_fields(fields, issue, worktree):
    """The fields of a `review` comment plus the record of the review cache (saani/review.py): the commit at HEAD of the issue's worktree
    (`--worktree`, else the worktree of the issue's branch) and the hash of its whole diff. A given field wins; no worktree found: the
    fields as they are (a review comment without a record)."""
    from . import review
    cwd = review.worktree_of(issue, worktree)
    return {**review.record_fields(cwd), **fields} if cwd else fields


def cmd_comment(args):
    fields = parse_fields(args.field)
    worktree = getattr(args, "worktree", None)
    if worktree and args.kind != "review":
        raise SystemExit("--worktree is for the review kind only (it names the worktree whose commit the comment records)")
    if args.kind == "review":
        fields = review_fields(fields, args.issue, worktree)
    post(args.issue, args.kind, fields, args.text or "", Direct(args.dry_run))
    return 0


def add_parsers(sub):
    p = sub.add_parser("comment", help="comment an issue with a status kind (.github/COMMENT_KINDS.md) and set its label")
    p.add_argument("issue", type=int)
    p.add_argument("kind", choices=list(KINDS))
    p.add_argument("--text", help="the body, under the fields")
    p.add_argument("--field", action="append", metavar="KEY=VALUE", help="a column of the one-row table (repeatable)")
    p.add_argument("--worktree", help="review only: the worktree whose HEAD and diff hash the comment records (saani review diff reads them)")
    p.add_argument("--dry-run", action="store_true", help="print the gh commands, post nothing")
    p.set_defaults(run=cmd_comment)
