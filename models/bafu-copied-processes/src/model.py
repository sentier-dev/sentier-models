"""
Generic model for BAFU 'copied process' families (hackathon issue #36).

Idea: for a family (= one reference_product repeated across locations),
split each exchange row into:
  - STRUCTURE (shared, stored once per family):  flow_name, direction, flow_type, unit
  - VALUES (per location, small):                flow_id, amount, uncertainty_type,
                                                  loc, scale, minimum, maximum

This works for BOTH observed buckets:
  - trivial-template / template-with-variation: one structure row per flow_name,
    N location-value rows referencing it.
  - variable-mix (e.g. production mixes): structure becomes the UNION of all
    flow_names ever used anywhere in the family (the shared technology catalog);
    each location only contributes value-rows for the flows it actually uses
    (i.e. it's naturally sparse -- absent = zero, nothing stored).

Reconstruction re-joins values -> structure and must reproduce the original
exchanges.parquet rows exactly (this is the round-trip check).

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.

Setup (see README.md in the parent folder for full instructions):
    git clone https://github.com/sentier-dev/sentier-inventory
    pip install -r requirements.txt

By default this script looks for a folder named "sentier-inventory" placed
NEXT TO this src/ folder (i.e. bafu-copied-processes/sentier-inventory). Override with
either:
    export BAFU_DATA_ROOT=/path/to/sentier-inventory
or:
    python model.py --data-root /path/to/sentier-inventory
--------------------------------------------------------------------------
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLKIT_ROOT = os.path.dirname(THIS_DIR)
DEFAULT_REPO = os.path.join(TOOLKIT_ROOT, "sentier-inventory")
REPO = os.environ.get("BAFU_DATA_ROOT", DEFAULT_REPO)

VALUE_COLS = ["flow", "amount", "uncertainty_type", "loc", "scale", "minimum", "maximum"]
STRUCT_COLS = ["flow_name", "direction", "flow_type", "unit"]


def load(repo=None):
    repo = repo or REPO
    if not os.path.isdir(repo):
        raise FileNotFoundError(
            f"sentier-inventory not found at: {repo}\n"
            f"Clone it first: git clone https://github.com/sentier-dev/sentier-inventory\n"
            f"...and place the 'sentier-inventory' folder next to bafu-copied-processes/src/, "
            f"or set BAFU_DATA_ROOT / pass --data-root."
        )
    all_p, all_e = [], []
    for d in sorted(glob.glob(os.path.join(repo, "data", "*/"))):
        sector = os.path.basename(d.rstrip("/"))
        if sector == "99-obsolete":
            continue
        p = pd.read_parquet(d + "processes.parquet")
        p["sector"] = sector
        e = pd.read_parquet(d + "exchanges.parquet")
        e["sector"] = sector
        all_p.append(p)
        all_e.append(e)
    return pd.concat(all_p, ignore_index=True), pd.concat(all_e, ignore_index=True)


def build_family(P, E, reference_product):
    procs = P[P["reference_product"] == reference_product].copy()
    pids = procs["process_id"].tolist()
    loc_of = dict(zip(procs["process_id"], procs["location"]))
    ex = E[E["process_id"].isin(pids)].drop(columns=["location", "sector"]).copy()
    ex["location"] = ex["process_id"].map(loc_of)

    # structure = distinct (flow_name, direction, flow_type, unit) combos seen anywhere
    structure = ex[STRUCT_COLS].drop_duplicates().reset_index(drop=True)
    structure.insert(0, "slot_id", range(len(structure)))

    values = ex.merge(structure, on=STRUCT_COLS, how="left")
    values = values[["process_id", "location", "slot_id"] + VALUE_COLS]

    proc_meta = procs[["process_id", "location", "reference_unit", "reference_amount",
                        "process_type", "technology", "comment"]].copy()

    return structure, values, proc_meta


def reconstruct(structure, values, proc_meta, reference_product):
    merged = values.merge(structure, on="slot_id", how="left")
    out = merged[["process_id"] + STRUCT_COLS + VALUE_COLS].copy()
    out = out[["process_id", "flow", "flow_name", "flow_type", "direction",
               "amount", "unit", "uncertainty_type", "loc", "scale", "minimum", "maximum"]]
    return out


def validate_roundtrip(reference_product, verbose=True, repo=None):
    P, E = load(repo)
    procs = P[P["reference_product"] == reference_product]
    pids = procs["process_id"].tolist()
    original = E[E["process_id"].isin(pids)].drop(columns=["sector", "location"]).reset_index(drop=True)

    structure, values, proc_meta = build_family(P, E, reference_product)
    rebuilt = reconstruct(structure, values, proc_meta, reference_product)

    key = ["process_id", "flow_name", "direction"]
    o = original.sort_values(key).reset_index(drop=True)
    r = rebuilt.sort_values(key).reset_index(drop=True)

    ok = True
    for col in ["flow", "flow_name", "flow_type", "direction", "unit"]:
        if not (o[col].astype(str).values == r[col].astype(str).values).all():
            ok = False
            if verbose:
                print(f"  MISMATCH in column {col}")
    for col in ["amount", "uncertainty_type", "loc", "scale", "minimum", "maximum"]:
        a, b = o[col].astype(float).values, r[col].astype(float).values
        same = np.allclose(a, b, equal_nan=True, rtol=1e-9)
        if not same:
            ok = False
            if verbose:
                print(f"  MISMATCH in column {col}")

    n_structure_rows = len(structure)
    n_value_rows = len(values)
    n_original_rows = len(original)
    n_locations = procs["location"].nunique()

    if verbose:
        print(f"'{reference_product}': roundtrip OK = {ok}")
        print(f"  number of locations       : {n_locations}")
        print(f"  original exchange rows    : {n_original_rows}")
        print(f"  -> structure rows         : {n_structure_rows}  (stored once)")
        print(f"  -> value rows             : {n_value_rows}  (numeric/id columns only)")
        struct_bytes = structure[STRUCT_COLS].astype(str).apply(lambda c: c.str.len()).sum().sum()
        naive_bytes = struct_bytes * n_locations if n_structure_rows else 0
        saved_pct = 0 if naive_bytes == 0 else 100 * (1 - struct_bytes / naive_bytes)
        print(f"  savings from not repeating structure text: ~{saved_pct:.1f}% "
              f"(text columns stored once instead of {n_locations} times)")
        print()
    return ok


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Validate the structure+values model against sentier-inventory.")
    ap.add_argument("--data-root", default=None,
                     help="Path to your local sentier-inventory clone. "
                          "Default: BAFU_DATA_ROOT env var, else ./sentier-inventory next to this toolkit.")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    repo = args.data_root or REPO
    ok = True
    for rp in [
        "Electricity, high voltage, at grid",   # trivial-template
        "Electricity, low voltage, at grid",    # template-with-variation
        "Electricity, production mix",          # variable-mix (the hard case)
        "Natural gas, at production onshore",   # variable-mix
    ]:
        ok = validate_roundtrip(rp, repo=repo) and ok
    return ok


if __name__ == "__main__":
    main()
