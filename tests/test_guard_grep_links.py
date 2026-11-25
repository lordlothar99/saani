"""saani/guard.py, Grep's walk below a link (#94): a symlink or a Windows junction below the search root that leads to a .git or a .ssh
folder must not hide it. The tests are marked xfail until the guard judges each walked entry by its resolved path (or refuses a tree that
holds a link leaving the root); the owner applies that patch and removes the marks."""
import os
import pathlib

import pytest

from guard_helpers import tool

pytestmark = pytest.mark.usefixtures("home")                                             # never the machine's real ~/.saani


def make_link(src, target, kind):
    """A `kind` link ("symlink" or "junction") `src` -> `target` folder; skips the test when it cannot be made here."""
    try:
        if kind == "symlink":
            os.symlink(target, src, target_is_directory=True)
        else:
            import _winapi
            _winapi.CreateJunction(str(target), str(src))
    except (OSError, NotImplementedError, ImportError, AttributeError):
        pytest.skip(f"no {kind} can be made here")


@pytest.mark.xfail(reason="#94: the walk judges entries below a link by their unresolved path", strict=True)
@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("folder,name", [(".git", "config"), (".ssh", "known_hosts")])
def test_grep_of_a_tree_with_a_link_to_a_secret_folder(ctx, tmp_path, kind, folder, name):
    target = tmp_path / "elsewhere" / folder
    target.mkdir(parents=True)
    (target / name).write_text("x")
    make_link(pathlib.Path(ctx.places["work"]) / "foo", target, kind)
    assert tool(ctx, "Grep", pattern="x", path="{work}")[2] == "secret", (kind, folder)
    assert tool(ctx, "Grep", pattern="x", path="{work}", glob="*")[2] == "secret", (kind, folder)
    assert tool(ctx, "Grep", pattern="x", path="{work}", glob="**/*")[2] == "secret", (kind, folder)


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_grep_of_a_tree_with_a_link_to_a_plain_folder_is_not_refused_by_the_tests_above(ctx, tmp_path, kind):
    """Control: the link helper works and a link to a plain folder is no secret (a plain glob leaves it out in any case)."""
    target = tmp_path / "elsewhere" / "plain"
    target.mkdir(parents=True)
    (target / "a.py").write_text("x")
    make_link(pathlib.Path(ctx.places["work"]) / "foo", target, kind)
    assert tool(ctx, "Grep", pattern="x", path="{work}", glob="*.py")[0] == "allow", kind
