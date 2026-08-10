# pCRM9 Wing -- case study data

This folder itself is gitignored (bundled downloads with an image asset,
committing them adds little over re-downloading) -- this README is the
one tracked exception, so provenance survives even though the data
doesn't. Re-download per the instructions below to reproduce it.

## Why this case study exists

Added specifically to test whether `scripts/mcp_server.py`'s camera/zoom
logic (natural-axis detection, root-left roll, governing-element/
isolated-group aim, analytical zoom auto-fit -- see the main
[README.md](../../README.md)'s MCP server section) actually generalizes,
or is quietly tuned to the NASA CRM wingbox's own geometry. A structurally
different, independently-meshed wing model is a much better test of that
than re-running the same case study again. It found two real bugs the
NASA CRM wingbox's own test coverage couldn't have caught -- see PR #36
and PR #37.

## Source

[pCRM9 aeroelastic aircraft wing model for NASTRAN without RBE2](https://zenodo.org/records/6390714),
Castro, Saullo G. P.; Lancelot, Paul (2022), Zenodo,
DOI: [10.5281/zenodo.6390714](https://doi.org/10.5281/zenodo.6390714).
Creative Commons Attribution 4.0 International (CC-BY-4.0). Downloaded
2026-08-10:

- `pCRM9_103_MAIN_FILE.bdf` -- main deck (SOL 103, `INCLUDE`s the rest)
- `pCRM9_model_2.dat` -- CBEAM spar-cap/stiffener elements + GRIDs
- `pCRM9_ribs_fem.dat` -- rib CQUAD4/CTRIA3 elements + GRIDs
- `pCRM9_CONM2_MTOW.dat` -- distributed fuel mass at MTOW (CONM2)
- `pCRM9_PSHELL.dat` / `pCRM9_mat.dat` -- shell properties / MAT1
- `pCRM9_103_MAIN_FILE.f06` / `.f04` -- the original authors' own MSC
  Nastran run, kept for reference (see "A note on cross-checking" below)
- `pCRM9_FEM_model.PNG` -- the original authors' own reference image

Based on the University of Michigan's undeflected Common Research Model
(uCRM) geometry -- a different mesh, different author, different unit
system (mm/N/tonne/MPa, vs. the NASA CRM wingbox's inches/lbf/psi) and a
different element mix (CBEAM + CQUAD4 + CTRIA3, vs. the NASA model's
CQUAD4 + CBAR) from the NASA CRM wingbox already in this repo, despite
both ultimately tracing back to the same CRM aircraft geometry family.

## Folder structure

- `original/` -- exactly as downloaded, unmodified.
- `derived/` -- empty. See "Real MYSTRAN incompatibility" below for why
  this deck isn't solved in this repo.

## Real MYSTRAN incompatibility: not solvable as-is

The deck already has a valid `SOL 103`/`CEND`/`BEGIN BULK` structure (no
OptiStruct-style patch needed, unlike the NASA CRM wingbox), so this
looked at first like a straightforward `run_solver.py` run. It isn't.

MYSTRAN 19.0.0 rejects the deck's two `PBEAML` property cards (`BAR`
cross-sections, referenced by all 1,147 `CBEAM` spar-cap/stiffener
elements -- ~31% of the model) with a parse error
(`*ERROR 1701: NO DECIMAL POINT WAS FOUND...`, `*ERROR 1136: REQUIRED
CONTINUATION FOR PBEAML ... MISSING`). Checking MYSTRAN's own bundled
Users Manual directly (not just trial and error) confirms this isn't a
one-off formatting fix: `PBEAM`/`PBEAML` don't appear in it at all, and
neither does `CBEAM` -- MYSTRAN 19.0.0 only supports the `CBAR`/`PBAR`
beam family. This is a real, structural solver-capability gap, not a
malformed file (the original authors' own MSC Nastran run in
`original/pCRM9_103_MAIN_FILE.f06` completes without issue against the
identical deck).

Making this solvable in MYSTRAN would mean converting all 1,147 `CBEAM`
elements to `CBAR` and both `PBEAML` properties to `PBAR` -- straightforward
for area and bending inertia (closed-form rectangle formulas, and
pyNastran's own `PBEAML.Area()`/`I11()`/`I22()` already compute them
directly from the library-shape dimensions), but torsion constant `J`
has no closed-form answer for a rectangle and would need an approximate
engineering formula. That's a big enough change to the original model
that it's being logged here as a known gap rather than made silently --
matching how the main [README.md](../../README.md) already documents a
comparable gap for the University of Michigan uCRM model (MYSTRAN's
`PSHELL` rejecting a nonzero `MID4`). This case study stands on its
geometry/camera-generality renders alone (see "Why this case study
exists" above); a from-scratch normal-modes analysis isn't part of it.
