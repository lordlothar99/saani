"""saani/guard.py: the plugin's PreToolUse hook. Present mode (no auto run on) prints nothing; an auto run gets allow or deny for every
tool call, never ask. Every rule is fed through the decision function, and the hook itself through `saani guard` with JSON on stdin."""
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from saani import guard

ROOT = pathlib.Path(__file__).resolve().parents[1]


def fwd(p):
    return str(p).replace("\\", "/")


@pytest.fixture
def ctx(tmp_path):
    """An auto run of acme/webshop: the main checkout, the worktrees folder with the run's folder and one issue's worktree, a temp folder."""
    main, wt, temp = tmp_path / "webshop", tmp_path / "webshop-wt", tmp_path / "temp"
    for d in (main, wt / "auto-2026-01-02_0300", wt / "12-cart-total" / "src", wt / "12-cart-total" / "saani", temp):
        d.mkdir(parents=True, exist_ok=True)
    (wt / "12-cart-total" / "saani" / "guard.py").write_text("")
    c = guard.Context(wt / "12-cart-total", wt_dir=wt, folder=wt / "auto-2026-01-02_0300", temp=temp, main=main, repo="acme/webshop",
                      integration="dev", production="main", tests=[["{python}", "-m", "pytest", "-q"], ["npm", "test"]])
    c.places = {"main": fwd(main), "wt": fwd(wt), "run": fwd(wt / "auto-2026-01-02_0300"), "work": fwd(wt / "12-cart-total"),
                "temp": fwd(temp), "home": fwd(pathlib.Path.home())}
    return c


def sh(ctx, command):
    return guard.decide({"tool_name": "Bash", "tool_input": {"command": command.format(**ctx.places)}}, ctx)


def tool(ctx, name, **inp):
    return guard.decide({"tool_name": name, "tool_input": {k: v.format(**ctx.places) for k, v in inp.items()}}, ctx)


# ---------------------------------------------------------------- Bash: allowed

