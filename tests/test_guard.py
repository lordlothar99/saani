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
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("x")                                           # a key a glob must never reach
    c = guard.Context(wt / "12-cart-total", wt_dir=wt, folder=wt / "auto-2026-01-02_0300", temp=temp, main=main, repo="acme/webshop",
                      integration="dev", production="main", tests=[["{python}", "-m", "pytest", "-q"], ["npm", "test"]])
    c.places = {"main": fwd(main), "wt": fwd(wt), "run": fwd(wt / "auto-2026-01-02_0300"), "work": fwd(wt / "12-cart-total"),
                "temp": fwd(temp), "home": fwd(home), "plugin": PLUGIN, "python_exe": fwd(sys.executable)}
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
    "saani test --worktree {work} -- python -m pytest -q", "saani test --kind landing --worktree={work} -- python -m pytest -q tests/test_a.py",
    "saani test --worktree {run} --kind full -- python -m pytest -q", "saani test --worktree {work} python -m pytest -q",
    "cd {work}/src && saani test -- python -m pytest -q tests",
    "saani status", "saani land --worktree {work}", "saani start 12", "saani test --kind landing -- python -m pytest -q",
    "saani test --kind tier1 -- python -m pytest -q", "saani test --kind=landing -- python -m pytest -q",
    "saani test -- npm test", "saani test --kind=full -- python -m pytest -q", "saani comment 12 progress",
    "saani comment 12 progress --text 'half done, the token is not here' --field Step=2", "saani kpis", "saani kpis --since 2026-01-01 --json",
    "saani progress show build/run.txt", "saani progress watch build/run.txt --every 60", "saani ui", "saani ui --port 8772", "saani map",
    "saani ci status", "saani ci pr", "saani ci pr --pc", "saani auto pick", "saani auto pick --json --only 3 4 --limit 2", "saani auto status", "saani auto status --check",
    "saani auto report", "saani auto report --pr 3 --post",
    "saani start 12 --slug cart-total --no-comment", "saani land --worktree {work} --no-comment --wait 5",
    "python -I {plugin}/saani.py status", "{plugin}/bin/saani status",
    "cd {work}", "cd {run} && saani start 12", "cd {wt}/12-cart-total/src", "cd ../auto-2026-01-02_0300",
    "ls -la", "cat src/app.py", "head -n 5 README.md", "tail -20 build/log.txt", "grep -n total src/app.py", "grep -e x -e y a.txt",
    "grep -inA3 total src/app.py", "grep -C 2 -w total src/app.py", "grep --color=never -c total a.txt", "grep -- -x a.txt",
    "diff -u a.txt b.txt", "diff --brief a.txt b.txt", "git diff -- src/app.py", "git diff HEAD src", "cd {run}",
    "wc -l src/*.py", "sort names.txt", "sort -rn -k 2 names.txt", "sort --unique names.txt", "find . -name '*.py'", "echo hello",
    "test -f README.md", "[ -d src ]", "[ a = b ]", "pwd", "diff a.txt b.txt",
    "git status", "git diff origin/dev", "git log --oneline -5", "git log --format=%H", "git show HEAD", "git show HEAD:src/app.py", "git add src/app.py", "git add -- src/app.py README.md",
    "git stash list", "saani auto status --check", "saani auto report --pr 3 --post",
    "git commit -m 'Cart: total in cents (refs #12)' -m 'Why: rounding' -m 'Undo: revert'", "git commit --amend --no-edit",
    "git commit -m 'Cart: -f is only text here, as is .env'", "git commit -F msg.txt", "git commit --file=msg.txt -q",
    "git commit -m x -m 'y z'", "git commit --amend -m x", "git commit -s -m x",
    "git commit -m \"$(cat <<'EOF'\nCart: total (refs #12)\n\nWhy: x\nUndo: y\nEOF\n)\"",
    "git commit -F - <<'EOF'\nCart: total (refs #12)\n\nWhy: x\nUndo: y\nEOF",
    "git commit -F - <<'EOF'\nCart: total\nEOF\ngit status",
    "git switch feat/12-cart-total", "git switch -c bug/13-cart-rounding origin/dev", "git checkout -b task/14-docs",
    "git checkout -- src/app.py", "git checkout origin/dev -- src/app.py", "git merge origin/dev", "git merge --no-edit origin/dev",
    "git merge --abort", "git rebase origin/dev", "git rebase --continue", "git fetch origin dev", "git fetch -q origin",
    "git fetch origin main", "git fetch origin feat/12-cart-total", "git switch -c feat/12-x origin/dev", "git checkout -b feat/12-x HEAD",
    "git worktree list --porcelain", "git rev-parse HEAD", "git branch --list 'feat/*'", "git branch --show-current", "git branch -a",
    "git branch", "git ls-files", "git restore src/app.py",
    "git -C {work} status", "git --no-pager log", "git diff --exit-code",
    "python -m pytest -q", "python -m pytest -q tests/test_cart.py", "python -m pytest -q -x -k cart", "python -m pytest -q --tb=short",
    "npm test", "'{python_exe}' -m pytest -q",
    "gh issue view 12", "gh issue view https://github.com/acme/webshop/issues/12", "gh issue list --label bug", "gh issue list -lbug -L5", "gh pr view 3", "gh pr list", "gh pr checks 3",
    "gh pr diff 3", "gh run view 9", "gh run list", "gh run watch 9", "gh api repos/acme/webshop/issues/12",
    "gh api repos/acme/webshop/pulls --jq .title", "gh api -i repos/acme/webshop/pulls", "gh api --paginate repos/acme/webshop/pulls",
    "gh issue comment 12 --body 'On dev: abc123, the token is gone'",
    "gh issue comment 12 --body \"$(cat <<'EOF'\nOn dev\n\nWhy: x\nEOF\n)\"",
    "gh pr create --base main --head dev --title 'Release' --body \"$(cat <<'EOF'\nrefs #12\nEOF\n)\"",
    "gh issue edit 12 --add-label in-dev --remove-label idea", "gh issue create --title 'Cart rounding' --body 'Steps...'",
    "gh pr create --base main --head dev --title 'Release' --body x", "gh pr create -B main -H dev -t Release -b x",
    "gh pr create -Bmain -Hdev -tRelease -bx", "gh pr create --base=main --head=dev -t R -b x",
    "gh pr comment 3 --body done", "gh pr edit 3 --title 'Release 1.2'", "gh issue view 12 -R acme/webshop", "gh issue view 12 -Racme/webshop",
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
    ("saani auto away", "saani command"), ("saani auto away --id r2 --folder {run}", "saani command"),   # #50: never from inside a run
    ("saani test --worktree {main} -- python -m pytest -q", "cd outside the worktrees"),
    ("saani test --worktree {wt} -- python -m pytest -q", "cd outside the worktrees"),
    ("saani test --worktree {wt}/* -- python -m pytest -q", "cd outside the worktrees"),
    ("saani test --worktree={wt}/* -- python -m pytest -q", "cd outside the worktrees"),
    ("saani test --worktree {work}/src -- python -m pytest -q ../../../evil/tests", "cd outside the worktrees"),   # deeper than the top
    ("cd {work}/src && saani test -- python -m pytest -q ../../evil/tests", "test option"),   # checked from the worktree's top, where it runs
    ("saani test --worktree={home} -- python -m pytest -q", "cd outside the worktrees"),
    ("saani test --worktree -- python -m pytest -q", "saani command"), ("saani test --work {work} -- python -m pytest -q", "saani command"),
    ("saani test --worktree {work} -- python -m pytest -q {main}/tests", "test option"),
    ("git push", "git push"), ("git push origin HEAD:dev", "git push"), ("git push --force", "git push"),
    ("git status && git push", "git push"), ("git reset --hard origin/dev", "git reset --hard"), ("git clean -fd", "git clean"),
    ("git branch -D feat/12-cart-total", "git branch -D"), ("git branch --delete --force x", "git branch -D"),
    ("git worktree remove {work}", "git worktree remove"), ("git checkout -f feat/12-x", "force flag"),
    ("git add -f .env", "force flag"), ("git add -Af x", "force flag"), ("git switch --force-create feat/12-x", "force flag"),
    ("git fetch --forc origin", "force flag"),
    ("gh pr merge 3", "gh pr merge"), ("gh release create v1", "gh release"), ("gh repo delete acme/webshop", "gh repo"),
    ("gh secret set X", "gh secret"), ("gh auth token", "gh auth"),
    ("curl -s http://localhost:8771/api/state", "web"), ("curl -X POST http://localhost:8771/api", "web"),
    ("curl https://example.com", "web"), ("curl -H @headers.txt http://localhost:8771/", "web"), ("wget -qO- http://localhost:8771/", "web"),
    ("git stash -u", "git stash"), ("git stash push -u", "git stash"), ("git stash push --include-untracked", "git stash"),
    ("git stash push -a", "git stash"), ("git stash push --all", "git stash"), ("git stash push -p", "git stash"),
    ("git stash push --only-untracked", "git stash"), ("git stash show", "git stash"), ("git stash show -p 'stash@{{0}}'", "git stash"),
    ("git stash apply", "git stash"), ("git stash push src/app.py", "git stash"),
    ("git -C {work} stash pop", "git stash"), ("git stash pop", "git stash"), ("git stash pop -q", "git stash"),      # the owner's stash
    ("git stash", "git stash"), ("git stash push -q -m 'wip'", "git stash"), ("git stash push", "git stash"),        # on top of the owner's
    ("git show stash", "git stash"), ("git show 'stash@{{0}}'", "git stash"), ("git show stash^3", "git stash"),
    ("git show refs/stash", "git stash"), ("git log -p stash", "git stash"), ("git show 'stash@{{0}}^3:notes.txt'", "git stash"),
    ("git show HEAD:../x", "secret"), ("git show HEAD:/etc/passwd", "secret"), ("git show HEAD:config/credentials.yml", "secret"),
    ("git restore .", "unsure"), ("git restore ..", "unsure"), ("git restore src", "unsure"), ("git restore src/", "unsure"),
    ("git checkout -- .", "unsure"), ("git checkout origin/dev -- src", "unsure"),
    ("git restore .gitignore", "write outside the worktrees"), ("git restore CLAUDE.md", "write outside the worktrees"),
    ("git add -A", "not on the list"), ("git add --all", "not on the list"), ("git add -u", "not on the list"),
    ("git add --update", "not on the list"), ("git add -p src/app.py", "not on the list"), ("git add .", "unsure"),
    ("git add src", "unsure"), ("git add src/", "unsure"), ("git add '*.py'", "unsure"), ("git add src/*.py", "unsure"),
    ("git add", "not on the list"), ("git add CLAUDE.md", "write outside the worktrees"),
    ("git add .github/workflows/ci.yml", "write outside the worktrees"), ("git add {main}/a.txt", "write outside the worktrees"),
    ("diff src a.txt", "recursive read"), ("diff a.txt {work}", "recursive read"),
    ("python -m pytest -q {main}/tests", "test option"), ("python -m pytest -q ../../webshop/tests/test_a.py::test_x", "test option"),
    ("saani test -- python -m pytest -q {home}", "test option"),
    ("rm -rf {home}/projects", "rm outside the worktrees"), ("rm -rf {main}/src", "rm outside the worktrees"),
    ("rm -rf {work}", "rm a worktree"), ("rm -rf {wt}", "rm a worktree"), ("rm -rf {run}", "rm a worktree"),
    ("rm -rf {work}/saani", "guard file"), ("rm -rf {work}/.git", "guard file"), ("rm -rf /", "rm outside the worktrees"),
    ("rm -rf {temp}", "guard file"), ("rm -f src/*.tmp", "unsure"), ("rm -rf *", "unsure"),
    ("sudo ls", "sudo"), ("chmod +x x", "not on the list"), ("bash -c ls", "not on the list"), ("node x.js", "not on the list"),
    ("python -c 'print(1)'", "python"), ("python x.py", "python"), ("python saani.py status", "python"),
    ("python -I saani.py status", "python"), ("python -I {work}/saani.py status", "python"), ("./bin/saani status", "not on the list"),
    ("saani merge", "saani command"), ("saani prune", "saani command"), ("saani deploy", "saani command"),
    ("saani projects add x", "saani command"), ("saani ci merged 3", "saani command"), ("saani ci sync", "saani command"),
    ("saani check-msg x", "saani command"), ("saani auto prune", "saani command"),
    ("cd {run} && saani auto on", "saani auto switch"), ("saani auto on", "saani auto switch"), ("saani auto off", "saani auto switch"),
    ("saani auto on --deploy", "saani auto switch"), ("saani auto off --run 2026-01-02_0300", "saani auto switch"),
    ("python -I {plugin}/saani.py auto on", "saani auto switch"),
    ("saani progress run --out x -- ls", "saani command"), ("saani progress run -- python -c 1", "saani command"),
    ("saani progress watch f --out x", "saani command"), ("saani progress show", "saani command"),
    ("saani start 12 --hotfix", "saani command"), ("saani start 12 --hot", "saani command"), ("saani start x", "saani command"),
    ("saani land --work {work}", "saani command"), ("saani land --worktree {main}", "cd outside the worktrees"),
    ("saani land extra", "saani command"), ("saani auto --id x", "saani command"), ("saani auto", "saani command"),
    ("saani kpis --out x", "saani command"), ("saani ui --json=1", "saani command"), ("saani ci pr --head feat/1-x", "saani command"),
    ("saani comment 12", "saani command"), ("saani progress show .env", "secret"), ("saani status -h", "saani command"),
    ("saani test --kind landing --foo -- python -m pytest -q", "saani command"),
    ("saani test --kind land -- python -m pytest -q", "saani command"), ("saani test --kind=land -- python -m pytest -q", "saani command"),
    ("cd {run} ; rm -rf build", "cd outside the worktrees"), ("cd {run} | ls", "cd outside the worktrees"),
    ("cd {run} || ls", "cd outside the worktrees"), ("cd {run} & ls", "cd outside the worktrees"),
    ("cd {run}\nls", "cd outside the worktrees"), ("cd {work}/nope && ls", "cd outside the worktrees"),
    ("ls || cd {run} && ls", "cd outside the worktrees"), ("ls | cd {run} && ls", "cd outside the worktrees"),
    ("grep --rec x .", "recursive read"), ("grep --directories=recurse x .", "recursive read"), ("grep -d recurse x .", "recursive read"),
    ("grep -nr x .", "recursive read"), ("grep --include=*.py x a", "grep option"), ("grep -P x a", "grep option"),
    ("grep -A x y z", "grep option"), ("grep -f .env a.txt", "grep option"), ("grep --file=x a.txt", "grep option"),
    ("grep --color=always x a", "grep option"), ("diff --recursive a b", "recursive read"), ("diff --rec a b", "recursive read"),
    ("diff -N a b", "diff option"), ("diff --to-file=x a", "diff option"),
    ("git diff --no-index a b", "git option"), ("git diff --no-i a b", "git option"), ("git diff -- {main}/x", "read outside the worktrees"),
    ("git diff HEAD {home}", "read outside the worktrees"),
    ("git restore '*.py'", "unsure"), ("git restore src/*.py", "unsure"), ("git restore ':/x'", "unsure"),
    ("git restore --source HEAD~1 {main}/a.txt", "write outside the worktrees"), ("git restore -s HEAD '*.py'", "unsure"),
    ("git restore -p x", "not on the list"), ("git restore --source=HEAD", "not on the list"),
    ("git checkout feat/12-cart-total src/app.py", "not a worker branch"), ("git switch feat/12-cart-total origin/dev", "not a worker branch"),
    ("git checkout -- '*.py'", "unsure"), ("git checkout origin/dev -- 'src/[a]pp.py'", "unsure"),
    ("gh issue view https://github.com/other/repo/issues/1", "another repository"),
    ("gh pr view https://evil.example/acme/webshop/pull/1", "another repository"), ("gh issue view other/repo#1", "another repository"),
    ("gh issue view https://github.com/acme/webshop.evil/issues/1", "another repository"),
    ("gh issue comment https://github.com/acme/webshop/../x/issues/1 --body x", "another repository"),
    ("gh issue edit https://github.com/other/repo/issues/1 --add-label x", "another repository"),
    ("gh pr checks https://github.com/acme/webshop@evil.example/pull/1", "another repository"),
    ("python -m pytest -q @args.txt", "test option"), ("saani test -- python -m pytest -q @args.txt", "test option"),
    ("/usr/bin/python -m pytest -q", "not on the list"), ("./python -m pytest -q", "not on the list"),
    ("{work}/python.exe -m pytest -q", "not on the list"),
    ("saani test -- cat x", "not a test command"), ("saani test --kind landing -- bash -c ls", "not a test command"),
    ("python -m pytest -q --rootdir=/", "test option"), ("python -m pytest -q -c x.ini", "test option"),
    ("python -m pytest -q -o addopts=-pevil", "test option"), ("python -m pytest -q -qp evil", "test option"),
    ("saani test -- python -m pytest -q -p evil", "test option"),
    ("cd {main}", "cd outside the worktrees"), ("cd {home}", "cd outside the worktrees"), ("cd", "cd outside the worktrees"),
    ("cd {wt}", "cd outside the worktrees"), ("cd -", "cd outside the worktrees"), ("cd -P {work}", "cd outside the worktrees"),
    ("cd src*", "cd outside the worktrees"),
    ("cd .. && ls", "cd outside the worktrees"), ("git -C {main} status", "cd outside the worktrees"),
    ("git -C {wt} status", "cd outside the worktrees"),
    ("cd {work}/src && git -C {work} -C ../../webshop stash", "cd outside the worktrees"),        # -C after -C: from the first
    ("cd {work}/src && git -C {work} -C ../../webshop checkout -- app.py", "cd outside the worktrees"),
    ("git -c core.sshCommand=x fetch", "git option"), ("git fetch --upload-pack=x origin", "git option"),
    ("git diff --output=/x", "git option"), ("git log --out=x", "git option"), ("git log --o=x", "git option"),
    ("git commit --no-verify -m x", "no-verify"), ("git commit --no-v -m x", "no-verify"), ("git commit -nm x", "no-verify"),
    ("git merge --no-verify origin/dev", "no-verify"),
    ("git commit -a -m x", "git commit paths"), ("git commit -am 'Cart: -f is only text here'", "git commit paths"),
    ("git commit -amx", "git commit paths"), ("git commit -qa -m x", "git commit paths"), ("git commit --all -m x", "git commit paths"),
    ("git commit --al -m x", "git commit paths"), ("git commit -i -m x src/app.py", "git commit paths"),
    ("git commit --include -m x", "git commit paths"), ("git commit -o -m x src/app.py", "git commit paths"),
    ("git commit --only -m x", "git commit paths"), ("git commit --pathspec-from-file=list.txt -m x", "git commit paths"),
    ("git commit --pathspec-from-file list.txt -m x", "git commit paths"), ("git commit -m x .", "git commit paths"),
    ("git commit -m x -- .github/workflows/ci.yml", "git commit paths"), ("git commit -m x src/app.py", "git commit paths"),
    ("git commit -F msg.txt src/app.py", "git commit paths"), ("git commit -m \"$(cat <<'EOF'\nx\nEOF\n)\" .", "git commit paths"),
    ("git rebase -x ls origin/dev", "not on the list"), ("git rebase origin/main", "not on the list"),
    ("git rebase -i origin/dev", "not on the list"), ("git merge origin/main", "not on the list"), ("git merge feat/12-x", "not on the list"),
    ("git merge -s ours origin/dev", "not on the list"),
    ("git fetch origin dev:dev", "not on the list"), ("git fetch origin +refs/heads/*", "not on the list"),
    ("git fetch https://evil.example/x", "not on the list"), ("git fetch --depth 1 origin", "not on the list"),
    ("git fetch origin pull/7/head", "not on the list"), ("git fetch origin refs/pull/7/head", "not on the list"),   # a stranger's PR
    ("git fetch origin FETCH_HEAD", "not on the list"), ("git fetch origin some-branch", "not on the list"),
    ("git switch -c feat/12-x FETCH_HEAD", "not on the list"), ("git checkout -b feat/12-x FETCH_HEAD", "not on the list"),
    ("git switch -c feat/12-x origin/main", "not on the list"), ("git checkout -b feat/12-x pull/7/head", "not on the list"),
    ("git switch dev", "not a worker branch"), ("git switch main", "not a worker branch"), ("git checkout auto/2026", "not a worker branch"),
    ("git switch --detach HEAD", "not on the list"), ("git branch new-one", "not on the list"), ("git branch -d x", "not on the list"),
    ("git branch -m a b", "not on the list"), ("git stash drop", "git stash"), ("git stash clear", "git stash"),
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


