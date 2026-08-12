"""
Merge multiple independently-authored midsurface IGES/STEP files into one
connected mesh and write it to a Nastran BDF with one PSHELL per input
component -- the multi-part follow-up to geometry_to_bdf.py's single-
component pipeline (see that module's docstring for why single-component
was the deliberate v1 scope).

Strategy, and why it's node-welding rather than CAD-level fragmenting:
meshing each component independently leaves them topologically
*disconnected* at their real-world shared edges (a rib doesn't share
nodes with the spar it's riveted to). The textbook fix is an OpenCASCADE
boolean fragment across all component files before meshing, so shared
edges become one shared topological curve both sides mesh identically.
**That was tried first and abandoned** after hitting real, reproducible
tooling limits on the actual NASA CRM wingbox geometry:

- Fragmenting just 2 of the 5 component files (ribs + spars, 91 of 469
  total faces) took 234 seconds.
- The fragment result contained sliver edges as short as 1.6e-5 mm --
  numerical noise from two independently-modeled surfaces meeting at a
  shared edge that was never bit-for-bit coincident to begin with -- which
  fail 1D meshing outright ("1D mesh cannot be divided by 2").
- `gmsh.model.occ.healShapes()` could not reliably resolve this: at its
  default/small tolerance it left the sliver count essentially unchanged
  (194 -> 44 curves under 0.1 mm, all still present after healing), and at
  a larger tolerance (5 mm, still tiny relative to a multi-meter wingbox)
  it crashed outright ("Could not fix wire in surface 594") instead of
  degrading gracefully.

This module instead meshes each component **independently** (reusing
geometry_to_bdf.py's proven single-component pipeline via
`_mesh_single_geometry`) and then **welds coincident nodes across
components** in a post-processing pass: any two nodes from *different*
components within `merge_tolerance` of each other are treated as the same
physical point and collapsed to one GRID. This is a standard, well-
precedented FE preprocessing operation (the same thing a "coincident node
equivalence" tool in a commercial preprocessor does), it never touches
OpenCASCADE's boolean engine, and it directly reuses code already proven
to work on this real geometry. The tradeoff, stated plainly: it's an
approximate connection (nodes within tolerance, not a mathematically exact
shared curve), not a perfect one -- acceptable for this project's actual
goal (a solvable, structurally connected model to compare against the
original), not for anything claiming CAD-exact fidelity.

Note the same error message, two unrelated real causes: "1D mesh cannot
be divided by 2" shows up both from the abandoned CAD-fragment approach's
slivers above, AND separately from a pure quad-recombination *parity*
failure that can hit a single, perfectly clean geometry file with no
merging involved at all -- confirmed on `CRM_ribs.igs` alone (meshes fine
at `mesh_size=150`, fails this way at `mesh_size=200`, triangulates fine
at *both* sizes) and on `CRM_stringers.igs` (all 3 available file variants
first looked like inherent unfixable degenerate geometry, until
recombination alone turned out to be the actual cause -- see
`_mesh_single_geometry`'s docstring for the automatic fallback this
module relies on). Don't assume every instance of this message is a CAD
problem; check whether recombination is involved first.

Validated end to end against the real, full 5-component NASA CRM wingbox
IGES download (ribs/spars/skins/rib_caps/stringers, all of it, no
exclusions): 71,628 nodes, 63,792 CQUAD4 + 15,455 CTRIA3 (stringers alone
fell back to triangles), 14,758 welded node pairs, ~13s wall time,
bounding box within 0.13% of the real solved model's actual span -- vs.
234+ seconds and outright failure for just 2 of 5 components under the
abandoned CAD-fragment approach.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geometry_to_bdf import (
    _GMSH_QUAD4,
    _GMSH_TRIANGLE3,
    MaterialProperties,
    _mesh_single_geometry,
)


@dataclass
class Component:
    """One midsurface geometry file to merge in, and the PSHELL thickness
    its elements should get once meshed."""

    name: str
    geometry_path: str | Path
    thickness: float


@dataclass
class AssemblyResult:
    success: bool
    bdf_path: Path
    n_nodes: int
    n_cquad4: int
    n_ctria3: int
    n_welded_pairs: int
    bounding_box: dict[str, dict[str, float]]
    counts_by_component: dict[str, dict[str, int]]
    pid_by_component: dict[str, int]
    material: MaterialProperties
    mesh_seconds: float
    weld_seconds: float
    warnings: list[str] = field(default_factory=list)


class _UnionFind:
    """Minimal union-find (disjoint-set) with path compression, used to
    collapse chains/clusters of coincident nodes (e.g. 3+ components
    meeting at one corner) into a single representative, not just pairs."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        root = i
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[i] != root:
            self._parent[i], i = root, self._parent[i]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


