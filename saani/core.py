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
         [--worktree PATH]                          rebuild the generated files, the landing tests, fast-forward into the integration
                                                    branch, release the lock, remove the worktree; a hotfix never lands on the integration
                                                    branch: the landing tests against production, the branch pushed and its pull request
                                                    into production opened
  saani auto pick [--json] [--limit N] [--only N..]   the open issues an auto run may take now, in order (saani/auto.py)
  saani auto on|off|status|report ...               an auto run's record, stop rules and report (saani/auto.py)
  saani auto [--id ID]                              a throwaway worktree auto-<YYYY-MM-DD_HHMM> on auto/<id> from the integration
                                                    branch, for an auto run to work in (never landed)
  saani status [--stats]                            what runs, is blocked or waits in Saani's machinery (landing lock, test queue, auto run,
                                                    tests started outside the queue), or that it is idle; --stats: the landing counts,
                                                    the merged branches waiting, the test queue's medians (saani/activity.py)
  saani prune [--dry-run] [--local-only]            delete the merged branches, locally and on GitHub, and the finished auto runs
  saani test [--kind landing|full] [--worktree P] [--fresh] -- <cmd...> run a test command through the test queue (saani/queue.py)
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
5. the landing tests (settings `[tests] landing`), through the test queue with the landing's priority (kind `land`); red stops the
   landing;
6. `git push origin HEAD:<integration>`, never forced: on a refusal it merges again and retries, the landing tests again only when the
   new commits touch the branch's files, at most `push_attempts` times;
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

from . import activity, comments, config, queue, status

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


def rerun_landing(moved, changed):
    """After the integration branch moved under a landing: the landing tests again only when the new commits touch a file this branch
    changed."""
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
    return pathlib.Path(folder) if any(same_path(folder, p) for p in known[1:]) else None


def same_path(a, b):
    """True when two paths are the same place: resolved, so the spelling (slashes, letter case on Windows) does not matter."""
    return os.path.normcase(str(pathlib.Path(a).resolve())) == os.path.normcase(str(pathlib.Path(b).resolve()))


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
    for name, only in (("folder", ("on", "away", "watch")), ("deploy", ("on", "away", "watch")), ("here", ("on",)), ("check", ("status",)),
                       ("pr", ("report",)), ("post", ("report",)), ("reason", ("report",)), ("run_id", ("off",)), ("wait", ("pick",)), ("keep_on", ("away", "watch"))):
        given = getattr(args, name, None)
        if given is not None and given is not False and action not in only:
            raise SystemExit(f"saani auto: --{name.removesuffix('_id').replace('_', '-')} belongs to "
                             + " and ".join(f"`saani auto {o}`" for o in only)
                             + (" (the run's mode is --keep-on; [auto] keep_on_quiet_minutes sets its quiet time)" if name == "wait" else ""))
    if action in ("on", "off", "status", "report", "end", "away", "watch"):
        if action not in ("on", "away", "watch") and getattr(args, "id", None):
            raise SystemExit(f"saani auto {action}: --id belongs to `saani auto`, `saani auto on` and `saani auto away`" + ("; `off` takes --run" if action == "off" else ""))
        if (getattr(args, "json", False) and action != "end") or getattr(args, "limit", None) is not None or (getattr(args, "only", None) and action not in ("away", "watch")):
            raise SystemExit("saani auto: --json and --limit belong to `saani auto pick`, --only to `pick` and `away`")
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
    print(f"branch:    {name} (from origin/{c.integration}; never landed; `saani prune` removes it once the run is off, it started more than 2 hours ago and it has no commit of its own, `saani ci merged` once it is merged)")


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
    if args.no_comment or not comments.try_post(args.issue, "started", {"Branch": name, "Worktree": folder.name}):
        status.advance(args.issue, "started")              # the comment sets it; without one (skipped or refused), set it here
        from . import auto
        auto.journal(args.issue, "started")                # the comment journals it itself; inside a run this marks the issue as the run's (#56)
    print(f"worktree: {folder}")
    print(f"branch:   {name} ({'existing' if local or remote else 'from ' + base})")
    print(f"timing:   {report or 'none found: the landing tests estimate is rougher'}")
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
    """A hotfix never reaches the integration branch: the landing tests against production, the branch pushed, the pull request into
    production opened (or the open one named). No landing lock. CI, merge, deploy and the merge back follow."""
    c = cfg()
    if args.dry_run:
        print(f"would run the landing tests against origin/{c.production}, push {branch} and open its pull request into {c.production}")
        return 0
    if c.landing and queued_run("land", [*c.command("landing"), "--base", f"origin/{c.production}"], cwd) != 0:
        print("HOTFIX STOPPED: the landing tests are red: fix it on the branch, commit, land again")
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


