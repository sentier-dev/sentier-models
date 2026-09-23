# BAFU Copied-Processes Toolkit — Issue #36

A working prototype prepared for the Brightcon 2026 hackathon, issue #36
("Modelisation of BAFU copied processes"). Nothing in this folder depends on
any cloud/Claude path — it runs on your own machine with the steps below.

## What it does (30-second version)

Most of the BAFU LCI processes in `sentier-inventory` are the same
`reference_product` copied once per country/period (e.g. "Electricity, low
voltage, at grid" is stored as 81 separate per-country copies). This tool
splits every exchange row into two parts:

- **structure** — which flow items exist, what unit they're in (stored once
  per family)
- **values** — how much of each item exists for a given country/period
  (numeric, small)

and proves this reconstructs the original data **losslessly** (round-trip),
both against our own code and against the real
`sentier-brightway` -> `bw2calc` chain.

## Who built this

Özge Özkılınç (oox@ubu, University of Burgos) — PI.

## AI tool used, and how the output was checked

Built with **Claude** (Anthropic), used throughout for design, coding, and
iteration on this toolkit. Every claim the toolkit makes is backed by a
runnable check rather than taken on trust from the model:

- **Round-trip correctness** — `full_run.py` rebuilds all 766 copied-process
  families from the compact structure/values model and diffs every row
  against the original `exchanges.parquet`: 766/766 match exactly, 0
  failures (see "Step 3" below).
- **Real LCA-engine cross-check** — `validate/compare_multi.py` rebuilds a
  `sentier-brightway` data cache with our model and compares real `bw2calc`
  method scores against the untouched original: they match exactly (Step 4).
- **Independent third-party data** — the same model was applied to a
  completely separate `.bw2package` export (see "Based on" below) with no
  code changes, and the round-trip held there too — evidence this isn't a
  fit to our own data's quirks.
- **Forecasting stretch goal reported honestly** — `forecast_superstructure.py`'s
  leave-one-out results are reported with both a naive baseline and
  precision/recall/F1 (not just MAE, which can be misleading on sparse
  data), including where the method is still weak (e.g. extreme outlier
  countries) — see REPORT.md.

## Based on

- **Method** — the MILP forecasting step (`forecast_superstructure.py`)
  adapts the superstructure / big-M disjunctive-programming pattern from
  Edgar, Himmelblau & Lasdon, *Optimization of Chemical Processes*, 2nd ed.
  (Sec. 9.6, Example 14.6) to a data-reconstruction problem instead of a
  chemical flowsheet. This is a copyrighted textbook: only the modeling
  *technique* is referenced here (as code + a written explanation in
  REPORT.md), no text or figures from the book are reproduced or
  redistributed.
