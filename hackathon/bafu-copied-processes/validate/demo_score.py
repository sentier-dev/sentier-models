"""
Reads the folder written by `sentier-brightway files --out DIR`. No bw2data
project involved -- reads the plain-files datapackages directly.

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths
(this one never had any -- it already took its folder as an argument --
but is included here with clearer docs and argparse for consistency).

Requires: pip install bw2calc bw_processing pandas (already covered if you
installed sentier-brightway itself, since it depends on bw2calc/bw_processing).

Usage:
  python demo_score.py --files-dir ./bafu-files --process "Electricity, low voltage, at grid" --location CH
--------------------------------------------------------------------------
"""
import argparse
import json
from pathlib import Path

import bw2calc as bc
import bw_processing as bwp
import pandas as pd


def datapackage(folder: Path) -> bwp.Datapackage:
    return bwp.load_datapackage(bwp.generic_directory_filesystem(dirpath=folder))


def _parse_args():
    ap = argparse.ArgumentParser(description="Score one BAFU process across all methods, from a sentier-brightway 'files' export.")
    ap.add_argument("--files-dir", default="./bafu-files",
                     help="Folder written by `sentier-brightway files --out DIR` (default: ./bafu-files)")
    ap.add_argument("--process", default="Electricity, low voltage, at grid", help="Process name")
    ap.add_argument("--location", default="CH", help="Process location code")
    ap.add_argument("--out-json", default="./scores.json", help="Where to write the resulting scores as JSON")
    return ap.parse_args()


def main():
    args = _parse_args()
    out = Path(args.files_dir)

    processes = pd.read_parquet(out / "registry/processes.parquet")
    row = processes[(processes.name == args.process) & (processes.location == args.location)].iloc[0]
    print(f"{row['name']} | {row.location} | 1 {row.unit} | bw_id {row.bw_id}\n")

    inventory = datapackage(out / "bw_package/bafu-2026")

    methods = pd.read_parquet(out / "registry/methods.parquet")
    results = {}
    for _, m in methods.iterrows():
        cfs = datapackage(out / "bw_package/methods" / m.method_id.replace(":", "__"))
        lca = bc.LCA({int(row.bw_id): 1}, data_objs=[inventory, cfs])
        lca.lci()
        lca.lcia()
        key = m.method_key.split("|")[-1]
        results[key] = lca.score
        print(f"{key:48s} {lca.score:14.6g} {m.unit}")

    with open(args.out_json, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nWritten: {args.out_json}")


if __name__ == "__main__":
    main()
