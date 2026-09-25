"""
Rebuilds every sector's exchanges.parquet by round-tripping each copied-process
family through our compact model (structure + values), and leaves every
non-family process byte-identical. Writes the result IN PLACE into a
--data-root folder that sentier-brightway can read directly with its own
--data-root flag (no download needed on the sentier-brightway side).

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.

Typical local workflow (see README.md for the full walkthrough):

  1. Create a venv and install sentier-brightway:
       python3 -m venv sb-venv && source sb-venv/bin/activate
       pip install git+https://github.com/sentier-dev/sentier-brightway

  2. Let it download its pinned data once, then copy the cache so you have
     a mutable working copy (path shown by sentier-brightway itself, usually
     under ~/.cache/sentier-brightway/<hash>/):
       cp -r ~/.cache/sentier-brightway/<hash> ./data-root

  3. Rebuild the sentier-inventory copy inside that working copy using THIS
     script:
       python rebuild_inventory.py --data-root ./data-root/sentier-inventory

  4. Point sentier-brightway at the rebuilt copy and compare:
       sentier-brightway files --out ./bafu-files-ours --data-root ./data-root
       sentier-brightway files --out ./bafu-files-original   # untouched download
--------------------------------------------------------------------------
"""
import argparse
import glob
import os
import sys

import pandas as pd

VALIDATE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(os.path.dirname(VALIDATE_DIR), "src")
sys.path.insert(0, SRC_DIR)
from model import build_family, check_roundtrip, reconstruct  # noqa: E402

STRUCT_COLS = ["flow_name", "direction", "flow_type", "unit"]


def load(src):
    all_p, all_e = [], []
    for d in sorted(glob.glob(os.path.join(src, "data", "*/"))):
        sector = os.path.basename(d.rstrip("/"))
        p = pd.read_parquet(d + "processes.parquet")
        p["sector"] = sector
        e = pd.read_parquet(d + "exchanges.parquet")
        e["sector"] = sector
        all_p.append(p)
        all_e.append(e)
    return pd.concat(all_p, ignore_index=True), pd.concat(all_e, ignore_index=True)


def _parse_args():
    ap = argparse.ArgumentParser(description="Rebuild sentier-inventory's exchanges.parquet files via the compact model, in place.")
    ap.add_argument("--data-root", required=True,
                     help="Path to a MUTABLE local copy of sentier-inventory "
                          "(e.g. ./data-root/sentier-inventory) -- this script overwrites "
                          "its exchanges.parquet files. Keep a separate untouched copy "
                          "if you want to compare against the original.")
    return ap.parse_args()


def main():
    args = _parse_args()
    src = args.data_root
    if not os.path.isdir(src):
        raise FileNotFoundError(f"--data-root not found: {src}")

    P, E = load(src)
    loc_counts = P.groupby("reference_product")["location"].nunique()
    families = loc_counts[loc_counts > 1].index.tolist()
    print(f"{len(families)} families to round-trip")

    rebuilt_chunks = []
    covered_pids = set()
    n_ok, n_fail = 0, 0

    for rp in families:
        # Use model.py's own check_roundtrip() as the single source of truth
        # for what "round-trip ok" means (every column, NaN-safe, including
        # the exchange's own `location` -- see build_family's docstring).
        # An earlier version of this loop dropped the `location` column
        # before comparing, so it never actually checked it.
        ok = check_roundtrip(P, E, rp, verbose=False)
        n_ok += ok
        n_fail += not ok

        structure, values, proc_meta = build_family(P, E, rp)
        rebuilt = reconstruct(structure, values, proc_meta, rp)
        pids = P[P["reference_product"] == rp]["process_id"].tolist()
        covered_pids.update(pids)
        rebuilt_chunks.append(rebuilt)

    print(f"round-trip ok: {n_ok}, failed: {n_fail}")

    rebuilt_all = pd.concat(rebuilt_chunks, ignore_index=True)
    # NOTE: do NOT overwrite rebuilt_all["location"] with the process's own
    # location here. reconstruct() already restores the exchange's TRUE
    # original location (it renames the preserved `link_location` value
    # column back to `location` -- see model.py). Overwriting it again with
    # loc_of (the process's own location) would silently reintroduce the
    # exact location-loss bug that build_family()/reconstruct() were fixed
    # to solve (see PR review: "build_family drops it, rebuild_inventory.py
    # overwrites it with the process location").
    sector_of_pid = dict(zip(P["process_id"], P["sector"]))
    rebuilt_all["sector"] = rebuilt_all["process_id"].map(sector_of_pid)

    untouched = E[~E["process_id"].isin(covered_pids)]

    final = pd.concat([rebuilt_all, untouched], ignore_index=True)
    print(f"original total rows: {len(E)}, rebuilt total rows: {len(final)}")

    for d in sorted(glob.glob(os.path.join(src, "data", "*/"))):
        sector = os.path.basename(d.rstrip("/"))
        orig_sector = pd.read_parquet(d + "exchanges.parquet")
        cols = list(orig_sector.columns)
        sub = final[final["sector"] == sector][cols].copy()
        for c in orig_sector.columns:
            sub[c] = sub[c].astype(orig_sector[c].dtype)
        assert len(sub) == len(orig_sector), f"{sector}: {len(sub)} vs {len(orig_sector)}"
        sub.to_parquet(d + "exchanges.parquet", index=False)
        print(f"  wrote {sector}: {len(sub)} rows")


if __name__ == "__main__":
    main()