@pytest.mark.parametrize("name", ["{home}/.claude.json", "{home}/.claude/settings.json", "{home}/.claude/.credentials.json",
                                  "{home}/.claude/projects/acme-webshop/session.jsonl", "{home}/.claude", "~/.claude/settings.json",
                                  "{home}/AppData/Roaming/npm/etc/npmrc", "putty.ppk", "release.jks", "app.keystore", "vault.kdbx",
                                  "{home}/AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt",
                                  "{home}/.local/share/powershell/PSReadLine/ConsoleHost_history.txt", "{main}/.git/config",
                                  "{work}/.git./config", "{main}/.git/config:x", ".git/config",
                                  "{home}/.bash_history", "{work}/.python_history", "{home}/.gitconfig", "{home}/.config/git/config",
                                  "{home}/.m2/settings.xml", "{work}/.yarnrc.yml", "{home}/AppData/Roaming/pip/pip.ini",
                                  "{home}/.config/pip/pip.conf", "{home}/.claude.json.backup", "{home}/.npmrc.bak", "{home}/.netrc~"])
def test_more_secrets_are_refused_to_read(ctx, name):
    decision, reason, code, _ = tool(ctx, "Read", file_path=name)
    assert (decision, code) == ("deny", "secret"), (name, reason)


def test_claude_s_memory_and_a_project_s_own_claude_folder_are_no_secret(ctx):
    assert tool(ctx, "Read", file_path="{home}/.claude/projects/acme-webshop/memory/MEMORY.md")[0] == "allow"
    assert tool(ctx, "Read", file_path="{work}/.claude/settings.json")[0] == "allow"
    assert tool(ctx, "Read", file_path="{work}/.git/HEAD")[0] == "allow"
    assert sh(ctx, "cat ~/.claude/settings.json")[2] == "secret"
    assert sh(ctx, "cat {main}/.git/config")[2] == "secret"
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
    assert (decision, code) == ("deny", "secret"), (command, reason)


