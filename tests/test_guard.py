"""saani/guard.py: the plugin's PreToolUse hook. Present mode (no auto entry for the session) prints nothing; an auto session gets allow or
deny for every tool call, never ask. Every rule is fed through the decision function, and the hook itself through `saani guard` with JSON
on stdin. The cases of the security review are kept here, each with the decision it must get."""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from saani import guard

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = str(ROOT).replace("\\", "/")


def fwd(p):
    return str(p).replace("\\", "/")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home of its own, so the registry (~/.saani/auto) and ~ are the test's."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


@pytest.fixture
def ctx(tmp_path, home):
    """An auto run of acme/webshop: the main checkout, the worktrees folder with the run's folder and one issue's worktree, a temp folder."""
    main, wt, temp = tmp_path / "webshop", tmp_path / "webshop-wt", tmp_path / "temp"
    for d in (main, wt / "auto-2026-01-02_0300", wt / "12-cart-total" / "src", wt / "12-cart-total" / "saani", temp):
        d.mkdir(parents=True, exist_ok=True)
    (wt / "12-cart-total" / "saani" / "guard.py").write_text("")
    (wt / "12-cart-total" / ".git").write_text("gitdir: x")
    c = guard.Context(wt / "12-cart-total", wt_dir=wt, folder=wt / "auto-2026-01-02_0300", temp=temp, main=main, repo="acme/webshop",
                      integration="dev", production="main", tests=[["{python}", "-m", "pytest", "-q"], ["npm", "test"]])
    c.places = {"main": fwd(main), "wt": fwd(wt), "run": fwd(wt / "auto-2026-01-02_0300"), "work": fwd(wt / "12-cart-total"),
                "temp": fwd(temp), "home": fwd(home), "plugin": PLUGIN}
    return c


def sh(ctx, command):
    return guard.decide({"tool_name": "Bash", "tool_input": {"command": command.format(**ctx.places)}}, ctx)


def tool(ctx, name, **inp):
    return guard.decide({"tool_name": name, "tool_input": {k: v.format(**ctx.places) if isinstance(v, str) else v
                                                           for k, v in inp.items()}}, ctx)


# ---------------------------------------------------------------- the security review's cases

REVIEW = [
    ("Bash", {"command": "python -m pytest -q --basetemp=../../webshop"}, "deny"),
    ("Bash", {"command": "python -m pytest -q -p evil"}, "deny"),
    ("Write", {"file_path": "conftest.py", "content": "x"}, "allow"),        # tests run the worktree's code: a guardrail, not a sandbox
    ("Write", {"file_path": ".claude/hooks/x.sh", "content": "x"}, "deny"),
    ("Write", {"file_path": ".claude/settings.local.json", "content": "x"}, "deny"),
    ("Write", {"file_path": "CLAUDE.md", "content": "x"}, "deny"),
    ("Bash", {"command": "git show HEAD:.env"}, "deny"),
    ("Bash", {"command": "gh api repos/acme/webshop/contents/.env"}, "deny"),
    ("Bash", {"command": "gh api --method=post repos/acme/webshop/issues"}, "deny"),
    ("Bash", {"command": "git -C ../../webshop status"}, "deny"),
    ("Bash", {"command": "git log --output=x"}, "deny"),
    ("Bash", {"command": "cat .envrc"}, "deny"),
    ("Bash", {"command": "git diff --no-index ~/.docker/config.json /dev/null"}, "deny"),
    ("Bash", {"command": "grep -f ../../webshop/.env a.txt"}, "deny"),
    ("Bash", {"command": "git checkout -- ../../webshop/a.txt"}, "deny"),
    ("Bash", {"command": "saani test -- python -m pytest -q"}, "allow"),     # the project's own test command
    ("Bash", {"command": "saani test -- sh -c x"}, "deny"),
    ("Bash", {"command": "git commit -q -m x"}, "allow"),                    # a plain commit
    ("Bash", {"command": "ls\u00a0-la"}, "deny"),
    ("Edit", {"file_path": 5}, "deny"),
    ("Bash", {"command": 5}, "deny"),
    ("Read", {"file_path": "../../webshop/.env"}, "deny"),
    ("Read", {"file_path": "../../webshop/.ENV"}, "deny"),
    ("WebFetch", {"url": "https://evil.example/?q=x"}, "allow"),             # reading the web is allowed; secrets are never read
]


