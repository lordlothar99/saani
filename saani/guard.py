"""The guard: a Claude Code PreToolUse hook shipped by the plugin (hooks/hooks.json runs `saani guard`). Standard library only.

  saani guard        reads the hook's JSON on stdin; prints Claude Code's PreToolUse decision, or nothing

Two modes (docs/SAANI.md, The guard), told apart by the hook's `session_id` only, never by the folder or a file the model can write:
- **present** (no entry for this session in `~/.saani/auto/`): it prints nothing, so Claude Code's normal permissions apply.
- **auto** (`~/.saani/auto/<session_id>.json`, written when the guard sees the Bash call `saani auto on`, removed by `saani auto off`):
  every tool call gets "allow" or "deny", never "ask", because an away run must not wait. The rules are an allowlist: what the guard
  cannot read for sure is refused, with the reason and how to go on. Each refusal adds a line to `~/.saani/auto/refusals.jsonl` (time,
  run, tool, short reason, the command's first word only).

Fail closed: when the registry cannot be read and an entry for this session might exist, the call is refused. If the hook cannot run at
all (timeout, no Python), Claude Code's normal permissions apply: it asks.

`state`, `turn_on`, `turn_off` and `refusals` are for the auto run's switch and stop rules. `decide(event, ctx)` is the whole decision,
with no side effect, for the tests.
"""
import json
import os
import re
import sys

REFUSALS = "refusals.jsonl"
SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}")
RUN_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
MAX_HOURS = 12                      # an entry older than this is over (the run's own limit is 10 hours): present mode again
WINDOWS = os.name == "nt"
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
HOW = ("skip it and note it for the report; the owner can do it when present, or a reviewed change can add a rule to the guard "
       "(docs/SAANI.md, The guard)")
SECRETS_HOW = "the owner handles secrets, never an agent"


class RegistryError(Exception):
    """The registry could not be read: the guard cannot tell present from auto, so it refuses."""


# ---------------------------------------------------------------- the registry of auto sessions (~/.saani/auto)

def registry():
    return os.path.join(os.path.expanduser("~"), ".saani", "auto")


def _entry(session_id):
    return os.path.join(registry(), session_id + ".json") if isinstance(session_id, str) and SESSION.fullmatch(session_id) else None


def state(session_id, now=None):
    """The auto run of this Claude Code session ({"id", "started", "main", "wt_dir", "folder", "deploy", settings}), or None. Raises
    RegistryError when the entry exists but cannot be read, or the registry cannot be looked at."""
    path = _entry(session_id)
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise RegistryError(type(e).__name__) from e
    if not isinstance(data, dict):
        raise RegistryError("not an object")
    import datetime
    try:
        started = datetime.datetime.fromisoformat(data["started"])
    except (KeyError, TypeError, ValueError) as e:
        raise RegistryError("no start time") from e
    if (now or datetime.datetime.now()) - started > datetime.timedelta(hours=MAX_HOURS):
        return None
    return data


