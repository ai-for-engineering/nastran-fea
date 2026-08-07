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

## Gotchas

Full explanations for anything setup/usage-relevant live in `README.md`
(case studies section, GUI section, solver section) -- don't duplicate the
rationale here, just the reminder:

- MYSTRAN's exit code / "terminated normally" message is not trustworthy on
  its own -- always go through `scripts/run_solver.py`, never shell out to
  the solver directly (full story: README + the script's own docstring).
- MYSTRAN's `PSHELL` can't take a nonzero `MID4` -- see README's case-studies
  section (uCRM vs NASA CRM) before assuming an anisotropic shell model will
  just run.
- OptiStruct-authored decks (no `SOL`/`CEND`, `ANALYSIS MODES/STATICS`
  syntax) need their case control rebuilt -- exact patch recipe is in
  README's NASA CRM case-study section.
- pyNastranGUI's dependency versions (VTK pin, etc.) are documented in
  README's GUI section -- check there before reinstalling anything.

Things with no README equivalent (code-level, not usage-level):

- pyNastran's `write_bdf`: use `size=8, enddata=True` explicitly --
  `size=16` has produced field overflow on high-precision floats before.
- MYSTRAN's real-number parser wants a decimal point (`1.0E5`, not `1E5`).
- pyNastran OP2 stress arrays: use
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

- `scripts/run_solver.py` -- generic MYSTRAN invocation wrapper (see
  Gotchas); not tied to any specific model
- `case_studies/nasa_crm_wingbox/` -- the current (only) case study, a real
  publicly-licensed NASA structural assembly (gitignored, see README for
  source/license and the OptiStruct→MYSTRAN case-control patch)
- `solver/` -- the MYSTRAN binary, gitignored, download per README