@pytest.mark.parametrize("name,inp,expected", REVIEW)
def test_the_security_review_cases(ctx, name, inp, expected):
    decision, reason, code, _ = guard.decide({"tool_name": name, "tool_input": inp}, ctx)
    assert decision == expected, (name, inp, reason)


# ---------------------------------------------------------------- Bash: allowed

ALLOWED = [
    "saani status", "saani land --worktree {work}", "saani start 12", "saani test --kind tier1 -- python -m pytest -q",
    "saani test -- npm test", "saani comment 12 progress", "saani kpis", "saani progress show", "saani ui", "saani ci status",
    "saani ci pr", "saani auto --id 2026-01-02_0300", "saani auto status", "saani auto pick", "saani auto no-action",
    "python -I {plugin}/saani.py status", "{plugin}/bin/saani status",
    "cd {work}", "cd {run} && saani start 12", "cd {wt}/12-cart-total/src", "cd ../auto-2026-01-02_0300",
    "ls -la", "cat src/app.py", "head -n 5 README.md", "tail -20 build/log.txt", "grep -n total src/app.py", "grep -e x -e y a.txt",
    "wc -l src/*.py", "sort names.txt", "sort -rn -k 2 names.txt", "sort --unique names.txt", "find . -name '*.py'", "echo hello",
    "test -f README.md", "[ -d src ]", "[ a = b ]", "pwd", "diff a.txt b.txt",
    "git status", "git diff origin/dev", "git log --oneline -5", "git log --format=%H", "git show HEAD", "git add src/app.py", "git add -A",
    "git commit -m 'Cart: total in cents (refs #12)' -m 'Why: rounding' -m 'Undo: revert'", "git commit --amend --no-edit",
    "git commit -am 'Cart: -f is only text here, as is .env'",
    "git commit -m \"$(cat <<'EOF'\nCart: total (refs #12)\n\nWhy: x\nUndo: y\nEOF\n)\"",
    "git commit -F - <<'EOF'\nCart: total (refs #12)\n\nWhy: x\nUndo: y\nEOF",
    "git commit -F - <<'EOF'\nCart: total\nEOF\ngit status",
    "git switch feat/12-cart-total", "git switch -c bug/13-cart-rounding origin/dev", "git checkout -b task/14-docs",
    "git checkout -- src/app.py", "git checkout origin/dev -- src/app.py", "git merge origin/dev", "git merge --no-edit origin/dev",
    "git merge --abort", "git rebase origin/dev", "git rebase --continue", "git fetch origin dev", "git fetch -q origin",
    "git worktree list --porcelain", "git rev-parse HEAD", "git branch --list 'feat/*'", "git branch --show-current", "git branch -a",
    "git branch", "git ls-files", "git stash", "git stash push -m 'wip on .env text'", "git stash pop", "git restore src/app.py",
    "git -C {work} status", "git --no-pager log", "git diff --exit-code",
    "python -m pytest -q", "python -m pytest -q tests/test_cart.py", "python -m pytest -q -x -k cart", "python -m pytest -q --tb=short",
    "npm test",
    "gh issue view 12", "gh issue list --label bug", "gh issue list -lbug -L5", "gh pr view 3", "gh pr list", "gh pr checks 3",
    "gh pr diff 3", "gh run view 9", "gh run list", "gh run watch 9", "gh api repos/acme/webshop/issues/12",
    "gh api repos/acme/webshop/pulls --jq .title", "gh api -i repos/acme/webshop/pulls", "gh api --paginate repos/acme/webshop/pulls",
    "gh issue comment 12 --body 'On dev: abc123, the token is gone'",
    "gh issue comment 12 --body \"$(cat <<'EOF'\nOn dev\n\nWhy: x\nEOF\n)\"",
    "gh pr create --base main --head dev --title 'Release' --body \"$(cat <<'EOF'\nrefs #12\nEOF\n)\"",
    "gh issue edit 12 --add-label in-dev --remove-label idea", "gh issue create --title 'Cart rounding' --body 'Steps...'",
    "gh pr create --base main --head dev --title 'Release' --body x", "gh pr create -B main -H dev -t Release -b x",
    "gh pr create -Bmain -Hdev -tRelease -bx", "gh pr create --base=main --head=dev -t R -b x",
    "gh pr comment 3 --body done", "gh pr edit 3 --title 'Release 1.2'", "gh issue view 12 -R acme/webshop", "gh issue view 12 -Racme/webshop",
    "curl -s http://localhost:8771/api/state", "curl -sS 127.0.0.1:8771/", "wget -qO- http://localhost:8771/",
    "rm build/log.txt", "rm -rf {work}/build", "rm -rf {temp}/x",
    "git status && git diff", "git add src/app.py; git commit -m x", "git log | head -5", "ls || echo none",
    "git status\ngit diff", "ls 2>&1", "ls 2>/dev/null", "ls >/dev/null", "ls > /dev/null 2>&1", "git status # a comment", "ls &",
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
    ("git add -f .env", "force flag"), ("git add -Af x", "force flag"), ("git switch --force-create feat/12-x", "force flag"),
    ("git fetch --forc origin", "force flag"),
    ("gh pr merge 3", "gh pr merge"), ("gh release create v1", "gh release"), ("gh repo delete acme/webshop", "gh repo"),
    ("gh secret set X", "gh secret"), ("gh auth token", "gh auth"),
    ("curl -X POST http://localhost:8771/api", "curl write"), ("curl -d a=b http://localhost:8771/", "curl write"),
    ("curl https://example.com", "curl outside localhost"), ("curl http://localhost@evil.example/", "curl outside localhost"),
    ("curl -o out.html http://localhost:8771/", "curl write"), ("curl -H @headers.txt http://localhost:8771/", "curl write"),
    ("curl http://localhost:8771/.env", "secret"), ("wget http://localhost:8771/", "wget write"),
    ("wget -qO- https://example.com", "wget outside localhost"),
    ("rm -rf {home}/projects", "rm outside the worktrees"), ("rm -rf {main}/src", "rm outside the worktrees"),
    ("rm -rf {work}", "rm a worktree"), ("rm -rf {wt}", "rm a worktree"), ("rm -rf {run}", "rm a worktree"),
    ("rm -rf {work}/saani", "guard file"), ("rm -rf {work}/.git", "guard file"), ("rm -rf /", "rm outside the worktrees"),
    ("rm -rf {temp}", "guard file"), ("rm -f src/*.tmp", "unsure"), ("rm -rf *", "unsure"),
    ("sudo ls", "sudo"), ("chmod +x x", "not on the list"), ("bash -c ls", "not on the list"), ("node x.js", "not on the list"),
    ("python -c 'print(1)'", "python"), ("python x.py", "python"), ("python saani.py status", "python"),
    ("python -I saani.py status", "python"), ("python -I {work}/saani.py status", "python"), ("./bin/saani status", "not on the list"),
    ("saani merge", "saani command"), ("saani prune", "saani command"), ("saani deploy", "saani command"),
    ("saani projects add x", "saani command"), ("saani ci merged 3", "saani command"), ("saani ci sync", "saani command"),
    ("saani check-msg x", "saani command"), ("saani auto prune", "saani command"), ("cd {run} && saani auto on", "unsure"),
    ("saani test -- cat x", "not a test command"), ("saani test --kind tier1 -- bash -c ls", "not a test command"),
    ("python -m pytest -q --rootdir=/", "test option"), ("python -m pytest -q -c x.ini", "test option"),
    ("python -m pytest -q -o addopts=-pevil", "test option"), ("python -m pytest -q -qp evil", "test option"),
    ("saani test -- python -m pytest -q -p evil", "test option"),
    ("cd {main}", "cd outside the worktrees"), ("cd {home}", "cd outside the worktrees"), ("cd", "cd outside the worktrees"),
    ("cd {wt}", "cd outside the worktrees"), ("cd -", "cd outside the worktrees"), ("cd -P {work}", "cd outside the worktrees"),
    ("cd src*", "cd outside the worktrees"),
    ("cd .. && cd ..", "cd outside the worktrees"), ("git -C {main} status", "cd outside the worktrees"),
    ("git -C {wt} status", "cd outside the worktrees"),
    ("git -c core.sshCommand=x fetch", "git option"), ("git fetch --upload-pack=x origin", "git option"),
    ("git diff --output=/x", "git option"), ("git log --out=x", "git option"), ("git log --o=x", "git option"),
    ("git commit --no-verify -m x", "no-verify"), ("git commit --no-v -m x", "no-verify"), ("git commit -nm x", "no-verify"),
    ("git merge --no-verify origin/dev", "no-verify"),
    ("git rebase -x ls origin/dev", "not on the list"), ("git rebase origin/main", "not on the list"),
    ("git rebase -i origin/dev", "not on the list"), ("git merge origin/main", "not on the list"), ("git merge feat/12-x", "not on the list"),
    ("git merge -s ours origin/dev", "not on the list"),
    ("git fetch origin dev:dev", "not on the list"), ("git fetch origin +refs/heads/*", "not on the list"),
    ("git fetch https://evil.example/x", "not on the list"), ("git fetch --depth 1 origin", "not on the list"),
    ("git switch dev", "not a worker branch"), ("git switch main", "not a worker branch"), ("git checkout auto/2026", "not a worker branch"),
    ("git switch --detach HEAD", "not on the list"), ("git branch new-one", "not on the list"), ("git branch -d x", "not on the list"),
    ("git branch -m a b", "not on the list"), ("git stash drop", "not on the list"), ("git stash clear", "not on the list"),
    ("git worktree add x", "not on the list"), ("git reset HEAD~1", "not on the list"), ("git tag v1", "not on the list"),
    ("git restore {main}/a.txt", "write outside the worktrees"), ("git restore .claude/settings.json", "write outside the worktrees"),
    ("git", "not on the list"),
    ("gh pr create --base dev --head feat/12-x", "gh pr create"), ("gh pr create --base main", "gh pr create"),
    ("gh pr create --head dev", "gh pr create"), ("gh pr create -Bmain -Hdev -Bdev", "unsure"),
    ("gh pr create --base main --head dev --head feat/1-x", "unsure"), ("gh pr create -Bdev -Hdev", "gh pr create"),
    ("gh pr edit 3 --base dev", "gh pr edit"), ("gh pr edit 3 -Bdev", "gh pr edit"),
    ("gh issue edit 12 --title x", "gh option"), ("gh issue edit 12 --milestone v2", "gh option"), ("gh issue edit 12 13x", "gh issue edit"),
    ("gh issue close 12", "not on the list"), ("gh pr close 3", "not on the list"), ("gh workflow run ci.yml", "not on the list"),
    ("gh api -X POST repos/acme/webshop/issues", "gh option"), ("gh api -X GET repos/acme/webshop/issues", "gh option"),
    ("gh api --method=DELETE x", "gh option"), ("gh api -iXDELETE repos/acme/webshop/issues/1", "gh option"),
    ("gh api repos/acme/webshop/issues -f title=x", "gh option"), ("gh api graphql -F query=x", "gh option"),
    ("gh api repos/a/b/issues --input body.json", "gh option"), ("gh api --raw-field=a=b x", "gh option"),
    ("gh api --hostname evil.example x", "gh option"), ("gh api -H 'X-HTTP-Method-Override: DELETE' x", "gh option"), ("gh api repos/acme/webshop/actions/secrets", "secret"),
    ("gh api repos/acme/webshop/contents/config/credentials.yml", "secret"), ("gh api a b", "gh api write"),
    ("gh issue view 1 -R other/repo", "another repository"), ("gh issue view 1 -Rother/repo", "another repository"),
    ("gh issue view 1 --repo=other/repo", "another repository"), ("gh issue view 1 -R acme/webshop -R other/repo", "unsure"),
    ("gh issue comment 1 --delete-last", "gh option"), ("gh issue comment 1 --body-file .env", "secret"),
    ("gh issue comment 1 -F.env", "secret"),
    ("gh issue create --body \"$(cat <<'EOF'\nx\nEOF\n)\"", "unsure"), ("gh pr view \"$(cat <<'EOF'\nx\nEOF\n)\"", "unsure"),
    ("find . -delete", "find writes"), ("find . -name x -execdir ls \\;", "find writes"),
    ("sort -o {main}/x names.txt", "sort option"), ("sort --compress-program=sh names.txt", "sort option"),
    ("sort -T /tmp names.txt", "sort option"), ("grep -r total .", "recursive read"),
    ("grep -rn total src", "recursive read"), ("diff -r a b", "recursive read"),
    ("echo x > {main}/notes.txt", "unsure"), ("echo x > {work}/notes.txt", "unsure"), ("echo x >> notes.txt", "unsure"),
    ("ls >| x", "unsure"), ("cat < src/app.py", "unsure"), ("ls 1>&2", "unsure"), ("ls &> x", "unsure"), ("ls |& cat", "unsure"),
    ("cat <(ls)", "unsure"), ("diff <(ls) >(cat)", "unsure"), ("ls>/dev/null", "unsure"),
    ("ls $(pwd)", "unsure"), ("ls `pwd`", "unsure"), ("cat $HOME/x", "unsure"), ('echo "$HOME"', "unsure"), ("cat ${{X}}", "unsure"),
    ("echo $'\\x41'", "unsure"), ('echo $"x"', "unsure"), ("echo $[1+1]", "unsure"), ("echo a$", "unsure"), ('echo "a $ b"', "unsure"),
    ("(cd /; ls)", "unsure"), ("{{ ls; }}", "unsure"), ("cat <<EOF\nx\nEOF", "unsure"), ("cat <<< x", "unsure"), ("echo 'open", "unsure"),
    ("X=1 ls", "unsure"), ("X=1", "unsure"), ("ls ~root", "unsure"), ("ls\r", "unsure"), ("ls\x00", "unsure"), ("ls;; ls", "unsure"),
    ("git commit -m \"$(cat <<'EOF'\nx\nEOF\nrm -rf /\nEOF\n)\"", "unsure"),
    ("git commit -m \"$(cat <<EOF\nx\nEOF\n)\"", "unsure"), ("git commit -m x\"$(cat <<'EOF'\nx\nEOF\n)\"", "unsure"),
    ("git commit -m \"$(cat <<'EOF'\nx\nEOF\n)\"x", "unsure"), ("echo \"$(cat <<'EOF'\nx\nEOF\n)\"", "unsure"),
    ("git log \"$(cat <<'EOF'\nx\nEOF\n)\"", "unsure"), ("git stash push -m \"$(cat <<'EOF'\nx\nEOF\n)\"", "unsure"),
    ("cat - <<'EOF'\nx\nEOF", "unsure"), ("git commit <<'EOF'\nx\nEOF", "unsure"), ("git status <<'EOF'\nx\nEOF", "unsure"),
    ("git commit -F - <<'EOF'\nx\nEOF\nrm -rf /", "rm outside the worktrees"),
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
    assert sh(ctx, "echo '$HOME'")[0] == "allow"                       # single quotes: no expansion
    words = guard.split_bash("git commit -m 'one two' \"three\" four\\ five")[0].words
    assert words == ["git", "commit", "-m", "one two", "three", "four five"]
    assert [p.words for p in guard.split_bash("a && b || c; d | e\nf & g")] == [["a"], ["b"], ["c"], ["d"], ["e"], ["f"], ["g"]]


def test_gh_options_are_read_the_way_gh_reads_them():
    spec = guard.GH_COMMON + " " + guard.GH_FLAGS[("pr", "create")]
    flags, positional = guard.pflags(["-Bmain", "-H", "dev", "--title=x", "-dtY", "--", "-z"], spec)
    assert [(n, v) for n, v, _ in flags] == [("base", "main"), ("head", "dev"), ("title", "x"), ("draft", None), ("title", "Y")]
    assert positional == [("-z", 6)]
    assert guard.pflags(["-B=main"], spec)[0][0][1] == "main"
    for bad in (["-z"], ["--nope"], ["-B"], ["--draft=false"]):
        with pytest.raises(guard.Unsure):
            guard.pflags(bad, spec)


def test_a_cd_moves_the_folder_later_parts_are_checked_in(ctx):
    assert sh(ctx, "cd {run} && rm -rf build")[0] == "allow"
    assert sh(ctx, "cd {run} && cd .. && rm -rf ../webshop/src")[2] == "cd outside the worktrees"


def test_the_refused_command_s_first_word_is_kept_never_its_text(ctx):
    assert sh(ctx, "ls && git push origin secret-branch")[3] == "git"
    assert guard.safe_word("TOKEN=abc123") == "?"
    assert guard.safe_word("curl") == "curl"
    assert guard.safe_word("") == ""


# ---------------------------------------------------------------- secrets, for every tool and inside commands

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
        assert (decision, code) == ("deny", "secret"), (t, name, reason)


@pytest.mark.parametrize("command", ["cat .env", "head {home}/.ssh/id_ed25519", "grep x .env.local", "cat server.pem",
                                     "git add .env", "git show HEAD:.env", "git diff -- config/credentials.yml", "cat .envrc",
                                     "diff a.txt api_token.txt", "python -m pytest -q {work}/.env",
                                     "gh issue create --body-file .env", "ls {home}/.ssh", "wc -l app_secret.txt", "cat {home}/.netrc",
                                     "cat /proc/self/environ", "grep -f .env a.txt", "grep --file=.env a.txt", "grep -if .env a.txt",
                                     "cat {home}/.kube/config", "git commit -F .env", "rm .env"])
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


# ---------------------------------------------------------------- other tools

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
    assert tool(ctx, "Grep", pattern="x", type="pem")[2] == "secret"
    (pathlib.Path(ctx.places["work"]) / ".env").write_text("x")
    assert tool(ctx, "Grep", pattern="total", path="{work}")[2] == "secret"
    assert tool(ctx, "Grep", pattern="total", path="{work}", glob="*.py")[0] == "allow"
    assert tool(ctx, "Grep", pattern="total", path="{work}", type="py")[0] == "allow"


def test_edits_are_allowed_only_in_the_worktrees_the_run_s_folder_and_temp(ctx):
    for t, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
        for ok in ("{work}/src/app.py", "{run}/notes.md", "{temp}/scratch.txt", "{wt}/13-other/x.py", "src/relative.py",
                   "{work}/.github/workflows/ci.yml"):
            assert tool(ctx, t, **{key: ok})[0] == "allow", (t, ok)
        for bad in ("{main}/src/app.py", "{home}/notes.txt", "{home}/.bashrc", "{wt}/../outside.py", "{work}/../../webshop/x.py",
                    "~/x.txt"):
            assert tool(ctx, t, **{key: bad})[2] == "edit outside the worktrees", (t, bad)


@pytest.mark.parametrize("path", ["{work}/saani/guard.py", "{work}/saani.toml", "{work}/hooks/hooks.json", "{work}/hooks/other.sh",
                                  "{work}/.claude/settings.json", "{work}/.claude/settings.local.json", "{work}/.claude/agents/x.md",
                                  "{work}/.git", "{work}/.githooks/commit-msg", "{work}/.mcp.json", "{work}/CLAUDE.md",
                                  "{work}/src/CLAUDE.md", "{temp}/saani/guard.py", "{temp}/.git/config", "{home}/.saani/auto/x.json",
                                  "{home}/.saani/projects.toml", "{main}/saani.toml", "{main}/.git/hooks/pre-commit"])
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
    for name in ("Agent", "Task", "TodoWrite", "ToolSearch", "Skill", "SendMessage", "WebSearch", "WebFetch", "BashOutput", "KillShell"):
        assert guard.decide({"tool_name": name, "tool_input": {}}, ctx)[0] == "allow", name
    assert guard.decide({"tool_name": "mcp__x__delete", "tool_input": {}}, ctx)[2] == "unknown tool"
    assert guard.decide({"tool_name": None}, ctx)[2] == "unknown tool"
    assert guard.decide({"tool_name": "AskUserQuestion", "tool_input": {}}, ctx)[2] == "question"
    assert guard.decide({"tool_name": "Bash", "tool_input": {}}, ctx)[2] == "unreadable command"
    assert guard.decide({"tool_name": "Write", "tool_input": {"content": "x"}}, ctx)[2] == "unreadable path"


# ---------------------------------------------------------------- the registry, the switch, the hook and the refusal log

def fake_repo(tmp_path, toml=None):
    """A main checkout with a .git folder and saani.toml, the run's folder and one worktree whose .git file points at it (no git)."""
    main = tmp_path / "webshop"
    (main / ".git" / "worktrees" / "12-cart-total").mkdir(parents=True)
    (main / "saani.toml").write_text(toml or '[project]\nrepo = "acme/webshop"\n\n[worktrees]\nfolder = "../webshop-wt"\n\n'
                                     '[tests]\ntier1 = ["{python}", "-m", "pytest", "-q"]\n', encoding="utf-8")
    work = tmp_path / "webshop-wt" / "12-cart-total"
    work.mkdir(parents=True)
    (work / ".git").write_text(f"gitdir: {fwd(main / '.git' / 'worktrees' / '12-cart-total')}\n")
    (main / ".git" / "worktrees" / "12-cart-total" / "commondir").write_text("../..\n")
    return main, work


def hook(home, cwd, tool_name, session="s-1", raw=None, **tool_input):
    event = {"session_id": session, "transcript_path": "t", "cwd": str(cwd), "permission_mode": "default", "hook_event_name": "PreToolUse",
             "tool_name": tool_name, "tool_input": tool_input}
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "guard"], input=raw if raw is not None else json.dumps(event).encode(),
                       capture_output=True, cwd=cwd, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode().strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