def test_a_glob_that_would_reach_a_secret_is_refused(ctx):
    work = pathlib.Path(ctx.places["work"])
    (work / "server.key").write_text("x")
    (work / "notes.txt").write_text("x")
    assert sh(ctx, "cat *.txt")[0] == "allow"
    assert sh(ctx, "cat *.key")[2] == "secret"
    assert sh(ctx, "cat server*")[2] == "secret"
    (work / ".env").write_text("x")
    assert sh(ctx, "cat .en?")[2] == "secret"
    assert sh(ctx, "cat src/../.e*")[2] == "secret"
    assert sh(ctx, "cat ?otes.txt")[0] == "allow"


@pytest.mark.parametrize("command", [
    "cat ~/.ss[^x]/id_rs[^x]", "cat ~/.ss?/id_rs?", "cat ~/.ss*/id_*", "cat {home}/.ss?/id_rs?", "head -1 ~/.ss?/id_rs?",
    "gh issue comment 1 --body-file ~/.ss[^x]/id_rs[^x]", "gh issue comment 1 -F ~/.ss?/id_rs?", "git commit -F ~/.ss?/id_rs?",
    "git diff ~/.ss?/id_rs? a.txt", "git diff -- ~/.ss?/id_rs?", "cat {main}/*.py", "cat ../../webshop/*", "wc -l {temp}/*",
    "cat src/[a]pp.py", "cat ~/.ss?/id_rs? && ls"])
