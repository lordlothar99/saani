"""The guard: a Claude Code hook shipped by the plugin (hooks/hooks.json runs `saani guard` before every tool call, PreToolUse, and after
every Bash call, PostToolUse). Standard library only.

  saani guard        reads the hook's JSON on stdin; prints Claude Code's PreToolUse decision, or nothing

Two modes (docs/SAANI.md, The guard), told apart by the hook's `session_id` only, never by the folder or a file the model can write:
- **present** (no entry for this session in `~/.saani/auto/`): it prints nothing, so Claude Code's normal permissions apply, to
  `saani auto on` too: the owner is asked unless their own rules allow it. A present session never switches itself into auto mode.
- **auto** (`~/.saani/auto/<session_id>.json`): written by the PostToolUse hook once the whole Bash command `saani auto on ...` ran,
  ended normally and printed its marker line (`saani auto: on <id> folder=<path>`); removed by the owner from a terminal (`saani auto off
  --run <id>`, `turn_off_run`). After 12 hours it is over (renamed `<session_id>.over`): every call of that session is refused, it never
  falls back to present mode. Every tool call gets "allow" or "deny", never "ask", because an away run must not wait;
  `saani auto on` and `off` are refused (a run never re-arms or ends itself). The rules are an allowlist: what the guard cannot read for
  sure is refused, with the reason and how to go on. Each refusal adds a line to `~/.saani/auto/refusals.jsonl` (time, run, tool, short
  reason, the command's first word only).

Fail closed: when the registry cannot be read and an entry for this session might exist, the call is refused. If the hook cannot run at
all (timeout, no Python), Claude Code's normal permissions apply: it asks.

`state`, `turn_on`, `turn_off`, `turn_off_run` and `refusals` are for the auto run's commands and stop rules. `decide(event, ctx)` is the
whole decision, with no side effect, for the tests.
"""
import json
import os
import re
import sys

REFUSALS = "refusals.jsonl"
SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}")
RUN_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
MAX_HOURS = 12                      # an entry older than this is over (the run's own limit is 10 hours): every call refused
OVER = ".over"                      # the mark of a session whose entry is over: it stays until the owner's `saani auto off --run <id>`
WINDOWS = os.name == "nt"
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
HOW = ("skip it and note it for the report; the owner can do it when present, or a reviewed change can add a rule to the guard "
       "(docs/SAANI.md, The guard)")
SECRETS_HOW = "the owner handles secrets, never an agent"


class RegistryError(Exception):
    """The registry could not be read: the guard cannot tell present from auto, so it refuses."""


class Refuse(Exception):
    """A refusal found deep inside a check, before the guard touches the filesystem or past its budget (a network path, a glob outside
    the run's places, a walk too long): `decide` turns it into a deny."""

    def __init__(self, code, why, how=HOW):
        super().__init__(why)
        self.code, self.why, self.how = code, why, how


BUDGET_SECONDS = 8.0                # the guard's own time for one decision, every walk and glob together (the hook has 15 s): past it, refused


def network(p):
    """True for a path the system would open over the network: `\\\\host\\share`, `//host/share`, `\\\\?\\UNC\\...`, `\\\\.\\...`."""
    return len(p) >= 2 and p[0] in "\\/" and p[1] in "\\/"


# ---------------------------------------------------------------- the registry of auto sessions (~/.saani/auto)

def registry():
    return os.path.join(os.path.expanduser("~"), ".saani", "auto")


def _entry(session_id):
    return os.path.join(registry(), session_id + ".json") if isinstance(session_id, str) and SESSION.fullmatch(session_id) else None


def _over(path):
    return path[:-len(".json")] + OVER


def state(session_id, now=None):
    """The auto run of this Claude Code session ({"id", "started", "main", "wt_dir", "folder", "deploy", settings}), or None. A session
    whose entry is over gets {..., "over": True}: it was on, so it never falls back to present mode. Raises RegistryError when the entry
    exists but cannot be read, or the registry cannot be looked at."""
    path = _entry(session_id)
    if path is None:
        return None
    try:
        with open(_over(path), encoding="utf-8") as f:
            data = json.load(f)
        return {**data, "over": True} if isinstance(data, dict) else {"over": True}
    except FileNotFoundError:
        pass
    except ValueError:
        return {"over": True}
    except OSError as e:
        raise RegistryError(type(e).__name__) from e
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
        try:
            os.replace(path, _over(path))                    # over: marked, every call refused until the owner's `saani auto off`
        except OSError:
            pass
        return {**data, "over": True}
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
    """Removes this session's entry and its over mark: present mode again. Returns the entry that was there, or None."""
    path = _entry(session_id)
    try:
        was = state(session_id)
    except RegistryError:
        was = {}
    for p in (path, path and _over(path)):
        if p and os.path.exists(p):
            os.remove(p)
    return was


