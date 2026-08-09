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


class _FakeBarStressArray:
    """Minimal stand-in for a pyNastran bar-stress result array -- just
    enough of the .get_headers()/.data/.element interface _peak_for_result
    actually uses, so its column-classification logic (_BAR_COLUMN_INFO) can
    be unit-tested without a real MYSTRAN solve."""

    def __init__(self, headers: list[str], row: list[float]):
        import numpy as np

        self._headers = headers
        self.data = np.array([[row]])  # shape (1 time, 1 entry, n columns)
        self.element = np.array([42])

    def get_headers(self) -> list[str]:
        return self._headers


_CBAR_HEADERS = [
    "s1a", "s2a", "s3a", "s4a", "axial", "smaxa", "smina", "MS_tension",
    "s1b", "s2b", "s3b", "s4b", "smaxb", "sminb", "MS_compression",
]


def test_peak_for_result_reports_axial_component_with_no_end():
    # All bending columns zero, pure axial load -- the sentinel MS columns
    # (1e10) must not win despite being numerically larger.
    row = [0, 0, 0, 0, 500.0, 500.0, 500.0, 1e10, 0, 0, 0, 0, 500.0, 500.0, 1e10]
    peak = ms._peak_for_result(_FakeBarStressArray(_CBAR_HEADERS, row))
    assert peak["max_stress"] == 500.0
    assert peak["component"] == "axial"
    assert "end" not in peak


def test_peak_for_result_reports_combined_component_and_end_b():
    # Bending-only columns (s1b-s4b) stay small; smaxb/sminb (the combined
    # axial+bending extreme) are the unambiguous largest-magnitude columns,
    # so those -- not a bending-only column -- must govern.
    row = [10, 10, 10, 10, 50.0, 60, -40, 1e10, 20, 20, 20, 20, 900.0, -800.0, 1e10]
    peak = ms._peak_for_result(_FakeBarStressArray(_CBAR_HEADERS, row))
    assert peak["max_stress"] == 900.0
    assert peak["component"] == "combined (axial + bending)"
    assert peak["end"] == "B"


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
    isn't the same physical quantity as plate von Mises), doesn't get
    fooled by the large margin-of-safety sentinel values (~1e10) that
    pyNastran fills in when a margin isn't computed, and correctly
    identifies which column governed: this model is fixed at node 1 (end A)
    with a pure transverse load at the free tip (node 2, end B) and no axial
    load at all -- max bending moment on a cantilever is at the fixed end,
    so the governing value must be bending at end A, not axial or end B."""
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
    assert cbar["component"] == "bending"
    assert cbar["end"] == "A"


# ---------------------------------------------------------------------------
# describe_loads_and_boundary_conditions
# ---------------------------------------------------------------------------

# A single global-case-control deck (no SUBCASE cards -- pyNastran reports
# this as subcase id 0) whose SPC/LOAD sets are combined via SPCADD/LOAD
# rather than referenced directly, to exercise the recursive-resolution
# path: SPCADD 20 combines SPC1 sets 2 (node 1, full 6-dof) and 3 (node 4,
# dof 3 only); LOAD 30 combines FORCE sets 3 and 31 with an overall scale of
# 1.0 and per-set scale factors 2.0/1.0.
COMBINATION_BDF = """\
SOL 101
CEND
ECHO = NONE
SPC = 20
LOAD = 30
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
SPCADD,20,2,3
SPC1,2,123456,1
SPC1,3,3,4
LOAD,30,1.0,2.0,3,1.0,31
FORCE,3,2,,1.0,1.0,0.0,0.0
FORCE,31,2,,1.0,0.0,0.0,1.0
ENDDATA
"""

# Two real SUBCASE cards sharing the same SPC set, one of them (Modes) with
# no LOAD request at all -- exercises the "requested vs not requested"
# distinction (boundary_conditions/loads should be None, not an empty dict,
# when a subcase doesn't ask for one) and the LABEL lookup.
MULTI_SUBCASE_BDF = """\
SOL 101
CEND
SUBCASE 1
LABEL = Modes
SPC = 2
SUBCASE 2
LABEL = GVW
SPC = 2
LOAD = 3
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


