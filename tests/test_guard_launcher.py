"""bin/saani, the guard hook's launcher, run through bash as Claude Code runs it (#63): with no auto run registered anywhere it prints
nothing and starts no Python; with an entry, an over mark, a `saani auto on` waiting for its hook, or a registry it cannot list for sure,
it starts Python and the guard decides as before. The Pythons on the PATH are fakes that note they were started, then run the real one."""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from saani import auto, guard
from guard_helpers import AUTO_ON, ROOT, _event, fake_repo, marker, ran, run_folder


def find_bash():
    """Git Bash on Windows (never WSL's bash.exe in System32), else bash from the PATH; None when there is none."""
    found = shutil.which("bash")
    if os.name != "nt":
        return found
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        return found
    git = shutil.which("git")
    for parent in pathlib.Path(git).resolve().parents if git else ():
        for c in (parent / "bin" / "bash.exe", parent / "usr" / "bin" / "bash.exe"):
            if c.is_file():
                return str(c)
    return None


BASH = find_bash()
pytestmark = [pytest.mark.skipif(BASH is None, reason="no bash"), pytest.mark.usefixtures("home")]
FAKE = "#!/bin/bash\necho started >> \"$SAANI_TEST_MARK\"\n[ \"$1\" = -3 ] && shift\nexec \"$SAANI_TEST_PYTHON\" \"$@\"\n"


def fwd(p):
    return str(p).replace("\\", "/")