AUTO_ON = f"python -I {PLUGIN}/saani.py auto on"


def test_the_shared_git_folder_is_found_from_a_worktree_without_git(tmp_path):
    main, work = fake_repo(tmp_path)
    assert guard.git_dirs(work) == (str(work), os.path.normpath(str(main / ".git")))
    (work / "deeper").mkdir()
    assert guard.git_dirs(work / "deeper") == (str(work), os.path.normpath(str(main / ".git")))
    assert guard.git_dirs(main) == (str(main), str(main / ".git"))


def test_turn_on_and_off_by_session(tmp_path, home):
    st = guard.turn_on("s-1", "2026-01-02_0300", tmp_path / "webshop", tmp_path / "webshop-wt", tmp_path / "webshop-wt" / "auto-x")
    assert st["deploy"] is False and st["id"] == "2026-01-02_0300"
    assert json.loads((home / ".saani" / "auto" / "s-1.json").read_text()) == st
    assert guard.state("s-1") == st and guard.state("s-2") is None
    assert guard.state("../x") is None and guard.state(None) is None
    assert guard.turn_off("s-1") == st
    assert guard.state("s-1") is None and guard.turn_off("s-1") is None
    with pytest.raises(ValueError):
        guard.turn_on("../evil", "x", "m", "w")
    with pytest.raises(ValueError):
        guard.turn_on("s-1", "a b", "m", "w")


