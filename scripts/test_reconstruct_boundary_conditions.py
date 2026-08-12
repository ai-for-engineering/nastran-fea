"""Tests for scripts/reconstruct_boundary_conditions.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reconstruct_boundary_conditions import (  # noqa: E402
    add_spc_by_y_band,
    add_uniform_z_load,
)

from pyNastran.bdf.bdf import BDF  # noqa: E402


def _bdf_with_grid_line(n: int = 11, spacing: float = 10.0) -> BDF:
    """A BDF with n GRID nodes at Y = 0, spacing, 2*spacing, ..., all at
    X=Z=0 -- enough to exercise Y-band selection without needing any
    elements at all."""
    bdf = BDF()
    for i in range(n):
        bdf.add_grid(i + 1, [0.0, i * spacing, 0.0])
    return bdf


def test_add_spc_by_y_band_selects_only_nodes_in_band():
    bdf = _bdf_with_grid_line(n=11, spacing=10.0)  # Y = 0, 10, ..., 100
    n_constrained = add_spc_by_y_band(bdf, spc_id=1, y_target=50.0, y_tolerance=5.0, components="123")
    assert n_constrained == 1  # only the node at exactly Y=50
    spc = bdf.spcs[1][0]
    assert spc.nodes == [6]  # node 6 sits at Y=50 (i=5, 0-indexed * 10)
    assert spc.components == "123"


def test_add_spc_by_y_band_widens_to_catch_multiple_nodes():
    bdf = _bdf_with_grid_line(n=11, spacing=10.0)
    n_constrained = add_spc_by_y_band(bdf, spc_id=1, y_target=50.0, y_tolerance=15.0, components="3")
    # Y=40, 50, 60 all fall within 15 of 50.
    assert n_constrained == 3


def test_add_spc_by_y_band_raises_when_nothing_in_band():
    bdf = _bdf_with_grid_line(n=11, spacing=10.0)
    with pytest.raises(ValueError, match="no GRID nodes found"):
        add_spc_by_y_band(bdf, spc_id=1, y_target=1000.0, y_tolerance=1.0, components="123")


def test_add_uniform_z_load_distributes_evenly_and_matches_resultant():
    bdf = _bdf_with_grid_line(n=10, spacing=10.0)
    summary = add_uniform_z_load(bdf, load_id=1, total_force_z=1000.0)

    assert summary["n_nodes"] == 10
    assert summary["per_node_force"] == pytest.approx(100.0)
    assert summary["resultant"] == pytest.approx(1000.0)

    forces = bdf.loads[1]
    assert len(forces) == 10
    total = sum(f.mag * f.xyz[2] for f in forces)
    assert total == pytest.approx(1000.0)
    for f in forces:
        assert f.xyz == pytest.approx([0.0, 0.0, 1.0])


def test_add_uniform_z_load_total_independent_of_node_count():
    """The whole point: a sparser or denser mesh gets the same total
    resultant, not the same per-node magnitude."""
    sparse = _bdf_with_grid_line(n=5, spacing=25.0)
    dense = _bdf_with_grid_line(n=50, spacing=2.5)

    sparse_summary = add_uniform_z_load(sparse, load_id=1, total_force_z=500.0)
    dense_summary = add_uniform_z_load(dense, load_id=1, total_force_z=500.0)

    assert sparse_summary["resultant"] == pytest.approx(500.0)
    assert dense_summary["resultant"] == pytest.approx(500.0)
    assert sparse_summary["per_node_force"] != pytest.approx(dense_summary["per_node_force"])


def test_add_uniform_z_load_raises_on_empty_bdf():
    bdf = BDF()
    with pytest.raises(ValueError, match="no GRID nodes"):
        add_uniform_z_load(bdf, load_id=1, total_force_z=100.0)
