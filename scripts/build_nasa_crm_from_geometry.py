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

Run: ./venv/Scripts/python.exe scripts/build_nasa_crm_from_geometry.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assemble_wingbox_geometry import Component, mesh_assembly_to_bdf  # noqa: E402
from geometry_to_bdf import MaterialProperties  # noqa: E402

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
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