- **Independent validation data** — [`electricity_grids_inventory.bw2package`](https://lci-electricity-93d31c.gitlab.io/electricity_grids_inventory.bw2package),
  from Eaternity's [lci-electricity](https://lci-electricity-93d31c.gitlab.io/index.html)
  project (a separate, non-Sentier project also built on BAFU/UVEK 2026
  data). Used only locally to validate the round-trip model against
  independent data — **not redistributed or committed here**, see the
  project's own page for the file. Licence: data CC-BY-4.0, code
  Apache-2.0 (per the project's own page); BAFU-derived output there
  carries attribution to "Life Cycle Inventory database of the Swiss
  Federal Administration, BAFU 2026."
- **Primary data** — `sentier-inventory` (MIT licence, see its own repo),
  sourced from the Life Cycle Inventory database of the Swiss Federal
  Administration (BAFU:2026). Not committed here either — see "Setup"
  below, you clone it yourself. `sentier-inventory`'s own README states its
  content is MIT; the underlying BAFU database's own redistribution terms
  aren't independently restated beyond that, so if you plan to redistribute
  derived data further, it's worth confirming with BAFU/sentier-dev
  directly rather than assuming.

## Setup

```bash
# 1) Clone sentier-inventory INSIDE this folder (same level as bafu-copied-processes/'s own contents)
cd bafu-copied-processes
git clone https://github.com/sentier-dev/sentier-inventory

# 2) Python dependencies
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The folder layout should now look like this:

```
bafu-copied-processes/
  sentier-inventory/     <- the data repo you just cloned
  src/
  schema/
  validate/
  requirements.txt
```

If you cloned the data somewhere else, every script accepts either a
`--data-root /your/path` flag or an environment variable:

```bash
export BAFU_DATA_ROOT=/your/path/sentier-inventory
```

## Quick start — one compact command

`src/bafu.py` is a single front door for everything below (Steps 1–3d), so
you don't have to remember or run six separate scripts:

```bash
cd src
python bafu.py              # runs every step in order, with defaults (~2 min total)
python bafu.py classify     # ...or just one step, e.g. Step 1
python bafu.py forecast --reference-product "Natural gas, at production onshore"
python bafu.py <step> -h    # see that step's own options
```

Available steps: `classify` (1), `validate` (2), `fullrun` (3), `timecheck`
(3b), `schema` (3c), `forecast` (3d) — same names as the section headers
below. Each one still works as its own standalone script too
(`python classify.py`, `python forecast_superstructure.py --reference-product ...`,
etc.); `bafu.py` just wraps them, it doesn't replace them. The sections below
describe each step in more detail, including Step 4 (real `bw2calc`
validation), which is a separate, heavier process and stays outside `bafu.py`
on purpose (its own venv, see Step 4).

Every run of `bafu.py` also saves a plain-text copy of everything it printed
to `reports/bafu_report_<step-or-all>_<timestamp>.txt` (next to `src/`), so
you have a ready-made report to paste into a write-up or attach as evidence,
without copy-pasting from the terminal. Pass `--no-report` to skip that.

## Step 1 — Classify all "copied-process" families

```bash
cd src
python classify.py
```

Output: a bucket breakdown on the console (`trivial-template` /
`template-with-variation` / `variable-mix`) plus
`bafu-copied-processes/data/families.parquet` and `families.csv`.

## Step 2 — Validate the model on one family

```bash
python model.py
```

Runs the round-trip test on 4 example families (easy/medium/hard cases) and
prints the compression statistics.

## Step 3 — Validate the model on ALL families

```bash
python full_run.py
```

Rebuilds every classified family and checks that row counts match, then
prints the total structure/value row ratio.

## Step 3b — The time axis (the "adapted for time" part of the issue)

```bash
python model_time.py
```

Adds the 32 time-tagged processes in the electricity sector (winter/summer
2018) as a second axis (`time_tag`) and validates the round-trip.

## Step 3d — Forecast a held-out member via superstructure optimization (stretch goal)

```bash
python forecast_superstructure.py --reference-product "Electricity, production mix"
```

Treats `structure.parquet`'s technology catalog for a family as a
**superstructure** (in the sense of Edgar, Himmelblau & Lasdon,
*Optimization of Chemical Processes*, 2nd ed., Sec. 9.6 "Disjunctive
Programming" and Example 14.6 "Reaction Synthesis via MINLP"): a binary
variable per candidate technology (used or not) and a continuous variable
for its share, linked by the same big-M constraint the book uses for
flowsheet synthesis (`x <= U*y`), plus a mass-balance constraint (shares
sum to 1) and bounds learned from the other family members.

For every real location in the family, it hides that location's data,
solves the MILP using only the *other* locations, and compares the result
against the real (held-out) values -- a leave-one-out validation of
"can we synthesize a member we don't have data for." Needs `pulp` (in
`requirements.txt`; bundles the open-source CBC solver, nothing else to
install). Use `--target-location XX` to see one country's full
slot-by-slot comparison instead of the summary table.

By default the objective anchors on same-region neighbors first (`geo.py`
resolves a location to Europe/Asia/Africa/North America/Latin
America/Oceania, falling back to the global mean per slot when there are
fewer than 3 same-region training points) rather than the whole family's
mean -- this measurably helps regional outliers like France's
nuclear-heavy mix. Pass `--anchor global` to use the plain whole-family
mean instead (the original version, useful for comparison).

## Step 3c — Generate real schema files

```bash
python build_demo_schema_files.py
```

Produces real `structure.parquet` / `values.parquet` files matching
`schema/structure.yaml` and `schema/values.yaml` (default: the
02-electricity sector; override with `--sector`). Output:
`bafu-copied-processes/demo-schema-output/data/<sector>/`.

## Step 4 (optional) — Validate against the real sentier-brightway -> bw2calc chain

This step proves the model doesn't just pass our own round-trip test, but
also doesn't change a single number in the real Brightway calculation
chain. We recommend a separate venv, since `sentier-brightway` installs its
own pinned `bw2calc`/`bw2data` versions:

```bash
cd ..                                   # back to the bafu-copied-processes/ root
python3 -m venv sb-venv
source sb-venv/bin/activate
pip install git+https://github.com/sentier-dev/sentier-brightway

# Download and export the original data (untouched reference)
sentier-brightway files --out ./bafu-files-original

# Find sentier-brightway's own cache (the pinned data it downloaded) and
# make a WORKING copy of it (the path is printed by the coverage command,
# usually under ~/.cache/sentier-brightway/<hash>/):
cp -r ~/.cache/sentier-brightway/<hash> ./data-root

# Rebuild the sentier-inventory copy inside that working copy with our model:
python ../validate/rebuild_inventory.py --data-root ./data-root/sentier-inventory

# Export the rebuilt data:
sentier-brightway files --out ./bafu-files-ours --data-root ./data-root

# Compare the two (bw2calc scores should match exactly):
python ../validate/compare_multi.py --original ./bafu-files-original --ours ./bafu-files-ours

# To see all method scores for a single process:
python ../validate/demo_score.py --files-dir ./bafu-files-original --process "Electricity, low voltage, at grid" --location CH
```

If `compare_multi.py` prints `OK` on every row, the model doesn't change the
real LCA result at all.

## Files

| File | What it does |
|---|---|
| `src/bafu.py` | Single compact entry point — wraps all the steps below, run all at once or one at a time |
| `src/classify.py` | Scans the whole dataset, sorts every family into one of 3 buckets |
| `src/model.py` | `build_family()` / `reconstruct()` — the core structure+values model |
| `src/full_run.py` | Bulk validation + statistics across all families |
| `src/forecast_superstructure.py` | MILP-based forecasting for a held-out member (superstructure optimization, stretch goal) |
| `src/geo.py` | Location code → region bucket, used by `--anchor regional` |
| `src/model_time.py` | Extension that adds the time axis (`time_tag`) |
| `src/build_demo_schema_files.py` | Generates real `structure.parquet`/`values.parquet` |
| `schema/structure.yaml`, `schema/values.yaml` | Proposed PR schema (matches sentier-inventory's own `schema/*.yaml` style) |
| `validate/rebuild_inventory.py` | Rebuilds a `sentier-brightway` cache copy using our model |
| `validate/demo_score.py`, `validate/compare_multi.py` | Compares real `bw2calc` scores between original and rebuilt data |

## Notes

- `classify.py`, `model.py`, `model_time.py`, and `build_demo_schema_files.py`
  don't depend on any absolute path: they read the data root from either the
  `BAFU_DATA_ROOT` environment variable, a `--data-root` argument, or (if
  neither is given) the `sentier-inventory/` folder at this toolkit's own
  root.
- The scripts in `validate/` require `bw2calc`/`bw_processing` — these come
  automatically with the `sentier-brightway` install, so we recommend running
  them in a separate venv (Step 4).
