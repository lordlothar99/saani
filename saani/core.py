"""Saani's worktrees and landing (docs/SAANI.md): one worktree per task, landing on the integration branch one at a time. Standard library
only; pure functions first (tests/test_core.py). The settings come from the project's saani.toml (saani/config.py).

  saani start <issue> [--slug words] [--no-assign] a worktree <wt>/<n>-<slug> on a branch <type>/<n>-<slug> from origin/<integration>
                                                    (type from the issue's GitHub Type; an issue with no Type is refused); build/ made,
                                                    the last timing report copied in, the issue assigned to the owner (the "in progress"
                                                    sign)
  saani start <issue> --hotfix                     an urgent fix: the branch <type>/<n>-hotfix-<slug> from the prod tag (what is live),
                                                    any Type (none: the hotfix default); it warns when production holds merged work that is
                                                    not live yet, it never refuses for that
  saani land [--keep] [--wait MIN] [--dry-run]      from inside a worker's worktree: take the landing lock, merge origin/<integration>,
         [--worktree PATH]                          rebuild the generated files, tier 1, fast-forward into the integration branch, release
                                                    the lock, remove the worktree; a hotfix never lands on the integration branch: tier 1
                                                    against production, the branch pushed and its pull request into production opened
  saani auto pick [--json] [--limit N] [--only N..]   the open issues an auto run may take now, in order (saani/auto.py)
  saani auto on|off|status|report ...               an auto run's record, stop rules and report (saani/auto.py)
  saani auto [--id ID]                              a throwaway worktree auto-<YYYY-MM-DD_HHMM> on auto/<id> from the integration
                                                    branch, for an auto run to work in (never landed)
  saani status                                      the landing lock, the worktrees, the conflicts per landing, the test queue
  saani prune [--dry-run] [--local-only]            delete the merged branches, locally and on GitHub, and the finished auto runs
  saani test [--kind tier1|land|full] -- <cmd...>   run a test command through the test queue (saani/queue.py)
  saani check-msg <file>                            the commit-msg hook: a commit on a worker's branch or the integration branch needs the
                                                    trailers (Why: and Undo: by default)

Landing, step by step:
1. the branch is a worker's, the worktree is clean, every commit of the branch carries the trailers; a frozen file (settings `[land]
   frozen`) whose mark is on the integration branch may not change;
2. the lock: a file in the repository's shared git folder, made atomically; another landing waits for it (--wait, 60 min by default);
   a lock older than `lock_stale_hours` is taken over;
3. `git merge origin/<integration>`; a conflict only in generated files is solved by taking the integration copy, they are rebuilt next;
   any other conflict stops the landing, the lock is released, the worker resolves, commits and lands again;
4. the generated files are rebuilt (settings `[land] rebuild`) and committed if they changed;
5. tier 1 (settings `[tests] tier1`), through the test queue with the landing's priority; red stops the landing;
6. `git push origin HEAD:<integration>`, never forced: on a refusal it merges again and retries, tier 1 again only when the new commits
   touch the branch's files, at most `push_attempts` times;
7. the lock is released, the landing is recorded (build/saani-landings.jsonl of the main checkout), the worktree and its local branch
   are removed (--keep leaves them).
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

from . import comments, config, queue

LOCK_NAME = "saani-landing.lock"
LANDINGS = "saani-landings.jsonl"
TESTS_LOG = "saani-tests.jsonl"
SLUG_WORDS = 4
SLUG_MAX = 40
STOP_WORDS = {"a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "with", "by", "at", "is", "be", "from", "into"}
KEPT_BRANCHES = {"master", "HEAD"}


def cfg():
    return config.current()


# ---------------------------------------------------------------- pure functions

def slug(title, words=SLUG_WORDS, limit=SLUG_MAX):
    """'Saani 2: one worktree per task, landing...' -> 'one-worktree-per-task'. Lower case, ASCII, no stop words, at most `words` words."""
    text = title.split(":", 1)[1] if ":" in title and len(title.split(":", 1)[1].split()) >= 2 else title
    text = text.lower().encode("ascii", "ignore").decode()
    parts = [w for w in re.findall(r"[a-z0-9]+", text) if w not in STOP_WORDS]
    out = "-".join(parts[:words])[:limit].strip("-")
    return out or "work"


def branch_type(issue_type):
    """The branch prefix from the issue's GitHub Type, or None when the issue has no Type the settings know (refused)."""
    return cfg().types.get((issue_type or "").strip())


def branch_name(prefix, number, slug_text):
    return f"{prefix}/{number}-{slug_text}"


def is_hotfix(branch):
    return bool(cfg().hotfix_branch.match(branch or ""))


def dev_name(prefix, number, slug_text):
    """A worker branch's name. Its slug never starts with `hotfix` (an issue titled "Hotfix procedure"), or land would take it for a
    hotfix and open a pull request into production: the leading word is dropped."""
    rest = re.sub(r"^(hotfix-?)+", "", slug_text).strip("-")
    return branch_name(prefix, number, rest or "work")


