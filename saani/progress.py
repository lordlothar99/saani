"""A progress bar with the remaining time for a test run in the background (docs/SAANI.md). Run from the project's checkout.

  saani progress run --out FILE -- python -m pytest -p no:warnings
  saani progress show FILE                  one line: bar, done/total, failures, remaining time
  saani progress watch FILE --every 180     that line every 3 minutes, and once more at the end (for the Monitor tool; after 30 s without new test output a note says so)

`run` writes the command's output to FILE, the start time and the command to FILE.meta.json, and at the end one line per run to
build/test-runs.json (the wall time against the sum of the tests' own durations: how much slower than its tests a whole run is).

The remaining time: each test's duration from the last build/tests.json (a timing report the project writes: {test id: seconds}). A test that is not in it
counts as its file's average, or else the median of every test; a test that was removed no longer counts. The order is the current
collection (pytest --collect-only, cached in FILE.collect.txt), which is the order pytest runs the tests in. That estimate is multiplied
by the overhead of earlier runs, then corrected by this run's real speed as it goes. Standard library only.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path.cwd()                  # the project's checkout: saani runs from there
HISTORY = ROOT / "build" / "tests.json"
RUNS = ROOT / "build" / "test-runs.json"
DEFAULT_OVERHEAD = 1.15
PROGRESS = re.compile(r"^(?:\S+\.py\s+)?([.sFExX]+)\s*(?:\[\s*\d+%\])?\s*$")
SUMMARY = re.compile(r"^(?:=+ .*\b(passed|failed|error|errors|skipped|no tests ran)\b.* in [\d.]+s"             # the usual last line, between ===
                     r"|(?:\d+ \w+, )*\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|deselected)(?:, \d+ \w+)* in [\d.]+s"   # with -q: no ===
                     r"|no tests ran in [\d.]+s)")
WIDTH = 40


def _meta_path(out):
    return pathlib.Path(str(out) + ".meta.json")


def _collect_path(out):
    return pathlib.Path(str(out) + ".collect.txt")


def pytest_args(command):
    """The pytest arguments inside a run's command, for the collection: after `-m pytest`, or after the `--` of test_report.py.
    Output flags are dropped (they change what --collect-only prints)."""
    args = []
    if "pytest" in command:
        args = command[command.index("pytest") + 1:]
    elif any(c.endswith("test_report.py") for c in command) and "--" in command:
        args = command[command.index("--") + 1:]
    output = ("-q", "-qq", "-v", "-vv", "-s")
    return [a for a in args if a not in output and not a.startswith(("-r", "--durations", "--test-report-json"))]


def collect(command, cache):
    """The node ids in run order, cached beside the output file."""
    if cache.exists():
        return [l for l in cache.read_text(encoding="utf-8").splitlines() if l]
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:warnings", *pytest_args(command)],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ids = [l.strip().replace("\\", "/") for l in proc.stdout.splitlines() if "::" in l and not l.startswith(" ")]
    cache.write_text("\n".join(ids) + "\n", encoding="utf-8")
    return ids


def load_history(path=HISTORY):
    try:
        tests = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))["tests"]
    except (OSError, ValueError, KeyError):
        return {}
    return {t["nodeid"].replace("\\", "/"): float(t.get("duration") or 0) for t in tests if t.get("outcome") not in ("not run",)}


def clip(history):
    """The history with each duration limited to `max(30 s, 5 x the 99th percentile)`: one freak run (a PC that slept, a browser that hung: one
    test once took 4220 s) must not add an hour to every forecast that follows."""
    values = sorted(history.values())
    if len(values) < 20:
        return dict(history)
    cap = max(30.0, 5 * values[int(0.99 * (len(values) - 1))])
    return {k: min(v, cap) for k, v in history.items()}


def estimates(ids, history):
    """Seconds per test, in the order of ids: its own last duration, else its file's average, else the median of all."""
    history = clip(history)
    by_file = {}
    for nodeid, d in history.items():
        by_file.setdefault(nodeid.split("::")[0], []).append(d)
    median = statistics.median(history.values()) if history else 0.5
    out = []
    for nodeid in ids:
        if nodeid in history:
            out.append(history[nodeid])
        else:
            same = by_file.get(nodeid.split("::")[0])
            out.append(sum(same) / len(same) if same else median)
    return out


def overhead(path=RUNS):
    """The wall time of earlier runs divided by the sum of their tests' durations (the last five runs)."""
    try:
        runs = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        ratios = [r["wall"] / r["sum"] for r in runs[-5:] if r.get("sum")]
        return statistics.median(ratios) if ratios else DEFAULT_OVERHEAD
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_OVERHEAD


