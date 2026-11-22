"""The saani command: `python -I saani.py <command> ...` from inside a project's git checkout (README.md, docs/SAANI.md).

  start, auto, land, status, prune, test, check-msg     worktrees, landings, the test queue, the commit-msg hook (saani/core.py)
  ci                                            hybrid CI: GitHub Actions within the free minutes, the PC for the rest (saani/ci.py)
  merge [PR] [--deploy]                         merge the green integration -> production pull request, then relabel and clean up (saani/merge.py)
  deploy [--dry-run]                            the checks, the deploy, the log read, the prod tag moved, the issues closed (saani/deploy.py)
  kpis                                          the numbers over a window of days (saani/kpis.py)
  ui (aliases: map, board)                      the local status page and the isometric map (saani/board_server.py)
  progress                                      a progress bar for a test run in the background (saani/progress.py)

The settings come from the project's saani.toml (docs/CONFIG.md). Standard library only, Python 3.11 or newer.
"""
import argparse
import pathlib
import sys

if sys.version_info < (3, 11):
    sys.exit("saani needs Python 3.11 or newer (tomllib)")
# -I leaves the script's folder off sys.path: put Saani's own folder there, and nothing else.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from saani import __version__, board_server, ci, core, deploy, kpis, merge, progress  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(prog="saani", description="Saani: GitHub issues from idea to production")
    parser.add_argument("--version", action="version", version=f"saani {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for module in (core, ci, merge, deploy, kpis, board_server, progress):
        module.add_parsers(sub)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