def test_a_glob_is_matched_as_bash_reads_it_and_only_inside_the_run_s_places(ctx, command):
    decision, reason, code, _ = sh(ctx, command)
    assert decision == "deny", (command, reason)
    assert code in ("glob outside the worktrees", "unsure", "read outside the worktrees"), (command, code)   # git diff's own rule


def test_a_git_bash_glob_is_read_from_its_windows_path(ctx):
    if os.name != "nt":
        pytest.skip("Git Bash's /c/... is read on Windows only")
    gitbash = re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), ctx.places["home"])
    assert sh(ctx, f"cat {gitbash}/.ss?/id_rs?")[2] == "glob outside the worktrees"
    work = re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), ctx.places["work"])
    (pathlib.Path(ctx.places["work"]) / "notes.txt").write_text("x")
    assert sh(ctx, f"cat {work}/*.txt")[0] == "allow"
    (pathlib.Path(ctx.places["work"]) / "server.key").write_text("x")
    assert sh(ctx, f"cat {work}/*.k?y")[2] == "secret"


def test_a_glob_past_the_guard_s_budget_is_refused(ctx, monkeypatch):
    import time
    big = pathlib.Path(ctx.places["work"]) / "big"
    big.mkdir()
    for k in range(5):
        (big / f"f{k}.txt").write_text("x")
    assert sh(ctx, "cat big/*")[0] == "allow"
    monkeypatch.setattr(guard, "WALK_CAP", 3)
    assert sh(ctx, "cat big/*")[2] == "too big"
    monkeypatch.setattr(guard, "WALK_CAP", 20000)
    monkeypatch.setattr(guard, "BUDGET_SECONDS", 0)
    began = time.monotonic()
    assert sh(ctx, "cat */*/*/*")[2] == "too big"
    assert time.monotonic() - began < 5


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


