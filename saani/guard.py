"""The guard: a Claude Code PreToolUse hook shipped by the plugin (hooks/hooks.json runs `saani guard`). Standard library only.

  saani guard        reads the hook's JSON on stdin; prints Claude Code's PreToolUse decision, or nothing

Two modes (docs/SAANI.md, The guard):
- **present** (no auto run on): it prints nothing, so Claude Code's normal permissions apply. It only looks for the state file, without
  running git, and imports nothing heavy.
- **auto** (the state file `saani-auto.json` is in the repository's shared git folder): every tool call gets "allow" or "deny", never
  "ask", because an away run must not wait. What is not on the guard's list is refused, with the reason and how to allow it. Each refusal
  adds one line to `saani-auto-refusals.jsonl` in the same folder (time, run, tool, short reason, the command's first word only).

`state`, `turn_on`, `turn_off` and `refusals` are for the auto run's switch and stop rules. `decide(event, ctx)` is the whole decision,
with no side effect, for the tests.
"""
import json
import os
import re
import sys

STATE = "saani-auto.json"
REFUSALS = "saani-auto-refusals.jsonl"
WINDOWS = os.name == "nt"
HOW = ("skip it and note it for the report; the owner can do it when present, or a reviewed change can add a rule to the guard "
       "(docs/SAANI.md, The guard)")


# ---------------------------------------------------------------- the state file (no git process)

def git_dirs(cwd):
    """(the checkout's top folder, the repository's shared git folder) for `cwd`, found by walking up to `.git` (a folder, or a worktree's
    `.git` file and its `commondir`) without running git; None outside a git checkout."""
    d = os.path.abspath(cwd)
    while True:
        dot = os.path.join(d, ".git")
        if os.path.isdir(dot):
            gitdir = dot
            break
        if os.path.isfile(dot):
            with open(dot, encoding="utf-8") as f:
                text = f.read().strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = text[7:].strip()
            if not os.path.isabs(gitdir):
                gitdir = os.path.join(d, gitdir)
            break
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    common = gitdir
    commondir = os.path.join(gitdir, "commondir")
    if os.path.isfile(commondir):
        with open(commondir, encoding="utf-8") as f:
            rel = f.read().strip()
        common = rel if os.path.isabs(rel) else os.path.join(gitdir, rel)
    return d, os.path.normpath(common)


def _shared(cwd, name):
    dirs = git_dirs(cwd)
    return os.path.join(dirs[1], name) if dirs else None


def state(cwd):
    """The auto run's state ({"id", "started", "folder", "deploy"}) when one is on, else None. A state file that cannot be read still
    means "on" (an empty dict): the guard never falls back to present mode because of a broken file."""
    path = _shared(cwd, STATE)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def turn_on(cwd, run_id, folder, deploy=False, started=None):
    """Writes the state file (atomically): the guard decides every tool call from now on. Returns the state."""
    import datetime
    path = _shared(cwd, STATE)
    if not path:
        raise ValueError(f"{cwd} is not inside a git checkout")
    data = {"id": str(run_id), "started": started or datetime.datetime.now().isoformat(timespec="seconds"),
            "folder": str(folder), "deploy": bool(deploy)}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return data


def turn_off(cwd):
    """Removes the state file: back to present mode. Returns the state that was on, or None."""
    was = state(cwd)
    path = _shared(cwd, STATE)
    if path and os.path.exists(path):
        os.remove(path)
    return was


def refusals(cwd, run=None):
    """{"total": n, "by_reason": {reason: n}} of the refusal log, for the run `run` (default: the run that is on; none on: every line)."""
    if run is None:
        run = (state(cwd) or {}).get("id")
    out = {"total": 0, "by_reason": {}}
    path = _shared(cwd, REFUSALS)
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if run is not None and row.get("run") != run:
                continue
            out["total"] += 1
            out["by_reason"][row.get("reason", "?")] = out["by_reason"].get(row.get("reason", "?"), 0) + 1
    return out


def safe_word(word):
    """The first word of a command as it may be logged: a plain name, else "?" (an `A=b` assignment could hold a value)."""
    return word if re.fullmatch(r"[A-Za-z0-9._+\[-]{1,32}", word or "") else ("?" if word else "")


def log_refusal(cwd, st, tool, reason, word):
    import datetime
    path = _shared(cwd, REFUSALS)
    if not path:
        return
    row = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "run": st.get("id"), "tool": str(tool)[:40],
           "reason": reason, "word": safe_word(word)}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- the run's places

