"""
Forecasting/backcasting a missing family member via superstructure optimization
(issue #36 stretch goal).

--------------------------------------------------------------------------
WHY THIS EXISTS

Until now, `structure.parquet` (the shared technology catalog for a family,
e.g. "Electricity, production mix") was only ever used to RECORD data that
already existed. This script uses it to SYNTHESIZE a missing or future
member instead -- i.e. a first concrete answer to the issue's stretch goal
("use backcasting/forecasting to add additional time periods for the same
data"), and more generally to fill in a location we have no data for at all.

THE METHOD (grounded in Edgar, Himmelblau & Lasdon, "Optimization of
Chemical Processes", 2nd ed.):

  - Section 9.6 "Disjunctive Programming" gives the big-M pattern for
    "exactly one (or a subset) of several discrete alternatives is active,
    each with its own constraints and cost": a binary y_i and a big-M link
    like  x_i - M*y_i <= 0  that forces the continuous variable to zero
    whenever the alternative isn't selected.
  - Example 14.6 "Reaction Synthesis via MINLP" (a hydrodealkylation
    process flowsheet synthesis problem, after Phimister et al. 1999)
    applies exactly this pattern to a real flowsheet: binary variables
    y_{i,j,k} decide whether component i flows from source node j to
    destination node k, continuous variables F_{i,j,k} are the flow rates,
    linked by F_{i,j,k} - U*y_{i,j,k} <= 0.

Our `structure.parquet` for a family IS a superstructure in exactly this
sense: each slot is a candidate "source" (a technology/upstream process).
A family member (one location x time_tag instance) is a *realization* of
that superstructure: y_slot = 1 if this member uses that technology,
x_slot >= 0 is how much. Historically we only ever recorded y and x after
the fact. This script instead *solves* for them for a member we hold out
(or don't have), using the same variables and linking constraint as the
book's examples, plus:

  - a mass-balance constraint (for "production mix" style families, the
    technosphere shares must sum to ~1, exactly as the book's HDA example
    balances component flows in and out of each node), and
  - bounds learned from the OTHER members of the family (so the solution
    stays inside the range real members actually exhibit).

VALIDATION: leave-one-out. For every real member of a family, we hide its
values, solve the MILP using only the remaining members' statistics, and
compare the solved (y, x) against the real, held-out values. This is the
concrete, testable version of "can we forecast a member we don't have
data for" -- and it's the same idea as the book's own worked example: they
solve a full-scale synthesis MINLP and then check the optimal configuration
against what a real design would look like (Figure E14.6c).

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine.
Requires: pip install pulp  (bundles the open-source CBC MILP solver)
See README.md for setup of the base toolkit (sentier-inventory data root).
--------------------------------------------------------------------------
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import pulp

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)
from model import load, build_family  # noqa: E402
from geo import region_of  # noqa: E402

EPS_ACTIVE = 1e-4          # minimum nonzero amount once a slot is "on" (avoids on-but-~0 degeneracy)
BIG_M_MARGIN = 1.5         # headroom above the max historical value used as each slot's big-M bound
MASS_BALANCE_TOL = 0.02    # how far Sum(x_input) may sit from 1.0 (real data isn't exactly 1.0 either)
MIN_REGIONAL_N = 3         # need at least this many same-region training points before trusting them
                            # over the global mean for a given slot (see geo.py)


def solve_member(structure, values, proc_meta, target_location, anchor="regional", verbose=False):
    """
    Hold out `target_location`, solve for its (y, x) via a MILP built on the
    family's superstructure (structure) and the remaining members' data
    (values, minus target_location), then return a comparison against the
    real, held-out values.

    anchor: "global"   -- objective pulls toward the mean profile of ALL
                           other members (the original version).
            "regional" -- objective pulls toward the mean profile of
                           same-region members first (see geo.py), falling
                           back to the global mean for any slot with fewer
                           than MIN_REGIONAL_N same-region data points.
                           Rationale: a country's electricity mix looks more
                           like its neighbors' than like the world average
                           (hydro-heavy Nordics, coal-heavy Balkans, etc.).
    """
    output_slots = structure[structure["direction"] == "output"]["slot_id"].tolist()
    input_slots = structure[structure["direction"] == "input"]["slot_id"].tolist()

    train = values[values["location"] != target_location]
    real = values[values["location"] == target_location]

    target_region = region_of(target_location)
    if anchor == "regional" and target_region is not None:
        train_locs = train["location"].unique().tolist()
        same_region_locs = {loc for loc in train_locs if region_of(loc) == target_region}
    else:
        same_region_locs = set()

    # Per-slot statistics from every OTHER member (training set only --
    # the target's own values are never used to build the model). "mean"
    # is the reference the objective pulls toward; see anchor= above.
    stats = {}
    for s in input_slots:
        col_all = train[train["slot_id"] == s]["amount"]
        if len(col_all) == 0:
            continue  # never seen in any other member -- not a candidate (see docstring / README)
        col_regional = train[(train["slot_id"] == s) & (train["location"].isin(same_region_locs))]["amount"]
        if len(col_regional) >= MIN_REGIONAL_N:
            ref_mean = float(col_regional.mean())
            ref_source = "regional"
        else:
            ref_mean = float(col_all.mean())
            ref_source = "global"
        stats[s] = {
            "n": len(col_all),
            "min": float(col_all.min()),
            "max": float(col_all.max()),
            "mean": ref_mean,
            "ref_source": ref_source,
        }
    candidate_slots = list(stats.keys())

    if not candidate_slots:
        raise ValueError(f"No candidate slots have training data outside {target_location}.")

    # Plausible active-slot count, learned from training members (soft envelope).
    active_counts = train[train["slot_id"].isin(candidate_slots)].groupby("process_id").size()
    min_active = max(1, int(active_counts.min()) - 1) if len(active_counts) else 1
    max_active = int(active_counts.max()) + 1 if len(active_counts) else len(candidate_slots)

    prob = pulp.LpProblem("bafu_superstructure_forecast", pulp.LpMinimize)

    y = {s: pulp.LpVariable(f"y_{s}", cat="Binary") for s in candidate_slots}
    x = {s: pulp.LpVariable(f"x_{s}", lowBound=0) for s in candidate_slots}
    d = {s: pulp.LpVariable(f"d_{s}", lowBound=0) for s in candidate_slots}  # |x - ref| helper

    # --- Big-M linking constraint (Edgar/Himmelblau & Lasdon, Sec. 9.6 /
    #     Example 14.6: F_{i,j,k} - U*y_{i,j,k} <= 0) ---
    for s in candidate_slots:
        U = max(stats[s]["max"] * BIG_M_MARGIN, EPS_ACTIVE * 10)
        prob += x[s] <= U * y[s], f"bigM_{s}"
        prob += x[s] >= EPS_ACTIVE * y[s], f"minactive_{s}"

    # --- Mass balance: the technosphere shares must sum to ~1 (same role
    #     as the HDA example's node-by-node component balances) ---
    total_input = pulp.lpSum(x[s] for s in candidate_slots)
    prob += total_input <= 1 + MASS_BALANCE_TOL, "mass_balance_upper"
    prob += total_input >= 1 - MASS_BALANCE_TOL, "mass_balance_lower"

    # --- Plausible technology-count envelope, learned from training members ---
    prob += pulp.lpSum(y[s] for s in candidate_slots) >= min_active, "min_active_count"
    prob += pulp.lpSum(y[s] for s in candidate_slots) <= max_active, "max_active_count"

    # --- Objective: land as close as possible to the historical mean
    #     profile of the OTHER members, while staying feasible ---
    for s in candidate_slots:
        ref = stats[s]["mean"]
        prob += d[s] >= x[s] - ref
        prob += d[s] >= ref - x[s]
    prob += pulp.lpSum(d[s] for s in candidate_slots)

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    status = pulp.LpStatus[prob.status]

    solved = {s: (x[s].value() or 0.0) for s in candidate_slots}
    real_by_slot = dict(zip(real["slot_id"], real["amount"]))

    rows = []
    for s in candidate_slots:
        rows.append({
            "slot_id": s,
            "flow_name": structure.set_index("slot_id").loc[s, "flow_name"],
            "real_amount": real_by_slot.get(s, 0.0),
            "solved_amount": solved[s],
            "abs_error": abs(real_by_slot.get(s, 0.0) - solved[s]),
        })
    # slots real target actually used but that had NO training data anywhere
    # else -- structurally impossible to discover from other members
    unseen_but_real = sorted(set(real_by_slot) - set(candidate_slots) - set(output_slots))
    for s in unseen_but_real:
        rows.append({
            "slot_id": s,
            "flow_name": structure.set_index("slot_id").loc[s, "flow_name"],
            "real_amount": real_by_slot.get(s, 0.0),
            "solved_amount": np.nan,
            "abs_error": np.nan,
        })

    comparison = pd.DataFrame(rows).sort_values("real_amount", ascending=False)
    mae = comparison["abs_error"].dropna().mean() if len(comparison) else np.nan

    # Technology-ACTIVATION precision/recall (which slots did we correctly
    # turn on/off) -- a fairer metric than MAE alone on a sparse family,
    # since "predict everything zero" scores well on MAE but is not even a
    # valid recipe (it violates the mass-balance constraint by construction).
    real_active = set(comparison[comparison["real_amount"] > 1e-6]["slot_id"])
    solved_active = set(comparison[comparison["solved_amount"].fillna(0) > 1e-6]["slot_id"])
    tp = len(real_active & solved_active)
    precision = tp / len(solved_active) if solved_active else np.nan
    recall = tp / len(real_active) if real_active else np.nan
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else np.nan

    if verbose:
        n_regional = sum(1 for s in stats.values() if s["ref_source"] == "regional")
        print(f"\n=== {target_location} (region: {target_region}, anchor: {anchor}, status: {status}) ===")
        print(f"candidate slots: {len(candidate_slots)}  |  unseen-elsewhere slots: {len(unseen_but_real)}  "
              f"|  slots anchored on regional data: {n_regional}/{len(candidate_slots)}")
        print(comparison.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
        print(f"MAE (over resolvable slots): {mae:.4f}")
        print(f"Technology activation -- precision: {precision:.3f}  recall: {recall:.3f}  F1: {f1:.3f}")

    return {
        "target_location": target_location,
        "status": status,
        "mae": mae,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n_candidate_slots": len(candidate_slots),
        "n_unseen_elsewhere": len(unseen_but_real),
        "comparison": comparison,
    }


def leave_one_out(reference_product, repo=None, locations=None, anchor="regional", verbose=False):
    P, E = load(repo)
    structure, values, proc_meta = build_family(P, E, reference_product)

    all_locations = proc_meta["location"].tolist()
    targets = locations or all_locations

    results = []
    for loc in targets:
        try:
            r = solve_member(structure, values, proc_meta, loc, anchor=anchor, verbose=verbose)
            results.append(r)
        except ValueError as e:
            print(f"  skipped {loc}: {e}")

    summary = pd.DataFrame([{
        "location": r["target_location"],
        "status": r["status"],
        "mae": r["mae"],
        "precision": r["precision"],
        "recall": r["recall"],
        "f1": r["f1"],
        "candidate_slots": r["n_candidate_slots"],
        "unseen_elsewhere": r["n_unseen_elsewhere"],
    } for r in results])
    return summary, results


def zero_baseline_mae(reference_product, repo=None):
    """MAE of the trivial 'predict every slot as inactive' baseline -- NOT a
    valid recipe (it doesn't sum to 1), included only to show that raw MAE
    alone is a misleading metric on a sparse family (see README)."""
    P, E = load(repo)
    structure, values, proc_meta = build_family(P, E, reference_product)
    input_slots = structure[structure["direction"] == "input"]["slot_id"].tolist()
    v = values[values["slot_id"].isin(input_slots)]
    per_loc = v.groupby("location")["amount"].apply(lambda s: s.abs().sum() / len(input_slots))
    return per_loc.mean()


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Forecast a held-out family member via superstructure MILP optimization "
                     "(leave-one-out validation against real data)."
    )
    ap.add_argument("--data-root", default=None,
                     help="Path to your local sentier-inventory clone. "
                          "Default: BAFU_DATA_ROOT env var, else ./sentier-inventory next to this toolkit.")
    ap.add_argument("--reference-product", default="Electricity, production mix",
                     help='Family to test (default: "Electricity, production mix")')
    ap.add_argument("--target-location", default=None,
                     help="Test a single location verbosely instead of running leave-one-out on all of them.")
    ap.add_argument("--anchor", choices=["regional", "global"], default="regional",
                     help="Objective reference profile: 'regional' (same-region neighbors first, "
                          "falls back to global mean per slot -- default) or 'global' (whole-family "
                          "mean, the original version -- pass this to compare).")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.target_location:
        P, E = load(args.data_root)
        structure, values, proc_meta = build_family(P, E, args.reference_product)
        solve_member(structure, values, proc_meta, args.target_location, anchor=args.anchor, verbose=True)
        return

    print(f"Leave-one-out superstructure forecast: '{args.reference_product}' (anchor: {args.anchor})\n")
    summary, _ = leave_one_out(args.reference_product, args.data_root, anchor=args.anchor, verbose=False)
    print(summary.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    baseline = zero_baseline_mae(args.reference_product, args.data_root)
    print(f"\nMean MAE across {len(summary)} locations : {summary['mae'].mean():.4f}")
    print(f"'Predict every slot inactive' baseline MAE: {baseline:.4f}  "
          f"(NOT a valid recipe -- doesn't sum to 1 -- shown only because raw MAE "
          f"alone is misleading on a sparse family; see README)")
    print(f"\nTechnology-activation precision: {summary['precision'].mean():.3f}   "
          f"(of the technologies we activated, fraction that were really used)")
    print(f"Technology-activation recall   : {summary['recall'].mean():.3f}   "
          f"(of the technologies really used, fraction we correctly activated)")
    print(f"Technology-activation F1       : {summary['f1'].mean():.3f}")


if __name__ == "__main__":
    main()
