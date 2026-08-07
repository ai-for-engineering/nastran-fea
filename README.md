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