class Context:
    """What the decision needs: the folders (normalized), the branches, the repository and the test commands."""

    def __init__(self, cwd, wt_dir=None, folder=None, temp=None, main=None, repo="", integration="dev", production="main",
                 worker_branch=None, tests=()):
        import tempfile
        self.temp = self.norm(temp or tempfile.gettempdir())
        self.cwd = self.norm(cwd)
        self.wt_dir = self.norm(wt_dir) if wt_dir else None
        self.folder = self.norm(folder) if folder else None
        self.main = self.norm(main) if main else None
        self.repo = repo
        self.integration = integration
        self.production = production
        self.worker_branch = worker_branch or re.compile(r"^(bug|feat|task)/(\d+)-[a-z0-9-]+$")
        self.tests = [list(t) for t in tests if t]

    @staticmethod
    def norm(p):
        return os.path.normcase(os.path.realpath(str(p)))

    def path(self, p, cwd=None, tilde=False):
        """`p` as a normalized absolute path: relative to `cwd`, `..` resolved, Git Bash's /c/... and /tmp read on Windows, case folded on
        Windows."""
        p = str(p)
        if WINDOWS:
            m = re.match(r"^/([a-zA-Z])(?=/|$)", p)
            if m:
                p = m.group(1) + ":" + (p[2:] or "/")
            elif p == "/tmp" or p.startswith("/tmp/"):
                p = self.temp + p[4:]
        if tilde and (p == "~" or p.startswith(("~/", "~\\"))):
            p = os.path.expanduser("~") + p[1:]
        return self.norm(os.path.join(cwd or self.cwd, p))

    @staticmethod
    def inside(p, base):
        if not base:
            return False
        return p == base or p.startswith(base.rstrip("\\/") + os.sep)

    def in_run(self, p):
        """Inside the worktrees folder or the run's folder."""
        return self.inside(p, self.wt_dir) or self.inside(p, self.folder)

    def writable(self, p):
        return self.in_run(p) or self.inside(p, self.temp)

    def checkout_rel(self, p):
        """The parts of `p` inside its checkout (a worktree or the main checkout), or None."""
        for base, skip in ((self.wt_dir, 1), (self.main, 0)):
            if base and self.inside(p, base) and p != base:
                parts = [x for x in re.split(r"[\\/]+", p[len(base):]) if x]
                return parts[skip:]
        return None


def context(cwd, st):
    """The Context of the project holding `cwd`: its saani.toml (the checkout's, else the main checkout's) and the run's state."""
    import tomllib
    from . import config
    top, common = git_dirs(cwd)
    main = os.path.dirname(common) if os.path.basename(common).lower() == ".git" else None
    cfg, wt_dir = config.Config(), None
    for folder in (top, main):
        toml = os.path.join(folder, config.FILE) if folder else None
        if toml and os.path.isfile(toml):
            with open(toml, "rb") as f:
                cfg = config.from_toml(tomllib.load(f), main or top)
            wt_dir = cfg.wt_dir
            break
    return Context(cwd, wt_dir=wt_dir, folder=st.get("folder") or None, main=main, repo=cfg.repo, integration=cfg.integration,
                   production=cfg.production, worker_branch=cfg.worker_branch, tests=[cfg.tier1, cfg.full, cfg.check])


# ---------------------------------------------------------------- secrets and the guard's own files

STRONG_NAMES = (".netrc", "_netrc", ".npmrc", ".pypirc")
KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
KEY_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "credentials")


def secret_name(raw, resolved=None, strict=True):
    """True when the path names a secret: .env and .env.*, keys (*.pem, *.key, *.p12, *.pfx, id_rsa*, id_ed25519*...), credentials*,
    .netrc, .npmrc, .pypirc, anything under .ssh/, gh's hosts.yml, a name with "secret" or "token" in it. Not `strict` (a word of a shell
    command that may not be a file at all, like a branch name): the "secret"/"token" rule needs a dot in the name or an existing file."""
    for c in (raw, resolved):
        if not c:
            continue
        parts = [x for x in re.split(r"[\\/]+", str(c).lower()) if x]
        if not parts:
            continue
        if ".ssh" in parts or parts[-2:] == ["gh", "hosts.yml"]:
            return True
        name = parts[-1].split(":")[0].rstrip(". ") or parts[-1]       # Windows: `.env::$DATA` and `.env.` are `.env`
        if name == ".env" or name.startswith(".env.") or name in STRONG_NAMES:
            return True
        if name.endswith(KEY_SUFFIXES) or name.startswith(KEY_PREFIXES):
            return True
        if "secret" in name or "token" in name:
            if strict or "." in name or (resolved and os.path.isfile(resolved)):
                return True
    return False


def guard_file(p, ctx=None):
    """True for the guard's own files, anywhere: saani/guard.py, saani.toml, .claude/settings*.json, hooks/hooks.json, the auto run's
    files in a git folder, and anything in a checkout's top `hooks/` folder."""
    parts = [x for x in re.split(r"[\\/]+", p.lower()) if x]
    if not parts:
        return False
    name = parts[-1]
    if name == "saani.toml" or parts[-2:] in (["saani", "guard.py"], ["hooks", "hooks.json"]):
        return True
    if len(parts) > 1 and parts[-2] == ".claude" and name.startswith("settings") and name.endswith(".json"):
        return True
    if len(parts) > 1 and parts[-2] == ".git" and name.startswith("saani-auto"):
        return True
    rel = ctx.checkout_rel(p) if ctx else None
    return bool(rel) and rel[0] == "hooks"


