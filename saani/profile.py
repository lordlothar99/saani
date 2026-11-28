"""Where an issue is: the profile of each place of the map and the one resolver (docs/SAANI.md, "Where an issue is"). Standard library only;
pure functions (tests/test_profile.py).

Each place an issue can sit in, from the Wayfarers' Tents to the Citadel, expects a value of each signal Saani reads (PROFILE, one row per
place, one cell per signal; ANY where the signal does not matter). `resolve(signals)` gives the issue's place, a confidence (`sure` when
every signal read agrees with the place's profile, `probable` otherwise), the signals that agree and the ones that contradict. Every reader
goes through it: the board's columns, queues and kanban places (saani/board_server.py `column_for`, `queue_for`, `kanban_place`; the map's
`normalize()` takes the place the server gives each card), `saani auto pick` (saani/auto.py), and the labels `land`, `merge` and
`ci merged` set (`arrival`). The map's "?" tooltips and its "Places and their rules" popup are `describe()`; docs/SAANI.md holds `markdown()`.

How the place is found:
1. an issue labelled `idea` with no worktree is off the map (None);
2. the facts narrow the places, in this order: the worktree and branch (tests running, landing tests green, work, an empty worktree, a
   session), the live checks of a released milestone, the issue's state, the labels that wait for the owner;
3. then the signals in the order of trust (`[pipeline]` in saani.toml; default: the GitHub status, then git, then the labels), then the
   release pull request, the deploy and the milestone. Each step keeps the places that accept the signal's value; a signal no place left
   accepts is set aside: it contradicts the place found;
4. of the places left, the first in PRECEDENCE.
With an order of trust set per place, each place is tried with its own order; of the places that win under their own order, the first in
PRECEDENCE. The owner's latest manual change (Saani's log) is not read yet: it comes with the structured log (#128).
"""
import collections

from . import config
from . import status as issue_status

ANY = None

# id, name, stage: the places that hold issues, in the map's flow order (the map's ZONES, the kanban's columns; the Hollow holds none)
PLACES = (
    ("backlog", "Wayfarers' Tents", "Backlog"),
    ("todo", "The Granary", "Todo"),
    ("you", "Council Rock", "Waiting for you"),
    ("works", "The Workshops", "In progress"),
    ("proving", "Proving Ground", "Testing"),
    ("yard", "The Loading Yard", "Ready to land"),
    ("depot", "Caravan Depot", "Integrated"),
    ("tower", "The Refinery", "Release PR"),
    ("pad", "Launch Pad", "To deploy"),
    ("archive", "The Archive", "To close"),
    ("outpost", "The Far Outpost", "Live checks"),
    ("citadel", "The Citadel", "Done"),
)
IDS = tuple(p[0] for p in PLACES)
NAME = {p[0]: p[1] for p in PLACES}
# when several places are left after every signal, the first of these
PRECEDENCE = ("proving", "yard", "outpost", "citadel", "archive", "pad", "tower", "depot", "you", "works", "todo", "backlog")

# id, title, read from, {value: words}. Words name labels and branches as {in_dev}, {int}...: `describe()` puts the project's own names in.
SIGNALS = (
    ("state", "Issue", "the issue: open, or closed and why",
     {"open": "open", "completed": "closed as completed", "not_planned": "closed as not planned or as a duplicate"}),
    ("worktree", "Worktree and branch", "the worktrees and worker branches, the test queue and the Claude Code sessions on this PC",
     {"none": "none", "session": "a Claude session on it (no worktree)", "empty": "an empty worktree (just started)",
      "work": "a worktree with commits not landed or edits not committed", "green": "a clean worktree whose landing tests are green for this very commit",
      "testing": "tests running in its worktree (`saani test`), or the final check of `saani land`"}),
    ("checks", "Live checks", "the issue's title and milestone, and the other issues of that milestone",
     {"released": "an open issue titled \"Live checks ...\" whose milestone's release is merged: every other issue of the milestone is closed "
                  "or labelled {in_main}, and, with a `[deploy] command`, none is still To deploy",
      "no": "not the live checks of a released milestone"}),
    ("need", "Waits for you", "the labels {answer} and {act}",
     {"yes": "{answer} (your answer) or {act} (your action)", "no": "neither {answer} nor {act}"}),
    ("status", "GitHub status", "the issue's GitHub status and its category (not read on a repo without statuses)",
     {"none": "no status", "backlog": "Backlog", "todo": "Todo", "started": "In progress", "review": "In review", "done": "Done",
      "closed": "Not planned or Duplicate"}),
    ("git", "Git", "the commits `refs #n` on {int} that {prod} does not have yet",
     {"none": "no commit `refs #n` waiting on {int}", "integration": "commits `refs #n` on {int}, not yet on {prod}", "production": "its commits are on {prod}"}),
    ("labels", "Labels", "the labels {in_dev} and {in_main}",
     {"none": "neither {in_dev} nor {in_main}", "in_dev": "{in_dev}", "in_main": "{in_main}"}),
    ("pr", "Release pull request", "the open {int} -> {prod} pull request (its commits and description)",
     {"carried": "carried by the open release pull request (`refs #n` or `Closes #n`)", "not": "not carried by an open release pull request"}),
    ("deploy", "Deploy", "`[deploy] paths` and `[deploy] command`",
     {"to_deploy": "something to deploy (a commit changes a file under `[deploy] paths`, or the project sets no paths)",
      "to_close": "nothing to deploy: none of its commits changes a file under `[deploy] paths`"}),
    ("milestone", "Milestone", "the issue's milestone", {"yes": "a milestone", "no": "no milestone"}),
    ("log", "Saani's log", "the structured log of the stage changes (#128): not read yet", {}),
)
SIGNAL_IDS = tuple(s[0] for s in SIGNALS)
TITLE = {s[0]: s[1] for s in SIGNALS}
WORDS = {s[0]: s[3] for s in SIGNALS}
FACTS_FIRST = ("worktree", "checks", "state", "need")
TRUSTED = ("status", "git", "labels")       # the signals an order of trust ranks ([pipeline] in saani.toml)
DEFAULT_TRUST = ("status", "git", "labels")
FACTS_LAST = ("pr", "deploy", "milestone")