@pytest.fixture
def combination_bdf(tmp_path: Path) -> Path:
    path = tmp_path / "combination.bdf"
    path.write_text(COMBINATION_BDF)
    return path


@pytest.fixture
def multi_subcase_bdf(tmp_path: Path) -> Path:
    path = tmp_path / "multi_subcase.bdf"
    path.write_text(MULTI_SUBCASE_BDF)
    return path


def test_describe_loads_and_boundary_conditions_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ms.describe_loads_and_boundary_conditions(str(tmp_path / "does_not_exist.bdf"))


def test_describe_loads_and_boundary_conditions_proper_deck(proper_bdf: Path):
    result = ms.describe_loads_and_boundary_conditions(str(proper_bdf))
    subcase = result["subcases"]["0"]
    assert subcase["boundary_conditions"] == {
        "set_id": 2,
        "constrained_nodes": 2,
        "by_component": {"123456": 2},
        "sample_node_ids": [1, 4],
    }
    assert subcase["loads"]["set_id"] == 3
    assert subcase["loads"]["load_cards"] == 1
    assert subcase["loads"]["by_type"] == {"FORCE": 1}
    assert subcase["loads"]["force_resultant_xyz"] == [1.0, 0.0, 0.0]
    assert subcase["loads"]["force_magnitude_range"] == [1.0, 1.0]


def test_describe_loads_and_boundary_conditions_resolves_combinations(
    combination_bdf: Path,
):
    """SPCADD/LOAD combination cards are filed by pyNastran in their own
    model.spcadds/model.load_combinations dicts, separate from model.spcs/
    model.loads -- this exercises that both get checked and that LOAD's
    per-set scale factors are actually applied."""
    result = ms.describe_loads_and_boundary_conditions(str(combination_bdf))
    subcase = result["subcases"]["0"]

    bc = subcase["boundary_conditions"]
    assert bc["set_id"] == 20
    assert bc["constrained_nodes"] == 2
    assert bc["by_component"] == {"123456": 1, "3": 1}

    loads = subcase["loads"]
    assert loads["set_id"] == 30
    assert loads["load_cards"] == 2
    # FORCE 3 (mag 1, xyz (1,0,0)) scaled 2.0 + FORCE 31 (mag 1, xyz (0,0,1))
    # scaled 1.0, overall LOAD scale 1.0 -> resultant (2, 0, 1).
    assert loads["force_resultant_xyz"] == [2.0, 0.0, 1.0]
    assert loads["force_magnitude_range"] == [1.0, 2.0]


def test_describe_loads_and_boundary_conditions_per_subcase(multi_subcase_bdf: Path):
    result = ms.describe_loads_and_boundary_conditions(str(multi_subcase_bdf))
    subcases = result["subcases"]

    modes = subcases["1"]
    assert modes["label"] == "Modes"
    assert modes["boundary_conditions"]["constrained_nodes"] == 2
    assert modes["loads"] is None  # no LOAD request in this subcase

    gvw = subcases["2"]
    assert gvw["label"] == "GVW"
    assert gvw["boundary_conditions"]["constrained_nodes"] == 2
    assert gvw["loads"]["load_cards"] == 1


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


def test_front_camera_preset_is_the_unrotated_reset_view():
    """"front" applies zero azimuth/elevation on top of pyNastranGUI's own
    camera reset -- i.e. whatever ResetCamera's default view direction is,
    unrotated -- added for the blog's case-study overview (canonical
    top/side/front/iso views), see issue #23."""
    assert ms._CAMERA_PRESETS["front"] == (0.0, 0.0)


def test_planform_camera_preset_matches_nasa_report_angle():
    """"planform" is tuned (by rendering and comparing against NASA's own
    CRM wingbox FEM description figures) to match their report-style
    overview angle: azimuth near 0 keeps the span horizontal in frame (like
    "top"), rather than "iso"'s 45 degrees, which rotates this wingbox's
    span into a tall portrait shape that wastes most of a landscape frame.
    Elevation sits between "front" (0, no depth cues) and "top" (89, reads
    flat/orthographic) to reveal the leading edge and root end-cap."""
    azimuth, elevation = ms._CAMERA_PRESETS["planform"]
    assert azimuth != ms._CAMERA_PRESETS["iso"][0]
    assert 0.0 < elevation < 89.0


