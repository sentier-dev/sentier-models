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

VALUE_COLS = ["flow", "amount", "uncertainty_type", "loc", "scale", "minimum", "maximum", "link_location"]
STRUCT_COLS = ["flow_name", "direction", "flow_type", "unit"]

# Columns that must match exactly (as strings) for a round-trip to count as OK.
_EXACT_COLS = ["flow", "flow_name", "flow_type", "direction", "unit", "location"]
# Columns compared numerically (np.allclose, NaN-safe).
_NUMERIC_COLS = ["amount", "uncertainty_type", "loc", "scale", "minimum", "maximum"]


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


# Cache of (groupby-by-reference_product-of-P, groupby-by-reference_product-of-E)
# keyed by object identity, so that a loop over hundreds of families (e.g.
# full_run.py, or check_roundtrip called once per family) does one O(n log n)
# groupby instead of one O(n) `isin` scan of the *entire* exchanges table per
# family per call (which is what made an earlier version of this loop take
# several minutes for ~770 families x ~390k exchange rows). Single-slot: only
# ever holds the most recently seen (P, E) pair.
_GROUP_CACHE = {"key": None, "groups": None}


def _grouped(P, E):
    key = (id(P), id(E))
    if _GROUP_CACHE["key"] != key:
        rp_of_process = P.set_index("process_id")["reference_product"]
        e_rp = E["process_id"].map(rp_of_process)
        _GROUP_CACHE["key"] = key
        _GROUP_CACHE["groups"] = (P.groupby("reference_product"), E.groupby(e_rp))
    return _GROUP_CACHE["groups"]


def _family_slice(P, E, reference_product):
    """Fast equivalent of P[P.reference_product == rp] / E[E.process_id.isin(pids)],
    via the cached groupby in _grouped() instead of a fresh full-table scan."""
    p_by_rp, e_by_rp = _grouped(P, E)
    try:
        procs = p_by_rp.get_group(reference_product).copy()
    except KeyError:
        procs = P.iloc[0:0].copy()
    try:
        ex = e_by_rp.get_group(reference_product).copy()
    except KeyError:
        ex = E.iloc[0:0].copy()
    return procs, ex


def build_family(P, E, reference_product):
    """
    NOTE on location: an exchange has TWO distinct location concepts, which
    this function keeps separate (they used to be conflated -- see git
    history / PR review):
      - `location`      -- which family MEMBER (process) this row belongs
                            to. Always the process's own location. This is
                            the join/grouping key used everywhere else
                            (leave-one-out targets, regional anchoring, ...).
      - `link_location`  -- the exchange's OWN location field as stored in
                            exchanges.parquet (e.g. which country a specific
                            technosphere input is actually sourced from).
                            For most rows this equals the process's own
                            location, but sentier-inventory has ~62k rows
                            dataset-wide (~34k within just these copied-
                            process families) where it genuinely differs --
                            e.g. a process located in "NG" (Nigeria) whose
                            upstream electricity input is itself linked to
                            "NG" while a generic infrastructure input is
                            linked to "RER" (a European proxy dataset).
                            Overwriting this with the process's own location
                            (the old behaviour) silently destroyed that
                            information. It is preserved verbatim here.
    """
    procs, ex = _family_slice(P, E, reference_product)
    loc_of = dict(zip(procs["process_id"], procs["location"]))
    ex = ex.drop(columns=["sector"])
    ex = ex.rename(columns={"location": "link_location"})
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
    out = out.rename(columns={"link_location": "location"})
    out = out[["process_id", "flow", "flow_name", "flow_type", "direction",
               "amount", "unit", "uncertainty_type", "loc", "scale", "minimum", "maximum", "location"]]
    return out


def compare_rows(o, r, exact_cols=None, numeric_cols=None, verbose=True):
    """Real, column-by-column, NaN-safe comparison of two already-aligned
    (same row order) DataFrames. Shared by check_roundtrip() and by the other
    scripts that round-trip their own family variant (model_time.py's time
    axis, build_demo_schema_files.py's demo export) so there is one place
    that defines "round-trip ok" instead of each script hand-rolling its own
    (weaker) check -- e.g. checking only `amount`, or dropping `location`
    before comparing, as earlier versions of those scripts did.

    exact_cols/numeric_cols default to model.py's own column lists; pass
    a different list for a caller whose columns differ (e.g. model_time.py
    additionally has `time_tag`).
    """
    exact_cols = _EXACT_COLS if exact_cols is None else exact_cols
    numeric_cols = _NUMERIC_COLS if numeric_cols is None else numeric_cols

    ok = len(o) == len(r)
    if not ok:
        if verbose:
            print(f"  MISMATCH in row count: original={len(o)} rebuilt={len(r)}")
        return False

    for col in exact_cols:
        # NaN-safe: astype(str) does not reliably turn a missing value into
        # the literal text "nan" across pandas string/object dtypes (and
        # NaN != NaN even when it did), so a null on both sides must be
        # treated as equal explicitly.
        oa, rb = o[col], r[col]
        both_null = oa.isna().values & rb.isna().values
        a, b = oa.astype(str).values, rb.astype(str).values
        same = (a == b) | both_null
        if not same.all():
            ok = False
            if verbose:
                print(f"  MISMATCH in column {col} ({(~same).sum()} rows)")
    for col in numeric_cols:
        a, b = o[col].astype(float).values, r[col].astype(float).values
        if not np.allclose(a, b, equal_nan=True, rtol=1e-9):
            ok = False
            if verbose:
                print(f"  MISMATCH in column {col}")
    return ok


def check_roundtrip(P, E, reference_product, verbose=True, return_stats=False):
    """Content-level round-trip check against already-loaded P/E (no reload
    per family -- full_run.py calls this in a loop over 766 families, and a
    fresh load() per call would be both slow and pointless). Checks real
    column-by-column equality (including the exchange's own `location`,
    see build_family's docstring), not just row counts: a row-count match
    alone can hide a silently-wrong column.

    Uses the same cached groupby as build_family() (via _family_slice) rather
    than a fresh `isin` scan of the whole exchanges table, and -- when
    return_stats=True -- returns the row/structure counts it already computed
    so a caller looping over many families (full_run.py) does not need to
    call build_family() a second time just to get those numbers."""
    procs, original = _family_slice(P, E, reference_product)
    original = original.drop(columns=["sector"]).reset_index(drop=True)

    structure, values, proc_meta = build_family(P, E, reference_product)
    rebuilt = reconstruct(structure, values, proc_meta, reference_product)

    # `flow` is included in the sort key (not just process_id/flow_name/
    # direction) so that two rows sharing the same (process_id, flow_name,
    # direction) -- e.g. the same flow appearing via two different links --
    # still sort into a matching, deterministic order on both sides.
    key = ["process_id", "flow_name", "direction", "flow"]
    o = original.sort_values(key).reset_index(drop=True)
    r = rebuilt.sort_values(key).reset_index(drop=True)

    ok = compare_rows(o, r, verbose=verbose)

    if verbose:
        n_structure_rows = len(structure)
        n_value_rows = len(values)
        n_original_rows = len(original)
        n_locations = procs["location"].nunique()
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
    if return_stats:
        return ok, len(original), len(structure), len(values)
    return ok


def validate_roundtrip(reference_product, verbose=True, repo=None):
    P, E = load(repo)
    return check_roundtrip(P, E, reference_product, verbose=verbose)


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
