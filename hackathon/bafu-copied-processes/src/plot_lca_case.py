"""
Illustrates WHY the leave-one-out forecast matters for LCA, using a real,
concrete case: France's electricity production mix.

The story this chart tells:
  - BAFU has REAL data for France (63% nuclear -- a real outlier vs the
    rest of the family).
  - If you had NO data for France (the situation this tool is meant for),
    a global-average-anchored forecast badly underestimates nuclear and
    invents a natural-gas dependency that isn't really there.
  - A regional-anchored forecast (same-region neighbors) gets closer on
    nuclear and correctly drops the natural-gas share to zero -- but BOTH
    forecasts invent an ~17% lignite share that doesn't exist in the real
    data. That's an honest limitation, called out directly on the chart.

This is what an LCA analyst actually cares about: not the F1/MAE numbers
in the abstract, but "if my product's supply chain needs France's
electricity mix and BAFU has no data for it, what would this tool hand
me instead of a blank cell or a naive world average -- and where would
it still mislead me?"

--------------------------------------------------------------------------
PORTABLE VERSION -- runs on your own machine, no Claude/cloud paths.
See README.md for setup (clone sentier-inventory, pip install -r requirements.txt).
Needs matplotlib in addition to the toolkit's own requirements.txt.
--------------------------------------------------------------------------
"""
import os
import sys

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, Rectangle

matplotlib.use("Agg")

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)
from model import load, build_family                    # noqa: E402
from forecast_superstructure import solve_member         # noqa: E402

# Validated categorical palette (dataviz skill, references/palette.md) --
# slots 1/2/3 (blue/orange/aqua) validate all-pairs in both light and dark.
COLOR_REAL = "#2a78d6"
COLOR_GLOBAL = "#eb6834"
COLOR_REGIONAL = "#1baf7a"
COLOR_TEXT = "#0b0b0b"
COLOR_MUTED = "#52514e"
COLOR_GRID = "#e4e2dd"
COLOR_SURFACE = "#fcfcfb"
COLOR_ZEBRA = "#f2f1ee"
COLOR_CALLOUT = "#9a3b1f"  # muted dark-orange ink for callout text/arrows

# Short, presentation-friendly labels for the technologies we highlight.
LABELS = {
    "Electricity, nuclear, at power plant pressure water reactor": "Nuclear",
    "Electricity, from natural gas": "Natural gas",
    "Electricity, lignite, at power plant": "Lignite",
    "Electricity, at wind farm": "Wind",
    "Electricity, hydropower, at run-of-river power plant": "Hydro (run-of-river)",
    "Electricity, hydropower, at reservoir power plant": "Hydro (reservoir)",
    "Electricity, production mix photovoltaic, at plant": "Solar PV",
    "Electricity, oil, at power plant": "Oil",
}


def rounded_hbar(ax, y_center, length, height, color, radius, zorder=2):
    """A horizontal bar with a rounded right (data) end and a square left
    (baseline) end -- per the dataviz skill's bar mark spec. Drawn as a
    fully-rounded box plus a plain square patch over the left half, which
    squares the baseline edge back off without needing a custom path."""
    if length <= 0:
        return
    r = min(radius, length / 2, height / 2)
    box = FancyBboxPatch(
        (0, y_center - height / 2), length, height,
        boxstyle=f"round,pad=0,rounding_size={r}",
        linewidth=0, facecolor=color, zorder=zorder, mutation_aspect=None,
    )
    ax.add_patch(box)
    square = Rectangle(
        (0, y_center - height / 2), max(length - r, 0.0001), height,
        linewidth=0, facecolor=color, zorder=zorder,
    )
    ax.add_patch(square)