QUIET = ("none", "session", "empty")        # nothing being built in a worktree
STARTED = ("started", "review", "none")     # the category started (one started status on some repos), or no status yet
# A cell is ANY (left out), the values it accepts, or {"default": ..., "deploy": ...} (the project has a `[deploy] command`) /
# {"default": ..., "no_statuses": ...} (the repo has no GitHub statuses, or `[status] enabled` is off).
PROFILE = {
    "backlog": {"state": ("open",), "worktree": ("none",), "checks": ("no",), "need": ("no",), "status": ("none", "backlog", "done", "closed"),
                "git": ("none",), "labels": ("none",), "milestone": {"default": ANY, "no_statuses": ("no",)}},
    "todo": {"state": ("open",), "worktree": ("none",), "checks": ("no",), "need": ("no",), "status": ("todo", "started", "review"),
             "git": ("none",), "labels": ("none",), "milestone": {"default": ANY, "no_statuses": ("yes",)}},
    "you": {"state": ("open",), "worktree": QUIET, "checks": ("no",), "need": ("yes",), "git": ("none",), "labels": ("none",)},
    "works": {"state": ("open",), "worktree": ("work", "empty", "session"), "status": STARTED, "labels": ("none",)},
    "proving": {"worktree": ("testing",)},
    "yard": {"worktree": ("green",)},
    "depot": {"state": ("open",), "worktree": QUIET, "checks": ("no",), "status": STARTED, "git": ("integration",), "labels": ("in_dev",),
              "pr": ("not",)},
    "tower": {"state": ("open",), "worktree": QUIET, "checks": ("no",), "status": STARTED, "git": ("integration",), "labels": ("in_dev",),
              "pr": ("carried",)},
    "pad": {"state": {"default": ("open",), "deploy": ("open", "completed")}, "worktree": QUIET, "checks": ("no",), "status": STARTED + ("done",),
            "git": ("production",), "labels": ("in_main",), "deploy": ("to_deploy",)},
    "archive": {"state": ("open",), "worktree": QUIET, "checks": ("no",), "status": STARTED, "git": ("production",), "labels": ("in_main",),
                "deploy": ("to_close",)},
    "outpost": {"state": ("open",), "worktree": QUIET, "checks": ("released",)},
    "citadel": {"state": ("completed", "not_planned"), "worktree": QUIET, "status": ("done", "closed", "none"), "labels": ("none",)},
}
# what moves an issue on, and what else to know, per place ({int}, {in_main}...: the project's names)
NEXT = {
    "backlog": "You set its status to Todo (or, without statuses, give it a milestone): it moves to The Granary.",
    "todo": "`saani start <n>`, or an auto run, creates its worktree and branch: it moves to The Workshops.",
    "you": "You answer and remove the label: it goes back to where its work stands (The Granary or The Workshops).",
    "works": "Committed and tests started (`saani test`, `saani land`): Proving Ground. Landed: Caravan Depot.",
    "proving": "Green: The Loading Yard. A failure: back to The Workshops.",
    "yard": "`saani land` merges it into {int}: Caravan Depot. A new commit resets the tests: back to The Workshops.",
    "depot": "The release pull request {int} -> {prod} names it: The Refinery.",
    "tower": "`saani merge` merges it into {prod}: the issues it `Closes` are closed, the others get {in_main}.",
    "pad": "`saani deploy` removes {in_main}: a closed issue moves to The Citadel.",
    "archive": "You close it, citing the merge commit: it moves to The Citadel.",
    "outpost": "You walk through the checklist on the live system and close the issue: it moves to The Citadel.",
    "citadel": "",
}
NOTE = {
    "proving": "Tests running win over every other signal.",
    "tower": "The beacon is that pull request's CI: green, red, or yellow while it runs. Without an open pull request this place is empty.",
    "outpost": "The live checks of a released milestone win over the issue's labels and status. Before the release the issue stays in its normal place.",
    "citadel": "Closed in the last 7 days; the done issues of closed milestones are left out.",
}
# the stage labels in the order an issue gets them; `arrival` reads the profile of the place an issue reaches
STAGE_LABELS = ("in_dev", "in_main")
ARRIVAL_PLACES = ("depot", "tower", "pad", "archive", "citadel")
# a board column of saani/board_server.py for each place (a Workshops card with only a session on it stays in `ready`)
COLUMN = {"backlog": "ready", "todo": "ready", "you": "you", "works": "building", "proving": "testing", "yard": "tested", "depot": "dev",
          "tower": "dev", "pad": "main", "archive": "close", "outpost": "checks", "citadel": "live"}