def named_worktree(path):
    """The checkout `--worktree PATH` names, or the current folder's when PATH is empty. A worktree is named instead of entered because a
    `cd` does not last: Claude Code undoes it in a subagent, and asks before `cd <folder> && <command>`."""
    if not path:
        return pathlib.Path(git("rev-parse", "--show-toplevel", cwd=pathlib.Path.cwd()))
    top = git("rev-parse", "--show-toplevel", cwd=pathlib.Path(path), check=False) if pathlib.Path(path).is_dir() else None
    cwd = known_worktree(top.stdout.strip(), [p for p, _ in worktrees()]) if top is not None and top.returncode == 0 else None
    if cwd is None:
        raise SystemExit(f"--worktree {path}: not a worktree of this repository (see saani status)")
    return cwd


def cmd_land(args):
    from . import auto                     # up front: the worktree's own files may be gone by the end of the landing
    c = cfg()
    cwd = named_worktree(args.worktree)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    m = c.worker_branch.match(branch)
    if not m:
        raise SystemExit(f"'{branch}' is not a worker's branch (<type>/<n>-<slug>): land runs inside a worktree made by saani start")
    if git("status", "--porcelain", "--untracked-files=no", cwd=cwd):
        raise SystemExit("the worktree has uncommitted changes: commit them (with the trailers) or put them aside first")
    hotfix = is_hotfix(branch)
    git("fetch", "-q", "origin", c.production if hotfix else c.integration, cwd=cwd)
    base = f"origin/{c.production if hotfix else c.integration}"
    # Commits already on production are exempt: history there is never rewritten, so a change made on it (no trailers) must be able
    # to come back to the integration branch. A hotfix is based on production, so there its range already leaves them out.
    prod = f"origin/{c.production}"
    if not hotfix and git("fetch", "-q", "origin", c.production, cwd=cwd, check=False).returncode == 0:
        own = git("rev-list", "--no-merges", f"{base}..HEAD", "--not", prod, cwd=cwd).split()
    else:
        if not hotfix:
            print(f"warning: could not fetch origin/{c.production}: every commit not on {c.integration} needs the trailers, a commit "
                  f"that came from {c.production} included")
        own = git("rev-list", "--no-merges", f"{base}..HEAD", cwd=cwd).split()
    bad = []
    for sha in own:
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
              f"rebuild {', '.join(c.generated) or 'nothing'}, landing tests, push HEAD:{c.integration}, "
              f"{'keep' if args.keep else 'remove'} the worktree")
        status.advance(m.group(2), "review", comments.Direct(True))
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
        need_tests, attempt = True, 0
        while True:
            git("fetch", "-q", "origin", c.integration, cwd=cwd)
            problem = merge_and_rebuild(cwd, base, row, m.group(2))
            if problem:
                return stop(lock, branch, row, problem)
            tested = git("rev-parse", base, cwd=cwd)
            if need_tests and c.landing:
                if queued_run("land", c.command("landing"), cwd) != 0:
                    return stop(lock, branch, row, "the landing tests are red: fix it on the branch, commit, land again")
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
            need_tests = rerun_landing(moved, changed)
            print(f"{c.integration} moved during the landing ({len(moved)} files): merging again, "
                  + ("the landing tests again (the new commits touch this branch's files)" if need_tests
                     else "no file in common: no new landing tests"))
        row["result"], row["seconds"] = "landed", round(time.time() - started)
        row["head"] = git("rev-parse", "--short", "HEAD", cwd=cwd)
        release_lock(lock, branch)
        record(row, cwd)
    except BaseException:
        release_lock(lock, branch)
        raise
    print(f"LANDED: {branch} -> {c.integration} at {row['head']} in {row['seconds']} s"
          + (f"; conflicts: {', '.join(row['conflicts'])}" if row["conflicts"] else ""))
    # Every step after the push runs before the worktree goes (its own saani files are deleted with it), and each one on its own:
    # a failure prints the step it left undone, never skips the others.
    restore_generated(cwd)                 # the landing tests may rewrite them with other line endings
    n = m.group(2)
    try:
        auto.journal(n, "landed")
    except (Exception, SystemExit) as e:   # noqa: BLE001 (git() exits on a failed command)
        print(f"next: journal the landing of #{n} (saani auto journal failed: {e})")
    why = ""
    try:
        commented = (not args.no_comment) and mark_on_dev(n, row)
    except (Exception, SystemExit) as e:   # noqa: BLE001
        commented, why = False, f" ({e})"
    if not commented:
        print(f"next: comment the commit on #{n} and label it {c.labels.get('in_dev', 'in-dev')}{why}")
        status.advance(n, "review")             # the comment sets it; without one (--no-comment, or refused), set it here
    if not args.keep:
        try:
            remove_landed_worktree(cwd, branch)
        except (Exception, SystemExit) as e:   # noqa: BLE001
            print(f"next: remove the worktree: git worktree remove {cwd}; git branch -D {branch} ({e})")
    return 0


