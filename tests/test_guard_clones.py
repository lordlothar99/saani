"""saani/guard.py, reference clones ([auto] reference_clones): an upstream project's local clone, read at a tag (fetch its tags, log, show,
diff, ls-tree...), never a checkout or any change to its files; everything outside a listed clone as before."""
import json
import os
import pathlib

import pytest

from saani import guard
from guard_helpers import AUTO_ON, fake_repo, fwd, hook, marker, post, ran, run_folder, sh, tool

pytestmark = pytest.mark.usefixtures("home")                                             # never the machine's real ~/.saani


def make_clone(path):
    """An invented upstream's clone (a .git folder and a few files; no git needed: the guard never runs it)."""
    (path / ".git" / "refs" / "tags").mkdir(parents=True)
    (path / ".git" / "config").write_text('[remote "origin"]\n\turl = https://x-access-token:abc@example.com/upstream-lib.git\n')
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (path / ".git" / "packed-refs").write_text("")
    (path / "src").mkdir()
    (path / "src" / "lib.py").write_text("x")
    (path / "README.md").write_text("x")
    return path


@pytest.fixture
def cctx(ctx, tmp_path):
    """The `ctx` auto run, with ../upstream-lib (beside the main checkout) as its reference clone."""
    clone = make_clone(tmp_path / "upstream-lib")
    c = guard.Context(ctx.cwd, wt_dir=ctx.wt_dir, folder=ctx.folder, temp=ctx.temp, main=ctx.main, repo="acme/webshop",
                      integration="dev", production="main", tests=ctx.tests, clones=[str(clone)])
    c.places = {**ctx.places, "clone": fwd(clone)}
    return c