def holds_guard(p, ctx):
    """True when `p` is a guard file or a folder holding one (for rm)."""
    if guard_file(p, ctx):
        return True
    if not os.path.isdir(p):
        return False
    rel = ctx.checkout_rel(p)
    if rel is not None and len(rel) == 0:
        return True
    probes = ("saani.toml", os.path.join("saani", "guard.py"), os.path.join("hooks", "hooks.json"), ".claude", "guard.py", "hooks.json")
    return any(os.path.exists(os.path.join(p, x)) for x in probes)


# ---------------------------------------------------------------- the decision

class Unsure(Exception):
    """A shell construct the guard does not read (substitution, variables, subshells...): refused."""


def allow(why):
    return ("allow", f"Saani guard (auto run): allowed, {why}.", "", "")


def deny(code, why, word="", how=HOW):
    return ("deny", f"Saani guard (auto run): refused, {why}. To go on: {how}.", code, word)


READ_TOOLS = {"Read": "file_path", "Glob": "path", "Grep": "path", "LS": "path"}
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
OTHER_TOOLS = {"Agent", "Task", "TodoWrite", "ToolSearch", "Skill", "SendMessage", "WebSearch", "WebFetch",
               "BashOutput", "KillShell", "KillBash", "TaskOutput", "TaskStop"}


def decide(event, ctx):
    """("allow" | "deny", reason, short reason for the log, the refused command's first word) for one PreToolUse event in an auto run."""
    tool = event.get("tool_name")
    inp = event.get("tool_input")
    if not isinstance(inp, dict):
        inp = {}
    if tool == "Bash":
        command = inp.get("command")
        if not isinstance(command, str) or not command.strip():
            return deny("unreadable command", "a Bash call without a command")
        return bash(command, ctx)
    if tool in READ_TOOLS:
        for key in (READ_TOOLS[tool], "file_path", "path", "pattern", "glob"):
            if key == "pattern" and tool != "Glob":
                continue                                    # Grep's pattern is a regex of the contents, not a file name
            value = inp.get(key)
            if isinstance(value, str) and value and secret_name(value, ctx.path(value, tilde=True)):
                return deny("secret", f"{tool} of a secret file (keys, .env, credentials, tokens are never read in an auto run)",
                            how="the owner handles secrets, never an agent")
        return allow(f"{tool} outside the secrets")
    if tool in EDIT_TOOLS:
        raw = inp.get(EDIT_TOOLS[tool]) or inp.get("file_path")
        if not isinstance(raw, str) or not raw:
            return deny("unreadable path", f"{tool} without a file path")
        p = ctx.path(raw, tilde=True)
        if secret_name(raw, p):
            return deny("secret", f"{tool} of a secret file", how="the owner handles secrets, never an agent")
        if guard_file(p, ctx):
            return deny("guard file", f"{tool} of the guard's own files (saani/guard.py, hooks/, .claude/settings*.json, saani.toml)",
                        how="the guard changes only in a session with the owner present")
        if not ctx.writable(p):
            return deny("edit outside the worktrees", f"{tool} outside the worktrees folder, the run's folder and the temp folder",
                        how="work in the issue's worktree (saani start <n>)")
        return allow(f"{tool} inside the run's folders")
    if tool == "AskUserQuestion":
        return deny("question", "no questions in an auto run (nobody answers)", how="note the question for the report and move on")
    if tool in OTHER_TOOLS:
        return allow(f"{tool} changes no file")
    return deny("unknown tool", f"the tool {tool!r} is not on the guard's list")


# ---------------------------------------------------------------- reading a shell command

class Word(str):
    """A shell word with its quotes removed; `glob` when it holds an unquoted *, ? or [."""
    glob = False


HEREDOC = re.compile(r"\$\(cat <<'([A-Za-z_][A-Za-z0-9_]*)'\n(.*?)\n\1\n\)", re.S)
EXPANSION = re.compile(r"[A-Za-z0-9_{(@*#?$!-]")


def _heredoc(text, i):
    """A `$(cat <<'EOF' ... EOF)` at i (the usual way to pass a commit message; the quoted delimiter makes its body plain text): its end,
    or None."""
    m = HEREDOC.match(text, i)
    if not m or m.group(1) in m.group(2).split("\n"):
        return None
    return m.end()