def hotfix_name(prefix, number, slug_text):
    """`bug/12-hotfix-login-loop`: the Type stays the prefix; `hotfix` starts the slug."""
    rest = slug_text.removeprefix("hotfix").strip("-")
    return branch_name(prefix, number, "hotfix" + (f"-{rest}" if rest else ""))


def existing_branch(number, branches, hotfix=False):
    """A branch already made for this issue (local or on origin), so its prefix is never renamed when the Type changes later. A hotfix
    branch and a worker branch of the same issue never stand for each other."""
    for b in branches:
        name = b.strip().removeprefix("* ").removeprefix("remotes/").removeprefix("origin/")
        m = cfg().worker_branch.match(name)
        if m and int(m.group(2)) == int(number) and is_hotfix(name) == hotfix:
            return name
    return None


def unreleased_warning(lines):
    """The commits of production that are not live (`prod..origin/production`), as a warning for a hotfix, or None. Never a refusal."""
    lines = [line for line in lines if line.strip()]
    if not lines:
        return None
    shown = "\n  ".join(lines[:10]) + (f"\n  ... and {len(lines) - 10} more" if len(lines) > 10 else "")
    return (f"WARNING: {cfg().production} holds {len(lines)} merged commit(s) that are not live yet. Merging this hotfix and deploying "
            f"ships them too: ship them as well, or revert them first.\n  {shown}")


def missing_trailers(message):
    """The trailers the commit message lacks; each must be a line `Name: text` (git trailer form) with some text."""
    lines = message.splitlines()
    return [t for t in cfg().trailers if not any(re.match(rf"^{t}:\s*\S", line) for line in lines)]


def needs_trailers(branch):
    return branch == cfg().integration or bool(cfg().worker_branch.match(branch or ""))


def split_conflicts(paths):
    """(generated, others): the conflicted files the landing solves itself (the integration copy, rebuilt next) and the worker's."""
    gen = cfg().generated
    return [p for p in paths if p in gen], [p for p in paths if p not in gen]


def rerun_tier1(moved, changed):
    """After the integration branch moved under a landing: tier 1 again only when the new commits touch a file this branch changed."""
    return bool((set(moved) & set(changed)) - set(cfg().generated))


def lock_is_stale(info, now, hours=None):
    hours = cfg().lock_stale_hours if hours is None else hours
    try:
        started = datetime.datetime.fromisoformat(info["started"])
    except (KeyError, TypeError, ValueError):
        return True
    return (now - started).total_seconds() > hours * 3600


def landing_summary(rows):
    """Landings, how many met a conflict, and the conflicts per landing."""
    done = [r for r in rows if r.get("result") == "landed"]
    with_conflicts = [r for r in done if r.get("conflicts")]
    total = sum(len(r.get("conflicts") or []) for r in done)
    return {"landed": len(done), "with_conflicts": len(with_conflicts),
            "conflicts_per_landing": round(total / len(done), 2) if done else 0.0,
            "stopped": len([r for r in rows if r.get("result") != "landed"])}


def milestone_warning(info, number):
    """Every issue has a milestone unless it is an idea: a warning, never a refusal. Pure."""
    if info.get("milestone") or cfg().labels.get("idea", "idea") in (info.get("labels") or []):
        return None
    return (f"WARNING: issue #{number} has no milestone: set the one whose result it serves "
            f"(gh issue edit {number} --repo {cfg().repo} --milestone <vX.Y.Z>), or label it {cfg().labels.get('idea', 'idea')}")


def known_worktree(folder, known):
    """`folder` when it is one of this repository's worktrees other than the main checkout (`known`: their paths, main first), else None."""
    def norm(p):
        return os.path.normcase(str(pathlib.Path(p).resolve()))
    target = norm(folder)
    return pathlib.Path(folder) if target in [norm(p) for p in known[1:]] else None


def prunable(merged, in_worktrees):
    """The merged branches that may go: never the integration or production branch, never an auto run's branch, never one checked out in
    a worktree (work may be going on there). Sorted, each once. Pure."""
    keep = KEPT_BRANCHES | {cfg().integration, cfg().production}
    out = set()
    for b in merged:
        name = b.strip().removeprefix("* ").removeprefix("+ ").removeprefix("origin/")
        if name and name not in keep and not name.startswith(RUN_BRANCHES) and name not in in_worktrees and "->" not in name:
            out.add(name)
    return sorted(out)


def hotfix_pr_body(number):
    c = cfg()
    after = c.hints.get("hotfix_after_merge", f"After the merge: deploy from `{c.production}`, then merge `{c.production}` back into "
                                              f"`{c.integration}`.")
    return (f"Hotfix, refs #{number}: from the `{c.prod_tag}` tag into `{c.production}`.\n\n{after}\n\n"
            "\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)")


# ---------------------------------------------------------------- git, gh and files

def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd or cfg().root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() if check else r


def worktrees(cwd=None):
    """[(path, branch)] of every worktree of the repository; the first is the main checkout."""
    out, path = [], None
    for line in git("worktree", "list", "--porcelain", cwd=cwd).splitlines():
        if line.startswith("worktree "):
            path = pathlib.Path(line[9:])
        elif line.startswith("branch ") and path:
            out.append((path, line[7:].removeprefix("refs/heads/")))
            path = None
        elif line == "detached" and path:
            out.append((path, ""))
            path = None
    return out


