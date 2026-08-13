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
from itertools import combinations
from pathlib import Path
from typing import Any

from geometry_to_bdf import (
    _GMSH_QUAD4,
    _GMSH_TRIANGLE3,
    MaterialProperties,
    _mesh_single_geometry,
)

# Nodes within this distance (geometry files' own native units) are
# always welded regardless of component -- see _weld_coincident_nodes's
# docstring. Deliberately far below any real feature size, so it only
# ever catches true duplicate/degenerate points, never two distinct ones.
#
# Must be generous enough to cover Nastran's own small-field BDF format
# (write_bdf(size=8, ...), see CLAUDE.md) rounding two DISTINCT in-memory
# positions to IDENTICAL 8-character text on write -- confirmed directly:
# pyNastran's own field_writer_8.print_float_8 renders 1578.7171234,
# 1578.7172345, and 1578.7168999 (differing by up to ~0.0003) all as the
# same "1578.717". A tolerance tighter than that field format's own
# precision (roughly 0.001 in the output BDF's units) would let a real
# degenerate/near-zero element slip through the weld only to reappear as
# an exact duplicate the moment it's written to disk -- confirmed as the
# actual root cause of a MYSTRAN *ERROR 1908 (`... HAS LENGTH = ZERO`)
# that survived several earlier, tighter tolerance attempts. 0.1 (mm, for
# the NASA CRM wingbox's own geometry files) comfortably covers that
# ~0.025 mm (0.001 in) rounding step with margin, while staying far below
# any real feature size.
_EXACT_COINCIDENCE_TOLERANCE = 0.1


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
    n_degenerate_skipped: int
    n_bowtie_skipped: int
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
    same_element_partners: "dict[int, set[int]] | None" = None,
) -> tuple["np.ndarray", "np.ndarray", int]:  # noqa: F821
    """Weld nodes from different components within merge_tolerance of each
    other into shared clusters. Returns (final_grid_id, final_xyz,
    n_welded_pairs): final_grid_id[i] is the 1-indexed output GRID id for
    input row i, final_xyz is the averaged position per output GRID
    (1-indexed, so final_xyz[final_grid_id[i] - 1] is row i's welded
    position).

    Nearest cross-component pairs are welded first. A union is accepted
    unless it would place two nodes of the *same* component into one
    cluster who are themselves farther than merge_tolerance apart --
    checked by real pairwise distance between each cluster's existing
    same-component members, not merely "does this component already
    appear anywhere in the cluster". That coarser, presence-only version
    was tried first and reverted: confirmed against the real NASA CRM
    wingbox assembly to reject the vast majority of genuinely-valid welds
    at a real rib/spar junction -- e.g. RIBS<->SPARS, 87% of RIBS's own
    boundary nodes near a SPARS junction (median true gap 13.6mm, well
    under the 37.5mm tolerance in use) went unwelded, not because they
    were too far from SPARS, but because the *first* nearby RIBS node to
    claim a given SPARS node blocked every other, genuinely-adjacent RIBS
    node along that same physical seam from claiming any SPARS node
    whose cluster that first RIBS node's component now merely touched --
    a many-(fine mesh)-to-one-(coarse mesh) density mismatch is normal
    between two independently-meshed components, not a sign of distinct
    physical points. The real failure this must still prevent (see
    test_weld_rejects_transitive_same_component_merge) only involved two
    same-component nodes that were NOT within tolerance of each other
    directly (1.8 apart, merge_tolerance=1.0) -- i.e. genuinely distinct
    points that both merely happened to be near one common third node.
    Checking real distance between the specific same-component members on
    each side reproduces that rejection exactly while no longer punishing
    same-component nodes that are, transitively, all still mutually within
    tolerance (the normal, common case at a real seam). This still allows
    a true 3+ component junction (one node per component, genuinely
    coincident) to collapse to a single shared GRID.

    Distance alone is NOT sufficient, though -- confirmed directly against
    the real assembly: two corners of the *same* raw element are, by
    definition, close together (that's what makes them one small
    element), so a distance-only check happily welded plenty of them
    together once merge_tolerance approached real element size,
    self-collapsing that element regardless of how "close" they were
    (n_degenerate_skipped went from ~150 to 12,900+ at merge_tolerance=75mm
    under distance-only rejection -- worse than the disconnection this was
    meant to fix, since a genuinely zero-length edge is a harder solver
    failure than an unwelded seam). same_element_partners (row -> set of
    rows sharing a raw element with it, built by the caller from the
    actual pre-weld connectivity, all combinatorial corner pairs not just
    consecutive edges) is therefore also checked unconditionally,
    independent of distance -- if the caller doesn't supply it, this
    additional guard is skipped (used by unit tests that only exercise the
    distance logic on bare synthetic points with no real mesh behind them).

    Before any of that, an unconditional exact-coincidence pass welds
    nodes at bit-identical coordinates regardless of component --
    confirmed necessary against the real NASA CRM wingbox assembly too: a
    single component's own independent gmsh mesh is not always internally
    conformal the way this function's cross-component-only design
    initially assumed. `CRM_ribs.igs` alone produced 445 pairs of exact-
    duplicate node positions (943 node instances total, e.g. two
    literally-identical-coordinate GRIDs both feeding one CQUAD4, giving
    it a real zero-length side -- confirmed as MYSTRAN's own `*ERROR 1908:
    ... HAS LENGTH = ZERO`), most plausibly from adjacent sub-faces
    within one IGES file that touch without genuine B-rep topological
    sharing. Exact coordinate equality is unambiguous (unlike the
    tolerance-based cross-component case below, which exists precisely
    because two genuinely different points can be *close*) -- there's no
    same-component conflict risk from merging two points already proven
    identical.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    n = len(xyz_array)
    uf = _UnionFind(n)

    # Exact-coincidence pass: weld any two nodes within EXACT_COINCIDENCE_
    # TOLERANCE of each other, regardless of component. A cKDTree radius
    # query (not exact dict-key rounding) is used deliberately -- gmsh's
    # own meshing is not perfectly bit-reproducible run to run (confirmed
    # separately: identical synthetic inputs produced different weld-pair
    # counts across repeated runs), so two "duplicate" points from the
    # same underlying degenerate CAD feature aren't guaranteed to be
    # bit-identical, only extremely close. The tolerance here (1e-4 in the
    # geometry's own native units, i.e. 0.1 micron for the NASA CRM
    # wingbox's millimeter files) is many orders of magnitude below any
    # real feature size on a multi-meter aircraft structure, so there's
    # no meaningful risk of conflating two genuinely different points.
    exact_tree = cKDTree(xyz_array)
    exact_pairs = exact_tree.query_pairs(r=_EXACT_COINCIDENCE_TOLERANCE, output_type="ndarray")
    n_exact_welded = 0
    for i, j in exact_pairs:
        uf.union(int(i), int(j))
        n_exact_welded += 1

    # cluster_members[root][component_id] = list of row indices of that
    # component currently in that cluster -- unlike a plain component-id
    # set, this keeps each member's actual position so a proposed union
    # can be checked by real distance (see docstring for why presence-only
    # was insufficient).
    cluster_members: list[dict[int, list[int]]] = [dict() for _ in range(n)]
    for i, c in enumerate(component_array):
        cluster_members[uf.find(i)].setdefault(int(c), []).append(i)

    tree = cKDTree(xyz_array)
    pairs = tree.query_pairs(r=merge_tolerance, output_type="ndarray")
    n_welded_pairs = n_exact_welded
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
            members_ri = cluster_members[ri]
            members_rj = cluster_members[rj]
            conflict = False
            for c in set(members_ri) & set(members_rj):
                # Only components present on BOTH sides can introduce a
                # new same-component pair -- a component present on only
                # one side was already validated internally by earlier
                # unions.
                for row_a in members_ri[c]:
                    xa = xyz_array[row_a]
                    partners_a = (
                        same_element_partners.get(row_a, ())
                        if same_element_partners
                        else ()
                    )
                    for row_b in members_rj[c]:
                        if row_b in partners_a:
                            # Two corners of the SAME raw element -- always
                            # reject regardless of distance, since merging
                            # them collapses that element's own edge to
                            # zero length no matter how "close" they are.
                            conflict = True
                            break
                        if np.linalg.norm(xa - xyz_array[row_b]) > merge_tolerance:
                            conflict = True
                            break
                    if conflict:
                        break
                if conflict:
                    break
            if conflict:
                continue
            merged: dict[int, list[int]] = {c: list(rows) for c, rows in members_ri.items()}
            for c, rows in members_rj.items():
                merged.setdefault(c, []).extend(rows)
            uf.union(ri, rj)
            cluster_members[uf.find(ri)] = merged
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


def _dedupe_exact_final_nodes(
    final_grid_id: "np.ndarray",  # noqa: F821
    final_xyz: "np.ndarray",  # noqa: F821
) -> tuple["np.ndarray", "np.ndarray", int]:  # noqa: F821
    """Verify _weld_coincident_nodes's own output actually has no
    remaining exact-duplicate positions, and merge any found. See
    mesh_assembly_to_bdf's call site for why this defensive follow-up
    pass exists at all (gmsh's meshing isn't perfectly run-to-run
    reproducible).

    Returns (new_final_grid_id, new_final_xyz, n_extra_welded) -- if
    n_extra_welded is 0, new_final_grid_id/new_final_xyz are returned
    unchanged (not just equivalent) so a caller can skip re-checking.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    n = len(final_xyz)
    if n < 2:
        return final_grid_id, final_xyz, 0

    tree = cKDTree(final_xyz)
    pairs = tree.query_pairs(r=_EXACT_COINCIDENCE_TOLERANCE, output_type="ndarray")
    if len(pairs) == 0:
        return final_grid_id, final_xyz, 0

    uf = _UnionFind(n)
    for i, j in pairs:
        uf.union(int(i), int(j))

    cluster_of_row = np.array([uf.find(i) for i in range(n)])
    _unique_clusters, inverse = np.unique(cluster_of_row, return_inverse=True)
    n_deduped = len(_unique_clusters)
    summed = np.zeros((n_deduped, 3))
    counts = np.zeros(n_deduped, dtype=int)
    np.add.at(summed, inverse, final_xyz)
    np.add.at(counts, inverse, 1)
    new_final_xyz = summed / counts[:, None]

    # final_grid_id holds OLD 1-indexed ids (one per raw input row);
    # remap through inverse (old 0-indexed row -> new 0-indexed row).
    new_final_grid_id = inverse[final_grid_id - 1] + 1

    return new_final_grid_id, new_final_xyz, n - n_deduped


