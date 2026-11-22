# Changelog

## Unreleased
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
