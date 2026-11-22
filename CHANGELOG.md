# Changelog

## Unreleased
- The `saani` command: `new` (worktree per issue, hotfix from the prod tag), `land` (one landing at a time behind a lock, generated files
  rebuilt, tier 1 through the queue, retries when the integration branch moves), `status`, `prune`, `test` (one test queue for the PC),
  `check-msg` (the commit-msg hook), `ci` (GitHub Actions within the free minutes, the PC for the rest), `kpis`, `board` (status page and
  isometric board), `progress`.
- Every project setting read from the project's `saani.toml` (docs/CONFIG.md).
- The board falls back on a synthetic sample, so it works as a published artifact.
- A Claude Code plugin skeleton: the long-run skill; this repository is also a plugin marketplace.
