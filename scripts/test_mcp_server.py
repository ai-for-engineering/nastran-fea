"""
Smoke tests for scripts/mcp_server.py.

load_model and patch_case_control are tested fully against small synthetic
BDFs generated here -- no external data needed.

get_max_stress is exercised end-to-end against a real MYSTRAN-produced OP2:
the test builds a tiny one-element model, patches/solves it with the actual
MYSTRAN binary, and checks the parsed max-stress result -- but only if
solver/ (gitignored, ~24MB, not present in every checkout/worktree -- see
CLAUDE.md) is actually available. Otherwise that one test is skipped with an
explicit reason rather than faked.

run_solver is not separately tested here beyond what get_max_stress's
end-to-end test already exercises: it's a thin, already-tested wrapper
(scripts/run_solver.py has its own docstring/commit-message history of
manual verification against real MYSTRAN behavior) and duplicating that
would just be re-testing run_solver.py, not this module.
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
    """Solve the tiny synthetic model for real and check the max-stress
    parsing logic (including the double-fiber-entry-per-element handling)
    against genuine MYSTRAN OP2 output."""
    solver_result = ms.run_solver(str(proper_bdf))
    assert solver_result["success"], solver_result["errors"]

    stress_result = ms.get_max_stress(solver_result["op2_path"])
    assert stress_result["element_id"] == 1
    assert stress_result["subcase"] == 1
    assert stress_result["von_mises"] > 0
