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
- `derived/istar_wing.{dat,OP2,F06,...}` -- `run_solver.py` against
  `ISTAR_Demo_Wing.bdf` directly (copied to `istar_wing.dat` first, per
  `run_solver`'s own staging convention -- see the main README), no
  case-control patching needed. Solves the model's own original SOL 103
  analysis exactly as its authors set it up -- no invented loads, since an
  eigenvalue analysis has none to invent.
- `derived/istar_wing_static.{dat,OP2,...}` -- a second, separate solve:
  the same geometry/mass/constraint, but with the case control swapped to
  `SOL 101` (linear statics) and a single `FORCE` card added (50 N, +Z,
  at GRID 10001). Unlike the modal run, this load is NOT from the original
  authors -- the deck has no static load case at all (`FORCE`/`PLOAD*`
  don't appear anywhere in it), since it was built purely for normal-modes
  analysis. GRID 10001 was picked deliberately, not arbitrarily: it's the
  independent node of an `RBE3` (`RBE3 10001 ... 1366 1380 ...`) the
  original modelers already placed at the wingtip (Y=0.685, this model's
  own span max) for load/sensor interpolation onto the surrounding tip
  patch -- reusing the one point the model's own GVT-style setup already
  intended for exactly this kind of concentrated load, rather than picking
  a node ourselves. See the main blog post for results and a caveat about
  the peak stress landing right at the load point.

Reproducing the static run: `scripts/mcp_server.py`'s `get_max_stress`
needed a real fix to handle this deck at all -- MYSTRAN's composite
(PCOMP) plate stress table has one row per ply, indexed by
`.element_layer` rather than `.element`/`.element_node` (neither
attribute exists on that array), which raised `AttributeError` the first
time this was tried. Fixed generically (any composite plate result, not
just this one) in `_element_ids_for`.

The static render also surfaced a second, unrelated real bug: this deck's
`RBE2`/`RBE3` rigid elements, its own `CORD2R` coordinate systems, and its
per-ply composite material orientations make pyNastranGUI create over a
dozen extra decorative actors (`Coord 511`, `mcid ply=1` through `ply=20`,
`rigid_lines`, `SPC=3`, `material coord`, ...) that neither the NASA CRM
wingbox nor this model's own modal run ever had, and `_build_postscript`
only ever hid one hardcoded actor name (`'Global XYZ'`). Left visible,
they produced an unreadable render (oversized overlapping corner text, a
badly mis-framed model). Fixed generically too: hide every
`geometry_actors` entry except `'main'`, rather than hardcoding each
newly-discovered name.

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
