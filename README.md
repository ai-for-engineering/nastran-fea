# AI4Engineering

Exploring AI applications for aerospace mechanical/stress engineering.

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
- `get_max_stress(op2_path)` -- parses an OP2 and returns the max von Mises
  CQUAD4 stress (value, element ID, subcase) across all subcases.

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