def test_render_model_view_hide_groups_without_ses_path(proper_bdf: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="ses_path"):
        ms.render_model_view(str(proper_bdf), str(tmp_path / "out.png"), hide_groups=["Skins"])


def test_render_model_view_auto_camera_requires_op2(proper_bdf: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="camera='auto'"):
        ms.render_model_view(str(proper_bdf), str(tmp_path / "out.png"), camera="auto")


def test_render_model_view_isolate_matching_zero_elements(
    two_property_bdf: Path, tmp_path: Path
):
    """Regression test: a .ses group (or property ID) can legitimately name
    IDs that aren't in model.elements at all -- e.g. mass points. This used
    to either silently render a blank scene, or (once render_stress_contour
    started trimming the OP2 to match the isolated set) crash with an
    opaque pyNastran-internal FatalError reading a degenerate, empty-tables
    OP2. Should raise a clear reason instead."""
    with pytest.raises(ValueError, match="matched 0 elements"):
        ms.render_model_view(
            str(two_property_bdf), str(tmp_path / "out.png"), isolate_property_ids=[999],
        )


def test_render_stress_contour_invalid_result(two_property_bdf: Path, tmp_path: Path):
    # camera="iso" (not "auto") so this fails on result validation before
    # ever trying to actually parse the dummy OP2 as a real result file.
    dummy_op2 = tmp_path / "dummy.OP2"
    dummy_op2.write_bytes(b"")
    with pytest.raises(ValueError, match="result"):
        ms.render_stress_contour(
            str(two_property_bdf), str(dummy_op2), str(tmp_path / "out.png"),
            camera="iso", result="bogus",
        )


def test_render_stress_contour_displacement_with_isolate_not_supported(
    two_property_bdf: Path, tmp_path: Path
):
    """result="displacement" combined with isolate_*/hide_* should raise
    up front rather than silently rendering an empty fringe --
    _write_filtered_op2 (needed to avoid the geometry/results-mismatch hang
    when isolating) drops displacement results entirely, keeping only
    stress tables."""
    dummy_op2 = tmp_path / "dummy.OP2"
    dummy_op2.write_bytes(b"")
    with pytest.raises(ValueError, match="displacement"):
        ms.render_stress_contour(
            str(two_property_bdf), str(dummy_op2), str(tmp_path / "out.png"),
            camera="iso", result="displacement", isolate_property_ids=[1],
        )


@pytest.mark.skipif(
    not Path(DEFAULT_SOLVER_PATH).is_file(),
    reason="MYSTRAN solver not available in this environment.",
)
def test_camera_look_direction_aims_at_governing_element(two_property_bdf: Path):
    """_camera_look_direction_for_governing_element is pure pyNastran/numpy
    (no GUI needed) -- covers the auto-camera math directly rather than only
    indirectly through a full render, since a full render also needs
    PyQt5/vtk (see _RENDER_SKIP_REASON) and is much slower."""
    solver_result = ms.run_solver(str(two_property_bdf))
    assert solver_result["success"], solver_result["errors"]

    peaks = ms.get_max_stress(solver_result["op2_path"])
    governing_eid = peaks["cquad4"]["element_id"]

    result = ms._camera_look_direction_for_governing_element(
        two_property_bdf, Path(solver_result["op2_path"])
    )
    assert result is not None
    focal_point, camera_position, view_up, legend_y, orientation_caption = result
    assert legend_y in (0.56, 0.08)

    # two_property_bdf's nodes span X in [0, 2], Y in [0, 1], Z always 0 --
    # so span=X (largest range), thickness=Z (smallest, degenerate at 0),
    # chord=Y. The elements are flat in the XY plane, so their outward
    # normal is pure +-Z (thickness axis): the new natural-orientation
    # camera should pick thickness as the PRIMARY viewing axis (dominant,
    # sign-matched to the normal) and chord as the secondary tilt, with
    # ZERO contribution from span -- unlike the old fixed 8-octant approach
    # this replaced, which gave span equal weight and is what made a real
    # published render (this model's tip displacement) look almost
    # vertical. See the "Natural-orientation camera philosophy" comment
    # above _natural_axes.
    import numpy as np

    offset = np.array(camera_position) - np.array(focal_point)
    direction = offset / np.linalg.norm(offset)
    assert direction[0] == pytest.approx(0.0, abs=1e-9)  # span (X): no contribution
    assert direction[2] > 0  # thickness (Z): primary, sign-matched to the outward normal
    assert abs(direction[2]) > abs(direction[1])  # thickness dominates chord (Y)
    assert np.linalg.norm(direction) == pytest.approx(1.0, abs=1e-6)

    assert np.linalg.norm(view_up) == pytest.approx(1.0, abs=1e-6)
    assert np.dot(view_up, direction) == pytest.approx(0.0, abs=1e-6)
    assert "span = X" in orientation_caption
    assert "up = Z" in orientation_caption
    assert "root at left" in orientation_caption

    from pyNastran.bdf.bdf import BDF

    model = BDF()
    model.read_bdf(str(two_property_bdf), xref=False)
    assert governing_eid in model.elements


