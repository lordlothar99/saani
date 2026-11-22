# The Saani plugin for Claude Code

What a Claude Code session needs to work the Saani way in any project: the long-run skill now; the worker agents and hooks later.

| Part | State |
|---|---|
| `skills/longrun/SKILL.md` | `/saani:longrun`, `/saani:longrun 12 15`, `/saani:longrun off`: the checklist of a long run (the Architect works alone for hours on the open issues) |
| Claude Code hooks | none yet (they will live in `hooks/hooks.json`). The **git** commit-msg hook is not part of the plugin: it is [.githooks/commit-msg](../.githooks/commit-msg) at the repo root |
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
This repository is also a plugin marketplace (`.claude-plugin/marketplace.json` at its root, its plugin in `./plugin`).

| Step | In PowerShell | In a Claude Code session |
|---|---|---|
| Add the marketplace (local clone) | `claude plugin marketplace add C:\Users\<you>\projects\saani` | `/plugin marketplace add C:\Users\<you>\projects\saani` (if a drive path is refused: open the clone's folder and use `./`) |
| or from GitHub (private: your git credentials, e.g. `gh auth setup-git`) | `claude plugin marketplace add lordlothar99/saani` | `/plugin marketplace add lordlothar99/saani` |
| Install | `claude plugin install saani@saani` | `/plugin install saani@saani` |
| After a change | `claude plugin marketplace update saani`, then `claude plugin update saani@saani` | the same with `/plugin`, then `/reload-plugins` |

- The skill is namespaced: **`/saani:longrun`**, not `/longrun`.
- `plugin.json` pins `version`: bump it at each release, or installed copies keep the old one.
- Check before a commit: `claude plugin validate .` (marketplace) and `claude plugin validate .\plugin --strict` (plugin).
- License: PolyForm Shield 1.0.0 ([LICENSE](../LICENSE)), source-available, not OSI open source.
- Worker agents will go in `agents/<name>.md`: every `.md` there is loaded as an agent, so no README in that folder.
- Sources: https://code.claude.com/docs/en/plugins/marketplace-reference.md, .../manifest-reference.md, .../components.md, .../install.md,
  .../cli-reference.md.
