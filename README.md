# sentier-models

Models and datasets built at the Brightcon 2026 hackathon (Aalborg, 21
September 2026). Participants turn spreadsheets, papers and legacy tools into
runnable Python models with AI coding assistants and publish them here, code
and data together.

## Add your model

1. Fork this repository.
2. Create `models/<your-model-name>/` (lowercase, dashes, no spaces).
3. Put your code and your data in it, plus a `README.md` that says:
   - what the model computes,
   - who built it,
   - what it is based on (paper, spreadsheet, tool) and the licence of that,
   - which AI tool you used and how you checked the output,
   - the source and licence of every data file,
   - how to run it.
4. Add a row to the table below and open a pull request.
   
| Folder | What it computes | Contributors |
|---|---|---|
| [`bafu-copied-processes`](models/bafu-copied-processes/) | Splits BAFU's copied-process families into structure+values (lossless round-trip on all 766 families, cross-checked against real bw2calc), and forecasts a held-out family member via MILP superstructure optimization | [Ozge Ozkilinc](https://github.com/ozgeozkilincc) |

## Rules

- **State the licence of your data.** If you do not know it, ask before
  committing. Data you may not redistribute stays on your machine; link to it.
- **No ecoinvent-derived amounts.** Exchange amounts, per-exchange
  characterised results and activity catalogs from ecoinvent are commercially
  licensed and must not be committed. Names, units and version numbers are
  fine.
- **No secrets.** Check your diff for API keys before you push.
- **Small files, committed directly.** No git-LFS, no release assets. Trim or
  link to the original if a dataset is more than a few megabytes.
- **Credit the original** tool or paper your model reimplements.

## Licence

Code is MIT (see `LICENSE`) unless a model folder says otherwise. Datasets
keep the licence stated in their folder's README.

Départ de Sentier, https://www.d-d-s.ch/