def force_remove_folder(path, tries=6, pause=1.0):
    """Deletes a folder git left behind after unregistering its worktree: read-only files are made writable, and a lock that is only
    transient (a process still closing a file, a scanner) gets a few seconds. True when the folder is gone."""
    def writable(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    folder = pathlib.Path(path)
    for attempt in range(tries):
        if attempt:
            time.sleep(pause)
        if not folder.exists():
            return True
        if sys.version_info >= (3, 12):
            shutil.rmtree(folder, onexc=writable)
        else:
            shutil.rmtree(folder, onerror=writable)
        if not folder.exists():
            return True
    return not folder.exists()


def remove_landed_worktree(cwd, branch):
    """Removes a landed worktree and its local branch, and says what is actually left when git fails: still registered (it stays, the
    branch too) or unregistered with a folder left behind (the branch goes, the owner removes the folder)."""
    home = main_checkout(cwd)
    os.chdir(home)
    gone = git("worktree", "remove", str(cwd), cwd=home, check=False)
    if gone.returncode == 0:
        git("branch", "-D", branch, cwd=home, check=False)
        print(f"removed: {cwd} and the local branch {branch}")
        return
    if any(same_path(p, cwd) for p, _ in worktrees(home)):             # still registered
        print(f"the worktree stays (in use?): git worktree remove {cwd}; git branch -D {branch}")
        return
    git("branch", "-D", branch, cwd=home, check=False)
    if not same_path(cwd, home) and force_remove_folder(cwd):          # git unregistered it but kept the folder: finish the job
        print(f"removed: {cwd} and the local branch {branch} (the folder git left behind)")
        return
    quoted = str(cwd).replace("'", "''")
    exists = pathlib.Path(cwd).exists()
    left = f"the folder {cwd} is left" if exists else "nothing is left"
    print(f"the worktree is unregistered but git could not delete its folder: the local branch {branch} is deleted; {left}"
          + (f", remove it yourself (e.g. Remove-Item -LiteralPath '{quoted}' -Recurse -Force)" if exists else ""))


def landing_fields(row):
    c = cfg()
    tests = "landing tests green" if c.landing else "no landing tests configured"
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
        if path and (not pathlib.Path(path).is_dir() or git("status", "--porcelain", cwd=path)):
            continue
        out.append((path, branch))
    return out


AUTO_GRACE_HOURS = 2       # a run younger than this is never pruned as unused: `saani auto` made its folder, `saani auto on` may not have run yet


def _same_path_key(p):
    """A path as a comparison key: normalised, forward slashes, no trailing slash, case-folded on Windows."""
    return os.path.normcase(os.path.normpath(str(p))).replace("\\", "/").rstrip("/")


def unused_auto_runs(cwd=None):
    """(found, kept): the auto runs (`auto/<id>`, with or without a worktree) of which the run is off (no run record, or the record is for
    another id) and the branch has no commit beyond the integration branch it came from, with nothing uncommitted: `found` is
    [(worktree path or None, branch)] as `finished_auto_runs`; `kept` is [(branch, reason)] for every other auto run branch (#75).
    A run that finished with nothing landed has no pull request, so nothing else would ever remove it."""
    from . import auto
    c = cfg()
    unreadable, on, on_folder = False, None, None      # fail safe: a record that cannot be read keeps every run
    try:
        rpath = auto.record_path()
    except SystemExit:
        rpath = None
    if rpath is None:
        unreadable = True
    elif pathlib.Path(rpath).exists():
        try:
            rec = json.loads(pathlib.Path(rpath).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = None
        if isinstance(rec, dict) and rec.get("id"):
            on = str(rec["id"])
            on_folder = _same_path_key(rec["folder"]) if rec.get("folder") else None
        else:
            unreadable = True
    held = {b: p for p, b in worktrees(cwd) if b}
    branches = git("branch", "--format=%(refname:short)", cwd=cwd).splitlines()
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=AUTO_GRACE_HOURS)
    found, kept = [], []
    for branch in sorted(b for b in branches if b.startswith(RUN_BRANCHES)):
        path = held.get(branch)
        if unreadable:
            kept.append((branch, "run record unreadable"))
            continue
        if on is not None and (branch.split("/", 1)[1] == on or (path and on_folder and _same_path_key(path) == on_folder)):
            kept.append((branch, "the run is still on"))
            continue
        started = auto_started(branch, path)
        if started is None:                                    # no timestamp in the id, no folder: the branch's last commit
            tip = git("log", "-1", "--format=%ct", branch, cwd=cwd, check=False)
            started = datetime.datetime.fromtimestamp(int(tip.stdout.strip())) if tip.returncode == 0 and tip.stdout.strip().isdigit() else None
        if started is None or started >= cutoff:
            kept.append((branch, f"started less than {AUTO_GRACE_HOURS} h ago" if started else "its start is unknown"))
            continue
        if path and not pathlib.Path(path).is_dir():
            kept.append((branch, "its folder is missing (`git worktree prune` clears it)"))
            continue
        ahead = git("rev-list", "--count", f"origin/{c.integration}..{branch}", cwd=cwd, check=False)
        if ahead.returncode != 0:
            kept.append((branch, f"cannot compare with origin/{c.integration}"))
        elif ahead.stdout.strip() != "0":
            kept.append((branch, f"it has commits that are not on {c.integration}"))
        elif path and (lambda r: r.returncode != 0 or r.stdout.strip())(git("status", "--porcelain", cwd=path, check=False)):
            kept.append((branch, "its folder has uncommitted changes (or its state cannot be read)"))
        else:
            found.append((path, branch))
    return found, kept


def remove_auto_runs(found, dry=False, cwd=None):
    """Removes those auto runs: the worktree first (git refuses a dirty one: then the branch stays too), then the branch. Returns the
    number removed."""
    done = 0
    for path, branch in found:
        what = f"the auto run {branch}" + (f" and its folder {path}" if path else "")
        if dry:
            print(f"would remove {what}")
            done += 1
            continue
        if path and git("worktree", "remove", str(path), cwd=cwd, check=False).returncode != 0:
            print(f"kept the auto run {branch}: git would not remove {path}")
            continue
        git("branch", "-D", branch, cwd=cwd, check=False)
        print(f"removed {what}")
        done += 1
    return done


def cmd_prune(args):
    local, remote = prune_candidates()
    runs = finished_auto_runs()
    unused, kept = unused_auto_runs()
    runs += [r for r in unused if r[1] not in {b for _, b in runs}]
    kept = [(b, why) for b, why in kept if b not in {b2 for _, b2 in runs}]
    for branch, why in kept:
        print(f"kept the auto run {branch}: {why}")
    removed = remove_auto_runs(runs, args.dry_run) if runs else 0
    if not local and (args.local_only or not remote):
        if not removed:
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

def claude_settings_files(root):
    """The Claude Code settings files that can widen a session of the project: its shared and its local settings, and the user's."""
    home = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
    return [root / ".claude" / "settings.json", root / ".claude" / "settings.local.json", home / "settings.json"]


def _abs(entry, base):
    """An additionalDirectories entry as an absolute path. Git Bash's `/c/...` is read as `C:/...` on Windows. A relative entry is taken
    from the main checkout (Claude Code does not document the base, so the warning asks for an absolute path)."""
    if os.name == "nt" and re.match(r"^/[A-Za-z](/|$)", entry):
        entry = entry[1].upper() + ":" + (entry[2:] or "/")
    p = pathlib.Path(entry)
    return p if p.is_absolute() else base / p


def _within(path, folder):
    """True when `path` is `folder` or below it; case-insensitive on Windows, `/` and `\\` alike."""
    p, f = (os.path.normcase(os.path.abspath(str(x))) for x in (path, folder))
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)