ALLOWED = [
    "git -C {clone} fetch --tags origin", "git -C {clone} fetch -q --tags origin", "git -C {clone} fetch --quiet --tags origin",
    "git -C {clone} log --oneline v1.0..v2.0", "git -C {clone} log -n 5 v2.0 -- src/lib.py", "git -C {clone} log --stat v1.0...v2.0",
    "git -C {clone} show v2.0:src/lib.py", "git -C {clone} show v2.0", "git -C {clone} show 1a2b3c4d", "git -C {clone} show HEAD~2",
    "git -C {clone} show refs/tags/v2.0:README.md", "git -C {clone} diff v1.0 v2.0", "git -C {clone} diff v1.0 v2.0 -- src/lib.py",
    "git -C {clone} diff --stat v1.0..v2.0", "git -C {clone} status", "git -C {clone} status --short",
    "git -C {clone} rev-parse v2.0", "git -C {clone} rev-parse --show-toplevel", "git -C {clone} tag", "git -C {clone} tag -l",
    "git -C {clone} tag --list 'v1.*'", "git -C {clone} tag -l --sort=-v:refname", "git -C {clone} ls-tree -r --name-only v2.0",
    "git -C {clone} ls-tree v2.0:src", "git -C {clone} ls-tree v2.0 src/lib.py", "git -C {clone}/src log -- lib.py",
    "git -C ../../upstream-lib log v2.0", "git -C {work} -C ../../upstream-lib diff v1.0 v2.0",
    "git -C {wt}/../upstream-lib show v2.0:src/lib.py", "git --no-pager -C {clone} log -p v1.0..v2.0",
    "git -C {clone} fetch --tags origin && git -C {clone} diff v1.0 v2.0 -- src",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_a_reference_clone_is_read_at_a_tag(cctx, command):
    assert sh(cctx, command)[0] == "allow", command


def test_a_clone_s_path_forms_and_tools(cctx):
    assert tool(cctx, "Read", file_path="{clone}/src/lib.py")[0] == "allow"
    assert tool(cctx, "Glob", pattern="**/*.py", path="{clone}")[0] == "allow"
    assert tool(cctx, "Grep", pattern="def", path="{clone}", glob="*.py")[0] == "allow"
    assert tool(cctx, "Grep", pattern="def", path="{clone}/src")[0] == "allow"
    assert tool(cctx, "Grep", pattern="def", path="{clone}/src", glob="*.py")[0] == "allow"
    assert tool(cctx, "Grep", pattern="def", path="{clone}/../webshop")[2] == "grep outside the worktrees"
    if os.name == "nt":
        back = cctx.places["clone"].replace("/", "\\")
        assert sh(cctx, f"git -C '{back}' log v2.0")[0] == "allow"
        assert sh(cctx, "git -C '..\\..\\upstream-lib' log v2.0")[0] == "allow"
        gitbash = "/" + cctx.places["clone"][0].lower() + cctx.places["clone"][2:]
        assert sh(cctx, f"git -C {gitbash} log v2.0")[0] == "allow"
        assert sh(cctx, f"git -C {cctx.places['clone'].upper()} log v2.0")[0] == "allow"      # Windows paths ignore case
    assert sh(cctx, "git -C C:upstream-lib log")[2] == "drive-relative path"


REFUSED = [
    ("git -C {clone} checkout v1.0", "git in a reference clone"), ("git -C {clone} checkout v1.0 -- src/lib.py", "git in a reference clone"),
    ("git -C {clone} switch --detach v1.0", "git in a reference clone"), ("git -C {clone} commit -m x", "git in a reference clone"),
    ("git -C {clone} push", "git in a reference clone"), ("git -C {clone} reset --hard v1.0", "git in a reference clone"),
    ("git -C {clone} reset v1.0", "git in a reference clone"), ("git -C {clone} clean -n", "git in a reference clone"),
    ("git -C {clone} merge v2.0", "git in a reference clone"), ("git -C {clone} rebase v2.0", "git in a reference clone"),
    ("git -C {clone} stash", "git in a reference clone"), ("git -C {clone} stash list", "git in a reference clone"),
    ("git -C {clone} branch topic", "git in a reference clone"), ("git -C {clone} branch -D main", "git in a reference clone"),
    ("git -C {clone} branch", "git in a reference clone"), ("git -C {clone} worktree add ../x v1.0", "git in a reference clone"),
    ("git -C {clone} worktree list", "git in a reference clone"), ("git -C {clone} config user.name x", "git in a reference clone"),
    ("git -C {clone} restore src/lib.py", "git in a reference clone"), ("git -C {clone} add src/lib.py", "git in a reference clone"),
    ("git -C {clone} pull", "git in a reference clone"), ("git -C {clone} apply x.patch", "git in a reference clone"),
    ("git -C {clone} remote add other https://example.com/x.git", "git in a reference clone"),
    ("git -C {clone} update-ref refs/heads/main v1.0", "git in a reference clone"), ("git -C {clone} gc", "git in a reference clone"),
    ("git -C {clone} ls-files", "git in a reference clone"), ("git -C {clone} am x.patch", "git in a reference clone"),
    ("git -C {clone} fetch origin", "not on the list"), ("git -C {clone} fetch --tags", "not on the list"),
    ("git -C {clone} fetch --tags upstream", "not on the list"), ("git -C {clone} fetch --tags origin main", "not on the list"),
    ("git -C {clone} fetch --tags origin v1:refs/heads/x", "not on the list"), ("git -C {clone} fetch --tags origin '+refs/*:refs/*'", "not on the list"),
    ("git -C {clone} fetch --prune --tags origin", "not on the list"), ("git -C {clone} fetch --prune-tags --tags origin", "not on the list"),
    ("git -C {clone} fetch --tags --force origin", "force flag"), ("git -C {clone} fetch -f --tags origin", "force flag"),
    ("git -C {clone} fetch --all --tags", "not on the list"), ("git -C {clone} fetch --upload-pack=x --tags origin", "git option"),
    ("git -C {clone} fetch --depth 1 --tags origin", "not on the list"),
    ("git -C {clone} tag v9.0", "not on the list"), ("git -C {clone} tag -d v1.0", "not on the list"),
    ("git -C {clone} tag -a v9 -m x", "not on the list"), ("git -C {clone} tag -l -d v1.0", "not on the list"),
    ("git -C {clone} tag -f v1.0", "force flag"),
    ("git -C {clone} ls-tree", "not on the list"), ("git -C {clone} ls-tree v2.0:../webshop", "secret"),
    ("git -C {clone} ls-tree v2.0 ../webshop", "read outside the worktrees"), ("git -C {clone} ls-tree 'HEAD@{{1}}'", "revision"),
    ("git -C {clone} ls-tree v2.0:.env", "secret"),
    ("git -C {clone} log --all", "every ref"), ("git -C {clone} log -g", "every ref"), ("git -C {clone} log --tags", "every ref"),
    ("git -C {clone} show 'stash@{{0}}'", "git stash"), ("git -C {clone} show refs/stash", "git stash"),
    ("git -C {clone} show 'HEAD@{{1}}'", "revision"), ("git -C {clone} log 'v1@{{2.days.ago}}'", "revision"),
    ("git -C {clone} show ':/fix'", "every ref"), ("git -C {clone} show v2.0:.env", "secret"),
    ("git -C {clone} show v2.0:config/.env.local", "secret"), ("git -C {clone} show v2.0:../x", "secret"),
    ("git -C {clone} show v2.0:/etc/passwd", "secret"), ("git -C {clone} diff --no-index a b", "git option"),
    ("git -C {clone} diff --output=x v1.0 v2.0", "git option"), ("git -C {clone} log --ext-diff -p", "git option"),
    ("git -C {clone} diff v1.0 v2.0 -- ../webshop/app.py", "read outside the worktrees"),
    ("git -C {clone} diff v1.0 {main}", "read outside the worktrees"), ("git -C {clone} log -- {home}/x", "read outside the worktrees"),
    ("git -C {clone} show v2.0:id_rsa", "secret"), ("git -C {clone} log -- .env", "secret"),
    ("cd {clone}", "cd outside the worktrees"), ("cd {clone} && git log", "cd outside the worktrees"),
    ("cd {clone}/src", "cd outside the worktrees"), ("git -C {clone}/.. log", "cd outside the worktrees"),
    ("git -C {clone} -C .. log", "cd outside the worktrees"), ("git -C {clone}/../webshop log", "cd outside the worktrees"),
    ("git -C {main} log", "cd outside the worktrees"), ("git -C {clone}-other log", "cd outside the worktrees"),
    ("rm {clone}/src/lib.py", "rm outside the worktrees"), ("saani test --worktree {clone} -- python -m pytest -q", "cd outside the worktrees"),
    ("python -m pytest -q {clone}/src", "test option"), ("cat {clone}/.env", "secret"),
]


@pytest.mark.parametrize("command,code", REFUSED)
def test_a_reference_clone_is_never_changed(cctx, command, code):
    verdict = sh(cctx, command)
    assert verdict[0] == "deny" and verdict[2] == code, (command, verdict)


def test_no_write_tool_in_a_clone_and_its_secrets_stay_refused(cctx):
    for name in ("Edit", "Write", "MultiEdit"):
        assert tool(cctx, name, file_path="{clone}/src/lib.py", content="x")[2] == "edit outside the worktrees", name
    assert tool(cctx, "NotebookEdit", notebook_path="{clone}/a.ipynb")[2] == "edit outside the worktrees"
    assert tool(cctx, "Write", file_path="{clone}/.git/hooks/pre-commit", content="x")[2] == "guard file"
    clone = pathlib.Path(cctx.places["clone"])
    (clone / "deploy.pem").write_text("x")
    assert tool(cctx, "Read", file_path="{clone}/deploy.pem")[2] == "secret"
    assert tool(cctx, "Read", file_path="{clone}/.git/config")[2] == "secret"
    assert tool(cctx, "Glob", pattern="**/.env", path="{clone}")[2] == "secret"
    assert tool(cctx, "Grep", pattern="def", path="{clone}/src")[0] == "allow"
    (clone / "src" / "deploy.pem").write_text("x")
    assert tool(cctx, "Grep", pattern="def", path="{clone}/src")[2] == "secret"            # the tree holds a key
    assert tool(cctx, "Grep", pattern="def", path="{clone}", glob="*.py")[0] == "allow"
    assert sh(cctx, "git -C {clone} show v2.0:deploy.pem")[2] == "secret"


@pytest.mark.parametrize("inp", [
    {"path": "{clone}/.git"}, {"path": "{clone}/.git/"}, {"path": "{clone}/.git/refs"}, {"path": "{clone}/.git/config"},
    {"path": "{clone}/.git."}, {"path": "{clone}/.GIT"}, {"path": "{clone}/src/../.git"}, {"path": "{clone}/.git", "glob": "*.py"},
    {"path": "{work}/.git"},                                                             # a worktree's .git file too
    {"path": "{clone}"},                                                                 # unnarrowed: it could read .git/config
    {"path": "{clone}", "glob": "config"}, {"path": "{clone}", "glob": "HEAD"}, {"path": "{clone}", "glob": "*"},
    {"path": "{clone}", "glob": "packed-*"}, {"path": "{clone}", "glob": "**/*.py"}, {"path": "{clone}", "glob": "src/*.py"},
])
def test_grep_never_reads_a_git_folder(cctx, inp):
    """ripgrep's own skipping of hidden folders is not relied on: a .git folder below the root is walked like any folder, and a search
    that could read a file in it is refused (an unnarrowed one, or a glob matching a name there)."""
    if os.name != "nt" and inp["path"].endswith(("/.GIT", "/.git.")):
        pytest.skip("case and trailing dots are Windows'")
    verdict = tool(cctx, "Grep", pattern="https://.*@", **inp)
    assert verdict[0] == "deny" and verdict[2] == "secret", (inp, verdict)


def test_grep_of_a_tree_with_a_git_folder_below_it(cctx):
    vendor = pathlib.Path(cctx.places["work"]) / "vendor" / "dep"
    (vendor / ".git").mkdir(parents=True)
    (vendor / ".git" / "config").write_text("x")
    (vendor / "dep.py").write_text("x")
    assert tool(cctx, "Grep", pattern="x", path="{work}")[2] == "secret"
    assert tool(cctx, "Grep", pattern="x", path="{work}", glob="config")[2] == "secret"
    assert tool(cctx, "Grep", pattern="x", path="{work}", glob="*.py")[0] == "allow"
    assert tool(cctx, "Read", file_path="{clone}/.git/modules/dep/config")[2] == "secret"
    assert tool(cctx, "Read", file_path="{clone}/.git/worktrees/x/config.worktree")[2] == "secret"


def link(src, target):
    """A link `src` -> `target` folder: a symlink, else (Windows without the right) a junction; None when neither can be made."""
    try:
        os.symlink(target, src, target_is_directory=True)
        return src
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(src))
        return src
    except (ImportError, OSError, AttributeError):
        return None