def split_bash(text):
    """[(words, redirections)] of a command line, split on &&, ||, ;, |, & and newlines, quotes removed as bash does. Redirections are
    (operator, target) pairs; ("dup", "") for 2>&1 and the like. Raises Unsure on what the guard does not read."""
    parts, words, redirs = [], [], []
    buf, started, quoted, globbed, pending = [], False, False, False, None
    home = os.path.expanduser("~").replace("\\", "/")
    n, i = len(text), 0

    def flush():
        nonlocal buf, started, quoted, globbed, pending
        if not started:
            return
        w = Word("".join(buf))
        w.glob = globbed
        if pending is not None:
            redirs.append((pending, w))
            pending = None
        else:
            words.append(w)
        buf, started, quoted, globbed = [], False, False, False

    def end_part():
        nonlocal words, redirs
        flush()
        if pending is not None:
            raise Unsure("a redirection without a target")
        if words or redirs:
            parts.append((words, redirs))
        words, redirs = [], []

    while i < n:
        c = text[i]
        if c in " \t\r":
            flush()
            i += 1
        elif c in "\n;":
            end_part()
            i += 1
        elif c == "&" and text.startswith("&>", i):
            flush()
            pending, i = (">>", i + 3) if text.startswith("&>>", i) else (">", i + 2)
        elif c in "&|":
            end_part()
            i += 2 if text[i:i + 2] in ("&&", "||", "|&") else 1
        elif c in "<>":
            if started and not quoted and "".join(buf).isdigit():
                buf, started = [], False                    # 2>file: the fd is part of the operator
            else:
                flush()
            op = next(o for o in ("<<<", "<<", "<>", "<&", ">>", ">&", ">|", "<", ">") if text.startswith(o, i))
            i += len(op)
            if op in ("<<<", "<<", "<>"):
                raise Unsure(f"the shell operator {op}")
            if op in (">&", "<&"):
                m = re.match(r"[0-9]+-?|-", text[i:])
                if m:
                    redirs.append(("dup", ""))
                    i += m.end()
                    continue
                if op == "<&":
                    raise Unsure("<&")
                op = ">"
            pending = ">" if op == ">|" else op
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise Unsure("an unclosed quote")
            buf.append(text[i + 1:j])
            started = quoted = True
            i = j + 1
        elif c == '"':
            started = quoted = True
            j = i + 1
            while True:
                if j >= n:
                    raise Unsure("an unclosed quote")
                ch = text[j]
                if ch == '"':
                    break
                if ch == "\\" and j + 1 < n and text[j + 1] in '"\\$`\n':
                    if text[j + 1] != "\n":
                        buf.append(text[j + 1])
                    j += 2
                    continue
                if ch == "$" and (end := _heredoc(text, j)):
                    buf.append("MESSAGE")
                    j = end
                    continue
                if ch == "`" or (ch == "$" and j + 1 < n and EXPANSION.match(text[j + 1])):
                    raise Unsure("a substitution or a variable")
                buf.append(ch)
                j += 1
            i = j + 1
        elif c == "\\":
            if i + 1 >= n:
                raise Unsure("a trailing backslash")
            if text[i + 1] != "\n":
                buf.append(text[i + 1])
                started = quoted = True
            i += 2
        elif c == "$" and (end := _heredoc(text, i)):
            buf.append("MESSAGE")
            started = True
            i = end
        elif c == "`" or (c == "$" and i + 1 < n and EXPANSION.match(text[i + 1])):
            raise Unsure("a substitution or a variable")
        elif c in "(){}":
            raise Unsure(f"the shell syntax {c}")
        elif c == "#" and not started:
            while i < n and text[i] != "\n":
                i += 1
        elif c == "~" and not started:
            if i + 1 < n and text[i + 1] not in "/ \t\n;&|<>":
                raise Unsure("~user")
            buf.append(home)
            started = True
            i += 1
        else:
            if c in "*?[":
                globbed = True
            buf.append(c)
            started = True
            i += 1
    end_part()
    return parts


# ---------------------------------------------------------------- the rules for one command

READ_ONLY = {"ls", "cat", "head", "tail", "grep", "egrep", "fgrep", "wc", "sort", "find", "echo", "test", "[", "pwd", "diff"}
FIND_WRITES = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
GIT_READ = {"status", "diff", "log", "show", "rev-parse", "ls-files", "fetch", "add", "restore", "merge", "commit"}
GIT_RUNS = ("--upload-pack", "--receive-pack", "--exec", "--output")
GH_READ = {("issue", "view"), ("issue", "list"), ("pr", "view"), ("pr", "list"), ("pr", "checks"), ("pr", "diff"),
           ("run", "view"), ("run", "list"), ("run", "watch")}
GH_DENIED = {"release": "gh release", "repo": "gh repo", "secret": "gh secret", "auth": "gh auth"}
LOCALHOST = {"localhost", "127.0.0.1", "::1"}


def is_python(word):
    name = re.split(r"[\\/]", word.lower())[-1].removesuffix(".exe")
    return bool(re.fullmatch(r"python(3(\.\d+)?)?|py", name)) or (bool(sys.executable) and word == sys.executable)


def is_test_command(words, ctx):
    """True when the words start with one of the project's test commands ([tests] tier1/full/check), `{python}` being any Python."""
    for test in ctx.tests:
        if len(words) >= len(test) and all(is_python(w) if t == "{python}" else w == t for w, t in zip(words, test)):
            return True
    return False