def turn_off_run(run_id):
    """Removes every entry of the run `run_id`, over marks included (the owner's `saani auto off --run <id>`, from a terminal). Returns
    how many."""
    done = 0
    try:
        names = os.listdir(registry())
    except FileNotFoundError:
        return 0
    for name in names:
        if not name.endswith((".json", OVER)):
            continue
        path = os.path.join(registry(), name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id") == str(run_id):
            os.remove(path)
            done += 1
    return done


def refusals(run=None, project=None):
    """{"total": n, "by_reason": {reason: n}} of the refusal log, for the run `run` (None: every line) and, when given, the project
    whose main checkout is `project` (two projects' runs never mix)."""
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
            if project is not None and row.get("project") != norm(project):
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
    project = (st or {}).get("main")
    row = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "run": (st or {}).get("id"),
           "project": norm(project) if project else None, "tool": str(tool)[:40], "reason": reason, "word": safe_word(word)}
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
    Windows. A network path is refused before any filesystem call (looking it up would connect to that host)."""
    p = str(p)
    if re.match(r"^[\\/]cygdrive([\\/]|$)", p, re.I):
        raise Refuse("unsure", "a /cygdrive path (the guard reads Git Bash's /c/..., not Cygwin's mounts)", how="use the C:/... path")
    if WINDOWS:
        m = re.match(r"^/([a-zA-Z])(?=[\\/]|$)", p)                 # Git Bash: /c/Users and /c\Users are both C:\Users
        if m:
            p = m.group(1) + ":" + (p[2:].replace("\\", "/") or "/")
        elif temp and (p == "/tmp" or p.startswith("/tmp/")):
            p = temp + p[4:]
    if tilde and (p == "~" or p.startswith(("~/", "~\\"))):
        p = os.path.expanduser("~") + p[1:]
    full = os.path.join(cwd, p)
    if network(full):
        raise Refuse("network path", "a network path (\\\\host\\share, //host/share): the guard does not look it up, that would connect "
                     "to the host", how="use a local path inside the worktree")
    return norm(full)


def inside(p, base):
    if not base:
        return False
    return p == base or p.startswith(base.rstrip("\\/") + os.sep)


class Context:
    """What the decision needs: the run's folders (normalized), the branches, the repository and the test commands."""

    def __init__(self, cwd, wt_dir=None, folder=None, temp=None, main=None, repo="", integration="dev", production="main",
                 worker_branch=None, tests=(), home=None, session=None):
        import tempfile
        self.session = session                                # the hook's session_id: its own saved tool outputs are readable
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
        self.deadline = None                                  # set by decide(): one deadline for the whole decision

    def in_time(self):
        """Refuses once the decision's time (BUDGET_SECONDS for the whole call, every walk and glob together) is spent."""
        import time
        now = time.monotonic()
        if self.deadline is None:
            self.deadline = now + BUDGET_SECONDS
        if now >= self.deadline:
            raise Refuse("too big", "more to look through than the guard's time for one decision (folder walks, globs)",
                         how="name the files, or a narrower path or glob")

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


def context(cwd, st, session=None):
    return Context(cwd, wt_dir=st.get("wt_dir"), folder=st.get("folder"), main=st.get("main"), repo=st.get("repo", ""),
                   integration=st.get("integration", "dev"), production=st.get("production", "main"),
                   worker_branch=st.get("worker_branch"), tests=st.get("tests") or (), session=session)


# ---------------------------------------------------------------- secrets and protected files

STRONG_NAMES = {".netrc", "_netrc", ".npmrc", "npmrc", ".pypirc", ".pgpass", ".envrc", ".git-credentials", "hosts.yml", ".claude.json",
                "consolehost_history.txt", ".bash_history", ".python_history", ".gitconfig", ".yarnrc.yml", "pip.ini", "pip.conf"}
STRONG_PREFIXES = (".claude.json", ".npmrc", ".netrc")          # their backups too (.claude.json.backup, .npmrc.bak, .netrc~)
STRONG_TAILS = ([".config", "git", "config"], [".m2", "settings.xml"])
SECRET_DIRS = {".ssh", ".gnupg", ".kube", "psreadline"}
KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".ppk", ".jks", ".keystore", ".kdbx")
KEY_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")


def home_claude(c, session=None):
    """True for a path inside ~/.claude/ (Claude Code's own settings, sessions, credentials), but its projects' memory
    (projects/<p>/memory/) and this session's own saved tool outputs (projects/<p>/<session>/tool-results/, never another session's: the
    owner's may hold printed secrets). Judged on the path as given (`..` folded) and, by the caller, on the resolved path too (a link out
    of tool-results/ is judged where it leads). A part's `:stream` and trailing dots and spaces are dropped as Windows drops them; a part
    that is then empty (`...`) or `..` (`.. `) is not read for sure: a secret."""
    if not os.path.isabs(c):
        return False
    home = os.path.join(os.path.expanduser("~"), ".claude")
    own = os.path.normcase(session) if isinstance(session, str) and SESSION.fullmatch(session) else None
    for base in {os.path.normcase(os.path.normpath(home)), norm(home)}:
        p = os.path.normcase(os.path.normpath(c))
        if inside(p, base):
            rel = [x.split(":")[0].rstrip(". ") for x in re.split(r"[\\/]+", p[len(base):]) if x]
            if "" in rel or ".." in rel or rel[:1] != ["projects"]:
                return True
            memory = len(rel) >= 3 and rel[2] == "memory"
            results = own is not None and len(rel) >= 4 and rel[2] == own and rel[3] == "tool-results"
            return not (memory or results)
    return False


def secret_name(raw, resolved=None, strict=True, session=None):
    """True when the path names a secret: .env, .env.*, .envrc, keys (*.pem, *.key, *.p12, *.pfx, *.ppk, *.jks, *.keystore, *.kdbx,
    id_rsa*, id_ed25519*...), anything with "credentials" in its name, .netrc, .npmrc and npmrc, .pypirc, .pgpass, .git-credentials, any
    hosts.yml, .docker/config.json, .git/config (tokens in remote URLs), ~/.claude.json and ~/.claude/ (but its projects' memory),
    PowerShell's history (PSReadLine/, ConsoleHost_history.txt), .bash_history, .python_history, .gitconfig and .config/git/config
    (insteadOf tokens), .m2/settings.xml, .yarnrc.yml, pip.ini, pip.conf, backups of .claude.json, .npmrc and .netrc, anything under .ssh/, .gnupg/, .kube/ or /proc/, a name with "secret" or
    "token" in it. Not `strict` (a word of a shell command that may not be a file, like a branch name): the "secret"/"token" rule needs a
    dot in the name or an existing file."""
    for c in (raw, resolved):
        if not c:
            continue
        low = str(c).replace("\\", "/").lower()
        if re.match(r"^/proc(/|$)", low):
            return True
        parts = [x for x in low.split("/") if x]
        if not parts:
            continue
        if SECRET_DIRS & set(parts) or parts[-2:] == [".docker", "config.json"] or home_claude(str(c), session):
            return True
        if [x.split(":")[0].rstrip(". ") for x in parts[-2:]] == [".git", "config"]:      # Windows: `.git./config` is .git/config
            return True
        name = parts[-1].split(":")[0].rstrip(". ") or parts[-1]       # Windows: `.env::$DATA` and `.env.` are `.env`
        if any(parts[-len(t):] == t for t in STRONG_TAILS):
            return True
        if name == ".env" or name.startswith((".env.",) + STRONG_PREFIXES) or name in STRONG_NAMES or "credentials" in name:
            return True
        if name.endswith(KEY_SUFFIXES) or name.startswith(KEY_PREFIXES):
            return True
        if "secret" in name or "token" in name:
            if strict or "." in name or (resolved and os.path.isfile(resolved)):
                return True
    return False


