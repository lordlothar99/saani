"""Every test runs with a fixed, invented project's settings (acme/webshop): no saani.toml is read, no real project is touched."""
import pathlib
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