def test_writing_git_runs_only_in_a_worktree_or_the_run_s_folder(ctx):
    main = guard.Context(ctx.places["main"], wt_dir=ctx.wt_dir, folder=ctx.folder, temp=ctx.temp, main=ctx.main, repo="acme/webshop",
                         tests=ctx.tests)
    main.places = ctx.places
    for write in ("git add a.txt", "git commit -m x", "git commit --amend --no-edit", "git stash", "git stash pop",
                  "git merge origin/dev", "git rebase origin/dev", "git switch feat/1-x", "git checkout -- a.txt", "git restore a.txt",
                  "git cherry-pick abc", "git revert abc", "git tag v1", "git am x.patch", "git apply x.patch"):
        assert sh(main, write)[2] == "git write outside the worktrees", write
    for read in ("git status", "git diff", "git log --oneline", "git show HEAD", "git rev-parse HEAD", "git ls-files",
                 "git branch --show-current", "git branch --list", "git fetch origin", "git worktree list"):
        assert sh(main, read)[0] == "allow", read
    assert sh(main, "git -C {work} add src/app.py")[0] == "allow"                       # -C into a worktree
    assert sh(main, "cd {work} && git add src/app.py")[0] == "allow"


def test_grep_and_rm_refuse_a_tree_too_big_to_look_through(ctx, monkeypatch):
    monkeypatch.setattr(guard, "WALK_CAP", 3)
    big = pathlib.Path(ctx.places["work"]) / "big"
    big.mkdir()
    for k in range(5):
        (big / f"f{k}.txt").write_text("x")
    assert tool(ctx, "Grep", pattern="x", path="{work}/big")[2] == "secret"
    assert tool(ctx, "Grep", pattern="x", path="{work}/big", glob="*.py")[2] == "secret"    # past the cap even with a glob
    assert tool(ctx, "Grep", pattern="x", path="{work}/big/f1.txt")[0] == "allow"
    assert sh(ctx, "rm -rf {work}/big")[2] == "guard file"


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