Result = collections.namedtuple("Result", "place confidence agree contradict")


def cfg():
    return config.current()


# ---------------------------------------------------------------- the settings: the order of trust

def check_trust(value, where):
    """The order of trust `value` (a list of signal names) completed with the signals it leaves out, in the default order; an unknown
    signal, a repeat or anything but a list of names is refused with the allowed names."""
    allowed = ", ".join(TRUSTED)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SystemExit(f"{config.FILE}: {where} must be a list of signals, e.g. [\"labels\", \"status\", \"git\"] (allowed: {allowed})")
    for v in value:
        if v not in TRUSTED:
            later = " (the owner's latest manual change is read once Saani logs its stage changes, #128)" if v in ("log", "owner") else ""
            raise SystemExit(f"{config.FILE}: {where}: unknown signal `{v}` (allowed: {allowed}){later}")
    if len(set(value)) != len(value):
        raise SystemExit(f"{config.FILE}: {where} names a signal twice (allowed, once each: {allowed})")
    return tuple(value) + tuple(s for s in DEFAULT_TRUST if s not in value)


def check_places(value, where):
    """{place: order} from `[pipeline] place`, each order completed (check_trust); an unknown place is refused with the allowed ones."""
    if not isinstance(value, dict):
        raise SystemExit(f"{config.FILE}: {where} must be a table of place = [signals], e.g. depot = [\"labels\", \"git\", \"status\"]")
    for place in value:
        if place not in IDS:
            raise SystemExit(f"{config.FILE}: {where}: unknown place `{place}` (allowed: {', '.join(IDS)})")
    return {place: check_trust(order, f"{where} {place}") for place, order in value.items()}


def trust_of(place, c=None):
    """The order of trust of a place: its own (`[pipeline] place`), else the project's (`[pipeline] trust`)."""
    c = c or cfg()
    return tuple(c.trust_by_place.get(place) or c.trust)


# ---------------------------------------------------------------- the profile and the resolver

def cell(place, signal, c=None, statuses=True):
    """What `place` expects of `signal`: ANY, or the values it accepts, for this project (`statuses`: the status is read)."""
    raw = PROFILE[place].get(signal, ANY)
    if isinstance(raw, dict):
        if "deploy" in raw and (c or cfg()).deploy_command:
            return raw["deploy"]
        if "no_statuses" in raw and not statuses:
            return raw["no_statuses"]
        return raw["default"]
    return raw


def accepts(place, signal, value, c=None, statuses=True):
    expected = cell(place, signal, c, statuses)
    return expected is ANY or value in expected


