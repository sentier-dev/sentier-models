"""
Runs the structure+values model across ALL classified families and checks
that nothing is lost (value row count == original row count for every family).

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.
See README.md for setup.
--------------------------------------------------------------------------
"""
import argparse
import os

from classify import classify
from model import build_family, load


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
        structure, values, proc_meta = build_family(P, E, rp)
        pids = P[P["reference_product"] == rp]["process_id"].tolist()
        orig = E[E["process_id"].isin(pids)]
        total_original += len(orig)
        total_structure += len(structure)
        total_value += len(values)
        if len(values) != len(orig):
            fails.append(rp)

    print(f"Families tested: {len(fam)}")
    print(f"Failed (row count mismatch) families: {len(fails)}")
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