ALLOWED = [
    "saani status", "saani land --worktree {work}", "saani start 12", "saani test --kind tier1 -- python -m pytest -q",
    "saani test -- npm test", "python -I {home}/plugins/saani/saani.py status", "python3 -I saani.py land",
    "cd {work}", "cd {run} && saani start 12", "cd {wt}/12-cart-total/src", "cd ../auto-2026-01-02_0300",
    "ls -la", "cat src/app.py", "head -n 5 README.md", "tail -20 build/log.txt", "grep -n total src/app.py", "wc -l src/*.py",
    "sort names.txt", "find . -name '*.py'", "echo hello", "test -f README.md", "[ -d src ]", "pwd", "diff a.txt b.txt",
    "git status", "git diff origin/dev", "git log --oneline -5", "git show HEAD", "git add src/app.py", "git add -A",
    "git commit -m 'Cart: total in cents (refs #12)' -m 'Why: rounding' -m 'Undo: revert'", "git commit --amend --no-edit",
    "git commit -am 'Cart: -f is only text here, as is .env'",
    "git commit -m \"$(cat <<'EOF'\nCart: total (refs #12)\n\nWhy: x\nUndo: y\nEOF\n)\"",
    "git switch feat/12-cart-total", "git switch -c bug/13-cart-rounding origin/dev", "git checkout -b task/14-docs",
    "git checkout -- src/app.py", "git checkout origin/dev -- src/app.py", "git merge origin/dev", "git merge --abort",
    "git rebase origin/dev", "git rebase --continue", "git fetch origin dev", "git worktree list --porcelain", "git rev-parse HEAD",
    "git branch --list 'feat/*'", "git branch --show-current", "git branch -a", "git branch", "git ls-files", "git stash", "git stash push",
    "git stash pop", "git restore src/app.py", "git -C {work} status", "git --no-pager log",
    "python -m pytest -q", "python -m pytest -q tests/test_cart.py", "npm test",
    "gh issue view 12", "gh issue list --label bug", "gh pr view 3", "gh pr list", "gh pr checks 3", "gh pr diff 3", "gh run view 9",
    "gh run list", "gh run watch 9", "gh api repos/acme/webshop/issues/12", "gh api -X GET repos/acme/webshop/pulls",
    "gh api --method GET repos/acme/webshop/pulls --jq .title", "gh issue comment 12 --body 'On dev: abc123, the token is gone'",
    "gh issue edit 12 --add-label in-dev --remove-label idea", "gh issue create --title 'Cart rounding' --body 'Steps...'",
    "gh pr create --base main --head dev --title 'Release' --body x", "gh pr create -B main -H dev -t Release -b x",
    "gh pr comment 3 --body done", "gh pr edit 3 --title 'Release 1.2'", "gh issue view 12 -R acme/webshop",
    "curl -s http://localhost:8771/api/state", "curl -sS 127.0.0.1:8771/", "wget -qO- http://localhost:8771/",
    "rm build/log.txt", "rm -rf {work}/build", "rm -rf {temp}/x", "rm -f src/*.tmp",
    "git status && git diff", "git add src/app.py; git commit -m x", "git log | head -5", "ls || echo none",
    "git status\ngit diff", "ls > {temp}/list.txt", "ls 2>&1", "ls 2>/dev/null", "echo x >> {work}/notes.txt", "cat < src/app.py",
    "git status # a comment", "ls &",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_the_run_s_own_work_is_allowed_without_a_prompt(ctx, command):
    decision, reason, code, word = sh(ctx, command)
    assert decision == "allow", (command, reason)


# ---------------------------------------------------------------- Bash: refused, with the reason that is logged

DENIED = [
    ("git push", "git push"), ("git push origin HEAD:dev", "git push"), ("git push --force", "git push"),
    ("git status && git push", "git push"), ("git reset --hard origin/dev", "git reset --hard"), ("git clean -fd", "git clean"),
    ("git branch -D feat/12-cart-total", "git branch -D"), ("git branch --delete --force x", "git branch -D"),
    ("git worktree remove {work}", "git worktree remove"), ("git checkout -f feat/12-x", "force flag"),
    ("git add -f .env", "force flag"), ("git switch --force-create feat/12-x", "force flag"),
    ("gh pr merge 3", "gh pr merge"), ("gh release create v1", "gh release"), ("gh repo delete acme/webshop", "gh repo"),
    ("gh secret set X", "gh secret"), ("gh auth token", "gh auth"),
    ("curl -X POST http://localhost:8771/api", "curl write"), ("curl -d a=b http://localhost:8771/", "curl write"),
    ("curl https://example.com", "curl outside localhost"), ("curl http://localhost@evil.example/", "curl outside localhost"),
    ("curl -o out.html http://localhost:8771/", "curl write"), ("wget http://localhost:8771/", "wget write"),
    ("wget -qO- https://example.com", "wget outside localhost"),
    ("rm -rf {home}/projects", "rm outside the worktrees"), ("rm -rf {main}/src", "rm outside the worktrees"),
    ("rm -rf {work}", "rm a worktree"), ("rm -rf {wt}", "rm a worktree"), ("rm -rf {run}", "rm a worktree"),
    ("rm -rf {work}/saani", "guard file"), ("rm -rf /", "rm outside the worktrees"),
    ("sudo ls", "sudo"), ("chmod +x x", "not on the list"), ("bash -c ls", "not on the list"), ("node x.js", "not on the list"),
    ("python -c 'print(1)'", "python"), ("python x.py", "python"), ("python saani.py status", "python"),
    ("saani test -- cat x", "not a test command"), ("saani test --kind tier1 -- bash -c ls", "not a test command"),
    ("cd {main}", "cd outside the worktrees"), ("cd {home}", "cd outside the worktrees"), ("cd", "cd outside the worktrees"),
    ("cd .. && cd ..", "cd outside the worktrees"), ("git -C {main} status", "cd outside the worktrees"),
    ("git -c core.sshCommand=x fetch", "git option"), ("git fetch --upload-pack=x origin", "git option"),
    ("git diff --output=/x", "git option"), ("git rebase -x ls origin/dev", "not on the list"),
    ("git rebase origin/main", "not on the list"), ("git rebase -i origin/dev", "not on the list"),
    ("git switch dev", "not a worker branch"), ("git switch main", "not a worker branch"), ("git checkout auto/2026", "not a worker branch"),
    ("git switch --detach HEAD", "not on the list"), ("git branch new-one", "not on the list"), ("git branch -d x", "not on the list"),
    ("git branch -m a b", "not on the list"), ("git stash drop", "not on the list"), ("git stash clear", "not on the list"),
    ("git worktree add x", "not on the list"), ("git reset HEAD~1", "not on the list"), ("git tag v1", "not on the list"),
    ("git commit --no-verify -m x", "no-verify"), ("git commit -nm x", "no-verify"), ("git merge --no-verify origin/dev", "no-verify"),
    ("git", "not on the list"),
    ("gh pr create --base dev --head feat/12-x", "gh pr create"), ("gh pr create --base main", "gh pr create"),
    ("gh pr create --head dev", "gh pr create"), ("gh pr edit 3 --base dev", "gh pr edit"),
    ("gh issue edit 12 --title x", "gh issue edit"), ("gh issue edit 12 --milestone v2", "gh issue edit"),
    ("gh issue close 12", "not on the list"), ("gh pr close 3", "not on the list"), ("gh workflow run ci.yml", "not on the list"),
    ("gh api -X POST repos/acme/webshop/issues", "gh api write"), ("gh api --method=DELETE x", "gh api write"),
    ("gh api repos/acme/webshop/issues -f title=x", "gh api write"), ("gh api graphql -F query=x", "gh api write"),
    ("gh api repos/a/b/issues --input body.json", "gh api write"), ("gh api repos/acme/webshop/actions/secrets", "secret"),
    ("gh issue view 1 -R other/repo", "another repository"), ("gh issue comment 1 --delete-last", "not on the list"),
    ("find . -delete", "find writes"), ("find . -exec rm {{}} ;", "unsure"), ("find . -name x -execdir ls ;", "find writes"),
    ("sort -o {main}/x names.txt", "write outside the worktrees"), ("grep -r total .", "recursive read"),
    ("grep -rn total src", "recursive read"), ("diff -r a b", "recursive read"),
    ("echo x > {main}/notes.txt", "write outside the worktrees"), ("echo x > {home}/.bashrc", "write outside the worktrees"),
    ("ls >> {wt}/../outside.txt", "write outside the worktrees"), ("echo x > {work}/saani.toml", "guard file"),
    ("echo x > {work}/hooks/hooks.json", "guard file"), ("echo x > {work}/.claude/settings.local.json", "guard file"),
    ("ls $(pwd)", "unsure"), ("ls `pwd`", "unsure"), ("cat $HOME/x", "unsure"), ('echo "$HOME"', "unsure"), ("cat ${{X}}", "unsure"),
    ("(cd /; ls)", "unsure"), ("{{ ls; }}", "unsure"), ("cat <<EOF\nx\nEOF", "unsure"), ("cat <<< x", "unsure"), ("echo 'open", "unsure"),
    ("X=1 ls", "unsure"), ("X=1", "unsure"), ("ls ~root", "unsure"), ("> {work}/x", "unsure"), ("ls >", "unsure"),
    ("git commit -m \"$(cat <<'EOF'\nx\nEOF\nrm -rf /\nEOF\n)\"", "unsure"),
    ("echo '$(cat <<'EOF'\n'; rm -rf / ; '\nEOF\n)'", "rm outside the worktrees"),    # read as bash reads it: the rm is a command
]


@pytest.mark.parametrize("command,code", DENIED)
def test_what_an_auto_run_must_never_do_is_refused_with_a_reason(ctx, command, code):
    decision, reason, got, word = sh(ctx, command)
    assert (decision, got) == ("deny", code), (command, reason)
    assert reason.startswith("Saani guard (auto run): refused") and "To go on:" in reason


def test_a_compound_command_is_refused_when_any_part_is(ctx):
    assert sh(ctx, "git status && git diff && ls")[0] == "allow"
    for bad in ("git status && curl https://example.com", "ls; sudo ls", "ls | bash", "git log || git push", "ls\nrm -rf /",
                "ls & git push"):
        assert sh(ctx, bad)[0] == "deny", bad


def test_quotes_are_read_as_bash_reads_them(ctx):
    assert sh(ctx, "git commit -m 'a && git push'")[0] == "allow"
    assert sh(ctx, 'git commit -m "a; git push"')[0] == "allow"
    assert sh(ctx, "echo a\\;git push")[0] == "allow"                  # an escaped ; is a character, not a separator
    assert sh(ctx, "echo 'a'\\''b'")[0] == "allow"
    assert sh(ctx, "echo \"a \\\"b\\\" c\"")[0] == "allow"
    words = guard.split_bash("git commit -m 'one two' \"three $\" four\\ five")[0][0]
    assert words == ["git", "commit", "-m", "one two", "three $", "four five"]
    assert [w for w, _ in guard.split_bash("a && b || c; d | e\nf & g")] == [["a"], ["b"], ["c"], ["d"], ["e"], ["f"], ["g"]]


def test_a_cd_moves_the_folder_later_parts_are_checked_in(ctx):
    assert sh(ctx, "cd {run} && rm -rf build")[0] == "allow"
    assert sh(ctx, "cd {run} && cd .. && rm -rf ../webshop/src")[2] == "rm outside the worktrees"


def test_the_refused_command_s_first_word_is_kept_never_its_text(ctx):
    assert sh(ctx, "ls && git push origin secret-branch")[3] == "git"
    assert guard.safe_word("TOKEN=abc123") == "?"
    assert guard.safe_word("curl") == "curl"
    assert guard.safe_word("") == ""


# ---------------------------------------------------------------- secrets, for every tool and inside commands

SECRETS = [".env", ".env.local", "config/.env.production", "server.pem", "tls/server.key", "id_rsa", "id_rsa.pub", "id_ed25519",
           "{home}/.ssh/config", "{home}/.ssh/known_hosts", "credentials.json", "aws/credentials", "cert.p12", "{home}/.netrc",
           "{home}/.npmrc", "{home}/.config/gh/hosts.yml", "app_secret.txt", "client-secrets.yml", "api_token.txt", "TOKEN.md",
           ".ENV", "src\\.env", ".env::$DATA", ".env."]


@pytest.mark.parametrize("name", SECRETS)
def test_secrets_are_refused_to_every_tool(ctx, name):
    for t, key in (("Read", "file_path"), ("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"),
                   ("NotebookEdit", "notebook_path"), ("Glob", "path"), ("Grep", "path"), ("Glob", "pattern"), ("Grep", "glob")):
        decision, reason, code, _ = tool(ctx, t, **{key: name})
        assert (decision, code) == ("deny", "secret"), (t, name, reason)


@pytest.mark.parametrize("command", ["cat .env", "head {home}/.ssh/id_ed25519", "grep x .env.local", "cat server.pem",
                                     "git add .env", "git show HEAD:.env", "git diff -- config/credentials.yml", "cat < .env",
                                     "echo x > .env", "rm .env", "diff a.txt api_token.txt", "python -m pytest -q --cov-config=.env",
                                     "gh issue create --body-file .env", "ls {home}/.ssh", "wc -l app_secret.txt", "cat {home}/.netrc"])
def test_secrets_are_refused_inside_shell_commands(ctx, command):
    decision, reason, code, _ = sh(ctx, command)
    assert (decision, code) == ("deny", "secret"), (command, reason)


def test_a_glob_that_would_reach_a_secret_is_refused(ctx):
    work = pathlib.Path(ctx.places["work"])
    (work / "server.key").write_text("x")
    (work / "notes.txt").write_text("x")
    assert sh(ctx, "cat *.txt")[0] == "allow"
    assert sh(ctx, "cat *.key")[2] == "secret"
    assert sh(ctx, "cat server*")[2] == "secret"
    assert sh(ctx, "rm -f server*")[2] == "secret"
    assert sh(ctx, "rm -rf *")[0] == "deny"                              # it reaches saani/guard.py and server.key


def test_a_branch_or_text_that_mentions_token_is_no_secret_but_a_token_file_is(ctx):
    assert sh(ctx, "git switch feat/15-rotate-token")[0] == "allow"
    assert sh(ctx, "grep -n token src/app.py")[0] == "allow"
    assert sh(ctx, "gh issue create --title 'Rotate the API token' --body 'secrets stay out'")[0] == "allow"
    (pathlib.Path(ctx.places["work"]) / "token").write_text("x")
    assert sh(ctx, "cat token")[2] == "secret"
    assert tool(ctx, "Grep", pattern="token|secret", path="{work}/src")[0] == "allow"      # Grep's pattern is about contents


def test_secret_names(ctx):
    for name in (".env", "a/.env.test", "k.PEM", "id_ecdsa", "C:\\Users\\me\\.ssh\\x", "x/gh/hosts.yml", "my-secret", "tokens.json"):
        assert guard.secret_name(name), name
    for name in ("src/app.py", "environment.md", "README.md", "keys.py", "hosts.yml", "monkey", "docs/env.md"):
        assert not guard.secret_name(name), name
    assert not guard.secret_name("feat/1-rotate-token", strict=False)


# ---------------------------------------------------------------- other tools

def test_reads_are_allowed_anywhere_but_secrets(ctx):
    assert tool(ctx, "Read", file_path="{main}/src/app.py")[0] == "allow"
    assert tool(ctx, "Read", file_path="{work}/saani/guard.py")[0] == "allow"
    assert tool(ctx, "Glob", pattern="**/*.py", path="{main}")[0] == "allow"
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="*.py")[0] == "allow"
    assert tool(ctx, "Glob", pattern="**/.env")[2] == "secret"


