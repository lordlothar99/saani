"""saani/guard.py, paths: devices, Git Bash and drive paths, network paths, what a tool may read or edit, the protected files, the tools."""
import os
import pathlib
import re

import pytest

from saani import guard
from guard_helpers import sh, tool


def test_a_device_is_refused_but_dev_null(ctx):
    depth = "../" * (len(pathlib.Path(ctx.places["work"]).parts) + 2)
    for command in ("cat /dev/clipboard", "head -c 9 /dev/clipboard", "cat /dev/../dev/clipboard", "cat /tmp/../dev/clipboard",
                    f"cat {depth}dev/clipboard", "cat ~/../../../../../../../../../../dev/clipboard", "ls /dev",
                    "gh issue comment 1 --body-file /dev/clipboard", "git commit -F /dev/clipboard", "cat /DEV/Clipboard"):
        assert sh(ctx, command)[2] == "posix path", command
    assert tool(ctx, "Read", file_path="/dev/clipboard")[2] == "posix path"
    assert sh(ctx, "ls > /dev/clipboard")[2] == "redirect"                               # a redirect: only to /dev/null
    assert sh(ctx, "cat < /dev/clipboard")[2] == "redirect"
    assert sh(ctx, "diff a.txt /dev/null")[0] == "allow"
    assert sh(ctx, "ls > /dev/null 2>&1")[0] == "allow"


def test_git_bash_paths_go_through_one_allowlist(ctx):
    """After `..` is folded as Git Bash folds it, only /<drive>/..., /tmp/... and /dev/null are read; the rest Git Bash maps outside the
    drives (its own install, /proc, mounts) and is refused before any lookup."""
    if os.name != "nt":
        pytest.skip("Git Bash's paths are read on Windows")
    depth = "../" * (len(pathlib.Path(ctx.places["work"]).parts) + 2)
    for path in (f"{depth}proc/self/environ", "/c/../proc/self/environ", "/tmp/../proc/x", "~/" + depth + "proc/self/environ",
                 f"{depth}cygdrive/c/x.txt", "/cygdrive/c/x.txt", "/etc/passwd", "/", "/usr/bin/env", "/mnt/c/x", "/opt/x",
                 "C:/../proc/self/environ", "C:\\..\\etc\\passwd", "\\etc\\passwd"):
        word = f"'{path}'" if "\\" in path else path                                     # unquoted: bash expands ~
        assert sh(ctx, f"cat {word}")[2] == "posix path", path
        assert tool(ctx, "Read", file_path=path)[2] == "posix path", path
    drive = ctx.places["work"][0].lower()
    work_gitbash = "/" + drive + ctx.places["work"][2:]
    (pathlib.Path(ctx.places["work"]) / "notes.txt").write_text("x")
    for ok in ("notes.txt", "src/../notes.txt", ctx.places["work"] + "/notes.txt", work_gitbash + "/notes.txt", "/tmp/x.txt", "/dev/null"):
        assert sh(ctx, f"cat '{ok}'")[0] == "allow", ok
        assert tool(ctx, "Read", file_path=ok)[0] == "allow", ok


def test_a_bare_drive_letter_is_its_current_folder_so_refused(ctx):
    for path in ("C:", "d:", "C:x.txt"):
        assert tool(ctx, "Read", file_path=path)[2] == "drive-relative path", path
        assert sh(ctx, f"ls {path}")[2] == "drive-relative path", path
    assert tool(ctx, "Read", file_path="C:/")[0] == "allow"


def test_a_mapped_network_drive_is_refused_before_any_lookup(ctx, monkeypatch):
    if os.name != "nt":
        pytest.skip("drive letters are Windows'")
    letter = ctx.places["work"][0].upper()
    monkeypatch.setattr(guard, "drive_type", lambda x: guard.DRIVE_REMOTE if x == letter else 3)
    assert tool(ctx, "Read", file_path=ctx.places["work"] + "/notes.txt")[2] == "network path"
    assert sh(ctx, "cat notes.txt")[2] == "network path"


def test_a_git_bash_drive_path_with_backslashes_is_read_as_its_windows_path(ctx):
    assert sh(ctx, "cat /cygdrive/c/x.txt")[2] == "posix path"
    assert tool(ctx, "Read", file_path="/cygdrive/c/x.txt")[2] == "posix path"
    if os.name != "nt":
        return
    home = ctx.places["home"].replace("/", "\\")
    gitbash = "/" + home[0].lower() + home[2:]                                           # /c\Users\...\home
    assert sh(ctx, f"cat '{gitbash}\\.claude\\settings.json'")[2] == "secret"
    assert tool(ctx, "Read", file_path=gitbash + "\\.claude\\settings.json")[2] == "secret"


def test_reads_are_allowed_anywhere_but_secrets(ctx):
    assert tool(ctx, "Read", file_path="{main}/src/app.py")[0] == "allow"
    assert tool(ctx, "Read", file_path="{work}/saani/guard.py")[0] == "allow"
    assert tool(ctx, "Glob", pattern="**/*.py", path="{main}")[0] == "allow"
    assert tool(ctx, "Glob", pattern="**/.env")[2] == "secret"
    assert tool(ctx, "Read", file_path=5)[2] == "unreadable path"