def parse(text):
    """(done, failed, finished, summary) from pytest's console output, with or without -q."""
    done = failed = 0
    finished, summary = False, ""
    started = counting = False
    for line in text.splitlines():
        line = line.rstrip()
        if SUMMARY.match(line):
            finished, summary = True, line.strip("= ")
            break
        if line.startswith("collected ") or line.startswith("=") and "test session starts" in line:
            started = counting = True
            continue
        m = PROGRESS.match(line)
        if m and (counting or not started and (line[:1] in ".sFExX" or ".py" in line)):
            chars = m.group(1)
            done += len(chars)
            failed += chars.count("F") + chars.count("E")
            started = counting = True
        elif done and (line.startswith("=") or line.startswith("_")):
            counting = False  # failures, warnings and the short summary follow: keep reading only for the last line
    return done, failed, finished, summary


def bar(fraction, width=WIDTH):
    full = int(max(0.0, min(1.0, fraction)) * width)
    return "█" * full + "░" * (width - full)


def remaining(est, done, elapsed, prior):
    """Seconds left: the estimate of the tests not run yet, times this run's speed (blended with earlier runs' until enough has run)."""
    est_done, est_left = sum(est[:done]), sum(est[done:])
    if est_done <= 0:
        return est_left * prior
    observed = elapsed / est_done
    weight = min(1.0, est_done / 600.0)
    factor = max(0.5, min(4.0, weight * observed + (1 - weight) * prior))
    return est_left * factor


def fmt_minutes(seconds):
    if seconds < 60:
        return "<1 min"
    return f"{round(seconds / 60)} min"


def line(out, now=None, history_path=HISTORY, runs_path=RUNS, note=""):
    """One line: the bar, the count, the failures, the time left. `note` ends the line."""
    out = pathlib.Path(out)
    now = now or time.time()
    try:
        meta = json.loads(_meta_path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = out.stat() if out.exists() else None
        meta = {"start": getattr(st, "st_birthtime", st.st_ctime) if st else now, "command": []}
    text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    done, failed, finished, summary = parse(text)
    elapsed = now - meta["start"]
    if finished:
        return f"Tests [{bar(1.0)}] 100%  done in {fmt_minutes(elapsed)}: {summary}"
    ids = collect(meta.get("command") or [], _collect_path(out))
    total = len(ids) or done or 1
    est = estimates(ids, load_history(history_path))
    left = remaining(est, min(done, len(est)), elapsed, overhead(runs_path))
    end = datetime.datetime.fromtimestamp(now + left).strftime("%H:%M")
    fails = f"{failed} failed" if failed else "0 failed"
    return f"Tests [{bar(done / total)}] {100 * done // total}%  {done}/{total}  {fails}  ~{fmt_minutes(left)} left (ends ~{end}){note}"


def record_run(out, wall, history_path=HISTORY, runs_path=RUNS):
    """One line per finished run in build/test-runs.json: the wall time against the sum of the tests' durations just written."""
    durations = load_history(history_path)
    if not durations:
        return
    runs_path = pathlib.Path(runs_path)
    try:
        runs = json.loads(runs_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        runs = []
    kept = clip(durations)          # a freak duration is not part of the ratio: it leaves the sum and the wall time alike
    away = sum(durations.values()) - sum(kept.values())
    runs.append({"date": datetime.datetime.now().isoformat(timespec="seconds"), "tests": len(durations),
                 "sum": round(sum(kept.values()), 1), "wall": round(max(wall - away, 0.0), 1)})
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    runs_path.write_text(json.dumps(runs[-20:], indent=1) + "\n", encoding="utf-8")


def cmd_run(args):
    out = pathlib.Path(args.out)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    start = time.time()
    _meta_path(out).write_text(json.dumps({"start": start, "command": command}), encoding="utf-8")
    _collect_path(out).unlink(missing_ok=True)
    history_before = HISTORY.stat().st_mtime if HISTORY.exists() else 0
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(out, "w", encoding="utf-8") as fh:
        code = subprocess.run(command, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, env=env).returncode
    if HISTORY.exists() and HISTORY.stat().st_mtime > history_before:
        record_run(out, time.time() - start)
    return code


def _size(path):
    try:
        return pathlib.Path(path).stat().st_size
    except OSError:
        return 0


def cmd_watch(args):
    last_size, quiet = -1, 0
    while True:
        size = _size(args.file)
        quiet = 0 if size != last_size else quiet          # seconds since the output last grew
        last_size = size
        note = f"  (no new test for {int(quiet)} s)" if quiet >= 30 else ""
        text = line(args.file, note=note)
        print(text, flush=True)
        if " done in " in text:
            return 0
        for _ in range(int(args.every)):
            time.sleep(1)
            if parse(pathlib.Path(args.file).read_text(encoding="utf-8", errors="replace"))[2]:
                break
        if _size(args.file) == last_size:
            quiet += int(args.every)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("command", nargs=argparse.REMAINDER)
    s = sub.add_parser("show")
    s.add_argument("file")
    w = sub.add_parser("watch")
    w.add_argument("file")
    w.add_argument("--every", type=float, default=180)
    args = p.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "show":
        print(line(args.file))
        return 0
    return cmd_watch(args)


if __name__ == "__main__":
    sys.exit(main())


def add_parsers(sub):
    p = sub.add_parser("progress", help="a progress bar with the remaining time for a test run (run, show, watch)")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(run=lambda a: main(a.rest))
