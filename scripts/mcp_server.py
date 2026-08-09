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
        Parse an OP2 with pyNastran and return the peak stress per element
        type present (plate elements report von_mises, bar elements report
        max_stress -- these are different physical quantities, deliberately
        not blended into one number) across all subcases.

    describe_loads_and_boundary_conditions(bdf_path)
        Parse a BDF's SPC/SPC1 (following SPCADD) and FORCE/MOMENT/...
        (following LOAD combinations) and summarize what's actually
        constraining and loading the model, per subcase -- constrained node/
        DOF counts and a resultant force/moment vector, so a caller can
        sanity-check a model's BCs and loads before trusting a stress result.

    render_model_view(bdf_path, output_png, ...)
    render_stress_contour(bdf_path, op2_path, output_png, ...)
        Render a screenshot of the model (plain geometry, or colored by von
        Mises stress) via a scripted pyNastranGUI session. Both support
        hiding elements by named group (parsed from a Patran/HyperMesh .ses
        session file, when the case study has one -- see ses_groups.py) or
        by raw PSHELL/PBAR property ID. See GitHub issues #8/#9 for how this
        works and its real limitations (needs an active desktop session,
        not display-less headless).

Run directly for stdio transport (the default an MCP client like Claude
Desktop/Code expects):

    ./venv/Scripts/python.exe scripts/mcp_server.py
