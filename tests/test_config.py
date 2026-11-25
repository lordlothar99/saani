"""saani.toml: every table read into the settings, the defaults kept, a typo refused, the shipped example valid. No git, no gh."""
import pathlib
import sys
import tomllib

import pytest

from saani import config

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_shipped_example_is_a_valid_saani_toml():
    data = tomllib.loads((ROOT / "examples" / "saani.toml").read_text(encoding="utf-8"))
    cfg = config.from_toml(data, ROOT)
    assert cfg.repo == "acme/webshop"
    assert (cfg.integration, cfg.production, cfg.prod_tag) == ("dev", "main", "prod")
    assert cfg.trailers == ("Why", "Undo")
    assert "pr-ci" in cfg.ci_jobs


def test_saani_own_settings_are_valid():
    cfg = config.from_toml(tomllib.loads((ROOT / "saani.toml").read_text(encoding="utf-8")), ROOT)
    assert cfg.name == "saani"


def test_an_unknown_setting_is_refused_so_a_typo_is_never_silent():
    with pytest.raises(SystemExit, match=r"unknown setting \[tests\] teir1"):
        config.from_toml({"project": {"repo": "acme/webshop"}, "tests": {"teir1": []}}, ".")


def test_a_setting_outside_a_table_is_refused():
    with pytest.raises(SystemExit, match="must be a table"):
        config.from_toml({"project": {"repo": "acme/webshop"}, "repo": "acme/webshop"}, ".")


def test_auto_keep_on_quiet_minutes_is_read_and_validated():
    base = {"project": {"repo": "acme/webshop"}}
    assert config.from_toml({**base, "auto": {"keep_on_quiet_minutes": 30}}, ".").keep_on_quiet_minutes == 30
    assert config.from_toml({**base, "auto": {"keep_on_quiet_minutes": 2.5}}, ".").keep_on_quiet_minutes == 2.5
    assert config.from_toml(base, ".").keep_on_quiet_minutes == 60
    for bad in (True, 0, -5, "60"):
        with pytest.raises(SystemExit, match="keep_on_quiet_minutes must be a number of minutes"):
            config.from_toml({**base, "auto": {"keep_on_quiet_minutes": bad}}, ".")


def test_auto_reference_clones_are_read_and_validated(tmp_path, git):
    base = {"project": {"repo": "acme/webshop"}, "worktrees": {"folder": "../webshop-wt"}}
    main, upstream = tmp_path / "webshop", tmp_path / "upstream-lib"
    for d in (main, upstream, tmp_path / "no-origin", tmp_path / "webshop-wt" / "lib" / ".git", tmp_path / "plain"):
        d.mkdir(parents=True)
    for d in (main, upstream, tmp_path / "no-origin"):
        git("init", "-q", cwd=d)
    git("remote", "add", "origin", "https://example.com/upstream-lib.git", cwd=upstream)
    git("remote", "add", "origin", "https://example.com/webshop.git", cwd=main)
    cfg = config.from_toml({**base, "auto": {"reference_clones": ["../upstream-lib"]}}, main)
    assert cfg.reference_clones == ("../upstream-lib",) and cfg.reference_clone_paths == [upstream.resolve()]
    assert config.from_toml(base, main).reference_clones == ()
    # A worktree of the project itself, beside it: its .git file leads to the project's own repository.
    git("-c", "user.name=x", "-c", "user.email=x@example.com", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "start", cwd=main)
    git("worktree", "add", "-q", "--detach", str(tmp_path / "own-wt"), cwd=main)
    for bad, why in ((["../plain"], "not a git clone"), (["vendor/lib"], "outside the project"),
                     (["../webshop-wt/lib"], "outside the project"), ([".."], "outside the project"), ([str(upstream)], "relative"),
                     (["C:/upstream-lib"], "relative"), (["/upstream-lib"], "relative"), ("../upstream-lib", "a list of folders"),
                     ([""], "a list of folders"), ([5], "a list of folders"), (["../no-origin"], "no remote.origin.url"),
                     (["../own-wt"], "the project's own repository"), (["../upstream-lib", "../own-wt"], "the project's own repository")):
        with pytest.raises(SystemExit, match=why):
            config.from_toml({**base, "auto": {"reference_clones": bad}}, main)


def test_a_missing_reference_clone_is_a_warning_when_a_run_starts_not_a_refusal(tmp_path, git):
    base = {"project": {"repo": "acme/webshop"}, "worktrees": {"folder": "../webshop-wt"}}
    main, upstream = tmp_path / "webshop", tmp_path / "upstream-lib"
    main.mkdir()
    upstream.mkdir()
    git("init", "-q", cwd=upstream)
    git("remote", "add", "origin", "https://example.com/upstream-lib.git", cwd=upstream)
    cfg = config.from_toml({**base, "auto": {"reference_clones": ["../upstream-lib", "../moved-lib"]}}, main)   # read, no refusal
    here, warning = config.present_reference_clones(cfg)
    assert here == [upstream.resolve()]
    assert "../moved-lib" in warning and "../upstream-lib" not in warning and "goes without it" in warning
    assert config.present_reference_clones(config.from_toml(base, main)) == ([], "")
    with pytest.raises(SystemExit, match="outside the project"):                         # structural errors stay refusals, even missing
        config.from_toml({**base, "auto": {"reference_clones": ["../webshop-wt/nope"]}}, main)