def test_a_walk_past_the_guard_s_time_budget_is_refused(ctx, monkeypatch):
    (pathlib.Path(ctx.places["work"]) / "src" / "app.py").write_text("x")
    assert tool(ctx, "Grep", pattern="x", path="{work}")[0] == "allow"
    assert sh(ctx, "rm -rf {work}/src")[0] == "allow"
    monkeypatch.setattr(guard, "BUDGET_SECONDS", 0)
    assert tool(ctx, "Grep", pattern="x", path="{work}")[2] == "too big"                 # the walk ran out of time: refused
    assert sh(ctx, "rm -rf {work}/src")[2] == "too big"


def test_one_deadline_bounds_the_whole_decision_not_each_glob(ctx, monkeypatch):
    import time
    work = pathlib.Path(ctx.places["work"])
    for k in range(20):
        (work / f"f{k}.txt").write_text("x")
    words = " ".join(f"f{k}*" for k in range(20))
    assert sh(ctx, f"cat {words}")[0] == "allow"
    clock = [time.monotonic()]

    def tick():                                                                          # every look at the clock: one second later
        clock[0] += 1
        return clock[0]
    monkeypatch.setattr(time, "monotonic", tick)
    assert sh(ctx, f"cat {words}")[2] == "too big"                                        # 20 globs, each well under 8 s, too long together


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
                                     '[tests]\nlanding = ["{python}", "-m", "pytest", "-q"]\n', encoding="utf-8")
    work = tmp_path / "webshop-wt" / "12-cart-total"
    work.mkdir(parents=True)
    (work / ".git").write_text(f"gitdir: {fwd(main / '.git' / 'worktrees' / '12-cart-total')}\n")
    (main / ".git" / "worktrees" / "12-cart-total" / "commondir").write_text("../..\n")
    return main, work