def test_a_broken_entry_fails_closed_and_an_old_one_is_over(tmp_path, home):
    guard.turn_on("s-1", "r", "m", "w")
    (home / ".saani" / "auto" / "s-1.json").write_text("not json")
    with pytest.raises(guard.RegistryError):
        guard.state("s-1")
    old = (datetime.datetime.now() - datetime.timedelta(hours=guard.MAX_HOURS + 1)).isoformat()
    guard.turn_on("s-2", "r", "m", "w", started=old)
    assert guard.state("s-2") is None


def test_present_mode_prints_nothing(tmp_path, home):
    main, work = fake_repo(tmp_path)
    assert hook(home, work, "Bash", command="git push --force") is None
    assert hook(home, work, "Write", file_path=str(home / ".bashrc"), content="x") is None
    assert hook(home, work, "Bash", raw=b"not json") is None
    assert hook(home, work, "Bash", session=None, command="git push") is None
    assert not (home / ".saani" / "auto" / guard.REFUSALS).exists()


def test_saani_auto_on_registers_the_session_and_off_removes_it(tmp_path, home):
    main, work = fake_repo(tmp_path)
    run = tmp_path / "webshop-wt" / "auto-run-1"
    run.mkdir()
    (run / ".git").write_text(f"gitdir: {fwd(main / '.git' / 'worktrees' / '12-cart-total')}\n")
    out = hook(home, run, "Bash", command=AUTO_ON)
    assert out["permissionDecision"] == "allow" and "auto run run-1 on" in out["permissionDecisionReason"]
    st = guard.state("s-1")
    assert st["id"] == "run-1" and st["main"] == str(main) and st["repo"] == "acme/webshop"
    assert guard.norm(st["folder"]) == guard.norm(run) and guard.norm(st["wt_dir"]) == guard.norm(tmp_path / "webshop-wt")
    assert st["tests"][0] == ["{python}", "-m", "pytest", "-q"]
    assert hook(home, work, "Bash", session="s-2", command="git push") is None          # another session stays present
    assert hook(home, work, "Bash", command="git status && git diff")["permissionDecision"] == "allow"
    out = hook(home, work, "Bash", command="git push origin HEAD:dev --secret-words-here")
    assert out["permissionDecision"] == "deny" and "saani land" in out["permissionDecisionReason"]
    assert hook(home, work, "Read", file_path=str(work / ".env"))["permissionDecision"] == "deny"
    assert hook(home, work, "Write", file_path=str(home / ".saani" / "x.json"))["permissionDecision"] == "deny"
    assert hook(home, work, "Bash", command="python -m pytest -q")["permissionDecision"] == "allow"
    sub = {"agent_id": "a-1", "agent_type": "saani:coder"}                              # a subagent's call: same session
    event = {"session_id": "s-1", "cwd": str(work), "tool_name": "Bash", "tool_input": {"command": "git push"}, **sub}
    assert hook(home, work, "Bash", raw=json.dumps(event).encode())["permissionDecision"] == "deny"
    log = (home / ".saani" / "auto" / guard.REFUSALS).read_text().splitlines()
    rows = [json.loads(line) for line in log]
    assert [(r["tool"], r["reason"], r["word"], r["run"]) for r in rows] == [
        ("Bash", "git push", "git", "run-1"), ("Read", "secret", "", "run-1"), ("Write", "guard file", "", "run-1"),
        ("Bash", "git push", "git", "run-1")]
    assert "secret-words-here" not in "".join(log) and "HEAD:dev" not in "".join(log)
    assert guard.refusals("run-1") == {"total": 4, "by_reason": {"git push": 2, "secret": 1, "guard file": 1}}
    assert guard.refusals("other")["total"] == 0
    out = hook(home, work, "Bash", command="saani auto off")
    assert out["permissionDecision"] == "allow" and guard.state("s-1") is None
    assert hook(home, work, "Bash", command="git push") is None
    assert guard.refusals()["total"] == 4


