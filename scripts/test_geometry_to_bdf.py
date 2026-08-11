"""Tests for scripts/geometry_to_bdf.py.

Uses a small STEP file generated on the fly (a flat rectangle, via Gmsh's
own OpenCASCADE kernel) rather than the real NASA CRM IGES midsurfaces --
those are gitignored (~110MB download, see case_studies/nasa_crm_wingbox/
README.md) and not guaranteed present in every checkout/CI run. The
end-to-end check against the real CRM_ribs.igs file lives in this
project's own manual verification (PR description), not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from geometry_to_bdf import MaterialProperties, mesh_geometry_to_bdf  # noqa: E402

ALUMINUM = MaterialProperties(mid=1, e=1.0e7, g=3.8e6, nu=0.31, rho=0.101)


@pytest.fixture
def rectangle_step(tmp_path: Path) -> Path:
    """A flat 100x50 rectangle in the XY plane (z=0), written as a STEP
    file -- stands in for a real midsurface panel without needing any
    external download."""
    import gmsh

    path = tmp_path / "rectangle.step"
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("rectangle")
        gmsh.model.occ.addRectangle(0.0, 0.0, 0.0, 100.0, 50.0)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()
    return path


@pytest.fixture
def line_only_step(tmp_path: Path) -> Path:
    """A single 1D edge, no surface at all -- exercises the "not a
    midsurface/shell geometry" error path."""
    import gmsh

    path = tmp_path / "line.step"
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("line")
        p1 = gmsh.model.occ.addPoint(0.0, 0.0, 0.0)
        p2 = gmsh.model.occ.addPoint(100.0, 0.0, 0.0)
        gmsh.model.occ.addLine(p1, p2)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()
    return path


def test_missing_geometry_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        mesh_geometry_to_bdf(
            geometry_path=tmp_path / "does_not_exist.step",
            output_bdf_path=tmp_path / "out.bdf",
            mesh_size=20.0,
            thickness=0.1,
            material=ALUMINUM,
        )


def test_no_surfaces_raises(line_only_step: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="no 2D surfaces"):
        mesh_geometry_to_bdf(
            geometry_path=line_only_step,
            output_bdf_path=tmp_path / "out.bdf",
            mesh_size=20.0,
            thickness=0.1,
            material=ALUMINUM,
        )


def test_mesh_rectangle_end_to_end(rectangle_step: Path, tmp_path: Path):
    out_path = tmp_path / "rectangle.bdf"
    result = mesh_geometry_to_bdf(
        geometry_path=rectangle_step,
        output_bdf_path=out_path,
        mesh_size=20.0,
        thickness=0.1,
        material=ALUMINUM,
    )

    assert result.success
    assert result.bdf_path == out_path
    assert out_path.is_file()
    assert result.n_nodes > 0
    # A flat rectangle recombines cleanly into quads with no leftovers.
    assert result.n_cquad4 > 0
    assert result.n_ctria3 == 0
    assert result.warnings == []

    bbox = result.bounding_box
    assert bbox["x"]["min"] == pytest.approx(0.0, abs=1e-6)
    assert bbox["x"]["max"] == pytest.approx(100.0, abs=1e-6)
    assert bbox["y"]["min"] == pytest.approx(0.0, abs=1e-6)
    assert bbox["y"]["max"] == pytest.approx(50.0, abs=1e-6)
    assert bbox["z"]["min"] == pytest.approx(0.0, abs=1e-6)
    assert bbox["z"]["max"] == pytest.approx(0.0, abs=1e-6)


def test_mesh_rectangle_round_trips_through_pynastran(rectangle_step: Path, tmp_path: Path):
    from pyNastran.bdf.bdf import BDF

    out_path = tmp_path / "rectangle.bdf"
    result = mesh_geometry_to_bdf(
        geometry_path=rectangle_step,
        output_bdf_path=out_path,
        mesh_size=20.0,
        thickness=0.25,
        material=ALUMINUM,
        pshell_id=7,
    )

    bdf = BDF()
    bdf.read_bdf(str(out_path), xref=True)
    assert len(bdf.nodes) == result.n_nodes
    assert len(bdf.elements) == result.n_cquad4 + result.n_ctria3
    assert bdf.properties[7].t == pytest.approx(0.25)
    assert bdf.materials[1].e == pytest.approx(1.0e7)
    assert bdf.materials[1].nu == pytest.approx(0.31)


def test_unit_scale_applies_to_coordinates_only(rectangle_step: Path, tmp_path: Path):
    out_path = tmp_path / "rectangle_scaled.bdf"
    result = mesh_geometry_to_bdf(
        geometry_path=rectangle_step,
        output_bdf_path=out_path,
        mesh_size=20.0,
        thickness=0.1,
        material=ALUMINUM,
        unit_scale=1.0 / 25.4,
    )

    assert result.bounding_box["x"]["max"] == pytest.approx(100.0 / 25.4, abs=1e-6)
    assert result.bounding_box["y"]["max"] == pytest.approx(50.0 / 25.4, abs=1e-6)


def test_material_accepts_plain_dict(rectangle_step: Path, tmp_path: Path):
    result = mesh_geometry_to_bdf(
        geometry_path=rectangle_step,
        output_bdf_path=tmp_path / "out.bdf",
        mesh_size=20.0,
        thickness=0.1,
        material={"mid": 3, "e": 1.0e7, "g": 3.8e6, "nu": 0.31, "rho": 0.101},
    )
    assert result.material.mid == 3
    assert result.pshell_id == 1  # default pshell_id, independent of material mid


def test_quad_dominant_false_still_succeeds(rectangle_step: Path, tmp_path: Path):
    result = mesh_geometry_to_bdf(
        geometry_path=rectangle_step,
        output_bdf_path=tmp_path / "out.bdf",
        mesh_size=20.0,
        thickness=0.1,
        material=ALUMINUM,
        quad_dominant=False,
    )
    assert result.success
    assert (result.n_cquad4 + result.n_ctria3) > 0
