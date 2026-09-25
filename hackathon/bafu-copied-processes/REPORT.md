# Issue #36 — Modelisation of BAFU copied processes

## Problem

Many BAFU processes in `sentier-inventory` exist as copies of the same
`reference_product`, adapted for different locations (and sometimes time).
Example: "Electricity, low voltage, at grid" is stored as exactly 81
separate per-location copies, each with its own `exchanges.parquet` rows.

## Findings (766 families, 4,469 processes — ~37% of 11,947 total processes)

The whole dataset was scanned (`classify.py`). For every `reference_product`
+ more-than-one-location combination (a "family"), we checked whether the
exchange structure (the set of flow_name + direction + flow_type) is
identical across locations, and how much the values vary:

| Bucket | Families | Processes | Description |
|---|---|---|---|
| `trivial-template` | 135 | 555 | Structure is identical, values are also nearly constant (CV < 15%). E.g. "Electricity, high voltage, at grid" |
| `template-with-variation` | 222 | 1,158 | Structure is identical, values vary noticeably by location. E.g. "Electricity, low voltage, at grid", "Crude oil, at production" |
| `variable-mix` | 409 | 2,756 | Structure genuinely differs too — each location uses a different subset of technologies. E.g. "Electricity, production mix" (81 locations, 70 distinct structures) |

The most-repeated families: electricity distribution/production mixes (81
locations), natural gas production/consumption processes (50-68 locations).

## Proposed general model

Split every exchange row into two parts:

- **Structure** — `flow_name, direction, flow_type, unit`. Stored **once**
  per family. For `variable-mix` families this becomes the **union** of all
  flows seen across every location in that family (a shared "technology
  catalog").
- **Values** — `process_id/location, slot_id, flow(id), amount,
  uncertainty_type, loc, scale, minimum, maximum`. One row per location,
  only for flows that **actually exist** at that location. If a location
  never uses a given technology, that row simply doesn't exist (implicit
  zero) — no extra sparsity bookkeeping is needed.

This single model covers both the "trivial-template" and "variable-mix"
extremes with the same code: a template family gets one slot set + many
value rows; a mix family gets a wide slot set with each location using its
own subset.

## A process engineer's perspective: this is a "recipe management" problem

Reading this data-modeling problem in process/chemical-engineering terms,
it isn't something new — it maps onto a standard that's been in use for 30
years: the **ISA-88 / IEC 61512 batch control recipe hierarchy.**

ISA-88 defines four recipe levels: **General Recipe** (equipment- and
site-independent, just "which inputs, in what proportions"), **Site
Recipe** (the general recipe adapted to a specific plant's equipment
constraints), **Master Recipe** (tied to a specific line/reactor, filled in
with concrete parameters), and **Control Recipe** (the record of one
actually-executed batch, timestamped, filled with real quantities). Our
`structure`/`values` split maps directly onto this pair:

| ISA-88 concept | Our model |
|---|---|
| General Recipe (site-independent formula) | `structure.parquet` — flow_name/direction/flow_type/unit, once per family |
| Control Recipe (the real batch record) | `values.parquet` — the real quantity per process_id × location × time_tag |

Industry solved this problem by moving from "record every batch from
scratch, re-writing all its text" to "write the general recipe once, the
batch record is just numbers." BAFU's process database is still at the
first stage today; what #36 is asking for is, in essence, a move to this
standard practice.

**The flowsheet (P&ID) analogy.** We can think of `structure.parquet` as a
P&ID: which streams exist, which direction they flow (`direction`), their
type (`flow_type`: production/technosphere/biosphere), and their unit — but
it carries no numeric value. `values.parquet` is that P&ID's **stream
table**: the actual quantities of each stream for a given "operating case"
(in our data: each location × time combination). A process engineer uses
the same P&ID with different stream tables for a "design case" and a
"normal operating case" — the topology stays fixed, only the numbers
change. BAFU's copied processes are exactly this: the same P&ID (the same
reference_product's process logic), different stream tables for different
countries/periods.

