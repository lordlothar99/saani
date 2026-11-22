"""saani/glance.py: the At a glance table of an issue, read the same way for the map and the long run."""
from saani import glance

BODY = """## At a glance
| Field | Value |
|---|---|
| Long run | 🟡 Partly: the data model yes; the layout follows the owner's review |
| Deploy | None (Saani itself is a tool on the owner's PC) |
| Needs the owner | Act: choose the notification channel |
| Blocked by | #13, #17 |
| Parent | #6 |

## Context
| Long run | ✅ Yes |
"""


def test_the_table_is_read_row_by_row():
    p = glance.parse(BODY)
    assert p["long_run"] == {"fit": "partly", "why": "the data model yes; the layout follows the owner's review"}
    assert p["deploy"] is None
    assert p["needs_owner"] == {"kind": "act", "what": "choose the notification channel"}
    assert p["blocked_by"] == {"issues": [13, 17], "date": None, "text": "#13, #17"}
    assert p["parent"] == 6                                  # a later table or row named like one never overrides the first


def test_each_answer_of_each_row_has_its_meaning():
    row = lambda name, value: glance.parse(f"## At a glance\n| Field | Value |\n|---|---|\n| {name} | {value} |\n")
    assert row("Long run", "✅ Yes")["long_run"]["fit"] == "yes"
    assert row("Long run", "⛔ No: changes the guard")["long_run"] == {"fit": "no", "why": "changes the guard"}
    assert row("Deploy", "restart the server")["deploy"] == "restart the server"
    assert row("Needs the owner", "No (decided)")["needs_owner"] == {"kind": "none", "what": ""}
    assert row("Needs the owner", "Answer: which channel?")["needs_owner"] == {"kind": "answer", "what": "which channel?"}
    assert row("Blocked by", "—")["blocked_by"] == {"issues": [], "date": None, "text": ""}
    assert row("Blocked by", "2026-12-01")["blocked_by"]["date"] == "2026-12-01"
    assert row("Parent", "—")["parent"] is None


def test_an_issue_without_the_table_says_nothing():
    assert glance.parse("Just some words\n| a | b |") == {} and glance.parse(None) == {}


def test_blocked_while_a_named_issue_is_open_or_the_date_is_ahead():
    p = glance.parse(BODY)
    assert glance.is_blocked(p, {17, 20}) and not glance.is_blocked(p, {20})
    dated = glance.parse("## At a glance\n| Field | Value |\n|---|---|\n| Blocked by | 2026-12-01 |\n")
    assert glance.is_blocked(dated, set(), "2026-11-22") and not glance.is_blocked(dated, set(), "2026-12-02")
    assert not glance.is_blocked({}, {1})
