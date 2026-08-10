# Nastran FEA

Part of **ai-for-engineering** -- an umbrella project exploring AI applications
across engineering disciplines. This repo is the first exploration:
AI-driven finite element analysis in native Nastran bulk-data format, using
open-source tools only.

Case study: solving a real, publicly-published NASA structural assembly
(the Common Research Model wingbox) using open-source tools only — Gmsh
(meshing/CAD), pyNastran (BDF/OP2 I/O) + MYSTRAN (solver), all
reading/writing genuine Nastran bulk-data format.

## Setup

```bash
python -m venv venv
./venv/Scripts/pip install -r requirements.txt
```

Download the MYSTRAN solver binary (not committed to this repo, ~24MB)
from the [official releases page](https://github.com/MystranSolver/MYSTRANSolver/releases)
and place it in `solver/`.

## Solving a model

```bash
./venv/Scripts/python scripts/run_solver.py <path-to-model>.bdf
```
This stages a `.dat` copy MYSTRAN expects, invokes the solver, and parses
the `.F06` output for fatal errors -- MYSTRAN's process exit code and its
"terminated normally" message are not reliable success signals on their own
(see `scripts/run_solver.py` docstring). It's also importable as
`run_solver(bdf_path, solver_exe_path) -> SolverResult` for programmatic use.

## MCP server

`scripts/mcp_server.py` exposes the pipeline as MCP tool calls so a client
like Claude can drive load → patch → solve → results conversationally
instead of by running scripts by hand. Start it (stdio transport):

```bash
./venv/Scripts/python scripts/mcp_server.py
```

Point an MCP client (e.g. Claude Desktop/Code config) at that command. Tools:

- `load_model(bdf_path)` -- parses with pyNastran, returns node/element/
  property/material counts and any parse warnings. A deck that fails to
  parse (e.g. OptiStruct-style, no SOL/CEND) comes back as a structured
  `{"success": false, "error": ...}` rather than an exception, so a client
  can recover by calling `patch_case_control` next.
- `patch_case_control(bdf_path, output_path)` -- detects a missing SOL/CEND
  case control section and rebuilds it (see the NASA CRM case-study patch
  below), preserving any SPC/LOAD/DISPLACEMENT/STRESS/ECHO requests already
  present in the original header. If the deck already has SOL/CEND, this is
  a no-op copy to `output_path`.
- `run_solver(bdf_path, solver_exe_path=None, timeout=None)` -- thin wrapper
  around `run_solver()` below; same success/errors/paths result, as JSON.
- `get_max_stress(op2_path)` -- parses an OP2 and returns the peak stress
  per element type present (plate elements like CQUAD4/CTRIA3 report
  `von_mises`; bar elements like CBAR report `max_stress`, since bar direct
  stress isn't the same physical quantity as plate von Mises and the two
  are deliberately not blended into one number), each with element ID and
  governing subcase, e.g.
  `{"cquad4": {"von_mises": ..., "element_id": ..., "subcase": ...}, "cbar": {"max_stress": ..., ...}}`.
  Bar-type results also report which specific column governed:
  `"component"` (`"axial"`, `"bending"`, or `"combined (axial + bending)"`)
  and `"end"` (`"A"`/`"B"`, omitted for `"axial"` since it has no fixed
  end) -- `"max_stress"` alone doesn't say which of those it actually is.
- `get_normal_modes(op2_path)` -- parses a `SOL 103` (normal modes) OP2
  and returns each extracted mode's number, frequency (Hz), and eigenvalue
  (rad²/s²). frequency_hz is computed as `sqrt(eigenvalue) / (2*pi)`
  directly rather than trusted from pyNastran's own `mode_cycles`
  eigenvector attribute — confirmed against a real F06 that despite the
  name, it actually holds radians/s for this result type, not Hz. Raises
  if the OP2 has no eigenvector table at all (e.g. a static analysis —
  use `get_max_stress` for that instead).
- `describe_loads_and_boundary_conditions(bdf_path)` -- explains what's
  actually constraining and loading the model, per subcase: reads SPC/SPC1
  (following SPCADD combinations) for boundary conditions and
  FORCE/MOMENT/FORCE1/FORCE2/MOMENT1/MOMENT2 (following LOAD combinations
  and their scale factors) for loads, and reports constrained node/DOF
  counts plus a resultant force/moment vector. A subcase that doesn't
  request an SPC or LOAD at all (e.g. a modes-only subcase) reports `None`
  for that half rather than an empty result, so "not requested" is
  distinguishable from "requested but empty". Pressure/gravity loads
  (PLOAD*/GRAV) are counted but not vector-summed -- they need element
  geometry or a mass distribution this tool doesn't load. MPC is not yet
  handled (not exercised by the NASA CRM wingbox validation case).
- `render_model_view(bdf_path, output_png, ...)` /
  `render_stress_contour(bdf_path, op2_path, output_png, ...)` -- render a
  screenshot of the model (plain geometry, or colored by von Mises stress)
  via a scripted pyNastranGUI session. Needs an active desktop session --
  this is non-interactive, not display-less headless (see issue #8). Both
  support `hide_groups`/`hide_property_ids` (remove these elements) and
  `isolate_groups`/`isolate_property_ids` (keep ONLY these, e.g. "show only
  the ribs") -- named groups are parsed from a case study's `.ses` file
  when it has one (`ses_path`, see `ses_groups.py`), with property-ID
  filtering as the fallback. `camera` picks a named preset (`"iso"`,
  `"top"`, `"side"`, `"front"`, `"planform"`) or `"auto"`
  (`render_stress_contour`'s default), which aims itself instead of
  guessing: along whichever of the model's own natural span/chord/thickness
  axes (detected from its geometry, not hardcoded) best faces the governing
  stress element normally, or at an isolated group's shared face normal
  (tilted to fan out parallel elements like ribs so each is distinguishable)
  when isolating -- both keep span horizontal and roll the camera so root
  lands on the left. `"planform"` is tuned to match NASA's
  own CRM wingbox FEM description figures -- span horizontal in frame,
  elevated just enough to reveal the leading edge and root end-cap as
  depth cues -- a better single "what does this model look like" overview
  than `"iso"`, which rotates a long swept wingbox into a tall portrait
  shape that wastes most of a landscape frame (see `_CAMERA_PRESETS`'
  comment in `scripts/mcp_server.py`). Framing/zoom is fit automatically by
  default -- the model's own projected bounding rectangle is sized to fill
  the frame regardless of camera or isolate_* choice (see
  `_build_postscript`'s `fit_block`); pass `zoom` to additionally scale that
  automatic fit. Isolating
  with `render_stress_contour` transparently trims the OP2 to match the
  isolated element set first -- pairing a filtered geometry with the full
  untrimmed OP2 is a real, confirmed hang in pyNastranGUI.
  `render_stress_contour`'s `result` parameter picks what to color by:
  `"von_mises"` (default, plate elements only), `"displacement"` (nodal
  translational displacement magnitude), `"axial"` (bar elements only --
  CBAR's real per-element axial direct stress, not the GUI-synthesized
  pseudo-vonMises value bars get lumped into under `"von_mises"`; see
  `_build_postscript`'s `"__bar_axial__"` branch for how it's found, since
  bar-stress cases aren't keyed by a descriptive name the way plate/
  displacement cases are), or `"mode_shape"` (one specific mode's
  eigenvector displacement from a `SOL 103` OP2 -- requires `mode_number`,
  1-indexed, since every mode shares the same result-case name; see
  `_build_postscript`'s `"__mode_shape__"` branch, which additionally
  filters on the case's own mode index). `result="displacement"` and
  `result="mode_shape"` don't support `hide_*`/`isolate_*` (raises if
  combined) -- the OP2 trimming those need only preserves stress tables,
  so a displacement or mode-shape fringe on a trimmed OP2 would silently
  find nothing to show; `"von_mises"` and `"axial"` are both stress-table
  results and support `hide_*`/`isolate_*` fine.

## 3D visualization (pyNastranGUI)

```bash
./venv/Scripts/pip install PyQt5 "vtk==9.3.1" setuptools
./venv/Scripts/pyNastranGUI -i <path-to-model>.bdf -o <path-to-results>.OP2 -f nastran
```
VTK must be pinned to 9.3.1 -- the latest release (9.6.x as of writing) removed
an API pyNastran 1.4.1 depends on. Avoid clicking on the model until it has
fully finished loading (including results): clicking early can hit a real,
recoverable bug in pyNastran's click handler (`case_keys[None]`) that spams
the console but generally doesn't crash the app -- though on large models it's
best avoided.

## Case study: NASA CRM wingbox

`case_studies/nasa_crm_wingbox/` holds a real, publicly-licensed aerospace
structural assembly used to stress-test the pipeline against a model we
didn't author ourselves. Not committed to git (~110MB) -- re-download from
the source below.

A full-scale semi-span wingbox (50+ ribs, dual spars, stringers) from NASA's
Common Research Model, isotropic aluminum (MAT1/CQUAD4/CBAR), genuinely
MYSTRAN-compatible. Source: [NASA CRM Wingbox FEM files](https://commonresearchmodel.larc.nasa.gov/fem-file/wingbox-fem-files/)
("could be used by anyone without any restrictions" per NASA). Get
`CRM_V15wingbox_1_noHM.zip` and `CRM_Wingbox_FEMMidsurfaces_IGES.zip`
(IGES midsurfaces, split by component: ribs/spars/skins/stringers/rib caps).

Note: the bulk data is standard Nastran, but the case control was authored
for Altair OptiStruct (`ANALYSIS MODES`/`ANALYSIS STATICS`, no `SOL`/`CEND`).
To run the static "GVW" subcase in MYSTRAN, replace everything before
`BEGIN BULK` with:
```
SOL 101
CEND
ECHO = NONE
SPC = 2
LOAD = 3
DISPLACEMENT = ALL
STRESS = ALL
```

We picked this dataset over an alternative (University of Michigan's uCRM,
CC BY 4.0, DOI 10.17632/gpk4zn73xn.1) because that one's PSHELL cards each
reference 4 independent MAT2 materials (membrane/bending/shear/coupling) to
represent smeared stiffened-panel properties, and MYSTRAN's PSHELL rejects a
nonzero MID4 (membrane-bending coupling) -- a real solver capability gap,
not a bug in our pipeline. Worth knowing about before assuming any given
anisotropic shell model will just run.

## Case study: pCRM9 wing (geometry only -- real MYSTRAN gap)

`case_studies/pcrm9_wingbox/` (gitignored, see its own README for the
Zenodo source/license) is a second, independently-authored wing model
(TU Delft / University of Michigan CRM-derived geometry, CBEAM + CQUAD4 +
CTRIA3, mm/N/tonne units) added specifically to test whether the camera/
zoom logic generalizes beyond the NASA CRM wingbox -- it does, and found a
real bug in the process (see `_write_filtered_bdf`'s docstring). Not
solved: MYSTRAN 19.0.0's own bundled manual doesn't document `CBEAM`,
`PBEAM`, or `PBEAML` at all, and ~31% of this model's elements use exactly
those cards. See the case study's own README for the full gap writeup.

## Case study: DLR ISTAR wing (composite, normal modes)

`case_studies/istar_wing/` (gitignored, see its own README for the Zenodo
source/license) is a third wing model, and a genuinely different
construction: composite CQUAD4 shells (`PCOMP`/`MAT8`, no isotropic
material or bar element anywhere), and its own original analysis is
`SOL 103` (normal modes) rather than static stress -- exercising
`get_normal_modes` and `render_stress_contour`'s `result="mode_shape"`.
Solves cleanly in MYSTRAN; its bundled original MSC Nastran 2018.2 F06
lets frequencies be cross-checked independently (agreement to 6
significant figures across all 15 extracted modes).

## Blog

`docs/` is a GitHub Pages site (Jekyll, `minima` theme) with write-ups of
each case study -- the NASA CRM wingbox post covers the pipeline end to
end (case-control patch, solver-compatibility gap, results, conversational
demo, honest caveats) plus a second section on the pCRM9/DLR ISTAR
generality check above.