def secret_word(ctx, word, cwd):
    """True when a word of a command names a secret: the word, its pieces around : and =, and what its glob matches."""
    pieces = {word, *re.split(r"[:=]", word)}
    for piece in pieces:
        if piece and secret_name(piece, ctx.path(piece, cwd), strict=False):
            return True
    if getattr(word, "glob", False):
        import glob
        base = word if os.path.isabs(word) else os.path.join(cwd, word)
        for k, match in enumerate(glob.iglob(base)):
            if k > 2000 or secret_name(match):
                return True
    return False


def scan(ctx, words, cwd, skip=()):
    """The first word naming a secret, or None (`skip`: indexes of words that are text, not paths: a commit message, a title...)."""
    for k, w in enumerate(words):
        if k not in skip and secret_word(ctx, w, cwd):
            return w
    return None


def option_values(args, names, prefixes=()):
    """Indexes of the values of the options `names` (`-m text`, `--title text`) and of `--name=text` words."""
    out = set()
    for k, w in enumerate(args):
        if w in names:
            out.add(k + 1)
        elif w.startswith(prefixes):
            out.add(k)
    return out


def command(words, redirs, ctx, cwd):
    """(verdict or None for allowed, the folder after it) for one simple command."""
    for op, target in redirs:
        if op == "dup":
            continue
        if getattr(target, "glob", False):
            return deny("unsure", "a redirection to a glob", words[0] if words else ""), cwd
        if op == "<":
            if secret_word(ctx, target, cwd):
                return deny("secret", "a command reading a secret file", words[0] if words else "",
                            how="the owner handles secrets, never an agent"), cwd
            continue
        if target in ("/dev/null", "NUL", "nul"):
            continue
        p = ctx.path(target, cwd)
        if secret_name(target, p, strict=False):
            return deny("secret", "a command writing a secret file", words[0] if words else "",
                        how="the owner handles secrets, never an agent"), cwd
        if guard_file(p, ctx):
            return deny("guard file", "a command writing the guard's own files", words[0] if words else "",
                        how="the guard changes only in a session with the owner present"), cwd
        if not ctx.writable(p):
            return deny("write outside the worktrees", "a redirection into a file outside the worktrees folder, the run's folder and temp",
                        words[0] if words else "", how="write inside the issue's worktree"), cwd
    if not words:
        return deny("unsure", "a redirection without a command"), cwd
    cmd, args = words[0], words[1:]
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", cmd):
        return deny("unsure", "an environment assignment before a command (or alone)", cmd, how="run the command without it"), cwd
    if cmd == "sudo":
        return deny("sudo", "sudo", cmd), cwd
    if cmd == "cd":
        if len(args) != 1:
            return deny("cd outside the worktrees", "cd needs one folder", cmd), cwd
        target = ctx.path(args[0], cwd)
        if not ctx.in_run(target):
            return deny("cd outside the worktrees", "cd outside the worktrees folder and the run's folder", cmd,
                        how="work in the run's folder or an issue's worktree"), cwd
        return None, target
    if cmd == "saani":
        return saani_command(args, ctx, cwd), cwd
    if is_python(cmd) and len(args) >= 2 and args[0] == "-I" and re.split(r"[\\/]", args[1])[-1].lower() == "saani.py":
        return saani_command(args[2:], ctx, cwd), cwd
    if is_test_command(words, ctx):
        bad = scan(ctx, args, cwd)
        return (deny("secret", "a test command naming a secret file", cmd) if bad else None), cwd
    if is_python(cmd):
        return deny("python", "Python runs only saani.py (python -I <path>/saani.py) and the project's test commands", cmd), cwd
    if cmd in READ_ONLY:
        return read_only(cmd, args, ctx, cwd), cwd
    if cmd == "git":
        return git_command(args, ctx, cwd), cwd
    if cmd == "gh":
        return gh_command(args, ctx, cwd), cwd
    if cmd in ("curl", "wget"):
        return fetch_command(cmd, args, ctx, cwd), cwd
    if cmd == "rm":
        return rm_command(args, ctx, cwd), cwd
    return deny("not on the list", f"the command {safe_word(cmd) or '?'!r} is not on the guard's list", cmd), cwd


def saani_command(args, ctx, cwd):
    """`saani ...` is allowed; `saani test` runs any command, so only the project's test commands."""
    if args[:1] == ["test"]:
        rest = list(args[1:])
        if "--" in rest:
            rest = rest[rest.index("--") + 1:]
        else:
            while rest and rest[0].startswith("--kind"):
                rest = rest[1:] if "=" in rest[0] else rest[2:]
        if not is_test_command(rest, ctx):
            return deny("not a test command", "saani test runs only the project's test commands ([tests] in saani.toml)", "saani")
        if scan(ctx, rest, cwd):
            return deny("secret", "a test command naming a secret file", "saani")
    return None