def resolve(signals, c=None, among=None):
    """The place of an issue from its `signals` ({signal: value}, a value None or a signal left out: not read): a Result (place,
    confidence, agree, contradict), or None for an `idea` with no worktree (off the map). `among`: only these places (a queue)."""
    c = c or cfg()
    if signals.get("idea") and signals.get("worktree") in (None, "none", "session"):
        return None
    statuses = signals.get("status") is not None
    places = [p for p in PRECEDENCE if among is None or p in among]

    def narrow(order):
        left = places
        for s in FACTS_FIRST + tuple(order) + FACTS_LAST:
            value = signals.get(s)
            if value is None:
                continue
            kept = [p for p in left if accepts(p, s, value, c, statuses)]
            if kept:
                left = kept
        return left[0]
    winners = []
    for order in dict.fromkeys(trust_of(p, c) for p in places):
        won = narrow(order)
        if trust_of(won, c) == order:              # a place wins only under its own order of trust
            winners.append(won)
    place = min(winners, key=PRECEDENCE.index) if winners else narrow(tuple(c.trust))
    read = [s for s in SIGNAL_IDS if signals.get(s) is not None]
    agree = tuple(s for s in read if cell(place, s, c, statuses) is not ANY and accepts(place, s, signals[s], c, statuses))
    contradict = tuple(s for s in read if not accepts(place, s, signals[s], c, statuses))
    return Result(place, "probable" if contradict else "sure", agree, contradict)


def not_planned(issue):
    """True for an issue closed as not planned or as a duplicate (its state reason, or its GitHub status category)."""
    return (issue.get("state_reason") or "").upper() in ("NOT_PLANNED", "DUPLICATE") or \
        ((issue.get("status") or {}).get("category") in ("not_planned", "duplicate"))


def worktree_value(worktree, testing=False, session=False):
    """The worktree signal: testing, green, work, empty, session or none."""
    if testing:
        return "testing"
    if worktree and worktree.get("green") and worktree.get("ahead") and not worktree.get("dirty"):
        return "green"
    if worktree and (worktree.get("ahead") or worktree.get("dirty")):
        return "work"
    if worktree:
        return "empty"
    return "session" if session else "none"


def read(issue, *, worktree=None, testing=False, session=False, checks=False, closing=False, carried=False, git=None, statuses_on=None,
         statuses=None, c=None):
    """The signals of an issue ({signal: value}) from what the caller has read: its worktree ({ahead, dirty, green} or None), tests
    running, a session on it, the live checks of a released milestone, nothing to deploy, carried by the release pull request, `git`
    ("integration" when commits `refs #n` wait on the integration branch, None when not read). `statuses_on`: the repo has statuses (None:
    the issue's own status says); `statuses`: the repo's list, to tell In review from In progress by category."""
    c = c or cfg()
    lab = {**config.Config().labels, **(c.labels or {})}
    have = set(issue.get("labels") or [])
    if statuses_on is None:
        statuses_on = issue_status.enabled() and bool(issue.get("status"))
    state = "open" if issue.get("state", "OPEN") == "OPEN" else ("not_planned" if not_planned(issue) else "completed")
    return {
        "state": state,
        "worktree": worktree_value(worktree, testing, session),
        "checks": "released" if checks else "no",
        "need": "yes" if {lab["answer"], lab["act"]} & have else "no",
        "status": (issue_status.step_of(issue.get("status"), statuses) or "none") if statuses_on else None,
        "git": git,
        "labels": "in_main" if lab["in_main"] in have else "in_dev" if lab["in_dev"] in have else "none",
        "pr": "carried" if carried else "not",
        "deploy": "to_close" if closing else "to_deploy",
        "milestone": "yes" if issue.get("milestone") else "no",
        "idea": lab["idea"] in have,
    }


def column_of(result, signals):
    """The board column (saani/board_server.py COLUMNS) of a resolved place; a Workshops card with only a session on it stays Ready."""
    if result is None:
        return None
    if result.place == "works" and signals.get("worktree") == "session":
        return "ready"
    return COLUMN[result.place]


def landed(result, signals):
    """"dev" or "main" when the place, or a signal that contradicts it, says the issue's work is already on the integration branch or on
    production (`saani auto pick` takes no such issue); else None."""
    if result is None:
        return None
    if result.place in ("pad", "archive"):
        return "main"
    if result.place in ("depot", "tower"):
        return "dev"
    for s in result.contradict:
        if signals.get(s) in ("in_main", "production"):
            return "main"
        if signals.get(s) in ("in_dev", "integration"):
            return "dev"
    return None


def arrival(place):
    """(label key to add or None, [label keys to remove]) when an issue reaches `place`: the stage label its profile expects, and the
    stage labels of the earlier stages it does not expect (`land` -> Caravan Depot, `merge` / `ci merged` -> Launch Pad, or The Citadel for
    an issue the merge closed in a project without a deploy)."""
    if place not in ARRIVAL_PLACES:
        raise ValueError(f"no label change on arrival at {place} (one of {', '.join(ARRIVAL_PLACES)})")
    expected = PROFILE[place]["labels"]
    add = next((v for v in expected if v in STAGE_LABELS), None)
    upto = STAGE_LABELS.index(add) if add else len(STAGE_LABELS)
    return add, [k for k in STAGE_LABELS[:upto] if k not in expected]