def test_camera_look_direction_fans_out_isolated_group(two_property_bdf: Path):
    """_camera_look_direction_for_isolated_group is pure pyNastran/numpy (no
    solver or GUI needed) -- unlike the governing-element camera, this needs
    no OP2 at all, just the elements being isolated."""
    from pyNastran.bdf.bdf import BDF

    model = BDF()
    model.read_bdf(str(two_property_bdf), xref=False)
    eids = set(model.elements.keys())
    assert eids == {1, 2}

    result = ms._camera_look_direction_for_isolated_group(two_property_bdf, eids)
    assert result is not None
    focal_point, camera_position, view_up, legend_y, orientation_caption = result
    assert legend_y in (0.56, 0.08)
    assert "root at left" in orientation_caption

    # Both CQUAD4s are flat in the XY plane (z=0), so their shared normal is
    # +-Z. A straight-on view (camera offset from focal point only in Z)
    # would perfectly overlap two coplanar elements, which is exactly what
    # this camera is meant to avoid -- it must be tilted, i.e. the camera
    # has to differ from the focal point in X and/or Y too, not just Z.
    import numpy as np

    dx = camera_position[0] - focal_point[0]
    dy = camera_position[1] - focal_point[1]
    assert abs(dx) > 1e-3 or abs(dy) > 1e-3

    view_direction = np.array(camera_position) - np.array(focal_point)
    view_direction /= np.linalg.norm(view_direction)
    assert np.linalg.norm(view_up) == pytest.approx(1.0, abs=1e-6)
    assert np.dot(view_up, view_direction) == pytest.approx(0.0, abs=1e-6)


def test_legend_corner_y_avoids_content_in_top_right():
    """Regression test for a real published clash: a wide fan whose content
    actually reaches into the top-right corner should get bottom-right
    instead, not the default. Pure numpy -- no BDF/GUI needed."""
    import numpy as np

    view_direction = np.array([0.0, 0.0, 1.0])
    up = np.array([0.0, 1.0, 0.0])

    # A cluster of points sitting squarely in the top-right candidate zone
    # (norm_x > 0.66, norm_y > 0.08) alongside a spread-out background, so
    # the top-right corner is genuinely occupied -- bottom-right should win.
    rng = np.random.default_rng(0)
    background = rng.uniform(-1.0, 1.0, size=(200, 3))
    background[:, 2] = 0.0
    top_right_cluster = np.column_stack([
        rng.uniform(0.8, 1.0, size=50),
        rng.uniform(0.5, 1.0, size=50),
        np.zeros(50),
    ])
    points = np.concatenate([background, top_right_cluster], axis=0)

    assert ms._legend_corner_y(view_direction, up, points) == 0.08


