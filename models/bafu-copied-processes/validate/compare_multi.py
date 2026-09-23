"""
Scores a handful of representative (process, location) pairs against BOTH
the original sentier-brightway export and your rebuilt-via-our-model
export, and prints a match table. This is the real end-to-end proof that
the compact model changes nothing downstream in bw2calc.

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.

Usage (after producing both folders with `sentier-brightway files --out ...`,
once from the untouched download and once with --data-root pointing at your
rebuild_inventory.py output):

  python compare_multi.py --original ./bafu-files-original --ours ./bafu-files-ours
--------------------------------------------------------------------------
"""
import argparse
from pathlib import Path

import bw2calc as bc
import bw_processing as bwp
import pandas as pd

CASES = [
    ("Electricity, low voltage, at grid", "PT"),
    ("Electricity, production mix", "FR"),
    ("Natural gas, at production onshore", "AE"),
    ("Agricultural machinery, general, production", "CH"),  # singleton control
]


def datapackage(folder):
    return bwp.load_datapackage(bwp.generic_directory_filesystem(dirpath=folder))


def score_all(out_dir, method_key="Climate change"):
    out = Path(out_dir)
    processes = pd.read_parquet(out / "registry/processes.parquet")
    methods = pd.read_parquet(out / "registry/methods.parquet")
    m = methods[methods.method_key.str.endswith(method_key)].iloc[0]
    cfs = datapackage(out / "bw_package/methods" / m.method_id.replace(":", "__"))
    inventory = datapackage(out / "bw_package/bafu-2026")
    results = {}
    for name, loc in CASES:
        row = processes[(processes.name == name) & (processes.location == loc)]
        if len(row) == 0:
            results[(name, loc)] = None
            continue
        row = row.iloc[0]
        lca = bc.LCA({int(row.bw_id): 1}, data_objs=[inventory, cfs])
        lca.lci()
        lca.lcia()
        results[(name, loc)] = lca.score
    return results


def _parse_args():
    ap = argparse.ArgumentParser(description="Compare bw2calc scores: original data vs. our rebuilt data.")
    ap.add_argument("--original", default="./bafu-files-original", help="Folder for the untouched sentier-brightway export")
    ap.add_argument("--ours", default="./bafu-files-ours", help="Folder for the export built from our rebuilt data")
    ap.add_argument("--method", default="Climate change", help="Method name suffix to score (default: Climate change)")
    return ap.parse_args()


def main():
    args = _parse_args()
    orig = score_all(args.original, args.method)
    ours = score_all(args.ours, args.method)

    print(f"{'process | location':60s} {'original':>16s} {'ours':>16s} {'match':>7s}")
    for k in CASES:
        o, u = orig[k], ours[k]
        match = "OK" if o == u else "DIFF"
        label = f"{k[0][:45]} | {k[1]}"
        print(f"{label:60s} {o:16.8g} {u:16.8g} {match:>7s}")


if __name__ == "__main__":
    main()
