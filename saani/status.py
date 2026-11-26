"""GitHub's issue statuses (Backlog, Todo, In progress, In review, Done, Not planned, Duplicate), set by Saani's steps (docs/SAANI.md).

An issue's status is a property of the issue itself (no GitHub Project, no `project` scope): GitHub put it in public preview on
2026-11-23. Each status has a category (`backlog`, `planned`, `started`, `completed`, `not_planned`, `duplicate`) that decides whether
the issue is open or closed. The list is the repository's (GraphQL `repository.planningStatuses`, `null` where statuses are not
available); names may change, so a step is matched by its configured name first, then by its category.

Saani's steps (the decision of #72), the status only ever moving forward:
  start                                   -> started  (In progress)
  the review starts, land, merge          -> review   (In review); `in-dev` / `in-main` say where the change waits
  the release pull request's `Closes #n` -> Done (GitHub sets it when the merge closes the issue; `saani deploy` closes nothing)
`saani auto pick` takes every ready issue except those in `backlog` (Backlog) when the repo has statuses. `[status] enabled = "off"`, or a repo without statuses:
nothing here does anything (a step says so in one line), and Saani works from its labels alone. `gh` has no status flag (2.92): reads and writes go through `gh api`.

  saani status sync [--apply]    give each open issue the status its state implies (a dry run without --apply)
"""
import json

from . import config

STEPS = ("backlog", "todo", "started", "review", "done")
CATEGORY = {"backlog": "backlog", "todo": "planned", "started": "started", "review": "started", "done": "completed"}
CLOSED = {"completed", "not_planned", "duplicate"}
QUERY_STATUSES = "query($o:String!,$r:String!){repository(owner:$o,name:$r){planningStatuses(first:50){nodes{id name category{key}}}}}"
QUERY_ISSUE = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){id state status{id name category{key}}}}}"
MUTATION = "mutation($i:ID!,$s:ID!){setIssueStatus(input:{issueId:$i,statusId:$s}){issue{number status{name}}}}"

_cache = {}


def cfg():
    return config.current()


def io_default():
    from . import comments
    return comments.Direct()


def graphql(io, query, **variables):
    """`gh api graphql` with typed variables (-F: numbers stay numbers); the `data` of the answer."""
    cmd = ["gh", "api", "graphql", "-f", "query=" + query]
    for k, v in variables.items():
        cmd += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    return ((io.json(*cmd) or {}).get("data")) or {}


def enabled():
    return cfg().status_enabled != "off"


def available(io=None, strict=False):
    """The repo's statuses [{id, name, category}] in display order, or None: off in saani.toml or not available for the repo (both read
    once per process). When gh fails, the answer is unknown: `strict` stops with gh's message (a command that decides from the statuses,
    like `auto pick`, never guesses), else None for this call only (bookkeeping that never fails its command)."""
    if not enabled():
        return None
    repo = cfg().repo
    if repo not in _cache:
        owner, name = repo.split("/", 1)
        try:
            nodes = ((graphql(io or io_default(), QUERY_STATUSES, o=owner, r=name).get("repository") or {})
                     .get("planningStatuses") or {}).get("nodes")
        except (SystemExit, OSError, ValueError) as e:
            if strict:
                raise SystemExit(f"could not read the repo's issue statuses: {e}")
            return None
        _cache[repo] = [{"id": s["id"], "name": s["name"], "category": (s.get("category") or {}).get("key")} for s in nodes] if nodes else None
    return _cache[repo]


def resolve(step, statuses):
    """The status {id, name, category} for a step: the configured name (any case), else the category's status (review: the last of the
    `started` ones, the others: the first), else None."""
    name = (cfg().status_names.get(step) or "").casefold()
    for s in statuses or []:
        if s["name"].casefold() == name:
            return s
    same = [s for s in statuses or [] if s["category"] == CATEGORY[step]]
    return (same[-1] if step == "review" else same[0]) if same else None


def step_of(status, statuses=None):
    """The step a status {name, category} stands for (one of STEPS, `closed` for not planned and duplicate), or None for no status."""
    if not status:
        return None
    names = {(cfg().status_names.get(s) or "").casefold(): s for s in STEPS}
    if (status.get("name") or "").casefold() in names:
        return names[status["name"].casefold()]
    cat = status.get("category")
    if cat == "started":                      # an unnamed started status: review if it is the last started one of the repo
        started = [s["name"] for s in statuses or [] if s["category"] == "started"]
        return "review" if started and status.get("name") == started[-1] and len(started) > 1 else "started"
    return {"backlog": "backlog", "planned": "todo", "completed": "done"}.get(cat, "closed" if cat in CLOSED else None)


def rank(step):
    """How far along a step is; a closed issue (done, not planned, duplicate) is the end."""
    return STEPS.index(step) if step in STEPS else (len(STEPS) if step == "closed" else -1)