def test_the_old_wait_quiet_minutes_key_is_refused_as_unknown():
    with pytest.raises(SystemExit):
        config.from_toml({"project": {"repo": "acme/webshop"}, "auto": {"wait_quiet_minutes": 30}}, ".")


def test_repo_must_be_owner_slash_name():
    with pytest.raises(SystemExit, match="owner/name"):
        config.from_toml({"project": {"repo": "webshop"}}, ".")


def test_labels_given_in_part_keep_the_other_defaults():
    cfg = config.from_toml({"project": {"repo": "acme/webshop"}, "issues": {"labels": {"in_dev": "on-dev"}}}, ".")
    assert cfg.labels["in_dev"] == "on-dev"
    assert cfg.labels["in_main"] == "in-main"
    assert cfg.labels["idea"] == "idea"


def test_lists_that_must_not_change_become_tuples_and_hints_are_free_text():
    cfg = config.from_toml({"project": {"repo": "acme/webshop"}, "commits": {"trailers": ["Why"]},
                            "land": {"generated": ["docs/SPEC.md"]}, "hints": {"deploy": "run deploy.ps1"}}, ".")
    assert cfg.trailers == ("Why",)
    assert cfg.generated == ("docs/SPEC.md",)
    assert cfg.hints == {"deploy": "run deploy.ps1"}


def test_the_worktrees_sit_beside_the_main_checkout_unless_a_folder_is_set(tmp_path):
    root = tmp_path / "webshop"
    assert config.Config(repo="acme/webshop", root=root).wt_dir == tmp_path / "webshop-wt"
    assert config.Config(repo="acme/webshop", root=root, worktree_folder="../wt").wt_dir == (root / "../wt").resolve()
    absolute = tmp_path / "elsewhere"
    assert config.Config(repo="acme/webshop", root=root, worktree_folder=str(absolute)).wt_dir == absolute


def test_python_in_a_command_is_the_interpreter_that_runs_saani():
    cfg = config.Config(landing=["{python}", "-m", "pytest", "-q"])
    assert cfg.command("landing") == [sys.executable, "-m", "pytest", "-q"]


def test_worker_branches_follow_the_issue_types():
    cfg = config.Config(types={"Task": "task", "Bug": "bug", "Chore": "chore"})
    assert cfg.worker_branch.match("chore/12-bump-deps")
    assert not cfg.worker_branch.match("feat/12-bump-deps")      # not a type of this project
    assert not cfg.worker_branch.match("chore/12-Bump")          # slugs are lower case
    assert cfg.hotfix_branch.match("bug/3-hotfix-login")


def test_load_outside_git_says_so(tmp_path):
    with pytest.raises(SystemExit, match="git checkout"):
        config.load(tmp_path)


def test_board_folders_accepts_only_work_deploy_and_auto():
    """#47: [board] folders is a table of known keys, so a typo or an old key is never silently ignored."""
    ok = config.from_toml({"project": {"repo": "acme/webshop"}, "board": {"folders": {"work": "w", "deploy": "d", "auto": "a"}}}, ".")
    assert ok.folders == {"work": "w", "deploy": "d", "auto": "a"}
    for folders, says in (({"longrun": "x"}, "`longrun` is now `auto`"), ({"wrok": "x"}, "unknown [board] folders key `wrok`"),
                          ({"auto": 3}, "must be a path"), ("C:/work", "must be a table")):
        with pytest.raises(SystemExit) as e:
            config.from_toml({"project": {"repo": "acme/webshop"}, "board": {"folders": folders}}, ".")
        assert says in str(e.value), (folders, str(e.value))


def test_tests_landing_is_the_key_and_the_old_tier1_is_still_read_with_a_message(capsys):
    """#48: [tests] tier1 is now [tests] landing; the old key keeps working and says so, both at once are refused."""
    cmd = ["{python}", "-m", "pytest", "-q"]
    assert config.from_toml({"project": {"repo": "acme/webshop"}, "tests": {"landing": cmd}}, ".").landing == cmd
    assert "tier1" not in capsys.readouterr().err
    config._warned.clear()
    assert config.from_toml({"project": {"repo": "acme/webshop"}, "tests": {"tier1": cmd}}, ".").landing == cmd
    assert "[tests] tier1 is now [tests] landing" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="keep only landing"):
        config.from_toml({"project": {"repo": "acme/webshop"}, "tests": {"tier1": cmd, "landing": cmd}}, ".")
    assert not hasattr(config.Config(), "tier1")