def main_checkout(cwd=None):
    return worktrees(cwd)[0][0]


def in_wt_dir(path):
    """True when `path` is under the workers' worktree folder."""
    try:
        pathlib.Path(path).resolve().relative_to(cfg().wt_dir.resolve())
        return True
    except ValueError:
        return False


def lock_path(cwd=None):
    common = pathlib.Path(git("rev-parse", "--git-common-dir", cwd=cwd))
    return (common if common.is_absolute() else (cwd or cfg().root) / common) / LOCK_NAME


def take_lock(path, branch, now=None):
    """Make the lock file atomically; True when taken. A stale lock is replaced."""
    now = now or datetime.datetime.now()
    info = {"branch": branch, "pid": os.getpid(), "started": now.isoformat(timespec="seconds")}
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = read_lock(path)
            if held is None:                       # released meanwhile: try again
                continue
            if not held or not lock_is_stale(held, now):   # {} = being written by its holder
                return False
            print(f"the landing lock of {held and held.get('branch')} is stale (over {cfg().lock_stale_hours} h): taken over")
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f)
        return True
    return False


def read_lock(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None if not path.exists() else {}


def release_lock(path, branch):
    held = read_lock(path)
    if held is not None and held.get("branch") == branch:
        path.unlink(missing_ok=True)


def record(row, cwd=None):
    out = main_checkout(cwd) / "build" / LANDINGS
    out.parent.mkdir(exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def latest_timing_report(cwd=None):
    """The newest timing report among every worktree (the first of `[tests] timing_reports`)."""
    names = cfg().timing_reports[:1]
    found = [p / r for p, _ in worktrees(cwd) for r in names if (p / r).is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


# ---------------------------------------------------------------- saani start

def issue_info(number):
    r = subprocess.run(["gh", "api", f"repos/{cfg().repo}/issues/{number}", "--jq",
                        "{title: .title, type: .type.name, state: .state, milestone: .milestone.title, labels: [.labels[].name]}"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"cannot read issue #{number}: {r.stderr.strip()}")
    return json.loads(r.stdout)


def hotfix_base():
    """The prod tag as GitHub has it now (it floats: fetched with --force), else origin/production; and the warning, if any."""
    c = cfg()
    git("fetch", "-q", "origin", c.production)
    got = git("fetch", "-q", "--force", "origin", f"refs/tags/{c.prod_tag}:refs/tags/{c.prod_tag}", check=False)
    if got.returncode != 0:
        print(f"no `{c.prod_tag}` tag on GitHub: the hotfix starts from origin/{c.production}")
        return f"origin/{c.production}", None
    ahead = git("log", "--oneline", f"{c.prod_tag}..origin/{c.production}").splitlines()
    return c.prod_tag, unreleased_warning(ahead)


def cmd_auto(args):
    """A throwaway worktree for an auto run: folder `auto-<YYYY-MM-DD_HHMM>`, branch `auto/<YYYY-MM-DD_HHMM>` from the integration
    branch. The run's coordinator works there, so the owner's own folder is never touched; each issue still gets its own `saani start`."""
    action = getattr(args, "action", None)
    for name, only in (("folder", "on"), ("deploy", "on"), ("check", "status"), ("pr", "report"), ("post", "report"), ("run_id", "off")):
        if getattr(args, name, None) and action != only:
            raise SystemExit(f"saani auto: --{name.removesuffix('_id')} belongs to `saani auto {only}`")
    if action in ("on", "off", "status", "report"):
        if action != "on" and getattr(args, "id", None):
            raise SystemExit(f"saani auto {action}: --id belongs to `saani auto` and `saani auto on`" + ("; `off` takes --run" if action == "off" else ""))
        if getattr(args, "json", False) or getattr(args, "limit", None) is not None or getattr(args, "only", None):
            raise SystemExit("saani auto: --json, --limit and --only belong to `saani auto pick`")
        from . import auto
        return auto.cmd_run(args)
    if action != "pick" and (getattr(args, "json", False) or getattr(args, "limit", None) is not None or getattr(args, "only", None)):
        raise SystemExit("saani auto: --json, --limit and --only belong to `saani auto pick`")
    if action == "pick" and args.id:
        raise SystemExit("saani auto pick: --id belongs to `saani auto` (the run's worktree)")
    if action == "pick":
        from . import auto
        return auto.cmd_pick(args)
    c = cfg()
    stamp = args.id or time.strftime("%Y-%m-%d_%H%M")
    if not re.fullmatch(r"[0-9A-Za-z_-]+", stamp):
        raise SystemExit(f"run id '{stamp}': letters, digits, - and _ only")
    folder, name = c.wt_dir / f"auto-{stamp}", f"auto/{stamp}"
    if folder.exists():
        raise SystemExit(f"{folder} already exists: this run id is taken")
    git("fetch", "-q", "origin", c.integration)
    folder.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--no-track", "-b", name, str(folder), f"origin/{c.integration}")
    (folder / "build").mkdir(exist_ok=True)
    print(f"auto run:  {stamp}")
    print(f"worktree:  {folder}")
    print(f"branch:    {name} (from origin/{c.integration}; never landed; `saani prune` and `saani ci merged` remove it once it is finished)")


def cmd_start(args):
    c = cfg()
    info = issue_info(args.issue)
    git("fetch", "-q", "origin", c.integration)
    base, warning = hotfix_base() if args.hotfix else (f"origin/{c.integration}", None)
    branches = git("branch", "-a", "--format=%(refname:short)").splitlines()
    name = existing_branch(args.issue, branches, hotfix=args.hotfix)
    if not name:
        prefix = branch_type(info.get("type"))
        if not prefix and args.hotfix:
            prefix = c.hotfix_default_type
            print(f"issue #{args.issue} has no Type: the hotfix branch takes '{prefix}/' (set the Type later, the branch keeps its name)")
        if not prefix:
            raise SystemExit(f"issue #{args.issue} has no Type ({', '.join(c.types)}): set it first "
                             f"(gh api -X PATCH repos/{c.repo}/issues/{args.issue} -f type={next(iter(c.types))})")
        words = slug(args.slug or info["title"])
        name = hotfix_name(prefix, args.issue, words) if args.hotfix else dev_name(prefix, args.issue, words)
    folder = c.wt_dir / name.split("/", 1)[1]
    if folder.exists():
        raise SystemExit(f"{folder} already exists: work there, or land or remove it first")
    folder.parent.mkdir(parents=True, exist_ok=True)
    local = name in branches
    remote = f"origin/{name}" in branches
    if local:
        git("worktree", "add", str(folder), name)
    elif remote:
        git("worktree", "add", "-b", name, str(folder), f"origin/{name}")
    else:
        git("worktree", "add", "--no-track", "-b", name, str(folder), base)
    (folder / "build").mkdir(exist_ok=True)
    report = latest_timing_report()
    if report:
        shutil.copy2(report, folder / "build" / report.name)
    if (folder / ".githooks").is_dir():
        git("config", "extensions.worktreeConfig", "true")
        git("config", "--worktree", "core.hooksPath", ".githooks", cwd=folder)
    if not args.no_assign and c.owner:
        subprocess.run(["gh", "issue", "edit", str(args.issue), "--repo", c.repo, "--add-assignee", c.owner], capture_output=True)
    if not args.no_comment:
        comments.try_post(args.issue, "started", {"Branch": name, "Worktree": folder.name})
    print(f"worktree: {folder}")
    print(f"branch:   {name} ({'existing' if local or remote else 'from ' + base})")
    print(f"timing:   {report or 'none found: the tier 1 estimate is rougher'}")
    need = " and ".join(t + ":" for t in c.trailers)
    if args.hotfix:
        print(f"HOTFIX: commits need {need}; `saani land` pushes the branch and opens the pull request into {c.production} "
              f"(never {c.integration})")
    else:
        print(f"commits need the trailers {need} (the hook checks); land with: saani land")
    for note in (warning, milestone_warning(info, args.issue)):
        if note:
            print(note)
    return 0


# ---------------------------------------------------------------- saani land

def rebuild_generated(cwd):
    """Run the rebuild commands, stage the generated files, return the ones that changed (none: restored as git has them)."""
    c = cfg()
    if not c.generated:
        return []
    for command in c.rebuild:
        cmd = [sys.executable if part == "{python}" else part for part in command]
        subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)
    # Staged, so git's line-ending rules apply: a rebuild may write LF in a CRLF checkout, and that alone is no change.
    git("add", "--", *c.generated, cwd=cwd)
    changed = [p for p in git("diff", "--cached", "--name-only", "--", *c.generated, cwd=cwd).splitlines() if p.strip()]
    if not changed:
        restore_generated(cwd)
    return changed


def restore_generated(cwd):
    """The generated files as git has them, so a worktree holds no line-ending-only change (which also blocks its removal)."""
    gen = cfg().generated
    if gen:
        git("reset", "-q", "--", *gen, cwd=cwd, check=False)
        git("checkout", "--", *gen, cwd=cwd, check=False)


def merge_and_rebuild(cwd, base, row, issue):
    """Merge the integration branch (conflicts in generated files solved), rebuild the generated files. None when done, else why not."""
    c = cfg()
    merge = git("merge", "--no-edit", base, cwd=cwd, check=False)
    if merge.returncode != 0:
        conflicted = git("diff", "--name-only", "--diff-filter=U", cwd=cwd).splitlines()
        if not conflicted:
            git("merge", "--abort", cwd=cwd, check=False)
            return f"git merge {base} failed: {(merge.stderr or merge.stdout).strip()}"
        generated, others = split_conflicts(conflicted)
        row["conflicts"] = sorted(set(row["conflicts"]) | set(conflicted))
        row["auto_resolved"] = sorted(set(row["auto_resolved"]) | set(generated))
        for p in generated:
            git("checkout", "--theirs", "--", p, cwd=cwd)
            git("add", "--", p, cwd=cwd)
        if others:
            return ("conflicts to resolve: " + ", ".join(others) +
                    " (the generated files were solved). Resolve, `git commit --no-edit`, then land again")
        git("commit", "--no-edit", cwd=cwd)
    regenerated = rebuild_generated(cwd)
    if regenerated:
        git("add", "--", *c.generated, cwd=cwd)
        trailers = "\n".join(f"{t}: " + {"Why": "generated files are rebuilt by saani land, never by hand.",
                                          "Undo": "revert this commit; the next landing rebuilds them again."}.get(t, "saani land")
                             for t in c.trailers)
        git("commit", "-m", f"Generated files rebuilt at landing (refs #{issue})\n\n{trailers}", cwd=cwd)
        print("rebuilt: " + ", ".join(regenerated))
    return None


def frozen_violation(changed, cwd, base):
    """The frozen file this branch changes while its mark is on the integration branch, or None."""
    for path, mark in cfg().frozen.items():
        if path in changed:
            shown = git("show", f"{base}:{path}", cwd=cwd, check=False)
            if shown.returncode == 0 and mark in shown.stdout[:2000]:
                return path
    return None


def stop(lock, branch, row, message, code=1):
    release_lock(lock, branch)
    row["result"] = "stopped"
    row["why"] = message
    record(row)
    print(f"LANDING STOPPED: {message}")
    return code


def land_hotfix(args, cwd, branch, number):
    """A hotfix never reaches the integration branch: tier 1 against production, the branch pushed, the pull request into production
    opened (or the open one named). No landing lock. CI, merge, deploy and the merge back follow."""
    c = cfg()
    if args.dry_run:
        print(f"would run tier 1 against origin/{c.production}, push {branch} and open its pull request into {c.production}")
        return 0
    if c.tier1 and queued_run("land", [*c.command("tier1"), "--base", f"origin/{c.production}"], cwd) != 0:
        print("HOTFIX STOPPED: tier 1 is red: fix it on the branch, commit, land again")
        return 1
    push = git("push", "-u", "origin", branch, cwd=cwd, check=False)
    if push.returncode != 0:
        print(f"HOTFIX STOPPED: git push refused: {(push.stderr or '').strip()[-300:]}")
        return 1
    found = subprocess.run(["gh", "pr", "list", "--repo", c.repo, "--head", branch, "--base", c.production, "--state", "open",
                            "--json", "url", "--jq", ".[0].url"], capture_output=True, text=True, encoding="utf-8")
    url = found.stdout.strip()
    if not url:
        title = git("log", "-1", "--format=%s", cwd=cwd)
        made = subprocess.run(["gh", "pr", "create", "--repo", c.repo, "--base", c.production, "--head", branch,
                               "--title", f"Hotfix: {title}"[:250], "--body", hotfix_pr_body(number)],
                              capture_output=True, text=True, encoding="utf-8")
        if made.returncode != 0:
            print(f"HOTFIX: the branch is pushed, but the pull request failed: {made.stderr.strip()}")
            return 1
        url = made.stdout.strip().splitlines()[-1]
    print(f"HOTFIX PUSHED: {branch} -> {url}")
    print(f"next: saani ci pr --head {branch}; merge; deploy" + (f" ({c.folders['deploy']})" if c.folders.get("deploy") else "")
          + f"; merge {c.production} back into {c.integration}. The worktree stays until the merge.")
    return 0


def cmd_land(args):
    c = cfg()
    if args.worktree:
        # An auto run cannot change folder: it names the worktree instead, from the main checkout.
        top = git("rev-parse", "--show-toplevel", cwd=pathlib.Path(args.worktree), check=False)
        cwd = known_worktree(top.stdout.strip(), [p for p, _ in worktrees()]) if top.returncode == 0 else None
        if cwd is None:
            raise SystemExit(f"--worktree {args.worktree}: not a worker's worktree of this repository (see saani status)")
    else:
        cwd = pathlib.Path(git("rev-parse", "--show-toplevel", cwd=pathlib.Path.cwd()))
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    m = c.worker_branch.match(branch)
    if not m:
        raise SystemExit(f"'{branch}' is not a worker's branch (<type>/<n>-<slug>): land runs inside a worktree made by saani start")
    if git("status", "--porcelain", "--untracked-files=no", cwd=cwd):
        raise SystemExit("the worktree has uncommitted changes: commit them (with the trailers) or put them aside first")
    hotfix = is_hotfix(branch)
    git("fetch", "-q", "origin", c.production if hotfix else c.integration, cwd=cwd)
    base = f"origin/{c.production if hotfix else c.integration}"
    bad = []
    for sha in git("rev-list", "--no-merges", f"{base}..HEAD", cwd=cwd).split():
        missing = missing_trailers(git("log", "-1", "--format=%B", sha, cwd=cwd))
        if missing:
            bad.append(f"{sha[:8]} {git('log', '-1', '--format=%s', sha, cwd=cwd)[:70]} (no {', '.join(missing)})")
    if bad:
        raise SystemExit("commits without the trailers (reword them, e.g. git rebase with reword):\n  " + "\n  ".join(bad))
    if hotfix:
        return land_hotfix(args, cwd, branch, m.group(2))
    changed = git("diff", "--name-only", f"{base}...HEAD", cwd=cwd).splitlines()
    frozen = frozen_violation(changed, cwd, base)
    if frozen:
        raise SystemExit(f"{frozen} is frozen: the record of a change is its commit message and the issue comment")
    if args.dry_run:
        print(f"would land {branch} ({len(changed)} files) into {c.integration}: lock, merge {base}, "
              f"rebuild {', '.join(c.generated) or 'nothing'}, tier 1, push HEAD:{c.integration}, "
              f"{'keep' if args.keep else 'remove'} the worktree")
        return 0

    lock = lock_path(cwd)
    deadline = time.time() + args.wait * 60
    while not take_lock(lock, branch):
        held = read_lock(lock) or {}
        if time.time() > deadline:
            raise SystemExit(f"the landing lock is held by {held.get('branch')} since {held.get('started')}: try again later")
        print(f"waiting for the landing of {held.get('branch')} (since {held.get('started')})")
        time.sleep(15)
    started = time.time()
    row = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "branch": branch, "issue": int(m.group(2)),
           "conflicts": [], "auto_resolved": []}
    try:
        need_tier1, attempt = True, 0
        while True:
            git("fetch", "-q", "origin", c.integration, cwd=cwd)
            problem = merge_and_rebuild(cwd, base, row, m.group(2))
            if problem:
                return stop(lock, branch, row, problem)
            tested = git("rev-parse", base, cwd=cwd)
            if need_tier1 and c.tier1:
                if queued_run("land", c.command("tier1"), cwd) != 0:
                    return stop(lock, branch, row, "tier 1 is red: fix it on the branch, commit, land again")
            push = git("push", "origin", f"HEAD:{c.integration}", cwd=cwd, check=False)
            if push.returncode == 0:
                break
            attempt += 1
            row["push_retries"] = attempt
            if attempt >= c.push_attempts:
                return stop(lock, branch, row, f"git push into {c.integration} was refused {attempt} times (it keeps moving outside "
                            f"Saani): land again. {(push.stderr or '').strip()[-300:]}")
            git("fetch", "-q", "origin", c.integration, cwd=cwd)
            moved = git("diff", "--name-only", tested, base, cwd=cwd).splitlines()
            need_tier1 = rerun_tier1(moved, changed)
            print(f"{c.integration} moved during the landing ({len(moved)} files): merging again, "
                  + ("tier 1 again (the new commits touch this branch's files)" if need_tier1 else "no file in common: no new tier 1"))
        row["result"], row["seconds"] = "landed", round(time.time() - started)
        row["head"] = git("rev-parse", "--short", "HEAD", cwd=cwd)
        release_lock(lock, branch)
        record(row, cwd)
    except BaseException:
        release_lock(lock, branch)
        raise
    print(f"LANDED: {branch} -> {c.integration} at {row['head']} in {row['seconds']} s"
          + (f"; conflicts: {', '.join(row['conflicts'])}" if row["conflicts"] else ""))
    restore_generated(cwd)                 # tier 1 may rewrite them with other line endings
    if not args.keep:
        home = main_checkout(cwd)
        os.chdir(home)
        gone = git("worktree", "remove", str(cwd), cwd=home, check=False)
        if gone.returncode == 0:
            git("branch", "-D", branch, cwd=home, check=False)
            print(f"removed: {cwd} and the local branch {branch}")
        else:
            print(f"the worktree stays (in use?): git worktree remove {cwd}; git branch -D {branch}")
    from . import auto
    auto.journal(m.group(2), "landed")
    if args.no_comment or not mark_on_dev(m.group(2), row):
        print(f"next: comment the commit on #{m.group(2)} and label it {c.labels.get('in_dev', 'in-dev')}")
    return 0


def landing_fields(row):
    c = cfg()
    tests = "tier 1 green" if c.tier1 else "no tier 1 configured"
    return {"Commit": row["head"], "Tests": f"{tests}, landed in {row['seconds']} s", "Next": f"goes to {c.production} with the next pull request"}


def landing_comment(row):
    """The issue comment of a landing (.github/COMMENT_KINDS.md, `On dev`): the commit, how long it took, where it goes next."""
    return comments.render("on-dev", landing_fields(row))


def mark_on_dev(number, row):
    """Comment the landing on the issue and add its in-dev label (saani/comments.py). True when the comment went out (a failed label
    edit prints its own step); False (nothing said, the caller prints both steps) when gh is missing or refuses: a landing is never undone by its bookkeeping."""
    try:
        comments.post(number, "on-dev", landing_fields(row), labels=False)
    except (SystemExit, OSError):
        return False
    try:
        comments.apply_labels(number, "on-dev")
    except (SystemExit, OSError):
        print(f"next: label #{number} {cfg().labels.get('in_dev', 'in-dev')} (the comment is posted)")
    return True


# ---------------------------------------------------------------- saani prune

def prune_candidates(cwd=None):
    """(local, remote): local branches inside origin/production or origin/integration, remote ones inside origin/production, as of a
    fresh `git fetch --prune`."""
    c = cfg()
    git("fetch", "-q", "--prune", "origin", cwd=cwd)
    held = {b for _, b in worktrees(cwd) if b}
    local = set()
    for base in (f"origin/{c.production}", f"origin/{c.integration}"):
        local |= set(git("branch", "--format=%(refname:short)", "--merged", base, cwd=cwd).splitlines())
    remote = git("branch", "-r", "--format=%(refname:short)", "--merged", f"origin/{c.production}", cwd=cwd).splitlines()
    return prunable(local, held), prunable([r for r in remote if r.startswith("origin/")], held)


RUN_BRANCHES = ("auto/", "longrun/")      # "longrun/" is the old name of "auto/": runs made before the rename are still recognised and removed
AUTO_MAX_HOURS = 12        # the auto run's own stop rule is 10 hours: past 12 a run is over


def auto_started(branch, folder=None):
    """When an auto run began: from its id (`auto/YYYY-MM-DD_HHMM`), else from its folder's creation time; None when neither is known."""
    m = re.fullmatch(r"(?:auto|longrun)/(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})", branch)
    if m:
        return datetime.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}")
    if folder and pathlib.Path(folder).exists():
        return datetime.datetime.fromtimestamp(pathlib.Path(folder).stat().st_ctime)
    return None


def finished_auto_runs(before=None, cwd=None):
    """[(worktree path or None, branch)] of the auto runs that are over: the branch is merged into production or the integration branch, the
    worktree (if any) has nothing uncommitted, and the run started before `before` (default: AUTO_MAX_HOURS ago). An auto run's own
    folder holds no commits (each issue has its own worktree), so only its age tells a finished run from one still working."""
    c = cfg()
    merged = set()
    for base in (f"origin/{c.production}", f"origin/{c.integration}"):
        merged |= set(git("branch", "--format=%(refname:short)", "--merged", base, cwd=cwd).splitlines())
    cutoff = before or datetime.datetime.now() - datetime.timedelta(hours=AUTO_MAX_HOURS)
    held = {b: p for p, b in worktrees(cwd) if b}
    out = []
    for branch in sorted(b for b in merged if b.startswith(RUN_BRANCHES)):
        path = held.get(branch)
        started = auto_started(branch, path)
        if started is None or started >= cutoff:
            continue
        if path and git("status", "--porcelain", cwd=path):
            continue
        out.append((path, branch))
    return out


def remove_auto_runs(found, dry=False, cwd=None):
    """Removes those auto runs: the worktree first (git refuses a dirty one: then the branch stays too), then the branch. Returns the
    number removed."""
    done = 0
    for path, branch in found:
        print(("would remove" if dry else "removing") + f" the auto run {branch}" + (f" and its folder {path}" if path else ""))
        if dry:
            continue
        if path and git("worktree", "remove", str(path), cwd=cwd, check=False).returncode != 0:
            print(f"  kept: git would not remove {path}")
            continue
        git("branch", "-D", branch, cwd=cwd, check=False)
        done += 1
    return done


def cmd_prune(args):
    local, remote = prune_candidates()
    runs = finished_auto_runs()
    if runs:
        remove_auto_runs(runs, args.dry_run)
    if not local and (args.local_only or not remote):
        if not runs:
            print("nothing to prune: every merged branch is already gone")
        return 0
    if local:
        print(("would delete" if args.dry_run else "deleting") + f" {len(local)} local branch(es): {', '.join(local)}")
        if not args.dry_run:
            for b in local:
                git("branch", "-D", b, check=False)      # -D: merged into origin, maybe not into the local copy
    if remote and not args.local_only:
        print(("would delete" if args.dry_run else "deleting") + f" {len(remote)} branch(es) on GitHub: {', '.join(remote)}")
        if not args.dry_run:
            done = git("push", "origin", "--delete", *remote, check=False)
            if done.returncode != 0:
                print(f"GitHub refused: {(done.stderr or '').strip()[-300:]}")
                return 1
    return 0


# ---------------------------------------------------------------- status and the commit-msg hook

def cmd_status(args):
    held = read_lock(lock_path())
    print("landing lock: " + ("free" if held is None else f"held by {held.get('branch')} since {held.get('started')}"))
    for path, branch in worktrees():
        if in_wt_dir(path):
            print(f"  {branch:45} {path}")
    log = main_checkout() / "build" / LANDINGS
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] if log.is_file() else []
    s = landing_summary(rows)
    print(f"landings: {s['landed']}, with conflicts: {s['with_conflicts']}, conflicts per landing: {s['conflicts_per_landing']}, "
          f"stopped: {s['stopped']}")
    try:
        local, remote = prune_candidates()
        runs = finished_auto_runs()
        if local or remote or runs:
            print(f"merged branches waiting: {len(local)} local, {len(remote)} on GitHub, {len(runs)} finished auto run(s) (saani prune)")
    except SystemExit:                                   # offline: status still prints the rest
        print("merged branches: unknown (git fetch failed)")
    folder = lock_path().parent / queue.QUEUE_DIR
    jobs = queue.jobs_in(folder) if folder.is_dir() else []
    busy = queue.read_json(folder / queue.RUNNER) if folder.is_dir() else None
    waiting = queue.order([j for j in jobs if j.get("state") == "waiting"])
    print("test queue: " + (f"{busy.get('kind')} of {pathlib.Path(busy.get('worktree', '')).name} runs" if busy else "idle")
          + (f"; waiting: {', '.join(j['kind'] + ' of ' + pathlib.Path(j['worktree']).name for j in waiting)}" if waiting else ""))
    for kind, k in queue.summary(queue.rows(main_checkout() / "build" / TESTS_LOG)).items():
        print(f"  {kind:6} {k['jobs']} job(s), median wait {k['wait']} s, median run {k['run']} s")
    return 0