def _weld_coincident_nodes(
    xyz_array: "np.ndarray",  # noqa: F821
    component_array: "np.ndarray",  # noqa: F821
    merge_tolerance: float,
) -> tuple["np.ndarray", "np.ndarray", int]:  # noqa: F821
    """Weld nodes from different components within merge_tolerance of each
    other into shared clusters. Returns (final_grid_id, final_xyz,
    n_welded_pairs): final_grid_id[i] is the 1-indexed output GRID id for
    input row i, final_xyz is the averaged position per output GRID
    (1-indexed, so final_xyz[final_grid_id[i] - 1] is row i's welded
    position).

    Nearest cross-component pairs are welded first, and a union is only
    accepted if neither side's existing cluster already contains a node
    from the other's component -- confirmed necessary against the real
    NASA CRM wingbox assembly, where naive transitive union-find (weld
    every pair within radius, regardless of order) collapsed two distinct
    RIBS corners into one GRID because both independently landed within
    tolerance of the same nearby SPARS node, producing an invalid
    self-degenerate CQUAD4 (two of its own corners identical). This still
    allows a true 3+ component junction (one node per component,
    genuinely coincident) to collapse to a single shared GRID -- it only
    ever rejects a union that would put two nodes from the *same*
    component in one cluster.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    n = len(xyz_array)
    uf = _UnionFind(n)
    cluster_components: list[set[int]] = [{int(c)} for c in component_array]

    tree = cKDTree(xyz_array)
    pairs = tree.query_pairs(r=merge_tolerance, output_type="ndarray")
    n_welded_pairs = 0
    if len(pairs) > 0:
        cross = component_array[pairs[:, 0]] != component_array[pairs[:, 1]]
        pairs = pairs[cross]
        dists = np.linalg.norm(xyz_array[pairs[:, 0]] - xyz_array[pairs[:, 1]], axis=1)
        order = np.argsort(dists)
        for k in order:
            i, j = int(pairs[k, 0]), int(pairs[k, 1])
            ri, rj = uf.find(i), uf.find(j)
            if ri == rj:
                continue
            if cluster_components[ri] & cluster_components[rj]:
                # Accepting this union would put two nodes from the same
                # component in one cluster -- reject it.
                continue
            merged = cluster_components[ri] | cluster_components[rj]
            uf.union(ri, rj)
            cluster_components[uf.find(ri)] = merged
            n_welded_pairs += 1

    cluster_of_row = np.array([uf.find(i) for i in range(n)])
    _unique_clusters, inverse = np.unique(cluster_of_row, return_inverse=True)
    n_final_nodes = len(_unique_clusters)
    summed = np.zeros((n_final_nodes, 3))
    counts = np.zeros(n_final_nodes, dtype=int)
    np.add.at(summed, inverse, xyz_array)
    np.add.at(counts, inverse, 1)
    final_xyz = summed / counts[:, None]
    final_grid_id = inverse + 1  # 1-indexed
    return final_grid_id, final_xyz, n_welded_pairs


def mesh_assembly_to_bdf(
    components: list[Component],
    output_bdf_path: str | Path,
    mesh_size: float,
    material: MaterialProperties | dict[str, Any],
    *,
    unit_scale: float = 1.0,
    quad_dominant: bool = True,
    merge_tolerance: float | None = None,
) -> AssemblyResult:
    """Mesh each component independently, weld nodes from different
    components that land within `merge_tolerance` of each other, and
    write GRID + CQUAD4/CTRIA3 (one PSHELL per component, all sharing one
    MAT1) to a Nastran BDF.

    Args:
        components: geometry files to merge, each with its own PSHELL
            thickness.
        output_bdf_path: where to write the resulting BDF.
        mesh_size: target element size, in the geometry files' own native
            units (all components must share one unit system).
        material: MaterialProperties (or equivalent dict) for the single
            shared MAT1 every component's PSHELL references.
        unit_scale: multiplies every meshed node coordinate before
            writing.
        quad_dominant: ask Gmsh to recombine triangles into quads where
            it can.
        merge_tolerance: nodes from different components within this
            distance (in geometry_path's native units, NOT unit_scale'd --
            consistent with mesh_size) are welded into one GRID. Defaults
            to mesh_size / 4 -- generous enough to catch independently-
            meshed nodes that land near, but not exactly on, a shared
            edge, while staying well below a typical element's own size
            so unrelated nearby-but-distinct nodes aren't accidentally
            welded.

    Returns:
        AssemblyResult, including mesh_seconds/weld_seconds (so a caller
        can see where time actually went), n_welded_pairs (nodes actually
        merged -- a sanity signal that welding did something; 0 would
        mean components never really touched, worth investigating rather
        than assuming success), and counts_by_component to confirm no
        component's elements silently vanished.

    Raises:
        FileNotFoundError: a component's geometry_path doesn't exist.
        ValueError: components is empty, or a component's geometry has no
            2D surfaces.
    """
    import time

    import numpy as np
    from pyNastran.bdf.bdf import BDF

    if not components:
        raise ValueError("components must be non-empty")
    if isinstance(material, dict):
        material = MaterialProperties(**material)
    if merge_tolerance is None:
        merge_tolerance = mesh_size / 4.0

    for comp in components:
        if not Path(comp.geometry_path).is_file():
            raise FileNotFoundError(f"geometry file not found: {comp.geometry_path}")

    warnings: list[str] = []

    # Mesh every component in its own independent gmsh session (no CAD
    # interaction between components at all -- see module docstring).
    # gmsh's own node/element tags reset per session, so everything is
    # immediately renumbered into one global, collision-free ID space
    # (component_of[i] tracks provenance through the rest of the pipeline).
    global_xyz: list[np.ndarray] = []
    component_of: list[int] = []
    # (component_index, local_node_tag) -> global row index into global_xyz
    local_to_global: dict[tuple[int, int], int] = {}
    raw_elements: list[tuple[int, int, list[int]]] = []  # (component_index, n_nodes, local_node_tags)

    t_mesh_start = time.time()
    for comp_idx, comp in enumerate(components):
        (
            node_tags,
            node_coords,
            elem_types,
            elem_tags_list,
            elem_node_tags_list,
            used_quad_dominant,
        ) = _mesh_single_geometry(Path(comp.geometry_path), mesh_size, quad_dominant)
        if quad_dominant and not used_quad_dominant:
            warnings.append(
                f"component {comp.name!r}: quad recombination failed on "
                "this geometry (see _mesh_single_geometry's docstring), "
                "fell back to an all-triangle mesh for this component"
            )
        xyz = node_coords.reshape(-1, 3)
        for tag, pos in zip(node_tags, xyz):
            local_to_global[(comp_idx, int(tag))] = len(global_xyz)
            global_xyz.append(pos)
            component_of.append(comp_idx)

        for etype, etags, enodes in zip(elem_types, elem_tags_list, elem_node_tags_list):
            if etype == _GMSH_TRIANGLE3:
                nodes_per_elem = 3
            elif etype == _GMSH_QUAD4:
                nodes_per_elem = 4
            else:
                warnings.append(
                    f"skipped {len(etags)} element(s) of unsupported gmsh "
                    f"type {etype} on component {comp.name!r}"
                )
                continue
            enodes = enodes.reshape(-1, nodes_per_elem)
            for nids in enodes:
                raw_elements.append((comp_idx, nodes_per_elem, [int(n) for n in nids]))
    mesh_seconds = time.time() - t_mesh_start

    if not global_xyz:
        raise ValueError("no nodes produced by any component")

    xyz_array = np.array(global_xyz)
    component_array = np.array(component_of)

    # Weld nodes from different components that landed within
    # merge_tolerance of each other -- see _weld_coincident_nodes's
    # docstring for why naive transitive union-find isn't safe here.
    t_weld_start = time.time()
    final_grid_id, final_xyz_native, n_welded_pairs = _weld_coincident_nodes(
        xyz_array, component_array, merge_tolerance
    )
    weld_seconds = time.time() - t_weld_start
    n_final_nodes = len(final_xyz_native)
    final_xyz = final_xyz_native * unit_scale

    pid_by_component: dict[str, int] = {
        comp.name: i + 1 for i, comp in enumerate(components)
    }
    thickness_by_component = {comp.name: comp.thickness for comp in components}

    bdf = BDF()
    bdf.add_mat1(material.mid, material.e, material.g, material.nu, rho=material.rho)
    for name, pid in pid_by_component.items():
        bdf.add_pshell(pid, mid1=material.mid, t=thickness_by_component[name])

    for row in range(n_final_nodes):
        bdf.add_grid(row + 1, final_xyz[row].tolist())

    n_cquad4 = 0
    n_ctria3 = 0
    counts_by_component: dict[str, dict[str, int]] = {
        comp.name: {"cquad4": 0, "ctria3": 0} for comp in components
    }
    next_eid = 1
    for comp_idx, nodes_per_elem, local_nids in raw_elements:
        global_rows = [local_to_global[(comp_idx, nid)] for nid in local_nids]
        final_nids = [int(final_grid_id[row]) for row in global_rows]
        comp_name = components[comp_idx].name
        if len(set(final_nids)) != nodes_per_elem:
            # Safety net, not expected to trigger: the greedy conflict-
            # checked weld above is specifically designed to prevent two
            # of one element's own corners from ever collapsing into the
            # same GRID. If it ever does happen anyway (e.g. a genuinely
            # degenerate zero-area element in the source geometry, not a
            # welding bug), fail loudly with the actual IDs rather than
            # write pyNastran an element it will reject anyway with a far
            # less specific error at read-back time.
            raise RuntimeError(
                f"component {comp_name!r} element with local nodes "
                f"{local_nids} welded to duplicate final node IDs "
                f"{final_nids} -- degenerate element, not a valid "
                f"{'CTRIA3' if nodes_per_elem == 3 else 'CQUAD4'}"
            )
        pid = pid_by_component[comp_name]
        eid = next_eid
        next_eid += 1
        if nodes_per_elem == 3:
            bdf.add_ctria3(eid, pid, final_nids)
            n_ctria3 += 1
            counts_by_component[comp_name]["ctria3"] += 1
        else:
            bdf.add_cquad4(eid, pid, final_nids)
            n_cquad4 += 1
            counts_by_component[comp_name]["cquad4"] += 1

    output_bdf_path = Path(output_bdf_path)
    output_bdf_path.parent.mkdir(parents=True, exist_ok=True)
    bdf.write_bdf(str(output_bdf_path), size=8, enddata=True)

    bounding_box = {
        axis: {"min": float(final_xyz[:, i].min()), "max": float(final_xyz[:, i].max())}
        for i, axis in enumerate("xyz")
    }

    if n_welded_pairs == 0 and len(components) > 1:
        warnings.append(
            "no nodes were welded across components -- merge_tolerance "
            f"({merge_tolerance}) may be too small, or these components "
            "genuinely don't touch"
        )

    return AssemblyResult(
        success=True,
        bdf_path=output_bdf_path,
        n_nodes=n_final_nodes,
        n_cquad4=n_cquad4,
        n_ctria3=n_ctria3,
        n_welded_pairs=n_welded_pairs,
        bounding_box=bounding_box,
        counts_by_component=counts_by_component,
        pid_by_component=pid_by_component,
        material=material,
        mesh_seconds=mesh_seconds,
        weld_seconds=weld_seconds,
        warnings=warnings,
    )
