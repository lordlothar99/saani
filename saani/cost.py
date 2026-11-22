"""Tokens and estimated cost per issue, role and model, read from Claude Code's session transcripts (docs/SAANI.md, KPIs).

Claude Code writes every session to `<config dir>/projects/<slug>/<sessionId>.jsonl` (the config dir is `~/.claude`, or CLAUDE_CONFIG_DIR),
its subagents to `<slug>/<sessionId>/subagents/agent-<agentId>.jsonl` with `agent-<agentId>.meta.json` beside it. The slug is the session's
folder with every character outside [A-Za-z0-9] turned into `-`. The project's folders are the ones whose slug starts with the main
checkout's slug or with the worktrees folder's slug.

Only numbers and ids are read: message id, model, usage, folder, branch, time, agent type and description. Never a message's text.
A message id seen on several lines is counted once.

An issue is found, first match: the worker branch (`task/12-words`), the worktree folder in the session's folder, `#12` in a subagent's
description; else "unattributed" (the Architect's own overhead). A role is "architect" for a main session, else the subagent's type
without its `saani:` prefix.
"""
import datetime
import json
import os
import pathlib
import re

UNATTRIBUTED = "unattributed"
BRANCH = re.compile(r"^[a-z]+/(\d+)-")
HASH_NUMBER = re.compile(r"#(\d+)")
FIELDS = ("input", "output", "cache_read", "cache_write")

# USD per million tokens: input, output, cache read, cache write. ESTIMATES: check against the provider's price page.
# Keyed by model family prefix; the longest matching prefix wins. A model that matches none is counted in tokens only (cost "?").
PRICES = {
    "claude-opus-": (15.0, 75.0, 1.50, 18.75),
    "claude-sonnet-": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-": (1.0, 5.0, 0.10, 1.25),
    "claude-fable-": (1.0, 5.0, 0.10, 1.25),
}


# ---------------------------------------------------------------- pure functions (tested)

def slug(path):
    """Claude Code's folder name for a working folder."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def price_of(model):
    best = max((p for p in PRICES if str(model).startswith(p)), key=len, default=None)
    return PRICES[best] if best else None


def cost_of(model, tokens):
    """Estimated USD of a {input, output, cache_read, cache_write} count, or None for an unknown model."""
    p = price_of(model)
    if p is None:
        return None
    return sum(tokens.get(f, 0) * rate for f, rate in zip(FIELDS, p)) / 1_000_000


def issue_of(branch, cwd, wt_name, description=""):
    """The issue number a message belongs to, or UNATTRIBUTED."""
    m = BRANCH.match(branch or "")
    if m:
        return int(m.group(1))
    m = re.search(re.escape(wt_name) + r"[\\/](\d+)-", cwd or "") if wt_name else None
    if m:
        return int(m.group(1))
    m = HASH_NUMBER.search(description or "")
    return int(m.group(1)) if m else UNATTRIBUTED


def role_of(agent_type):
    """None (a main session) is the architect; a subagent is its type without the `saani:` prefix."""
    if not agent_type:
        return "architect"
    return agent_type[len("saani:"):] if agent_type.startswith("saani:") else agent_type


def in_window(stamp, since, until):
    try:
        return since <= datetime.date.fromisoformat(str(stamp)[:10]) <= until
    except ValueError:
        return False


def project_folders(projects_dir, main_checkout, wt_dir):
    """The transcript folders of the project: its main checkout and its worktrees."""
    prefixes = [slug(main_checkout).lower(), slug(wt_dir).lower()]      # the drive letter's case differs between tools
    projects_dir = pathlib.Path(projects_dir)
    if not projects_dir.is_dir():
        return []
    return sorted(d for d in projects_dir.iterdir() if d.is_dir() and any(d.name.lower().startswith(p) for p in prefixes))


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
    return d.get("agentType") or None, str(d.get("description") or "")


def transcripts(folders):
    """[(path, is_subagent)] of every main and subagent transcript in the folders."""
    out = []
    for folder in folders:
        out += [(p, False) for p in sorted(folder.glob("*.jsonl"))]
        out += [(p, True) for p in sorted(folder.glob("*/subagents/agent-*.jsonl"))]
    return out


def usage_rows(files, wt_name, since, until):
    """One row per distinct message id: {issue, role, model, input, output, cache_read, cache_write}, inside the window."""
    seen, rows = set(), []
    for path, sub in files:
        agent_type, description = read_meta(path) if sub else (None, "")
        role = (role_of(agent_type) if agent_type else "subagent") if sub else "architect"
        for line in read_lines(path):
            msg = line.get("message")
            if line.get("type") != "assistant" or not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            mid = msg.get("id")
            if mid in seen or not in_window(line.get("timestamp"), since, until):
                continue
            if mid:
                seen.add(mid)
            u = msg["usage"]

            def num(k):
                return u[k] if isinstance(u.get(k), int) else 0
            rows.append({"issue": issue_of(line.get("gitBranch"), line.get("cwd"), wt_name, description), "role": role,
                         "model": str(msg.get("model") or "?"),
                         "input": num("input_tokens"), "output": num("output_tokens"),
                         "cache_read": num("cache_read_input_tokens"), "cache_write": num("cache_creation_input_tokens")})
    return rows


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
    out = {"estimate": True, "prices_usd_per_million": {k: dict(zip(FIELDS, v)) for k, v in PRICES.items()},
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
    return aggregate(usage_rows(files, c.wt_dir.name, since, until), top)