def queued_run(kind, command, cwd):
    """Run a test command through the test queue from the worktree `cwd`; its exit code."""
    return queue.run(kind, command, lock_path(cwd).parent, cwd, git("rev-parse", "HEAD", cwd=cwd),
                     main_checkout(cwd) / "build" / TESTS_LOG)


def cmd_test(args):
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("give the command after --, e.g. saani test -- python -m pytest")
    cwd = pathlib.Path(git("rev-parse", "--show-toplevel", cwd=pathlib.Path.cwd()))
    return queued_run(args.kind, command, cwd)


def cmd_check_msg(args):
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=pathlib.Path.cwd(), check=False)
    branch = branch.stdout.strip() if branch.returncode == 0 else ""
    git_dir = pathlib.Path(git("rev-parse", "--git-dir", cwd=pathlib.Path.cwd()))
    if not git_dir.is_absolute():
        git_dir = pathlib.Path.cwd() / git_dir
    if not needs_trailers(branch) or (git_dir / "MERGE_HEAD").exists():
        return 0
    text = pathlib.Path(args.file).read_text(encoding="utf-8", errors="replace")
    message = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    missing = missing_trailers(message)
    if missing:
        print(f"commit refused on {branch}: the message needs the trailers {' and '.join(t + ':' for t in missing)} "
              "(why the change, how to undo it; docs/SAANI.md)", file=sys.stderr)
        return 1
    return 0