def test_legend_corner_y_defaults_to_top_right_when_ambiguous():
    """Symmetric content (nothing preferring one corner over the other)
    should keep the default top-right placement."""
    import numpy as np

    view_direction = np.array([0.0, 0.0, 1.0])
    up = np.array([0.0, 1.0, 0.0])
    rng = np.random.default_rng(0)
    points = rng.uniform(-1.0, 1.0, size=(200, 3))
    points[:, 2] = 0.0

    assert ms._legend_corner_y(view_direction, up, points) == 0.56


def _synthetic_tapered_wing(rng, n=400):
    """A crude but genuinely tapered "wing" point cloud for testing the
    natural-orientation helpers without needing a real BDF: span runs along
    Y (0 to 100), chord and thickness both shrink linearly from root
    (Y near 0, chord/thickness +-10) to tip (Y near 100, chord/thickness
    +-1) -- root has a much bigger chord x thickness cross-section than
    tip, same tapering property a real wing has."""
    import numpy as np

    y = rng.uniform(0.0, 100.0, size=n)
    half_chord = 10.0 * (1.0 - y / 100.0) + 1.0
    half_thickness = 10.0 * (1.0 - y / 100.0) + 1.0
    x = rng.uniform(-1.0, 1.0, size=n) * half_chord
    z = rng.uniform(-1.0, 1.0, size=n) * half_thickness
    return np.column_stack([x, y, z])


def test_natural_axes_identifies_span_chord_thickness():
    """span = largest bounding-box range, thickness = smallest, chord =
    whatever's left -- detected from geometry alone, not a hardcoded axis
    assumption (see "Natural-orientation camera philosophy" above
    _natural_axes)."""
    import numpy as np

    rng = np.random.default_rng(0)
    points = _synthetic_tapered_wing(rng)
    span_axis, chord_axis, thickness_axis = ms._natural_axes(points)
    assert span_axis == 1  # Y: range 0-100, by far the largest
    assert {chord_axis, thickness_axis} == {0, 2}  # X/Z, in either order


def test_root_at_min_span_detects_wider_end():
    """The root (bigger chord x thickness cross-section, per real aircraft
    tapering) sits at Y near 0 in _synthetic_tapered_wing -- the MIN end of
    the span axis."""
    import numpy as np

    rng = np.random.default_rng(1)
    points = _synthetic_tapered_wing(rng)
    span_axis, chord_axis, thickness_axis = ms._natural_axes(points)
    assert ms._root_at_min_span(points, span_axis, chord_axis, thickness_axis) is True


def test_root_at_min_span_false_when_root_is_at_the_max_end():
    """Mirroring the span axis (Y -> 100 - Y) should flip root detection to
    the MAX end -- confirms the heuristic tracks the actual cross-section,
    not just "always pick the min"."""
    import numpy as np

    rng = np.random.default_rng(1)
    points = _synthetic_tapered_wing(rng)
    points = points.copy()
    points[:, 1] = 100.0 - points[:, 1]
    span_axis, chord_axis, thickness_axis = ms._natural_axes(points)
    assert ms._root_at_min_span(points, span_axis, chord_axis, thickness_axis) is False


def test_apply_root_left_roll_flips_up_when_root_would_be_on_the_right():
    """Looking down -Z (view_direction=+Z, i.e. camera looking in the -Z
    direction... here view_from_direction is world->camera, so use +Z) with
    up=+Y makes screen-right = up x view_direction = Y x Z = X. If the root
    (span axis Y's min end, per _synthetic_tapered_wing) sits at +X, it's on
    the right of frame -- the roll must flip up to move it to the left."""
    import numpy as np

    rng = np.random.default_rng(2)
    points = _synthetic_tapered_wing(rng)
    # Shift every root-end (small Y) point to +X, every tip-end point to -X,
    # so root unambiguously projects to screen-right before correction.
    root_mask = points[:, 1] < 10.0
    points = points.copy()
    points[root_mask, 0] += 50.0
    points[~root_mask, 0] -= 50.0

    view_from_direction = np.array([0.0, 0.0, 1.0])
    up = np.array([0.0, 1.0, 0.0])
    right_before = np.cross(up, view_from_direction)
    assert right_before[0] > 0  # sanity check: +X is indeed screen-right here

    corrected_up = ms._apply_root_left_roll(
        view_from_direction, up, points, span_axis=1, root_at_min=True
    )
    assert np.allclose(corrected_up, -up)

    right_after = np.cross(corrected_up, view_from_direction)
    root_centroid = points[root_mask].mean(axis=0)
    tip_centroid = points[~root_mask].mean(axis=0)
    assert np.dot(root_centroid - tip_centroid, right_after) < 0  # now on the left