def test_a_link_out_of_the_clone_is_judged_where_it_leads(cctx):
    clone = pathlib.Path(cctx.places["clone"])
    if not link(clone / "out", pathlib.Path(cctx.places["main"])):
        pytest.skip("no symlink or junction can be made here")
    assert sh(cctx, "git -C {clone}/out log")[2] == "cd outside the worktrees"
    assert sh(cctx, "git -C {clone} diff v1.0 -- out/app.py")[2] == "read outside the worktrees"
    assert tool(cctx, "Grep", pattern="x", path="{clone}/out")[2] == "grep outside the worktrees"


def test_a_link_into_the_clone_reads_as_the_clone(cctx, tmp_path):
    into = tmp_path / "temp" / "to-upstream"
    if not link(into, pathlib.Path(cctx.places["clone"])):
        pytest.skip("no symlink or junction can be made here")
    assert sh(cctx, f"git -C {fwd(into)} show v2.0:src/lib.py")[0] == "allow"
    assert sh(cctx, f"git -C {fwd(into)} checkout v1.0")[2] == "git in a reference clone"


def test_without_reference_clones_a_clone_is_outside_as_before(ctx, tmp_path):
    make_clone(tmp_path / "upstream-lib")
    ctx.places["clone"] = fwd(tmp_path / "upstream-lib")
    assert sh(ctx, "git -C {clone} log v2.0")[2] == "cd outside the worktrees"
    assert sh(ctx, "git -C {clone} fetch --tags origin")[2] == "cd outside the worktrees"
    assert tool(ctx, "Grep", pattern="x", path="{clone}")[2] == "grep outside the worktrees"


