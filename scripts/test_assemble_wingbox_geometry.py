"""Tests for scripts/assemble_wingbox_geometry.py.

Uses small synthetic STEP files (two rectangles that share an edge, and a
control pair that doesn't touch at all) rather than the real NASA CRM IGES
midsurfaces -- those are gitignored, and this module's whole point is
never needing the slow/fragile CAD-level boolean operation the real
end-to-end verification (PR description) exercises against them; these
tests exist to pin down the node-welding logic itself, fast.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assemble_wingbox_geometry import (  # noqa: E402
    Component,
    _dedupe_exact_final_nodes,
    _is_bad_quad_geometry,
    _weld_coincident_nodes,
    mesh_assembly_to_bdf,
)
from geometry_to_bdf import MaterialProperties  # noqa: E402

ALUMINUM = MaterialProperties(mid=1, e=1.0e7, g=3.8e6, nu=0.31, rho=0.101)


def _write_rectangle_step(path: Path, x0: float, y0: float, dx: float, dy: float) -> None:
    import gmsh

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add(path.stem)
        gmsh.model.occ.addRectangle(x0, y0, 0.0, dx, dy)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()


@pytest.fixture
def adjacent_rectangles(tmp_path: Path) -> list[Component]:
    """Two 50x50 rectangles sharing the edge at x=50 -- meshed
    independently with a mesh size (10) that evenly divides the shared
    edge's length (50), so both components' independent 1D meshing should
    place nodes at the same positions along it, welding cleanly."""
    left = tmp_path / "left.step"
    right = tmp_path / "right.step"
    _write_rectangle_step(left, 0.0, 0.0, 50.0, 50.0)
    _write_rectangle_step(right, 50.0, 0.0, 50.0, 50.0)
    return [
        Component(name="LEFT", geometry_path=left, thickness=0.1),
        Component(name="RIGHT", geometry_path=right, thickness=0.2),
    ]


@pytest.fixture
def disjoint_rectangles(tmp_path: Path) -> list[Component]:
    """Two 50x50 rectangles nowhere near each other -- a control case
    where no node welding should occur, and both components should still
    show up complete in the merged mesh."""
    a = tmp_path / "a.step"
    b = tmp_path / "b.step"
    _write_rectangle_step(a, 0.0, 0.0, 50.0, 50.0)
    _write_rectangle_step(b, 1000.0, 1000.0, 50.0, 50.0)
    return [
        Component(name="A", geometry_path=a, thickness=0.1),
        Component(name="B", geometry_path=b, thickness=0.2),
    ]


def test_empty_components_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="non-empty"):
        mesh_assembly_to_bdf([], tmp_path / "out.bdf", mesh_size=10.0, material=ALUMINUM)


def test_missing_component_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        mesh_assembly_to_bdf(
            [Component(name="X", geometry_path=tmp_path / "missing.step", thickness=0.1)],
            tmp_path / "out.bdf",
            mesh_size=10.0,
            material=ALUMINUM,
        )


def test_single_component_still_works(tmp_path: Path):
    only = tmp_path / "only.step"
    _write_rectangle_step(only, 0.0, 0.0, 50.0, 50.0)
    result = mesh_assembly_to_bdf(
        [Component(name="ONLY", geometry_path=only, thickness=0.1)],
        tmp_path / "out.bdf",
        mesh_size=10.0,
        material=ALUMINUM,
    )
    assert result.success
    assert result.n_nodes > 0
    assert result.counts_by_component["ONLY"]["cquad4"] > 0
    assert result.n_welded_pairs == 0


def test_adjacent_rectangles_weld_nodes_at_interface(
    adjacent_rectangles: list[Component], tmp_path: Path
):
    from pyNastran.bdf.bdf import BDF

    out_path = tmp_path / "merged.bdf"
    result = mesh_assembly_to_bdf(
        adjacent_rectangles, out_path, mesh_size=10.0, material=ALUMINUM
    )

    assert result.success
    assert result.counts_by_component["LEFT"]["cquad4"] > 0
    assert result.counts_by_component["RIGHT"]["cquad4"] > 0
    # The shared edge is 50 units long with mesh_size=10 -- 6 nodes (0, 10,
    # ..., 50) should land at identical positions on both independently
    # meshed rectangles and get welded.
    assert result.n_welded_pairs >= 5

    bdf = BDF()
    bdf.read_bdf(str(out_path), xref=True)

    left_pid = result.pid_by_component["LEFT"]
    right_pid = result.pid_by_component["RIGHT"]
    left_nodes: set[int] = set()
    right_nodes: set[int] = set()
    for elem in bdf.elements.values():
        if elem.pid == left_pid:
            left_nodes.update(elem.node_ids)
        elif elem.pid == right_pid:
            right_nodes.update(elem.node_ids)

    shared = left_nodes & right_nodes
    assert shared, "expected welded (shared) nodes along the x=50 interface, found none"
    for nid in shared:
        x = bdf.nodes[nid].get_position()[0]
        assert x == pytest.approx(50.0, abs=1e-6)


def test_disjoint_rectangles_produce_no_welds_but_both_survive(
    disjoint_rectangles: list[Component], tmp_path: Path
):
    from pyNastran.bdf.bdf import BDF

    out_path = tmp_path / "merged.bdf"
    result = mesh_assembly_to_bdf(
        disjoint_rectangles, out_path, mesh_size=10.0, material=ALUMINUM
    )

    assert result.n_welded_pairs == 0
    assert "no nodes were welded" in " ".join(result.warnings)
    assert result.counts_by_component["A"]["cquad4"] > 0
    assert result.counts_by_component["B"]["cquad4"] > 0

    bdf = BDF()
    bdf.read_bdf(str(out_path), xref=True)
    pid_a = result.pid_by_component["A"]
    pid_b = result.pid_by_component["B"]
    nodes_a = {n for e in bdf.elements.values() if e.pid == pid_a for n in e.node_ids}
    nodes_b = {n for e in bdf.elements.values() if e.pid == pid_b for n in e.node_ids}
    assert not (nodes_a & nodes_b)


def test_per_component_thickness_and_shared_material(
    adjacent_rectangles: list[Component], tmp_path: Path
):
    from pyNastran.bdf.bdf import BDF

    out_path = tmp_path / "merged.bdf"
    result = mesh_assembly_to_bdf(
        adjacent_rectangles, out_path, mesh_size=10.0, material=ALUMINUM
    )

    bdf = BDF()
    bdf.read_bdf(str(out_path), xref=True)

    assert len(bdf.materials) == 1
    left_prop = bdf.properties[result.pid_by_component["LEFT"]]
    right_prop = bdf.properties[result.pid_by_component["RIGHT"]]
    assert left_prop.t == pytest.approx(0.1)
    assert right_prop.t == pytest.approx(0.2)
    # mid2 (bending)/mid3 (transverse shear) must be set, not left blank
    # -- see geometry_to_bdf.py's mesh_geometry_to_bdf for why (a blank
    # mid2 gives a membrane-only shell, confirmed to make MYSTRAN's
    # AUTOSPC silently auto-constrain every rotational DOF in a real
    # rebuilt model rather than raising a hard error).
    assert left_prop.mid2 == ALUMINUM.mid
    assert right_prop.mid2 == ALUMINUM.mid
    assert left_prop.mid3 == ALUMINUM.mid
    assert right_prop.mid3 == ALUMINUM.mid


def test_bounding_box_covers_both_components(
    adjacent_rectangles: list[Component], tmp_path: Path
):
    result = mesh_assembly_to_bdf(
        adjacent_rectangles, tmp_path / "out.bdf", mesh_size=10.0, material=ALUMINUM
    )
    assert result.bounding_box["x"]["min"] == pytest.approx(0.0, abs=1e-6)
    assert result.bounding_box["x"]["max"] == pytest.approx(100.0, abs=1e-6)
    assert result.bounding_box["y"]["max"] == pytest.approx(50.0, abs=1e-6)


def test_timing_fields_are_populated(adjacent_rectangles: list[Component], tmp_path: Path):
    result = mesh_assembly_to_bdf(
        adjacent_rectangles, tmp_path / "out.bdf", mesh_size=10.0, material=ALUMINUM
    )
    assert result.mesh_seconds >= 0.0
    assert result.weld_seconds >= 0.0


def test_weld_rejects_transitive_same_component_merge():
    """Regression test for a real bug found against the actual NASA CRM
    wingbox assembly: naive transitive union-find (weld every pair within
    radius) collapsed two genuinely distinct RIBS corners into one GRID
    because both independently landed within tolerance of the same nearby
    SPARS node, producing an invalid CQUAD4 with a repeated node ID
    (nodes=[2, 1062, 4513, 2]).

    Reproduced at minimal scale: A and B are two DIFFERENT points of
    component 0 that don't touch each other (1.8 apart), but C (component
    1) sits 0.9 from each of them -- within a merge_tolerance of 1.0 of
    both. Naive transitive welding would chain A~C~B into one cluster,
    silently merging two distinct same-component points. The fix must
    weld C to (at most) one of them and leave the other alone.
    """
    import numpy as np

    xyz = np.array(
        [
            [0.0, 0.0, 0.0],  # A, component 0
            [1.8, 0.0, 0.0],  # B, component 0
            [0.9, 0.0, 0.0],  # C, component 1
        ]
    )
    components = np.array([0, 0, 1])

    final_grid_id, final_xyz, n_welded_pairs = _weld_coincident_nodes(
        xyz, components, merge_tolerance=1.0
    )

    a_id, b_id = final_grid_id[0], final_grid_id[1]
    assert a_id != b_id, "two distinct same-component nodes must never share a GRID"
    # Exactly one weld should have happened (C to whichever of A/B was
    # processed first -- both are equidistant, so either is a valid,
    # correct outcome), not zero and not two.
    assert n_welded_pairs == 1
    c_id = final_grid_id[2]
    assert c_id == a_id or c_id == b_id


def test_weld_merges_exact_duplicate_same_component_nodes():
    """Regression test for a real bug found solving the actual rebuilt
    NASA CRM wingbox in MYSTRAN: CRM_ribs.igs alone (a single component,
    no merging with anything else involved) produced 445 pairs of GRID
    nodes at bit-identical coordinates -- most plausibly adjacent sub-
    faces within that one IGES file that touch without genuine B-rep
    topological sharing. Two such nodes both feeding one CQUAD4 gave it a
    real zero-length side, caught by MYSTRAN itself
    (*ERROR 1908: ... HAS LENGTH = ZERO). The original weld design only
    ever considered cross-component pairs, on the assumption that a
    single component's own gmsh mesh is always internally conformal --
    false here. Exact coordinate equality must weld regardless of
    component, since (unlike the tolerance-based cross-component case)
    there's no ambiguity about whether it's really the same point.
    """
    import numpy as np

    xyz = np.array(
        [
            [10.0, 10.0, 10.0],  # A, component 0
            [10.0, 10.0, 10.0],  # B, component 0 -- exact duplicate of A
            [50.0, 50.0, 50.0],  # C, component 0, unrelated
        ]
    )
    components = np.array([0, 0, 0])

    final_grid_id, _final_xyz, n_welded_pairs = _weld_coincident_nodes(
        xyz, components, merge_tolerance=1.0
    )

    assert final_grid_id[0] == final_grid_id[1]
    assert final_grid_id[2] != final_grid_id[0]
    assert n_welded_pairs == 1


def test_dedupe_exact_final_nodes_is_noop_when_already_clean():
    import numpy as np

    final_xyz = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [20.0, 0.0, 0.0]])
    final_grid_id = np.array([1, 2, 3, 1, 2])  # 5 raw rows -> 3 clean final nodes

    new_grid_id, new_xyz, n_extra = _dedupe_exact_final_nodes(final_grid_id, final_xyz)
    assert n_extra == 0
    assert new_grid_id is final_grid_id  # unchanged references, not just equal
    assert new_xyz is final_xyz


def test_dedupe_exact_final_nodes_merges_remaining_duplicates():
    """Simulates exactly the real bug found solving the actual rebuilt
    NASA CRM wingbox: _weld_coincident_nodes's own output can still
    contain exact-duplicate final positions (gmsh's meshing isn't
    perfectly run-to-run reproducible) -- this defensive follow-up must
    catch and merge them."""
    import numpy as np

    # final node 1 and final node 3 are exact duplicates that should
    # never have survived as separate GRIDs.
    final_xyz = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    final_grid_id = np.array([1, 2, 3, 1, 3])  # some raw rows point at each

    new_grid_id, new_xyz, n_extra = _dedupe_exact_final_nodes(final_grid_id, final_xyz)
    assert n_extra == 1
    assert len(new_xyz) == 2  # 3 final nodes collapsed to 2
    # Every raw row that pointed at old id 1 or 3 must now point at the
    # SAME merged id.
    merged_id = new_grid_id[0]
    assert new_grid_id[2] == merged_id  # was old id 3
    assert new_grid_id[3] == merged_id  # was old id 1
    assert new_grid_id[4] == merged_id  # was old id 3
    assert new_grid_id[1] != merged_id  # old id 2 stays distinct


def test_is_bad_quad_geometry_flags_real_failing_element():
    """The exact corner coordinates of CQUAD4 49690 from the real
    rebuilt NASA CRM wingbox, which MYSTRAN rejected with
    *ERROR 1928: ... HAS JACOBIAN LESS THAN OR EQUAL TO ZERO."""
    import numpy as np

    pts = [
        np.array([1046.461, 118.6596, -192.804]),
        np.array([1045.663, 117.3947, -192.815]),
        np.array([1045.024, 118.5041, -192.753]),
        np.array([1044.237, 118.582, -192.716]),
    ]
    assert _is_bad_quad_geometry(pts) is True


def test_is_bad_quad_geometry_accepts_valid_square():
    import numpy as np

    square = [
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
    ]
    assert _is_bad_quad_geometry(square) is False


def test_degenerate_element_is_skipped_not_fatal(monkeypatch, tmp_path: Path):
    """Regression test for a real failure solving the actual rebuilt NASA
    CRM wingbox: gmsh's own meshing occasionally produces an element with
    two corners within the exact-coincidence tolerance of each other (a
    genuinely near-zero-length edge, not a welding bug -- see
    _weld_coincident_nodes's docstring). Such an element must be skipped
    with a warning, not raise and abort the whole build.

    Forces this deterministically by monkeypatching _weld_coincident_nodes
    to collapse two of one element's own corners onto the same final GRID
    -- reproducing the exact condition found in the real mesh without
    depending on gmsh happening to generate one (its meshing isn't
    perfectly reproducible run to run, confirmed separately).
    """
    import assemble_wingbox_geometry as awg

    only = tmp_path / "only.step"
    _write_rectangle_step(only, 0.0, 0.0, 50.0, 50.0)

    import numpy as np

    def rigged_weld(xyz_array, component_array, merge_tolerance, same_element_partners=None):
        # Collapse every node down to just 2 distinct final GRID ids --
        # any element (4 corners for a CQUAD4) is then guaranteed to
        # reference duplicate ids, regardless of which raw rows actually
        # belong to which element.
        n = len(xyz_array)
        final_grid_id = (np.arange(n) % 2) + 1
        final_xyz = np.array([xyz_array[0], xyz_array[1] if n > 1 else xyz_array[0]])
        return final_grid_id, final_xyz, 0

    monkeypatch.setattr(awg, "_weld_coincident_nodes", rigged_weld)

    result = mesh_assembly_to_bdf(
        [Component(name="ONLY", geometry_path=only, thickness=0.1)],
        tmp_path / "out.bdf",
        mesh_size=10.0,
        material=ALUMINUM,
    )
    assert result.success
    assert result.n_degenerate_skipped > 0
    assert any("degenerate element" in w for w in result.warnings)
    # None of the (rigged, all-degenerate) elements should have been
    # written -- confirms skipping actually happened, not just counting.
    assert result.n_cquad4 == 0
    assert result.n_ctria3 == 0


def test_disjoint_rectangles_ignore_merge_tolerance(
    disjoint_rectangles: list[Component], tmp_path: Path
):
    """A merge_tolerance far larger than default still shouldn't weld two
    components that are genuinely 1000 units apart -- confirms the
    tolerance is a real distance check, not a blanket weld-everything
    switch once any tolerance is set."""
    result = mesh_assembly_to_bdf(
        disjoint_rectangles,
        tmp_path / "out.bdf",
        mesh_size=10.0,
        material=ALUMINUM,
        merge_tolerance=5.0,
    )
    assert result.n_welded_pairs == 0
