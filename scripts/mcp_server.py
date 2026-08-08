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
    """
    import numpy as np

    headers = arr.get_headers()
    elem_ids = _element_ids_for(arr)

    if "von_mises" in headers:
        quantity = "von_mises"
        values = arr.data[:, :, headers.index("von_mises")]
    else:
        quantity = "max_stress"
        stress_cols = [i for i, h in enumerate(headers) if not h.startswith("MS")]
        values = np.max(np.abs(arr.data[:, :, stress_cols]), axis=2)

    itime, ientry = np.unravel_index(np.argmax(values), values.shape)
    return {
        quantity: float(values[itime, ientry]),
        "element_id": int(elem_ids[ientry]),
    }


@mcp.tool()
def get_max_stress(op2_path: str) -> dict[str, Any]:
    """Parse an OP2 and return the peak stress for each element type present,
    across all subcases:

        {"cquad4": {"von_mises": ..., "element_id": ..., "subcase": ...},
         "cbar": {"max_stress": ..., "element_id": ..., "subcase": ...},
         ...}

    Element types not present in the OP2 are omitted. Plate-type elements
    (CQUAD4, CTRIA3, ...) report "von_mises"; bar-type elements (CBAR, ...)
    report "max_stress" (peak direct axial+bending stress magnitude) -- these
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
# render_model_view / render_stress_contour
# ---------------------------------------------------------------------------

# Deliberately minimal (named presets, not full 6-DOF control) per issue #9's
# scope. (azimuth, elevation) in degrees, applied after a camera reset.
_CAMERA_PRESETS = {
    "iso": (45.0, 20.0),
    "top": (0.0, 89.0),
    "side": (90.0, 0.0),
}

_DEFAULT_RENDER_TIMEOUT_S = 300


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
        keep: set[int] = set()
        if isolate_groups:
            keep.update(_eids_for_groups(isolate_groups, ses_path))
        if isolate_property_ids:
            keep.update(_eids_for_property_ids(bdf_path, isolate_property_ids))

        from pyNastran.bdf.bdf import BDF

        model = BDF()
        model.read_bdf(str(bdf_path), xref=False)
        return set(model.elements.keys()) - keep

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