PROTECTED_PARTS = {".git", ".githooks", ".claude"}
PROTECTED_NAMES = {".mcp.json", "saani.toml", "claude.md", "claude.local.md", "codeowners", ".gitignore"}
WALK_CAP = 20000                    # entries a folder walk looks at; past it the guard cannot tell, so it refuses


def protected(p, ctx):
    """True for what an auto run never writes: any path through .git, .githooks or .claude, .mcp.json, CLAUDE.md and CLAUDE.local.md
    (the agents' instructions), .github/workflows/, .github/actions/, CODEOWNERS, .github/dependabot.yml, ~/.saani, and the guard's own
    files (saani/guard.py, saani.toml, hooks/hooks.json, a checkout's top `hooks/` folder). Trailing dots and spaces of a part are ignored
    (Windows drops them: `.git.` is `.git`)."""
    parts = [x if k == 0 and re.fullmatch(r"[a-z]:", x) else (x.split(":")[0].rstrip(". ") or x)
             for k, x in enumerate(x for x in re.split(r"[\\/]+", p.lower()) if x)]
    if not parts:
        return True
    if PROTECTED_PARTS & set(parts) or parts[-1] in PROTECTED_NAMES:
        return True
    for k, part in enumerate(parts[:-1]):
        if part == ".github" and (parts[k + 1] in ("workflows", "actions") or parts[k + 1] in ("dependabot.yml", "dependabot.yaml")):
            return True
    if parts[-2:] in (["saani", "guard.py"], ["hooks", "hooks.json"]) or inside(p, ctx.saani_home):
        return True
    rel = ctx.checkout_rel(p)
    return bool(rel) and rel[0] == "hooks"


def holds_protected(p, ctx):
    """True when removing `p` would remove something protected or one of the run's places: `p` itself, or anything in its tree (a
    capped walk: a tree too big to look through is refused too)."""
    if protected(p, ctx):
        return True
    if any(base and inside(base, p) for base in (ctx.wt_dir, ctx.folder, ctx.main, ctx.saani_home, ctx.temp, ctx.home)):
        return True
    return os.path.isdir(p) and tree_has(p, lambda path, name: protected(path, ctx), ctx)


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
    import time
    ctx.deadline = time.monotonic() + BUDGET_SECONDS
    try:
        return decide_tool(event, ctx)
    except Refuse as e:
        return deny(e.code, e.why, how=e.how)


def decide_tool(event, ctx):
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
            if value and secret_name(value, ctx.path(value, tilde=True), session=ctx.session):
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
    if inp.get("type"):
        return deny("grep option", "Grep with a type (its file kinds are ripgrep's, the guard does not read them)", how="give a glob, like *.py")
    if glob_.startswith("!"):
        return deny("grep option", "Grep with a negated glob (it searches everything else)", how="give a glob of the files to search")
    if secret_name(inp.get("path") or "", root) or (glob_ and secret_name(glob_)):
        return deny("secret", "Grep in secret files", how=SECRETS_HOW)
    if root == ctx.home or not ctx.in_run(root):
        return deny("grep outside the worktrees", "Grep outside the worktrees folder and the run's folder",
                    how="search inside a worktree, or Read the file")
    if os.path.isdir(root):
        # Every file the search could read is looked at, by its path below the root (.docker/config.json, .kube/config, .ssh/...): a
        # glob narrows it only when it is a plain name pattern (no /, no **, no braces), which ripgrep matches against the file's name.
        import fnmatch
        plain = bool(glob_) and not re.search(r"[/\\{}]|\*\*", glob_)

        def hit(path, name):
            if os.path.isdir(path) or (plain and not fnmatch.fnmatchcase(name, glob_.lower())):
                return False
            return secret_name(os.path.relpath(path, root))
        if tree_has(root, hit, ctx, skip=(".git",)):
            return deny("secret", "Grep in a folder tree that holds a secret file the search could read (or too big to look through)",
                        how="give Grep a glob of plain names that leaves the secret out (*.py), or a deeper path")
    return allow("Grep inside the run's folders")


def tree_has(root, test, ctx, skip=()):
    """True when `test(path, lowercase name)` holds for an entry of the tree, or the tree has more than WALK_CAP entries. Refused once the
    decision's time is spent (ctx.in_time)."""
    seen = 0
    for top, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in skip]
        for name in dirs + files:
            seen += 1
            ctx.in_time()
            if seen > WALK_CAP or test(os.path.join(top, name), name.lower()):
                return True
    return False


# ---------------------------------------------------------------- reading a shell command (a strict grammar)

class Word(str):
    """A shell word with its quotes removed; `glob` when it holds an unquoted *, ? or [; `heredoc` for a "$(cat <<'EOF' ... EOF\n)"."""
    glob = False
    heredoc = False