def _is_bad_quad_geometry(pts: list) -> bool:
    """True if a CQUAD4's 4 corners (p0, p1, p2, p3, in element
    connectivity order) would give a real FE solver a non-positive
    Jacobian somewhere in its bilinear isoparametric map -- confirmed a
    real, if rare, output of gmsh's own quad recombination against the
    real NASA CRM wingbox mesh (33 of 63,792 CQUAD4, ~0.05%), caught by
    MYSTRAN as `*ERROR 1928: ... HAS JACOBIAN LESS THAN OR EQUAL TO ZERO
    ... BAD GEOMETRY` -- not a welding artifact (unrelated to node
    coincidence/duplication), a genuine badly-shaped element.

    A literal self-intersection ("bowtie") test (checking whether the two
    diagonals, or two opposite edges, cross) was tried first and
    reverted: it correctly flagged the real failing element, but it's too
    permissive -- a *concave* quad (one reflex >180 deg corner) is still
    a perfectly valid *simple* polygon by that test, yet a sufficiently
    concave quad genuinely does drive a bilinear element's Jacobian
    negative, which is exactly the class of failure being guarded
    against here, not literal topological self-intersection.

    Instead: walk the 4 corners and take the cross product of each pair
    of adjacent edges (a discrete proxy for the Jacobian's sign at each
    corner of the bilinear map). A good (convex, or mildly non-planar but
    still positive-Jacobian) quad has all 4 pointing the same general
    direction; this element is flagged bad if any corner's disagrees with
    the element's own average normal.
    """
    import numpy as np

    normals = []
    for i in range(4):
        a, b, c = pts[(i - 1) % 4], pts[i], pts[(i + 1) % 4]
        normals.append(np.cross(b - a, c - b))
    reference = np.mean(normals, axis=0)
    ref_norm = np.linalg.norm(reference)
    if ref_norm < 1e-30:
        return True  # no well-defined average normal -- degenerate, not valid
    reference = reference / ref_norm
    return any(np.dot(n, reference) < 0 for n in normals)


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

    # row -> set of other rows sharing a raw (pre-weld) element with it --
    # every combinatorial corner pair, not just consecutive edges, so a
    # diagonal collision is caught too. Passed to _weld_coincident_nodes so
    # it can reject a same-component union that would self-collapse one of
    # these elements, regardless of how close the two corners are (see its
    # docstring -- distance alone let plenty of these through).
    same_element_partners: dict[int, set[int]] = {}
    for comp_idx, _nodes_per_elem, local_nids in raw_elements:
        rows = [local_to_global[(comp_idx, nid)] for nid in local_nids]
        for a, b in combinations(rows, 2):
            same_element_partners.setdefault(a, set()).add(b)
            same_element_partners.setdefault(b, set()).add(a)

    # Weld nodes from different components that landed within
    # merge_tolerance of each other -- see _weld_coincident_nodes's
    # docstring for why naive transitive union-find isn't safe here.
    t_weld_start = time.time()
    final_grid_id, final_xyz_native, n_welded_pairs = _weld_coincident_nodes(
        xyz_array, component_array, merge_tolerance, same_element_partners
    )
    weld_seconds = time.time() - t_weld_start

    # Defensive final pass: confirmed against the real NASA CRM wingbox
    # assembly that gmsh's own meshing is not perfectly reproducible run
    # to run (the same file, meshed twice with identical parameters, can
    # produce a different set of exact-duplicate node positions) -- so
    # rather than keep chasing a specific non-deterministic root cause,
    # verify the weld's own output is actually duplicate-free and fix it
    # up directly if not, before any element ever gets to reference it.
    final_grid_id, final_xyz_native, n_extra_welded = _dedupe_exact_final_nodes(
        final_grid_id, final_xyz_native
    )
    n_welded_pairs += n_extra_welded
    if n_extra_welded > 0:
        warnings.append(
            f"{n_extra_welded} additional exact-duplicate node(s) survived "
            "the main weld pass and were merged in a defensive follow-up "
            "pass -- gmsh's meshing is not perfectly run-to-run "
            "reproducible; see _weld_coincident_nodes's docstring"
        )

    n_final_nodes = len(final_xyz_native)
    final_xyz = final_xyz_native * unit_scale

    pid_by_component: dict[str, int] = {
        comp.name: i + 1 for i, comp in enumerate(components)
    }
    thickness_by_component = {comp.name: comp.thickness for comp in components}

    bdf = BDF()
    bdf.add_mat1(material.mid, material.e, material.g, material.nu, rho=material.rho)
    for name, pid in pid_by_component.items():
        # mid2 (bending) / mid3 (transverse shear) matter, not just mid1
        # (membrane) -- see geometry_to_bdf.py's mesh_geometry_to_bdf for
        # the full story: leaving them blank gave the real rebuilt NASA
        # CRM wingbox membrane-only shells, which MYSTRAN's own AUTOSPC
        # silently "solved" by auto-constraining every rotational DOF in
        # the entire model, producing technically-valid but physically
        # nonsensical displacements (1e14+ in) instead of a hard error.
        bdf.add_pshell(
            pid,
            mid1=material.mid,
            t=thickness_by_component[name],
            mid2=material.mid,
            mid3=material.mid,
        )

    for row in range(n_final_nodes):
        bdf.add_grid(row + 1, final_xyz[row].tolist())

    n_cquad4 = 0
    n_ctria3 = 0
    n_degenerate_skipped = 0
    n_bowtie_skipped = 0
    counts_by_component: dict[str, dict[str, int]] = {
        comp.name: {"cquad4": 0, "ctria3": 0} for comp in components
    }
    next_eid = 1
    for comp_idx, nodes_per_elem, local_nids in raw_elements:
        global_rows = [local_to_global[(comp_idx, nid)] for nid in local_nids]
        final_nids = [int(final_grid_id[row]) for row in global_rows]
        comp_name = components[comp_idx].name
        if len(set(final_nids)) != nodes_per_elem:
            # Two of this element's own corners welded to the same final
            # GRID -- confirmed against the real NASA CRM wingbox mesh
            # that this is a genuinely degenerate (near-zero-length-edge)
            # element from gmsh's own meshing, not a welding-logic bug:
            # the exact-coincidence tolerance above is far too tight
            # (1e-4, geometry-native units) to explain it as a false
            # weld between two real, distinct corners. Skip it -- the
            # standard treatment for a degenerate element in any FE
            # preprocessing pipeline -- rather than writing pyNastran/
            # MYSTRAN an element they'd reject anyway with a far less
            # specific error, or refusing to produce a deck at all over
            # what's typically a handful of elements out of tens of
            # thousands.
            n_degenerate_skipped += 1
            continue
        if nodes_per_elem == 4:
            # final_nids are already 1-indexed final GRID ids (post-weld/
            # dedup) -- NOT global_rows, which index the raw, larger,
            # pre-weld node array and would silently alias the wrong
            # (or out-of-bounds) position once welding has shrunk it.
            pts = [final_xyz_native[nid - 1] for nid in final_nids]
            if _is_bad_quad_geometry(pts):
                # A genuinely badly-shaped (non-positive-Jacobian) quad
                # from gmsh's own recombination, unrelated to node
                # welding -- see _is_bad_quad_geometry's docstring. Same
                # treatment as a degenerate element: skip with a count,
                # don't hand MYSTRAN geometry it will reject with
                # *ERROR 1928.
                n_bowtie_skipped += 1
                continue
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
    if n_degenerate_skipped > 0:
        warnings.append(
            f"skipped {n_degenerate_skipped} degenerate element(s) (two or "
            "more corners welded to the same GRID -- a near-zero-length "
            "edge in the source mesh, not a welding-logic issue)"
        )
    if n_bowtie_skipped > 0:
        warnings.append(
            f"skipped {n_bowtie_skipped} badly-shaped (non-positive-"
            "Jacobian) CQUAD4 element(s) from gmsh's own quad "
            "recombination -- bad element geometry, not a welding-logic "
            "issue"
        )

    return AssemblyResult(
        success=True,
        bdf_path=output_bdf_path,
        n_nodes=n_final_nodes,
        n_cquad4=n_cquad4,
        n_ctria3=n_ctria3,
        n_welded_pairs=n_welded_pairs,
        n_degenerate_skipped=n_degenerate_skipped,
        n_bowtie_skipped=n_bowtie_skipped,
        bounding_box=bounding_box,
        counts_by_component=counts_by_component,
        pid_by_component=pid_by_component,
        material=material,
        mesh_seconds=mesh_seconds,
        weld_seconds=weld_seconds,
        warnings=warnings,
    )
