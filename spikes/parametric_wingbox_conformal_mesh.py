"""Proof of concept: a synthetic wingbox (2 spars, N ribs, top+bottom skin)
built parametrically in ONE gmsh OpenCASCADE session, boolean-fragmented,
and meshed -- to test whether the real NASA CRM wingbox's node-connectivity
gap (see CLAUDE.md's Gotchas -- ~23% of near-boundary node pairs
structurally unweldable even after fixing assemble_wingbox_geometry.py's
weld-conflict bug) is a limitation of the *meshing approach*, or of the
*source CAD data* (NASA's 5 independently-exported "dumb" IGES files, with
no shared topology encoded at all).

Hypothesis: it's the source data, not the approach. `gmsh.model.occ.
fragment` was already tried directly on the real IGES files and abandoned
(234s for 2/5 components, unhealable sub-micron slivers) -- see
assemble_wingbox_geometry.py's module docstring. But that geometry was
re-imported from loosely-toleranced IGES exports where two "coincident"
edges from different files are only approximately equal, never exactly.
This script builds all surfaces directly as exact parametric rectangles in
one shared gmsh session (no export/re-import round trip, no per-component
tolerance loss) -- if fragment+mesh produces a FULLY conformal mesh here
(zero unwelded gap) where it failed on the real IGES data, that confirms
the gap is a CAD-data-quality problem, not a fundamental limit of the
open-source gmsh+pyNastran+MYSTRAN stack.

Run: ./venv/Scripts/python.exe spikes/parametric_wingbox_conformal_mesh.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Synthetic wingbox geometry, all dimensions in mm (matching the real case
# study's own native geometry units).
X_FRONT_SPAR = 0.0
X_REAR_SPAR = 300.0
Z_BOTTOM = 0.0
Z_TOP = 50.0
Y_MIN = 0.0
Y_MAX = 1000.0
RIB_STATION_SPACING = 200.0
MESH_SIZE = 25.0

OUTPUT_BDF = REPO_ROOT / "spikes" / "output" / "synthetic_wingbox_conformal.bdf"


def _rib_stations() -> list[float]:
    n = int(round((Y_MAX - Y_MIN) / RIB_STATION_SPACING))
    return [Y_MIN + i * RIB_STATION_SPACING for i in range(n + 1)]


def build_and_mesh() -> dict:
    import gmsh
    import numpy as np

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("synthetic_wingbox")
        occ = gmsh.model.occ

        def add_quad_surface(corners: list[tuple[float, float, float]]) -> int:
            pts = [occ.addPoint(*c) for c in corners]
            lines = [occ.addLine(pts[i], pts[(i + 1) % 4]) for i in range(4)]
            loop = occ.addCurveLoop(lines)
            return occ.addPlaneSurface([loop])

        component_of_input: dict[int, str] = {}
        input_dimtags: list[tuple[int, int]] = []

        # SPARS: rectangles in the Y-Z plane, at X = front/rear spar.
        for name, x in [("SPAR_FRONT", X_FRONT_SPAR), ("SPAR_REAR", X_REAR_SPAR)]:
            corners = [
                (x, Y_MIN, Z_BOTTOM),
                (x, Y_MAX, Z_BOTTOM),
                (x, Y_MAX, Z_TOP),
                (x, Y_MIN, Z_TOP),
            ]
            tag = add_quad_surface(corners)
            component_of_input[tag] = name
            input_dimtags.append((2, tag))

        # SKINS: rectangles in the X-Y plane, at Z = bottom/top.
        for name, z in [("SKIN_BOTTOM", Z_BOTTOM), ("SKIN_TOP", Z_TOP)]:
            corners = [
                (X_FRONT_SPAR, Y_MIN, z),
                (X_REAR_SPAR, Y_MIN, z),
                (X_REAR_SPAR, Y_MAX, z),
                (X_FRONT_SPAR, Y_MAX, z),
            ]
            tag = add_quad_surface(corners)
            component_of_input[tag] = name
            input_dimtags.append((2, tag))

        # RIBS: rectangles in the X-Z plane, at each spanwise station.
        for i, y in enumerate(_rib_stations()):
            corners = [
                (X_FRONT_SPAR, y, Z_BOTTOM),
                (X_REAR_SPAR, y, Z_BOTTOM),
                (X_REAR_SPAR, y, Z_TOP),
                (X_FRONT_SPAR, y, Z_TOP),
            ]
            tag = add_quad_surface(corners)
            component_of_input[tag] = f"RIB_{i}"
            input_dimtags.append((2, tag))

        occ.synchronize()
        n_input_surfaces = len(input_dimtags)

        # The actual fix under test: fragment ALL surfaces together, in one
        # CAD session, so intersecting surfaces genuinely share topology
        # (a common edge becomes ONE curve both sides mesh identically) --
        # unlike assemble_wingbox_geometry.py's independent-mesh-then-weld,
        # and unlike the abandoned real-IGES fragment attempt, this input
        # has no export/tolerance loss to begin with.
        out, out_map = occ.fragment(input_dimtags, [])
        occ.synchronize()

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
        gmsh.model.mesh.generate(2)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        xyz_by_tag = {
            int(t): (node_coords[3 * i], node_coords[3 * i + 1], node_coords[3 * i + 2])
            for i, t in enumerate(node_tags)
        }

        elements_by_component: dict[str, list[list[int]]] = {}
        for surf_tag, comp_name in surface_component.items():
            elem_types, elem_tags_list, elem_node_tags_list = gmsh.model.mesh.getElements(2, surf_tag)
            for etype, enodes in zip(elem_types, elem_node_tags_list):
                npe = {2: 3, 3: 4}.get(etype)  # gmsh type 2=tri3, 3=quad4
                if npe is None:
                    continue
                enodes = np.array(enodes).reshape(-1, npe)
                elements_by_component.setdefault(comp_name, []).extend(
                    [int(n) for n in row] for row in enodes
                )

        return {
            "n_input_surfaces": n_input_surfaces,
            "n_output_surfaces": n_output_surfaces,
            "xyz_by_tag": xyz_by_tag,
            "elements_by_component": elements_by_component,
        }
    finally:
        gmsh.finalize()


def write_bdf(result: dict) -> None:
    from pyNastran.bdf.bdf import BDF

    bdf = BDF(debug=False)
    bdf.add_mat1(1, 1.0e7, 3.8e6, 0.31, rho=0.101)
    pid_by_component = {name: i + 1 for i, name in enumerate(sorted(result["elements_by_component"]))}
    for name, pid in pid_by_component.items():
        bdf.add_pshell(pid, mid1=1, t=0.1, mid2=1, mid3=1)

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
    """Mirror the diagnostic used against the real NASA CRM assembly: for
    every pair of components, how many nodes are shared vs. how many
    boundary nodes never found a partner at all."""
    from collections import Counter
    from itertools import combinations

    nodes_by_component = {
        name: {n for elem in elems for n in elem}
        for name, elems in result["elements_by_component"].items()
    }

    print(f"Input surfaces: {result['n_input_surfaces']}, "
          f"output (post-fragment) surfaces: {result['n_output_surfaces']}")
    print(f"Total nodes: {len(result['xyz_by_tag'])}")
    print()
    print("Cross-component shared nodes (RIBS shown only for the first/last for brevity):")
    names = sorted(nodes_by_component)
    shown = 0
    for a, b in combinations(names, 2):
        # Only report pairs that are geometrically adjacent (spar<->skin,
        # spar<->rib, rib<->skin) -- rib-to-rib and spar-to-spar never touch.
        is_spar_a, is_skin_a, is_rib_a = "SPAR" in a, "SKIN" in a, "RIB" in a
        is_spar_b, is_skin_b, is_rib_b = "SPAR" in b, "SKIN" in b, "RIB" in b
        adjacent = (is_spar_a and (is_skin_b or is_rib_b)) or (is_skin_a and (is_spar_b or is_rib_b)) or (is_rib_a and (is_spar_b or is_skin_b))
        if not adjacent:
            continue
        shared = len(nodes_by_component[a] & nodes_by_component[b])
        if shown < 12 or shared == 0:
            print(f"  {a:12s} <-> {b:12s}: {shared:4d} shared nodes")
            shown += 1

    # Boundary (free-edge) nodes per component, and how many are unwelded
    # (i.e. never appear in any other component's node set) -- the exact
    # metric that showed an 88% gap on the real NASA CRM assembly.
    print()
    print("Per-component boundary-node connectivity:")
    all_other_nodes = {
        name: set().union(*(nodes_by_component[o] for o in names if o != name))
        for name in names
    }
    total_boundary = 0
    total_unwelded = 0
    for name, elems in result["elements_by_component"].items():
        edge_count = Counter()
        own_nodes = nodes_by_component[name]
        for nids in elems:
            n = len(nids)
            for i in range(n):
                edge_count[frozenset((nids[i], nids[(i + 1) % n]))] += 1
        boundary_nodes = set()
        for edge, cnt in edge_count.items():
            if cnt == 1:
                boundary_nodes.update(edge)
        unwelded = boundary_nodes - all_other_nodes[name]
        total_boundary += len(boundary_nodes)
        total_unwelded += len(unwelded)
    print(f"  TOTAL across all components: {total_boundary} boundary nodes, "
          f"{total_unwelded} never shared with any neighboring component "
          f"({100*total_unwelded/max(total_boundary,1):.1f}% gap)")
    print("  (compare: the real NASA CRM assembly showed 43-99% gap per pair, "
          "even after fixing the weld-conflict bug)")


def main() -> None:
    result = build_and_mesh()
    write_bdf(result)
    check_connectivity(result)
    print(f"\nWrote {OUTPUT_BDF}")


if __name__ == "__main__":
    main()
