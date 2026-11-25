"""saani/guard.py, sensitive files ("secrets"): the files an auto run never reads, for every tool and inside shell commands."""
import os
import pathlib

import pytest

from saani import guard
from guard_helpers import ROOT_CODE, sh, tool

SECRETS = [".env", ".env.local", "config/.env.production", ".envrc", "server.pem", "tls/server.key", "id_rsa", "id_rsa.pub", "id_ed25519",
           "{home}/.ssh/config", "{home}/.ssh/known_hosts", "credentials.json", "aws/credentials", "my-CREDENTIALS.txt",
           "application_default_credentials.json", "cert.p12", "{home}/.netrc", "{home}/.npmrc", "{home}/.config/gh/hosts.yml",
           "hosts.yml", "{home}/.git-credentials", "{home}/.docker/config.json", "{home}/.kube/config", "{home}/.gnupg/pubring.kbx",
           "{home}/.pgpass", "/proc/self/environ", "app_secret.txt", "client-secrets.yml", "api_token.txt", "TOKEN.md", ".ENV",
           "src\\.env", ".env::$DATA", ".env."]


@pytest.mark.parametrize("name", SECRETS)
def test_secrets_are_refused_to_every_tool(ctx, name):
    for t, key in (("Read", "file_path"), ("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"),
                   ("NotebookEdit", "notebook_path"), ("Glob", "path"), ("Glob", "pattern"), ("LS", "path"), ("Grep", "path"),
                   ("Grep", "glob")):
        decision, reason, code, _ = tool(ctx, t, **{key: name})
        codes = {"secret", ROOT_CODE("secret")} if name.startswith("/proc") else {"secret"}     # a glob is text, a path is read first
        assert decision == "deny" and code in codes, (t, name, reason)


@pytest.mark.parametrize("name", ["{home}/.claude.json", "{home}/.claude/settings.json", "{home}/.claude/.credentials.json",
                                  "{home}/.claude/projects/acme-webshop/session.jsonl", "{home}/.claude", "~/.claude/settings.json",
                                  "{home}/AppData/Roaming/npm/etc/npmrc", "putty.ppk", "release.jks", "app.keystore", "vault.kdbx",
                                  "{home}/AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt",
                                  "{home}/.local/share/powershell/PSReadLine/ConsoleHost_history.txt", "{main}/.git/config",
                                  "{work}/.git./config", "{main}/.git/config:x", ".git/config",
                                  "{home}/.bash_history", "{work}/.python_history", "{home}/.gitconfig", "{home}/.config/git/config",
                                  "{home}/.m2/settings.xml", "{work}/.yarnrc.yml", "{home}/AppData/Roaming/pip/pip.ini",
                                  "{home}/.config/pip/pip.conf", "{home}/.claude.json.backup", "{home}/.npmrc.bak", "{home}/.netrc~",
                                  "{main}/.git/refs/stash", "{main}/.git/logs/refs/stash", "{main}/.git/packed-refs",
                                  "{main}/.git/packed-refs::$DATA", "{main}/.git./refs/stash", "{home}/.m2./settings.xml. ",
                                  "{home}/.config/git/config:x", "{main}/.git/saani-auto-run.json",
                                  "{main}/.git./saani-auto-run.json"])
def test_more_secrets_are_refused_to_read(ctx, name):
    decision, reason, code, _ = tool(ctx, "Read", file_path=name)
    assert (decision, code) == ("deny", "secret"), (name, reason)


def test_claude_s_memory_and_a_project_s_own_claude_folder_are_no_secret(ctx):
    assert tool(ctx, "Read", file_path="{home}/.claude/projects/acme-webshop/memory/MEMORY.md")[0] == "allow"
    assert tool(ctx, "Read", file_path="{work}/.claude/settings.json")[0] == "allow"
    assert tool(ctx, "Read", file_path="{work}/.git/HEAD")[0] == "allow"
    assert sh(ctx, "cat ~/.claude/settings.json")[2] == "secret"
    assert sh(ctx, "cat {main}/.git/config")[2] == "secret"
    assert sh(ctx, "cat {main}/.git/saani-auto-run.json")[2] == "secret"          # the run's record holds the watcher's token (#67)
    assert sh(ctx, "git status")[0] == "allow"


