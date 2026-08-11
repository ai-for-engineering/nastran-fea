"""
Rebuild the NASA CRM wingbox from its own CAD geometry (the IGES
midsurfaces at case_studies/nasa_crm_wingbox/original/IGES_midsurfaces/)
via assemble_wingbox_geometry.mesh_assembly_to_bdf, using real per-group
thickness/material values -- issue #43 of the rebuild-and-compare epic
(#47). See CLAUDE.md/README for the case study background and #42 for
why this is node-welding, not a CAD-level merge.

Per-group thickness sources, all real numbers, not guesses:

- RIBS (0.167 in), SPARS (0.410 in), SKINS (0.159 in): averaged directly
  from the original solved model's own PSHELL thicknesses, grouped by its
  `.ses` named element groups (see spikes/model_description_extract.py,
  and the blog's Model description chapter in #40).
- STRINGERS (1.01 in): the original model represents stringers as CBAR
  (14,134 of them, avg cross-section area 0.584 in^2), not shells -- the
  IGES download only provides them as 2D midsurface strips, so there's no
  direct thickness to reuse. Instead, back-calculated to preserve the
  same *total structural material volume* as the original: the original
  Stiffeners group's total volume (sum of length * area over all 14,134
  CBARs) is 93,506 in^3; the rebuilt STRINGERS shell strips' total
  footprint area is 92,723 in^2; dividing gives a thickness of ~1.01 in
  that puts the same total material into the model, even though the
  cross-section shape (thin wide shell vs. compact bar) is genuinely
  different -- a real, stated modeling difference, not hidden.
- RIB_CAPS (0.167 in): the original model has no separate "rib caps"
  named group at all (only Ribs/ShearWebs/Skin/Spars/Stiffeners) -- this
  geometry has nothing to calibrate a thickness against. Assumed equal to
  RIBS as the closest structurally-analogous member (a rib cap is a
  flange reinforcing a rib edge). This is a stated assumption, not a
  measurement, and should be treated as the least-confident value in this
  rebuild when interpreting results.

Boundary conditions and load (issue #44) are reconstructed geometrically
against this rebuilt mesh's own node distribution, not copied from the
original's node IDs (which don't correspond to anything in a re-meshed
model):

- Root SPC (T1/T2/T3): every node within 0.1 in of Y=0 -- confirmed a
  clean, stable count (1,532 nodes) across a range of tolerances from
  0.05 to 0.2 in, i.e. a genuine flat root cross-section, not an
  arbitrary cutoff.
- Second support SPC (T3 only): every node within 0.05 in of Y=120.25 --
  a real rib station found by inspecting where RIBS-component nodes
  actually cluster in this mesh (not assumed), remarkably close to
  NASA's own documented second support point (~120 in, see #40's blog
  Model description chapter).
- Load: 249,777.6 lbf total in +Z (the original's own GVW resultant, see
  #40), distributed evenly across every GRID node -- preserves the
  original's total applied load rather than its literal per-node
  magnitude, since this mesh has a different node count.

Run: ./venv/Scripts/python.exe scripts/build_nasa_crm_from_geometry.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assemble_wingbox_geometry import Component, mesh_assembly_to_bdf  # noqa: E402
from geometry_to_bdf import MaterialProperties  # noqa: E402
from reconstruct_boundary_conditions import (  # noqa: E402
    add_spc_by_y_band,
    add_uniform_z_load,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GEOMETRY_DIR = (
    REPO_ROOT
    / "case_studies"
    / "nasa_crm_wingbox"
    / "original"
    / "IGES_midsurfaces"
)
OUTPUT_BDF = (
    REPO_ROOT / "case_studies" / "nasa_crm_wingbox" / "derived" / "rebuilt_from_geometry.bdf"
)
OUTPUT_BDF_WITH_BC = (
    REPO_ROOT
    / "case_studies"
    / "nasa_crm_wingbox"
    / "derived"
    / "rebuilt_from_geometry_with_bc.bdf"
)

# Boundary conditions -- see module docstring for how these were derived
# from the rebuilt mesh's own node distribution, not assumed.
ROOT_SPC_ID = 1
ROOT_Y_TARGET = 0.0
ROOT_Y_TOLERANCE = 0.1

SECOND_SPC_ID = 2
SECOND_Y_TARGET = 120.25
SECOND_Y_TOLERANCE = 0.05

SPCADD_ID = 10  # combines ROOT_SPC_ID + SECOND_SPC_ID for case control

# GVW static-strength load: the original model's own resultant (see #40's
# blog Model description chapter), reproduced as a total, not a per-node
# value.
LOAD_ID = 3
TOTAL_LOAD_Z_LBF = 249_777.6

# Aluminum, matching the original model's MAT1 exactly (see #40's Model
# description chapter): E=1.0e7 psi, G=3.8e6 psi, nu=0.31, rho=0.101 lbm/in^3.
ALUMINUM = MaterialProperties(mid=1, e=1.0e7, g=3.8e6, nu=0.31, rho=0.101)

COMPONENTS = [
    Component(name="RIBS", geometry_path=GEOMETRY_DIR / "CRM_ribs.igs", thickness=0.167),
    Component(name="SPARS", geometry_path=GEOMETRY_DIR / "CRM_spars.igs", thickness=0.410),
    Component(name="SKINS", geometry_path=GEOMETRY_DIR / "CRM_skins.igs", thickness=0.159),
    Component(
        name="RIB_CAPS", geometry_path=GEOMETRY_DIR / "CRM_rib_caps.igs", thickness=0.167
    ),
    Component(
        name="STRINGERS", geometry_path=GEOMETRY_DIR / "CRM_stringers.igs", thickness=1.01
    ),
]

# The IGES midsurfaces are in mm; the original solved BDF (and this
# rebuild, to stay comparable to it) is in inches.
UNIT_SCALE_MM_TO_IN = 1.0 / 25.4

# Native units (mm) -- chosen to land in the same ballpark element count
# as the original (35,489 elements) without specifically trying to match
# its per-component mesh density.
MESH_SIZE_MM = 150.0


def main() -> None:
    from pyNastran.bdf.bdf import BDF

    result = mesh_assembly_to_bdf(
        COMPONENTS,
        OUTPUT_BDF,
        mesh_size=MESH_SIZE_MM,
        material=ALUMINUM,
        unit_scale=UNIT_SCALE_MM_TO_IN,
    )
    summary = {
        "success": result.success,
        "bdf_path": str(result.bdf_path),
        "n_nodes": result.n_nodes,
        "n_cquad4": result.n_cquad4,
        "n_ctria3": result.n_ctria3,
        "n_welded_pairs": result.n_welded_pairs,
        "counts_by_component": result.counts_by_component,
        "bounding_box": result.bounding_box,
        "mesh_seconds": result.mesh_seconds,
        "weld_seconds": result.weld_seconds,
        "warnings": result.warnings,
    }

    bdf = BDF()
    bdf.read_bdf(str(OUTPUT_BDF), xref=True)

    n_root = add_spc_by_y_band(
        bdf, ROOT_SPC_ID, ROOT_Y_TARGET, ROOT_Y_TOLERANCE, components="123"
    )
    n_second = add_spc_by_y_band(
        bdf, SECOND_SPC_ID, SECOND_Y_TARGET, SECOND_Y_TOLERANCE, components="3"
    )
    bdf.add_spcadd(SPCADD_ID, [ROOT_SPC_ID, SECOND_SPC_ID])
    load_summary = add_uniform_z_load(bdf, LOAD_ID, TOTAL_LOAD_Z_LBF)

    OUTPUT_BDF_WITH_BC.parent.mkdir(parents=True, exist_ok=True)
    bdf.write_bdf(str(OUTPUT_BDF_WITH_BC), size=8, enddata=True)

    summary["boundary_conditions"] = {
        "bdf_path": str(OUTPUT_BDF_WITH_BC),
        "root_spc_id": ROOT_SPC_ID,
        "root_n_nodes": n_root,
        "second_spc_id": SECOND_SPC_ID,
        "second_n_nodes": n_second,
        "spcadd_id": SPCADD_ID,
        "load_id": LOAD_ID,
        "load_summary": load_summary,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