def test_grep_reads_contents_only_inside_the_run_s_folders(ctx):
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="*.py")[0] == "allow"
    assert tool(ctx, "Grep", pattern="total")[0] == "allow"                              # the worktree itself
    assert tool(ctx, "Grep", pattern="total", path="{home}")[2] == "grep outside the worktrees"
    assert tool(ctx, "Grep", pattern="total", path="{main}")[2] == "grep outside the worktrees"
    assert tool(ctx, "Grep", pattern="total", path="{temp}")[2] == "grep outside the worktrees"
    assert tool(ctx, "Grep", pattern="total", path="{home}/.ssh")[2] == "secret"
    assert tool(ctx, "Grep", pattern="x", type="pem")[2] == "grep option"
    assert tool(ctx, "Grep", pattern="x", type="py")[2] == "grep option"
    assert tool(ctx, "Grep", pattern="x", glob="!*.py")[2] == "grep option"
    deep = pathlib.Path(ctx.places["work"]) / "src" / "deep"
    deep.mkdir()
    (deep / ".env").write_text("x")                                                      # anywhere in the tree, not only at the top
    assert tool(ctx, "Grep", pattern="total", path="{work}")[2] == "secret"
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="*")[2] == "secret"
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="*.env")[2] == "secret"
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="**/.env*")[2] == "secret"
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="*.{{py,env}}")[2] == "secret"
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="*.json")[0] == "allow"    # it cannot read .env
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="*.py")[0] == "allow"
    assert tool(ctx, "Grep", pattern="total", path="{work}/src/app.py")[0] == "allow"


@pytest.mark.parametrize("name,inp", [
    ("Read", {"file_path": "\\\\host\\s\\x.md"}), ("Read", {"file_path": "//host/s/x.md"}), ("Read", {"file_path": "\\\\?\\UNC\\host\\s\\x"}),
    ("Read", {"file_path": "\\\\.\\pipe\\x"}), ("Read", {"file_path": "/\\host\\s\\x.md"}), ("Glob", {"pattern": "*.md", "path": "//host/s"}),
    ("LS", {"path": "\\\\host\\s"}), ("Grep", {"pattern": "x", "path": "\\\\host\\s"}), ("Write", {"file_path": "//host/s/x.md"}),
    ("Edit", {"file_path": "\\\\host\\s\\x.md"}), ("Bash", {"command": "cat //host/s/x.md"}), ("Bash", {"command": "cat '\\\\host\\s\\x.md'"}),
    ("Bash", {"command": "gh issue comment 1 --body-file //host/s/x.md"}), ("Bash", {"command": "cd //host/s"}),
    ("Bash", {"command": "git diff //host/s/x a.txt"}), ("Bash", {"command": "//host/s/python.exe -m pytest -q"}),
    ("Bash", {"command": "python -m pytest -q //host/s/tests"}), ("Bash", {"command": "grep x a=//host/s/x"})])
def test_a_network_path_is_refused_before_any_filesystem_call(ctx, monkeypatch, name, inp):
    def local_only(real):
        def call(p, *a, **k):
            if isinstance(p, (str, os.PathLike)) and guard.network(os.fspath(p)):
                raise AssertionError(f"the guard looked a network path up: {p}")
            return real(p, *a, **k)
        return call
    for fn in ("realpath", "exists", "isdir", "isfile", "lexists"):
        monkeypatch.setattr(os.path, fn, local_only(getattr(os.path, fn)))
    for fn in ("stat", "scandir", "listdir"):
        monkeypatch.setattr(os, fn, local_only(getattr(os, fn)))
    decision, reason, code, _ = guard.decide({"tool_name": name, "tool_input": inp}, ctx)
    assert (decision, code) == ("deny", "network path"), (name, inp, reason)


def test_a_read_of_a_network_path_never_reaches_realpath(ctx, monkeypatch):
    def no_lookup(*a, **k):
        raise AssertionError("the guard looked a path up")
    monkeypatch.setattr(os.path, "realpath", no_lookup)
    assert tool(ctx, "Read", file_path="\\\\host\\s\\x.md")[2] == "network path"
    assert tool(ctx, "Read", file_path="//host/s/x.md")[2] == "network path"


def test_a_url_is_not_looked_up_as_a_network_path(ctx, monkeypatch):
    real = os.path.realpath

    def local_only(p, *a, **k):
        assert not guard.network(str(p)), p
        return real(p, *a, **k)
    monkeypatch.setattr(os.path, "realpath", local_only)
    assert sh(ctx, "gh issue view https://github.com/acme/webshop/issues/12")[0] == "allow"
    assert sh(ctx, "gh pr view https://evil.example/acme/webshop/pull/1")[2] == "another repository"


def test_rm_of_a_folder_looks_at_its_whole_tree(ctx):
    docs = pathlib.Path(ctx.places["work"]) / "docs" / "sub"
    docs.mkdir(parents=True)
    (docs / "notes.md").write_text("x")
    assert sh(ctx, "rm -rf {work}/docs")[0] == "allow"
    (docs / "CLAUDE.local.md").write_text("x")
    assert sh(ctx, "rm -rf {work}/docs")[2] == "guard file"
    (docs / "CLAUDE.local.md").unlink()
    (docs / ".github" / "workflows").mkdir(parents=True)
    assert sh(ctx, "rm -rf {work}/docs")[2] == "guard file"


