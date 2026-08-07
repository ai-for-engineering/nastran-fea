# AI4Engineering

Exploring AI applications for aerospace mechanical/stress engineering.

First use case: an MCP-driven pipeline for building and solving lug FEA
models using open-source tools only — Gmsh (meshing) + pyNastran (BDF/OP2
I/O) + MYSTRAN (solver), all reading/writing genuine Nastran bulk-data
format.

## Setup

```bash
python -m venv venv
./venv/Scripts/pip install -r requirements.txt
```

Download the MYSTRAN solver binary (not committed to this repo, ~24MB)
from the [official releases page](https://github.com/MystranSolver/MYSTRANSolver/releases)
and place it in `solver/`.

## Scripts

1. `scripts/01_build_mesh.py` — builds the lug geometry (plate + hole) and mesh via Gmsh
2. `scripts/02_build_bdf.py` — material, loads, BCs; writes the Nastran BDF deck
3. `scripts/03_postprocess.py` — runs results through pyNastran, reports max stress / margin of safety

Run the MYSTRAN solver between steps 2 and 3:
```bash
./venv/Scripts/python scripts/run_solver.py models/lug_model.bdf
```
This stages the `.dat` copy MYSTRAN expects, invokes the solver, and parses
the `.F06` output for fatal errors -- MYSTRAN's process exit code and its
"terminated normally" message are not reliable success signals on their own
(see `scripts/run_solver.py` docstring). It's also importable as
`run_solver(bdf_path, solver_exe_path) -> SolverResult` for programmatic use.

## 3D visualization (pyNastranGUI)

```bash
./venv/Scripts/pip install PyQt5 "vtk==9.3.1" setuptools
./venv/Scripts/pyNastranGUI -i models/lug_model.bdf -o models/lug_model.OP2 -f nastran
```
VTK must be pinned to 9.3.1 -- the latest release (9.6.x as of writing) removed
an API pyNastran 1.4.1 depends on. Avoid clicking on the model until it has
fully finished loading (including results): clicking early can hit a real,
recoverable bug in pyNastran's click handler (`case_keys[None]`) that spams
the console but generally doesn't crash the app -- though on large models it's
best avoided.

## Case studies

`case_studies/` holds real, publicly-licensed aerospace structural models used
to stress-test the pipeline against models we didn't author ourselves. Not
committed to git (~320MB combined) -- re-download from the sources below.

**NASA CRM wingbox** (`case_studies/nasa_crm_wingbox/`) -- a full-scale
semi-span wingbox (50+ ribs, dual spars, stringers) from NASA's Common
Research Model, isotropic aluminum (MAT1/CQUAD4/CBAR), genuinely
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

**uCRM** (`case_studies/ucrm_wingbox/`) -- University of Michigan MDO Lab's
aerostructural benchmark (CC BY 4.0), includes IGES wing-body-tail geometry
and a ready-to-run BDF deck. **Not MYSTRAN-compatible as-is**: its PSHELL
cards reference 4 independent MAT2 materials each (membrane/bending/shear/
coupling), and MYSTRAN's PSHELL rejects a nonzero MID4 (membrane-bending
coupling) -- a real solver capability gap, not a bug in our pipeline. Source:
[Mendeley Data, DOI 10.17632/gpk4zn73xn.1](https://data.mendeley.com/datasets/gpk4zn73xn/1)
(Brooks, Kenway, Martins). Get `uCRM9.zip`.
