"""The hotfix path: an urgent fix from the prod tag to production without the integration branch, any issue Type, the Type as the branch
prefix and `hotfix` in the name; `saani land` never lands it on the integration branch. Pure functions only: no git, no gh."""
from saani import core


def test_a_hotfix_branch_keeps_the_type_as_prefix_and_hotfix_starts_the_slug():
    assert core.hotfix_name("bug", 12, "login-loop") == "bug/12-hotfix-login-loop"
    assert core.hotfix_name("task", 7, "restart-gateway") == "task/7-hotfix-restart-gateway"
    assert core.hotfix_name("feat", 3, "hotfix-already") == "feat/3-hotfix-already"     # never hotfix-hotfix
    assert core.hotfix_name("bug", 4, "hotfix") == "bug/4-hotfix"


def test_any_type_can_be_a_hotfix_and_the_landing_knows_one_by_its_name(cfg):
    for name in ("bug/12-hotfix-login-loop", "task/7-hotfix-restart", "feat/3-hotfix", "bug/4-hotfix"):
        assert core.is_hotfix(name), name
        assert cfg.worker_branch.match(name), name                   # the hook and land still treat it as a worker's branch
        assert core.needs_trailers(name), name                       # Why: and Undo: as everywhere
    for name in ("bug/12-login-loop", "task/7-not-a-hotfix-here", "dev", "main", "hotfix/12-x", "longrun/2026-11-22_0044"):
        assert not core.is_hotfix(name), name


def test_an_issue_without_a_type_is_never_blocked_for_a_hotfix(cfg):
    assert core.branch_type(None) is None                            # a worker branch still needs a Type
    assert cfg.hotfix_default_type == "bug"


def test_a_worker_branch_never_looks_like_a_hotfix_even_when_the_title_starts_with_the_word():
    assert core.dev_name("task", 544, "hotfix-procedure") == "task/544-procedure"
    assert core.dev_name("task", 5, "hotfix") == "task/5-work"
    assert core.dev_name("bug", 6, "login-hotfix") == "bug/6-login-hotfix"
    assert not core.is_hotfix(core.dev_name("task", 544, "hotfix-hotfix-x"))


def test_a_hotfix_branch_and_a_worker_branch_of_the_same_issue_never_stand_for_each_other():
    branches = ["dev", "task/544-procedure", "origin/bug/544-hotfix-urgent"]
    assert core.existing_branch(544, branches) == "task/544-procedure"
    assert core.existing_branch(544, branches, hotfix=True) == "bug/544-hotfix-urgent"
    assert core.existing_branch(545, branches, hotfix=True) is None
    assert core.existing_branch(544, ["bug/544-hotfix-urgent"]) is None


def test_production_ahead_of_the_prod_tag_is_a_warning_never_a_refusal():
    assert core.unreleased_warning([]) is None
    assert core.unreleased_warning(["", "  "]) is None
    text = core.unreleased_warning(["abc1234 a merged change", "def5678 another"])
    assert "2 merged commit(s) that are not live yet" in text
    assert "abc1234 a merged change" in text
    assert "... and 3 more" in core.unreleased_warning([f"{i:07d} c" for i in range(13)])


def test_the_hotfix_pull_request_names_the_issue_and_the_way_back(cfg):
    body = core.hotfix_pr_body(42)
    assert "refs #42" in body and "closes" not in body.lower() and "back into" in body
    assert body.rstrip().endswith("(https://claude.com/claude-code)")
