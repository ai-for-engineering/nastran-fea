"""
MCP server exposing the wingbox pipeline (load -> patch case control -> solve
-> read results) as tool calls, so an MCP client (e.g. Claude) can drive the
workflow conversationally instead of by running scripts by hand.

Tools:
    load_model(bdf_path)
        Parse a BDF with pyNastran and return summary counts, so a caller can
        sanity-check a deck before doing anything else. A deck that fails to
        parse (e.g. an OptiStruct-authored deck missing SOL/CEND -- see
        README's NASA CRM case-study section) is reported as a structured
        failure (success=False) rather than raising, so the caller can
        recover by calling patch_case_control and retrying.

    patch_case_control(bdf_path, output_path)
        Detect whether a deck is missing proper Nastran case control
        (OptiStruct-style: no SOL/CEND) and, if so, rebuild it using the
        recipe documented in README's NASA CRM case-study section, preserving
        any SPC/LOAD/DISPLACEMENT/STRESS/ECHO requests already present in the
        deck's original header. If the deck already has SOL/CEND, this is a
        safe no-op copy.

    run_solver(bdf_path, solver_exe_path=None, timeout=None)
        Thin wrapper around scripts/run_solver.py's run_solver() -- see that
        module's docstring for why MYSTRAN's own exit code can't be trusted.

    get_max_stress(op2_path)
        Parse an OP2 with pyNastran and return the max von Mises CQUAD4
        stress across all subcases (value, element ID, subcase), correctly
        handling the fact that each element appears twice in the stress
        array (once per shell fiber location) -- see CLAUDE.md's Gotchas.

Run directly for stdio transport (the default an MCP client like Claude
Desktop/Code expects):

    ./venv/Scripts/python.exe scripts/mcp_server.py
"""
from __future__ import annotations

import dataclasses
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

# scripts/ is a flat directory, not a package -- make sibling imports work
# regardless of how this file is invoked (direct script, `mcp run`, an MCP
# client launching it with an absolute path, etc).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_solver import (  # noqa: E402
    DEFAULT_SOLVER_PATH,
    DEFAULT_TIMEOUT_S,
    run_solver as _run_solver,
)

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("nastran-fea-wingbox")


# ---------------------------------------------------------------------------
# load_model
# ---------------------------------------------------------------------------

def _parse_bdf(bdf_path: Path):
    """Parse bdf_path with pyNastran, capturing warning/error-level log
    messages instead of letting them print to stdout. Returns
    (bdf_or_none, messages, error_or_none)."""
    from cpylog import SimpleLogger
    from pyNastran.bdf.bdf import BDF

    messages: list[str] = []

    def _log_func(typ: str, _fname: str, _lineno: int, msg: str) -> None:
        messages.append(f"{typ}: {msg}")

    logger = SimpleLogger(level="warning", log_func=_log_func)
    bdf = BDF(log=logger)
    try:
        bdf.read_bdf(str(bdf_path), xref=True)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        return None, messages, f"{type(exc).__name__}: {exc}"
    return bdf, messages, None


