"""The saani command: `python -I saani.py <command> ...` from inside a project's git checkout (README.md, docs/SAANI.md).

  start, auto, land, status, prune, test, check-msg     worktrees, landings, the test queue, the commit-msg hook (saani/core.py)
  comment                                       a status comment on an issue and its label (saani/comments.py)
  ci                                            hybrid CI: GitHub Actions within the free minutes, the PC for the rest (saani/ci.py)
  merge [PR] [--deploy]                         merge the green integration -> production pull request, then relabel and clean up (saani/merge.py)
  deploy [--dry-run]                            the checks, the deploy, the log read, the prod tag moved, the issues closed (saani/deploy.py)
  kpis                                          the numbers over a window of days (saani/kpis.py)
  release notes <milestone> | --pr N [--short] [--post] the release notes from the GitHub issues; release create <version>: a GitHub release (saani/notes.py)
  ui (aliases: map, board)                      the local status page and the isometric map (saani/board_server.py)
  progress                                      a progress bar for a test run in the background (saani/progress.py)
  guard                                         the plugin's Claude Code hook (PreToolUse, PostToolUse): JSON on stdin (saani/guard.py)

The settings come from the project's saani.toml (docs/CONFIG.md). Standard library only, Python 3.11 or newer.
"""
import os
import sys

if sys.version_info < (3, 11):
    sys.exit("saani needs Python 3.11 or newer (tomllib)")
# -I leaves the script's folder off sys.path: put Saani's own folder there, and nothing else.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

if __name__ == "__main__" and sys.argv[1:2] == ["guard"]:
    # The hook runs before every tool call. No auto run registered at all (~/.saani/auto holds no entry, no `.over` mark of a run that
    # is over) and not a `saani auto on` call: present mode, nothing to print, before any import. Else load the guard alone (argparse and
    # the other modules cost more).
    _raw = sys.stdin.buffer.read()
    try:
        _some = any(n.endswith((".json", ".over")) for n in os.listdir(os.path.join(os.path.expanduser("~"), ".saani", "auto")))
    except FileNotFoundError:
        _some = False
    except OSError:
        _some = True
    if not _some and b"auto" not in _raw:
        sys.exit(0)
    from saani import guard
    sys.exit(guard.main(_raw))

import argparse  # noqa: E402

from saani import __version__, board_server, ci, comments, core, deploy, guard, kpis, merge, notes, progress  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(prog="saani", description="Saani: GitHub issues from idea to production")
    parser.add_argument("--version", action="version", version=f"saani {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for module in (core, ci, comments, merge, deploy, kpis, notes, board_server, progress, guard):
        module.add_parsers(sub)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