def turn_on(session_id, run_id, main, wt_dir, folder=None, deploy=False, started=None, repo="", integration="dev", production="main",
            tests=(), worker_branch=r"^(bug|feat|task)/(\d+)-[a-z0-9-]+$"):
    """Registers this session as an auto run (atomically). The settings are copied in, so nothing the run can edit changes the rules."""
    import datetime
    path = _entry(session_id)
    if path is None:
        raise ValueError("not a session id")
    if not RUN_ID.fullmatch(str(run_id)):
        raise ValueError("run id: letters, digits, - and _ only")
    data = {"id": str(run_id), "started": started or datetime.datetime.now().isoformat(timespec="seconds"), "main": str(main),
            "wt_dir": str(wt_dir), "folder": str(folder) if folder else None, "deploy": bool(deploy), "repo": repo,
            "integration": integration, "production": production, "tests": [list(t) for t in tests if t], "worker_branch": worker_branch}
    os.makedirs(registry(), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return data


def turn_off(session_id):
    """Removes this session's entry: present mode again. Returns the entry that was there, or None."""
    path = _entry(session_id)
    try:
        was = state(session_id)
    except RegistryError:
        was = {}
    if path and os.path.exists(path):
        os.remove(path)
    return was


def refusals(run=None):
    """{"total": n, "by_reason": {reason: n}} of the refusal log, for the run `run` (None: every line)."""
    out = {"total": 0, "by_reason": {}}
    path = os.path.join(registry(), REFUSALS)
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if run is not None and row.get("run") != run:
                continue
            reason = row.get("reason", "?")
            out["total"] += 1
            out["by_reason"][reason] = out["by_reason"].get(reason, 0) + 1
    return out


def safe_word(word):
    """The first word of a command as it may be logged: a plain name, else "?" (it could hold a value)."""
    return word if re.fullmatch(r"[A-Za-z0-9._+\[-]{1,32}", word or "") else ("?" if word else "")


def log_refusal(st, tool, reason, word):
    import datetime
    row = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "run": (st or {}).get("id"), "tool": str(tool)[:40],
           "reason": reason, "word": safe_word(word)}
    try:
        os.makedirs(registry(), exist_ok=True)
        with open(os.path.join(registry(), REFUSALS), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- paths

def git_dirs(cwd):
    """(the checkout's top folder, the repository's shared git folder) for `cwd`, without running git; None outside a git checkout."""
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


def norm(p):
    return os.path.normcase(os.path.realpath(str(p)))


def resolve(p, cwd, temp=None, tilde=False):
    """`p` as a normalized absolute path: relative to `cwd`, `..` resolved, Git Bash's /c/... and /tmp read on Windows, case folded on
    Windows."""
    p = str(p)
    if WINDOWS:
        m = re.match(r"^/([a-zA-Z])(?=/|$)", p)
        if m:
            p = m.group(1) + ":" + (p[2:] or "/")
        elif temp and (p == "/tmp" or p.startswith("/tmp/")):
            p = temp + p[4:]
    if tilde and (p == "~" or p.startswith(("~/", "~\\"))):
        p = os.path.expanduser("~") + p[1:]
    return norm(os.path.join(cwd, p))


def inside(p, base):
    if not base:
        return False
    return p == base or p.startswith(base.rstrip("\\/") + os.sep)


class Context:
    """What the decision needs: the run's folders (normalized), the branches, the repository and the test commands."""

    def __init__(self, cwd, wt_dir=None, folder=None, temp=None, main=None, repo="", integration="dev", production="main",
                 worker_branch=None, tests=(), home=None):
        import tempfile
        self.temp = norm(temp or tempfile.gettempdir())
        self.cwd = norm(cwd)
        self.wt_dir = norm(wt_dir) if wt_dir else None
        self.folder = norm(folder) if folder else None
        self.main = norm(main) if main else None
        self.home = norm(home or os.path.expanduser("~"))
        self.saani_home = norm(os.path.join(home or os.path.expanduser("~"), ".saani"))
        self.repo = repo
        self.integration = integration
        self.production = production
        self.worker_branch = re.compile(worker_branch) if isinstance(worker_branch, str) else (
            worker_branch or re.compile(r"^(bug|feat|task)/(\d+)-[a-z0-9-]+$"))
        self.tests = [list(t) for t in tests if t]

    def path(self, p, cwd=None, tilde=False):
        return resolve(p, cwd or self.cwd, self.temp, tilde)

    def in_run(self, p):
        """Inside the worktrees folder or the run's folder."""
        return inside(p, self.wt_dir) or inside(p, self.folder)

    def cd_ok(self, p):
        """A worktree (or a folder in one) or the run's folder; never the worktrees folder itself."""
        return inside(p, self.folder) or (inside(p, self.wt_dir) and p != self.wt_dir)

    def writable(self, p):
        return self.in_run(p) or inside(p, self.temp)

    def checkout_rel(self, p):
        """The parts of `p` inside its checkout (a worktree or the main checkout), or None."""
        for base, skip in ((self.wt_dir, 1), (self.main, 0)):
            if base and inside(p, base) and p != base:
                return [x for x in re.split(r"[\\/]+", p[len(base):]) if x][skip:]
        return None


def context(cwd, st):
    return Context(cwd, wt_dir=st.get("wt_dir"), folder=st.get("folder"), main=st.get("main"), repo=st.get("repo", ""),
                   integration=st.get("integration", "dev"), production=st.get("production", "main"),
                   worker_branch=st.get("worker_branch"), tests=st.get("tests") or ())


# ---------------------------------------------------------------- secrets and protected files

STRONG_NAMES = {".netrc", "_netrc", ".npmrc", ".pypirc", ".pgpass", ".envrc", ".git-credentials", "hosts.yml"}
SECRET_DIRS = {".ssh", ".gnupg", ".kube"}
KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
KEY_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")


def secret_name(raw, resolved=None, strict=True):
    """True when the path names a secret: .env, .env.*, .envrc, keys (*.pem, *.key, *.p12, *.pfx, id_rsa*, id_ed25519*...), anything with
    "credentials" in its name, .netrc, .npmrc, .pypirc, .pgpass, .git-credentials, any hosts.yml, .docker/config.json, anything under
    .ssh/, .gnupg/, .kube/ or /proc/, a name with "secret" or "token" in it. Not `strict` (a word of a shell command that may not be a file,
    like a branch name): the "secret"/"token" rule needs a dot in the name or an existing file."""
    for c in (raw, resolved):
        if not c:
            continue
        low = str(c).replace("\\", "/").lower()
        if re.match(r"^/proc(/|$)", low):
            return True
        parts = [x for x in low.split("/") if x]
        if not parts:
            continue
        if SECRET_DIRS & set(parts) or parts[-2:] == [".docker", "config.json"]:
            return True
        name = parts[-1].split(":")[0].rstrip(". ") or parts[-1]       # Windows: `.env::$DATA` and `.env.` are `.env`
        if name == ".env" or name.startswith(".env.") or name in STRONG_NAMES or "credentials" in name:
            return True
        if name.endswith(KEY_SUFFIXES) or name.startswith(KEY_PREFIXES):
            return True
        if "secret" in name or "token" in name:
            if strict or "." in name or (resolved and os.path.isfile(resolved)):
                return True
    return False


PROTECTED_PARTS = {".git", ".githooks", ".claude"}


def protected(p, ctx):
    """True for what an auto run never writes: any path through .git, .githooks or .claude, .mcp.json, CLAUDE.md (the agents'
    instructions), ~/.saani, and the guard's own files (saani/guard.py, saani.toml, hooks/hooks.json, a checkout's top `hooks/` folder)."""
    parts = [x for x in re.split(r"[\\/]+", p.lower()) if x]
    if not parts:
        return True
    if PROTECTED_PARTS & set(parts) or parts[-1] in (".mcp.json", "saani.toml", "claude.md"):
        return True
    if parts[-2:] in (["saani", "guard.py"], ["hooks", "hooks.json"]) or inside(p, ctx.saani_home):
        return True
    rel = ctx.checkout_rel(p)
    return bool(rel) and rel[0] == "hooks"


def holds_protected(p, ctx):
    """True when removing `p` would remove something protected or one of the run's places."""
    if protected(p, ctx):
        return True
    if any(base and inside(base, p) for base in (ctx.wt_dir, ctx.folder, ctx.main, ctx.saani_home, ctx.temp, ctx.home)):
        return True
    probes = (".git", ".githooks", ".claude", ".mcp.json", "saani.toml", os.path.join("saani", "guard.py"), "hooks", "guard.py")
    return os.path.isdir(p) and any(os.path.exists(os.path.join(p, x)) for x in probes)


# ---------------------------------------------------------------- the decision

class Unsure(Exception):
    """A shell construct the guard does not read for sure: refused."""


def allow(why):
    return ("allow", f"Saani guard (auto run): allowed, {why}.", "", "")


def deny(code, why, word="", how=HOW):
    return ("deny", f"Saani guard (auto run): refused, {why}. To go on: {how}.", code, word)


READ_TOOLS = {"Read": "file_path", "LS": "path", "Glob": "path"}
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
        for key in (READ_TOOLS[tool], "file_path", "path", "pattern"):
            value = inp.get(key)
            if value is not None and not isinstance(value, str):
                return deny("unreadable path", f"{tool} with a {key} that is not text")
            if value and secret_name(value, ctx.path(value, tilde=True)):
                return deny("secret", f"{tool} of a secret file (keys, .env, credentials, tokens are never read in an auto run)",
                            how=SECRETS_HOW)
        return allow(f"{tool} outside the secrets")
    if tool == "Grep":
        return grep_tool(inp, ctx)
    if tool in EDIT_TOOLS:
        raw = inp.get(EDIT_TOOLS[tool], inp.get("file_path"))
        if not isinstance(raw, str) or not raw:
            return deny("unreadable path", f"{tool} without a file path")
        p = ctx.path(raw, tilde=True)
        if secret_name(raw, p):
            return deny("secret", f"{tool} of a secret file", how=SECRETS_HOW)
        if protected(p, ctx):
            return deny("guard file", f"{tool} of a protected file (.git, .githooks, .claude, .mcp.json, ~/.saani, the guard's files)",
                        how="those change only in a session with the owner present")
        if not ctx.writable(p):
            return deny("edit outside the worktrees", f"{tool} outside the worktrees folder, the run's folder and the temp folder",
                        how="work in the issue's worktree (saani start <n>)")
        return allow(f"{tool} inside the run's folders")
    if tool == "AskUserQuestion":
        return deny("question", "no questions in an auto run (nobody answers)", how="note the question for the report and move on")
    if tool in OTHER_TOOLS:
        return allow(f"{tool} changes no file")
    return deny("unknown tool", f"the tool {str(tool)[:40]!r} is not on the guard's list")


def grep_tool(inp, ctx):
    """Grep reads contents: only inside the worktrees or the run's folder, never a folder holding a secret unless a glob narrows it."""
    for key in ("path", "glob", "type"):
        if inp.get(key) is not None and not isinstance(inp.get(key), str):
            return deny("unreadable path", f"Grep with a {key} that is not text")
    root = ctx.path(inp.get("path") or ".", tilde=True)
    glob_ = inp.get("glob") or ""
    kind = inp.get("type") or ""
    if secret_name(inp.get("path") or "", root) or (glob_ and secret_name(glob_)) or (kind and secret_name("x." + kind)):
        return deny("secret", "Grep in secret files", how=SECRETS_HOW)
    if root == ctx.home or not ctx.in_run(root):
        return deny("grep outside the worktrees", "Grep outside the worktrees folder and the run's folder",
                    how="search inside a worktree, or Read the file")
    if os.path.isdir(root) and not (glob_ or kind):
        try:
            names = os.listdir(root)
        except OSError:
            names = []
        if any(secret_name(n) for n in names):
            return deny("secret", "Grep in a folder that holds a secret file", how="give Grep a glob or a type, or a deeper path")
    return allow("Grep inside the run's folders")


# ---------------------------------------------------------------- reading a shell command (a strict grammar)

class Word(str):
    """A shell word with its quotes removed; `glob` when it holds an unquoted *, ? or [; `heredoc` for a "$(cat <<'EOF' ... EOF\n)"."""
    glob = False
    heredoc = False


class Part:
    """One simple command: its words, and `stdin` when it ends with a <<'EOF' here-document."""

    def __init__(self, words, stdin=False):
        self.words, self.stdin = words, stdin


DQ_HEREDOC = re.compile(r"\"\$\(cat <<'([A-Za-z_][A-Za-z0-9_]*)'\n(.*?)\n\1\n\)\"", re.S)
STDIN_HEREDOC = re.compile(r"<<'([A-Za-z_][A-Za-z0-9_]*)'[ \t]*\n(.*?)\n\1(?=\n|$)", re.S)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
BOUNDARY = " \t\n;&|"


def split_bash(text):
    """[Part] of a command line, split on &&, ||, ;, |, & and newlines, quotes removed as bash does. Only what the guard reads for sure
    passes: plain words, single quotes, double quotes without $ or backticks, the redirections 2>&1, >/dev/null and 2>/dev/null, and the
    two message here-documents. Anything else raises Unsure."""
    if CONTROL.search(text):
        raise Unsure("a control character")
    parts, words = [], []
    buf, started, quoted, globbed, heredoc, stdin = [], False, False, False, False, False
    home = os.path.expanduser("~").replace("\\", "/")
    n, i = len(text), 0

    def flush():
        nonlocal buf, started, quoted, globbed, heredoc
        if started:
            w = Word("".join(buf))
            w.glob, w.heredoc = globbed, heredoc
            words.append(w)
        buf, started, quoted, globbed, heredoc = [], False, False, False, False

    def end_part():
        nonlocal words, stdin
        flush()
        if words:
            parts.append(Part(words, stdin))
        elif stdin:
            raise Unsure("a here-document without a command")
        words, stdin = [], False

    while i < n:
        c = text[i]
        if c in " \t":
            flush()
            i += 1
        elif c in "\n;":
            if text.startswith(";;", i):
                raise Unsure(";;")
            end_part()
            i += 1
        elif c in "&|":
            if text.startswith("|&", i) or text.startswith("&>", i):
                raise Unsure(text[i:i + 2])
            end_part()
            i += 2 if text[i:i + 2] in ("&&", "||") else 1
        elif c in "<>":
            fd = "".join(buf) if started and not quoted and "".join(buf) in ("1", "2") else None
            if started and fd is None:
                raise Unsure("a redirection glued to a word")
            buf, started = [], False
            if text.startswith("2>&1", i - 1) and fd == "2" and text.startswith(">&1", i) and (i + 3 >= n or text[i + 3] in BOUNDARY):
                i += 3
                continue
            if c == "<" and fd is None and (m := STDIN_HEREDOC.match(text, i)):
                stdin = True
                flush()
                i = m.end()
                continue
            if c == ">" and not text.startswith(">>", i) and not text.startswith(">&", i) and not text.startswith(">|", i):
                m = re.compile(r">[ \t]*/dev/null(?=[ \t\n;&|]|$)").match(text, i)
                if m:
                    i = m.end()
                    continue
            raise Unsure("a redirection other than 2>&1 and >/dev/null")
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise Unsure("an unclosed quote")
            buf.append(text[i + 1:j])
            started = quoted = True
            i = j + 1
        elif c == '"':
            m = DQ_HEREDOC.match(text, i) if not started else None
            if m and m.group(1) not in m.group(2).split("\n") and (m.end() >= n or text[m.end()] in BOUNDARY):
                buf.append("MESSAGE")
                started = quoted = heredoc = True
                i = m.end()
                continue
            started = quoted = True
            j = i + 1
            while True:
                if j >= n:
                    raise Unsure("an unclosed quote")
                ch = text[j]
                if ch == '"':
                    break
                if ch in "$`":
                    raise Unsure("$ or a backtick")
                if ch == "\\" and j + 1 < n and text[j + 1] in '"\\\n':
                    if text[j + 1] != "\n":
                        buf.append(text[j + 1])
                    j += 2
                    continue
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
        elif c in "$`(){}":
            raise Unsure("$, a backtick, a subshell or braces")
        elif c == "#" and not started:
            while i < n and text[i] != "\n":
                i += 1
        elif c == "~" and not started:
            if i + 1 < n and text[i + 1] not in "/" + BOUNDARY:
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

READ_ONLY = {"ls", "cat", "head", "tail", "grep", "wc", "sort", "find", "echo", "test", "[", "pwd", "diff"}
FIND_WRITES = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
SORT_FLAGS = set("bdfghiMnRrsuVz")
SORT_LONG = {"--numeric-sort", "--reverse", "--unique", "--ignore-case", "--human-numeric-sort", "--version-sort", "--stable",
             "--ignore-leading-blanks", "--general-numeric-sort", "--month-sort", "--dictionary-order", "--random-sort", "--zero-terminated"}
GIT_READ = {"status", "diff", "log", "show", "rev-parse", "ls-files"}
GIT_DANGEROUS = ("output", "output-directory", "upload-pack", "receive-pack", "exec", "exec-path", "ext-diff", "open-files-in-pager",
                 "git-dir", "work-tree", "namespace", "force", "force-with-lease", "force-if-includes", "no-verify", "config-env")
GIT_SHORT_FORCE = {"add", "checkout", "switch", "branch", "fetch", "worktree", "stash", "restore"}
TEST_FLAGS = {"-q", "-qq", "-v", "-vv", "-x", "-s", "-l", "--lf", "--ff", "--sw", "--no-header", "--exitfirst", "--last-failed",
              "--failed-first", "-ra", "-rA", "--color=no", "--color=yes"}
TEST_VALUED = {"-k", "-m", "--tb", "--maxfail", "--durations"}
SAANI_OK = {"start", "land", "status", "comment", "kpis", "progress", "ui", "map", "board"}
AUTO_WORDS = {"on", "off", "status", "pick", "report", "no-action"}
LOCALHOST = {"localhost", "127.0.0.1", "::1"}


def is_python(word):
    name = re.split(r"[\\/]", word.lower())[-1].removesuffix(".exe")
    return bool(re.fullmatch(r"python(3(\.\d+)?)?|py", name)) or (bool(sys.executable) and word == sys.executable)


def saani_start(words, cwd, temp=None):
    """How many words call the plugin's own saani (`saani`, its bin/saani, or `python -I <plugin>/saani.py`), or 0."""
    if not words:
        return 0
    if words[0] == "saani":
        return 1
    launchers = {norm(os.path.join(PLUGIN_ROOT, "bin", n)) for n in ("saani", "saani.cmd")}
    if resolve(words[0], cwd, temp) in launchers:
        return 1
    if is_python(words[0]) and len(words) >= 3 and words[1] == "-I" and resolve(words[2], cwd, temp) == norm(os.path.join(PLUGIN_ROOT,
                                                                                                                     "saani.py")):
        return 3
    return 0


def test_extras(words, ctx):
    """The words after one of the project's test commands ([tests] tier1/full/check, `{python}` being any Python), or None."""
    for test in ctx.tests:
        if len(words) >= len(test) and all(is_python(w) if t == "{python}" else w == t for w, t in zip(words, test)):
            return words[len(test):]
    return None


def test_command(words, ctx, cwd, who):
    extras = test_extras(words, ctx)
    if extras is None:
        return deny("not a test command", "not one of the project's test commands ([tests] in saani.toml)", who)
    k = 0
    while k < len(extras):
        w = extras[k]
        if w in TEST_VALUED:
            k += 2
            continue
        if w in TEST_FLAGS or w.split("=", 1)[0] in TEST_VALUED or not w.startswith("-"):
            k += 1
            continue
        return deny("test option", f"the test option {w[:30]} (options like --basetemp, -p, --rootdir, -c and -o change where tests write "
                    "or what they load)", who, how="run the test command as saani.toml has it, with -q, -x, -k, a test path")
    if scan(ctx, extras, cwd):
        return deny("secret", "a test command naming a secret file", who, how=SECRETS_HOW)
    return None


def secret_word(ctx, word, cwd):
    """True when a word of a command names a secret: the word, its pieces around : and =, and what its glob matches."""
    for piece in {word, *re.split(r"[:=@]", word)}:
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
    """The first word naming a secret, or None (`skip`: indexes of words that are text, not paths)."""
    for k, w in enumerate(words):
        if k not in skip and secret_word(ctx, w, cwd):
            return w
    return None


def command(part, ctx, cwd):
    """(verdict or None for allowed, the folder after it) for one simple command."""
    words = part.words
    cmd, args = words[0], words[1:]
    if part.stdin and not (cmd == "git" and args[:1] == ["commit"]):
        return deny("unsure", "a here-document other than git commit -F -", cmd), cwd
    if any(w.heredoc for w in words) and cmd not in ("git", "gh"):
        return deny("unsure", "a $(cat <<'EOF' ...) other than a commit message or a gh body or title", cmd), cwd
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", cmd):
        return deny("unsure", "an environment assignment", cmd, how="run the command without it"), cwd
    if cmd == "sudo":
        return deny("sudo", "sudo", cmd), cwd
    if cmd == "cd":
        if len(args) != 1 or args[0].startswith("-") or args[0].glob:
            return deny("cd outside the worktrees", "cd needs one plain folder", cmd), cwd
        target = ctx.path(args[0], cwd)
        if not ctx.cd_ok(target):
            return deny("cd outside the worktrees", "cd outside a worktree and the run's folder", cmd,
                        how="work in the run's folder or an issue's worktree"), cwd
        return None, target
    start = saani_start(words, cwd, ctx.temp)
    if start:
        return saani_command(words[start:], ctx, cwd), cwd
    if test_extras(words, ctx) is not None:
        return test_command(words, ctx, cwd, cmd), cwd
    if is_python(cmd):
        return deny("python", "Python runs only the plugin's saani.py and the project's test commands", cmd), cwd
    if cmd in READ_ONLY:
        return read_only(cmd, args, ctx, cwd), cwd
    if cmd == "git":
        return git_command(part, ctx, cwd), cwd
    if cmd == "gh":
        return gh_command(args, ctx, cwd), cwd
    if cmd in ("curl", "wget"):
        return fetch_command(cmd, args, ctx, cwd), cwd
    if cmd == "rm":
        return rm_command(args, ctx, cwd), cwd
    return deny("not on the list", f"the command {safe_word(cmd) or '?'!r} is not on the guard's list", cmd), cwd


def saani_command(args, ctx, cwd):
    sub = args[0] if args else ""
    if sub == "test":
        rest = list(args[1:])
        if "--" in rest:
            rest = rest[rest.index("--") + 1:]
        else:
            while rest and rest[0].startswith("--kind"):
                rest = rest[1:] if "=" in rest[0] else rest[2:]
        return test_command(rest, ctx, cwd, "saani")
    if sub == "auto":
        if args[1:2] and args[1] in ("on", "off"):
            return deny("unsure", f"saani auto {args[1]} with other commands", "saani", how=f"run saani auto {args[1]} alone")
        if args[1:2] and not args[1].startswith("-") and args[1] not in AUTO_WORDS:
            return deny("saani command", f"saani auto {safe_word(args[1])}", "saani")
        return None
    if sub == "ci":
        return None if args[1:2] in (["status"], ["pr"]) else deny("saani command", "saani ci other than status and pr", "saani",
                                                                 how="the owner runs it")
    if sub in SAANI_OK:
        return None
    return deny("saani command", f"saani {safe_word(sub) or ''} in an auto run (merge, prune, deploy, projects... are the owner's)".replace(
        "  ", " "), "saani", how="the owner runs it")


def read_only(cmd, args, ctx, cwd):
    skip = set()
    if cmd == "echo":
        skip = set(range(len(args)))
    elif cmd == "find":
        if any(a in FIND_WRITES for a in args):
            return deny("find writes", "find with -delete or -exec", cmd, how="list with find, then act with an allowed command")
    elif cmd == "sort":
        k = 0
        while k < len(args):
            a = args[k]
            if a in ("-k", "-t"):
                k += 2
                continue
            if not a.startswith("-") or a in SORT_LONG or a.startswith(("--key=", "--field-separator=")) or (
                    re.fullmatch(r"-[a-zA-Z]+", a) and set(a[1:]) <= SORT_FLAGS):
                k += 1
                continue
            return deny("sort option", f"the sort option {a[:30]}", cmd)
    elif cmd in ("grep", "diff"):
        if any(a in ("--recursive", "--dereference-recursive", "-d", "--directories=recurse") or re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", a)
               for a in args):
            return deny("recursive read", f"{cmd} -r reads every file of a folder, secrets included", cmd,
                        how="use the Grep tool, or name the files")
        if cmd == "grep":
            skip = {k + 1 for k, a in enumerate(args) if a in ("-e", "--regexp")} | {k for k, a in enumerate(args) if a.startswith("--regexp=")}
            files = any(a in ("-f", "--file") or a.startswith("--file=") or re.fullmatch(r"-[a-zA-Z]*f.*", a) for a in args)
            if not skip and not files:
                positional = [k for k, a in enumerate(args) if not a.startswith("-")]
                skip = set(positional[:1])
    if scan(ctx, args, cwd, skip):
        return deny("secret", f"{cmd} of a secret file", cmd, how=SECRETS_HOW)
    return None


def git_command(part, ctx, cwd):
    args, gcwd = list(part.words[1:]), cwd
    while args and args[0].startswith("-"):
        if args[0] == "-C" and len(args) > 1:
            gcwd = ctx.path(args[1], cwd)
            if args[1].glob or not ctx.cd_ok(gcwd):
                return deny("cd outside the worktrees", "git -C outside a worktree and the run's folder", "git")
            args = args[2:]
        elif args[0] == "--no-pager":
            args = args[1:]
        else:
            return deny("git option", f"the git option {args[0][:30]} before the command", "git")
    if not args:
        return deny("not on the list", "git without a command", "git")
    sub, rest = args[0], args[1:]
    text = set()
    if sub in ("commit", "stash"):
        for k, w in enumerate(rest):
            if w == "--message" or re.fullmatch(r"-[a-zA-Z]*m", w):
                text.add(k + 1)
            elif w.startswith("--message=") or re.fullmatch(r"-[a-zA-Z]*m.+", w):
                text.add(k)
    if any(w.heredoc for k, w in enumerate(rest) if k not in text) or (sub != "commit" and any(w.heredoc for w in rest)):
        return deny("unsure", "a $(cat <<'EOF' ...) other than the value of git commit -m", "git")
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
    for w in words:
        name = w[2:].split("=", 1)[0] if w.startswith("--") else ""
        if name and (name.startswith("force") or any(d.startswith(name) for d in GIT_DANGEROUS)):   # git takes abbreviations: --out
            if name.startswith("f"):
                return deny("force flag", "a force flag", "git")
            if name.startswith("no-v"):
                return deny("no-verify", f"git {sub} --no-verify skips the commit-msg hook", "git")
            return deny("git option", f"the git option --{name[:30]} (it runs a program, writes a file or moves the repository)", "git")
        if w == "-f" or (sub in GIT_SHORT_FORCE and re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", w)):
            return deny("force flag", "a force flag", "git")
    verdict = git_sub(sub, words, ctx, gcwd, part)
    if verdict:
        return verdict
    if scan(ctx, rest, gcwd, text):
        return deny("secret", f"git {sub} of a secret file", "git", how=SECRETS_HOW)
    return None


def git_paths_writable(paths, ctx, gcwd, sub):
    for p in paths:
        if p.glob:
            continue                                         # git matches pathspecs inside the repository only
        target = ctx.path(p, gcwd)
        if not ctx.writable(target) or protected(target, ctx):
            return deny("write outside the worktrees", f"git {sub} of a path outside the worktree or protected", "git")
    return None


def git_sub(sub, words, ctx, gcwd, part):
    no = deny("not on the list", f"git {sub} {' '.join(words[:1])[:30]}".strip() + " is not on the guard's list", "git")
    if part.stdin and sub != "commit":
        return deny("unsure", "a here-document other than git commit -F -", "git")
    if sub in GIT_READ or sub == "add":
        return None
    if sub == "commit":
        if any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", w) for w in words):
            return deny("no-verify", "git commit -n skips the commit-msg hook", "git")
        if part.stdin and not any(words[k:k + 2] == ["-F", "-"] or w in ("--file=-",) or words[k:k + 2] == ["--file", "-"]
                                  for k, w in enumerate(words)):
            return deny("unsure", "a here-document without git commit -F -", "git")
        return None
    if sub == "restore":
        paths = [w for w in words if not w.startswith("-")]
        return git_paths_writable(paths, ctx, gcwd, sub)
    if sub == "merge":
        rest = [w for w in words if w not in ("--no-edit", "-q", "--quiet")]
        if rest in ([f"origin/{ctx.integration}"], ["--abort"], ["--continue"]):
            return None
        return deny("not on the list", f"git merge other than origin/{ctx.integration}", "git")
    if sub == "fetch":
        positional = []
        for w in words:
            if w in ("-q", "--quiet", "--prune", "-p", "--tags", "--no-tags"):
                continue
            if w.startswith("-"):
                return deny("not on the list", f"the git fetch option {w[:30]}", "git")
            positional.append(w)
        if positional and positional[0] != "origin":
            return deny("not on the list", "git fetch from a remote other than origin", "git")
        if any(":" in w or w.startswith("+") for w in positional[1:]):
            return deny("not on the list", "git fetch with a refspec that writes a branch (: or +)", "git")
        return None
    if sub in ("switch", "checkout"):
        if "--" in words:
            before, after = words[:words.index("--")], words[words.index("--") + 1:]
            if len(before) <= 1 and not any(w.startswith("-") for w in before):
                return git_paths_writable(after, ctx, gcwd, sub)
            return no
        options = {"-b", "-c", "--create", "-q", "--quiet", "--track", "--no-track", "-t"}
        bad = [w for w in words if w.startswith("-") and w not in options]
        if bad:
            return deny("not on the list", f"git {sub} with {bad[0][:30]}", "git")
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
        return None if words in (["list"], ["list", "--porcelain"]) else deny(
            "not on the list", "git worktree other than list", "git", how="saani start makes worktrees, saani prune removes them")
    if sub == "branch":
        listing = {"--list", "-l", "--show-current", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose"}
        valued = {"--merged", "--no-merged", "--contains", "--no-contains", "--points-at", "--sort"}
        k, pattern_ok = 0, "--list" in words or "-l" in words
        while k < len(words):
            w = words[k]
            if w in valued:
                k += 2
                continue
            if w.startswith(("--format=", "--sort=")) or w in listing or (pattern_ok and not w.startswith("-")):
                k += 1
                continue
            return deny("not on the list", "git branch other than listing", "git", how="saani start makes branches, saani prune deletes them")
        return None
    if sub == "stash":
        return None if words[:1] in ([], ["push"], ["pop"], ["list"], ["show"]) else no
    return no


# gh options per command, pflag style: `x/name=` a short and a long option taking a value, `x/name` a flag, `/name=` long only.
GH_COMMON = "R/repo="
GH_OUT = "/json= q/jq= t/template="
GH_FLAGS = {
    ("issue", "view"): "c/comments " + GH_OUT,
    ("issue", "list"): "a/assignee= A/author= l/label= L/limit= s/state= S/search= m/milestone= /mention= /app= " + GH_OUT,
    ("pr", "view"): "c/comments " + GH_OUT,
    ("pr", "list"): "a/assignee= A/author= B/base= H/head= l/label= L/limit= s/state= S/search= d/draft /app= " + GH_OUT,
    ("pr", "checks"): "/watch i/interval= /fail-fast /required " + GH_OUT,
    ("pr", "diff"): "/color= /name-only /patch",
    ("run", "view"): "j/job= /log /log-failed v/verbose /exit-status a/attempt= " + GH_OUT,
    ("run", "list"): "b/branch= c/commit= e/event= L/limit= s/status= u/user= w/workflow= /created= a/all " + GH_OUT,
    ("run", "watch"): "i/interval= /exit-status /compact",
    ("issue", "comment"): "b/body= F/body-file= /edit-last",
    ("pr", "comment"): "b/body= F/body-file= /edit-last",
    ("issue", "create"): "t/title= b/body= F/body-file= l/label= a/assignee= m/milestone= p/project= T/template=",
    ("issue", "edit"): "/add-label= /remove-label=",
    ("pr", "create"): "B/base= H/head= t/title= b/body= F/body-file= l/label= a/assignee= r/reviewer= m/milestone= p/project= d/draft "
                      "T/template= /dry-run /no-maintainer-edit",
    ("pr", "edit"): "t/title= b/body= F/body-file= B/base= /add-label= /remove-label= /add-reviewer= /remove-reviewer= /add-assignee= "
                    "/remove-assignee= m/milestone= /remove-milestone",
    ("api", ""): "p/preview= q/jq= t/template= i/include /paginate /slurp /silent /verbose /cache=",      # no -H: a header can change the method
}
GH_HEREDOC = {("issue", "comment"), ("pr", "create"), ("pr", "comment")}
GH_DENIED = {"release": "gh release", "repo": "gh repo", "secret": "gh secret", "auth": "gh auth"}


def pflags(args, spec):
    """([(long name, value or None, index of a separate value word or None)], [(positional, index)]) of gh's arguments, read the way
    gh's pflag reads them: `-Bdev`, `-B dev`, `-B=dev`, `--base dev`, `--base=dev`, clusters like `-iq.x`. An unknown option raises
    Unsure."""
    short, long = {}, {}
    for item in spec.split():
        s, _, name = item.partition("/")
        takes = name.endswith("=")
        name = name.rstrip("=")
        long[name] = takes
        if s:
            short[s] = (name, takes)
    flags, positional, k = [], [], 0
    while k < len(args):
        w = args[k]
        if w == "--":
            positional += [(a, j) for j, a in enumerate(args[k + 1:], k + 1)]
            break
        if w.startswith("--"):
            name, eq, value = w[2:].partition("=")
            if name not in long:
                raise Unsure(f"the gh option --{name[:30]}")
            if long[name]:
                vi = None
                if not eq:
                    k += 1
                    if k >= len(args):
                        raise Unsure(f"--{name} without a value")
                    value, vi = args[k], k
                flags.append((name, value, vi))
            elif eq:
                raise Unsure(f"--{name}=")
            else:
                flags.append((name, None, None))
        elif w.startswith("-") and len(w) > 1:
            j = 1
            while j < len(w):
                if w[j] not in short:
                    raise Unsure(f"the gh option -{w[j]}")
                name, takes = short[w[j]]
                if takes:
                    value, vi = w[j + 1:], None
                    if value.startswith("="):
                        value = value[1:]
                    if not value and j + 1 == len(w):
                        k += 1
                        if k >= len(args):
                            raise Unsure(f"-{w[j]} without a value")
                        value, vi = args[k], k
                    flags.append((name, value, vi))
                    break
                flags.append((name, None, None))
                j += 1
        else:
            positional.append((w, k))
        k += 1
    return flags, positional


def gh_command(args, ctx, cwd):
    args = list(args)
    group = args[0] if args else ""
    if group == "pr" and args[1:2] == ["merge"]:
        return deny("gh pr merge", "gh pr merge (a run never merges)", "gh", how="the owner merges with saani merge")
    if group in GH_DENIED:
        return deny(GH_DENIED[group], GH_DENIED[group], "gh", how="the owner does it")
    key = ("api", "") if group == "api" else (group, args[1] if len(args) > 1 else "")
    if key not in GH_FLAGS:
        return deny("not on the list", f"gh {' '.join(key)}".strip()[:40] + " is not on the guard's list", "gh")
    rest = args[1:] if group == "api" else args[2:]
    try:
        flags, positional = pflags(rest, GH_COMMON + " " + GH_FLAGS[key])
    except Unsure as e:
        return deny("gh option", f"{e} (not on the guard's list for gh {' '.join(key).strip()})", "gh")
    names = [f[0] for f in flags]
    for once in ("repo", "base", "head"):
        if names.count(once) > 1:
            return deny("unsure", f"gh with --{once} twice", "gh")
    values = {f[0]: f[1] for f in flags}
    if "repo" in values and values["repo"].lower() != ctx.repo.lower():
        return deny("another repository", f"gh on a repository other than {ctx.repo}", "gh")
    text_at = {f[2] for f in flags if f[0] in ("title", "body") and f[2] is not None}
    heredocs = {k for k, w in enumerate(rest) if w.heredoc}
    if heredocs and (key not in GH_HEREDOC or not heredocs <= text_at):
        return deny("unsure", "a $(cat <<'EOF' ...) other than the --body or --title of gh issue comment, pr create, pr comment", "gh")
    if key == ("issue", "edit") and not all(re.fullmatch(r"#?\d+|https://\S+", p) for p, _ in positional):
        return deny("gh issue edit", "gh issue edit other than labels", "gh", how="comment on the issue instead")
    if key == ("pr", "create") and (values.get("base") != ctx.production or values.get("head") != ctx.integration):
        return deny("gh pr create", f"gh pr create other than --base {ctx.production} --head {ctx.integration}", "gh",
                    how=f"open the one pull request with --base {ctx.production} --head {ctx.integration}")
    if key == ("pr", "edit") and "base" in values and values["base"] != ctx.production:
        return deny("gh pr edit", f"gh pr edit --base other than {ctx.production}", "gh")
    if key == ("api", ""):
        if len(positional) != 1:
            return deny("gh api write", "gh api needs exactly one endpoint", "gh")
        endpoint = positional[0][0]
        pieces = [x for x in re.split(r"[/?&=]", endpoint) if x]
        if "secret" in endpoint.lower() or any(secret_name(x, strict=False) for x in pieces):
            return deny("secret", "gh api on secrets", "gh", how=SECRETS_HOW)
    text_values = {f[2] for f in flags if f[0] in ("title", "body")}
    checked = [rest[i] for i in range(len(rest)) if i not in text_values and i not in heredocs]
    for name, value, vi in flags:
        if vi is None and value is not None and name not in ("title", "body"):
            checked.append(Word(value))
    if scan(ctx, checked, cwd):
        return deny("secret", "gh naming a secret file", "gh", how=SECRETS_HOW)
    return None


def fetch_command(cmd, args, ctx, cwd):
    """curl (and wget to stdout) to localhost only, GET only."""
    valued = {"-H", "--header", "-m", "--max-time", "--connect-timeout", "-w", "--write-out", "-A", "--user-agent", "--retry"}
    flags = {"--silent", "--show-error", "--fail", "--location", "--include", "--head", "--verbose", "--compressed", "--quiet",
             "--server-response"}
    short = "sSfLiIv" if cmd == "curl" else "qS"
    if any(a.startswith("@") or "=@" in a for a in args):
        return deny(f"{cmd} write", f"{cmd} reading a file (@)", cmd)
    if scan(ctx, args, cwd):
        return deny("secret", f"{cmd} naming a secret file", cmd, how=SECRETS_HOW)
    urls, k = [], 0
    while k < len(args):
        w = args[k]
        if cmd == "wget" and (w in ("-qO-", "-O-") or (w == "-O" and args[k + 1:k + 2] == ["-"])):
            k += 2 if w == "-O" else 1
            continue
        if w in valued and k + 1 < len(args):
            k += 2
            continue
        if w in flags or (re.fullmatch(r"-[a-zA-Z]+", w) and set(w[1:]) <= set(short)):
            pass
        elif w.startswith("-"):
            return deny(f"{cmd} write", f"the {cmd} option {w[:30]} (methods, data, uploads, files and configs are refused)", cmd)
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
    for w in paths:
        if w.glob:
            return deny("unsure", "rm with a glob", "rm", how="name each file")
        p = ctx.path(w, cwd)
        if secret_name(w, p, strict=False):
            return deny("secret", "rm of a secret file", "rm", how=SECRETS_HOW)
        if ctx.wt_dir and (p == ctx.wt_dir or os.path.dirname(p) == ctx.wt_dir) or p == ctx.folder:
            return deny("rm a worktree", "rm of a worktree or of the worktrees folder", "rm", how="saani prune removes finished worktrees")
        if not ctx.writable(p):
            return deny("rm outside the worktrees", "rm outside the worktrees folder, the run's folder and temp", "rm")
        if holds_protected(p, ctx):
            return deny("guard file", "rm of a protected file or a folder holding one", "rm",
                        how="those change only in a session with the owner present")
    return None


def bash(text, ctx):
    """Every part of a compound command must be allowed; a `cd` moves the folder the later parts are read from."""
    try:
        parts = split_bash(text)
    except Unsure as e:
        return deny("unsure", f"the guard does not read {e} in a shell command", how="write the command plainly: simple commands, "
                    "quoted text, no $, backticks, redirections (but 2>&1 and >/dev/null) or subshells")
    if not parts:
        return deny("unreadable command", "an empty command")
    cwd = ctx.cwd
    for part in parts:
        verdict, cwd = command(part, ctx, cwd)
        if verdict:
            return verdict
    return allow("every part of the command is on the guard's list")


# ---------------------------------------------------------------- the switch: saani auto on / off, seen by the guard

def auto_switch(command, cwd):
    """("on", [its options]) or ("off", []) when the whole Bash command is the plugin's `saani auto on|off`, else None."""
    if "auto" not in command:
        return None
    parts = split_bash(command)
    if len(parts) != 1 or parts[0].stdin:
        return None
    words = parts[0].words
    start = saani_start(words, cwd)
    if not start or words[start:start + 1] != ["auto"]:
        return None
    if words[start + 1:start + 2] == ["on"]:
        return "on", list(words[start + 2:])
    if words[start + 1:] == ["off"]:
        return "off", []
    return None


def register(session_id, options, cwd):
    """Registers the session from `saani auto on [--id ID] [--folder PATH] [--deploy]` run in `cwd`: the repository's main checkout, its
    worktrees folder and settings from the main checkout's saani.toml, the run's folder (inside the worktrees folder; default: `cwd` when
    it is there). Returns the entry, or None when it cannot (then the guard stays out of the way)."""
    import time
    import tomllib
    from . import config
    opts, k = {"deploy": False}, 0
    while k < len(options):
        w = options[k]
        name, eq, value = w.partition("=")
        if name in ("--id", "--folder"):
            if not eq:
                if k + 1 >= len(options):
                    return None
                value, k = options[k + 1], k + 1
            opts[name[2:]] = value
        elif w == "--deploy":
            opts["deploy"] = True
        else:
            return None
        k += 1
    folder = resolve(opts["folder"], cwd) if "folder" in opts else norm(cwd)
    dirs = git_dirs(folder if os.path.isdir(folder) else cwd)
    if not dirs or os.path.basename(dirs[1]).lower() != ".git":
        return None
    main = os.path.dirname(dirs[1])
    toml = os.path.join(main, config.FILE)
    if not os.path.isfile(toml):
        return None
    with open(toml, "rb") as f:
        cfg = config.from_toml(tomllib.load(f), main)
    wt = norm(cfg.wt_dir)
    if not (inside(folder, wt) and folder != wt):
        if "folder" in opts:
            return None
        folder = None
    run_id = opts.get("id") or (os.path.basename(folder).removeprefix("auto-") if folder else time.strftime("%Y-%m-%d_%H%M"))
    return turn_on(session_id, run_id, main, wt, folder, deploy=opts["deploy"], repo=cfg.repo, integration=cfg.integration,
                   production=cfg.production, tests=[cfg.tier1, cfg.full, cfg.check], worker_branch=cfg.worker_branch.pattern)


# ---------------------------------------------------------------- the hook

def emit(decision, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}))


def main(raw=None):
    """The hook: JSON on stdin (or `raw`, already read); a decision on stdout for an auto session, nothing in present mode. Never
    raises."""
    try:
        if raw is None:
            raw = sys.stdin.buffer.read()
        event = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(event, dict):
            event = None
    except Exception:
        event = None
    error = "Saani guard (auto run): guard error: {}. To go on: the owner looks at the guard."
    if event is None:
        try:
            some = any(n.endswith(".json") for n in os.listdir(registry()))
        except FileNotFoundError:
            some = False
        except OSError:
            some = True
        if some:
            emit("deny", error.format("unreadable hook input"))
        return 0
    session_id = event.get("session_id")
    try:
        st = state(session_id)
    except Exception as e:
        log_refusal({}, event.get("tool_name", "?"), "guard error", "")
        emit("deny", error.format(f"registry {type(e).__name__}"))
        return 0
    cwd = event.get("cwd")
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    try:
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else os.getcwd()
        command = tool_input.get("command")
        switch = auto_switch(command, cwd) if event.get("tool_name") == "Bash" and isinstance(command, str) else None
    except Exception:
        switch = None
    if switch and switch[0] == "on":
        try:
            entry = register(session_id, switch[1], cwd)
        except (Exception, SystemExit):
            entry = None
        if entry:
            emit("allow", f"Saani guard: auto run {entry['id']} on for this session; the guard decides every call until saani auto off.")
        elif st is not None:
            log_refusal(st, "Bash", "auto on refused", "saani")
            emit("deny", "Saani guard (auto run): refused, saani auto on could not register this run (not inside a Saani project, or a "
                         "folder outside its worktrees). To go on: run it from the run's folder.")
        return 0
    if switch and switch[0] == "off":
        if st is not None:
            try:
                turn_off(session_id)
                emit("allow", f"Saani guard: auto run {st.get('id')} off; Claude Code's own permissions apply again.")
            except Exception as e:
                emit("deny", error.format(f"auto off {type(e).__name__}"))
        return 0
    if st is None:
        return 0
    try:
        decision, reason, code, word = decide(event, context(cwd, st))
    except (Exception, SystemExit) as e:
        decision, reason, code, word = "deny", error.format(type(e).__name__), "guard error", ""
    if decision == "deny":
        log_refusal(st, event.get("tool_name", "?"), code, word)
    emit(decision, reason)
    return 0


def add_parsers(sub):
    """`saani guard`, listed in the help; saani.py runs it before loading anything else."""
    p = sub.add_parser("guard", help="the Claude Code PreToolUse hook of the plugin (reads the hook's JSON on stdin)")
    p.set_defaults(run=lambda args: main())