def test_auto_on_registers_only_the_whole_command_from_inside_a_saani_project(tmp_path, home):
    main, work = fake_repo(tmp_path)
    assert hook(home, work, "Bash", command=f"cd {fwd(work)} && {AUTO_ON}") is None
    assert hook(home, work, "Bash", command="python -I saani.py auto on") is None       # not the plugin's saani.py
    assert hook(home, tmp_path, "Bash", command=AUTO_ON) is None                         # not a git checkout
    assert guard.state("s-1") is None
    assert hook(home, work, "Bash", command=AUTO_ON + " --folder " + fwd(main)) is None  # a run folder outside the worktrees
    assert hook(home, main, "Bash", command=AUTO_ON + " --id r-2")["permissionDecision"] == "allow"
    assert guard.state("s-1")["folder"] is None                                          # the main checkout is never the run's folder
    assert hook(home, main, "Bash", command="cd " + fwd(main))["permissionDecision"] == "deny"


def test_the_guard_fails_closed(tmp_path, home):
    main, work = fake_repo(tmp_path)
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt")
    (home / ".saani" / "auto" / "s-1.json").write_text("{broken")
    out = hook(home, work, "Bash", command="ls")
    assert out["permissionDecision"] == "deny" and "guard error: registry" in out["permissionDecisionReason"]
    out = hook(home, work, "Bash", raw=b"{broken")                                       # unreadable input while a run is registered
    assert out["permissionDecision"] == "deny" and "unreadable hook input" in out["permissionDecisionReason"]
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt")
    (home / ".saani" / "auto" / "s-1.json").write_text(json.dumps({**guard.state("s-1"), "worker_branch": "("}))
    out = hook(home, work, "Bash", command="ls")
    assert out["permissionDecision"] == "deny" and "guard error" in out["permissionDecisionReason"]


# ---------------------------------------------------------------- the plugin wiring

def test_the_plugin_ships_the_hook_through_its_own_saani_command():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = hooks["hooks"]["PreToolUse"]
    assert entry["matcher"] == "*"
    assert entry["hooks"] == [{"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/bin/saani" guard', "timeout": 15}]
    launcher = (ROOT / "bin" / "saani").read_text()
    assert '"$@"' in launcher and "%*" in (ROOT / "bin" / "saani.cmd").read_text()
    assert "py -3" in launcher and "WindowsApps" in launcher and "sys.exit(sys.version_info" not in launcher