def test_edits_are_allowed_only_in_the_worktrees_the_run_s_folder_and_temp(ctx):
    for t, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
        for ok in ("{work}/src/app.py", "{run}/notes.md", "{temp}/scratch.txt", "{wt}/13-other/x.py", "src/relative.py"):
            assert tool(ctx, t, **{key: ok})[0] == "allow", (t, ok)
        for bad in ("{main}/src/app.py", "{home}/notes.txt", "{home}/.bashrc", "{wt}/../outside.py", "{work}/../../webshop/x.py",
                    "~/x.txt"):
            assert tool(ctx, t, **{key: bad})[2] == "edit outside the worktrees", (t, bad)


@pytest.mark.parametrize("path", ["{work}/saani/guard.py", "{work}/saani.toml", "{work}/hooks/hooks.json", "{work}/hooks/other.sh",
                                  "{work}/.claude/settings.json", "{work}/.claude/settings.local.json", "{temp}/saani/guard.py",
                                  "{main}/saani.toml", "{main}/.git/saani-auto.json", "{main}/.git/saani-auto-refusals.jsonl"])
def test_the_guard_s_own_files_are_never_edited_in_an_auto_run(ctx, path):
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
        assert sh(ctx, "echo x > '" + work.replace("/", "\\") + "\\out.txt'")[0] == "allow"