def read_only(cmd, args, ctx, cwd):
    skip = set()
    if cmd == "echo":
        skip = set(range(len(args)))
    elif cmd == "find":
        if any(a in FIND_WRITES for a in args):
            return deny("find writes", "find with -delete or -exec", cmd, how="list with find, then act with an allowed command")
    elif cmd == "sort":
        if any(a == "-o" or a.startswith("--output") or re.fullmatch(r"-[a-zA-Z]*o.*", a) for a in args):
            return deny("write outside the worktrees", "sort -o writes a file", cmd)
    elif cmd in ("grep", "egrep", "fgrep", "diff"):
        if any(a in ("--recursive", "--dereference-recursive", "-d", "--directories=recurse") or re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", a)
               for a in args):
            return deny("recursive read", f"{cmd} -r reads every file of a folder, secrets included", cmd,
                        how="use the Grep tool, or name the files")
        if cmd != "diff":
            skip = option_values(args, {"-e", "--regexp"}, ("--regexp=",))
            if not skip:
                positional = [k for k, a in enumerate(args) if not a.startswith("-")]
                skip = set(positional[:1])
    if scan(ctx, args, cwd, skip):
        return deny("secret", f"{cmd} of a secret file", cmd, how="the owner handles secrets, never an agent")
    return None


def git_command(args, ctx, cwd):
    args, gcwd = list(args), cwd
    while args and args[0].startswith("-"):
        if args[0] == "-C" and len(args) > 1:
            gcwd = ctx.path(args[1], cwd)
            if not ctx.in_run(gcwd):
                return deny("cd outside the worktrees", "git -C outside the worktrees folder and the run's folder", "git")
            args = args[2:]
        elif args[0] == "--no-pager":
            args = args[1:]
        else:
            return deny("git option", f"the git option {args[0]} (it can change what git runs)", "git")
    if not args:
        return deny("not on the list", "git without a command", "git")
    sub, rest = args[0], args[1:]
    text = set()
    if sub == "commit":
        for k, w in enumerate(rest):
            if w == "--message" or re.fullmatch(r"-[a-zA-Z]*m", w):
                text.add(k + 1)
            elif w.startswith("--message=") or re.fullmatch(r"-[a-zA-Z]*m.+", w):
                text.add(k)
    words = [w for k, w in enumerate(rest) if k not in text]
    if sub == "push":
        return deny("git push", "git push (only saani land pushes, as its own process)", "git", how="land with saani land")
    if sub == "reset" and "--hard" in words:
        return deny("git reset --hard", "git reset --hard", "git", how="git restore <path>, or git stash")
    if sub == "clean":
        return deny("git clean", "git clean", "git", how="remove the files one by one with rm inside the worktree")
    if sub == "branch" and ("-D" in words or (("--delete" in words or "-d" in words) and ("--force" in words or "-f" in words))):
        return deny("git branch -D", "git branch -D", "git", how="saani prune deletes merged branches")
    if sub == "worktree" and words[:1] == ["remove"]:
        return deny("git worktree remove", "git worktree remove", "git", how="saani prune removes finished worktrees")
    if any(w in ("-f", "--force") or w.startswith("--force") for w in words):
        return deny("force flag", "a force flag", "git")
    if any(w.startswith(GIT_RUNS) for w in words):
        return deny("git option", "a git option that runs a program or writes a file", "git")
    verdict = git_sub(sub, words, ctx)
    if verdict:
        return verdict
    if scan(ctx, rest, gcwd, text):
        return deny("secret", f"git {sub} of a secret file", "git", how="the owner handles secrets, never an agent")
    return None