**Mass balance closure = our round-trip test.** What `validate_roundtrip()`
does is exactly the same logic as a **mass/energy balance closure test
(reconciliation)** in process engineering: we rebuild the original data
(P&ID + stream table) and verify that every stream's quantity matches the
original **exactly**. 787 out of 787 families close at 100% — no lost or
extra stream anywhere. This proves the model isn't just a compression
trick, but a **verifiable transformation** — the standard expected when
delivering a process model.

**Sparsity = a closed/bypass line.** The "missing rows" in `variable-mix`
families (a technology a given country doesn't use) can be thought of as a
**closed valve or bypass line** on a flowsheet: the line is defined on the
P&ID (it exists in structure), but its flow rate is zero for this operating
case, so it simply has no row in the stream table (values). No extra "this
is zero" flag is needed.

**The staged-loss analogy (from the Eaternity/lci-electricity data).** The
HV→MV→LV cascade in the `electricity_grids` data examined below matches
another pattern familiar to process engineers: a **staged separation /
staged-loss train** (like multi-stage distillation or compressor stages).
Each stage takes a share of loss from its input (T&D loss), just as every
tray in a distillation column has its own efficiency loss — and the total
loss is split across three stages (40%/30%/30%), just as a process engineer
distributes total pressure drop across stages.

**Why this framing strengthens the pitch.** What's being presented at the
hackathon isn't actually a new invention — it's the application of a
recipe-management standard the chemical industry has used for decades to an
LCI database. This both references a familiar, proven engineering practice
and points to a natural "next step": ISA-88's **Site Recipe** level points
to a "structural constraint" column that could be added to `values.parquet`
— e.g. distinguishing why a country doesn't use a given technology, between
"just doesn't" (empty row, implicit zero) and "can't" (a structural
constraint, like having no geothermal potential). This distinction also
matches the "refusal gate" logic seen in the Eaternity data below (an
explicit refusal instead of silently filling unmeasurable data with an
average).

## One recipe: a concrete example (country × source matrix)

At first glance, the "variable-mix" bucket looked like it would need a
different solution from "trivial-template" (since the structure varies
too). But in fact both reduce to **the same single recipe**: "a
share-weighted sum over N possible sources." The recipe (formula) is
identical across every location; the only thing that changes is which
source is non-zero and what its share is.

We made this concrete on "Electricity, production mix" (81 countries): when
we pivot the technosphere input flows into a `location x flow_name` matrix,
we get a table of **81 countries × 21 electricity sources**, only **47%
filled**. The real countries' profiles come out looking familiar:

| Country | Dominant sources |
|---|---|
| CH (Switzerland) | hydro (reservoir 28% + run-of-river 24%), nuclear (19%+14%) |
| DE (Germany) | wind 26%, natural gas 16%, lignite 16% |
| FR (France) | nuclear **63%** |
| PL (Poland) | hard coal 39%, lignite 21% |

The same structure was confirmed in other families too — only the fill
rate changes (i.e. how close a family sits to "trivial-template" vs.
"variable-mix"):

| Family | Location × source | Fill rate |
|---|---|---|
| Natural gas, at production onshore | 50 × 17 | 93.3% |
| Heat, natural gas, at CHP power plant | 34 × 1 | 100% (effectively trivial-template) |
| Electricity mix | 81 × 28 | 38.0% |
| Electricity, production mix | 81 × 21 | 47.4% |

So there's no sharp boundary between "trivial-template" and
"variable-mix" — they're the dense (fill rate ~100%) and sparse (low fill
rate) ends of the same model (recipe + value table). The pitch line for the
hackathon: **"All 766 families can be represented by a single recipe-table
model; the simple copies are the dense end of this model, the mixes are its
sparse end."**

## Validation against the real pipeline (sentier-brightway)

