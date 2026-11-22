"""Every test runs with a fixed, invented project's settings (acme/webshop): no saani.toml is read, no real project is touched."""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saani import config  # noqa: E402


@pytest.fixture(autouse=True)
def cfg(tmp_path):
    return config.use(config.Config(repo="acme/webshop", owner="someone", root=tmp_path / "webshop",
                                    generated=("docs/SPEC.md", "tests/support/browser_groups.json"),
                                    tier1=["{python}", "-c", "pass"], full=["{python}", "-c", "pass"],
                                    folders={"work": "C:/work/webshop", "deploy": "C:/work/webshop-main"}))
