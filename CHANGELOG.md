# Changelog

## Unreleased
- The guard: a Claude Code PreToolUse hook shipped by the plugin (`hooks/hooks.json`, `saani guard`). Off unless `saani auto on` registered
  the Claude Code session (`~/.saani/auto/`); then it allows the run's work without prompts through a strict allowlist and refuses, with a
  reason, pushes, merges, force flags, secrets, edits outside the worktrees and to protected files, and counts the refusals. It fails
  closed. `bin/saani` starts one Python only (`py -3`, `python`, `python3`, never the Windows Store stub).
- `saani comment <n> <kind>`: a status comment in the shape of `.github/COMMENT_KINDS.md` and its label change. `saani start` posts `Started` (`--no-comment` skips it), `saani land` posts `On dev` through it, `saani ci pr` posts `In PR` and `saani ci merged` posts `On main`, each once per pull request and issue.

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
- `saani merge [PR] [--deploy] [--dry-run]` (`/saani:merge`, owner-typed only): merges the green integration -> production pull request with a merge commit, refusing unless it is mergeable and its CI is green on the exact head, then relabels and removes the finished auto runs (`saani ci merged`). `--deploy` hands over to the deployer (issue #5); until it exists, it exits 2 after the merge.
- Two renames: the long run is now **auto** (`saani auto`, `/saani:auto`, worktree `auto-<id>`, branch `auto/<id>`, the At a glance row `Auto`, `[board] folders` key `auto`), and `saani new` is now `saani start` (`/saani:start`). No aliases; old issues with a `Long run` row and old `longrun/*` branches are still read and pruned.