def test_apply_root_left_roll_leaves_up_unchanged_when_already_left():
    """The mirror image of the flip test: root already on the left should
    round-trip through unchanged."""
    import numpy as np

    rng = np.random.default_rng(2)
    points = _synthetic_tapered_wing(rng)
    root_mask = points[:, 1] < 10.0
    points = points.copy()
    points[root_mask, 0] -= 50.0
    points[~root_mask, 0] += 50.0

    view_from_direction = np.array([0.0, 0.0, 1.0])
    up = np.array([0.0, 1.0, 0.0])

    corrected_up = ms._apply_root_left_roll(
        view_from_direction, up, points, span_axis=1, root_at_min=True
    )
    assert np.allclose(corrected_up, up)


def test_axis_role_caption_includes_root_claim_only_when_asked():
    caption = ms._axis_role_caption(1, 0, 2)
    assert caption == "Axes: span = Y  |  chord = X  |  up = Z"

    caption_with_root = ms._axis_role_caption(1, 0, 2, note_root_left=True)
    assert caption_with_root == "Axes: span = Y  |  chord = X  |  up = Z  (root at left)"


def test_camera_look_direction_isolated_group_no_plate_elements(
    two_property_bdf: Path,
) -> None:
    result = ms._camera_look_direction_for_isolated_group(two_property_bdf, set())
    assert result is None


@pytest.mark.skipif(
    not Path(DEFAULT_SOLVER_PATH).is_file(),
    reason="MYSTRAN solver not available in this environment.",
)
def test_write_filtered_op2_keeps_only_requested_element(
    two_property_bdf: Path, tmp_path: Path
):
    """_write_filtered_op2 is what fixes the real hang from issue #9:
    pairing a filtered-down BDF with the ORIGINAL full-model OP2. Covers the
    trim+round-trip directly rather than only indirectly through a full
    render (see test_render_stress_contour_isolate_end_to_end below for
    that)."""
    solver_result = ms.run_solver(str(two_property_bdf))
    assert solver_result["success"], solver_result["errors"]

    out_op2 = tmp_path / "filtered.OP2"
    ms._write_filtered_op2(Path(solver_result["op2_path"]), {1}, out_op2)

    from pyNastran.op2.op2 import OP2

    trimmed = OP2(debug=False)
    trimmed.read_op2(str(out_op2), build_dataframe=False)

    # Check the unique element IDs actually present, not the .nelements
    # attribute itself -- it's an internal bookkeeping value that doesn't
    # necessarily round-trip through the OP2 writer/reader consistently,
    # confirmed by inspecting it directly during development. The real
    # correctness signal is what elements/data are actually in the array,
    # which is exactly what pyNastranGUI reads to build the fringe.
    cquad4 = trimmed.op2_results.stress.cquad4_stress[1]
    assert set(cquad4.element_node[:, 0].tolist()) == {1}

    # Other result categories are dropped entirely -- see _write_filtered_op2's
    # docstring for why (their own node/element counts would mismatch the
    # isolated subset just as badly as the untrimmed stress array did).
    assert not trimmed.displacements


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


