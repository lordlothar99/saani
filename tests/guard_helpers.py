"""Helpers the guard's test files share (the fixtures `home` and `ctx` are in conftest.py): decisions of a Bash command or another tool, a
fake checkout, and the PreToolUse / PostToolUse hook, run in this process (`hook`) or as the plugin runs it (`hook_cli`)."""
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys

from saani import guard

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = str(ROOT).replace("\\", "/")
AUTO_ON = f"python -I {PLUGIN}/saani.py auto on"


def fwd(p):
    return str(p).replace("\\", "/")


def ROOT_CODE(code):
    """The refusal of a path Git Bash maps outside the drives (/, /usr, /proc): on Windows it is refused first, as a POSIX path."""
    return "posix path" if os.name == "nt" else code


def sh(ctx, command):
    return guard.decide({"tool_name": "Bash", "tool_input": {"command": command.format(**ctx.places)}}, ctx)


def tool(ctx, name, **inp):
    return guard.decide({"tool_name": name, "tool_input": {k: v.format(**ctx.places) if isinstance(v, str) else v
                                                           for k, v in inp.items()}}, ctx)


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


def _event(cwd, tool_name, session, event_name, response, tool_input):
    event = {"session_id": session, "transcript_path": "t", "cwd": str(cwd), "permission_mode": "default", "hook_event_name": event_name,
             "tool_name": tool_name, "tool_input": tool_input}
    if response is not None:
        event["tool_response"] = response
    return event


def _no_hard_exit(code):
    raise AssertionError(f"the guard cut the decision off (hard exit {code}): a slow decision fails the test, it does not end pytest")


def hook(home, cwd, tool_name, session="s-1", raw=None, event_name="PreToolUse", response=None, **tool_input):
    """What `saani guard` prints for one hook event (the `home` fixture has set HOME): the hook's answer, or None when it prints nothing."""
    data = raw if raw is not None else json.dumps(_event(cwd, tool_name, session, event_name, response, tool_input)).encode()
    out = io.StringIO()
    real_exit, guard.hard_exit = guard.hard_exit, _no_hard_exit
    try:
        with contextlib.redirect_stdout(out):
            assert guard.main(data) == 0
    finally:
        guard.hard_exit = real_exit
    text = out.getvalue().strip()
    return json.loads(text)["hookSpecificOutput"] if text else None


def hook_cli(home, cwd, tool_name, session="s-1", raw=None, event_name="PreToolUse", response=None, **tool_input):
    """The same through the command, as the plugin runs it: `python -I saani.py guard` with the event on stdin."""
    data = raw if raw is not None else json.dumps(_event(cwd, tool_name, session, event_name, response, tool_input)).encode()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "guard"], input=data, capture_output=True, cwd=cwd, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode().strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


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