def add_parsers(sub):
    """The subcommands of this module, for saani.py."""
    p = sub.add_parser("start", help="a worktree and branch for an issue")
    p.add_argument("issue", type=int)
    p.add_argument("--slug", help="words for the branch name instead of the issue's title")
    p.add_argument("--no-assign", action="store_true", help="do not assign the issue to the owner")
    p.add_argument("--hotfix", action="store_true", help="an urgent fix: from the prod tag, landed as a pull request into production")
    p.add_argument("--no-comment", action="store_true", help="do not post the Started comment on the issue")
    p.set_defaults(run=cmd_start)
    p = sub.add_parser("auto", help="a throwaway worktree (auto-<timestamp>) for an auto run to work in; `auto pick`: the issues to take now")
    p.add_argument("action", nargs="?", choices=["pick", "on", "off", "status", "report"],
                   help="pick: the eligible issues in the run's order, the free slots, why the others are left; on, off, status, report: the run (saani/auto.py)")
    p.add_argument("--id", help="the run's id instead of the date and time (YYYY-MM-DD_HHMM); with `on`, the id of the run")
    p.add_argument("--folder", help="on: the run's folder (default: the current one), inside the worktrees folder")
    p.add_argument("--deploy", action="store_true", help="on: the owner started the run so that it may deploy")
    p.add_argument("--run", dest="run_id", help="off: end this run (its guard entries and the record), from a terminal")
    p.add_argument("--check", action="store_true", help="status: exit 3 when a stop rule is reached")
    p.add_argument("--pr", type=int, help="report: the pull request the report is for (the merge commands name it)")
    p.add_argument("--post", action="store_true", help="report: comment the report on the pull request (--pr)")
    p.add_argument("--json", action="store_true", help="pick: as JSON")
    p.add_argument("--limit", type=int, help="pick: at most this many eligible issues")
    p.add_argument("--only", type=int, nargs="+", metavar="N", help="pick: take only these issues (before the overlap check)")
    p.set_defaults(run=cmd_auto)
    p = sub.add_parser("land", help="land the current worktree's branch into the integration branch")
    p.add_argument("--keep", action="store_true", help="keep the worktree and its branch after landing")
    p.add_argument("--wait", type=float, default=60, help="minutes to wait for the landing lock")
    p.add_argument("--dry-run", action="store_true", help="check and print the plan, change nothing")
    p.add_argument("--worktree", help="land this worktree instead of the current folder's (an auto run cannot change folder)")
    p.add_argument("--no-comment", action="store_true", help="do not comment the landing on the issue or label it in-dev (print the step instead)")
    p.set_defaults(run=cmd_land)
    p = sub.add_parser("status", help="the lock, the worktrees, the conflicts per landing, the test queue")
    p.set_defaults(run=cmd_status)
    p = sub.add_parser("prune", help="delete the merged branches, locally and on GitHub (never the integration or production branch)")
    p.add_argument("--dry-run", action="store_true", help="print what would be deleted")
    p.add_argument("--local-only", action="store_true", help="leave GitHub alone")
    p.set_defaults(run=cmd_prune)
    p = sub.add_parser("test", help="run a test command through the test queue, one job at a time")
    p.add_argument("--kind", choices=sorted(queue.PRIORITY), default="tier1", help="land first, then tier1, then full")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(run=cmd_test)
    p = sub.add_parser("check-msg", help="the commit-msg hook")
    p.add_argument("file")
    p.set_defaults(run=cmd_check_msg)
