"""
bafu.py -- one compact entry point for the whole BAFU copied-processes
toolkit (Brightcon 2026 hackathon, issue #36). Wraps classify.py / model.py
/ full_run.py / model_time.py / build_demo_schema_files.py /
forecast_superstructure.py so you don't have to remember which script does
what or run five separate commands.

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.
See README.md for setup (clone sentier-inventory, pip install -r requirements.txt).
--------------------------------------------------------------------------

Usage:
    python bafu.py                     run every step below with defaults, in order
    python bafu.py --data-root PATH    ...against a specific sentier-inventory clone
    python bafu.py <step>              run just one step
    python bafu.py <step> -h           see that step's own options (e.g. --reference-product)
    python bafu.py --no-report         don't save a copy of the output to reports/

Steps (in the order "run everything" executes them):
    classify    Step 1  - classify every copied-process family
    validate    Step 2  - round-trip check on 4 example families
    fullrun     Step 3  - round-trip check on ALL families
    timecheck   Step 3b - time-axis round-trip check (electricity)
    schema      Step 3c - generate real structure.parquet/values.parquet
    forecast    Step 3d - MILP superstructure forecasting (stretch goal)

Every step also still works as its own standalone script (python classify.py,
python forecast_superstructure.py --reference-product "...", etc.) -- this
file only adds one shared front door on top, it doesn't replace them.

Every run of bafu.py (whether "run everything" or a single step) also saves
a plain-text copy of everything it printed to reports/bafu_report_<step-or-
all>_<timestamp>.txt, next to this toolkit's own root folder -- so you have
something to paste into a hackathon write-up or attach as evidence, without
having to copy-paste from the terminal. Pass --no-report to skip that.
"""
import argparse
import datetime
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLKIT_ROOT = os.path.dirname(THIS_DIR)
REPORTS_DIR = os.path.join(TOOLKIT_ROOT, "reports")
sys.path.insert(0, THIS_DIR)

import classify as classify_mod                  # noqa: E402
import model as model_mod                        # noqa: E402
import full_run as full_run_mod                   # noqa: E402
import model_time as model_time_mod                # noqa: E402
import build_demo_schema_files as schema_mod        # noqa: E402
import forecast_superstructure as forecast_mod      # noqa: E402

STEPS = [
    ("classify",  "Step 1  - classify every copied-process family",           classify_mod.main),
    ("validate",  "Step 2  - round-trip check on 4 example families",         model_mod.main),
    ("fullrun",   "Step 3  - round-trip check on ALL families",               full_run_mod.main),
    ("timecheck", "Step 3b - time-axis round-trip check (electricity)",       model_time_mod.main),
    ("schema",    "Step 3c - generate real structure.parquet/values.parquet", schema_mod.main),
    ("forecast",  "Step 3d - MILP superstructure forecasting (stretch goal)", forecast_mod.main),
]
STEP_NAMES = [name for name, _, _ in STEPS]


class _Tee:
    """Writes everything to several streams at once (console + a report file)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


def _run_with_report(label, run_fn):
    """Runs run_fn() with stdout/stderr mirrored into reports/bafu_report_<label>_<timestamp>.txt,
    so every run leaves behind a plain-text copy of what it printed."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORTS_DIR, f"bafu_report_{label}_{ts}.txt")

    real_stdout, real_stderr = sys.stdout, sys.stderr
    exit_code = None
    with open(report_path, "w", encoding="utf-8") as f:
        sys.stdout = _Tee(real_stdout, f)
        sys.stderr = _Tee(real_stderr, f)
        try:
            run_fn()
        except SystemExit as e:
            exit_code = e.code
        finally:
            sys.stdout, sys.stderr = real_stdout, real_stderr

    print(f"\nReport written to: {report_path}")
    if exit_code:
        sys.exit(exit_code)


def _run_all(data_root):
    argv = ["--data-root", data_root] if data_root else []
    failures = []
    for name, label, fn in STEPS:
        print("=" * 72)
        print(label)
        print("=" * 72)
        try:
            fn(argv)
        except Exception as exc:
            failures.append(name)
            print(f"[{name}] FAILED: {exc}")
        print()

    print("=" * 72)
    if failures:
        print(f"Done, with failures in: {', '.join(failures)}")
        sys.exit(1)
    print("Done -- all steps completed.")


def main():
    argv = sys.argv[1:]

    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return

    report = True
    if "--no-report" in argv:
        argv = [a for a in argv if a != "--no-report"]
        report = False

    if argv and argv[0] in STEP_NAMES:
        step, rest = argv[0], argv[1:]
        for name, _, fn in STEPS:
            if name == step:
                if report:
                    _run_with_report(step, lambda fn=fn, rest=rest: fn(rest))
                else:
                    fn(rest)
                return

    if argv and argv[0] not in STEP_NAMES:
        # "run everything" mode only understands --data-root; a step's own
        # options (--reference-product, --sector, ...) need that step run
        # on its own -- see the usage note above.
        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument("--data-root", default=None)
        args, unknown = ap.parse_known_args(argv)
        if unknown:
            print(f"Unknown option(s) for 'run everything' mode: {' '.join(unknown)}")
            print("A step's own options need that step run on its own, e.g.:")
            print('  python bafu.py forecast --reference-product "Electricity, production mix"')
            print(f"\nAvailable steps: {', '.join(STEP_NAMES)}")
            sys.exit(1)
        if report:
            _run_with_report("all", lambda: _run_all(args.data_root))
        else:
            _run_all(args.data_root)
        return

    # no arguments at all -- run everything with defaults
    if report:
        _run_with_report("all", lambda: _run_all(None))
    else:
        _run_all(None)


if __name__ == "__main__":
    main()