class Part:
    """One simple command: its words, `stdin` when it ends with a <<'EOF' here-document, and `sep`, the operator after it (&&, ||, ;,
    |, &, a newline; None for the last)."""

    def __init__(self, words, stdin=False, sep=None):
        self.words, self.stdin, self.sep = words, stdin, sep


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

    def end_part(sep=None):
        nonlocal words, stdin
        flush()
        if words:
            parts.append(Part(words, stdin, sep))
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
            end_part(c)
            i += 1
        elif c in "&|":
            if text.startswith("|&", i) or text.startswith("&>", i):
                raise Unsure(text[i:i + 2])
            op = text[i:i + 2] if text[i:i + 2] in ("&&", "||") else c
            end_part(op)
            i += len(op)
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
GIT_READ_ANYWHERE = GIT_READ | {"fetch", "branch", "worktree", "push", "clean", "reset"}   # the last three are refused with their reasons
GIT_DANGEROUS = ("output", "output-directory", "upload-pack", "receive-pack", "exec", "exec-path", "ext-diff", "open-files-in-pager",
                 "git-dir", "work-tree", "namespace", "force", "force-with-lease", "force-if-includes", "no-verify", "config-env")
GIT_SHORT_FORCE = {"add", "checkout", "switch", "branch", "fetch", "worktree", "stash", "restore"}
TEST_FLAGS = {"-q", "-qq", "-v", "-vv", "-x", "-s", "-l", "--lf", "--ff", "--sw", "--no-header", "--exitfirst", "--last-failed",
              "--failed-first", "-ra", "-rA", "--color=no", "--color=yes"}
TEST_VALUED = {"-k", "-m", "--tb", "--maxfail", "--durations"}


def is_python(word):
    """A real Python: `python`, `python3`, `python3.N` or `py` found on the PATH (a bare name, never a path to some file), or the very
    interpreter running saani."""
    if re.fullmatch(r"python(3(\.\d+)?)?|py", word):
        return True
    return bool(sys.executable) and ("/" in word or "\\" in word) and not network(word) and norm(word) == norm(sys.executable)


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
    """The words after one of the project's test commands ([tests] landing/full/check, `{python}` being any Python), or None."""
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
        if w.startswith("@"):
            return deny("test option", "a test argument read from a file (@file)", who)
        if w in TEST_VALUED:
            k += 2
            continue
        if not w.startswith("-"):
            target = ctx.path(w.split("::", 1)[0], cwd)
            if not ctx.writable(target):
                return deny("test option", "a test path outside the run's places (its conftest.py would run)", who,
                            how="test paths inside the worktree")
            k += 1
            continue
        if w in TEST_FLAGS or w.split("=", 1)[0] in TEST_VALUED:
            k += 1
            continue
        return deny("test option", f"the test option {w[:30]} (options like --basetemp, -p, --rootdir, -c and -o change where tests write "
                    "or what they load)", who, how="run the test command as saani.toml has it, with -q, -x, -k, a test path")
    if scan(ctx, extras, cwd):
        return deny("secret", "a test command naming a secret file", who, how=SECRETS_HOW)
    return None


def secret_word(ctx, word, cwd):
    """True when a word of a command names a secret: the word, its pieces around : and =, and what its glob matches. A network path is
    refused (by ctx.path) before any lookup; the //host/... piece of a URL (https://...) is judged by its name only, never looked up."""
    url = re.match(r"[A-Za-z][A-Za-z0-9+.-]+://", word)
    for piece in {word, *re.split(r"[:=@]", word)}:
        if url and network(piece) and not network(word):
            if secret_name(piece, strict=False):
                return True
        elif piece and secret_name(piece, ctx.path(piece, cwd), strict=False, session=ctx.session):
            return True
    if getattr(word, "glob", False):
        # The word as bash reads it (ctx.path: /c/..., /tmp, ~), matched here with a budget; never outside the run's places, never a
        # [...] (bash reads [^x] as "not x", Python's fnmatch as "^ or x", and bash has [[:alpha:]]).
        if "[" in word:
            raise Refuse("unsure", "a glob with [...] (the guard does not read it as bash does)", how="name the files, or use * and ?")
        pattern = ctx.path(word, cwd)
        if not ctx.in_run(pattern):
            raise Refuse("glob outside the worktrees", "a glob outside the worktrees folder and the run's folder",
                         how="name the file, or Read it; globs only inside a worktree")
        for match in glob_matches(pattern, ctx):
            if secret_name(match) or not ctx.in_run(norm(match)):
                return True
    return False


def glob_matches(pattern, ctx):
    """The paths matching the absolute, normalized `pattern`, one path part at a time with * and ? (in any case, hidden names too: never
    fewer than bash lists). Refused past WALK_CAP entries or once the decision's time is spent."""
    import fnmatch
    seen = [0]

    def tick():
        seen[0] += 1
        ctx.in_time()
        if seen[0] > WALK_CAP:
            raise Refuse("too big", "a glob that reaches more entries than the guard looks through in its time",
                         how="name the files, or a narrower glob")
    drive, rest = os.path.splitdrive(pattern)
    level = [drive + os.sep]
    for part in [x for x in re.split(r"[\\/]+", rest) if x]:
        found = []
        for d in level:                                # (a plain part: one lookup per path already counted)
            if not re.search(r"[*?]", part):
                if os.path.lexists(os.path.join(d, part)):
                    found.append(os.path.join(d, part))
                continue
            try:
                with os.scandir(d) as entries:
                    for e in entries:
                        tick()
                        if fnmatch.fnmatchcase(e.name.lower(), part.lower()):
                            found.append(e.path)
            except OSError:
                continue
        level = found
    return level


def scan(ctx, words, cwd, skip=()):
    """The first word naming a secret, or None (`skip`: indexes of words that are text, not paths)."""
    for k, w in enumerate(words):
        ctx.in_time()
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
        return deny("web", f"{cmd} in an auto run", cmd, how="use WebFetch to read a page"), cwd
    if cmd == "rm":
        return rm_command(args, ctx, cwd), cwd
    return deny("not on the list", f"the command {safe_word(cmd) or '?'!r} is not on the guard's list", cmd), cwd