@mcp.tool()
def load_model(bdf_path: str) -> dict[str, Any]:
    """Validate a BDF file exists/is readable and parse it with pyNastran.

    Returns basic summary info (counts of nodes/elements/properties/
    materials, per-card-type counts, any parse warnings) so a caller can
    sanity-check a deck before proceeding to patch_case_control/run_solver.

    A deck that pyNastran cannot parse at all (most commonly an
    OptiStruct-authored deck with no SOL/CEND) is reported as
    {"success": False, "error": ...} rather than raising, since that's an
    expected, recoverable step in the workflow (call patch_case_control next).
    Missing/unreadable files still raise, since those are infrastructure
    problems the caller must fix before retrying anything.
    """
    path = Path(bdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"BDF file not found: {path}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"BDF file is not readable: {path}")

    bdf, messages, error = _parse_bdf(path)
    if error is not None:
        return {
            "success": False,
            "bdf_path": str(path),
            "error": error,
            "warnings": messages,
        }

    return {
        "success": True,
        "bdf_path": str(path),
        "counts": {
            "nodes": len(bdf.nodes),
            "elements": len(bdf.elements),
            "properties": len(bdf.properties),
            "materials": len(bdf.materials),
        },
        "card_count": dict(bdf.card_count),
        "warnings": messages,
    }


# ---------------------------------------------------------------------------
# patch_case_control
# ---------------------------------------------------------------------------

_BEGIN_BULK_RE = re.compile(r"^\s*BEGIN\s+BULK\b", re.IGNORECASE)
_CEND_RE = re.compile(r"^\s*CEND\s*$", re.IGNORECASE)
_SOL_RE = re.compile(r"^\s*SOL\s+\d+", re.IGNORECASE)
# Case-control request lines worth preserving verbatim from an OptiStruct
# header (e.g. "SPC = 2", "LOAD = 3") when rebuilding case control. This is
# intentionally not hardcoded to the NASA CRM wingbox's specific set IDs --
# see README's case-study section for that deck's exact original values.
_REQUEST_RE = re.compile(
    r"^\s*(SPC|MPC|LOAD|DISPLACEMENT|STRESS|SPCFORCES|ECHO)\s*=\s*(\S.*)$",
    re.IGNORECASE,
)
_DEFAULT_REQUESTS = {"ECHO": "NONE", "DISPLACEMENT": "ALL", "STRESS": "ALL"}


def _split_header_bulk(lines: list[str]) -> tuple[list[str], list[str], bool]:
    """Split file lines into (header_lines, bulk_lines, begin_bulk_found).
    bulk_lines includes the BEGIN BULK line itself when found."""
    for i, line in enumerate(lines):
        if _BEGIN_BULK_RE.match(line):
            return lines[:i], lines[i:], True
    return [], lines, False


def _has_proper_case_control(header_lines: list[str]) -> bool:
    has_sol = any(_SOL_RE.match(line) for line in header_lines)
    has_cend = any(_CEND_RE.match(line) for line in header_lines)
    return has_sol and has_cend


def _rebuild_case_control(header_lines: list[str]) -> list[str]:
    """Build a MYSTRAN-compatible SOL 101/CEND case control block, carrying
    forward any SPC/LOAD/DISPLACEMENT/STRESS/ECHO requests already present in
    an OptiStruct-style header (falling back to sensible defaults for
    ECHO/DISPLACEMENT/STRESS if the header didn't specify them)."""
    requests: dict[str, str] = {}
    for line in header_lines:
        match = _REQUEST_RE.match(line)
        if match:
            key = match.group(1).upper()
            requests[key] = match.group(2).strip()

    for key, default in _DEFAULT_REQUESTS.items():
        requests.setdefault(key, default)

    out = ["SOL 101", "CEND"]
    # Deterministic, README-matching order: ECHO, SPC, LOAD, then the rest.
    ordered_keys = ["ECHO", "SPC", "LOAD", "MPC", "DISPLACEMENT", "STRESS", "SPCFORCES"]
    for key in ordered_keys:
        if key in requests:
            out.append(f"{key} = {requests.pop(key)}")
    for key, value in requests.items():
        out.append(f"{key} = {value}")
    return out


@mcp.tool()
def patch_case_control(bdf_path: str, output_path: str) -> dict[str, Any]:
    """Rebuild case control for an OptiStruct-authored deck (no SOL/CEND) so
    MYSTRAN can run it, writing the result to output_path.

    If bdf_path already has proper SOL/CEND case control, this is a safe
    no-op copy (not an error) -- the deck is written to output_path
    unchanged so callers can always feed patch_case_control's output
    straight into run_solver.
    """
    in_path = Path(bdf_path)
    out_path = Path(output_path)
    if not in_path.is_file():
        raise FileNotFoundError(f"BDF file not found: {in_path}")
    if not os.access(in_path, os.R_OK):
        raise PermissionError(f"BDF file is not readable: {in_path}")

    text = in_path.read_text(errors="replace")
    lines = text.splitlines()
    header_lines, bulk_lines, begin_bulk_found = _split_header_bulk(lines)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if begin_bulk_found and _has_proper_case_control(header_lines):
        shutil.copyfile(in_path, out_path)
        return {
            "patched": False,
            "output_path": str(out_path),
            "reason": "deck already has SOL/CEND case control; copied unchanged",
        }

    new_header = _rebuild_case_control(header_lines)
    if not begin_bulk_found:
        new_header.append("BEGIN BULK")
    new_text = "\n".join(new_header) + "\n" + "\n".join(bulk_lines) + "\n"
    out_path.write_text(new_text)

    return {
        "patched": True,
        "output_path": str(out_path),
        "reason": (
            "no BEGIN BULK found; treated whole file as bulk data and prepended case control"
            if not begin_bulk_found
            else "missing SOL/CEND (OptiStruct-style); case control rebuilt"
        ),
        "case_control": new_header,
    }


# ---------------------------------------------------------------------------
# run_solver
# ---------------------------------------------------------------------------

@mcp.tool()
def run_solver(
    bdf_path: str,
    solver_exe_path: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run the MYSTRAN solver on bdf_path and report whether it actually
    succeeded (per scripts/run_solver.py's F06-based check -- MYSTRAN's exit
    code alone is not trustworthy).

    solver_exe_path/timeout default to scripts/run_solver.py's own defaults
    (solver/mystran-*.exe relative to the repo, 600s) when omitted.

    Raises for infrastructure problems (missing BDF, missing solver binary,
    solve exceeding timeout) -- a solve that runs but fails on its own terms
    is returned as a structured result (success=False, errors=[...]) instead.
    """
    kwargs: dict[str, Any] = {}
    if solver_exe_path is not None:
        kwargs["solver_exe_path"] = solver_exe_path
    if timeout is not None:
        kwargs["timeout"] = timeout

    result = _run_solver(bdf_path, **kwargs)
    result_dict = dataclasses.asdict(result)
    for key in ("dat_path", "f06_path", "op2_path"):
        result_dict[key] = str(result_dict[key])
    return result_dict


# ---------------------------------------------------------------------------
# get_max_stress
# ---------------------------------------------------------------------------

@mcp.tool()
def get_max_stress(op2_path: str) -> dict[str, Any]:
    """Parse an OP2 and return the maximum von Mises CQUAD4 stress across all
    subcases: {"von_mises": ..., "element_id": ..., "subcase": ...}.

    Per pyNastran's API (see CLAUDE.md Gotchas): reads
    op2.op2_results.stress.cquad4_stress[subcase], where von_mises is column
    index 7 and each element appears twice (element_node[:, 0] repeats one
    entry per shell fiber location). Taking a single max over the whole
    (times x entries) array and mapping the winning entry back to its
    element ID via element_node handles that double-entry naturally -- no
    separate dedup step is needed since we want the worst fiber anyway.
    """
    import numpy as np
    from pyNastran.op2.op2 import OP2

    path = Path(op2_path)
    if not path.is_file():
        raise FileNotFoundError(f"OP2 file not found: {path}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"OP2 file is not readable: {path}")

    op2 = OP2(debug=False)
    op2.read_op2(str(path))
    stress = op2.op2_results.stress.cquad4_stress

    if not stress:
        raise ValueError(f"No CQUAD4 stress results found in {path}")

    best: dict[str, Any] | None = None
    for subcase, arr in stress.items():
        von_mises = arr.data[:, :, 7]  # (ntimes, nentries)
        elem_ids = arr.element_node[:, 0]
        itime, ientry = np.unravel_index(np.argmax(von_mises), von_mises.shape)
        value = float(von_mises[itime, ientry])
        if best is None or value > best["von_mises"]:
            best = {
                "von_mises": value,
                "element_id": int(elem_ids[ientry]),
                "subcase": int(subcase) if isinstance(subcase, (int, np.integer)) else subcase,
            }

    assert best is not None
    return best


if __name__ == "__main__":
    mcp.run()