def test_other_tools_are_allowed_unknown_ones_refused_and_questions_never_asked(ctx):
    for name in ("Agent", "Task", "TodoWrite", "ToolSearch", "Skill", "SendMessage", "WebSearch", "WebFetch", "BashOutput", "KillShell"):
        assert guard.decide({"tool_name": name, "tool_input": {}}, ctx)[0] == "allow", name
    assert guard.decide({"tool_name": "mcp__x__delete", "tool_input": {}}, ctx)[2] == "unknown tool"
    assert guard.decide({"tool_name": None}, ctx)[2] == "unknown tool"
    assert guard.decide({"tool_name": "AskUserQuestion", "tool_input": {}}, ctx)[2] == "question"
    assert guard.decide({"tool_name": "Bash", "tool_input": {}}, ctx)[2] == "unreadable command"
    assert guard.decide({"tool_name": "Write", "tool_input": {"content": "x"}}, ctx)[2] == "unreadable path"


# ---------------------------------------------------------------- the state file, the hook and the refusal log

def fake_repo(tmp_path, toml=None):
    """A main checkout with a .git folder and saani.toml, and one worktree whose .git file points at it (no git process needed)."""
    main = tmp_path / "webshop"
    (main / ".git" / "worktrees" / "12-cart-total").mkdir(parents=True)
    (main / "saani.toml").write_text(toml or '[project]\nrepo = "acme/webshop"\n\n[worktrees]\nfolder = "../webshop-wt"\n\n'
                                     '[tests]\ntier1 = ["{python}", "-m", "pytest", "-q"]\n', encoding="utf-8")
    work = tmp_path / "webshop-wt" / "12-cart-total"
    work.mkdir(parents=True)
    (work / ".git").write_text(f"gitdir: {fwd(main / '.git' / 'worktrees' / '12-cart-total')}\n")
    (main / ".git" / "worktrees" / "12-cart-total" / "commondir").write_text("../..\n")
    return main, work