def test_only_this_session_s_saved_tool_outputs_are_read_never_a_way_out_of_them(ctx):
    assert guard.context(ctx.cwd, {}, "sess-1").session == "sess-1"                     # main() passes the hook's session_id
    ctx.session = "sess-1"
    project = pathlib.Path(ctx.places["home"]) / ".claude" / "projects" / "acme-webshop"
    for sid in ("sess-1", "sess-2"):
        (project / sid / "tool-results").mkdir(parents=True)
        (project / sid / "tool-results" / "out.txt").write_text("x")
    (project.parents[1] / "settings.json").write_text("{}")
    tr = "{home}/.claude/projects/acme-webshop/sess-1/tool-results"
    assert tool(ctx, "Read", file_path=tr + "/out.txt")[0] == "allow"
    assert tool(ctx, "Read", file_path="~/.claude/projects/acme-webshop/sess-1/tool-results/out.txt")[0] == "allow"
    assert sh(ctx, "head -50 ~/.claude/projects/acme-webshop/sess-1/tool-results/out.txt")[0] == "allow"
    assert sh(ctx, f"cat {tr}/out.txt")[0] == "allow"
    for escape in ("{home}/.claude/projects/acme-webshop/sess-2/tool-results/out.txt",          # another session's outputs
                   "{home}/.claude/projects/acme-webshop/sess-1.jsonl", "{home}/.claude/projects/acme-webshop/sess-1/x.jsonl",
                   "{home}/.claude/projects/acme-webshop/tool-results/out.txt", tr + "/../../../../settings.json",
                   tr + "/../../sess-2/tool-results/out.txt", tr + "/.../settings.json", tr + "/.. /.. /.. /.. /settings.json",
                   "{home}/.claude/settings.json", "{home}/.claude/projects/acme-webshop/sess-1/tool-results.jsonl",
                   "{home}/.claude/tool-results/out.txt"):
        assert tool(ctx, "Read", file_path=escape)[2] == "secret", escape
        assert sh(ctx, f"cat '{escape}'")[2] == "secret", escape
    ctx.session = None                                                                   # no session id: no outputs at all
    assert tool(ctx, "Read", file_path=tr + "/out.txt")[2] == "secret"
    ctx.session = "sess-1"
    try:
        os.symlink(project.parents[1] / "settings.json", project / "sess-1" / "tool-results" / "link.json")
    except (OSError, NotImplementedError):
        return                                                                           # no symlinks here (Windows without the right)
    assert tool(ctx, "Read", file_path=tr + "/link.json")[2] == "secret"                 # judged where it leads
    assert sh(ctx, f"cat {tr}/link.json")[2] == "secret"


@pytest.mark.parametrize("command", ["cat .env", "head {home}/.ssh/id_ed25519", "grep x .env.local", "cat server.pem",
                                     "git add .env", "git show HEAD:.env", "git diff -- config/credentials.yml", "cat .envrc",
                                     "diff a.txt api_token.txt", "python -m pytest -q {work}/.env",
                                     "gh issue create --body-file .env", "ls {home}/.ssh", "wc -l app_secret.txt", "cat {home}/.netrc",
                                     "cat /proc/self/environ",
                                     "cat {home}/.kube/config", "git commit -F .env", "rm .env"])
def test_secrets_are_refused_inside_shell_commands(ctx, command):
    decision, reason, code, _ = sh(ctx, command)
    assert (decision, code) == ("deny", ROOT_CODE("secret") if "/proc" in command else "secret"), (command, reason)


def test_a_branch_or_text_that_mentions_token_is_no_secret_but_a_token_file_is(ctx):
    assert sh(ctx, "git switch feat/15-rotate-token")[0] == "allow"
    assert sh(ctx, "grep -n token src/app.py")[0] == "allow"
    assert sh(ctx, "gh issue create --title 'Rotate the API token' --body 'secrets stay out'")[0] == "allow"
    (pathlib.Path(ctx.places["work"]) / "token").write_text("x")
    assert sh(ctx, "cat token")[2] == "secret"
    assert tool(ctx, "Grep", pattern="token|secret", path="{work}/src")[0] == "allow"      # Grep's pattern is about contents


def test_secret_names():
    for name in (".env", "a/.env.test", "k.PEM", "id_ecdsa", "C:\\srv\\app\\.ssh\\x", "/srv/app/.ssh/x", "x/gh/hosts.yml", "hosts.yml",
                 "my-secret", "tokens.json", ".pgpass", "x/.docker/config.json", "/proc/1/maps", "Credentials"):
        assert guard.secret_name(name), name
    for name in ("src/app.py", "environment.md", "README.md", "keys.py", "monkey", "docs/env.md", "config.json", "x/procs/a"):
        assert not guard.secret_name(name), name
    assert not guard.secret_name("feat/1-rotate-token", strict=False)


@pytest.mark.parametrize("secret", [".docker/config.json", ".kube/config", ".ssh/known_hosts", ".gnupg/x", "gh/hosts.yml"])
def test_grep_s_walk_reads_paths_below_the_root_not_only_names(ctx, secret):
    target = pathlib.Path(ctx.places["work"]) / "home-copy" / secret
    target.parent.mkdir(parents=True)
    target.write_text("x")
    assert tool(ctx, "Grep", pattern="x", path="{work}")[2] == "secret", secret
    assert tool(ctx, "Grep", pattern="x", path="{work}", glob="*")[2] == "secret", secret
    expected = "secret" if "config" in secret else "allow"                               # a plain glob reads only the names it matches
    assert tool(ctx, "Grep", pattern="x", path="{work}", glob="config*")[2 if expected == "secret" else 0] == expected, secret
    assert tool(ctx, "Grep", pattern="x", path="{work}", glob="**/*")[2] == "secret", secret
    assert tool(ctx, "Grep", pattern="x", path="{work}", glob="*.py")[0] == "allow", secret
