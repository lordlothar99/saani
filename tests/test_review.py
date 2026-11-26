"""saani/review.py: the review cache. Real temporary git repositories, gh faked: no network."""
import pytest

from saani import comments, review


def commit(repo, git, name, text="x\n"):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", f"add {name}", cwd=repo)


def head(repo, git_out=None):
    import subprocess
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def branch(repo_copy, git):
    git("checkout", "-q", "-b", "task/5-cart", cwd=repo_copy)
    commit(repo_copy, git, "a.py", "a\n")
    return repo_copy


def review_comment(sha):
    return comments.render("review", {review.COMMIT: sha, review.DIGEST: "0123456789abcdef"})


def test_no_record_is_the_whole_diff(branch, git):
    commit(branch, git, "b.py")
    p = review.plan(branch, ["### ▶ Started\n| Branch |\n|---|\n| task/5-cart |", "### 🔎 In review"], "dev")
    assert p["mode"] == "whole" and p["since"] is None and "no earlier review" in p["why"]
    assert "a.py" in p["diff"] and "b.py" in p["diff"]


def test_a_record_then_two_new_commits_reviews_only_those(branch, git):
    fields = review.record_fields(branch, "dev")
    assert len(fields[review.DIGEST]) == 16 and head(branch).startswith(fields[review.COMMIT])
    body = review_comment(fields[review.COMMIT])
    commit(branch, git, "b.py")
    commit(branch, git, "c.py")
    p = review.plan(branch, [body], "dev")
    assert p["mode"] == "since" and p["since"] == fields[review.COMMIT]
    assert "b.py" in p["diff"] and "c.py" in p["diff"] and "a.py" not in p["diff"]
    text = review.render(p)
    assert "Review range: since" in text and "Not re-read" in text


def test_the_latest_record_counts(branch, git):
    first = review.record_fields(branch, "dev")[review.COMMIT]
    commit(branch, git, "b.py")
    second = review.record_fields(branch, "dev")[review.COMMIT]
    commit(branch, git, "c.py")
    p = review.plan(branch, [review_comment(first), review_comment(second)], "dev")
    assert p["since"] == second and "c.py" in p["diff"] and "b.py" not in p["diff"]


def test_a_record_whose_commit_is_gone_is_the_whole_diff(branch, git):
    commit(branch, git, "b.py")
    gone = review.record_fields(branch, "dev")[review.COMMIT]
    git("reset", "-q", "--hard", "HEAD~1", cwd=branch)                  # the reviewed commit is no longer in the branch
    commit(branch, git, "d.py")
    p = review.plan(branch, [review_comment(gone)], "dev")
    assert p["mode"] == "whole" and "no longer in the branch" in p["why"]
    assert "a.py" in p["diff"] and "d.py" in p["diff"]
    p = review.plan(branch, [review_comment("deadbeefdead")], "dev")      # a commit that never existed
    assert p["mode"] == "whole"


def test_nothing_new_since_the_review(branch):
    sha = review.record_fields(branch, "dev")[review.COMMIT]
    p = review.plan(branch, [review_comment(sha)], "dev")
    assert p["mode"] == "none" and p["diff"] == ""


def test_guard_landing_ci_and_deploy_code_is_always_read_whole(branch, git):
    commit(branch, git, "saani/guard.py", "g = 1\n")
    sha = review.record_fields(branch, "dev")[review.COMMIT]
    commit(branch, git, "z.py")
    p = review.plan(branch, [review_comment(sha)], "dev")
    assert p["mode"] == "since" and "guard.py" not in p["diff"]
    assert p["whole"] == ["saani/guard.py"] and "g = 1" in p["whole_diff"]
    assert "Always reviewed whole" in review.render(p)
    for path in (".github/workflows/ci.yml", "scripts/deploy.sh", "src/landing_page.py"):
        assert review.ALWAYS_WHOLE.search(path)
    assert not review.ALWAYS_WHOLE.search("src/cart.py")


def test_a_review_comment_records_the_commit_and_the_diff_hash(branch, monkeypatch):
    monkeypatch.setattr(review, "base_ref", lambda: "dev")
    fields = comments.review_fields({"Who": "reviewer"}, 5, None)                   # found by the issue's number
    assert list(fields) == [review.COMMIT, review.DIGEST, "Who"]
    assert review.last_record([comments.render("review", fields)]) == fields[review.COMMIT]
    assert review.worktree_of(5) == branch                              # found by the issue's number
    assert comments.review_fields({}, 6, None) == {}                    # no worktree of the issue: a comment without a record
