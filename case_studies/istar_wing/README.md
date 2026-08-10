# DLR ISTAR Demonstrator Wing -- case study data

This folder itself is gitignored (bundled downloads, committing them adds
little over re-downloading) -- this README is the one tracked exception,
so provenance survives even though the data doesn't. Re-download per the
instructions below to reproduce it.

## Why this case study exists

The second real case study alongside the NASA CRM wingbox, added to test
whether `scripts/mcp_server.py`'s camera/zoom logic actually generalizes
across genuinely different models (see the main
[README.md](../../README.md)'s MCP server section) -- and to exercise a
result type the NASA CRM wingbox case study never needed: SOL 103 normal
modes (natural frequencies + mode shapes), via the new `get_normal_modes`
tool and `render_stress_contour`'s `result="mode_shape"`.

A different wing than `pcrm9_wingbox` was tried first and found genuinely
incompatible with MYSTRAN 19.0.0 (CBEAM/PBEAM/PBEAML aren't supported at
all -- see that case study's own README). This one is composite
(CQUAD4 + PCOMP + MAT8 -- no beams at all), a different construction
entirely, and confirmed to solve cleanly.

## Source

[Finite Element Model of the ISTAR Demonstrator Wing](https://zenodo.org/records/7017137),
Dillinger, Johannes; Klimmek, Thomas; Gundlach, Janto (2022), Zenodo,
DOI: [10.5281/zenodo.7017137](https://doi.org/10.5281/zenodo.7017137).
Creative Commons Attribution 4.0 International (CC-BY-4.0). Downloaded
2026-08-10:

- `ISTAR_Demo_Wing.bdf` -- the model (SOL 103, already has a valid
  `CEND`/`BEGIN BULK` structure -- no OptiStruct-style patch needed)
- `istar_demo_wing.f06` -- the original authors' own real MSC Nastran
  2018.2 run, kept for the cross-check below

A miniature (small-scale) representation of DLR's own ISTAR research
aircraft wing, built with DLR's in-house ModGen tool: 1,384 nodes, 1,574
CQUAD4 elements, each with its own PCOMP multi-layer GFRP composite
layup (1,574 PCOMP cards, 17 MAT8 orthotropic materials) -- no PSHELL/
MAT1/CTRIA3 at all, a genuinely different construction from both the NASA
CRM wingbox (isotropic aluminum, CQUAD4+CBAR) and pCRM9 (isotropic
aluminum, CQUAD4+CTRIA3+CBEAM).

## Folder structure

- `original/` -- exactly as downloaded, unmodified.
- `derived/` -- produced by this project: `run_solver.py` against
  `ISTAR_Demo_Wing.bdf` directly (copied to `istar_wing.dat` first, per
  `run_solver`'s own staging convention -- see the main README), no
  case-control patching needed. Solves the model's own original SOL 103
  analysis exactly as its authors set it up -- no invented loads, since an
  eigenvalue analysis has none to invent.

## MYSTRAN vs. real MSC Nastran: a genuine cross-check

Unlike the NASA CRM wingbox (no independent solver run to compare
against), this case study's `original/istar_demo_wing.f06` is a real MSC
Nastran 2018.2 run of the identical deck. Comparing its 15 extracted
natural frequencies against MYSTRAN's own: agreement to 6 significant
figures on every mode (mode 1: 9.171 Hz vs. 9.171 Hz MSC; mode 15:
467.28 Hz vs. 467.28 Hz MSC) -- a real, independent confirmation that
MYSTRAN's composite shell (PCOMP/MAT8) and normal-modes (EIGRL/Lanczos)
support produce correct results, not just "the solver exits 0."

Reproducing this comparison: `get_normal_modes`'s `frequency_hz` is
computed as `sqrt(eigenvalue) / (2*pi)` directly, not read from
pyNastran's own `mode_cycles` eigenvector attribute -- confirmed via this
exact case that despite the name, `mode_cycles` holds the F06's RADIANS
column (rad/s) for this result type, not CYCLES (Hz); see
`get_normal_modes`'s own docstring for the full explanation.