def plan(current, step, statuses):
    """The status to set to move an issue at `current` ({name, category} or None) to `step`, or (None, why not)."""
    target = resolve(step, statuses)
    if not target:
        return None, f"the repo has no status for {step}"
    if current and current.get("name") == target["name"]:
        return None, f"already {target['name']}"
    now = step_of(current, statuses)
    if current and now is None:                   # a status Saani does not know (a new category in the preview): the owner's, left
        return None, f"{current.get('name')} is not one of Saani's steps"
    if now is not None and rank(now) >= rank(step):
        return None, f"already {current['name']}"
    return target, None


def advance(number, step, io=None):
    """Move issue `number` to the step's status, only forward (an In review issue is never put back In progress, a closed one never
    reopened). Never fails the calling command: a refusal, and a no-op (statuses off, not available, unreadable, or the `plan()` reason),
    is printed as one line. In a dry run it prints the status it would set and writes nothing. Returns the status name set, or None."""
    io = io or io_default()
    statuses = available(io)
    if not statuses:
        if not enabled():
            why = "issue statuses are off in saani.toml ([status] enabled)"
        elif cfg().repo in _cache:
            why = "the repo has no issue statuses"
        else:
            why = "the repo's issue statuses could not be read"
        print(f"status of #{number} unchanged ({step}): {why}")
        return None
    owner, name = cfg().repo.split("/", 1)
    try:
        issue = (graphql(io, QUERY_ISSUE, o=owner, r=name, n=int(number)).get("repository") or {}).get("issue") or {}
        if not issue.get("id"):
            print(f"status of #{number} unchanged ({step}): the issue was not found")
            return None
        current = issue.get("status") and {"name": issue["status"]["name"], "category": (issue["status"].get("category") or {}).get("key")}
        target, why = plan(current, step, statuses)
        if not target:
            print(f"status of #{number} unchanged ({step}): {why}")
            return None
        if getattr(io, "dry", False):
            print(f"dry-run: #{number} status -> {target['name']}")
        else:
            io.write("gh", "api", "graphql", "-f", "query=" + MUTATION, "-f", f"i={issue['id']}", "-f", f"s={target['id']}")
        return target["name"]
    except (SystemExit, OSError, ValueError) as e:
        print(f"could not set the status of #{number} ({step}): {e}")
        return None


def from_rest(raw):
    """{name, category} from the REST issue's `status` ({"id", "name", "category"}), or None."""
    return {"name": raw.get("name"), "category": raw.get("category")} if isinstance(raw, dict) and raw.get("name") else None


# ---------------------------------------------------------------- saani status sync

def sync_target(issue, started):
    """The step an open issue's state implies: a worktree -> started; in-dev / in-main -> review; a milestone -> todo; else backlog."""
    labels, have = cfg().labels, set(issue.get("labels") or [])
    if have & {labels["in_dev"], labels["in_main"]}:
        return "review"
    if issue["number"] in started:
        return "started"
    return "todo" if issue.get("milestone") else "backlog"


def sync_plan(issues, started, statuses):
    """[(number, from name or None, to name, step, why)] for the open issues whose status should move forward; the others are left."""
    out = []
    for i in sorted(issues, key=lambda i: i["number"]):
        step = sync_target(i, started)
        current = i.get("status")
        if current and step in ("todo", "backlog"):
            continue                                       # a status set by hand on an issue nobody works on is the owner's
        target, _ = plan(current, step, statuses)
        if target:
            why = {"review": "on dev or main", "started": "a worktree", "todo": "a milestone", "backlog": "no status yet"}[step]
            out.append((i["number"], current and current["name"], target["name"], step, why))
    return out


def read_open_issues(io):
    """The open issues (not pull requests) with their labels, milestone and status, from the REST API."""
    out = io.read("gh", "api", "--paginate", f"repos/{cfg().repo}/issues?state=open&per_page=100", "--jq",
                  r'.[] | select(.pull_request | not) | {number, labels: [.labels[].name], milestone: .milestone.title, status}')
    return [{**r, "status": from_rest(r.get("status"))} for r in (json.loads(line) for line in out.splitlines() if line.strip())]


def cmd_sync(args):
    from . import auto, core
    io = io_default()
    statuses = available(io, strict=True)
    if not statuses:
        print("saani status sync: issue statuses are " + ("off in saani.toml ([status] enabled)" if not enabled()
                                                          else "not available for this repo: nothing to do"))
        return 0
    c = cfg()
    folders = [p.name for p in c.wt_dir.iterdir()] if c.wt_dir.is_dir() else []
    branches = core.git("branch", "-a", "--format=%(refname:short)", check=False).stdout.splitlines()
    started, _ = auto.started_issues(folders, branches)
    rows = sync_plan(read_open_issues(io), set(started), statuses)
    for n, old, new, _, why in rows:
        print(f"  #{n:<5} {old or 'no status':12} -> {new:12} ({why})")
    if not rows:
        print("every open issue already has its status")
        return 0
    if not args.apply:
        print(f"{len(rows)} issue(s) would move: run again with --apply")
        return 0
    moved = sum(1 for n, _, _, step, _ in rows if advance(n, step, io))
    print(f"{moved} of {len(rows)} issue(s) moved")
    return 0 if moved == len(rows) else 1

