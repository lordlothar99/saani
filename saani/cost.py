"""Tokens and estimated cost per issue, role and model, read from Claude Code's session transcripts (docs/SAANI.md, KPIs).

Claude Code writes every session to `<config dir>/projects/<slug>/<sessionId>.jsonl` (the config dir is `~/.claude`, or CLAUDE_CONFIG_DIR),
its subagents to `<slug>/<sessionId>/subagents/agent-<agentId>.jsonl` with `agent-<agentId>.meta.json` beside it. The slug is the session's
folder with every character outside [A-Za-z0-9] turned into `-`. The project's folders are the ones whose slug starts with the main
checkout's slug or with the worktrees folder's slug.

Only numbers and ids are read: message id, model, usage, folder, branch, time, agent type and description. Never a message's text.
A message id seen on several lines is counted once.

An issue is found, first match: the worker branch (`task/12-words`), the worktree folder in the session's folder, `#12` in a subagent's
description; else "unattributed" (the main session's own overhead). A role is "architect" for Claude Code's main session, else the subagent's type
without its `saani:` prefix.
"""
import datetime
import json
import os
import pathlib
import re

UNATTRIBUTED = "unattributed"
HASH_NUMBER = re.compile(r"#(\d+)")
FIELDS = ("input", "output", "cache_read", "cache_write")

# USD per million tokens: input, output, cache read (source: Anthropic's model table, cached 2026-11-03). ESTIMATES: check against the
# provider's price page. Keyed by exact model id where listed, by family prefix for other versions; the longest matching prefix wins.
# A model that matches none is counted in tokens only (cost "?"). A cache write costs 1.25x the input price (5-minute cache) or 2x (1-hour).
PRICES = {
    "claude-opus-": (4.0, 20.0, 0.20),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-": (2.0, 10.0, 0.10),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-haiku-": (0.10, 0.50, 0.01),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-fable-": (8.0, 40.0, 0.20),
    "claude-fable-5-2": (8.0, 40.0, 0.20),
}
CACHE_WRITE_5M, CACHE_WRITE_1H = 1.25, 2.0


# ---------------------------------------------------------------- pure functions (tested)

