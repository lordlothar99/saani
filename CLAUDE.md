# Saani: instructions for Claude Code

- Saani is the owner's AI workshop for GitHub projects (README.md, docs/SAANI.md). Public repo, open source under the GNU AGPL-3.0-only (LICENSE); the name "Saani" is not licensed. It holds no secrets and reads none.
- The code lives here: `saani.py` (the command), `saani/` (the package), `tests/`, `docs/`, `plugin/` (the Claude Code plugin).
- Saani builds itself: `python -I saani.py new <n>` and `python -I saani.py land` (settings in saani.toml). Tests: `python -m pytest -q`.
- Branches: `main` is released, `dev` is where work happens. Commits say what, `refs #n`, and the trailers `Why:` and `Undo:`.
- Standard library only, Python 3.11 or newer. Commands for the owner are Windows PowerShell 5.1.
- Saani is generic: no project's name, path, issue number or private data in its code, docs, tests or samples (samples use an invented
  project, acme/webshop).
