"""
Extends the compact model (issue #36) to a SECOND axis: time.

Today, time variation exists but is invisible to the model: BAFU encodes it
as free text inside `name`/`reference_product` ("... winter 2018"), so two
seasonal versions of the same process count as two unrelated
reference_products -- classify.py never groups them into one family.

This script:
  1. Parses the "<season> <year>" tag out of the name (electricity sector,
     the only place it currently appears -- 32 processes, confirmed by a
     dataset-wide regex scan).
  2. Groups by the time-stripped base name instead of the raw
     reference_product, so a family can now vary by LOCATION, TIME, or both
     at once.
  3. Reuses the exact same structure/values split as model.py, just with a
     second key column (`time_tag`) added next to `location`.
  4. Round-trips back to the original exchange rows to prove nothing is lost.

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.
See README.md for setup.
--------------------------------------------------------------------------
"""
import argparse
import os
import re
import sys

import pandas as pd

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)
from model import STRUCT_COLS, VALUE_COLS, compare_rows  # noqa: E402

TOOLKIT_ROOT = os.path.dirname(THIS_DIR)
DEFAULT_REPO = os.path.join(TOOLKIT_ROOT, "sentier-inventory")
REPO = os.environ.get("BAFU_DATA_ROOT", DEFAULT_REPO)
TIME_PAT = re.compile(r",?\s*(winter|summer)\s*(\d{4})", re.IGNORECASE)


def split_time(name: str):
    m = TIME_PAT.search(name)
    if not m:
        return name, None
    base = TIME_PAT.sub("", name).strip()
    return base, f"{m.group(1).lower()}-{m.group(2)}"


def load_sector(sector, repo=None):
    repo = repo or REPO
    if not os.path.isdir(repo):
        raise FileNotFoundError(
            f"sentier-inventory not found at: {repo}\n"
            f"Clone it first: git clone https://github.com/sentier-dev/sentier-inventory\n"
            f"...and place the 'sentier-inventory' folder next to bafu-copied-processes/src/, "
            f"or set BAFU_DATA_ROOT / pass --data-root."
        )
    d = os.path.join(repo, "data", sector)
    p = pd.read_parquet(os.path.join(d, "processes.parquet"))
    e = pd.read_parquet(os.path.join(d, "exchanges.parquet"))
    return p, e


def build_time_family(P, E, base_name):
    """Same structure/values split as model.py's build_family, plus a second
    key column (`time_tag`) next to `location`. Preserves the exchange's own
    true location under `link_location` rather than overwriting it with the
    process's own location -- see model.py's build_family docstring for why
    that distinction matters (~62k dataset-wide rows where they differ)."""
    procs = P[P["base_name"] == base_name].copy()
    pids = procs["process_id"].tolist()
    key_of = dict(zip(procs["process_id"], zip(procs["location"], procs["time_tag"])))

    ex = E[E["process_id"].isin(pids)].drop(columns=["sector"], errors="ignore").copy()
    ex = ex.rename(columns={"location": "link_location"})
    ex["location"] = ex["process_id"].map(lambda pid: key_of[pid][0])
    ex["time_tag"] = ex["process_id"].map(lambda pid: key_of[pid][1])

    structure = ex[STRUCT_COLS].drop_duplicates().reset_index(drop=True)
    structure.insert(0, "slot_id", range(len(structure)))

    values = ex.merge(structure, on=STRUCT_COLS, how="left")
    values = values[["process_id", "location", "time_tag", "slot_id"] + VALUE_COLS]
    return structure, values, procs


def reconstruct_time(structure, values):
    merged = values.merge(structure, on="slot_id", how="left")
    out = merged[["process_id"] + STRUCT_COLS + VALUE_COLS].copy()
    out = out.rename(columns={"link_location": "location"})
    out = out[["process_id", "flow", "flow_name", "flow_type", "direction",
               "amount", "unit", "uncertainty_type", "loc", "scale", "minimum", "maximum", "location"]]
    return out


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Round-trip the time+location model on the electricity sector.")
    ap.add_argument("--data-root", default=None,
                     help="Path to your local sentier-inventory clone. "
                          "Default: BAFU_DATA_ROOT env var, else ./sentier-inventory next to this toolkit.")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    P, E = load_sector("02-electricity", args.data_root)
    # Key on reference_product (like classify.py/model.py), not on `name` --
    # in this dataset the two are identical for every process (verified
    # across all 11,947 processes), so this is behaviour-preserving today,
    # but reference_product is model.py's canonical family key and this
    # keeps that convention consistent instead of silently depending on
    # name==reference_product holding forever.
    P[["base_name", "time_tag"]] = P["reference_product"].apply(lambda n: pd.Series(split_time(n)))

    time_families = P[P["time_tag"].notna()]["base_name"].unique().tolist()
    print(f"{len(time_families)} time-families found (2 axes: location + time_tag)\n")

    n_ok, n_fail, total_rows = 0, 0, 0
    for bn in time_families:
        fam_procs = P[P["base_name"] == bn]
        pids = fam_procs["process_id"].tolist()
        n_locations = fam_procs["location"].nunique()
        n_variants = fam_procs["time_tag"].nunique()

        structure, values, procs = build_time_family(P, E, bn)
        rebuilt = reconstruct_time(structure, values)

        orig = E[E["process_id"].isin(pids)].drop(columns=["sector"], errors="ignore")
        # `flow` in the sort key, same reasoning as model.py's check_roundtrip.
        key = ["process_id", "flow_name", "direction", "flow"]
        o = orig.sort_values(key).reset_index(drop=True)
        r = rebuilt.sort_values(key).reset_index(drop=True)
        # Real column-by-column check (via model.py's shared compare_rows),
        # including `location` -- an earlier version of this script dropped
        # `location` before comparing and only checked `flow`/`amount`.
        ok = compare_rows(o, r, verbose=False)
        n_ok += ok
        n_fail += not ok
        total_rows += len(orig)
        print(f"{bn[:55]:55s} | {n_locations} locations x {n_variants} time variants = {len(pids)} processes "
              f"| structure={len(structure)} rows | roundtrip={'OK' if ok else 'FAIL'}")

    print(f"\nTotal: {n_ok} succeeded, {n_fail} failed families. Total exchange rows: {total_rows}")


if __name__ == "__main__":
    main()