"""
from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
from ses_groups import parse_ses_groups  # noqa: E402

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

def _element_ids_for(arr: Any) -> Any:
    """Bar-type results carry one row per element (`.element`); plate-type
    results carry two rows per element -- one per shell fiber location --
    indexed via `.element_node[:, 0]`."""
    if hasattr(arr, "element_node"):
        return arr.element_node[:, 0]
    return arr.element



# CBAR/CBEAM-style stress columns -> (component, end). "axial" has no fixed
# end (constant along a bar with no distributed axial load, and pyNastran
# only ever reports it once per element); the s1-s4 columns are bending
# stress at that end's four cross-section stress-recovery points; smax/smin
# are the combined (axial + bending) extreme at that end -- see the
# CBAR-specific caveat in _peak_for_result's docstring for why "max_stress"
# alone doesn't say which of these actually governed.
_BAR_COLUMN_INFO: dict[str, tuple[str, str | None]] = {
    "axial": ("axial", None),
    "s1a": ("bending", "A"), "s2a": ("bending", "A"),
    "s3a": ("bending", "A"), "s4a": ("bending", "A"),
    "smaxa": ("combined (axial + bending)", "A"),
    "smina": ("combined (axial + bending)", "A"),
    "s1b": ("bending", "B"), "s2b": ("bending", "B"),
    "s3b": ("bending", "B"), "s4b": ("bending", "B"),
    "smaxb": ("combined (axial + bending)", "B"),
    "sminb": ("combined (axial + bending)", "B"),
}


def _peak_for_result(arr: Any) -> dict[str, Any]:
    """Return the governing {"<quantity>": value, "element_id": ..., "subcase"
    omitted here} for a single subcase's stress array.

    Plate-type results (CQUAD4, CTRIA3, ...) report a `von_mises` column --
    use it directly, taking a single max over the whole (times x entries)
    array; each element's two fiber-location rows are handled for free since
    we want the worst fiber anyway.

    Bar-type results (CBAR, ...) report direct stresses (axial + bending,
    e.g. s1a/s2a/s3a/s4a/axial/smaxa/smina/s1b/.../smaxb/sminb) rather than a
    von Mises equivalent -- NOT the same physical quantity as plate von
    Mises, so it's reported as "max_stress" instead of "von_mises" to avoid
    implying a false equivalence. Margin-of-safety columns (header starting
    with "MS", e.g. MS_tension/MS_compression) are excluded: pyNastran fills
    them with large sentinel values (~1e10) when a margin isn't computed,
    which would otherwise dominate a naive max/abs over all columns.

    "max_stress" alone doesn't say whether the governing value is axial,
    bending, or the combined extreme, nor which end of the bar (A/B) it's
    at -- so for the bar branch, the specific column that produced the max
    (not just its value) is looked up in _BAR_COLUMN_INFO and reported as
    "component" (and "end", when that column is tied to one) alongside
    "max_stress". A column not in that table (e.g. an element type this
    hasn't been taught about yet) reports its raw header name as
    "component" and no "end", rather than raising.
    """
    import numpy as np

    headers = arr.get_headers()
    elem_ids = _element_ids_for(arr)

    if "von_mises" in headers:
        quantity = "von_mises"
        values = arr.data[:, :, headers.index("von_mises")]
        itime, ientry = np.unravel_index(np.argmax(values), values.shape)
        return {
            quantity: float(values[itime, ientry]),
            "element_id": int(elem_ids[ientry]),
        }

    quantity = "max_stress"
    stress_cols = [i for i, h in enumerate(headers) if not h.startswith("MS")]
    abs_vals = np.abs(arr.data[:, :, stress_cols])
    values = np.max(abs_vals, axis=2)
    itime, ientry = np.unravel_index(np.argmax(values), values.shape)
    col_within = int(np.argmax(abs_vals[itime, ientry, :]))
    governing_header = headers[stress_cols[col_within]]
    component, end = _BAR_COLUMN_INFO.get(governing_header, (governing_header, None))

    result = {
        quantity: float(values[itime, ientry]),
        "element_id": int(elem_ids[ientry]),
        "component": component,
    }
    if end is not None:
        result["end"] = end
    return result


@mcp.tool()
def get_max_stress(op2_path: str) -> dict[str, Any]:
    """Parse an OP2 and return the peak stress for each element type present,
    across all subcases:

        {"cquad4": {"von_mises": ..., "element_id": ..., "subcase": ...},
         "cbar": {"max_stress": ..., "element_id": ..., "subcase": ...,
                  "component": "axial" | "bending" | "combined (axial + bending)",
                  "end": "A" | "B"},  # "end" omitted for "axial" (no fixed end)
         ...}

    Element types not present in the OP2 are omitted. Plate-type elements
    (CQUAD4, CTRIA3, ...) report "von_mises"; bar-type elements (CBAR, ...)
    report "max_stress" (peak direct axial+bending stress magnitude) plus
    "component"/"end" identifying which specific column governed -- these
    are deliberately NOT combined into one blended "the max" number since
    they're different physical quantities, and silently comparing them could
    hide whichever one actually governs. See _peak_for_result's docstring for
    the per-type column handling (including the CBAR margin-of-safety
    sentinel-value gotcha).

    Iterates over whatever `*_stress` result containers are actually present
    on `op2.op2_results.stress` rather than hardcoding to specific element
    types, so e.g. a model with CROD or CBEAM results is handled too as long
    as it exposes a `get_headers()`/`.data` array in the same shape.
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
    stress_results = op2.op2_results.stress

    peaks: dict[str, Any] = {}
    for attr_name in dir(stress_results):
        if not attr_name.endswith("_stress") or attr_name.startswith("_"):
            continue
        subcases = getattr(stress_results, attr_name)
        if not subcases:
            continue
        element_type = attr_name[: -len("_stress")]

        best: dict[str, Any] | None = None
        for subcase, arr in subcases.items():
            peak = _peak_for_result(arr)
            peak["subcase"] = int(subcase) if isinstance(subcase, (int, np.integer)) else subcase
            quantity_key = "von_mises" if "von_mises" in peak else "max_stress"
            if best is None or peak[quantity_key] > best[quantity_key]:
                best = peak
        if best is not None:
            peaks[element_type] = best

    if not peaks:
        raise ValueError(f"No stress results found in {path}")

    return peaks


# ---------------------------------------------------------------------------
# describe_loads_and_boundary_conditions
# ---------------------------------------------------------------------------

def _spc_entries(card: Any) -> list[tuple[int, str]]:
    """(node_id, component_string) pairs for one SPC/SPC1 card. SPC stores
    one component string per node (parallel lists, e.g. two constraints on
    one line); SPC1 stores a single component string shared by every node
    listed on the card."""
    if card.type == "SPC1":
        return [(node, str(card.components)) for node in card.nodes]
    return [(node, str(comp)) for node, comp in zip(card.nodes, card.components)]


def _resolve_constraint_set(
    model: Any, set_id: int, seen: set[int] | None = None
) -> list[tuple[int, str]]:
    """Flatten an SPC set id to its (node_id, component) pairs, following
    SPCADD combinations (which reference other set ids, not nodes directly)
    recursively. `seen` guards a malformed deck with a circular SPCADD.

    pyNastran files SPCADD cards in their own `model.spcadds` dict, separate
    from `model.spcs` (SPC/SPC1) -- both are keyed by set id, so both need
    checking here."""
    if seen is None:
        seen = set()
    if set_id in seen:
        return []
    seen.add(set_id)

    entries: list[tuple[int, str]] = []
    for card in model.spcs.get(set_id, []):
        entries.extend(_spc_entries(card))
    for card in model.spcadds.get(set_id, []):
        for sub_id in card.sets:
            entries.extend(_resolve_constraint_set(model, sub_id, seen))
    return entries


def _summarize_constraints(entries: list[tuple[int, str]]) -> dict[str, Any]:
    from collections import Counter

    by_component = Counter(comp for _node, comp in entries)
    node_ids = sorted({node for node, _comp in entries})
    return {
        "constrained_nodes": len(node_ids),
        "by_component": dict(sorted(by_component.items())),
        "sample_node_ids": node_ids[:10],
    }


def _resolve_load_set(
    model: Any, set_id: int, scale: float = 1.0, seen: set[int] | None = None
) -> list[tuple[float, Any]]:
    """Flatten a LOAD set id to its (effective_scale, card) pairs, following
    LOAD combination cards (an overall scale times a per-referenced-set
    scale factor) recursively -- mirrors _resolve_constraint_set's SPCADD
    handling on the load side. `seen` guards a circular LOAD reference.

    pyNastran files LOAD combination cards in their own
    `model.load_combinations` dict, separate from `model.loads`
    (FORCE/MOMENT/...) -- both are keyed by set id, so both need checking."""
    if seen is None:
        seen = set()
    if set_id in seen:
        return []
    seen.add(set_id)

    out: list[tuple[float, Any]] = []
    for card in model.loads.get(set_id, []):
        out.append((scale, card))
    for card in model.load_combinations.get(set_id, []):
        for sub_scale, sub_id in zip(card.scale_factors, card.load_ids):
            out.extend(
                _resolve_load_set(model, sub_id, scale * card.scale * sub_scale, seen)
            )
    return out


_FORCE_TYPES = {"FORCE", "FORCE1", "FORCE2"}
_MOMENT_TYPES = {"MOMENT", "MOMENT1", "MOMENT2"}


def _summarize_loads(resolved: list[tuple[float, Any]]) -> dict[str, Any]:
    """Summarize resolved (scale, card) pairs: counts by card type, plus a
    resultant force/moment vector and magnitude range for FORCE*/MOMENT*
    cards (global-frame xyz*mag, scaled). Other load types actually seen in
    the wild (PLOAD* pressure loads, GRAV) need element geometry or a mass
    distribution this tool doesn't load, so they're counted in "by_type" but
    deliberately left out of the resultant rather than silently guessed at.
    """
    import numpy as np
    from collections import Counter

    by_type = Counter(card.type for _scale, card in resolved)
    force_vecs: list[Any] = []
    moment_vecs: list[Any] = []
    non_global_cid = False
    unresolved_types: set[str] = set()

    for scale, card in resolved:
        if card.type in _FORCE_TYPES or card.type in _MOMENT_TYPES:
            if getattr(card, "cid", 0) not in (0, None):
                non_global_cid = True
            vec = scale * card.mag * np.asarray(card.xyz, dtype=float)
            (force_vecs if card.type in _FORCE_TYPES else moment_vecs).append(vec)
        else:
            unresolved_types.add(card.type)

    result: dict[str, Any] = {
        "load_cards": len(resolved),
        "by_type": dict(sorted(by_type.items())),
    }
    if force_vecs:
        arr = np.array(force_vecs)
        result["force_resultant_xyz"] = arr.sum(axis=0).tolist()
        result["force_magnitude_range"] = [
            float(np.linalg.norm(arr, axis=1).min()),
            float(np.linalg.norm(arr, axis=1).max()),
        ]
    if moment_vecs:
        result["moment_resultant_xyz"] = np.array(moment_vecs).sum(axis=0).tolist()

    notes: list[str] = []
    if unresolved_types:
        result["unresolved_types"] = sorted(unresolved_types)
        notes.append(
            "types in unresolved_types are counted but not vector-summed into "
            "the resultant -- they need element geometry (PLOAD*) or a mass "
            "distribution (GRAV) this tool doesn't load"
        )
    if non_global_cid:
        notes.append(
            "some FORCE/MOMENT cards use a non-global coordinate system "
            "(cid != 0); the resultant sums their raw xyz vectors without "
            "transforming to global, so treat it as approximate"
        )
    if notes:
        result["notes"] = notes
    return result


@mcp.tool()
def describe_loads_and_boundary_conditions(bdf_path: str) -> dict[str, Any]:
    """Parse bdf_path and explain, in engineering terms, what's actually
    constraining and loading the model -- the thing a stress engineer wants
    to know before trusting any downstream stress result.

    Reads SPC/SPC1 (following SPCADD combinations) for boundary conditions
    and FORCE/MOMENT/FORCE1/FORCE2/MOMENT1/MOMENT2 (following LOAD
    combinations) for loads, grouped by subcase using whatever SPC/LOAD case
    control requests are present. Returns:

        {"subcases": {"<subcase_id>": {
            "label": ... | None,
            "boundary_conditions": {"set_id": 2, "constrained_nodes": 196,
                                     "by_component": {"3": 56, "123": 140},
                                     "sample_node_ids": [...]} | None,
            "loads": {"set_id": 3, "load_cards": 12238,
                      "by_type": {"FORCE": 12238},
                      "force_resultant_xyz": [0.0, 0.0, 249777.6],
                      "force_magnitude_range": [20.41, 20.41]} | None,
        }, ...}}

    A subcase missing an SPC or LOAD request entirely (e.g. a modes-only
    subcase) reports None for that half rather than omitting the key, so a
    caller can tell "not requested" apart from "requested but empty".

    MPC is deliberately not handled yet -- not exercised by the NASA CRM
    wingbox validation case (see README); would need the same
    resolve-then-summarize treatment as SPC if a case study needs it.
    """
    from pyNastran.bdf.bdf import BDF

    path = Path(bdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"BDF file not found: {path}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"BDF file is not readable: {path}")

    model = BDF(debug=False)
    model.read_bdf(str(path), xref=False)

    subcases: dict[str, Any] = {}
    for subcase_id, subcase in model.subcases.items():
        params = subcase.params
        label = None
        for key in ("LABEL", "SUBTITLE"):
            if key in params:
                label = params[key][0]
                break

        bc_summary = None
        if "SPC" in params:
            spc_set_id = params["SPC"][0]
            entries = _resolve_constraint_set(model, spc_set_id)
            bc_summary = {"set_id": spc_set_id, **_summarize_constraints(entries)}

        load_summary = None
        if "LOAD" in params:
            load_set_id = params["LOAD"][0]
            resolved = _resolve_load_set(model, load_set_id)
            load_summary = {"set_id": load_set_id, **_summarize_loads(resolved)}

        subcases[str(subcase_id)] = {
            "label": label,
            "boundary_conditions": bc_summary,
            "loads": load_summary,
        }

    return {"bdf_path": str(path), "subcases": subcases}


# ---------------------------------------------------------------------------
# render_model_view / render_stress_contour
# ---------------------------------------------------------------------------

# Deliberately minimal (named presets, not full 6-DOF control) per issue #9's
# scope. (azimuth, elevation) in degrees, applied after a camera reset.
_CAMERA_PRESETS = {
    "iso": (45.0, 20.0),
    "top": (0.0, 89.0),
    "side": (90.0, 0.0),
    "front": (0.0, 0.0),
}

_DEFAULT_RENDER_TIMEOUT_S = 300

# render_stress_contour's `result` -> the case/whitespace/underscore-
# insensitive substring _build_postscript's fringe search looks for in
# self.result_cases (see pyNastranGUI's own case names, confirmed by
# inspecting self.result_cases during development: 'vonMises',
# 'Displacement T_XYZ', 'Displacement R_XYZ'). "displacementt" (not just
# "displacement") deliberately excludes the rotational displacement case --
# both names contain "displacement", only the translational one has a "T"
# right after it once spaces/underscores are stripped.
_FRINGE_RESULT_MATCH = {
    "von_mises": "vonmises",
    "displacement": "displacementt",
}

# pyNastranGUI's default main-window render size on this setup, confirmed
# from actual screenshot dimensions (1606x768) -- used to pick a camera
# roll (view_up) that matches the OUTPUT frame's aspect ratio rather than
# whatever aspect the model's own silhouette happens to have. See
# _up_vector_for_best_frame_fit.
_RENDER_ASPECT_RATIO = 1606.0 / 768.0


def _eids_for_groups(names: list[str], ses_path: str | None) -> set[int]:
    if not ses_path:
        raise ValueError("a *_groups argument was given but ses_path was not provided")
    groups = parse_ses_groups(ses_path)
    eids: set[int] = set()
    for name in names:
        if name not in groups:
            raise ValueError(
                f"Group {name!r} not found in {ses_path}; "
                f"available groups: {sorted(groups)}"
            )
        eids.update(groups[name])
    return eids


def _eids_for_property_ids(bdf_path: Path, property_ids: list[int]) -> set[int]:
    from pyNastran.bdf.bdf import BDF

    model = BDF()
    model.read_bdf(str(bdf_path), xref=False)
    pid_set = set(property_ids)
    return {eid for eid, elem in model.elements.items() if elem.pid in pid_set}


def _eids_for_isolate(
    bdf_path: Path,
    isolate_groups: list[str] | None,
    isolate_property_ids: list[int] | None,
    ses_path: str | None,
) -> set[int]:
    """The set of element IDs isolate_groups/isolate_property_ids names (the
    elements to KEEP) -- split out of _resolve_hidden_eids so the camera
    code can also ask "what's actually being isolated?" without redoing the
    hide-vs-isolate resolution."""
    keep: set[int] = set()
    if isolate_groups:
        keep.update(_eids_for_groups(isolate_groups, ses_path))
    if isolate_property_ids:
        keep.update(_eids_for_property_ids(bdf_path, isolate_property_ids))
    return keep


def _resolve_hidden_eids(
    bdf_path: Path,
    hide_groups: list[str] | None,
    hide_property_ids: list[int] | None,
    isolate_groups: list[str] | None,
    isolate_property_ids: list[int] | None,
    ses_path: str | None,
) -> set[int]:
    """Figure out which element IDs to exclude from the render.

    hide_* name elements to remove (everything else stays). isolate_* name
    the elements to KEEP (everything else is removed) -- the complement --
    for "show only the ribs" style views, which also has the side effect of
    letting the camera fit tightly to just that subset (see
    render_model_view's docstring). Mutually exclusive with hide_*: mixing
    "remove these" and "keep only these" in one call is ambiguous, so this
    raises rather than guessing which one wins.

    Named groups are parsed from ses_path (a Patran/HyperMesh .ses session
    file -- see ses_groups.py and issue #9 for why this is preferred over
    raw property IDs when available).
    """
    hide_requested = bool(hide_groups or hide_property_ids)
    isolate_requested = bool(isolate_groups or isolate_property_ids)
    if hide_requested and isolate_requested:
        raise ValueError(
            "hide_* and isolate_* are mutually exclusive -- pick one "
            "(remove these elements, or keep only these elements)"
        )

    if isolate_requested:
        keep = _eids_for_isolate(bdf_path, isolate_groups, isolate_property_ids, ses_path)

        from pyNastran.bdf.bdf import BDF

        model = BDF()
        model.read_bdf(str(bdf_path), xref=False)
        all_eids = set(model.elements.keys())
        kept = all_eids & keep
        if not kept:
            # A .ses group can legitimately name IDs that aren't in
            # model.elements at all -- e.g. LUMPED_MASS entries are mass
            # points (CONM2, tracked separately in model.masses), not
            # standard elements. Isolating one used to silently render a
            # blank scene (everything hidden); worse, once render_stress_
            # contour started trimming the OP2 to match, a genuinely empty
            # kept set produced a degenerate OP2 with no tables at all,
            # which pyNastran's own reader treats as a fatal error. Catching
            # it here gives a clear reason instead of either outcome.
            raise ValueError(
                f"isolate_groups/isolate_property_ids matched 0 elements in "
                f"{bdf_path} -- the requested IDs may not correspond to "
                f"standard elements (e.g. mass points like CONM2 aren't in "
                f"model.elements)"
            )
        return all_eids - kept

    hidden: set[int] = set()
    if hide_groups:
        hidden.update(_eids_for_groups(hide_groups, ses_path))
    if hide_property_ids:
        hidden.update(_eids_for_property_ids(bdf_path, hide_property_ids))
    return hidden


def _write_filtered_bdf(bdf_path: Path, hidden_eids: set[int], output_path: Path) -> None:
    """Write a copy of bdf_path with hidden_eids removed, to output_path.

    Filtering at the BDF level (rather than calling pyNastranGUI's
    hide_eids on the loaded model) sidesteps a real performance/hang
    concern found during issue #8's spike: hide_eids did not complete
    within 180s (possibly hung, not just slow) on the full ~35k-element
    wingbox model. Rewriting the deck without the unwanted elements before
    the GUI ever loads it is simpler and avoids that entirely.
    """
    from pyNastran.bdf.bdf import BDF

    model = BDF()
    model.read_bdf(str(bdf_path), xref=False)
    for eid in hidden_eids:
        model.elements.pop(eid, None)
    model.write_bdf(str(output_path), size=8, enddata=True)


def _write_filtered_op2(op2_path: Path, kept_eids: set[int], output_path: Path) -> None:
    """Write a copy of op2_path with every stress result trimmed down to
    kept_eids, and every other result table (displacements, spc_forces,
    load_vectors, ...) dropped entirely, to output_path.

    This is the fix for a real hang found in issue #9: pairing a BDF
    filtered down to an isolated subset (a handful of elements) with the
    ORIGINAL full-model OP2 (results for all ~35k elements/~14k nodes) sent
    pyNastranGUI's result-loading code down a path that didn't complete
    within 240s -- confirmed by timing it directly. Trimming the OP2 to
    match the filtered BDF's element set exactly (and dropping every result
    category we're not going to use for a stress fringe anyway, since THEIR
    node/element counts would reintroduce the same mismatch) brought the
    same load down to ~12s in testing.

    Only stress results are kept since that's all render_stress_contour's
    fringe needs. nelements is recomputed from the actual number of unique
    element IDs kept rather than the trimmed row count, since plate types
    store 2 fiber rows per element but bar types store 1.
    """
    import numpy as np
    from pyNastran.op2.op2 import OP2

    op2 = OP2(debug=False)
    op2.read_op2(str(op2_path), build_dataframe=False)
    kept_array = np.fromiter(kept_eids, dtype="int64")

    stress = op2.op2_results.stress
    for attr_name in dir(stress):
        if not attr_name.endswith("_stress") or attr_name.startswith("_"):
            continue
        subcases = getattr(stress, attr_name)
        if not subcases:
            continue
        for subcase, arr in list(subcases.items()):
            has_element_node = hasattr(arr, "element_node")
            id_col = arr.element_node[:, 0] if has_element_node else arr.element
            mask = np.isin(id_col, kept_array)
            if not mask.any():
                del subcases[subcase]
                continue
            arr.data = arr.data[:, mask, :]
            if has_element_node:
                arr.element_node = arr.element_node[mask, :]
                arr.nelements = len(np.unique(arr.element_node[:, 0]))
            else:
                arr.element = arr.element[mask]
                arr.nelements = int(mask.sum())
            arr.ntotal = int(mask.sum())

    # Drop other populated result categories (direct OP2 attributes, not
    # nested under op2_results like stress is) -- their node/element counts
    # would reconcile against the isolated subset just as badly as the
    # original full-model stress array did, and render_stress_contour never
    # uses them anyway. get_table_types() also lists many attributes that
    # don't exist as plain OP2 attributes (nested under op2_results in ways
    # that vary by table), so this targets the handful of top-level result
    # dicts that are actually ever populated by a real solve rather than
    # trying to walk every path it returns.
    for attr_name in (
        "displacements", "velocities", "accelerations",
        "spc_forces", "mpc_forces", "load_vectors", "applied_loads",
        "grid_point_forces", "strain_energy", "temperatures",
    ):
        if getattr(op2, attr_name, None):
            setattr(op2, attr_name, {})

    op2.write_op2(str(output_path))


def _up_vector_for_best_frame_fit(
    view_direction, points, target_aspect: float = _RENDER_ASPECT_RATIO, n_steps: int = 360
) -> "np.ndarray":
    """Given a view direction and a (n, 3) array of world-space points,
    return a view_up vector that rolls the camera so the points' bounding
    box, projected into the plane perpendicular to view_direction, matches
    the render's own aspect ratio as closely as possible.

    ResetCamera fits whichever dimension (screen width or height) is more
    constraining and leaves margin on the other -- it can't change the
    frame's shape. So the fraction of the frame actually filled is governed
    entirely by how close the projected bounding box's aspect ratio is to
    the frame's, not by how tightly the box wraps the model. A previous
    PCA-based approach (align the point cloud's long axis horizontal)
    picked whichever roll made the cloud's own natural bounding box
    tightest, which is a different objective and can pick a badly-mismatched
    aspect ratio -- confirmed against the isolated-ribs case, where a fan of
    ~50 near-parallel rib planes has a naturally tall, narrow silhouette:
    PCA picked a bounding-box aspect of ~4.3:1 against this setup's ~2.1:1
    frame, filling the width but leaving large empty margins top and bottom.
    Directly searching for the roll that matches the frame's aspect ratio
    instead brings that to ~1:1 (no systematic margin on either axis) for
    every case tried (ribs, shear webs, spars, skin panels).

    Shared by both the governing-stress-element camera and the
    isolated-group camera below. n_steps=360 (half-degree resolution over
    the 0-180 degree period a roll axis repeats in) is cheap even for
    thousands of points since it's fully vectorized.
    """
    import numpy as np

    arbitrary = (
        np.array([0.0, 0.0, 1.0]) if abs(view_direction[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    )
    e1 = np.cross(view_direction, arbitrary)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(view_direction, e1)

    centered = points - points.mean(axis=0)
    u = centered @ e1
    v = centered @ e2

    thetas = np.linspace(0.0, np.pi, n_steps, endpoint=False)
    cos_t = np.cos(thetas)[:, None]
    sin_t = np.sin(thetas)[:, None]
    # Rotating (u, v) by theta is equivalent to rolling the camera so that
    # up = sin(theta)*e1 + cos(theta)*e2 maps to screen-vertical.
    rotated_u = u[None, :] * cos_t - v[None, :] * sin_t
    rotated_v = u[None, :] * sin_t + v[None, :] * cos_t
    width = rotated_u.max(axis=1) - rotated_u.min(axis=1)
    height = rotated_v.max(axis=1) - rotated_v.min(axis=1)
    aspect = width / height
    mismatch = np.abs(np.log(aspect / target_aspect))
    theta = thetas[np.argmin(mismatch)]

    up = np.sin(theta) * e1 + np.cos(theta) * e2
    return up


def _legend_corner_y(view_direction, up, points) -> float:
    """Given a finalized camera direction/up and the same points used to
    frame it, return the scalar-bar's y position (in the 0.85-x, VTK-
    bottom-up viewport coordinates _build_postscript's legend_block uses):
    0.56 for top-right, 0.08 for bottom-right.

    Top-right is usually empty (every isolate/governing-element "auto"
    camera tends to run its content diagonally from the root/thick end at
    upper-left to the tip/thin end at lower-right), but not always -- a
    wide fan (e.g. the real wingbox's ShearWebs group) can spread far
    enough to reach into it too, confirmed by an actual clash in a
    published render. Rather than assume, project the same points used to
    frame the camera onto the screen's own (right, up) axes -- right =
    up x view_direction, the same relationship implied by
    _up_vector_for_best_frame_fit's rotation -- normalize by the points'
    own bounding box (a fair proxy for what fills the frame after
    ResetCamera, since the aspect-fit up-vector already makes that bounding
    box's aspect ratio closely match the frame's), and check which
    candidate corner region actually has fewer points in it.

    No extra render/screenshot needed -- this reuses geometry already in
    hand. (An earlier version of this fix took a real probe screenshot
    with the legend hidden and measured the image directly instead; that
    approach turned out to hang pyNastranGUI when on_take_screenshot was
    called a second time in the same process, confirmed by two end-to-end
    tests timing out at their full 60s/300s limits -- this analytical
    version avoids the second screenshot call entirely.)
    """
    import numpy as np

    right = np.cross(up, view_direction)
    screen_x = points @ right
    screen_y = points @ up

    x_center = (screen_x.max() + screen_x.min()) / 2.0
    x_half = (screen_x.max() - screen_x.min()) / 2.0
    y_center = (screen_y.max() + screen_y.min()) / 2.0
    y_half = (screen_y.max() - screen_y.min()) / 2.0
    if x_half == 0 or y_half == 0:
        return 0.56

    norm_x = (screen_x - x_center) / x_half
    norm_y = (screen_y - y_center) / y_half

    # Matches the legend's actual footprint (x in [0.85, 0.97], width 0.12)
    # translated from 0-1 viewport coordinates into this -1..1 normalized
    # space, with a little margin: viewport 0.83 -> norm 0.66.
    in_right_column = norm_x > 0.66
    top_right_count = np.count_nonzero(in_right_column & (norm_y > 0.08))
    bottom_right_count = np.count_nonzero(in_right_column & (norm_y < -0.08))

    return 0.56 if top_right_count <= bottom_right_count else 0.08


def _camera_look_direction_for_governing_element(
    bdf_path: Path, op2_path: Path
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    float,
] | None:
    """Compute (focal_point, camera_position, view_up, legend_y) so a camera
    looking through them views the model from whichever isometric octant
    best faces the outward face normal of whichever plate element
    (CQUAD4/CTRIA3) has the governing (highest) von Mises stress in
    op2_path -- see get_max_stress for why plate vs. bar stress isn't
    blended into one number. legend_y (see _legend_corner_y) is where
    _build_postscript should place the scalar-bar legend so it doesn't
    clash with the model.

    An isometric direction (equal angle to all three world axes) is a
    standard engineering-drawing convention specifically because no single
    axis is degenerate, so it can't collapse a flat panel edge-on the way a
    "top"/"side" preset can. Rather than aiming continuously straight down
    the governing element's own computed normal (this function's previous
    approach), this picks whichever of the 8 canonical isometric octants
    the element's normal points most toward (highest dot product) --
    simpler, a more standard/recognizable view, and more robust to messy
    per-element geometry (a slightly-off normal from a warped quad still
    resolves to the same octant, rather than continuously skewing the
    computed direction).

    camera_position is placed far enough out (2x the model's bounding-box
    diagonal) that a subsequent vtkRenderer.ResetCamera() call -- which
    preserves view direction/up but repositions along it to fit the whole
    scene -- fits the entire model into frame rather than just the one
    element.

    Returns None if there's no plate stress result to aim at (e.g. a
    bar-only model where a CBAR governs, which has no comparable face
    normal) -- callers should fall back to a fixed camera preset.
    """
    import numpy as np
    from pyNastran.bdf.bdf import BDF

    peaks = get_max_stress(str(op2_path))
    plate_peaks = {etype: peak for etype, peak in peaks.items() if "von_mises" in peak}
    if not plate_peaks:
        return None
    _governing_type, governing = max(plate_peaks.items(), key=lambda kv: kv[1]["von_mises"])
    eid = governing["element_id"]

    model = BDF(debug=False)
    model.read_bdf(str(bdf_path), xref=False)
    elem = model.elements[eid]
    coords = np.array([model.nodes[nid].get_position() for nid in elem.nodes])
    centroid = coords.mean(axis=0)

    if len(coords) >= 4:
        normal = np.cross(coords[2] - coords[0], coords[3] - coords[1])
    else:
        normal = np.cross(coords[1] - coords[0], coords[2] - coords[0])
    norm_length = np.linalg.norm(normal)
    if norm_length == 0:
        return None
    normal = normal / norm_length

    all_coords = np.array([grid.get_position() for grid in model.nodes.values()])
    bbox_min, bbox_max = all_coords.min(axis=0), all_coords.max(axis=0)
    bbox_center = (bbox_min + bbox_max) / 2.0

    # CQUAD4/CTRIA3 node winding gives an arbitrary sign -- point the normal
    # away from the model's bulk (outward) regardless of winding convention,
    # by checking which side of the model center the element actually sits.
    if np.dot(centroid - bbox_center, normal) < 0:
        normal = -normal

    octant_directions = np.array(
        [[sx, sy, sz] for sx in (1.0, -1.0) for sy in (1.0, -1.0) for sz in (1.0, -1.0)]
    ) / np.sqrt(3.0)
    view_from_direction = octant_directions[np.argmax(octant_directions @ normal)]

    diag = float(np.linalg.norm(bbox_max - bbox_min))
    camera_position = bbox_center + view_from_direction * diag * 2.0
    up = _up_vector_for_best_frame_fit(view_from_direction, all_coords)
    legend_y = _legend_corner_y(view_from_direction, up, all_coords)

    return (
        (float(bbox_center[0]), float(bbox_center[1]), float(bbox_center[2])),
        (float(camera_position[0]), float(camera_position[1]), float(camera_position[2])),
        (float(up[0]), float(up[1]), float(up[2])),
        legend_y,
    )


def _camera_look_direction_for_isolated_group(
    bdf_path: Path, eids: set[int], tilt_deg: float = 40.0
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    float,
] | None:
    """Compute (focal_point, camera_position, view_up, legend_y) for
    isolate_groups/isolate_property_ids views, tuned for the common case of
    a group of roughly-parallel planar elements (ribs, spars, frames, ...).
    legend_y (see _legend_corner_y) is where _build_postscript should place
    the scalar-bar legend so it doesn't clash with the model.

    A view straight down the group's shared face normal perfectly overlaps
    every parallel element into one; a view perpendicular to it (what a
    fixed "iso"/"top"/"side" preset often ends up giving, since they're
    picked for the whole original model, not this subset -- see
    render_model_view's docstring) collapses them edge-on into an
    unreadable sliver, confirmed against a real render of an isolated ribs
    group. Splitting the difference -- tilted tilt_deg off the shared
    normal, mostly looking along it but angled enough to see each element's
    extent -- is the standard oblique "stacked bulkheads" engineering view:
    the parallel elements fan out and are individually distinguishable.

    tilt_deg=40 (previously 65, before _up_vector_for_best_frame_fit
    existed) trades a little separation for a lot more per-element face
    area: at 65 degrees each rib/web is viewed close to edge-on, so once
    framing stopped being the bottleneck (see
    _up_vector_for_best_frame_fit), the plates themselves were still too
    foreshortened to show their own internal stress gradient -- confirmed
    by re-rendering the real ribs and shear-web groups at both angles: 40
    degrees keeps every element distinguishable while making each one wide
    enough to actually read.

    The shared normal is the average of each element's own local outward
    normal (hemisphere-aligned first, since node winding can flip sign
    element to element -- naively averaging raw normals could otherwise
    cancel toward zero). Returns None if eids has no plate elements to
    compute a normal from (e.g. an isolated group of only CBARs).
    """
    import numpy as np
    from pyNastran.bdf.bdf import BDF

    model = BDF(debug=False)
    model.read_bdf(str(bdf_path), xref=False)

    normals = []
    group_coords = []
    for eid in eids:
        elem = model.elements.get(eid)
        if elem is None or elem.type not in ("CQUAD4", "CTRIA3"):
            continue
        coords = np.array([model.nodes[nid].get_position() for nid in elem.nodes])
        group_coords.append(coords)
        if len(coords) >= 4:
            normal = np.cross(coords[2] - coords[0], coords[3] - coords[1])
        else:
            normal = np.cross(coords[1] - coords[0], coords[2] - coords[0])
        length = np.linalg.norm(normal)
        if length > 0:
            normals.append(normal / length)

    if not normals:
        return None

    normals = np.array(normals)
    reference = normals[0]
    flipped = np.where((normals @ reference < 0)[:, None], -normals, normals)
    mean_normal = flipped.mean(axis=0)
    mean_normal /= np.linalg.norm(mean_normal)

    group_points = np.concatenate(group_coords, axis=0)
    bbox_min, bbox_max = group_points.min(axis=0), group_points.max(axis=0)
    bbox_center = (bbox_min + bbox_max) / 2.0
    diag = float(np.linalg.norm(bbox_max - bbox_min))

    # Tilt the view off the shared normal, toward whichever world axis is
    # least parallel to it, so the tilt actually shows up in the image
    # rather than being swallowed by an unlucky choice of tangent.
    world_axes = np.eye(3)
    tangent_candidate = world_axes[np.argmin(np.abs(world_axes @ mean_normal))]
    tangent = tangent_candidate - np.dot(tangent_candidate, mean_normal) * mean_normal
    tangent /= np.linalg.norm(tangent)

    theta = np.radians(tilt_deg)
    view_from_direction = np.cos(theta) * mean_normal + np.sin(theta) * tangent
    view_from_direction /= np.linalg.norm(view_from_direction)

    camera_position = bbox_center + view_from_direction * diag * 2.0
    up = _up_vector_for_best_frame_fit(view_from_direction, group_points)
    legend_y = _legend_corner_y(view_from_direction, up, group_points)

    return (
        (float(bbox_center[0]), float(bbox_center[1]), float(bbox_center[2])),
        (float(camera_position[0]), float(camera_position[1]), float(camera_position[2])),
        (float(up[0]), float(up[1]), float(up[2])),
        legend_y,
    )


def _build_postscript(
    output_png: Path,
    camera: str,
    zoom: float,
    want_stress_fringe: bool,
    custom_camera: tuple[
        tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]
    ]
    | None = None,
    legend_y: float = 0.56,
    fringe_match: str = "vonmises",
) -> str:
    """Build a pyNastranGUI postscript (see spikes/pynastrangui_screenshot_
    postscript.py and issue #8) that sets a camera preset, optionally
    selects a von Mises stress result as the active fringe, takes a
    screenshot, and exits.

    magnify=1 is passed to on_take_screenshot explicitly: pyNastranGUI's
    default (magnify=5, i.e. vtkRenderLargeImage-based tiling) renders the
    3D geometry at 5x resolution but NOT the 2D overlay actors (legend,
    orientation axes) at the same scale, making them look disproportionately
    huge relative to the model in the final image -- confirmed by comparing
    screenshots with and without an explicit magnify during development.

    The background is set to flat white (pyNastranGUI's own
    set_background_color_to_white, normally used for GIF export) instead of
    its default dark gradient -- reads better in a blog post. The corner
    orientation-axes triad is always hidden and the scalar-bar legend is
    always shrunk to a fixed, modest font size (rather than pyNastranGUI's
    default, which auto-scales the legend text to fill its bounding box --
    on an 11-label bar that makes 5-digit stress values render enormous, as
    confirmed against the first published renders) with explicit black
    title/label text (visible against the white background; pyNastranGUI's
    default white text was tuned for its old dark background) and pinned to
    the top-right corner, which every "auto" camera in this module
    consistently leaves empty (content runs diagonally from the root/thick
    end at upper-left to the tip/thin end at lower-right across every group
    tested against the real wingbox case study) -- the previous
    bottom-right placement could clash with a fan's own tip reaching into
    that corner. When there's no meaningful result to show
    (want_stress_fringe=False, e.g. a plain render_model_view call), the
    legend is hidden entirely instead of left showing pyNastranGUI's
    default NodeID coloring.

    zoom (>1 zooms in) is applied after the camera is set via self.zoom() --
    a plain camera reset (or vtkRenderer.ResetCamera()) alone leaves
    substantial empty margin around the model rather than filling the frame;
    see render_model_view's docstring for when to increase this (isolate_*
    callers get a non-1.0 default automatically).

    custom_camera, when given, overrides the named azimuth/elevation preset
    entirely: (focal_point, position, view_up), all (x, y, z) world
    coordinates/vectors, computed by
    _camera_look_direction_for_governing_element so the camera views the
    model from whichever isometric octant best faces the governing stress
    element's outward face normal -- guaranteeing an unobstructed view of
    it -- then vtkRenderer.ResetCamera() fits the whole model to the frame
    along that fixed direction.

    want_stress_fringe searches the loaded result cases for one whose name
    matches fringe_match case/whitespace/underscore-insensitively (default
    "vonmises" -- pyNastran labels it 'vonMises', confirmed by inspecting
    self.result_cases during the spike; render_stress_contour's
    result="displacement" passes "displacementt" instead, matching
    'Displacement T_XYZ' but not the rotational 'Displacement R_XYZ') and
    reports whether it found one via a small sidecar file
    (<output_png>.fringe_set, "1" or "0") next to the screenshot, since the
    postscript runs in a separate subprocess with no other way to report
    back to the caller. Falls back to whatever pyNastranGUI displays by
    default (e.g. for a bar-only model with no plate von Mises result) if
    no matching case is found.
    """
    output_png_repr = repr(str(output_png))
    fringe_flag_repr = repr(str(output_png) + ".fringe_set")

    # "Global XYZ" is a real 3D-space actor pyNastran draws at the model's
    # coordinate-system origin (see nastran_io.py/tool_actions.py's
    # create_coordinate_system) -- NOT the small screen-corner orientation
    # widget set_corner_axis_visiblity controls. It sits far from the
    # model's own geometry (the origin is rarely inside the model's bounding
    # box), so it must be hidden BEFORE any ResetCamera() call, not after --
    # confirmed by testing: hiding it afterward still let its off-to-the-side
    # bounds skew the auto-fit, pushing the actual model to one edge of the
    # frame instead of filling it.
    axes_block = """\
self.set_corner_axis_visiblity(False)
if 'Global XYZ' in self.geometry_actors:
    self.geometry_actors['Global XYZ'].VisibilityOff()
"""

    # pyNastranGUI's default is a dark gradient background -- set_background_
    # color_to_white is its own built-in helper (used for GIF export) that
    # both disables the gradient and sets a flat white background, which
    # reads much better in a blog post than the default. render=False since
    # nothing's on screen to redraw yet (no interactive window in this
    # scripted/non-interactive mode).
    background_block = """\
self.settings.set_background_color_to_white(render=False)
"""

    if custom_camera is not None:
        (fx, fy, fz), (px, py, pz), (ux, uy, uz) = custom_camera
        camera_block = f"""\
_camera = self.rend.GetActiveCamera()
_camera.SetFocalPoint({fx!r}, {fy!r}, {fz!r})
_camera.SetPosition({px!r}, {py!r}, {pz!r})
_camera.SetViewUp({ux!r}, {uy!r}, {uz!r})
self.rend.ResetCamera()
self.rend.ResetCameraClippingRange()
"""
    else:
        azimuth, elevation = _CAMERA_PRESETS[camera]
        camera_block = f"""\
self.on_reset_camera()
_camera = self.rend.GetActiveCamera()
_camera.Azimuth({azimuth})
_camera.Elevation({elevation})
self.rend.ResetCameraClippingRange()
"""

    if want_stress_fringe:
        # legend_y (0.56 for top-right, 0.08 for bottom-right) is decided by
        # the caller -- see _legend_corner_y -- by analyzing where the
        # model's own geometry actually falls in the projected frame, since
        # top-right isn't always the empty corner (a wide fan, e.g. the real
        # wingbox's ShearWebs group, can reach into it -- confirmed by an
        # actual clash in a published render). Text color is explicit black
        # (rather than pyNastranGUI's default white) since it's now sitting
        # on the white background set above instead of the old dark one.
        legend_block = f"""\
_sb = self.scalar_bar.scalar_bar
_sb.SetUnconstrainedFontSize(True)
_sb.SetWidth(0.12)
_sb.SetHeight(0.38)
_sb.SetPosition(0.85, {legend_y!r})
_sb.GetTitleTextProperty().SetFontSize(16)
_sb.GetTitleTextProperty().SetColor(0.0, 0.0, 0.0)
_sb.GetLabelTextProperty().SetFontSize(13)
_sb.GetLabelTextProperty().SetColor(0.0, 0.0, 0.0)
"""
    else:
        # No result to show a scale for -- hiding just the legend while
        # leaving pyNastranGUI's default NodeID rainbow coloring on the mesh
        # looks broken (color gradient with nothing to explain it), so
        # switch the mapper to a solid neutral color instead. Darker than
        # the previous (0.7, 0.7, 0.75) -- that was tuned for contrast
        # against the old dark background and washes out against white.
        legend_block = """\
self.scalar_bar.set_visibility(False)
self.grid_mapper.ScalarVisibilityOff()
self.geometry_actors['main'].GetProperty().SetColor(0.55, 0.55, 0.6)
"""

    fringe_block = ""
    if want_stress_fringe:
        fringe_block = f"""
_fringe_set = False
for _key, _val in self.result_cases.items():
    try:
        _obj, (_i, _resname) = _val
    except Exception:
        continue
    if {fringe_match!r} in _resname.lower().replace(" ", "").replace("_", ""):
        self.on_fringe(_key)
        _fringe_set = True
        break
with open({fringe_flag_repr}, "w") as _f:
    _f.write("1" if _fringe_set else "0")
"""

    return f"""\
{axes_block}
{background_block}
{camera_block}
self.zoom({zoom})
{fringe_block}
{legend_block}
self.on_take_screenshot({output_png_repr}, magnify=1, show_msg=False)

import sys
sys.exit(0)
"""


def _run_pynastrangui(
    bdf_path: Path, op2_path: Path | None, postscript_path: Path, timeout: float
) -> subprocess.CompletedProcess:
    args = [sys.executable, "-m", "pyNastran.gui.gui", "-i", str(bdf_path)]
    if op2_path is not None:
        args += ["-o", str(op2_path)]
    args += ["-f", "nastran", "-p", str(postscript_path)]

    return subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def _render(
    *,
    bdf_path: str,
    op2_path: str | None,
    output_png: str,
    hide_groups: list[str] | None,
    hide_property_ids: list[int] | None,
    isolate_groups: list[str] | None,
    isolate_property_ids: list[int] | None,
    ses_path: str | None,
    camera: str,
    zoom: float | None,
    timeout: float | None,
    result: str = "von_mises",
) -> dict[str, Any]:
    in_path = Path(bdf_path).resolve()
    if not in_path.is_file():
        raise FileNotFoundError(f"BDF file not found: {in_path}")

    resolved_op2_path = None
    if op2_path is not None:
        resolved_op2_path = Path(op2_path).resolve()
        if not resolved_op2_path.is_file():
            raise FileNotFoundError(f"OP2 file not found: {resolved_op2_path}")

    if camera != "auto" and camera not in _CAMERA_PRESETS:
        raise ValueError(
            f"Unknown camera preset {camera!r}; choose from "
            f"{sorted(_CAMERA_PRESETS) + ['auto']}"
        )
    if result not in _FRINGE_RESULT_MATCH:
        raise ValueError(
            f"Unknown result {result!r}; choose from {sorted(_FRINGE_RESULT_MATCH)}"
        )
    is_isolating = bool(isolate_groups or isolate_property_ids)
    if camera == "auto" and resolved_op2_path is None and not is_isolating:
        raise ValueError(
            "camera='auto' needs something to aim at: either op2_path (aims "
            "at the governing stress element -- use render_stress_contour) "
            "or isolate_groups/isolate_property_ids (aims for a readable "
            "view of the isolated elements' shared face normal). With "
            "neither, pick a fixed preset instead."
        )

    out_png = Path(output_png).resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    render_timeout = timeout if timeout is not None else _DEFAULT_RENDER_TIMEOUT_S

    custom_camera = None
    legend_y = 0.56
    if camera == "auto":
        camera_result = None
        if is_isolating:
            # Isolating removes everything else from the scene, so the
            # occlusion concern the governing-element camera solves doesn't
            # apply -- whatever's left is visible no matter which way it's
            # viewed. What actually matters for a sub-component view (ribs,
            # spars, skin panels, ...) is showing the isolated elements
            # themselves well, e.g. fanning out parallel ribs instead of
            # collapsing them edge-on -- so this takes priority over aiming
            # at the model's single worst element, which might not even be
            # part of the isolated group (isolating ribs while the worst
            # element is on the skin would otherwise aim off of what's
            # actually being shown).
            isolated_eids = _eids_for_isolate(
                in_path, isolate_groups, isolate_property_ids, ses_path
            )
            camera_result = _camera_look_direction_for_isolated_group(in_path, isolated_eids)
        if camera_result is None and resolved_op2_path is not None:
            camera_result = _camera_look_direction_for_governing_element(
                in_path, resolved_op2_path
            )
        if camera_result is None:
            # Bar-only model/group (e.g. CBAR governs, or an isolated group
            # of only CBARs) -- no plate face normal to aim at, so fall back
            # to the generic iso preset rather than erroring on an
            # otherwise-valid render request.
            camera = "iso"
        else:
            custom_camera, legend_y = camera_result[:3], camera_result[3]

    # A custom_camera (either "auto" mode) is a tight, deliberate framing --
    # it fits tighter by default than a generic preset so it actually fills
    # the frame (see render_model_view's docstring). Isolating without a
    # custom_camera (fixed preset applied to a filtered-down scene) still
    # benefits from a tighter default than the un-zoomed 1.0 used for a
    # plain full-model preset view, just not as tight as the tuned "auto"
    # views. An isolated stress contour is zoomed slightly less than an
    # isolated plain geometry view (1.9 vs 2.2) purely to leave room for the
    # legend, which the geometry-only view doesn't have. All values verified
    # by eye during development against the real wingbox case study.
    if zoom is not None:
        resolved_zoom = zoom
    elif custom_camera is not None:
        if is_isolating:
            resolved_zoom = 1.9 if resolved_op2_path is not None else 2.2
        else:
            resolved_zoom = 2.1
    elif is_isolating:
        resolved_zoom = 1.5
    else:
        resolved_zoom = 1.0

    hidden_eids = _resolve_hidden_eids(
        in_path, hide_groups, hide_property_ids, isolate_groups, isolate_property_ids, ses_path
    )
    if hidden_eids and result != "von_mises" and resolved_op2_path is not None:
        # _write_filtered_op2 (below) drops displacement/velocity/etc.
        # result categories entirely -- it only ever trims and keeps stress
        # tables, since that's all a von Mises fringe needs. A displacement
        # fringe combined with hide_*/isolate_* would silently find nothing
        # to show (fringe_set=False) rather than erroring, which is worse
        # than just saying so up front: this combination isn't supported
        # yet, so ask for the untrimmed model instead of guessing.
        raise ValueError(
            f"result={result!r} with hide_groups/hide_property_ids/"
            "isolate_groups/isolate_property_ids isn't supported yet -- "
            "the OP2 trimming needed to avoid the geometry/results mismatch "
            "hang (see _write_filtered_op2) only preserves stress tables, "
            "so a displacement fringe would silently find nothing to show. "
            "Render the full, untrimmed model for a displacement contour."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        render_bdf_path = in_path
        if hidden_eids:
            render_bdf_path = tmpdir_path / "filtered.bdf"
            _write_filtered_bdf(in_path, hidden_eids, render_bdf_path)

        render_op2_path = resolved_op2_path
        if hidden_eids and resolved_op2_path is not None:
            # Pairing a filtered-down BDF with the ORIGINAL full-model OP2
            # is exactly the mismatch that hangs pyNastranGUI (see
            # _write_filtered_op2's docstring / issue #9) -- trim the OP2 to
            # the same kept element set so geometry and results match.
            # render_bdf_path was just written with hidden_eids removed, so
            # reading its own element IDs back is the simplest way to get
            # exactly what's actually being rendered.
            from pyNastran.bdf.bdf import BDF

            kept_model = BDF()
            kept_model.read_bdf(str(render_bdf_path), xref=False)
            kept_eids = set(kept_model.elements.keys())

            render_op2_path = tmpdir_path / "filtered.OP2"
            _write_filtered_op2(resolved_op2_path, kept_eids, render_op2_path)

        if result == "von_mises":
            # Only actually attempt the vonMises fringe if a plate stress
            # result (the only type get_max_stress calls "von_mises") is
            # genuinely present in what's about to be loaded. This matters
            # beyond just "don't bother" for a bar-only selection:
            # pyNastranGUI internally synthesizes a bar-stress-derived
            # pseudo-vonMises case even when there's no plate data at all
            # (see render_stress_contour's "GUI-internal combined scalar"
            # caveat), and _build_postscript's search would happily find and
            # apply THAT instead -- confirmed to be dramatically slow for a
            # large all-CBAR selection (a Stiffeners-only isolate, 14,134
            # elements, hung past 180s with it, vs. ~15s once skipped
            # entirely). CBARs were never going to get a meaningful von
            # Mises fringe anyway (see get_max_stress's "max_stress" vs
            # "von_mises" distinction), so there's nothing lost by not
            # trying.
            want_stress_fringe = False
            if render_op2_path is not None:
                try:
                    peaks = get_max_stress(str(render_op2_path))
                except ValueError:
                    peaks = {}
                want_stress_fringe = any("von_mises" in peak for peak in peaks.values())
        else:
            # Displacement is a genuine nodal result already in the OP2
            # (whenever the case control requested it), not something
            # pyNastranGUI synthesizes on the fly the way it does the bar
            # pseudo-vonMises case above -- so there's no analogous hang
            # risk to guard against, just "is there an OP2 loaded at all".
            want_stress_fringe = render_op2_path is not None

        postscript_path = tmpdir_path / "postscript.py"
        postscript_path.write_text(
            _build_postscript(
                out_png,
                camera,
                resolved_zoom,
                want_stress_fringe=want_stress_fringe,
                custom_camera=custom_camera,
                fringe_match=_FRINGE_RESULT_MATCH[result],
                legend_y=legend_y,
            )
        )
        fringe_flag_path = Path(str(out_png) + ".fringe_set")
        fringe_flag_path.unlink(missing_ok=True)

        try:
            proc = _run_pynastrangui(render_bdf_path, render_op2_path, postscript_path, render_timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"pyNastranGUI did not finish within {render_timeout}s"
            ) from exc

    fringe_set = None
    if resolved_op2_path is not None:
        # Known False without needing the subprocess's sidecar file when we
        # never asked it to look (see want_stress_fringe above) -- only an
        # attempted-but-not-found search actually depends on that.
        fringe_set = False
    if fringe_flag_path.is_file():
        fringe_set = fringe_flag_path.read_text().strip() == "1"
        fringe_flag_path.unlink()

    if not out_png.is_file():
        return {
            "success": False,
            "output_png": str(out_png),
            "hidden_element_count": len(hidden_eids),
            "errors": [
                f"pyNastranGUI did not produce {out_png}",
                f"returncode={proc.returncode}",
                proc.stdout[-2000:] if proc.stdout else "",
            ],
        }

    result: dict[str, Any] = {
        "success": True,
        "output_png": str(out_png),
        "hidden_element_count": len(hidden_eids),
        "returncode": proc.returncode,
    }
    if fringe_set is not None:
        result["fringe_set"] = fringe_set
    return result


@mcp.tool()
def render_model_view(
    bdf_path: str,
    output_png: str,
    hide_groups: list[str] | None = None,
    hide_property_ids: list[int] | None = None,
    isolate_groups: list[str] | None = None,
    isolate_property_ids: list[int] | None = None,
    ses_path: str | None = None,
    camera: str = "iso",
    zoom: float | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Render a plain geometry screenshot (no results) of bdf_path to
    output_png, via a scripted pyNastranGUI session -- see issues #8/#9 for
    how this works and its real limitations (needs an active desktop
    session; this is non-interactive, not display-less headless).

    hide_groups/hide_property_ids: remove these elements from the view,
    keep everything else. isolate_groups/isolate_property_ids: the inverse
    -- keep ONLY these elements, remove everything else, e.g. "show only
    the ribs". Mutually exclusive with hide_* (raises if both are given).
    Named groups are parsed from ses_path (a Patran/HyperMesh .ses session
    file -- see ses_groups.parse_ses_groups); only some case studies ship
    one of these. *_property_ids filter by raw PSHELL/PBAR property ID
    instead (or in addition) -- the fallback when no .ses file exists.

    camera: default "iso" (also "top"/"side"/"front", see _CAMERA_PRESETS) applies a
    generic preset chosen for the whole original model, which may not suit
    an isolated subset's actual shape well -- e.g. isolating thin,
    mostly-planar groups like ribs can render them near edge-on, collapsed
    into an unreadable sliver (confirmed against a real render). Pass
    camera="auto" together with isolate_groups/isolate_property_ids instead
    to aim for the isolated elements' shared face normal, tilted ~40 degrees
    off it, fanning out parallel elements so each is distinguishable rather
    than overlapping (see _camera_look_direction_for_isolated_group).
    "auto" isn't the default here because it has nothing to aim at without
    either op2_path (this tool doesn't take one -- see
    render_stress_contour, where "auto" *is* the default) or isolate_*; it
    raises in that case rather than guessing.
    zoom: >1 zooms in after the camera is set (plain camera reset alone
    leaves significant empty margin around the model). Default is 1.0 (no
    zoom) normally; 1.5 with isolate_* and a fixed preset (an isolated
    subset is typically small relative to the original scene, so the
    tighter default actually fills the frame instead of leaving it mostly
    empty); 2.2 with isolate_* and camera="auto" (its deliberate framing
    fits tighter still). All verified by eye during development against the
    real wingbox case study -- override explicitly if a default crops or
    under-fills your case.

    The corner orientation-axes triad is always hidden, and since there's no
    result to show, the legend is hidden too (rather than left showing
    pyNastranGUI's default NodeID coloring, which isn't a result and was a
    documented point of confusion in the first published renders).

    Raises for infrastructure problems (missing bdf_path, unknown camera
    preset, unknown group name, hide_*/isolate_* both given, pyNastranGUI
    timing out). A render that runs but doesn't produce the expected PNG is
    returned as {"success": False, "errors": [...]} instead.
    """
    return _render(
        bdf_path=bdf_path,
        op2_path=None,
        output_png=output_png,
        hide_groups=hide_groups,
        hide_property_ids=hide_property_ids,
        isolate_groups=isolate_groups,
        isolate_property_ids=isolate_property_ids,
        ses_path=ses_path,
        camera=camera,
        zoom=zoom,
        timeout=timeout,
    )


@mcp.tool()
def render_stress_contour(
    bdf_path: str,
    op2_path: str,
    output_png: str,
    hide_groups: list[str] | None = None,
    hide_property_ids: list[int] | None = None,
    isolate_groups: list[str] | None = None,
    isolate_property_ids: list[int] | None = None,
    ses_path: str | None = None,
    camera: str = "auto",
    zoom: float | None = None,
    timeout: float | None = None,
    result: str = "von_mises",
) -> dict[str, Any]:
    """Same as render_model_view, but also loads op2_path and colors the
    view by a result -- "von_mises" (default) or "displacement" (nodal
    translational displacement magnitude, T_XYZ; rotational displacement
    isn't exposed here). The result dict includes "fringe_set": True if a
    matching result case was found and applied, False if the OP2 has no
    such case (e.g. a bar-only model with result="von_mises" -- see
    get_max_stress's "max_stress" vs "von_mises" distinction) and
    pyNastranGUI's default coloring was left in place instead.

    result="displacement" doesn't support hide_groups/hide_property_ids/
    isolate_groups/isolate_property_ids (raises if combined) -- the OP2
    trimming those need to avoid a real pyNastranGUI hang (see
    _write_filtered_op2) only preserves stress tables, so a displacement
    fringe on a trimmed OP2 would silently find nothing to show. Render the
    full model for a displacement contour.

    camera: "iso"/"top"/"side"/"front" (see _CAMERA_PRESETS), or the default,
    "auto". Without isolate_groups/isolate_property_ids, "auto" looks up the
    governing (highest von Mises) plate element via get_max_stress, then
    views the model from whichever of the 8 canonical isometric octants
    (equal angle to all three world axes -- the standard engineering-
    drawing convention, chosen specifically because no single axis is
    degenerate the way a "top"/"side" preset can be) best faces that
    element's outward face normal, before fitting the whole model to the
    frame. This isn't a hard occlusion guarantee the way aiming exactly
    down the element's own normal would be (some other part of the model
    could in principle sit further out along that octant's fixed diagonal),
    but it keeps the governing element close to face-on and avoids the
    foreshortened-edge-on failure mode a fixed preset can leave it in, while
    being a simpler, more standard view than a continuously-computed exact
    direction. WITH isolate_groups/isolate_property_ids, "auto" aims for the
    isolated elements' shared face normal instead (see
    _camera_look_direction_for_isolated_group) -- isolating already removes
    any occlusion concern, so showing the isolated sub-component itself well
    (e.g. fanning out parallel ribs instead of collapsing them edge-on)
    takes priority over aiming at the model's single worst element, which
    might not even be part of what's isolated. Falls back to "iso" if
    there's no plate element to aim at either way (e.g. a bar-only model/
    group where a CBAR governs).

    Caveat verified during development: pyNastranGUI's own "Stress vonMises"
    fringe/legend is a GUI-internal combined scalar across element types --
    when CBAR elements are visible in frame, the legend's reported max can
    come from a bar element (some bar-stress equivalent the GUI computes
    internally), not necessarily a plate element's true von Mises. Confirmed
    by checking the element ID the legend reported as "Max" against the BDF
    directly. This is NOT the same value/quantity as get_max_stress's
    "von_mises" (plates only) or "max_stress" (bars, raw direct stress) --
    don't assume the on-screen legend matches get_max_stress's output when
    bar elements share the frame with plates.

    That GUI-synthesized bar-stress-derived pseudo-vonMises case is also why
    the fringe is only attempted at all when get_max_stress reports a real
    plate von_mises result -- for an all-CBAR selection (e.g. isolating a
    "Stiffeners" group that's 100% CBAR), applying that synthesized case
    turned out to be dramatically slow (a 14,134-element all-CBAR isolate
    hung past 180s with it, vs. ~15s once skipped). CBARs never had a
    meaningful von Mises value anyway, so skipping it costs nothing;
    fringe_set comes back False in that case rather than trying and hanging.

    The corner orientation-axes triad is always hidden, and the legend is
    shrunk to a small fixed font size rather than left at pyNastranGUI's
    default, which auto-scales legend text to fill its bounding box (on an
    11-label bar, 5-digit stress values came out enormous -- confirmed
    against the first published renders).

    isolate_groups/isolate_property_ids work here too, for a stress contour
    on just one sub-component (ribs, spars, skin panels, ...) -- pairing a
    filtered-down BDF with the full-model OP2 used to hang pyNastranGUI
    (observed: >240s / didn't complete for a ~6,200-of-35,489 isolated
    subset, vs. ~90s for the full model), so the OP2 is now trimmed to the
    same kept element set first (see _write_filtered_op2), which brought
    that same case down to ~12s. Only the stress results needed for the
    fringe are kept in the trimmed OP2 -- other result categories
    (displacements, spc_forces, ...) are dropped rather than reconciling
    their own node/element counts against the isolated subset, since
    render_stress_contour doesn't use them anyway.
    """
    return _render(
        bdf_path=bdf_path,
        op2_path=op2_path,
        output_png=output_png,
        hide_groups=hide_groups,
        hide_property_ids=hide_property_ids,
        isolate_groups=isolate_groups,
        isolate_property_ids=isolate_property_ids,
        ses_path=ses_path,
        camera=camera,
        zoom=zoom,
        timeout=timeout,
        result=result,
    )


if __name__ == "__main__":
    mcp.run()
