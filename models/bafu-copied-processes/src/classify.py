"""
Classify all "copied process" families in sentier-inventory (hackathon issue #36).

A family = one reference_product that appears in >1 location.
For each family we check:
  - is the exchange structure (set of flow_name+direction+flow_type) identical
    across every location? -> "template" candidate (near-duplicate copies)
  - if not identical, how much does the structure vary? -> "mix" candidate
    (genuinely different composition per location, e.g. production mixes)

Output: data/families.parquet with one row per family + classification,
plus a printed summary.

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.
See README.md for setup. Data root resolution: BAFU_DATA_ROOT env var,
--data-root flag, or ./sentier-inventory next to this toolkit's src/ folder.
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


def classify(repo=None):
    P, E = load(repo)
    loc_counts = P.groupby("reference_product")["location"].nunique()
    families = loc_counts[loc_counts > 1].index.tolist()

    E_by_pid = E.drop(columns=["location"]).groupby("process_id")

    rows = []
    for rp in families:
        sub = P[P["reference_product"] == rp]
        pids = sub["process_id"].tolist()
        locs = dict(zip(sub["process_id"], sub["location"]))

        structures = {}
        amounts_by_flow = {}
        for pid in pids:
            try:
                g = E_by_pid.get_group(pid)
            except KeyError:
                continue
            key = tuple(sorted(zip(g["flow_name"], g["direction"], g["flow_type"])))
            structures[pid] = key
            for _, row in g.iterrows():
                amounts_by_flow.setdefault(row["flow_name"], {})[locs[pid]] = row["amount"]

        n_locations = len(pids)
        n_distinct_structures = len(set(structures.values()))
        identical_structure = n_distinct_structures == 1

        max_cv = np.nan
        if identical_structure and amounts_by_flow:
            cvs = []
            for flow, by_loc in amounts_by_flow.items():
                vals = np.array(list(by_loc.values()), dtype=float)
                if len(vals) > 1 and np.abs(vals.mean()) > 1e-12:
                    cvs.append(np.std(vals) / abs(np.mean(vals)))
            if cvs:
                max_cv = max(cvs)

        rows.append({
            "reference_product": rp,
            "n_locations": n_locations,
            "n_distinct_structures": n_distinct_structures,
            "identical_structure": identical_structure,
            "max_coefficient_of_variation": max_cv,
            "sector": sub["sector"].iloc[0],
        })

    fam = pd.DataFrame(rows).sort_values("n_locations", ascending=False)

    def bucket(r):
        if not r["identical_structure"]:
            return "variable-mix"
        if pd.isna(r["max_coefficient_of_variation"]) or r["max_coefficient_of_variation"] < 0.15:
            return "trivial-template"
        return "template-with-variation"

    fam["bucket"] = fam.apply(bucket, axis=1)
    return fam


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Classify all copied-process families in sentier-inventory.")
    ap.add_argument("--data-root", default=None,
                     help="Path to your local sentier-inventory clone. "
                          "Default: BAFU_DATA_ROOT env var, else ./sentier-inventory next to this toolkit.")
    ap.add_argument("--out", default=os.path.join(THIS_DIR, "..", "data"),
                     help="Output folder for families.parquet (default: bafu-copied-processes/data/)")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    fam = classify(args.data_root)

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    fam.to_parquet(os.path.join(out_dir, "families.parquet"), index=False)
    fam.to_csv(os.path.join(out_dir, "families.csv"), index=False)

    print(f"Total {len(fam)} copied-process families (reference_product, >1 location)\n")
    print("Bucket distribution:")
    print(fam["bucket"].value_counts())
    print()
    print("Total affected process_id count (sum of n_locations):", fam["n_locations"].sum())
    print()
    for b in ["trivial-template", "template-with-variation", "variable-mix"]:
        sub = fam[fam["bucket"] == b].sort_values("n_locations", ascending=False)
        print(f"--- {b} ({len(sub)} families, {sub['n_locations'].sum()} processes) ---")
        print(sub.head(6)[["reference_product", "sector", "n_locations", "n_distinct_structures", "max_coefficient_of_variation"]].to_string(index=False))
        print()
    print(f"Written: {out_dir}/families.parquet and families.csv")
    return fam


if __name__ == "__main__":
    main()
