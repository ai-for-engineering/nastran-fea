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
    assert bdf.properties[result.pid_by_component["LEFT"]].t == pytest.approx(0.1)
    assert bdf.properties[result.pid_by_component["RIGHT"]].t == pytest.approx(0.2)


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