# The saani subcommands an auto run may use, each with its options (True: takes a value) and its positionals (patterns, in order). An
# option off the list, an abbreviation included, is refused; no allowed option runs a command or writes a chosen path.
SAANI_SPEC = {
    ("start",): ({"--slug": True, "--no-assign": False, "--no-comment": False}, [r"\d+"]),
    ("land",): ({"--keep": False, "--wait": True, "--dry-run": False, "--worktree": True, "--no-comment": False}, []),
    ("status",): ({}, []),
    ("comment",): ({"--text": True, "--field": True, "--dry-run": False}, [r"\d+", r"[a-z-]+"]),
    ("kpis",): ({"--since": True, "--until": True, "--baseline": False, "--ref": True, "--json": False}, []),
    ("ui",): ({"--port": True, "--open": False, "--json": False}, []),
    ("progress", "show"): ({}, [r".+"]),
    ("progress", "watch"): ({"--every": True}, [r".+"]),
    ("auto", "pick"): ({"--json": False, "--limit": True, "--only": "+"}, []),
    ("auto", "status"): ({"--check": False}, []),
    ("auto", "report"): ({"--pr": True, "--post": False}, []),
    ("ci", "status"): ({}, []),
    ("ci", "pr"): ({"--dry-run": False, "--pc": False}, []),
}
SAANI_TEXT = {"--slug", "--text", "--field"}


def checkout_top(ctx, p):
    """The top folder of the checkout `p` is in, as far as the run's places tell: the run's folder, or the worktree (the first folder
    below the worktrees folder). Elsewhere `p` itself, which the test command's own path rules then judge."""
    if inside(p, ctx.folder):
        return ctx.folder
    if ctx.wt_dir and inside(p, ctx.wt_dir) and p != ctx.wt_dir:
        return os.path.join(ctx.wt_dir, os.path.relpath(p, ctx.wt_dir).split(os.sep)[0])
    return p


def saani_command(args, ctx, cwd):
    sub = args[0] if args else ""
    sub = "ui" if sub in ("map", "board") else sub
    if sub == "test":
        # saani test [--kind K] [--worktree P] [--] <command>: it runs in the top folder of a checkout, so the test command's paths are
        # checked from there: the named worktree (exactly a worktree's top folder or the run's folder, no glob), else the top of the
        # worktree the current folder is in (a `cd` into a subfolder must not shift the check).
        rest, opts = list(args[1:]), []
        if "--" in rest:
            opts, rest = rest[:rest.index("--")], rest[rest.index("--") + 1:]
        else:
            while rest and (rest[0].startswith("--kind") or rest[0].startswith("--worktree")):
                n = 1 if "=" in rest[0] else 2
                opts, rest = opts + rest[:n], rest[n:]
        where, k = checkout_top(ctx, ctx.path(".", cwd)), 0
        while k < len(opts):
            w = opts[k]
            name, eq, value = w.partition("=")
            if name not in ("--kind", "--worktree"):
                return deny("saani command", "saani test with an option other than --kind and --worktree", "saani")
            glob = bool(getattr(w, "glob", False))           # `--worktree=<glob>`: the word as a whole carries it
            if not eq:
                if k + 1 >= len(opts):
                    return deny("saani command", f"saani test {name} without a value", "saani")
                k += 1
                value, glob = opts[k], bool(getattr(opts[k], "glob", False))
            if name == "--kind" and value not in ("landing", "full", "tier1"):
                return deny("saani command", "saani test with an option other than --kind and --worktree", "saani")
            if name == "--worktree":
                # Exactly a worktree's top folder or the run's folder: saani test runs in the checkout's top folder, so the test paths
                # are checked from the folder they run in (a folder deeper down would let `../..` reach out of the run).
                where = ctx.path(value, cwd)
                top = where == ctx.folder or (ctx.wt_dir is not None and os.path.dirname(where) == ctx.wt_dir)
                if glob or not top or not ctx.cd_ok(where):
                    return deny("cd outside the worktrees", "saani test --worktree that is not a worktree's top folder or the run's folder",
                                "saani")
            k += 1
        return test_command(rest, ctx, where, "saani")
    if sub == "auto" and args[1:2] in (["on"], ["off"]):
        return deny("saani auto switch", f"saani auto {args[1]} inside an auto run (a run never re-arms or ends itself)", "saani",
                    how="report and stop; the owner ends the run from a terminal: saani auto off --run <id>")
    key = (sub, args[1]) if (sub, args[1] if len(args) > 1 else "") in SAANI_SPEC else (sub,)
    if key not in SAANI_SPEC:
        return deny("saani command", f"saani {' '.join(safe_word(a) for a in args[:2])}".strip() + " in an auto run (merge, prune, deploy, "
                    "projects, progress run, the other ci commands... are the owner's)", "saani", how="the owner runs it")
    options, patterns = SAANI_SPEC[key]
    rest, positional, paths, k = list(args[len(key):]), [], [], 0
    while k < len(rest):
        w = rest[k]
        name, eq, value = w.partition("=")
        if w.startswith("-"):
            if name not in options:
                return deny("saani command", f"the option {safe_word(name) or '?'} of saani {' '.join(key)}", "saani")
            takes = options[name]
            if takes == "+":
                k += 1
                while k < len(rest) and re.fullmatch(r"\d+", rest[k]):
                    k += 1
                continue
            if takes and not eq:
                if k + 1 >= len(rest):
                    return deny("saani command", f"saani {' '.join(key)} {name} without a value", "saani")
                k += 1
                value = rest[k]
            elif not takes and eq:
                return deny("saani command", f"saani {' '.join(key)} {name}= takes no value", "saani")
            if takes and name not in SAANI_TEXT:
                paths.append(Word(value))
            if name == "--worktree" and not ctx.cd_ok(ctx.path(value, cwd)):
                return deny("cd outside the worktrees", "saani land --worktree outside a worktree", "saani")
        else:
            positional.append(w)
            paths.append(w)
        k += 1
    if len(positional) != len(patterns) or not all(re.fullmatch(pat, w) for pat, w in zip(patterns, positional)):
        return deny("saani command", f"saani {' '.join(key)} with other arguments", "saani")
    if scan(ctx, paths, cwd):
        return deny("secret", f"saani {' '.join(key)} naming a secret file", "saani", how=SECRETS_HOW)
    return None


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
        verdict, skip = grep_diff_options(cmd, args)
        if verdict:
            return verdict
        if cmd == "diff" and any(not a.startswith("-") and os.path.isdir(ctx.path(a, cwd)) for a in args):
            return deny("recursive read", "diff of a folder reads every file in it", cmd, how="diff two named files")
    if scan(ctx, args, cwd, skip):
        return deny("secret", f"{cmd} of a secret file", cmd, how=SECRETS_HOW)
    return None


