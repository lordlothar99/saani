# Changelog

## Unreleased
- The guard: a Claude Code hook shipped by the plugin (`hooks/hooks.json`, `saani guard`, PreToolUse and PostToolUse). Off unless
  `saani auto on` ran in the Claude Code session and its PostToolUse hook registered it (`~/.saani/auto/`); only the owner ends a run
  (`saani auto off --run <id>`). Then it allows the run's work without prompts through a strict allowlist and refuses, with a reason,
  pushes, merges, force flags, curl and wget, secrets, git writes outside the worktrees, edits outside the worktrees and to protected
  files, and counts the refusals by run and project. It fails closed. `bin/saani` starts one Python only (`py -3`, `python`, `python3`,
  never the Windows Store stub).
- `saani auto pick [--json] [--limit N]`: the issues an auto run may take now, in order, from the At a glance tables, with the free slots and why the others are left (blocked, no Type, already in a worktree, overlapping files). `[auto] parallel` sizes the run (`0`: from the cores and free RAM, at most 3). The auto skill drives the pipeline for several issues at once: coder, reviewer, land, the next issue picked as soon as one lands.
- `saani deploy [--dry-run]` (`/saani:deploy`, owner-typed only) and the `saani:deployer` agent (issue #5): CI green on the production head, no issue in production since the prod tag whose At a glance Deploy row says not to deploy, the `[deploy]` checks and dry run, the deploy with a timeout and its log in `build/`, then the prod tag moved (the one force push Saani does: the moving tag) and the `in-main` issues commented `✔ Deployed` and closed. New `[deploy]` settings: `command`, `dry_run`, `checks`, `log_ok`, `key`, `timeout_minutes`; `{key}`, `{sha}` and `{python}` are expanded. A merge commit left by `saani merge` counts as green when its tree equals a green parent's; production is fetched again before the deploy; the output is shown as it comes. The Deploy row marker is `⛔` / `Do not deploy` / `Don't deploy`; "None" and "No" never block. `saani merge --deploy` now runs it.
- `saani comment <n> <kind>`: a status comment in the shape of `.github/COMMENT_KINDS.md` and its label change. `saani start` posts `Started` (`--no-comment` skips it), `saani land` posts `On dev` through it, `saani ci pr` posts `In PR` and `saani ci merged` posts `On main`, each once per pull request and issue.
- `saani merge [PR] [--deploy] [--dry-run]` (`/saani:merge`, owner-typed only): merges the green integration -> production pull request with a merge commit, refusing unless it is mergeable and its CI is green on the exact head, then relabels and removes the finished auto runs (`saani ci merged`). `--deploy` hands over to the deployer (issue #5); until it exists, it exits 2 after the merge.
- The map draws the plugin's agents (`saani:coder`, `saani:reviewer`...) with the look of their role, and counts their runs on the role's row.

## 0.3.0 (2026-11-22)
- Plugin 0.3.0: the board is the Map (`/map`, `saani map`), the map shows the GitHub issue Type, switching project is a sand storm, long run becomes `auto` and `saani new` becomes `saani start`.
- The `saani` command: `start` (worktree per issue, hotfix from the prod tag), `land` (one landing at a time behind a lock, generated files
  rebuilt, tier 1 through the queue, retries when the integration branch moves), `status`, `prune`, `test` (one test queue for the PC),
  `check-msg` (the commit-msg hook), `ci` (GitHub Actions within the free minutes, the PC for the rest), `kpis`, `ui` (status page and
  isometric map; `map` and `board` are aliases), `progress`.
- Every project setting read from the project's `saani.toml` (docs/CONFIG.md).
- The map (once called the board; `/board` and `saani board` still work) falls back on a synthetic sample, so it works as a published artifact.
- A Claude Code plugin skeleton: the auto skill; this repository is also a plugin marketplace.
- A slash command for every `saani` command (`/saani:ui`, `/saani:start`, `/saani:land`...). The whole repository is the plugin, and its
  `bin/saani` puts the command on the PATH of Claude Code's shell, so nothing else needs setting up.
- The worker subagents `saani:coder`, `saani:tester`, `saani:reviewer`, `saani:scribe` and `saani:researcher` (`agents/`), and the pipeline from an issue to dev.
- A workflow that deletes the head branch of a pull request merged into the production branch, except `dev`, `main` and `master`.
- `saani ci merged <PR>` relabels `in-dev` to `in-main` itself with `gh` when no workflow or `[ci] relabel` command does, and, like `saani prune`, removes the finished auto runs (worktree and branch).
- Two renames: the long run is now **auto** (`saani auto`, `/saani:auto`, worktree `auto-<id>`, branch `auto/<id>`, the At a glance row `Auto`, `[board] folders` key `auto`), and `saani new` is now `saani start` (`/saani:start`). No aliases; old issues with a `Long run` row and old `longrun/*` branches are still read and pruned.
