"""Every test runs with a fixed, invented project's settings (acme/webshop): no saani.toml is read, no real project is touched.

Shared here: `cfg` (every test), `home` and `ctx` (the guard's tests), and the git fixtures `git` (run a git command), `repo_copy` (a small
repository, copied from one made once per run) and `origin_clone` (a bare origin with dev and main and a clone of it, the same way). Helpers
only the guard's tests use are in guard_helpers.py."""
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saani import config, guard, status  # noqa: E402
from guard_helpers import PLUGIN, fwd  # noqa: E402


@pytest.fixture(autouse=True)
def cfg(tmp_path):
    # The invented repo has no GitHub issue statuses unless a test gives it some (saani/status.py): nothing asks GitHub for them.
    status._cache.clear()
    status._cache["acme/webshop"] = None
    return config.use(config.Config(repo="acme/webshop", owner="someone", root=tmp_path / "webshop",
                                    generated=("docs/SPEC.md", "tests/support/browser_groups.json"),
                                    landing=["{python}", "-c", "pass"], full=["{python}", "-c", "pass"],
                                    folders={"work": "C:/work/webshop", "deploy": "C:/work/webshop-main"}))


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home of its own, so the registry (~/.saani/auto) and ~ are the test's, never the machine's."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


@pytest.fixture
def ctx(tmp_path, home):
    """An auto run of acme/webshop (the guard's tests): the main checkout, the worktrees folder with the run's folder and one issue's
    worktree, a temp folder."""
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


# ---------------------------------------------------------------- real, temporary git repositories (no network, no gh)

def run_git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git():
    """`git(*args, cwd=...)`: runs git, fails the test when git fails. Skips the test where git is not installed."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    return run_git


@pytest.fixture(scope="session")
def _git_templates(tmp_path_factory):
    """Made once per run: a repository on `dev` with SPEC.md and app.py in one commit, and a bare origin holding that commit as dev and
    main. Each test copies them (a folder copy, not five git commands)."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    base = tmp_path_factory.mktemp("git-templates")
    repo, origin = base / "repo", base / "origin.git"
    repo.mkdir()
    run_git("init", "-q", "-b", "dev", cwd=repo)
    run_git("config", "user.email", "someone@example.com", cwd=repo)
    run_git("config", "user.name", "Someone", cwd=repo)
    run_git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "SPEC.md").write_text("# Spec\nFROZEN v1\n", encoding="utf-8")
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    run_git("add", ".", cwd=repo)
    run_git("commit", "-q", "-m", "start", cwd=repo)
    run_git("init", "-q", "--bare", "-b", "dev", str(origin), cwd=base)
    run_git("push", "-q", str(origin), "HEAD:dev", "HEAD:main", cwd=repo)
    return repo, origin


@pytest.fixture
def repo_copy(_git_templates, tmp_path):
    """A repository on `dev` (SPEC.md and app.py committed, user configured) at tmp_path/webshop; the project's root is not changed."""
    root = tmp_path / "webshop"
    shutil.copytree(_git_templates[0], root)
    return root


@pytest.fixture
def origin_clone(_git_templates, tmp_path, git):
    """(origin, main): a bare origin with dev and main at tmp_path/origin.git and its clone at tmp_path/webshop (on dev, no user set: a
    test that commits passes -c user.name and -c user.email)."""
    origin, main = tmp_path / "origin.git", tmp_path / "webshop"
    shutil.copytree(_git_templates[1], origin)
    git("clone", "-q", str(origin), str(main), cwd=tmp_path)
    return origin, main
