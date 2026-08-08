"""
Smoke tests for scripts/mcp_server.py.

load_model and patch_case_control are tested fully against small synthetic
BDFs generated here -- no external data needed.

get_max_stress is exercised end-to-end against real MYSTRAN-produced OP2s:
one test solves a tiny one-CQUAD4 model, another solves a simple cantilever
CBAR beam, each checking the parsed max-stress result for that element
type's branch (von_mises for plates, max_stress for bars) -- but only if
solver/ (gitignored, ~24MB, not present in every checkout/worktree -- see
CLAUDE.md) is actually available. Otherwise those tests are skipped with an
explicit reason rather than faked.

run_solver is not separately tested here beyond what get_max_stress's
end-to-end test already exercises: it's a thin, already-tested wrapper
(scripts/run_solver.py has its own docstring/commit-message history of
manual verification against real MYSTRAN behavior) and duplicating that
would just be re-testing run_solver.py, not this module.

render_model_view/render_stress_contour are exercised end-to-end against a
small synthetic two-property model (fast: a couple seconds of pyNastranGUI
startup overhead, not the ~90s+ the full NASA CRM wingbox takes to load --
that was verified manually during development, by eye, per issue #9's
acceptance criteria, not re-verified here). Gated on the solver being
present (to produce a real OP2) AND PyQt5/vtk being importable (pyNastranGUI
deps) -- skipped cleanly with an explicit reason otherwise, same pattern as
get_max_stress's end-to-end tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mcp_server as ms  # noqa: E402
from run_solver import DEFAULT_SOLVER_PATH  # noqa: E402

PROPER_BDF = """\
SOL 101
CEND
ECHO = NONE
SPC = 2
LOAD = 3
DISPLACEMENT = ALL
STRESS = ALL
BEGIN BULK
GRID,1,,0.0,0.0,0.0
GRID,2,,1.0,0.0,0.0
GRID,3,,1.0,1.0,0.0
GRID,4,,0.0,1.0,0.0
CQUAD4,1,1,1,2,3,4
PSHELL,1,1,0.1
MAT1,1,1.0E7,,0.3
SPC1,2,123456,1,4
FORCE,3,2,,1.0,1.0,0.0,0.0
ENDDATA
"""

# OptiStruct-style: no SOL/CEND, "ANALYSIS STATICS" instead, but the same
# SPC/LOAD request lines pyNastran/MYSTRAN case control also uses.
OPTISTRUCT_BDF = """\
ANALYSIS STATICS
SPC = 2
LOAD = 3
$ optistruct-style header, no SOL/CEND
BEGIN BULK
GRID,1,,0.0,0.0,0.0
GRID,2,,1.0,0.0,0.0
GRID,3,,1.0,1.0,0.0
GRID,4,,0.0,1.0,0.0
CQUAD4,1,1,1,2,3,4
PSHELL,1,1,0.1
MAT1,1,1.0E7,,0.3
SPC1,2,123456,1,4
FORCE,3,2,,1.0,1.0,0.0,0.0
ENDDATA
"""

# A simple cantilever beam (single CBAR, fixed at node 1, transverse tip load
# at node 2) -- used to exercise get_max_stress's bar-type ("max_stress",
# not "von_mises") branch against real MYSTRAN output. Built via pyNastran's
# API (see the cbar_bdf fixture) rather than hand-typed card text: PBAR's
# stress-recovery-point fields (C1/C2/D1/D2/E1/E2/F1/F2) span a continuation
# line, and without them MYSTRAN recovers stress at y=z=0 -- giving ~zero
# bending stress even under a real bending load -- so getting that
# continuation line exactly right matters, which the API guarantees and
# hand-typed text doesn't.
CBAR_CASE_CONTROL = """\
SOL 101
CEND
ECHO = NONE
SPC = 1
LOAD = 1
DISPLACEMENT = ALL
STRESS = ALL
BEGIN BULK
"""


@pytest.fixture
def proper_bdf(tmp_path: Path) -> Path:
    path = tmp_path / "proper.bdf"
    path.write_text(PROPER_BDF)
    return path


@pytest.fixture
def optistruct_bdf(tmp_path: Path) -> Path:
    path = tmp_path / "optistruct.bdf"
    path.write_text(OPTISTRUCT_BDF)
    return path


@pytest.fixture
def cbar_bdf(tmp_path: Path) -> Path:
    from pyNastran.bdf.bdf import BDF

    model = BDF()
    model.add_grid(1, [0.0, 0.0, 0.0])
    model.add_grid(2, [10.0, 0.0, 0.0])
    model.add_cbar(1, 1, [1, 2], x=[0.0, 0.0, 1.0], g0=None)
    # Rectangular-section stress recovery points at the four corners
    # (+-0.5 in each direction) so bending stress is actually nonzero.
    model.add_pbar(
        1, 1, A=1.0, i1=0.0833, i2=0.0833, j=0.1406,
        c1=0.5, c2=0.5, d1=0.5, d2=-0.5,
        e1=-0.5, e2=-0.5, f1=-0.5, f2=0.5,
    )
    model.add_mat1(1, 1.0e7, None, 0.3)
    model.add_spc1(1, "123456", [1])
    model.add_force(1, 2, 100.0, [0.0, 1.0, 0.0])

    bulk_path = tmp_path / "cbar_bulk.bdf"
    model.write_bdf(str(bulk_path), size=8, enddata=True)

    path = tmp_path / "cbar.bdf"
    path.write_text(CBAR_CASE_CONTROL + bulk_path.read_text())
    return path


# ---------------------------------------------------------------------------
# load_model
# ---------------------------------------------------------------------------

def test_load_model_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ms.load_model(str(tmp_path / "does_not_exist.bdf"))


def test_load_model_proper_deck(proper_bdf: Path):
    result = ms.load_model(str(proper_bdf))
    assert result["success"] is True
    assert result["counts"] == {
        "nodes": 4,
        "elements": 1,
        "properties": 1,
        "materials": 1,
    }
    assert result["card_count"]["CQUAD4"] == 1
    assert result["warnings"] == []


def test_load_model_optistruct_deck_reports_structured_failure(optistruct_bdf: Path):
    """An OptiStruct-style deck (no SOL/CEND) can't be parsed by pyNastran's
    BDF reader as-is -- load_model should report that as a structured
    failure (so a caller can recover via patch_case_control), not raise."""
    result = ms.load_model(str(optistruct_bdf))
    assert result["success"] is False
    assert "error" in result
    assert result["error"]


# ---------------------------------------------------------------------------
# patch_case_control
# ---------------------------------------------------------------------------

def test_patch_case_control_rebuilds_optistruct_header(
    optistruct_bdf: Path, tmp_path: Path
):
    out_path = tmp_path / "patched.bdf"
    result = ms.patch_case_control(str(optistruct_bdf), str(out_path))

    assert result["patched"] is True
    assert out_path.is_file()

    text = out_path.read_text()
    assert "SOL 101" in text
    assert "CEND" in text
    # SPC/LOAD from the original OptiStruct header must be preserved, not
    # dropped or hardcoded to some other value.
    assert "SPC = 2" in text
    assert "LOAD = 3" in text
    # Sensible defaults filled in since the OptiStruct header didn't specify
    # them.
    assert "DISPLACEMENT = ALL" in text
    assert "STRESS = ALL" in text

    # And the patched deck must now actually be parseable by pyNastran.
    load_result = ms.load_model(str(out_path))
    assert load_result["success"] is True
    assert load_result["counts"]["elements"] == 1


def test_patch_case_control_noop_on_proper_deck(proper_bdf: Path, tmp_path: Path):
    out_path = tmp_path / "copy.bdf"
    result = ms.patch_case_control(str(proper_bdf), str(out_path))

    assert result["patched"] is False
    assert out_path.read_text() == proper_bdf.read_text()


def test_patch_case_control_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ms.patch_case_control(
            str(tmp_path / "does_not_exist.bdf"), str(tmp_path / "out.bdf")
        )


# ---------------------------------------------------------------------------
# get_max_stress
# ---------------------------------------------------------------------------

def test_get_max_stress_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ms.get_max_stress(str(tmp_path / "does_not_exist.OP2"))


@pytest.mark.skipif(
    not Path(DEFAULT_SOLVER_PATH).is_file(),
    reason=(
        "MYSTRAN solver binary not present (solver/ is gitignored, ~24MB -- "
        "see README setup instructions); cannot produce a real OP2 to parse "
        "in this environment."
    ),
)
def test_get_max_stress_end_to_end(proper_bdf: Path):
    """Solve the tiny synthetic CQUAD4 model for real and check the
    max-stress parsing logic (including the double-fiber-entry-per-element
    handling) against genuine MYSTRAN OP2 output."""
    solver_result = ms.run_solver(str(proper_bdf))
    assert solver_result["success"], solver_result["errors"]

    stress_result = ms.get_max_stress(solver_result["op2_path"])
    assert set(stress_result.keys()) == {"cquad4"}
    cquad4 = stress_result["cquad4"]
    assert cquad4["element_id"] == 1
    assert cquad4["subcase"] == 1
    assert cquad4["von_mises"] > 0


@pytest.mark.skipif(
    not Path(DEFAULT_SOLVER_PATH).is_file(),
    reason=(
        "MYSTRAN solver binary not present (solver/ is gitignored, ~24MB -- "
        "see README setup instructions); cannot produce a real OP2 to parse "
        "in this environment."
    ),
)
def test_get_max_stress_end_to_end_cbar(cbar_bdf: Path):
    """Solve a simple cantilever CBAR beam for real and check the bar-type
    branch: reports "max_stress" (not "von_mises", since bar direct stress
    isn't the same physical quantity as plate von Mises), and doesn't get
    fooled by the large margin-of-safety sentinel values (~1e10) that
    pyNastran fills in when a margin isn't computed."""
    solver_result = ms.run_solver(str(cbar_bdf))
    assert solver_result["success"], solver_result["errors"]

    stress_result = ms.get_max_stress(solver_result["op2_path"])
    assert set(stress_result.keys()) == {"cbar"}
    cbar = stress_result["cbar"]
    assert cbar["element_id"] == 1
    assert cbar["subcase"] == 1
    assert "max_stress" in cbar
    assert "von_mises" not in cbar
    # A real bending stress on a loaded cantilever, not a margin-of-safety
    # sentinel value (which would be ~1e10).
    assert 0 < cbar["max_stress"] < 1_000_000


# ---------------------------------------------------------------------------
# render_model_view / render_stress_contour
# ---------------------------------------------------------------------------

@pytest.fixture
def two_property_bdf(tmp_path: Path) -> Path:
    """Two CQUAD4 elements sharing an edge, each with its own PSHELL
    property -- enough to exercise hide_property_ids meaningfully (hiding
    one property leaves exactly one element)."""
    from pyNastran.bdf.bdf import BDF

    model = BDF()
    model.add_grid(1, [0.0, 0.0, 0.0])
    model.add_grid(2, [1.0, 0.0, 0.0])
    model.add_grid(3, [2.0, 0.0, 0.0])
    model.add_grid(4, [0.0, 1.0, 0.0])
    model.add_grid(5, [1.0, 1.0, 0.0])
    model.add_grid(6, [2.0, 1.0, 0.0])
    model.add_cquad4(1, 1, [1, 2, 5, 4])
    model.add_cquad4(2, 2, [2, 3, 6, 5])
    model.add_pshell(1, mid1=1, t=0.1)
    model.add_pshell(2, mid1=1, t=0.1)
    model.add_mat1(1, 1.0e7, None, 0.3)
    model.add_spc1(1, "123456", [1, 4])
    model.add_force(1, 3, 100.0, [0.0, 0.0, 1.0])
    model.add_force(1, 6, 100.0, [0.0, 0.0, 1.0])

    bulk_path = tmp_path / "two_prop_bulk.bdf"
    model.write_bdf(str(bulk_path), size=8, enddata=True)

    path = tmp_path / "two_prop.bdf"
    path.write_text(CBAR_CASE_CONTROL + bulk_path.read_text())
    return path


def test_render_model_view_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ms.render_model_view(str(tmp_path / "does_not_exist.bdf"), str(tmp_path / "out.png"))


def test_render_model_view_invalid_camera(proper_bdf: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="camera"):
        ms.render_model_view(str(proper_bdf), str(tmp_path / "out.png"), camera="bogus")


def test_render_model_view_hide_groups_without_ses_path(proper_bdf: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="ses_path"):
        ms.render_model_view(str(proper_bdf), str(tmp_path / "out.png"), hide_groups=["Skins"])


def _rendering_deps_available() -> bool:
    try:
        import PyQt5  # noqa: F401
        import vtk  # noqa: F401
    except ImportError:
        return False
    return True


_RENDER_SKIP_REASON = (
    "MYSTRAN solver and/or PyQt5/vtk (pyNastranGUI deps) not available in "
    "this environment -- see README's solver/GUI setup sections."
)


@pytest.mark.skipif(
    not (Path(DEFAULT_SOLVER_PATH).is_file() and _rendering_deps_available()),
    reason=_RENDER_SKIP_REASON,
)
def test_render_model_view_end_to_end(two_property_bdf: Path, tmp_path: Path):
    output_png = tmp_path / "view.png"
    result = ms.render_model_view(
        str(two_property_bdf), str(output_png), hide_property_ids=[2],
    )
    assert result["success"], result.get("errors")
    assert result["hidden_element_count"] == 1
    assert output_png.is_file()
    assert output_png.stat().st_size > 0


@pytest.mark.skipif(
    not (Path(DEFAULT_SOLVER_PATH).is_file() and _rendering_deps_available()),
    reason=_RENDER_SKIP_REASON,
)
def test_render_stress_contour_end_to_end(two_property_bdf: Path, tmp_path: Path):
    solver_result = ms.run_solver(str(two_property_bdf))
    assert solver_result["success"], solver_result["errors"]

    output_png = tmp_path / "contour.png"
    result = ms.render_stress_contour(
        str(two_property_bdf), solver_result["op2_path"], str(output_png),
    )
    assert result["success"], result.get("errors")
    assert result["fringe_set"] is True
    assert output_png.is_file()
    assert output_png.stat().st_size > 0
