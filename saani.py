"""The saani command: `python -I saani.py <command> ...` from inside a project's git checkout (README.md, docs/SAANI.md).

  start, auto, land, status, prune, test, check-msg     worktrees, landings, the test queue, the commit-msg hook (saani/core.py)
  ci                                            hybrid CI: GitHub Actions within the free minutes, the PC for the rest (saani/ci.py)
  kpis                                          the numbers over a window of days (saani/kpis.py)
  ui (aliases: map, board)                      the local status page and the isometric map (saani/board_server.py)
  progress                                      a progress bar for a test run in the background (saani/progress.py)
  guard                                         the plugin's Claude Code PreToolUse hook: JSON on stdin (saani/guard.py)

The settings come from the project's saani.toml (docs/CONFIG.md). Standard library only, Python 3.11 or newer.
"""
import os
import sys

if sys.version_info < (3, 11):
    sys.exit("saani needs Python 3.11 or newer (tomllib)")
# -I leaves the script's folder off sys.path: put Saani's own folder there, and nothing else.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

if __name__ == "__main__" and sys.argv[1:2] == ["guard"]:
    # The hook runs before every tool call: load the guard alone, nothing else (argparse and the other modules cost more than the guard).
    from saani import guard
    sys.exit(guard.main())

import argparse  # noqa: E402

from saani import __version__, board_server, ci, core, guard, kpis, progress  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(prog="saani", description="Saani: GitHub issues from idea to production")
    parser.add_argument("--version", action="version", version=f"saani {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for module in (core, ci, kpis, board_server, progress, guard):
        module.add_parsers(sub)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