def hook(cwd, tool_name, **tool_input):
    event = {"session_id": "s", "transcript_path": "t", "cwd": str(cwd), "permission_mode": "default", "hook_event_name": "PreToolUse",
             "tool_name": tool_name, "tool_input": tool_input}
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "guard"], input=json.dumps(event).encode(), capture_output=True,
                       cwd=cwd, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.decode().strip()


def test_the_shared_git_folder_is_found_from_a_worktree_without_git(tmp_path):
    main, work = fake_repo(tmp_path)
    assert guard.git_dirs(work) == (str(work), os.path.normpath(str(main / ".git")))
    (work / "deeper").mkdir()
    assert guard.git_dirs(work / "deeper") == (str(work), os.path.normpath(str(main / ".git")))
    assert guard.git_dirs(main) == (str(main), str(main / ".git"))


def test_turn_on_and_off(tmp_path):
    main, work = fake_repo(tmp_path)
    assert guard.state(work) is None
    st = guard.turn_on(work, "2026-01-02_0300", tmp_path / "webshop-wt" / "auto-2026-01-02_0300")
    assert st["deploy"] is False and st["id"] == "2026-01-02_0300"
    assert json.loads((main / ".git" / "saani-auto.json").read_text()) == st
    assert guard.state(main) == st                                       # one state for every worktree
    assert guard.turn_off(main) == st
    assert guard.state(work) is None and guard.turn_off(work) is None
    (main / ".git" / "saani-auto.json").write_text("not json")
    assert guard.state(work) == {}                                       # a broken file still means on
    with pytest.raises(ValueError):
        guard.turn_on(pathlib.Path(tmp_path.anchor) / "no-such-folder-xyz", "x", "y")