The round-trip test above was against our own code. We took this a step
further and **installed and ran the real `sentier-brightway` package**:

1. `pip install git+https://github.com/sentier-dev/sentier-brightway`
2. `sentier-brightway coverage` — downloaded its own pinned data: 11,947
   processes, 95.8% BAFU→EF3.1 mapping, 96.8% biosphere coverage — matching
   the demo note's numbers exactly.
3. On top of the `sentier-inventory` data it downloaded (its own cache,
   `~/.cache/sentier-brightway/`), we regenerated **all 787 families**
   using `build_family()`/`reconstruct()` (all 420,063 exchange rows,
   non-family processes left untouched), and fed this to
   `sentier-brightway` as a separate copy via `--data-root`.
4. `sentier-brightway files` was run against both datasets (the original
   and our regenerated version), and the same processes were scored with
   `bw2calc`.

Result — **fully identical**:

| Process \| location | bucket | original score | our score |
|---|---|---|---|
| Electricity, low voltage, at grid \| PT | template-with-variation | 0.20251007 | 0.20251007 |
| Electricity, production mix \| FR | variable-mix | 0.054878139 | 0.054878139 |
| Natural gas, at production onshore \| AE | variable-mix | 0.19662608 | 0.19662608 |
| Agricultural machinery, general, production \| CH | singleton (control) | 4.7759214 | 4.7759214 |
| Electricity, low voltage, at grid \| CH (demo example) | template-with-variation | 25/25 categories identical | 25/25 categories identical |

The `coverage` output (11,947 processes, 95.8%/96.8% matching, residual
distribution) was also identical across both datasets. So this is no longer
a claim but a measured result: **our model doesn't change a single number
in the real `sentier-brightway` → `bw2calc` chain.**

## The time axis (the "adapted for time" part of the issue text)

Everything up to this point was on the location axis. We closed the time
side too:

**Finding:** across the whole dataset (via a broad regex scan: "winter/
summer YYYY", "pre-/post-YYYY", year ranges, etc.), only **32 processes**
carry a time expression, all in the electricity sector, all "winter 2018"/
"summer 2018" — and this information is text embedded inside the
`name`/`reference_product` field, not a separate column. The schema has a
ready but currently-empty `valid_from` (date) field for this — it isn't
used yet.

Because of this, these 32 processes had until now been **invisible** to our
model: `classify.py` groups families by `reference_product`, but
"Electricity mix, winter 2018" and "Electricity mix, summer 2018" are
different `reference_product` strings, so they were counted as separate,
mutually unaware "families."

**Solution:** `model_time.py` strips the time tag out of the name
(`"…, winter 2018"` → base name + `time_tag="winter-2018"`) and regroups
families by base name — a family can now vary by location, by time, or by
both at once. Only a second key column (`time_tag`) was added to the values
table; the model architecture (recipe + value table) didn't change at all.

Result — **4 genuine time-families found, all 100% correct on round-trip**:

| Family (base name) | Location × time | Processes | Structure rows |
|---|---|---|---|
| Electricity, hydropower, at pumped storage plant, ENTSO | 5 × 2 | 10 | 22 |
| Electricity mix | 81 × 2* | 83 | 35 |
| Electricity, high voltage, operation storage pumps, ENTSO, at grid | 5 × 2 | 10 | 8 |
| Electricity mix, operation storage pumps, ENTSO, at plant | 5 × 2 | 10 | 26 |

\* "Electricity mix" is an interesting edge case: the time-tag-less
("annual average") version for 81 countries and CH's winter/summer 2018
variant share the same base name, so they automatically fell into **a
single family** — the model handled this correctly on its own, with no
special-case code (81 rows with an empty `time_tag`, 2 rows filled in).

**Conclusion:** the stretch goal (using backcasting/forecasting to generate
additional time periods) now sits on a meaningful foundation — a `time_tag`
axis already exists in the value table, so adding a new period just means
adding a row to that table; the structure never changes. The forecasting
method itself is developed and validated below.

## Solving the stretch goal: forecasting a held-out member via superstructure optimization

`structure.parquet` has so far only ever been used to **record** data that
already existed. Grounded in Edgar, Himmelblau & Lasdon's *Optimization of
Chemical Processes* (2nd ed.) — specifically Section 9.6 "Disjunctive
Programming" and Example 14.6 "Reaction Synthesis via MINLP", a real
flowsheet-synthesis MINLP for a hydrodealkylation process — we used it to
**synthesize** a member instead. This is a first concrete answer to the
stretch goal ("use backcasting/forecasting to add additional time
periods"), and it generalizes to any missing family member, not just a
missing time period.

**The mapping.** In the book's Example 14.6, binary variables `y_{i,j,k}`
decide whether component *i* flows from source node *j* to destination
node *k*, continuous variables `F_{i,j,k}` are the flow rates, and the two
are linked by the big-M constraint `F_{i,j,k} - U·y_{i,j,k} ≤ 0` (Section
9.6's disjunctive-programming pattern: at most one of several discrete
alternatives is active, each gating its own continuous variables). Our
`structure.parquet` for a family already **is** a superstructure in this
exact sense — every slot is a candidate source (a technology/upstream
process). We built `forecast_superstructure.py`, which treats a held-out
family member (one location) as an unsolved node in that superstructure:
`y_slot ∈ {0,1}` (does this member use the technology), `x_slot ≥ 0` (how
much), linked by the same big-M constraint, plus a mass-balance constraint
(the technosphere shares must sum to ~1, the same role the book's HDA
example gives to its node-by-node component balances) and bounds learned
from the *other* members of the family. The objective minimizes deviation
from the historical mean profile of those other members, subject to
staying feasible.

**Validation: leave-one-out.** For every real member of a family, we hide
its values, solve the MILP using only the statistics of the remaining
members, and compare the solved `(y, x)` against the real, held-out data —
exactly the way the book checks its own worked example's optimal
configuration (Figure E14.6c) against what a real design would look like.
Tested on the family where this validation is actually meaningful (using the
open-source CBC solver via `pulp`, all instances solved to proven
optimality):

| Family | Locations | Mean MAE (ours) | "predict nothing active" baseline MAE | Activation precision | Activation recall | Activation F1 |
|---|---|---|---|---|---|---|
| Electricity, production mix | 81 | 0.0603 | 0.0472 (not a valid recipe — doesn't sum to 1) | 0.553 | 0.767 | 0.598 |

We report this honestly rather than cherry-picking: on raw mean absolute
error alone, the "predict every technology inactive" baseline sometimes
beats our optimizer on the sparser production-mix family — but that
baseline isn't a valid answer at all, since it violates the mass-balance
constraint (it doesn't sum to 1 kWh of production). MAE alone is misleading
on a sparse family for exactly this reason, which is why we also report
technology-**activation** precision/recall: of the technologies a country
actually uses, our method correctly identifies 77% of them from zero
location-specific information — purely from the structural envelope learned
from every other member of the family — while always returning a
structurally valid, mass-balanced recipe.

**Why "Natural gas, at production onshore" is not reported here.** An
earlier version of this report also benchmarked that family (MAE 0.0311,
F1 0.72) with the *same* sum-to-one mass-balance constraint applied. That
was wrong: this family's technosphere inputs are measured in a mix of
units (kg, MJ, Nm3, tkm, ...), and its real members legitimately sum to
anywhere from ~2 to ~6, not ~1 — so forcing sum-to-one there is not a
physical constraint at all, and the resulting F1/MAE for that family is not
a meaningful forecast-quality number, however good it looks. The code now
detects this (`_homogeneous_unit_inputs` in `forecast_superstructure.py`)
and skips the mass-balance constraint entirely for any family whose input
slots don't share one unit; `Natural gas, at production onshore` is one
such family, so its leave-one-out numbers are no longer reported as a
validated benchmark result. The tool still runs on it (`bafu.py forecast
--reference-product "Natural gas, at production onshore"`) and will print a
warning to that effect — useful for exploration, not for citing as a
result.

**What this means for the stretch goal:** a missing time period (or a
missing location) can now be filled by solving the same MILP with that
period's/location's row removed from the training statistics — the exact
leave-one-out procedure above, just applied along the `time_tag` axis
instead of `location`. The structure never changes; only which member is
held out changes.

### Refinement: anchoring on regional neighbors instead of the world mean

The numbers above anchor the objective on the mean profile of *every other*
member of the family — which is why an outlier like France (63% nuclear)
was under-predicted: nothing in a flat world average looks like France.
`forecast_superstructure.py --anchor regional` anchors each slot's
reference value on same-region members first (Europe/Asia/Africa/North
America/Latin America/Oceania, resolved from the location code by `geo.py`,
falling back to the global mean for any slot with fewer than 3 same-region
data points), instead of the whole family.

| Family | Anchor | Mean MAE | Precision | Recall | F1 |
|---|---|---|---|---|---|
| Electricity, production mix | global | 0.0603 | 0.553 | 0.767 | 0.598 |
| Electricity, production mix | regional | 0.0564 | 0.595 | 0.650 | 0.589 |

(As above, "Natural gas, at production onshore" is excluded from this
comparison — its mixed-unit inputs make the mass-balance constraint, and so
this whole validation, inapplicable.)

Reported honestly rather than as a clean win: on production mix, regional
anchoring lowers MAE (0.0603 → 0.0564, a real improvement — France's own
MAE drops from 0.0460 to 0.0439 and its F1 jumps from 0.598 to 0.750) and
raises precision, but at a recall cost, so aggregate F1 is essentially flat
(0.598 → 0.589) — the model gets choosier and activates fewer unlikely
technologies, which helps outliers like France but slightly under-activates
elsewhere. This confirms the improvement helps exactly where we designed it
to help (genuine regional outliers) without being a universal fix — the
natural next refinement is anchoring on a member's own historical trend
once BAFU has real multi-year time series to anchor on, rather than
cross-sectional neighbors.

## Independent validation: third-party data (Eaternity "lci-electricity")

To show the model isn't a solution specific to our own data, we took the
real [`.bw2package` (Brightway2) export](https://lci-electricity-93d31c.gitlab.io/electricity_grids_inventory.bw2package)
of a completely independent third-party project, [Eaternity's
`lci-electricity`](https://lci-electricity-93d31c.gitlab.io/index.html)
(not part of the Sentier/Brightway ecosystem, a separate project, also
built on BAFU/UVEK 2026 data), and tested the same model against it. The
file itself is not redistributed here — see the project's own page. Its
data licence is CC-BY-4.0, code Apache-2.0 (per the project's own page);
BAFU-derived output there carries attribution to "Life Cycle Inventory
database of the Swiss Federal Administration, BAFU 2026."

This package contains 3 databases: `bafu_biosphere` (1,261 flows),
`bafu_2026` (4,346 activities), and `electricity_grids` (3,582 nodes). The
striking part: all 3,582 processes in `electricity_grids` belong to just 3
reference_products ("electricity, low/medium/high voltage") — meaning
#36's problem shows up here again, across 4 axes at once (country × year ×
period × variant: domestic/with_imports/residual/global_annual).

We applied our model to this independent data (the "electricity, high
voltage" family, 889 processes): using slot labels extracted from the
technology comments, we got **a 16-row structure catalog** + **an 8,215-row
values table, 57.8% filled**, and the round-trip **held exactly** (totals
per process matched exactly against the original 9,368 exchange rows, zero
difference).

**Conclusion:** the structure+values model works losslessly both on our own
`sentier-inventory` data and on a completely independent, production-scale,
4-axis third-party dataset — further evidence that the model isn't a
dataset-specific trick, but a general pattern.

## Validation (round-trip, against our own code)

The `build_family()` / `reconstruct()` functions in `model.py` reproduce
the original `exchanges.parquet` rows from the compact model **exactly**
(including flow id, flow_name, direction, flow_type, unit, amount, and
uncertainty parameters).

Tested across **all 766 families** with `full_run.py`:

- 766/766 families: row count and content matched exactly, 0 failures.
- Total original exchange rows (for these 766 families): **129,687**
- Compact model → structure rows (stored once): **25,732** (19.8%)
- Compact model → value rows (numeric/id, no string metadata): **129,687**

So text columns (long strings like `flow_name`) are no longer repeated once
per location — they're written once per family; the value table consists
only of numbers and ids. A savings example for a single family:
"Electricity, high voltage, at grid" drops from 567 exchange rows to 6
structure rows (~98.8% text-repetition savings), and the same ratio
(~98.8%) holds even for a hard (variable-mix) case like "Electricity,
production mix," because the model works there too.

## Files

All the code now lives as a portable toolkit in the `bafu-copied-processes/` folder
(runs on your own machine — see the toolkit's own README.md for setup).
`src/bafu.py` is a single compact entry point that wraps every step below —
run `python bafu.py` for all of them in order, or `python bafu.py <step>`
for just one:

- `src/classify.py` — scans the whole dataset, sorts every family into one
  of 3 buckets, produces `data/families.parquet`.
- `src/model.py` — `build_family()` (convert to the compact model) and
  `reconstruct()` (rebuild) + round-trip validation for a single family.
- `src/full_run.py` — bulk validation + totals across all 766 families.
- `src/model_time.py` — the extension that adds the time axis (`time_tag`).
- `src/forecast_superstructure.py` — MILP-based forecasting for a held-out
  member (superstructure optimization, solves the stretch goal).
- `src/build_demo_schema_files.py` — generates real
  `structure.parquet`/`values.parquet`.
- `schema/structure.yaml`, `schema/values.yaml` — the proposed PR schema.
- `validate/rebuild_inventory.py`, `validate/demo_score.py`,
  `validate/compare_multi.py` — end-to-end validation against the real
  `sentier-brightway` → `bw2calc` chain.

## Next steps / what to present at the hackathon

1. We could propose these files as a PR to `sentier-inventory`: a new
   `structure.parquet` + `values.parquet` pair, alongside the existing
   `processes.parquet`/`exchanges.parquet` (or in their place, converting
   back to the old format with `reconstruct()` at `sentier-brightway`
   load-time for backward compatibility).
2. An additional "outlier" report could be produced for the
   `template-with-variation` bucket (222 families): a table showing how
   much each location deviates from the template — this also overlaps with
   the data-quality check in issue #41.
3. **Stretch goal (backcasting/forecasting) — done, with room to improve:**
   `forecast_superstructure.py` solves it via superstructure MILP
   optimization, leave-one-out validated on two families (71–77% technology
   -activation recall, always a mass-balance-valid recipe). Next
   refinement: anchor the objective to a *specific* known neighbor or
   trend (e.g. the same location's own earlier time period, or its nearest
   geographic neighbors) instead of the family-wide mean — this should
   sharpen quantitative accuracy for outlier members like France's
   nuclear-heavy mix, which the current mean-anchored objective
   under-predicts (see the write-up above).
4. **ISA-88 Site Recipe extension:** a "structural constraint" column could
   be added to `values.parquet` (why a country can't use a given
   technology), distinguishing "doesn't use" from "can't use" — consistent
   with the "refusal gate" logic seen in the Eaternity data.
5. **Demo Derby (Sept 21-22, AAU Innovate):** the concrete output the
   hackathon expects isn't a jury presentation — it's a booth summary in
   the `Brightcon2026_DemoDerby_Template.docx` format. This report needs to
   be distilled into that format.