def git_sub(sub, words, ctx):
    no = deny("not on the list", f"git {sub} {' '.join(words[:1])}".strip() + " is not on the guard's list", "git")
    if sub in GIT_READ:
        if sub in ("commit", "merge") and any(w == "--no-verify" or (sub == "commit" and re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", w))
                                              for w in words):
            return deny("no-verify", f"git {sub} --no-verify skips the commit-msg hook", "git")
        return None
    if sub in ("switch", "checkout"):
        if "--" in words:
            before = words[:words.index("--")]
            if len(before) <= 1 and not any(w.startswith("-") for w in before):
                return None
            words = before
        options = {"-b", "-c", "--create", "-q", "--quiet", "--track", "--no-track", "-t"}
        if any(w.startswith("-") and w not in options for w in words):
            return deny("not on the list", f"git {sub} with {[w for w in words if w.startswith('-') and w not in options][0]}", "git")
        positional = [w for w in words if not w.startswith("-")]
        if not positional or not ctx.worker_branch.match(positional[0]) or len(positional) > 2:
            return deny("not a worker branch", f"git {sub} to a branch that is not a worker's (<type>/<n>-<words>)", "git",
                        how="saani start <n> makes the issue's worktree and branch")
        return None
    if sub == "rebase":
        positional = [w for w in words if w not in ("-q", "--quiet")]
        if positional in ([f"origin/{ctx.integration}"], ["--continue"], ["--abort"], ["--skip"]):
            return None
        return deny("not on the list", f"git rebase other than onto origin/{ctx.integration}", "git")
    if sub == "worktree":
        return None if words[:1] == ["list"] else deny("not on the list", "git worktree other than list", "git",
                                                       how="saani start makes worktrees, saani prune removes them")
    if sub == "branch":
        listing = {"--list", "-l", "--show-current", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose"}
        valued = {"--merged", "--no-merged", "--contains", "--no-contains", "--points-at", "--sort"}
        k, pattern_ok = 0, "--list" in words or "-l" in words
        while k < len(words):
            w = words[k]
            if w in valued:
                k += 2
                continue
            if w.startswith(("--format=", "--sort=", "--merged=", "--contains=")) or w in listing or (pattern_ok and not w.startswith("-")):
                k += 1
                continue
            return deny("not on the list", "git branch other than listing", "git", how="saani start makes branches, saani prune deletes them")
        return None
    if sub == "stash":
        return None if words[:1] in ([], ["push"], ["pop"], ["list"], ["show"]) else no
    return no


def gh_command(args, ctx, cwd):
    args = list(args)
    for k, w in enumerate(args):
        value = args[k + 1] if w in ("-R", "--repo") and k + 1 < len(args) else (w[7:] if w.startswith("--repo=") else None)
        if w in ("-R", "--repo") and value is None:
            return deny("unsure", "gh -R without a repository", "gh")
        if value is not None and value.lower() != ctx.repo.lower():
            return deny("another repository", f"gh on a repository other than {ctx.repo}", "gh")
    group, sub = (args + ["", ""])[:2]
    rest = args[2:]
    if (group, sub) == ("pr", "merge"):
        return deny("gh pr merge", "gh pr merge (a run never merges)", "gh", how="the owner merges the pull request")
    if group in GH_DENIED:
        return deny(GH_DENIED[group], GH_DENIED[group], "gh", how="the owner does it")
    text = option_values(rest, {"-t", "--title", "-b", "--body"}, ("--title=", "--body="))
    verdict = gh_sub(group, sub, rest, ctx)
    if verdict:
        return verdict
    if scan(ctx, rest, cwd, text):
        return deny("secret", "gh naming a secret file", "gh", how="the owner handles secrets, never an agent")
    return None


def gh_sub(group, sub, rest, ctx):
    if (group, sub) in GH_READ:
        return None
    if (group, sub) in (("issue", "comment"), ("pr", "comment")):
        return deny("not on the list", f"gh {group} comment --delete-last", "gh") if "--delete-last" in rest else None
    if (group, sub) == ("issue", "create"):
        return None
    if (group, sub) == ("issue", "edit"):
        k = 0
        while k < len(rest):
            w = rest[k]
            if w in ("--add-label", "--remove-label", "-R", "--repo"):
                k += 2
            elif w.startswith(("--add-label=", "--remove-label=", "--repo=")) or re.fullmatch(r"#?\d+|https://\S+", w):
                k += 1
            else:
                return deny("gh issue edit", "gh issue edit other than labels", "gh", how="comment on the issue instead")
        return None
    if (group, sub) in (("pr", "create"), ("pr", "edit")):
        def value(short, long):
            for k, w in enumerate(rest):
                if w in (short, long) and k + 1 < len(rest):
                    return rest[k + 1]
                if w.startswith(long + "="):
                    return w[len(long) + 1:]
            return None
        base, head = value("-B", "--base"), value("-H", "--head")
        if sub == "create" and (base != ctx.production or head != ctx.integration):
            return deny("gh pr create", f"gh pr create other than --base {ctx.production} --head {ctx.integration}", "gh",
                        how=f"open the one pull request with --base {ctx.production} --head {ctx.integration}")
        if sub == "edit" and base is not None and base != ctx.production:
            return deny("gh pr edit", f"gh pr edit --base other than {ctx.production}", "gh")
        return None
    if group == "api":
        words = [sub, *rest]                                # options may come before the endpoint
        if any("secret" in w.lower() for w in words):
            return deny("secret", "gh api on secrets", "gh", how="the owner handles secrets, never an agent")
        for k, w in enumerate(words):
            method = words[k + 1] if w in ("-X", "--method") and k + 1 < len(words) else (
                w[2:] if w.startswith("-X") and len(w) > 2 else (w[9:] if w.startswith("--method=") else None))
            if method is not None and method.upper() != "GET":
                return deny("gh api write", "gh api with a method other than GET", "gh")
            if w in ("-f", "-F", "--field", "--raw-field", "--input") or w.startswith(("--field=", "--raw-field=", "--input=")) or \
                    re.fullmatch(r"-[fF].+", w):
                return deny("gh api write", "gh api with fields or input (that makes it a POST)", "gh")
        return None
    return deny("not on the list", f"gh {group} {sub}".strip() + " is not on the guard's list", "gh")


def fetch_command(cmd, args, ctx, cwd):
    """curl (and wget to stdout) to localhost only, GET only."""
    valued = {"-H", "--header", "-m", "--max-time", "--connect-timeout", "-w", "--write-out", "-A", "--user-agent", "--retry", "-X",
              "--request"}
    flags = {"--silent", "--show-error", "--fail", "--location", "--include", "--head", "--verbose", "--insecure", "--compressed",
             "--quiet", "--server-response"}
    short = "sSfLiIvkq" if cmd == "curl" else "qS"
    urls, k = [], 0
    while k < len(args):
        w = args[k]
        if cmd == "wget" and (w in ("-qO-", "-O-") or (w == "-O" and args[k + 1:k + 2] == ["-"])):
            k += 2 if w == "-O" else 1
            continue
        if w in valued and k + 1 < len(args):
            if w in ("-X", "--request") and args[k + 1].upper() != "GET":
                return deny(f"{cmd} write", f"{cmd} with a method other than GET", cmd)
            k += 2
            continue
        if w.startswith("-X") and len(w) > 2:
            if w[2:].upper() != "GET":
                return deny(f"{cmd} write", f"{cmd} with a method other than GET", cmd)
        elif w in flags or (re.fullmatch(r"-[a-zA-Z]+", w) and set(w[1:]) <= set(short)):
            pass
        elif w.startswith("-"):
            return deny(f"{cmd} write", f"the {cmd} option {w} (data, uploads, files and configs are refused)", cmd)
        else:
            urls.append(w)
        k += 1
    if cmd == "wget" and not any(a in ("-qO-", "-O-", "-O") for a in args):
        return deny("wget write", "wget that saves a file", cmd, how="use curl to localhost")
    if not urls:
        return deny("unsure", f"{cmd} without a URL", cmd)
    from urllib.parse import urlsplit
    for url in urls:
        try:
            host = urlsplit(url if "://" in url else "http://" + url).hostname
        except ValueError:
            host = None
        if host not in LOCALHOST:
            return deny(f"{cmd} outside localhost", f"{cmd} to a host other than localhost", cmd, how="use WebFetch to read a page")
    return None


def rm_command(args, ctx, cwd):
    paths, ended = [], False
    for w in args:
        if w == "--" and not ended:
            ended = True
        elif w.startswith("-") and not ended:
            continue
        else:
            paths.append(w)
    if not paths:
        return deny("unsure", "rm without a path", "rm")
    targets = []
    import glob
    for w in paths:
        found = glob.glob(w if os.path.isabs(w) else os.path.join(cwd, w)) if getattr(w, "glob", False) else []
        targets += found or [w]
    for t in targets:
        p = ctx.path(t, cwd)
        if secret_name(t, p, strict=False):
            return deny("secret", "rm of a secret file", "rm", how="the owner handles secrets, never an agent")
        if ctx.wt_dir and (p == ctx.wt_dir or os.path.dirname(p) == ctx.wt_dir) or p == ctx.folder or p == ctx.temp:
            return deny("rm a worktree", "rm of a worktree or of the worktrees folder", "rm", how="saani prune removes finished worktrees")
        if holds_guard(p, ctx):
            return deny("guard file", "rm of the guard's own files", "rm", how="the guard changes only in a session with the owner present")
        if not ctx.writable(p):
            return deny("rm outside the worktrees", "rm outside the worktrees folder, the run's folder and temp", "rm")
    return None


def bash(text, ctx):
    """Every part of a compound command must be allowed; a `cd` moves the folder the later parts are read from."""
    try:
        parts = split_bash(text)
    except Unsure as e:
        return deny("unsure", f"the guard does not read {e} in a shell command", how="write the command plainly, one simple command "
                    "per part (no $(...), backticks, variables or subshells)")
    if not parts:
        return deny("unreadable command", "an empty command")
    cwd = ctx.cwd
    for words, redirs in parts:
        verdict, cwd = command(words, redirs, ctx, cwd)
        if verdict:
            return verdict
    return allow("every part of the command is on the guard's list")


# ---------------------------------------------------------------- the hook

def main(argv=None):
    """The hook: JSON on stdin, a decision on stdout in an auto run, nothing in present mode. Never raises."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            event = None
    except ValueError:
        event = None
    cwd = (event or {}).get("cwd")
    try:
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else os.getcwd()
        st = state(cwd)
    except Exception:
        return 0
    if st is None:
        return 0
    try:
        if event is None:
            raise ValueError("unreadable hook input")
        decision, reason, code, word = decide(event, context(cwd, st))
    except (Exception, SystemExit) as e:
        decision, reason, code, word = "deny", f"Saani guard (auto run): guard error: {type(e).__name__}", "guard error", ""
    if decision == "deny":
        log_refusal(cwd, st, (event or {}).get("tool_name", "?"), code, word)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}))
    return 0


def add_parsers(sub):
    """`saani guard`, listed in the help; saani.py runs it before loading anything else."""
    p = sub.add_parser("guard", help="the Claude Code PreToolUse hook of the plugin (reads the hook's JSON on stdin)")
    p.set_defaults(run=lambda args: main())