def _build_postscript(
    output_png: Path, camera: str, zoom: float, want_stress_fringe: bool
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

    zoom (>1 zooms in) is applied after the camera preset via self.zoom() --
    plain camera.Reset() alone leaves substantial empty margin around the
    model rather than filling the frame; see render_model_view's docstring
    for when to increase this (isolate_* callers get a non-1.0 default
    automatically).

    want_stress_fringe searches the loaded result cases for one whose name
    matches "vonmises" case/whitespace/underscore-insensitively (pyNastran
    labels it 'vonMises', confirmed by inspecting self.result_cases during
    the spike) and reports whether it found one via a small sidecar file
    (<output_png>.fringe_set, "1" or "0") next to the screenshot, since the
    postscript runs in a separate subprocess with no other way to report
    back to the caller. Falls back to whatever pyNastranGUI displays by
    default (e.g. for a bar-only model with no plate von Mises result) if
    no matching case is found.
    """
    azimuth, elevation = _CAMERA_PRESETS[camera]
    output_png_repr = repr(str(output_png))
    fringe_flag_repr = repr(str(output_png) + ".fringe_set")

    fringe_block = ""
    if want_stress_fringe:
        fringe_block = f"""
_fringe_set = False
for _key, _val in self.result_cases.items():
    try:
        _obj, (_i, _resname) = _val
    except Exception:
        continue
    if "vonmises" in _resname.lower().replace(" ", "").replace("_", ""):
        self.on_fringe(_key)
        _fringe_set = True
        break
with open({fringe_flag_repr}, "w") as _f:
    _f.write("1" if _fringe_set else "0")
"""

    return f"""\
self.on_reset_camera()
_camera = self.rend.GetActiveCamera()
_camera.Azimuth({azimuth})
_camera.Elevation({elevation})
self.rend.ResetCameraClippingRange()
self.zoom({zoom})
{fringe_block}
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
) -> dict[str, Any]:
    in_path = Path(bdf_path).resolve()
    if not in_path.is_file():
        raise FileNotFoundError(f"BDF file not found: {in_path}")

    resolved_op2_path = None
    if op2_path is not None:
        resolved_op2_path = Path(op2_path).resolve()
        if not resolved_op2_path.is_file():
            raise FileNotFoundError(f"OP2 file not found: {resolved_op2_path}")

    if camera not in _CAMERA_PRESETS:
        raise ValueError(
            f"Unknown camera preset {camera!r}; choose from {sorted(_CAMERA_PRESETS)}"
        )

    out_png = Path(output_png).resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    render_timeout = timeout if timeout is not None else _DEFAULT_RENDER_TIMEOUT_S
    is_isolating = bool(isolate_groups or isolate_property_ids)
    # Isolating typically leaves a small subset of a much larger scene --
    # fit tighter by default so it actually fills the frame (see
    # render_model_view's docstring). Plain hide/no-filter calls keep the
    # un-zoomed default since the remaining geometry is usually still most
    # of the original scene.
    resolved_zoom = zoom if zoom is not None else (1.8 if is_isolating else 1.0)

    hidden_eids = _resolve_hidden_eids(
        in_path, hide_groups, hide_property_ids, isolate_groups, isolate_property_ids, ses_path
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        render_bdf_path = in_path
        if hidden_eids:
            render_bdf_path = tmpdir_path / "filtered.bdf"
            _write_filtered_bdf(in_path, hidden_eids, render_bdf_path)

        postscript_path = tmpdir_path / "postscript.py"
        postscript_path.write_text(
            _build_postscript(
                out_png, camera, resolved_zoom, want_stress_fringe=resolved_op2_path is not None
            )
        )
        fringe_flag_path = Path(str(out_png) + ".fringe_set")
        fringe_flag_path.unlink(missing_ok=True)

        try:
            proc = _run_pynastrangui(render_bdf_path, resolved_op2_path, postscript_path, render_timeout)
        except subprocess.TimeoutExpired as exc:
            hint = ""
            if is_isolating and resolved_op2_path is not None:
                hint = (
                    " (known slow/hanging path -- see issue #9: loading full-"
                    "model results against a heavily-isolated/reduced geometry "
                    "subset can take excessively long; consider render_model_view "
                    "without op2_path for isolate_* views instead)"
                )
            raise RuntimeError(
                f"pyNastranGUI did not finish within {render_timeout}s{hint}"
            ) from exc

    fringe_set = None
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

    camera: one of "iso", "top", "side" (see _CAMERA_PRESETS) -- a generic
    preset chosen for the whole original model may not suit an isolated
    subset's actual shape well (e.g. isolating thin, mostly-planar groups
    like ribs can render them near edge-on from "iso"); there's no
    automatic best-angle selection here, just the same three presets
    applied to whatever's left after filtering.
    zoom: >1 zooms in after the camera preset is applied (plain camera
    reset alone leaves significant empty margin around the model). Default
    is 1.0 (no zoom) normally, but 1.8 automatically when isolate_* is used
    -- an isolated subset is typically small relative to the original
    scene, so the tighter default actually fills the frame instead of
    leaving it mostly empty (verified by eye during development: 1.8 filled
    the frame well for a ~6,200-of-35,489-element isolated group without
    cropping it). Override explicitly if 1.8 crops or under-fills your case.

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
    camera: str = "iso",
    zoom: float | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Same as render_model_view, but also loads op2_path and colors the
    view by von Mises stress. The result dict includes "fringe_set": True
    if a von Mises result case was found and applied, False if the OP2 has
    no such case (e.g. a bar-only model -- see get_max_stress's "max_stress"
    vs "von_mises" distinction) and pyNastranGUI's default coloring was
    left in place instead.

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

    Known limitation: combining isolate_groups/isolate_property_ids with
    op2_path can hang or take excessively long when the isolated subset is
    a small fraction of a much larger model -- loading full-model results
    against heavily-reduced geometry appears to hit a slow path in
    pyNastran (observed: >180s / didn't complete for a ~6,200-of-35,489
    isolated subset, vs. ~90s for the full model). If you hit this, use
    render_model_view (no results) for isolated views instead until this is
    resolved -- see issue #9.
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
    )


if __name__ == "__main__":
    mcp.run()