@pytest.fixture
def launch(tmp_path, home):
    """Runs bin/saani guard through bash with one hook event: (its output, parsed or None, and whether a Python was started)."""
    fakes = tmp_path / "fakebin"
    fakes.mkdir()
    for name in ("py", "python", "python3"):
        (fakes / name).write_text(FAKE, newline="\n")
        (fakes / name).chmod(0o755)
    (home / ".profile").write_text("", encoding="utf-8")                  # a home lists files: an empty one is not read for sure
    mark = tmp_path / "python-started"

    def run(cwd, event, **env_more):
        if mark.exists():
            mark.unlink()
        env = {**{k: v for k, v in os.environ.items() if k.upper() != "PATH"}, "PATH": str(fakes), "HOME": str(home),
               "USERPROFILE": str(home), "SAANI_TEST_MARK": fwd(mark), "SAANI_TEST_PYTHON": fwd(sys.executable), **env_more}
        # Written by hand, not through communicate(), which hides a broken pipe: the event must be read to its end, as Claude Code
        # writes all of it.
        p = subprocess.Popen([BASH, fwd(ROOT) + "/bin/saani", "guard"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=cwd, env=env)
        try:
            p.stdin.write(json.dumps(event).encode())
            p.stdin.close()
            out, err = p.stdout.read(), p.stderr.read()
            assert p.wait(timeout=60) == 0, err
        finally:
            if p.poll() is None:
                p.kill()
        run.err = err
        out = out.decode().strip()
        return (json.loads(out)["hookSpecificOutput"] if out else None), mark.exists()
    run.fakes = fakes
    return run


def pre(cwd, command, session="s-1"):
    return _event(cwd, "Bash", session, "PreToolUse", None, {"command": command})


def after(cwd, command, response, session="s-1"):
    return _event(cwd, "Bash", session, "PostToolUse", response, {"command": command})


def registry():
    path = pathlib.Path(guard.registry())
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("layout", ["no .saani", "no registry", "only the refusal log"])
def test_no_auto_run_registered_no_python_started_and_no_output(tmp_path, home, launch, layout):
    main, work = fake_repo(tmp_path)
    if layout != "no .saani":
        (home / ".saani").mkdir()
        (home / ".saani" / "projects.toml").write_text("projects = []\n", encoding="utf-8")
    if layout == "only the refusal log":
        (registry() / guard.REFUSALS).write_text("", encoding="utf-8")
    assert launch(work, pre(work, "git push --force")) == (None, False)
    assert launch(work, after(work, "ls", ran("x"))) == (None, False)
    assert launch(work, after(work, AUTO_ON, ran("auto"))) == (None, False)      # no `saani auto on` waiting: nothing to register


FAKE_CAT = "#!/bin/bash\necho cat >> \"$SAANI_TEST_CAT\"\nwhile IFS= read -r -d '' _; do :; done\n"


@pytest.mark.parametrize("drain", ["read -N", "cat", "no cat"])
def test_a_big_event_is_read_to_its_end_on_the_fast_path(tmp_path, launch, drain):
    """Over 64 KB, more than a pipe holds: bin/saani reads it all before it exits, so the writer never meets a closed pipe, and prints
    nothing. bash 5.1 or newer reads with `read -N`; an older one (forced here by SAANI_TEST_DRAIN_CAT) with cat, or bash's byte-wise
    read when there is no cat (the PATH holds only the fake Pythons)."""
    main, work = fake_repo(tmp_path)
    big = _event(work, "Write", "s-1", "PreToolUse", None, {"file_path": str(work / "a.txt"), "content": "x" * 200_000})
    cat_mark = tmp_path / "cat-started"
    if drain == "cat":
        (launch.fakes / "cat").write_text(FAKE_CAT, newline="\n")
        (launch.fakes / "cat").chmod(0o755)
    env = {"SAANI_TEST_CAT": fwd(cat_mark)} if drain == "read -N" else {"SAANI_TEST_CAT": fwd(cat_mark), "SAANI_TEST_DRAIN_CAT": "1"}
    assert launch(work, big, **env) == (None, False)
    assert launch.err == b"" and cat_mark.exists() == (drain == "cat")


@pytest.mark.parametrize("drain", ["read -N", "cat"])
@pytest.mark.parametrize("stdin", ["closed", "empty"])
def test_a_closed_or_empty_stdin_ends_the_fast_path_at_once_and_silently(tmp_path, home, launch, drain, stdin):
    main, work = fake_repo(tmp_path)
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PATH": str(launch.fakes)}
    if drain == "cat":
        env["SAANI_TEST_DRAIN_CAT"] = "1"
    script = '"$BASH" "$1" guard <&-' if stdin == "closed" else '"$BASH" "$1" guard'
    r = subprocess.run([BASH, "-c", script, "bash", fwd(ROOT) + "/bin/saani"], stdin=subprocess.DEVNULL, capture_output=True, cwd=work,
                       env=env, timeout=30)
    assert (r.returncode, r.stdout, r.stderr) == (0, b"", b"")


def test_an_empty_home_starts_python(tmp_path, home, launch):
    """A home folder that lists nothing may be one bash cannot read: Python decides."""
    main, work = fake_repo(tmp_path)
    empty = tmp_path / "empty-home"
    empty.mkdir()
    assert launch(work, pre(work, "git status"), HOME=str(empty), USERPROFILE=str(empty))[1] is True


def test_an_auto_run_registered_the_guard_runs_and_decides_as_before(tmp_path, launch):
    main, work = fake_repo(tmp_path)
    guard.turn_on("s-1", "run-1", main, tmp_path / "webshop-wt")
    out, started = launch(work, pre(work, "git push origin HEAD:dev"))
    assert started and out["permissionDecision"] == "deny" and "saani land" in out["permissionDecisionReason"]
    out, started = launch(work, pre(work, "git status"))
    assert started and out["permissionDecision"] == "allow"
    assert launch(work, pre(work, "git push --force", session="s-2")) == (None, True)     # another session: present, but Python decided


@pytest.mark.parametrize("layout", ["entry", "over mark", "pending mark", "empty registry", "registry is a file", "empty .saani"])
def test_anything_else_in_the_registry_starts_python(tmp_path, home, launch, layout):
    main, work = fake_repo(tmp_path)
    (home / ".saani").mkdir()
    if layout == "entry":
        (registry() / "other-session.json").write_text("{}", encoding="utf-8")
    elif layout == "over mark":
        (registry() / "OTHER-SESSION.OVER").write_text("{}", encoding="utf-8")        # whatever the case of the name
    elif layout == "pending mark":
        (registry() / "run-1.pending").write_text("", encoding="utf-8")
    elif layout == "empty registry":
        registry()
    elif layout == "registry is a file":
        (home / ".saani" / "auto").write_text("", encoding="utf-8")
    assert launch(work, pre(work, "git status"))[1] is True


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="POSIX permissions, not as root")
def test_a_registry_that_cannot_be_listed_starts_python_and_fails_closed(tmp_path, launch):
    main, work = fake_repo(tmp_path)
    reg = registry()
    (reg / guard.REFUSALS).write_text("", encoding="utf-8")
    reg.chmod(0)
    try:
        out, started = launch(work, pre(work, "git status"))
    finally:
        reg.chmod(0o755)
    assert started and out["permissionDecision"] == "deny" and "guard error" in out["permissionDecisionReason"]


@pytest.mark.skipif(os.name != "nt", reason="Windows: a Windows Python's home is USERPROFILE, Git Bash's HOME may differ")
def test_windows_looks_at_home_and_userprofile(tmp_path, home, launch):
    main, work = fake_repo(tmp_path)
    other = tmp_path / "other-home"
    other.mkdir()
    (other / ".profile").write_text("", encoding="utf-8")
    assert launch(work, pre(work, "git status"), HOME=str(other)) == (None, False)
    guard.turn_on("s-1", "run-1", main, tmp_path / "webshop-wt")                    # under USERPROFILE (the `home` fixture) only
    assert launch(work, pre(work, "git status"), HOME=str(other))[1] is True
    assert launch(work, pre(work, "git status"), HOME=str(home), USERPROFILE=str(other))[1] is True


def test_saani_auto_on_still_registers_through_the_launcher(tmp_path, launch):
    """`saani auto on` leaves its .pending mark before its marker line, so the PostToolUse hook of that call starts Python and registers
    the session, even with no other run registered."""
    main, work = fake_repo(tmp_path)
    run = run_folder(tmp_path, main)
    auto.mark_pending("run-1")
    out, started = launch(work, after(work, AUTO_ON, ran(marker("run-1", run))))
    assert started and out is None
    assert guard.state("s-1")["id"] == "run-1"