def additional_directories(root, files=None):
    """The `permissions.additionalDirectories` of the project's Claude Code settings (all files merged), as absolute paths. Read only:
    the settings are the owner's, Saani never writes them. A file that is missing or not valid JSON adds nothing."""
    out = []
    for path in claude_settings_files(root) if files is None else files:
        try:
            data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        perms = data.get("permissions") if isinstance(data, dict) else None
        dirs = perms.get("additionalDirectories") if isinstance(perms, dict) else None
        out += [_abs(d.strip(), root) for d in dirs or [] if isinstance(d, str) and d.strip()] if isinstance(dirs, list) else []
    return out


def worktrees_warning(c, files=None):
    """A line for `saani status` when the workers' worktrees folder is outside every working directory of a Claude Code session of the
    project: then a command that writes there, the agents' included, asks the owner. None when the folder is inside the main checkout or
    under an `additionalDirectories` entry. The line to add is relative to the main checkout when it can be (`../webshop-wt`), so the
    project's committed `.claude/settings.json` covers every developer; Claude Code resolves it from the project's folder."""
    wt = c.wt_dir
    if _within(wt, c.root) or any(_within(wt, d) for d in additional_directories(c.root, files)):
        return None
    full = (wt.resolve() if wt.exists() else wt)
    try:
        entry = os.path.relpath(full, c.root.resolve())
    except ValueError:                                     # another drive on Windows: no relative path
        entry = str(full)
    entry = entry.replace("\\", "/")
    return (f"warning: worktrees folder {str(full).replace(chr(92), '/')} is not in permissions.additionalDirectories: a command that "
            f"writes there (the agents' included) asks the owner. Add it to the project's .claude/settings.json, committed so every "
            f"developer has it (Claude Code reads it once they trust the folder): "
            f'{{"permissions": {{"additionalDirectories": ["{entry}"]}}}}')