def test_edits_are_allowed_only_in_the_worktrees_the_run_s_folder_and_temp(ctx):
    for t, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
        for ok in ("{work}/src/app.py", "{run}/notes.md", "{temp}/scratch.txt", "{wt}/13-other/x.py", "src/relative.py",
                   "{work}/.github/ISSUE_TEMPLATE/bug.md", "{work}/docs/workflows/x.md"):
            assert tool(ctx, t, **{key: ok})[0] == "allow", (t, ok)
        for bad in ("{main}/src/app.py", "{home}/notes.txt", "{home}/.bashrc", "{wt}/../outside.py", "{work}/../../webshop/x.py",
                    "~/x.txt"):
            assert tool(ctx, t, **{key: bad})[2] == "edit outside the worktrees", (t, bad)


@pytest.mark.parametrize("path", ["{work}/saani/guard.py", "{work}/saani.toml", "{work}/hooks/hooks.json", "{work}/hooks/other.sh",
                                  "{work}/.claude/settings.json", "{work}/.claude/settings.local.json", "{work}/.claude/agents/x.md",
                                  "{work}/.git", "{work}/.githooks/commit-msg", "{work}/.mcp.json", "{work}/CLAUDE.md",
                                  "{work}/src/CLAUDE.md", "{temp}/saani/guard.py", "{temp}/.git/HEAD", "{home}/.saani/auto/x.json",
                                  "{home}/.saani/projects.toml", "{main}/saani.toml", "{main}/.git/hooks/pre-commit",
                                  "{work}/.github/workflows/ci.yml", "{work}/.github/actions/setup/action.yml", "{work}/.github/CODEOWNERS",
                                  "{work}/CODEOWNERS", "{work}/docs/CODEOWNERS", "{work}/.github/dependabot.yml", "{work}/CLAUDE.local.md",
                                  "{work}/.git./HEAD", "{work}/CLAUDE.md.", "{work}/.claude. /x", "{work}/.GitHub/Workflows/x.yml",
                                  "{work}/.gitignore", "{work}/src/.gitignore", "{work}/CLAUDE.md:stream", "{work}/.git:x/HEAD",
                                  "{work}/saani.toml::$DATA", "{work}/.github/workflows:s/ci.yml"])
def test_protected_files_are_never_edited_in_an_auto_run(ctx, path):
    decision, reason, code, _ = tool(ctx, "Write", file_path=path, content="x")
    assert (decision, code) == ("deny", "guard file"), (path, reason)


def test_a_project_s_own_hooks_folder_below_the_top_is_not_the_guard(ctx):
    assert tool(ctx, "Edit", file_path="{work}/src/hooks/useCart.ts")[0] == "allow"
    assert tool(ctx, "Edit", file_path="{work}/src/settings.json")[0] == "allow"


def test_paths_with_backslashes_dots_and_case(ctx):
    work = ctx.places["work"]
    assert tool(ctx, "Write", file_path=work.replace("/", "\\") + "\\src\\a.py")[0] == ("allow" if os.name == "nt" else "deny")
    assert tool(ctx, "Write", file_path=work + "/src/../../../webshop/a.py")[2] == "edit outside the worktrees"
    assert tool(ctx, "Write", file_path=work + "/./src/./a.py")[0] == "allow"
    if os.name == "nt":
        assert tool(ctx, "Write", file_path=work.upper() + "/SRC/A.PY")[0] == "allow"
        assert tool(ctx, "Write", file_path=work + "\\SAANI\\GUARD.PY")[2] == "guard file"
        gitbash = re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), work)
        assert sh(ctx, f"cd {gitbash}/src")[0] == "allow"
        assert sh(ctx, "rm -rf /c/Windows/Temp/x")[2] == "rm outside the worktrees"
        assert sh(ctx, "rm '" + work.replace("/", "\\") + "\\out.txt'")[0] == "allow"


def test_other_tools_are_allowed_unknown_ones_refused_and_questions_never_asked(ctx):
    for name in ("Agent", "Task", "TodoWrite", "ToolSearch", "Skill", "SendMessage", "WebSearch", "WebFetch", "BashOutput", "KillShell", "SubagentHandback"):
        assert guard.decide({"tool_name": name, "tool_input": {}}, ctx)[0] == "allow", name
    assert guard.decide({"tool_name": "mcp__x__delete", "tool_input": {}}, ctx)[2] == "unknown tool"
    assert guard.decide({"tool_name": None}, ctx)[2] == "unknown tool"
    assert guard.decide({"tool_name": "AskUserQuestion", "tool_input": {}}, ctx)[2] == "question"
    assert guard.decide({"tool_name": "Bash", "tool_input": {}}, ctx)[2] == "unreadable command"
    assert guard.decide({"tool_name": "Write", "tool_input": {"content": "x"}}, ctx)[2] == "unreadable path"