def hook(home, cwd, tool_name, session="s-1", raw=None, event_name="PreToolUse", response=None, **tool_input):
    event = {"session_id": session, "transcript_path": "t", "cwd": str(cwd), "permission_mode": "default", "hook_event_name": event_name,
             "tool_name": tool_name, "tool_input": tool_input}
    if response is not None:
        event["tool_response"] = response
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "guard"], input=raw if raw is not None else json.dumps(event).encode(),
                       capture_output=True, cwd=cwd, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode().strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


AUTO_ON = f"python -I {PLUGIN}/saani.py auto on"


def ran(text, **more):
    """The Bash tool's response to a call that ended normally."""
    return {"stdout": text, "stderr": "", "interrupted": False, "isImage": False, **more}


def marker(run_id, folder):
    return f"auto run {run_id} is on\nsaani auto: on {run_id} folder={guard.norm(folder)}\n"


def run_folder(tmp_path, main, name="auto-run-1"):
    run = tmp_path / "webshop-wt" / name
    run.mkdir()
    (run / ".git").write_text(f"gitdir: {fwd(main / '.git' / 'worktrees' / '12-cart-total')}\n")
    return run


def post(home, cwd, command, response, session="s-1"):
    return hook(home, cwd, "Bash", session=session, event_name="PostToolUse", response=response, command=command)


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
    assert guard.state("s-2")["over"] is True                                            # over, never present mode again
    assert guard.state("s-2")["over"] is True


def test_present_mode_prints_nothing(tmp_path, home):
    main, work = fake_repo(tmp_path)
    assert hook(home, work, "Bash", command="git push --force") is None
    assert hook(home, work, "Write", file_path=str(home / ".bashrc"), content="x") is None
    assert hook(home, work, "Bash", raw=b"not json") is None
    assert hook(home, work, "Bash", session=None, command="git push") is None
    assert not (home / ".saani" / "auto" / guard.REFUSALS).exists()


def test_saani_auto_on_is_asked_then_registered_after_it_ran_and_only_the_owner_ends_it(tmp_path, home):
    main, work = fake_repo(tmp_path)
    run = run_folder(tmp_path, main)
    assert hook(home, run, "Bash", command=AUTO_ON) is None                             # present: the owner's permissions decide
    assert guard.state("s-1") is None
    assert post(home, run, AUTO_ON, ran(marker("run-1", run))) is None                  # PostToolUse prints nothing
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
    for switch in (AUTO_ON, AUTO_ON + " --deploy", "saani auto off", "saani auto off --run run-1"):
        out = hook(home, run, "Bash", command=switch)
        assert out["permissionDecision"] == "deny" and "saani auto off --run" in out["permissionDecisionReason"], switch
    started = guard.state("s-1")["started"]
    post(home, run, AUTO_ON + " --deploy", ran(marker("run-1", run)))                  # never re-armed
    assert guard.state("s-1")["started"] == started and guard.state("s-1")["deploy"] is False
    log = (home / ".saani" / "auto" / guard.REFUSALS).read_text().splitlines()
    rows = [json.loads(line) for line in log]
    assert [(r["tool"], r["reason"], r["word"], r["run"]) for r in rows][:4] == [
        ("Bash", "git push", "git", "run-1"), ("Read", "secret", "", "run-1"), ("Write", "guard file", "", "run-1"),
        ("Bash", "git push", "git", "run-1")]
    assert "secret-words-here" not in "".join(log) and "HEAD:dev" not in "".join(log)
    assert {r["project"] for r in rows} == {guard.norm(main)}
    assert guard.refusals("run-1", project=main)["total"] == 8 and guard.refusals("run-1", project=tmp_path)["total"] == 0
    assert guard.refusals("run-1") == {"total": 8, "by_reason": {"git push": 2, "secret": 1, "guard file": 1, "saani auto switch": 4}}
    assert guard.refusals("other")["total"] == 0
    assert guard.turn_off_run("run-1") == 1 and guard.turn_off_run("run-1") == 0       # the owner, from a terminal
    assert guard.state("s-1") is None
    assert hook(home, work, "Bash", command="git push") is None
    assert guard.refusals()["total"] == 8


