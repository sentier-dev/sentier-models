"""
Produces real structure.parquet + values.parquet for one sector
(02-electricity by default), matching schema/structure.yaml and
schema/values.yaml. Families are grouped by TIME-STRIPPED reference_product,
so a family can vary by location, time, or both. Round-trips against the
original exchanges.parquet to prove correctness (full column-by-column
check, including the exchange's own `location`, via model.py's shared
compare_rows -- not just row counts / amount), then writes real parquet
files.

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.
See README.md for setup.
--------------------------------------------------------------------------
"""
import argparse
import hashlib
import os
import re
import sys

import pandas as pd

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)
from model import compare_rows  # noqa: E402

TOOLKIT_ROOT = os.path.dirname(THIS_DIR)
DEFAULT_REPO = os.path.join(TOOLKIT_ROOT, "sentier-inventory")
REPO = os.environ.get("BAFU_DATA_ROOT", DEFAULT_REPO)

STRUCT_COLS = ["flow_name", "direction", "flow_type", "unit"]
VALUE_COLS = ["flow", "amount", "uncertainty_type", "loc", "scale", "minimum", "maximum", "link_location"]
TIME_PAT = re.compile(r",?\s*(winter|summer)\s*(\d{4})", re.IGNORECASE)


def split_time(name):
    m = TIME_PAT.search(name)
    if not m:
        return name, None
    return TIME_PAT.sub("", name).strip(), f"{m.group(1).lower()}-{m.group(2)}"


def family_id_of(base_name):
    return hashlib.sha1(base_name.encode()).hexdigest()[:12]


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Build real structure.parquet/values.parquet for one sector.")
    ap.add_argument("--data-root", default=None,
                     help="Path to your local sentier-inventory clone. "
                          "Default: BAFU_DATA_ROOT env var, else ./sentier-inventory next to this toolkit.")
    ap.add_argument("--sector", default="02-electricity", help="Sector folder name (default: 02-electricity)")
    ap.add_argument("--out", default=None,
                     help="Output folder for structure.parquet/values.parquet "
                          "(default: bafu-copied-processes/demo-schema-output/data/<sector>/)")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    repo = args.data_root or REPO
    sector = args.sector
    out_dir = args.out or os.path.join(TOOLKIT_ROOT, "demo-schema-output", "data", sector)
    os.makedirs(out_dir, exist_ok=True)

    d = os.path.join(repo, "data", sector)
    if not os.path.isdir(d):
        raise FileNotFoundError(
            f"Sector folder not found: {d}\n"
            f"Clone sentier-inventory first and check --data-root / BAFU_DATA_ROOT."
        )
    P = pd.read_parquet(os.path.join(d, "processes.parquet"))
    E = pd.read_parquet(os.path.join(d, "exchanges.parquet"))

    # Key on reference_product (like classify.py/model.py), not on `name` --
    # in this dataset the two are identical for every process (verified
    # across all 11,947 processes dataset-wide), so this is behaviour-
    # preserving today, but keeps the family-key convention consistent with
    # model.py instead of silently depending on name==reference_product.
    P[["base_name", "time_tag"]] = P["reference_product"].apply(lambda n: pd.Series(split_time(n)))

    member_counts = P.groupby("base_name").apply(
        lambda g: g[["location", "time_tag"]].drop_duplicates().shape[0],
        include_groups=False,
    )
    family_base_names = member_counts[member_counts > 1].index.tolist()
    print(f"{len(family_base_names)} families in {sector} (location and/or time)")

    struct_chunks, value_chunks = [], []
    n_ok, n_fail = 0, 0
    covered_pids = set()

    for bn in family_base_names:
        fid = family_id_of(bn)
        procs = P[P["base_name"] == bn]
        pids = procs["process_id"].tolist()
        covered_pids.update(pids)
        key_of = dict(zip(procs["process_id"], zip(procs["location"], procs["time_tag"])))

        # Preserve the exchange's own TRUE location under `link_location`
        # instead of overwriting it with the process's own location (the
        # process's own location is kept, separately, as `location` -- see
        # model.py's build_family docstring for why the two differ for
        # ~62k rows dataset-wide).
        ex = E[E["process_id"].isin(pids)].drop(columns=["sector"], errors="ignore").copy()
        ex = ex.rename(columns={"location": "link_location"})
        ex["location"] = ex["process_id"].map(lambda p: key_of[p][0])
        ex["time_tag"] = ex["process_id"].map(lambda p: key_of[p][1])

        structure = ex[STRUCT_COLS].drop_duplicates().reset_index(drop=True)
        structure.insert(0, "slot_id", range(len(structure)))
        structure.insert(0, "family_id", fid)

        values = ex.merge(structure.drop(columns=["family_id"]), on=STRUCT_COLS, how="left")
        values = values[["process_id", "location", "time_tag", "slot_id"] + VALUE_COLS]
        values.insert(0, "family_id", fid)

        merged = values.merge(structure, on=["family_id", "slot_id"], how="left")
        rebuilt = merged[["process_id"] + STRUCT_COLS + VALUE_COLS].copy()
        rebuilt = rebuilt.rename(columns={"link_location": "location"})

        orig = E[E["process_id"].isin(pids)].drop(columns=["sector"], errors="ignore")
        # `flow` in the sort key, same reasoning as model.py's check_roundtrip.
        key = ["process_id", "flow_name", "direction", "flow"]
        o = orig.sort_values(key).reset_index(drop=True)
        r = rebuilt.sort_values(key).reset_index(drop=True)
        # Real column-by-column check (model.py's shared compare_rows),
        # including `location` -- an earlier version of this script dropped
        # `location` before comparing and only checked `amount`.
        ok = compare_rows(o, r, verbose=False)
        n_ok += ok
        n_fail += not ok

        struct_chunks.append(structure)
        value_chunks.append(values)

    structure_all = pd.concat(struct_chunks, ignore_index=True)
    values_all = pd.concat(value_chunks, ignore_index=True)

    structure_all.to_parquet(os.path.join(out_dir, "structure.parquet"), index=False)
    values_all.to_parquet(os.path.join(out_dir, "values.parquet"), index=False)

    print(f"round-trip: {n_ok} ok, {n_fail} failed")
    print(f"structure.parquet: {len(structure_all)} rows -> {os.path.join(out_dir, 'structure.parquet')}")
    print(f"values.parquet:    {len(values_all)} rows -> {os.path.join(out_dir, 'values.parquet')}")
    print(f"processes covered by families: {len(covered_pids)} / {len(P)} in this sector")
    print()
    print("structure.parquet sample:")
    print(structure_all.head(4).to_string(index=False))
    print()
    print("values.parquet sample:")
    print(values_all.head(4).to_string(index=False))


if __name__ == "__main__":
    main()