GREP_FLAGS = set("nilcEFwvqHho")
DIFF_FLAGS = set("uq")
RECURSIVE_LONG = ("recursive", "dereference-recursive", "directories")


def grep_diff_options(cmd, args):
    """(verdict or None, indexes of pattern words) for grep's and diff's options, an allowlist: grep -n -i -l -c -E -F -w -v -q -H -h -o,
    --color=never, -e PATTERN, -A/-B/-C N; diff -u -q --brief. Recursion in any spelling is refused with its own reason."""
    flags = GREP_FLAGS if cmd == "grep" else DIFF_FLAGS
    recursive = deny("recursive read", f"{cmd} -r reads every file of a folder, secrets included", cmd,
                     how="use the Grep tool, or name the files")
    text, k, ended, has_e = set(), 0, False, False
    while k < len(args):
        a = args[k]
        if ended or a == "-" or not a.startswith("-"):
            k += 1
            continue
        if a == "--":
            ended = True
        elif a.startswith("--"):
            name = a[2:].split("=", 1)[0]
            if name and any(x.startswith(name) for x in RECURSIVE_LONG):
                return recursive, set()
            if not (a == "--color=never" and cmd == "grep") and not (a == "--brief" and cmd == "diff"):
                return deny(f"{cmd} option", f"the {cmd} option {a[:30]}", cmd), set()
        else:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in "rRd":
                    return recursive, set()
                if cmd == "grep" and ch in "eABC":
                    value = a[j + 1:]
                    if not value:
                        k += 1
                        if k >= len(args):
                            return deny(f"{cmd} option", f"{cmd} -{ch} without a value", cmd), set()
                        value = args[k]
                        if ch == "e":
                            text.add(k)
                    elif ch == "e":
                        text.add(k)
                    if ch == "e":
                        has_e = True
                    elif not value.isdigit():
                        return deny(f"{cmd} option", f"{cmd} -{ch} with a value that is not a number", cmd), set()
                    break
                if ch not in flags:
                    return deny(f"{cmd} option", f"the {cmd} option -{ch}", cmd), set()
                j += 1
        k += 1
    if cmd == "grep" and not has_e:
        ended, first = False, None
        for i, a in enumerate(args):
            if i in text:
                continue
            if not ended and a == "--":
                ended = True
            elif ended or not a.startswith("-") or a == "-":
                if args[i - 1:i] and re.fullmatch(r"-[a-zA-Z]*[ABC]", args[i - 1]) and not ended:
                    continue                                     # the N of -A N
                first = i
                break
        if first is not None:
            text.add(first)
    return None, text


def git_command(part, ctx, cwd):
    args, gcwd = list(part.words[1:]), cwd
    while args and args[0].startswith("-"):
        if args[0] == "-C" and len(args) > 1:
            gcwd = ctx.path(args[1], gcwd)               # git reads a relative -C from the previous -C
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
        return deny("git reset --hard", "git reset --hard", "git", how="git restore <path>")
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
    if sub not in GIT_READ_ANYWHERE and not ctx.cd_ok(gcwd):
        return deny("git write outside the worktrees", f"git {sub} outside a worktree and the run's folder (never in the main checkout)",
                    "git", how="cd <worktree> && git ...")
    verdict = commit_takes_paths(rest) if sub == "commit" else None
    verdict = verdict or git_sub(sub, words, ctx, gcwd, part)
    if verdict:
        return verdict
    if scan(ctx, rest, gcwd, text):
        return deny("secret", f"git {sub} of a secret file", "git", how=SECRETS_HOW)
    return None


COMMIT_VALUED = {"-m", "--message", "-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message", "--fixup", "--squash", "--author",
                 "--date", "-t", "--template", "--cleanup", "--trailer"}
COMMIT_PATHS = ("all", "include", "only", "pathspec-from-file")


def commit_takes_paths(rest):
    """A refusal when git commit would take files itself rather than what `git add` staged: -a/--all, -i/--include, -o/--only,
    --pathspec-from-file (and their abbreviations), any path argument (a word that is not an option or its value, anything after --).
    Read on every word, message values included, so a glued `-amx` is seen as -a."""
    refused = deny("git commit paths", "git commit -a, -i, -o, --pathspec-from-file or a path (it would commit files git add refuses)",
                   "git", how="git add <file> <file>, then git commit -m <message>")
    k = 0
    while k < len(rest):
        w = rest[k]
        if not w.startswith("-") or w in ("-", "--"):
            return refused
        if w.startswith("--"):
            name, eq, _ = w[2:].partition("=")
            if any(x.startswith(name) for x in COMMIT_PATHS):
                return refused
            k += 2 if ("--" + name in COMMIT_VALUED and not eq) else 1
            continue
        for j, ch in enumerate(w[1:], 1):              # a cluster: flags, until an option that takes the rest (or the next word)
            if ch in "aio":
                return refused
            if ch in "mFCctSu":
                k += 1 if (j == len(w) - 1 and ch in "mFCct") else 0
                break
        k += 1
    return None


