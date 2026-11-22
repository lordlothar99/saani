# The Saani plugin for Claude Code

What a Claude Code session needs to work the Saani way in any project: a slash command for each `saani` command and the long-run skill now;
the worker agents and hooks later.

| Part | State |
|---|---|
| `skills/<command>/SKILL.md` | `/saani:ui`, `/saani:status`, `/saani:new`, `/saani:land`, `/saani:test`, `/saani:prune`, `/saani:ci`, `/saani:kpis`, `/saani:progress`: each runs that `saani` command and explains the result. `new`, `land` and `prune` change the repo, so they run only when the owner types them (`disable-model-invocation`), and `prune` shows a dry run and asks first. Each skill ends with the same "Running saani" section; a test keeps one skill per command and those sections identical |
| `bin/saani`, `bin/saani.cmd` | the `saani` command inside Claude Code: `bin/` is on the PATH of Claude's shell while the plugin is enabled, and the launchers run `saani.py` from the plugin's own copy (bash: the first Python 3.11+; Windows: `python`). `.gitattributes` keeps `bin/saani` in LF, or bash fails on Windows checkouts |
| `skills/longrun/SKILL.md` | `/saani:longrun`, `/saani:longrun 12 15`, `/saani:longrun off`: the checklist of a long run (the Architect works alone for hours on the open issues) |
| Claude Code hooks | none yet (they will live in `hooks/hooks.json`). The **git** commit-msg hook is not part of the plugin: it is [.githooks/commit-msg](../.githooks/commit-msg), and needs `SAANI_HOME` because git runs it outside Claude Code |
| Worker agents | planned, not written yet (no `agents/` folder until they exist, so Claude Code loads nothing half-made) |

## Planned worker roles
| Role | Model (first guess, to be measured) | Job |
|---|---|---|
| Coder | Sonnet, Opus for hard issues | writes the change for one issue in its own worktree |
| Tester | Haiku | drives `saani test`, reports only failures and what they mean |
| Reviewer | Opus | reviews the diff with fresh eyes, never the coder's reasoning |
| Scribe | Haiku | issue comments, labels, commit trailers |
| Researcher | Sonnet | reads docs, releases and code, brings back the answer |
| Sentinel | Sonnet | server diagnostics with a read-only key |
| Deployer | Sonnet | deploys production after the owner's tap, with a deploy-only key |

## Install (checked against the Claude Code docs and `claude plugin validate`, 2026-11-22)
This repository is both the marketplace and the plugin: `.claude-plugin/marketplace.json` lists one plugin whose source is `./`, the repo
root, with `.claude-plugin/plugin.json` beside it. Claude Code copies only a plugin's own folder at install, so the plugin is the whole
repo: that way its copy holds `saani.py` for `bin/saani`. A side effect: a plugin with a `bin/` folder cannot be installed from claude.ai or
Cowork, only in Claude Code (Saani needs git and a local machine anyway).

| Step | In PowerShell | In a Claude Code session |
|---|---|---|
| Add the marketplace (local clone) | `claude plugin marketplace add C:\Users\<you>\projects\saani` | `/plugin marketplace add C:\Users\<you>\projects\saani` (if a drive path is refused: open the clone's folder and use `./`) |
| or from GitHub (private: your git credentials, e.g. `gh auth setup-git`) | `claude plugin marketplace add lordlothar99/saani` | `/plugin marketplace add lordlothar99/saani` |
| Install | `claude plugin install saani@saani` | `/plugin install saani@saani` |
| After a change | `claude plugin marketplace update saani`, then `claude plugin update saani@saani` | the same with `/plugin`, then `/reload-plugins` |

- Skills are namespaced: **`/saani:ui`**, not `/ui`.
- Skills that need a file of the plugin use `${CLAUDE_PLUGIN_ROOT}` (substituted in the skill's text; it is not an environment variable of
  Claude's shell).
- `plugin.json` pins `version`: bump it at each release, or installed copies keep the old one.
- Check before a commit: `claude plugin validate .` (the marketplace and the plugin are the same folder). Two warnings are expected, so
  `--strict` fails: `CLAUDE.md` at the plugin root is not loaded (it is for working on Saani, not shipped as context), and no icon (only a
  plugin-directory listing needs one).
- License: GNU AGPL-3.0-only ([LICENSE](../LICENSE)).
- Worker agents will go in `agents/<name>.md`: every `.md` there is loaded as an agent, so no README in that folder.
- Sources: https://code.claude.com/docs/en/plugins/marketplace-reference.md, .../manifest-reference.md, .../components.md (Executables),
  .../loading.md (In-place and copied plugins), .../install.md, .../cli-reference.md, https://code.claude.com/docs/en/skills (string substitutions).
