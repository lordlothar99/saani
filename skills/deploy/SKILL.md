---
name: deploy
description: 'Deploy production: CI green, no issue says do not deploy, the checks, the dry run, the deploy, the log, then the prod tag moved and the issues closed. Use when the owner types /saani:deploy or says "deploy" in their own words.'
argument-hint: "[--dry-run]"
disable-model-invocation: true
---

# /saani:deploy: deploy production

**Arguments:** `$ARGUMENTS`.

Only the owner starts it; an auto run never does. The owner saying "deploy" in their own words in a session means `saani deploy`
(`saani merge --deploy` does the merge first). This session, which sees the owner, runs the real deploy; the `saani:deployer` agent only
prepares and explains.

1. Run `saani deploy --dry-run` first (the `saani:deployer` agent can, and reports the risks): the CI status on the production head, the
   issues in production since the prod tag, the `[deploy]` checks and dry run. Say what would change and every refusal with its next step.
2. Run `saani deploy $ARGUMENTS` only when the owner asked to deploy in this session. Tell the owner to expect the tap of their SSH agent
   (each command that uses the key may ask). The deploy's output is shown as it comes and kept in `build/saani-deploy-<UTC>.log`; on a success
   the prod tag moves (the one force push Saani does) and the issues in the range labelled `in-main` get `✔ Deployed` and are closed.
3. A failure: show the message, read the log, say what it means. Never touch the key, never work around a refusal.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