def cmd_status(args):
    if getattr(args, "action", None) == "sync":
        return status.cmd_sync(args)
    warning = worktrees_warning(cfg())
    if warning:
        print(warning)
    if not getattr(args, "stats", False):
        held = read_lock(lock_path())                      # None: free; {}: there, but unreadable
        folder = lock_path().parent / queue.QUEUE_DIR
        jobs = queue.jobs_in(folder) if folder.is_dir() else []
        busy = queue.read_json(folder / queue.RUNNER) if folder.is_dir() else None
        if busy and not queue.alive(busy.get("pid")):
            busy = None                                    # a runner whose process is gone runs nothing
        waiting = queue.order([j for j in jobs if j.get("state") == "waiting"])
        wts = [(p, b) for p, b in worktrees() if in_wt_dir(p)]
        procs = activity.processes()
        outside = activity.outside_runs(procs, [j.get("pid") for j in jobs] + [(busy or {}).get("pid")],
                                        activity.known_commands(cfg())) if procs is not None else []
        from . import auto
        lines = activity.tasks(held, busy, waiting, auto.read_record(), wts, outside, job_since=lambda j: activity.job_since(j, folder))
        print("\n".join(activity.render(lines, wts, outside_unknown=procs is None, parked=auto.run_parked_issues())))
        return 0
    print("statistics:")
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
    for kind, k in queue.summary(queue.rows(main_checkout() / "build" / TESTS_LOG)).items():
        print(f"  {kind:6} {k['jobs']} job(s), median wait {k['wait']} s, median run {k['run']} s")
    return 0