def slug(path):
    """Claude Code's folder name for a working folder."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def price_of(model):
    best = max((p for p in PRICES if str(model).startswith(p)), key=len, default=None)
    return PRICES[best] if best else None


def cost_of(model, tokens):
    """Estimated USD of a {input, output, cache_read, cache_write, cache_write_1h} count (the 1-hour part is inside cache_write), or
    None for an unknown model."""
    p = price_of(model)
    if p is None:
        return None
    long = min(tokens.get("cache_write_1h", 0), tokens.get("cache_write", 0))
    short = tokens.get("cache_write", 0) - long
    return (tokens.get("input", 0) * p[0] + tokens.get("output", 0) * p[1] + tokens.get("cache_read", 0) * p[2]
            + short * p[0] * CACHE_WRITE_5M + long * p[0] * CACHE_WRITE_1H) / 1_000_000


def issue_of(branch, cwd, wt_name, description="", worker_branch=None):
    """The issue number a message belongs to, or UNATTRIBUTED. `worker_branch`: the project's compiled worker-branch pattern
    (Settings.worker_branch, number in group 2)."""
    m = worker_branch.match(branch or "") if worker_branch else None
    if m:
        return int(m.group(2))
    m = re.search(re.escape(wt_name) + r"[\\/](\d+)-", cwd or "") if wt_name else None
    if m:
        return int(m.group(1))
    m = HASH_NUMBER.search(description or "")
    return int(m.group(1)) if m else UNATTRIBUTED


def role_of(agent_type):
    """None (Claude Code's main session) is the role `architect`; a subagent is its type without the `saani:` prefix."""
    if not agent_type:
        return "architect"
    return agent_type[len("saani:"):] if agent_type.startswith("saani:") else agent_type


def in_window(stamp, since, until):
    try:
        return since <= datetime.date.fromisoformat(str(stamp)[:10]) <= until
    except ValueError:
        return False


def project_folders(projects_dir, main_checkout, wt_dir):
    """The transcript folders of the project: the main checkout's (its slug exactly) and the worktrees folder's (its slug exactly, or
    followed by `-`: a worktree). Sibling projects (`webshop-admin`) are left out. Case-insensitive: the drive letter's case differs."""
    main, wt = slug(main_checkout).lower(), slug(wt_dir).lower()
    try:
        entries = [d for d in pathlib.Path(projects_dir).iterdir() if d.is_dir()]
    except OSError:
        return []
    return sorted(d for d in entries if d.name.lower() in (main, wt) or d.name.lower().startswith(wt + "-"))


def read_lines(path):
    """The JSON objects of a jsonl file; broken lines are skipped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def read_meta(jsonl):
    """(agentType, description) from the meta file beside a subagent transcript."""
    meta = jsonl.with_name(jsonl.name[:-len(".jsonl")] + ".meta.json")
    try:
        d = json.loads(meta.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None, ""
    d = d if isinstance(d, dict) else {}
    kind, desc = d.get("agentType"), d.get("description")
    return (kind if isinstance(kind, str) and kind else None), (desc if isinstance(desc, str) else "")


def transcripts(folders):
    """[(path, is_subagent)] of every main and subagent transcript in the folders."""
    out = []
    for folder in folders:
        try:
            out += [(p, False) for p in sorted(folder.glob("*.jsonl"))]
            out += [(p, True) for p in sorted(folder.glob("*/subagents/agent-*.jsonl"))]
        except OSError:
            continue
    return out


def text_of(value):
    return value if isinstance(value, str) else ""


def count(u, key):
    v = u.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def row_of(line, role, description, wt_name, worker_branch):
    """The usage row of a transcript line, or None when it is not an assistant message with usage and a string id."""
    msg = line.get("message")
    if line.get("type") != "assistant" or not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
        return None
    if not isinstance(msg.get("id"), str) or not msg["id"]:
        return None
    u = msg["usage"]
    made = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    short, long = count(made, "ephemeral_5m_input_tokens"), count(made, "ephemeral_1h_input_tokens")
    total = count(u, "cache_creation_input_tokens") or short + long
    long = min(long, total)
    return {"id": msg["id"], "issue": issue_of(text_of(line.get("gitBranch")), text_of(line.get("cwd")), wt_name, description, worker_branch),
            "role": role, "model": text_of(msg.get("model")) or "?",
            "input": count(u, "input_tokens"), "output": count(u, "output_tokens"), "cache_read": count(u, "cache_read_input_tokens"),
            "cache_write": total, "cache_write_1h": long}


def old_file(path, since):
    """True when the file was last written before the window starts (it holds nothing of it)."""
    try:
        return datetime.date.fromtimestamp(path.stat().st_mtime) < since
    except (OSError, OverflowError, ValueError):
        return False


def usage_rows(files, wt_name, since, until, worker_branch=None):
    """One row per distinct message id, inside the window: {issue, role, model, input, output, cache_read, cache_write,
    cache_write_1h}. Of a message id that repeats, the line with the most output tokens is kept. Rows with no token are dropped."""
    best = {}
    for path, sub in files:
        if old_file(path, since):
            continue
        agent_type, description = read_meta(path) if sub else (None, "")
        role = (role_of(agent_type) if agent_type else "subagent") if sub else "architect"
        for line in read_lines(path):
            if not in_window(line.get("timestamp"), since, until):
                continue
            row = row_of(line, role, description, wt_name, worker_branch)
            if row and (row["id"] not in best or row["output"] > best[row["id"]]["output"]):
                best[row["id"]] = row
    return [r for r in best.values() if r["input"] + r["output"] + r["cache_read"] + r["cache_write"] > 0]


def _empty():
    return {"messages": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0, "unpriced": False}


def _add(table, key, row):
    t = table.setdefault(key, _empty())
    t["messages"] += 1
    for f in FIELDS:
        t[f] += row[f]
    c = cost_of(row["model"], row)
    if c is None:
        t["unpriced"] = True
    else:
        t["cost"] += c


def aggregate(rows, top=10):
    """Totals per issue (top `top` by cost), role and model, and overall. Costs are USD estimates; `unpriced` marks a total that
    holds tokens of a model with no price (its cost is then only the priced part)."""
    issues, roles, models, total = {}, {}, {}, {"all": _empty()}
    for r in rows:
        _add(issues, r["issue"], r)
        _add(roles, r["role"], r)
        _add(models, r["model"], r)
        _add(total, "all", r)

    def order(t):
        return sorted(t.items(), key=lambda kv: (-kv[1]["cost"], -sum(kv[1][f] for f in FIELDS), str(kv[0])))
    out = {"estimate": True, "prices_usd_per_million": {k: dict(zip(("input", "output", "cache_read"), v)) for k, v in PRICES.items()},
           "total": total["all"],
           "per_issue": [dict(issue=k, **v) for k, v in order(issues)[:top]], "issues_total": len(issues),
           "per_role": [dict(role=k, **v) for k, v in order(roles)],
           "per_model": [dict(model=k, **v) for k, v in order(models)]}
    for e in out["per_issue"] + out["per_role"] + out["per_model"] + [out["total"]]:
        e["cost"] = round(e["cost"], 4)
    return out


def money(e):
    if e["unpriced"]:
        return "?" if e["cost"] == 0 else f"${e['cost']:.2f}+?"
    return f"${e['cost']:.2f}"


def text(c):
    def line(label, e):
        return (f"    {label}: {money(e)}  in {e['input']:,}, out {e['output']:,}, cache read {e['cache_read']:,}, "
                f"cache write {e['cache_write']:,}")
    t = c["total"]
    head = "Tokens and cost (estimate; prices in saani/cost.py, check against the provider's price page)"
    if not t["messages"]:
        return head + "\n  no Claude Code transcript of this project in the window"
    lines = [head, line("total", t), f"  per issue (top {len(c['per_issue'])} of {c['issues_total']} by cost)"]
    lines += [line(f"#{e['issue']}" if isinstance(e["issue"], int) else e["issue"], e) for e in c["per_issue"]]
    lines.append("  per role")
    lines += [line(e["role"], e) for e in c["per_role"]]
    lines.append("  per model")
    lines += [line(e["model"], e) for e in c["per_model"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- files

def claude_dir():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return pathlib.Path(env) if env else pathlib.Path.home() / ".claude"


def read(c, since, until, top=10):
    """The cost report of the project settings `c` over the window."""
    files = transcripts(project_folders(claude_dir() / "projects", c.root, c.wt_dir))
    return aggregate(usage_rows(files, c.wt_dir.name, since, until, c.worker_branch), top)
