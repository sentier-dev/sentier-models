"""
Runs the structure+values model across ALL classified families and checks
that nothing is lost -- a REAL content-level round-trip (every column,
including the exchange's own `location`, see model.py's build_family
docstring), not just a row-count comparison. An earlier version of this
script only checked `len(values) == len(orig)`, which is a much weaker
test: a row-count match says nothing about whether a value in one of those
rows is silently wrong (as the location column used to be -- see PR
review). This reuses model.py's own check_roundtrip() so there is exactly
one place that defines what "round-trip OK" means.

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.
See README.md for setup.
--------------------------------------------------------------------------
"""
import argparse
import os

from classify import classify
from model import check_roundtrip, load


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Round-trip every copied-process family and print totals.")
    ap.add_argument("--data-root", default=None,
                     help="Path to your local sentier-inventory clone. "
                          "Default: BAFU_DATA_ROOT env var, else ./sentier-inventory next to this toolkit.")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    P, E = load(args.data_root)
    fam = classify(args.data_root)

    total_original = 0
    total_structure = 0
    total_value = 0
    fails = []

    for rp in fam["reference_product"]:
        # return_stats=True: one pass per family (cached groupby inside
        # model.py) instead of calling build_family() a second time here
        # just to get row counts -- see check_roundtrip's docstring.
        ok, n_original, n_structure, n_value = check_roundtrip(P, E, rp, verbose=False, return_stats=True)
        total_original += n_original
        total_structure += n_structure
        total_value += n_value
        if not ok:
            fails.append(rp)

    print(f"Families tested: {len(fam)}")
    print(f"Failed (content-level round-trip, every column) families: {len(fails)}")
    if fails:
        for rp in fails[:20]:
            print(f"  FAILED: {rp}")
    print()
    print(f"Original total exchange rows (for these families): {total_original}")
    print(f"Compact model -> structure rows (stored once)      : {total_structure}")
    print(f"Compact model -> value rows (numeric/id only)      : {total_value}")
    print()
    if total_original:
        print(f"Structure/original ratio: {100*total_structure/total_original:.2f}% "
              f"(i.e. text columns are stored at only this fraction of the volume; "
              f"the rest is a reference instead of a repeat)")


if __name__ == "__main__":
    main()