def clean_tree(cwd):
    """The git tree id of the checkout `cwd` when it is clean, else None: no uncommitted change and no untracked file (not ignored) that the
    tests could read. Only then is a stored test result for that tree the result of this checkout; when git says anything unclear: None."""
    status = git("status", "--porcelain", "--untracked-files=all", cwd=cwd, check=False)
    if status.returncode != 0 or status.stdout.strip():
        return None
    tree = git("rev-parse", "HEAD^{tree}", cwd=cwd, check=False)
    return tree.stdout.strip() or None if tree.returncode == 0 else None


def queued_run(kind, command, cwd, fresh=False):
    """Run a test command through the test queue from the worktree `cwd`; its exit code. A clean checkout whose tree and command have a green
    result in the queue's store reuses it (`fresh`: run anyway; saani/queue.py)."""
    return queue.run(kind, command, lock_path(cwd).parent, cwd, git("rev-parse", "HEAD", cwd=cwd),
                     main_checkout(cwd) / "build" / TESTS_LOG, tree=lambda: clean_tree(cwd), fresh=fresh)


def test_kind(kind):
    """The queue kind for `saani test --kind`: landing or full; the old name tier1 is read as landing, with a message. `land` is the
    run inside `saani land` (it holds the landing lock, so it goes first): it is not offered here."""
    if kind in queue.RENAMED_KINDS:
        new = queue.RENAMED_KINDS[kind]
        print(f"saani test: --kind {kind} is now --kind {new} (run as {new})", file=sys.stderr)
        return new
    if kind == "land":
        raise SystemExit("saani test: --kind land is the run inside saani land (it holds the landing lock): use --kind landing")
    if kind not in queue.KINDS:
        raise SystemExit(f"saani test: unknown --kind {kind}: one of {', '.join(queue.KINDS)}")
    return kind


