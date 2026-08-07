# CLAUDE.md

Operating notes for working in this repo. See `README.md` for setup/usage.

## Project constraint: open source only

This project exists to demonstrate AI-driven FEA workflows using **only
open-source tools**, in **native Nastran bulk-data format** specifically
(not a translated/different syntax). Current stack: Gmsh (meshing) +
pyNastran (BDF/OP2 I/O) + MYSTRAN (solver). Don't suggest commercial
alternatives (Ansys, Abaqus, MSC/NX Nastran, HyperMesh) as part of the
actual pipeline -- they're fine to discuss for context/comparison, but the
deliverable must run on the free stack.

## Environment

- Python venv at `venv/` (not `base` conda env). Always invoke via
  `./venv/Scripts/python.exe` or `./venv/Scripts/pip.exe` explicitly --
  `python`/`pip` on PATH may resolve to a different (Microsoft Store stub or
  miniforge base) interpreter.
- MYSTRAN solver binary lives in `solver/`, gitignored (~24MB). Must be
  downloaded per README before anything can actually solve.
- `gh` CLI is authenticated on this machine (account `mabvscode`). Repo:
  `github.com/mabvscode/ai4engineering` (private).

## Real gotchas discovered so far (don't re-derive these)

- **MYSTRAN's exit code is 0 even on fatal errors.** Never trust the return
  code or "MYSTRAN terminated normally" alone -- always check the `.F06` for
  `*ERROR`/`FATAL` markers. `scripts/run_solver.py` does this correctly;
  use it rather than shelling out to the solver directly.
- If MYSTRAN can't find its input file, it **blocks on stdin** waiting for a
  filename instead of failing. Always redirect `stdin` from the null device
  when invoking it programmatically.
- BDF field-width bugs are easy to introduce with pyNastran's `write_bdf`:
  use `size=8` (small-field) and `enddata=True` explicitly -- `size=16` has
  produced overflowing/concatenated fields on high-precision floats before.
- MYSTRAN's `PSHELL` does not support a nonzero `MID4` (membrane-bending
  coupling via `MAT2`) -- real capability gap, not a bug. Real anisotropic
  "smeared stiffened panel" models (e.g. the uCRM case study) will fail with
  `*ERROR 1194` until that term is zeroed or the model is swapped for an
  isotropic (`MAT1`) one.
- MYSTRAN's real-number parser wants a decimal point (`1.0E5`, not `1E5`).
- Decks authored for Altair OptiStruct (via HyperMesh) often have no
  `SOL`/`CEND` at all and use OptiStruct-only case-control syntax
  (`ANALYSIS MODES`/`ANALYSIS STATICS`). The bulk data is usually standard
  Nastran; only the case-control header needs rebuilding.
- pyNastranGUI needs `PyQt5` + `vtk` (pinned to **9.3.1** -- newer 9.6.x
  removed an API pyNastran 1.4.1 uses) + `setuptools`, none of which are
  pulled in by the base `pynastran` package.
- pyNastran's OP2 stress arrays: use
  `op2.op2_results.stress.cquad4_stress[subcase]` (not the deprecated
  `op2.cquad4_stress`). `von_mises` is column index 7; `element_node[:, 0]`
  gives element IDs (each element appears twice, once per shell fiber).

## Workflow conventions

- **Branch + PR, not direct commits to `master`** for anything nontrivial.
  Backlog is tracked as GitHub issues; reference/close them from PRs.
- Delegated/autonomous work uses the `Agent` tool with
  `isolation: "worktree"` -- it works in an isolated git worktree and
  branch, then opens its own PR. Worktrees don't have their own `venv/` or
  `solver/` (gitignored, not checked out) -- point agents at the main
  checkout's absolute paths for those when they need to actually run
  something, but keep the code itself portable (no hardcoded personal paths).
- Clean up worktrees after merging (`git worktree remove`) before deleting
  the branch, or branch deletion fails.

## Repo layout

- `scripts/` -- the actual pipeline (mesh → BDF → solve → postprocess)
- `test_fixtures/` -- synthetic CAD/data generated only to develop against
  when we lack a real example; not the deliverable itself
- `case_studies/` -- real, publicly-licensed reference models (gitignored,
  see README for sources/licenses)
- `models/`, `results/` -- generated outputs, gitignored except source PNGs
  explicitly excluded too (regenerate via scripts)
