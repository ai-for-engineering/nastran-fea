"""Parametric rebuild of the NASA CRM wingbox's own real planform -- not a
generic toy box (see parametric_wingbox_conformal_mesh.py for that
earlier, deliberately-minimal proof of concept). Every dimension below was
measured directly from the ORIGINAL solved deck
(`case_studies/nasa_crm_wingbox/derived/CRM_V15_wingbox_1_static.dat`) via
`spikes/extract_crm_planform.py` -- span, root/tip chord, leading-edge
sweep, dihedral, box-depth taper, front/rear spar chordwise fraction, and
all 57 real (non-uniformly-spaced -- denser near the root/crank region,
~20.9in outboard) rib stations. See that script's own output for the raw
numbers this module's constants are transcribed from.

The point, per issue #59/CLAUDE.md's Gotchas: the real NASA CRM wingbox's
5 downloaded IGES files have no shared topology encoded at all (each
component independently modeled/exported), which is *why* the from-IGES
rebuild (`assemble_wingbox_geometry.py`) can only approximately weld
components together, never achieve full conformal connectivity. THIS
script builds the wing's ribs/spars/skins as one topologically fragmented
assembly, directly in a single gmsh OpenCASCADE session (no export/
re-import round trip) -- so touching parts share real topology by
construction, at this wing's own real planform, not a simplified box.

Scope, stated plainly (matching this project's own honesty convention):
- Ribs are modeled as flat planes perpendicular to the span axis (Y) --
  the real aircraft's ribs may be angled/streamwise in places (not
  verified either way here); this is a structural simplification, not a
  measured fact.
- Only the 2 main continuous spars (front/rear) are modeled. The
  original deck's own `ShearWebs` group resolves (via connected-component
  analysis in extract_crm_planform.py) to ~22 MORE continuous internal
  spanwise webs between them -- a genuinely multi-spar/multi-web wingbox,
  not just front+rear -- omitted here to keep this proof-of-concept's
  fragment/mesh complexity and runtime bounded. Documented as a real gap,
  not silently dropped.
- Skins are flat-idealized ruled surfaces (front-spar-edge to
  rear-spar-edge, no airfoil camber) -- matches how this project's other
  wingbox models already idealize skins (a structural wingbox model, not
  an aerodynamic OML), not a new simplification introduced here.
- No stringers/stiffeners.

Run: ./venv/Scripts/python.exe spikes/parametric_crm_wingbox.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# --- Real planform parameters, measured from the original solved deck ---
# (see spikes/extract_crm_planform.py's output; all lengths in inches,
# this model's own native unit, matching the original deck).
SPAN = 1151.32
ROOT_CHORD = 291.19
TIP_CHORD = 85.31
LE_ROOT_X = 1035.23  # leading-edge X at the root probe
LE_SWEEP_DEG = 32.35  # leading-edge sweep, root-to-tip probe

# Dihedral: real mid-depth Z shift from root to tip (measured, not assumed).
Z_MID_ROOT = 134.08
Z_MID_TIP = 262.88

# Box depth (thickness axis range) as a fraction of local chord -- real
# extracted values ranged 15.1-20.1% outboard of the root closeout region
# (the true root probe read 34.9%, an artifact of root closeout structure,
# not representative of the general spanwise trend -- excluded here in
# favor of the more representative 25%-span value as the "root" endpoint).
BOX_DEPTH_FRAC_ROOT = 0.18
BOX_DEPTH_FRAC_TIP = 0.20

# Front/rear spar chordwise position as a fraction of local chord, at root
# and tip (real extracted trend; root values were noisy right at the exact
# root-closeout probe, so representative endpoints are used).
FRONT_SPAR_FRAC_ROOT = 0.02
FRONT_SPAR_FRAC_TIP = 0.11
REAR_SPAR_FRAC_ROOT = 0.97
REAR_SPAR_FRAC_TIP = 0.92

# All 57 real rib spanwise (Y) stations -- transcribed directly from
# extract_crm_planform.py's connected-component analysis of the original
# deck's own RIBS group, not evenly spaced (denser near the root/crank
# region 120-324in, ~20.9in spacing outboard).
RIB_STATIONS = [
    0.0, 24.2, 48.2, 72.3, 96.3, 120.3, 141.2, 156.5, 172.7, 189.6, 206.1,
    223.0, 239.4, 256.4, 273.0, 290.1, 306.8, 323.9, 346.5, 369.0, 391.5,
    413.9, 436.0, 457.7, 478.8, 499.7, 520.6, 541.5, 562.3, 583.2, 604.1,
    625.0, 645.9, 666.7, 687.6, 708.5, 729.4, 750.3, 771.1, 792.0, 812.9,
    833.8, 854.7, 875.5, 896.4, 917.3, 938.2, 959.1, 979.9, 1000.8, 1021.7,
    1042.6, 1063.5, 1084.3, 1105.2, 1126.1, 1146.5,
    SPAN,  # tip-closure rib: the real rib list's own last station (1146.5)
           # is ~4.8in short of the true tip (measured from the full
           # bounding box), leaving the spar/skin panels' tip edge
           # unclosed -- confirmed as the exact, sole cause of the last
           # 24 (of 5128) unwelded boundary nodes, all at Y=SPAN.
]

MESH_SIZE = 8.0
OUTPUT_BDF = REPO_ROOT / "spikes" / "output" / "parametric_crm_wingbox.bdf"


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def chord_at(y: float) -> float:
    return _lerp(ROOT_CHORD, TIP_CHORD, y / SPAN)


def le_x_at(y: float) -> float:
    import math

    return LE_ROOT_X + y * math.tan(math.radians(LE_SWEEP_DEG))


def z_mid_at(y: float) -> float:
    return _lerp(Z_MID_ROOT, Z_MID_TIP, y / SPAN)


# Every quantity that defines the STRAIGHT EDGE of a spar or skin panel
# (built from just its root and tip corner points -- see build_and_mesh)
# must itself be exactly linear in Y, matching that straight edge. A rib's
# own edge is evaluated from the SAME functions, at its own Y station, so
# it lands exactly on that edge only if the function is truly linear.
# Computing a chordwise position as fraction(y) * chord(y) -- two linear
# functions multiplied together -- is genuinely QUADRATIC in Y, not
# linear: confirmed as a real bug here, not a meshing artifact (raising
# MESH_SIZE resolution didn't help) -- every rib except the root (where
# quadratic and linear coincide by construction) landed 0 nodes shared
# with either spar, only recovering once "fraction * chord" was replaced
# with precomputed root/tip values interpolated directly.
_FRONT_X_ROOT = le_x_at(0.0) + FRONT_SPAR_FRAC_ROOT * chord_at(0.0)
_FRONT_X_TIP = le_x_at(SPAN) + FRONT_SPAR_FRAC_TIP * chord_at(SPAN)
_REAR_X_ROOT = le_x_at(0.0) + REAR_SPAR_FRAC_ROOT * chord_at(0.0)
_REAR_X_TIP = le_x_at(SPAN) + REAR_SPAR_FRAC_TIP * chord_at(SPAN)
_DEPTH_ROOT = BOX_DEPTH_FRAC_ROOT * chord_at(0.0)
_DEPTH_TIP = BOX_DEPTH_FRAC_TIP * chord_at(SPAN)
_ZB_ROOT = Z_MID_ROOT - _DEPTH_ROOT / 2.0
_ZT_ROOT = Z_MID_ROOT + _DEPTH_ROOT / 2.0
_ZB_TIP = Z_MID_TIP - _DEPTH_TIP / 2.0
_ZT_TIP = Z_MID_TIP + _DEPTH_TIP / 2.0


def z_bottom_top_at(y: float) -> tuple[float, float]:
    t = y / SPAN
    return _lerp(_ZB_ROOT, _ZB_TIP, t), _lerp(_ZT_ROOT, _ZT_TIP, t)


def front_spar_x_at(y: float) -> float:
    return _lerp(_FRONT_X_ROOT, _FRONT_X_TIP, y / SPAN)


def rear_spar_x_at(y: float) -> float:
    return _lerp(_REAR_X_ROOT, _REAR_X_TIP, y / SPAN)


def _is_planar(corners: list[tuple[float, float, float]], tol: float = 1e-3) -> bool:
    """True if 4 corner points are (near-)coplanar. A tapered+swept wing's
    skin, bounded by 2 straight spar lines with different sweep rates, is
    generically NOT planar -- confirmed necessary here (unlike the earlier
    simple rectangular-box spike, where every surface was trivially
    planar): OCC's addPlaneSurface rejects a non-planar wire outright, so
    this determines which surfaces need addSurfaceFilling's ruled-surface
    fit instead."""
    import numpy as np

    p0, p1, p2, p3 = (np.array(c) for c in corners)
    n = np.cross(p1 - p0, p2 - p0)
    n_norm = np.linalg.norm(n)
    if n_norm < 1e-9:
        return True
    n = n / n_norm
    return abs(float(np.dot(p3 - p0, n))) < tol * max(
        np.linalg.norm(p1 - p0), np.linalg.norm(p2 - p0), 1.0
    )


def build_and_mesh() -> dict:
    import gmsh
    import numpy as np

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("parametric_crm_wingbox")
        occ = gmsh.model.occ

        def add_quad_surface(corners: list[tuple[float, float, float]]) -> int:
            pts = [occ.addPoint(*c) for c in corners]
            lines = [occ.addLine(pts[i], pts[(i + 1) % 4]) for i in range(4)]
            loop = occ.addCurveLoop(lines)
            if _is_planar(corners):
                return occ.addPlaneSurface([loop])
            return occ.addSurfaceFilling(loop)

        component_of_input: dict[int, str] = {}
        input_dimtags: list[tuple[int, int]] = []

        def register(tag: int, name: str) -> None:
            component_of_input[tag] = name
            input_dimtags.append((2, tag))

        # SPARS: front and rear, each a ruled quad between its own root and
        # tip edge (provably planar -- see module docstring's geometric
        # argument -- since each spar's own 4 corners are bounded by 2
        # parallel, purely-vertical Z lines at Y=0 and Y=SPAN).
        for name, x_at in [("SPAR_FRONT", front_spar_x_at), ("SPAR_REAR", rear_spar_x_at)]:
            zb0, zt0 = z_bottom_top_at(0.0)
            zb1, zt1 = z_bottom_top_at(SPAN)
            corners = [
                (x_at(0.0), 0.0, zb0),
                (x_at(SPAN), SPAN, zb1),
                (x_at(SPAN), SPAN, zt1),
                (x_at(0.0), 0.0, zt0),
            ]
            register(add_quad_surface(corners), name)

        # SKINS: top and bottom, front-spar-edge to rear-spar-edge -- NOT
        # generally planar (front/rear spars sweep at different rates on a
        # tapered wing), handled by add_quad_surface's ruled-surface
        # fallback.
        for name, z_of in [
            ("SKIN_BOTTOM", lambda y: z_bottom_top_at(y)[0]),
            ("SKIN_TOP", lambda y: z_bottom_top_at(y)[1]),
        ]:
            corners = [
                (front_spar_x_at(0.0), 0.0, z_of(0.0)),
                (rear_spar_x_at(0.0), 0.0, z_of(0.0)),
                (rear_spar_x_at(SPAN), SPAN, z_of(SPAN)),
                (front_spar_x_at(SPAN), SPAN, z_of(SPAN)),
            ]
            register(add_quad_surface(corners), name)

        # RIBS: flat planes perpendicular to Y at each real station --
        # trivially planar (constant Y).
        for i, y in enumerate(RIB_STATIONS):
            zb, zt = z_bottom_top_at(y)
            corners = [
                (front_spar_x_at(y), y, zb),
                (rear_spar_x_at(y), y, zb),
                (rear_spar_x_at(y), y, zt),
                (front_spar_x_at(y), y, zt),
            ]
            register(add_quad_surface(corners), f"RIB_{i}")

        occ.synchronize()
        n_input_surfaces = len(input_dimtags)

        t_fragment_start = time.time()
        out, out_map = occ.fragment(input_dimtags, [])
        occ.synchronize()
        fragment_seconds = time.time() - t_fragment_start

        surface_component: dict[int, str] = {}
        for i, dimtag in enumerate(input_dimtags):
            comp_name = component_of_input[dimtag[1]]
            for new_dim, new_tag in out_map[i]:
                if new_dim == 2:
                    surface_component[new_tag] = comp_name
        n_output_surfaces = len(surface_component)

        gmsh.option.setNumber("Mesh.MeshSizeMax", MESH_SIZE)
        gmsh.option.setNumber("Mesh.MeshSizeMin", MESH_SIZE / 2)
        gmsh.option.setNumber("Mesh.RecombineAll", 1)
        t_mesh_start = time.time()
        gmsh.model.mesh.generate(2)
        mesh_seconds = time.time() - t_mesh_start

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        xyz_by_tag = {
            int(t): (node_coords[3 * i], node_coords[3 * i + 1], node_coords[3 * i + 2])
            for i, t in enumerate(node_tags)
        }

        elements_by_component: dict[str, list[list[int]]] = {}
        for surf_tag, comp_name in surface_component.items():
            elem_types, _elem_tags_list, elem_node_tags_list = gmsh.model.mesh.getElements(2, surf_tag)
            for etype, enodes in zip(elem_types, elem_node_tags_list):
                npe = {2: 3, 3: 4}.get(etype)
                if npe is None:
                    continue
                enodes = np.array(enodes).reshape(-1, npe)
                elements_by_component.setdefault(comp_name, []).extend(
                    [int(n) for n in row] for row in enodes
                )

        return {
            "n_input_surfaces": n_input_surfaces,
            "n_output_surfaces": n_output_surfaces,
            "fragment_seconds": fragment_seconds,
            "mesh_seconds": mesh_seconds,
            "xyz_by_tag": xyz_by_tag,
            "elements_by_component": elements_by_component,
        }
    finally:
        gmsh.finalize()


def write_bdf(result: dict) -> None:
    from pyNastran.bdf.bdf import BDF

    bdf = BDF(debug=False)
    bdf.add_mat1(1, 1.0e7, 3.8e6, 0.31, rho=0.101)
    thickness_by_prefix = {
        "SPAR": 0.410,
        "SKIN": 0.159,
        "RIB": 0.167,
    }
    pid_by_component = {name: i + 1 for i, name in enumerate(sorted(result["elements_by_component"]))}
    for name, pid in pid_by_component.items():
        prefix = next((p for p in thickness_by_prefix if name.startswith(p)), None)
        t = thickness_by_prefix.get(prefix, 0.15)
        bdf.add_pshell(pid, mid1=1, t=t, mid2=1, mid3=1)

    for tag, xyz in result["xyz_by_tag"].items():
        bdf.add_grid(tag, list(xyz))

    eid = 1
    for name, elems in result["elements_by_component"].items():
        pid = pid_by_component[name]
        for nids in elems:
            if len(nids) == 3:
                bdf.add_ctria3(eid, pid, nids)
            else:
                bdf.add_cquad4(eid, pid, nids)
            eid += 1

    OUTPUT_BDF.parent.mkdir(parents=True, exist_ok=True)
    bdf.write_bdf(str(OUTPUT_BDF), size=8, enddata=True)


def check_connectivity(result: dict) -> None:
    from collections import Counter

    nodes_by_component = {
        name: {n for elem in elems for n in elem}
        for name, elems in result["elements_by_component"].items()
    }
    all_other_nodes = {
        name: set().union(*(nodes_by_component[o] for o in nodes_by_component if o != name))
        for name in nodes_by_component
    }
    total_boundary = 0
    total_unwelded = 0
    for name, elems in result["elements_by_component"].items():
        edge_count = Counter()
        for nids in elems:
            n = len(nids)
            for i in range(n):
                edge_count[frozenset((nids[i], nids[(i + 1) % n]))] += 1
        boundary_nodes = {n for edge, cnt in edge_count.items() if cnt == 1 for n in edge}
        unwelded = boundary_nodes - all_other_nodes[name]
        total_boundary += len(boundary_nodes)
        total_unwelded += len(unwelded)
    print(f"Total boundary nodes: {total_boundary}, never shared with a neighbor: "
          f"{total_unwelded} ({100*total_unwelded/max(total_boundary,1):.1f}% gap)")


def main() -> None:
    result = build_and_mesh()
    n_elems = sum(len(v) for v in result["elements_by_component"].values())
    print(f"Input surfaces: {result['n_input_surfaces']}, "
          f"post-fragment surfaces: {result['n_output_surfaces']}")
    print(f"fragment: {result['fragment_seconds']:.1f}s, mesh: {result['mesh_seconds']:.1f}s")
    print(f"Total nodes: {len(result['xyz_by_tag'])}, total elements: {n_elems}")
    check_connectivity(result)
    write_bdf(result)
    print(f"\nWrote {OUTPUT_BDF}")


if __name__ == "__main__":
    main()