def cmd_test(args):
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("give the command after --, e.g. saani test -- python -m pytest")
    return queued_run(test_kind(args.kind), command, named_worktree(args.worktree), fresh=bool(getattr(args, "fresh", False)))


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
    p.add_argument("action", nargs="?", choices=["pick", "on", "off", "status", "report", "end", "away", "watch"],
                   help="pick: the eligible issues in the run's order, the free slots, why the others are left; on, off, status, report: the run; end: whether the End opens or updates the release pull request; "
                        "away: start the run in a headless Claude Code session, in the background (saani/auto.py); "
                        "watch: what `away` starts (the session, then the run's off), never typed by hand")
    p.add_argument("--id", help="the run's id instead of the date and time (YYYY-MM-DD_HHMM); with `on`, the id of the run")
    p.add_argument("--folder", help="on, away: the run's folder (default: the current one), inside the worktrees folder")
    p.add_argument("--deploy", action="store_true", help="on, away: the owner started the run so that it may deploy")
    p.add_argument("--keep-on", dest="keep_on", action="store_true",
                   help="away: the run stays on and keeps taking newly eligible issues when its list is empty (ends after [auto] keep_on_quiet_minutes of quiet)")
    p.add_argument("--wait", type=float, default=None, metavar="MINUTES",
                   help="pick: block up to MINUTES until an issue is eligible or a stop rule is reached (JSON)")
    p.add_argument("--here", action="store_true", help="on: the interactive sub-mode: the run's record, the guard left in present mode")
    p.add_argument("--run", dest="run_id", help="off: end this run (its guard entries and the record), from a terminal")
    p.add_argument("--check", action="store_true", help="status: exit 3 when a stop rule is reached")
    p.add_argument("--pr", type=int, help="report: the pull request the report is for (the merge commands name it)")
    p.add_argument("--post", action="store_true", help="report: comment the report on the pull request (--pr)")
    p.add_argument("--reason", help="report: why the run stopped (no slot, nothing eligible, a stop rule), in the report and its journal")
    p.add_argument("--json", action="store_true", help="pick: as JSON")
    p.add_argument("--limit", type=int, help="pick: at most this many eligible issues")
    p.add_argument("--only", type=int, nargs="+", metavar="N", help="pick, away: take only these issues (pick: before the overlap check)")
    p.set_defaults(run=cmd_auto)
    p = sub.add_parser("land", help="land the current worktree's branch into the integration branch")
    p.add_argument("--keep", action="store_true", help="keep the worktree and its branch after landing")
    p.add_argument("--wait", type=float, default=60, help="minutes to wait for the landing lock")
    p.add_argument("--dry-run", action="store_true", help="check and print the plan, change nothing")
    p.add_argument("--worktree", help="land this worktree instead of the current folder's (an auto run cannot change folder)")
    p.add_argument("--no-comment", action="store_true", help="do not comment the landing on the issue or label it in-dev (print the step instead; the issue's GitHub status is still set)")
    p.set_defaults(run=cmd_land)
    p = sub.add_parser("status", help="what runs, what is blocked, what waits (or: everything is idle); `--stats`: landings and test queue medians; "
                                      "`status sync`: the issues' GitHub statuses")
    p.add_argument("--stats", action="store_true", help="print instead the landing counts, the merged branches waiting and the test queue's medians")
    p.add_argument("action", nargs="?", choices=["sync"],
                   help="sync: give each open issue the GitHub status its state implies (saani/status.py), a dry run without --apply")
    p.add_argument("--apply", action="store_true", help="sync: set the statuses (default: print what would move)")
    p.set_defaults(run=cmd_status)
    p = sub.add_parser("prune", help="delete the merged branches, locally and on GitHub (never the integration or production branch)")
    p.add_argument("--dry-run", action="store_true", help="print what would be deleted")
    p.add_argument("--local-only", action="store_true", help="leave GitHub alone")
    p.set_defaults(run=cmd_prune)
    p = sub.add_parser("test", help="run a test command through the test queue, one job at a time")
    p.add_argument("--kind", default="landing", metavar="{landing,full}",
                   help="landing (the tests before a landing) goes before full; a landing's own run goes first")
    p.add_argument("--worktree", help="run in this worktree instead of the current folder (a subagent's cd does not last)")
    p.add_argument("--fresh", action="store_true", help="run the command even when a green result for this tree and command is stored")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(run=cmd_test)
    p = sub.add_parser("check-msg", help="the commit-msg hook")
    p.add_argument("file")
    p.set_defaults(run=cmd_check_msg)