# ---------------------------------------------------------------- the words: the map's rules, the docs' table

def names(c=None, generic=False):
    """The project's label and branch names for the words."""
    c = c or cfg()
    lab = {**config.Config().labels, **(c.labels or {})}
    q = lambda s: f"`{s}`"  # noqa: E731
    return {"in_dev": q(lab["in_dev"]), "in_main": q(lab["in_main"]), "answer": q(lab["answer"]), "act": q(lab["act"]), "idea": q(lab["idea"]),
            "int": "the integration branch" if generic else q(c.integration), "prod": "production" if generic else q(c.production)}


def words(signal, values, n):
    if values is ANY:
        return "any"
    parts = [WORDS[signal][v].format(**n) for v in values]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " or " + parts[-1]


def cell_words(place, signal, n, c=None, statuses=True, generic=False):
    """The words of a cell: for this project, or (generic) with both variants of a conditional cell."""
    raw = PROFILE[place].get(signal, ANY)
    if generic and isinstance(raw, dict):
        when, variant = next((k, v) for k, v in raw.items() if k != "default")
        cond = {"deploy": "with a `[deploy] command`", "no_statuses": "a repo without statuses"}[when]
        return f"{words(signal, raw['default'], n)} ({cond}: {words(signal, variant, n)})"
    return words(signal, cell(place, signal, c, statuses), n)


def trust_words(order):
    return ", then ".join(TITLE[s] for s in order)


def wins(c=None, n=None):
    """The help popup's "Which place wins": how the resolver decides, in plain words."""
    c = c or cfg()
    n = n or names(c)
    per_place = "; ".join(f"{NAME[p]}: {trust_words(o)}" for p, o in c.trust_by_place.items())
    return [
        f"Labelled {n['idea']} with no worktree: not on the map at all.",
        "First the facts, in this order, each keeping the places that fit it: the worktree and branch (tests running: Proving Ground; landing "
        "tests green: The Loading Yard; commits or edits: The Workshops), the live checks of a released milestone (The Far Outpost), the "
        f"issue's state (closed: The Citadel, or Launch Pad), the labels {n['answer']} and {n['act']} (Council Rock).",
        f"Then the signals in the order of trust: {trust_words(tuple(c.trust))}" + (f" ({per_place})" if per_place else "") +
        " (`[pipeline]` in saani.toml). A signal that fits none of the places left is set aside: it contradicts the place, which is then "
        "probable, not sure.",
        "Then the release pull request (The Refinery or Caravan Depot), the deploy (Launch Pad or The Archive) and, without GitHub statuses, "
        "the milestone (The Granary or the Wayfarers' Tents).",
        "Of the places left, the first of: " + ", ".join(NAME[p] for p in PRECEDENCE) + ".",
    ]


def describe(c=None, statuses_on=True):
    """The profile as the map shows it (`profile` in /api/state and in the sample): per place its rows [signal title, words] for every
    signal it does not leave to ANY, its note, what moves it on and its order of trust; the signals; and "Which place wins"."""
    c = c or cfg()
    n = names(c)
    shown = [s for s in SIGNAL_IDS if statuses_on or s != "status"]
    places = []
    for place, name, stage in PLACES:
        rows = [[TITLE[s], cell_words(place, s, n, c, statuses_on)] for s in shown if cell(place, s, c, statuses_on) is not ANY]
        places.append({"id": place, "name": name, "stage": stage, "rows": rows, "note": NOTE.get(place, "").format(**n),
                       "next": NEXT[place].format(**n), "trust": [TITLE[s] for s in trust_of(place, c)]})
    return {"signals": [{"id": s, "title": t, "from": f.format(**n)} for s, t, f, _ in SIGNALS], "statuses": bool(statuses_on),
            "places": places, "wins": wins(c, n)}


def markdown():
    """The profile table of docs/SAANI.md (generic names, both variants of a conditional cell): one row per place, one column per signal."""
    n = names(config.Config(), generic=True)
    lines = ["| Place | " + " | ".join(TITLE[s] for s in SIGNAL_IDS) + " |", "|---" * (len(SIGNAL_IDS) + 1) + "|"]
    for place, name, stage in PLACES:
        lines.append(f"| {name} ({stage}) | " + " | ".join(cell_words(place, s, n, generic=True) for s in SIGNAL_IDS) + " |")
    return "\n".join(lines)
