"""The "At a glance" table of an issue (the issue templates in .github/ISSUE_TEMPLATE, docs/SAANI.md): one pure parser, shared by the map
and the auto run. No network, no settings: it reads the Markdown of an issue body and says what the table says, or nothing."""
import re

ROWS = {"auto": "auto", "long run": "auto",       # "Long run" is the old name of the row: issues written before the rename still read
        "deploy": "deploy", "needs the owner": "needs_owner", "blocked by": "blocked_by", "parent": "parent"}
FITS = (("✅", "yes"), ("🟡", "partly"), ("⛔", "no"))
NONE = {"", "-", "—", "–", "none", "no", "n/a"}


def table(body):
    """{field name lower-cased: value text} of the first "At a glance" table of `body`; empty when the issue has none."""
    rows, inside = {}, False
    for line in (body or "").splitlines():
        s = line.strip()
        if re.match(r"^#{1,6}\s*at a glance\s*$", s, re.I):
            inside = True
        elif inside and s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 2 and cells[0].lower() not in ("field", "") and not set(cells[0]) <= set("-: "):
                rows.setdefault(cells[0].lower(), cells[1])
        elif inside and s.startswith("#"):
            break
    return rows


def issue_numbers(text):
    return [int(n) for n in re.findall(r"#(\d+)", text or "")]


def parse(body):
    """What the table of an issue body says. Keys that are not in the table are absent:
    auto {fit: yes|partly|no, why}, deploy (text or None), needs_owner {kind: answer|act|none, what}, blocked_by {issues, date, text},
    parent (issue number or None)."""
    rows = {ROWS[k]: v for k, v in table(body).items() if k in ROWS}
    out = {}
    if "auto" in rows:
        v = rows["auto"]
        fit = next((name for mark, name in FITS if mark in v), None) or next(
            (name for word, name in (("yes", "yes"), ("partly", "partly"), ("no", "no")) if v.lower().startswith(word)), None)
        out["auto"] = {"fit": fit, "why": v.split(":", 1)[1].strip() if ":" in v else re.sub(r"^[✅🟡⛔]\s*(yes|partly|no)?\s*", "", v, flags=re.I).strip(" ():")}
    if "deploy" in rows:
        out["deploy"] = None if rows["deploy"].lower().startswith("none") or rows["deploy"].lower() in NONE else rows["deploy"]
    if "needs_owner" in rows:
        v = rows["needs_owner"]
        kind = "answer" if v.lower().startswith("answer") else "act" if v.lower().startswith("act") else "none"
        out["needs_owner"] = {"kind": kind, "what": v.split(":", 1)[1].strip() if kind != "none" and ":" in v else ""}
    if "blocked_by" in rows:
        v = rows["blocked_by"]
        date = re.search(r"\d{4}-\d{2}-\d{2}", v)
        out["blocked_by"] = {"issues": issue_numbers(v), "date": date.group(0) if date else None,
                             "text": "" if v.lower() in NONE else v}
    if "parent" in rows:
        nums = issue_numbers(rows["parent"])
        out["parent"] = nums[0] if nums else None
    return out


def is_blocked(parsed, open_issues, today=None):
    """True while an issue the table names is still open, or its date has not come (`today` as YYYY-MM-DD)."""
    b = (parsed or {}).get("blocked_by") or {}
    if any(n in open_issues for n in b.get("issues", [])):
        return True
    return bool(b.get("date") and today and b["date"] > today)