@pytest.mark.parametrize("command,response,why", [
    (AUTO_ON, {"stdout": "auto run run-1 is on\n", "interrupted": False}, "no marker"),
    (AUTO_ON, "MARKER", "a plain text response with the marker"),
    (AUTO_ON, {"stdout": "MARKER", "interrupted": True}, "interrupted"),
    (AUTO_ON, {"stdout": "MARKER", "interrupted": False, "exit_code": 1}, "a non-zero exit code"),
    (AUTO_ON, {"stdout": "MARKER" + "MARKER", "interrupted": False}, "the marker twice"),
    (AUTO_ON, {"stdout": "  MARKER", "interrupted": False}, "the marker not at the start of a line"),
    (AUTO_ON, {"stderr": "MARKER", "interrupted": False}, "the marker on stderr"),
    (AUTO_ON + " --id other", {"stdout": "MARKER", "interrupted": False}, "another id than the marker's"),
    (AUTO_ON + " --bogus", {"stdout": "MARKER", "interrupted": False}, "an unknown option"),
    ("cd . && " + AUTO_ON, {"stdout": "MARKER", "interrupted": False}, "a chained command"),
    ("echo 'saani auto: on run-1 folder=x'", {"stdout": "MARKER", "interrupted": False}, "not saani auto on"),
    ("python -I saani.py auto on", {"stdout": "MARKER", "interrupted": False}, "not the plugin's saani.py"),
])
def test_registration_needs_the_whole_command_a_normal_end_and_one_marker(tmp_path, home, command, response, why):
    main, work = fake_repo(tmp_path)
    run = run_folder(tmp_path, main)
    text = marker("run-1", run).split("\n", 1)[1]
    response = response.replace("MARKER", text) if isinstance(response, str) else {
        k: v.replace("MARKER", text) if isinstance(v, str) else v for k, v in response.items()}
    post(home, run, command, response)
    if why == "a plain text response with the marker":
        assert guard.state("s-1")["id"] == "run-1"                                       # a string response is the output itself
    else:
        assert guard.state("s-1") is None, why


def test_registration_needs_a_run_folder_inside_the_worktrees_of_a_saani_project(tmp_path, home):
    main, work = fake_repo(tmp_path)
    run = run_folder(tmp_path, main)
    post(home, run, AUTO_ON, ran(marker("run-1", main)))                                 # the main checkout as the run's folder
    post(home, run, AUTO_ON, ran(marker("run-1", tmp_path / "webshop-wt")))              # the worktrees folder itself
    post(home, run, AUTO_ON, ran(marker("run-1", tmp_path / "webshop-wt" / "nope")))     # a folder that does not exist
    post(home, run, AUTO_ON + " --folder " + fwd(work), ran(marker("run-1", run)))      # --folder and the marker disagree
    post(home, tmp_path, AUTO_ON, ran(marker("run-1", tmp_path)))                        # not a git checkout
    assert guard.state("s-1") is None
    post(home, run, AUTO_ON + " --id run-1 --deploy", ran(marker("run-1", run)))
    assert guard.state("s-1")["deploy"] is True


def test_an_expired_entry_is_over_every_call_refused_until_the_owner_ends_the_run(tmp_path, home):
    main, work = fake_repo(tmp_path)
    old = (datetime.datetime.now() - datetime.timedelta(hours=13)).isoformat()
    guard.turn_on("s-1", "r", main, tmp_path / "webshop-wt", started=old)
    for call in ({"command": "git status"}, {"command": "ls"}):                          # denied, and denied again: never present mode
        out = hook(home, work, "Bash", **call)
        assert out["permissionDecision"] == "deny" and "over" in out["permissionDecisionReason"], call
    assert hook(home, work, "Read", file_path=str(work / "README.md"))["permissionDecision"] == "deny"
    assert not (home / ".saani" / "auto" / "s-1.json").exists() and (home / ".saani" / "auto" / "s-1.over").exists()
    post(home, work, AUTO_ON, ran(marker("r", work)))                                    # never re-armed
    assert guard.state("s-1")["over"] is True
    assert guard.refusals("r")["by_reason"] == {"run over": 3}
    assert guard.turn_off_run("r") == 1                                                  # the owner, from a terminal: the mark goes too
    assert not (home / ".saani" / "auto" / "s-1.over").exists() and guard.state("s-1") is None
    assert hook(home, work, "Bash", command="git status") is None
    guard.turn_on("s-2", "r2", main, tmp_path / "webshop-wt", started=old)
    assert guard.state("s-2")["over"] is True and guard.turn_off("s-2")["over"] is True
    assert guard.state("s-2") is None


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
    (after,) = hooks["hooks"]["PostToolUse"]
    assert after["matcher"] == "Bash" and after["hooks"] == entry["hooks"]
    launcher = (ROOT / "bin" / "saani").read_text()
    assert '"$@"' in launcher and "%*" in (ROOT / "bin" / "saani.cmd").read_text()
    assert "py -3" in launcher and "WindowsApps" in launcher and "sys.exit(sys.version_info" not in launcher