@pytest.mark.skipif(
    not (Path(DEFAULT_SOLVER_PATH).is_file() and _rendering_deps_available()),
    reason=_RENDER_SKIP_REASON,
)
def test_render_stress_contour_isolate_end_to_end(two_property_bdf: Path, tmp_path: Path):
    """Regression test for issue #9's real hang: pairing an isolated
    (filtered-down) BDF with the untrimmed full-model OP2. _write_filtered_op2
    fixes it by trimming the OP2 to match; this exercises that fix through
    the actual public tool rather than just the trim function directly."""
    solver_result = ms.run_solver(str(two_property_bdf))
    assert solver_result["success"], solver_result["errors"]

    output_png = tmp_path / "isolated_contour.png"
    result = ms.render_stress_contour(
        str(two_property_bdf), solver_result["op2_path"], str(output_png),
        isolate_property_ids=[1], timeout=60,
    )
    assert result["success"], result.get("errors")
    assert result["hidden_element_count"] == 1
    assert result["fringe_set"] is True
    assert output_png.is_file()
    assert output_png.stat().st_size > 0


@pytest.mark.skipif(
    not (Path(DEFAULT_SOLVER_PATH).is_file() and _rendering_deps_available()),
    reason=_RENDER_SKIP_REASON,
)
def test_render_stress_contour_displacement_end_to_end(
    two_property_bdf: Path, tmp_path: Path
):
    """result="displacement" colors by nodal displacement magnitude
    (Displacement T_XYZ) instead of von Mises -- exercises _FRINGE_RESULT_
    MATCH's "displacementt" search actually finding and applying that case
    (and not the rotational "Displacement R_XYZ" one) via the real tool."""
    solver_result = ms.run_solver(str(two_property_bdf))
    assert solver_result["success"], solver_result["errors"]

    output_png = tmp_path / "displacement.png"
    result = ms.render_stress_contour(
        str(two_property_bdf), solver_result["op2_path"], str(output_png),
        camera="iso", result="displacement",
    )
    assert result["success"], result.get("errors")
    assert result["fringe_set"] is True
    assert output_png.is_file()
    assert output_png.stat().st_size > 0


@pytest.mark.skipif(
    not (Path(DEFAULT_SOLVER_PATH).is_file() and _rendering_deps_available()),
    reason=_RENDER_SKIP_REASON,
)
def test_render_stress_contour_axial_end_to_end(cbar_bdf: Path, tmp_path: Path):
    """result="axial" colors CBARs by their real per-element axial-stress
    result ("Stress XX", RealBarStressArray's own column) -- unlike
    result="von_mises", which CBARs have no genuine value for at all (see
    get_max_stress's "max_stress" vs "von_mises" distinction). Exercises
    _build_postscript's "__bar_axial__" branch (method-label lookup, not a
    resname substring search) via the real tool, and confirms it also works
    combined with isolate_property_ids -- axial is a stress-table result,
    kept by _write_filtered_op2's trim, unlike displacement."""
    solver_result = ms.run_solver(str(cbar_bdf))
    assert solver_result["success"], solver_result["errors"]

    output_png = tmp_path / "axial.png"
    result = ms.render_stress_contour(
        str(cbar_bdf), solver_result["op2_path"], str(output_png),
        camera="iso", result="axial", isolate_property_ids=[1], timeout=60,
    )
    assert result["success"], result.get("errors")
    assert result["fringe_set"] is True
    assert output_png.is_file()
    assert output_png.stat().st_size > 0


@pytest.mark.skipif(
    not (Path(DEFAULT_SOLVER_PATH).is_file() and _rendering_deps_available()),
    reason=_RENDER_SKIP_REASON,
)
def test_render_stress_contour_axial_no_bar_stress(two_property_bdf: Path, tmp_path: Path):
    """A plate-only model (no CBAR at all) has no "Stress XX" case to find --
    result="axial" should render with pyNastranGUI's default coloring left
    in place (fringe_set=False), the same graceful fallback von_mises gets
    against a bar-only model, rather than raising."""
    solver_result = ms.run_solver(str(two_property_bdf))
    assert solver_result["success"], solver_result["errors"]

    output_png = tmp_path / "axial_no_bars.png"
    result = ms.render_stress_contour(
        str(two_property_bdf), solver_result["op2_path"], str(output_png),
        camera="iso", result="axial",
    )
    assert result["success"], result.get("errors")
    assert result["fringe_set"] is False
    assert output_png.is_file()
    assert output_png.stat().st_size > 0