def test_present_mode_prints_nothing(tmp_path):
    main, work = fake_repo(tmp_path)
    assert hook(work, "Bash", command="git push --force") == ""
    assert hook(work, "Write", file_path=str(pathlib.Path.home() / ".bashrc"), content="x") == ""
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "guard"], input=b"not json", capture_output=True, cwd=work)
    assert (r.returncode, r.stdout) == (0, b"")
    assert not (main / ".git" / guard.REFUSALS).exists()


def test_an_auto_run_gets_a_decision_for_every_call_and_each_refusal_is_logged(tmp_path):
    main, work = fake_repo(tmp_path)
    guard.turn_on(work, "run-1", tmp_path / "webshop-wt" / "auto-run-1")
    out = json.loads(hook(work, "Bash", command="git status && git diff"))
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    out = json.loads(hook(work, "Bash", command="git push origin HEAD:dev --secret-words-here"))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "saani land" in out["permissionDecisionReason"]
    assert json.loads(hook(work, "Read", file_path=str(work / ".env")))["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert json.loads(hook(work, "Write", file_path=str(pathlib.Path.home() / "x.txt")))["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert json.loads(hook(work, "Bash", command="python -m pytest -q"))["hookSpecificOutput"]["permissionDecision"] == "allow"
    log = (main / ".git" / guard.REFUSALS).read_text().splitlines()
    rows = [json.loads(line) for line in log]
    assert [(r["tool"], r["reason"], r["word"], r["run"]) for r in rows] == [
        ("Bash", "git push", "git", "run-1"), ("Read", "secret", "", "run-1"), ("Write", "edit outside the worktrees", "", "run-1")]
    assert "secret-words-here" not in "".join(log) and "HEAD:dev" not in "".join(log)
    assert guard.refusals(work) == {"total": 3, "by_reason": {"git push": 1, "secret": 1, "edit outside the worktrees": 1}}
    assert guard.refusals(work, run="other")["total"] == 0
    guard.turn_off(work)
    assert guard.refusals(work)["total"] == 3                            # none on: every line


def test_an_internal_error_in_an_auto_run_is_a_refusal_never_a_crash(tmp_path):
    main, work = fake_repo(tmp_path, toml='[project]\nrepo = "acme/webshop"\n[nonsense]\nkey = 1\n')
    guard.turn_on(work, "run-2", work)
    out = json.loads(hook(work, "Bash", command="ls"))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "guard error: SystemExit" in out["permissionDecisionReason"]
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "guard"], input=b"{broken", capture_output=True, cwd=work)
    assert b"guard error: ValueError" in r.stdout
    assert guard.refusals(work)["by_reason"] == {"guard error": 2}


def test_the_hook_reads_the_project_s_settings(tmp_path):
    main, work = fake_repo(tmp_path)
    guard.turn_on(work, "run-3", tmp_path / "webshop-wt" / "auto-run-3")
    c = guard.context(str(work), guard.state(work))
    assert c.wt_dir == guard.Context.norm(tmp_path / "webshop-wt") and c.repo == "acme/webshop"
    assert c.main == guard.Context.norm(main) and c.tests[0] == ["{python}", "-m", "pytest", "-q"]
    assert c.folder == guard.Context.norm(tmp_path / "webshop-wt" / "auto-run-3")


# ---------------------------------------------------------------- the plugin wiring

def test_the_plugin_ships_the_hook_through_its_own_saani_command():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = hooks["hooks"]["PreToolUse"]
    assert entry["matcher"] == "*"
    assert entry["hooks"] == [{"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/bin/saani" guard'}]
    assert '"$@"' in (ROOT / "bin" / "saani").read_text() and "%*" in (ROOT / "bin" / "saani.cmd").read_text()