def test_a_clone_overlapping_the_run_s_places_is_dropped(ctx, tmp_path):
    for bad in (ctx.wt_dir, os.path.join(ctx.wt_dir, "12-cart-total"), ctx.main, os.path.join(ctx.main, "vendor"), str(tmp_path),
                ctx.temp, os.path.join(ctx.temp, "upstream-lib"), ctx.home, "relative/upstream", "//host/share/upstream", 5):
        c = guard.Context(ctx.cwd, wt_dir=ctx.wt_dir, folder=ctx.folder, temp=ctx.temp, main=ctx.main, clones=[bad])
        assert c.clones == [], bad
    c = guard.Context(ctx.cwd, wt_dir=ctx.wt_dir, folder=ctx.folder, temp=ctx.temp, main=ctx.main, clones=[str(tmp_path / "upstream-lib")])
    assert c.clones == [guard.norm(tmp_path / "upstream-lib")]


def real_clone(git, path):
    """An upstream's clone git can read: a repository with an origin remote (never fetched: no network)."""
    path.mkdir()
    git("init", "-q", cwd=path)
    git("remote", "add", "origin", "https://example.com/upstream-lib.git", cwd=path)
    return path


def test_the_clones_are_learned_from_saani_toml_when_the_run_starts(tmp_path, home, monkeypatch, git):
    import tempfile
    (tmp_path / "temp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))                     # pytest's tmp_path is inside the real temp
    clone = real_clone(git, tmp_path / "upstream-lib")
    main, work = fake_repo(tmp_path, toml='[project]\nrepo = "acme/webshop"\n\n[worktrees]\nfolder = "../webshop-wt"\n\n'
                                          '[tests]\nlanding = ["{python}", "-m", "pytest", "-q"]\n\n'
                                          '[auto]\nreference_clones = ["../upstream-lib", "../moved-lib"]\n')
    run = run_folder(tmp_path, main)
    post(home, run, AUTO_ON, ran(marker("run-1", run)))
    st = guard.state("s-1")
    assert [guard.norm(c) for c in st["reference_clones"]] == [guard.norm(clone)]           # the missing one is dropped
    out = hook(home, work, "Bash", command=f"git -C {fwd(clone)} show v2.0:src/lib.py")
    assert out["permissionDecision"] == "allow"
    out = hook(home, work, "Bash", command=f"git -C {fwd(clone)} checkout v1.0")
    assert out["permissionDecision"] == "deny" and "reference clone" in out["permissionDecisionReason"]
    rows = [json.loads(line) for line in (home / ".saani" / "auto" / guard.REFUSALS).read_text().splitlines()]
    assert rows[-1]["reason"] == "git in a reference clone" and rows[-1]["word"] == "git"


def test_an_entry_without_clones_reads_none(tmp_path, home):
    guard.turn_on("s-1", "r", tmp_path / "webshop", tmp_path / "webshop-wt")
    assert guard.state("s-1")["reference_clones"] == []
    assert guard.context(tmp_path, {"wt_dir": str(tmp_path / "webshop-wt")}).clones == []