def git_paths_writable(paths, ctx, gcwd, sub):
    """Pathspecs git writes to or stages: named files only (no glob characters, quoted or not, no :magic, no `.`, `..`, folder or path
    ending in /), inside the run's places, not protected, not secret."""
    if not paths:
        return deny("not on the list", f"git {sub} without a file", "git", how="name each file")
    for p in paths:
        if re.search(r"[*?\[]", p) or p.startswith(":"):
            return deny("unsure", f"git {sub} with a pathspec pattern (*, ?, [ or :magic)", "git", how="name each file")
        target = ctx.path(p, gcwd)
        if p in (".", "..") or p.endswith(("/", "\\")) or os.path.isdir(target):
            return deny("unsure", f"git {sub} of a folder (it would take every file in it)", "git", how="name each file")
        if secret_name(p, target, strict=False):
            return deny("secret", f"git {sub} of a secret file", "git", how=SECRETS_HOW)
        if not ctx.writable(target) or protected(target, ctx):
            return deny("write outside the worktrees", f"git {sub} of a path outside the worktree or protected", "git")
    return None


def git_sub(sub, words, ctx, gcwd, part):
    no = deny("not on the list", f"git {sub} {' '.join(words[:1])[:30]}".strip() + " is not on the guard's list", "git")
    if part.stdin and sub != "commit":
        return deny("unsure", "a here-document other than git commit -F -", "git")
    if sub == "diff":
        if any(w.startswith("--no-i") for w in words):
            return deny("git option", "git diff --no-index reads files outside the repository", "git")
        after = words.index("--") if "--" in words else len(words)
        for k, w in enumerate(words):
            if k == after or (k < after and (w.startswith("-") or not os.path.exists(ctx.path(w, gcwd)))):
                continue
            if not ctx.writable(ctx.path(w, gcwd)):
                return deny("read outside the worktrees", "git diff of a path outside the run's places", "git")
        return None
    if sub == "show" or sub == "log":
        for w in words:
            if w.startswith("-"):
                continue
            if re.match(r"(refs/)?stash($|@|\^|~)", w) or "stash@" in w:
                return deny("git stash", f"git {sub} of a stash (it can hold untracked files)", "git")
            if sub == "show" and ":" in w:
                path = w.split(":", 1)[1]
                parts = [x for x in re.split(r"[\\/]+", path) if x]
                if path.startswith(("/", "\\")) or ".." in parts or (path and secret_name(path, strict=False)):
                    return deny("secret", "git show <rev>:<path> of a secret or of a path outside the repository", "git",
                                how=SECRETS_HOW)
        return None
    if sub in GIT_READ:
        return None
    if sub == "add":
        paths, ended = [], False
        for w in words:
            if ended or not w.startswith("-"):
                paths.append(w)
            elif w == "--":
                ended = True
            elif w not in ("-q", "--quiet", "-v", "--verbose"):
                return deny("not on the list", f"the git add option {w[:30]} (name each file)", "git", how="git add <file> <file>")
        return git_paths_writable(paths, ctx, gcwd, sub)
    if sub == "commit":
        if any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", w) for w in words):
            return deny("no-verify", "git commit -n skips the commit-msg hook", "git")
        if part.stdin and not any(words[k:k + 2] == ["-F", "-"] or w in ("--file=-",) or words[k:k + 2] == ["--file", "-"]
                                  for k, w in enumerate(words)):
            return deny("unsure", "a here-document without git commit -F -", "git")
        return None
    if sub == "restore":
        paths, k, ended = [], 0, False
        while k < len(words):
            w = words[k]
            if ended or not w.startswith("-"):
                paths.append(w)
            elif w == "--":
                ended = True
            elif w in ("-s", "--source"):
                k += 1
            elif not (w.startswith("--source=") or w in ("-S", "--staged", "-W", "--worktree", "-q", "--quiet", "--ours", "--theirs")):
                return deny("not on the list", f"the git restore option {w[:30]}", "git")
            k += 1
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
        # Only the project's branches by name: never a pull request's ref (pull/7/head, refs/...), FETCH_HEAD, or a : or + refspec, so
        # nothing a stranger pushed can be fetched and then checked out.
        for w in positional[1:]:
            if w not in (ctx.integration, ctx.production) and not ctx.worker_branch.match(w):
                return deny("not on the list", f"git fetch of something other than origin's {ctx.integration}, {ctx.production} or a "
                            "worker branch (no pull/, refs/, FETCH_HEAD, : or + refspec)", "git")
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
        most = 2 if any(w in ("-b", "-c", "--create") for w in words) else 1     # a start point only for a new branch, never a path
        if not positional or not ctx.worker_branch.match(positional[0]) or len(positional) > most:
            return deny("not a worker branch", f"git {sub} to a branch that is not a worker's (<type>/<n>-<words>)", "git",
                        how="saani start <n> makes the issue's worktree and branch")
        if positional[1:] and positional[1] not in (f"origin/{ctx.integration}", "HEAD"):
            return deny("not on the list", f"git {sub} with a start point other than origin/{ctx.integration} or HEAD (never FETCH_HEAD "
                        "or a fetched pull request)", "git", how=f"git {sub} -c <branch> origin/{ctx.integration}")
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
        # refs/stash is shared by every worktree of the repository: pop, apply and drop would take the owner's stash, and a push puts the
        # run's on top of it (the owner's next pop applies it). Only the list is read.
        if words == ["list"]:
            return None
        return deny("git stash", "git stash other than list (refs/stash is shared with the owner's checkout)", "git",
                    how="commit the work on the worker's branch, or git restore <file>")
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
    own = re.compile(r"https://github\.com/" + re.escape(ctx.repo) + r"(/|$)", re.I)
    for name, value, _ in flags + [(None, p, None) for p, _ in positional]:
        if value is None or name in ("title", "body", "jq", "template", "json"):
            continue
        low = value.lower()
        if ("://" in low or "github." in low or re.fullmatch(r"[\w.-]+/[\w.-]+#\d+", value)) and (not own.match(value) or ".." in value or "@" in value):
            return deny("another repository", f"gh with a URL outside https://github.com/{ctx.repo}", "gh")
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
    cwd, sure = ctx.cwd, True                    # sure: every part so far runs, in order (only && before it)
    for k, part in enumerate(parts):
        verdict, moved = command(part, ctx, cwd)
        if verdict:
            return verdict
        if part.words[0] == "cd" and k < len(parts) - 1:
            # the folder carries only when the cd surely ran and the next part runs only after it: && all the way, a real folder
            if not (sure and part.sep == "&&" and os.path.isdir(moved)):
                return deny("cd outside the worktrees", "a cd followed by anything but && (or into a folder that does not exist), "
                            "so the guard cannot tell where the next part runs", "cd", how="cd <folder> && <command>")
            cwd = moved
        sure = sure and part.sep in ("&&", None)
    return allow("every part of the command is on the guard's list")