def main(reference_product="Electricity, production mix", target_location="FR",
         data_root=None, out_path=None):
    P, E = load(data_root)
    structure, values, proc_meta = build_family(P, E, reference_product)

    r_global = solve_member(structure, values, proc_meta, target_location, anchor="global")
    r_regional = solve_member(structure, values, proc_meta, target_location, anchor="regional")

    cg = r_global["comparison"].set_index("slot_id")
    cr = r_regional["comparison"].set_index("slot_id")

    rows = []
    for slot_id, row in cg.iterrows():
        name = row["flow_name"]
        if name not in LABELS:
            continue
        rows.append({
            "label": LABELS[name],
            "real": row["real_amount"],
            "global": row["solved_amount"],
            "regional": cr.loc[slot_id, "solved_amount"] if slot_id in cr.index else 0.0,
        })
    rows.sort(key=lambda d: d["real"], reverse=True)

    labels = [r["label"] for r in rows]
    real_vals = [r["real"] * 100 for r in rows]
    global_vals = [r["global"] * 100 for r in rows]
    regional_vals = [r["regional"] * 100 for r in rows]
    n = len(labels)

    y = np.arange(n)[::-1]  # top-to-bottom = largest real share first
    h = 0.24          # bar thickness
    gap = 0.03         # surface gap between the three bars in a group
    xmax = max(real_vals) * 1.18

    fig, ax = plt.subplots(figsize=(9.5, 7.1), dpi=200)
    fig.patch.set_facecolor(COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    # Zebra bands behind each category row, for scanability across 3 bars.
    for yi in y[::2]:
        ax.add_patch(Rectangle(
            (0, yi - 1.5 * h - gap), xmax, 3 * h + 2 * gap,
            facecolor=COLOR_ZEBRA, edgecolor="none", zorder=0,
        ))

    rounding = 0.55
    for yi, real, glob, reg in zip(y, real_vals, global_vals, regional_vals):
        rounded_hbar(ax, yi + h + gap, real, h, COLOR_REAL, rounding)
        rounded_hbar(ax, yi, glob, h, COLOR_GLOBAL, rounding)
        rounded_hbar(ax, yi - h - gap, reg, h, COLOR_REGIONAL, rounding)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=11, color=COLOR_TEXT)
    ax.set_ylim(y.min() - 1.8 * h - gap, y.max() + 1.8 * h + gap)
    ax.set_xlim(0, xmax)
    ax.set_xlabel("Share of France's electricity production mix (%)", fontsize=10.5, color=COLOR_MUTED)

    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(COLOR_GRID)
    ax.xaxis.grid(True, color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, labelsize=10)

    # Direct labels only on the "Real" bars (the series the whole story is
    # anchored to) -- selective, per the dataviz skill's labeling rule.
    for yi, v in zip(y, real_vals):
        ax.text(v + xmax * 0.012, yi + h + gap, f"{v:.0f}%", va="center",
                 fontsize=9.5, color=COLOR_TEXT, fontweight="600")

    # --- Two callouts carry the honest story, right where the eye already is ---
    nuclear_i = labels.index("Nuclear")
    lignite_i = labels.index("Lignite")
    y_nuclear, y_lignite = y[nuclear_i], y[lignite_i]

    ax.annotate(
        "Real value is 63% --\nboth forecasts fall well short",
        xy=(global_vals[nuclear_i] + 1, y_nuclear),
        xytext=(38, y_nuclear + 1.55),
        fontsize=9.3, color=COLOR_CALLOUT, ha="left",
        arrowprops=dict(arrowstyle="-", color=COLOR_CALLOUT, lw=1.1,
                         connectionstyle="arc3,rad=0.15"),
    )
    ax.annotate(
        "Both forecasts invent a ~17% lignite\nshare -- real value is 0%",
        xy=(max(global_vals[lignite_i], regional_vals[lignite_i]) + 1, y_lignite),
        xytext=(25, y_lignite + 1.2),
        fontsize=9.3, color=COLOR_CALLOUT, ha="left",
        arrowprops=dict(arrowstyle="-", color=COLOR_CALLOUT, lw=1.1,
                         connectionstyle="arc3,rad=-0.15"),
    )

    # Legend as a single horizontal row, in its own reserved band above the
    # axes -- clear of every bar.
    handles = [
        Rectangle((0, 0), 1, 1, facecolor=COLOR_REAL),
        Rectangle((0, 0), 1, 1, facecolor=COLOR_GLOBAL),
        Rectangle((0, 0), 1, 1, facecolor=COLOR_REGIONAL),
    ]
    fig.legend(
        handles, ["Real (BAFU data)", "Forecast -- global anchor", "Forecast -- regional anchor"],
        loc="upper center", bbox_to_anchor=(0.5, 0.855), ncol=3,
        frameon=False, fontsize=10, labelcolor=COLOR_TEXT,
        handlelength=1.2, handleheight=1.2, columnspacing=1.6,
    )

    fig.suptitle(
        "Why this matters for LCA: forecasting France's electricity mix\nwhen no real data exists for it",
        fontsize=14.5, fontweight="bold", color=COLOR_TEXT, x=0.012, y=0.985, ha="left",
    )
    fig.text(
        0.012, 0.895,
        "A concrete, honest look at what a data-driven forecast gets right -- and still gets wrong.",
        fontsize=10, color=COLOR_MUTED,
    )
    fig.text(
        0.012, 0.005,
        "Source: bafu-copied-processes/src/forecast_superstructure.py -- leave-one-out on real sentier-inventory data (BAFU LCI, 2026).",
        fontsize=7.5, color=COLOR_MUTED,
    )

    fig.tight_layout(rect=[0.01, 0.035, 0.99, 0.80])

    out_path = out_path or os.path.join(THIS_DIR, "..", "france_lca_case.png")
    out_path = os.path.abspath(out_path)
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    print(f"Written: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