# ---------------------------------------------------------------- the switch: registering a session after `saani auto on` ran

# The line the `saani auto on` process prints once the run is on (its last line, exit code 0). The PostToolUse hook registers the session
# only when the Bash call was the whole plugin `saani auto on ...` command, it ended normally, and its output holds exactly one such line.
MARKER = re.compile(r"^saani auto: on (?P<id>[0-9A-Za-z_-]{1,64}) folder=(?P<folder>\S.*?)[ \t\r]*$", re.M)


def auto_on_options(command, cwd):
    """The options of `saani auto on` when the whole Bash command is the plugin's `saani auto on ...`, else None."""
    if not isinstance(command, str) or "auto" not in command:
        return None
    try:
        parts = split_bash(command)
    except Unsure:
        return None
    if len(parts) != 1 or parts[0].stdin:
        return None
    words = parts[0].words
    start = saani_start(words, cwd)
    if not start or words[start:start + 2] != ["auto", "on"]:
        return None
    return list(words[start + 2:])


def finished_output(response):
    """The Bash call's output when it ended normally, else None: not interrupted, no non-zero exit code if the response reports one."""
    if isinstance(response, str):
        return response
    if not isinstance(response, dict) or response.get("interrupted") is True:
        return None
    for key in ("exit_code", "exitCode", "returncode", "returnCode", "code"):
        if response.get(key) not in (None, 0):
            return None
    out = response.get("stdout")
    return out if isinstance(out, str) else None


def register(session_id, options, cwd, run_id, folder):
    """Registers the session after `saani auto on [--id ID] [--folder PATH] [--deploy]` printed its marker (`run_id`, `folder`): the
    repository's main checkout, its worktrees folder and settings from the main checkout's saani.toml, the run's folder (an existing folder
    inside the worktrees folder). Returns the entry, or None when it cannot (then nothing is registered: present mode)."""
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
    if opts.get("id", run_id) != run_id:
        return None
    folder = norm(folder)
    if not os.path.isdir(folder) or ("folder" in opts and resolve(opts["folder"], cwd) != folder):
        return None
    dirs = git_dirs(folder)
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
        return None
    return turn_on(session_id, run_id, main, wt, folder, deploy=opts["deploy"], repo=cfg.repo, integration=cfg.integration,
                   production=cfg.production, tests=[cfg.landing, cfg.full, cfg.check], worker_branch=cfg.worker_branch.pattern)


def post_tool_use(event):
    """PostToolUse: registers the session when `saani auto on` ran and said so. Prints nothing; returns the entry or None."""
    if event.get("tool_name") != "Bash" or state(event.get("session_id")) is not None:
        return None                                          # never re-armed: a run on stays as it started
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and os.path.isdir(event.get("cwd")) else os.getcwd()
    options = auto_on_options(tool_input.get("command"), cwd)
    if options is None:
        return None
    out = finished_output(event.get("tool_response"))
    found = MARKER.findall(out or "")
    if len(found) != 1:
        return None
    return register(event.get("session_id"), options, cwd, found[0][0], found[0][1])


# ---------------------------------------------------------------- the hook

def emit(decision, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}))


def main(raw=None):
    """The hook: JSON on stdin (or `raw`, already read). PreToolUse: a decision on stdout for an auto session, nothing in present mode
    (`saani auto on` included: the owner's normal permissions decide it). PostToolUse: the registration after `saani auto on`, nothing on
    stdout. Never raises."""
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
            some = any(n.endswith((".json", OVER)) for n in os.listdir(registry()))
        except FileNotFoundError:
            some = False
        except OSError:
            some = True
        if some:
            emit("deny", error.format("unreadable hook input"))
        return 0
    if event.get("hook_event_name") == "PostToolUse":
        try:
            post_tool_use(event)
        except (Exception, SystemExit):
            pass
        return 0
    session_id = event.get("session_id")
    try:
        st = state(session_id)
    except Exception as e:
        log_refusal({}, event.get("tool_name", "?"), "guard error", "")
        emit("deny", error.format(f"registry {type(e).__name__}"))
        return 0
    if st is None:
        return 0
    if st.get("over"):
        log_refusal(st, event.get("tool_name", "?"), "run over", "")
        emit("deny", f"Saani guard (auto run): refused, this auto run is over (its {MAX_HOURS} hours are past). To go on: report and "
                     "stop; the owner ends the run from a terminal: saani auto off --run <id>.")
        return 0
    cwd = event.get("cwd")
    try:
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else os.getcwd()
        decision, reason, code, word = decide(event, context(cwd, st, session_id))
    except (Exception, SystemExit) as e:
        decision, reason, code, word = "deny", error.format(type(e).__name__), "guard error", ""
    if decision == "deny":
        log_refusal(st, event.get("tool_name", "?"), code, word)
    emit(decision, reason)
    return 0


def add_parsers(sub):
    """`saani guard`, listed in the help; saani.py runs it before loading anything else."""
    p = sub.add_parser("guard", help="the Claude Code PreToolUse and PostToolUse hook of the plugin (reads the hook's JSON on stdin)")
    p.set_defaults(run=lambda args: main())
