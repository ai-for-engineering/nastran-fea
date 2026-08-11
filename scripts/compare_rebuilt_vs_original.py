"""
Compare the rebuilt-from-geometry NASA CRM wingbox (#42-#45) against the
original solved model -- issue #46 of the rebuild-and-compare epic (#47).

A node-by-node comparison isn't meaningful: the two models have entirely
different GRID IDs and node counts (different mesh, independently
generated). Comparison happens at the physical level instead: tip
displacement, peak stress by structural component, and mesh density.

STRINGERS is excluded from the stress comparison, not silently cleaned up:
it's the least reliable component in this rebuild by construction (an
all-triangle mesh from the quad-recombination fallback, a back-calculated
-- not measured -- thickness, and the sole owner of every one of the
21 residual poorly-connected nodes from #45), and its peak stress remains
in the millions of psi even after excluding elements that directly touch
a high-displacement node -- the contamination runs deeper than a
localized artifact that can be surgically filtered out. Reporting a
"cleaned" STRINGERS number anyway would imply more confidence in it than
is honest.

Run: ./venv/Scripts/python.exe scripts/compare_rebuilt_vs_original.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
REBUILT_BDF = (
    REPO_ROOT / "case_studies" / "nasa_crm_wingbox" / "derived" / "rebuilt_from_geometry_static.bdf"
)
REBUILT_OP2 = (
    REPO_ROOT / "case_studies" / "nasa_crm_wingbox" / "derived" / "rebuilt_from_geometry_static.OP2"
)

# The original model's own real, already-solved results -- see #40's blog
# Model description chapter and "Peak stress by component" table. Not
# re-derived here (that OP2 is a fixed, checked-in reference result from
# earlier work); hardcoded so this comparison doesn't silently drift if
# that file is ever regenerated differently.
ORIGINAL_TIP_DISPLACEMENT_IN = 159.7
ORIGINAL_PEAK_STRESS_PSI = {
    "RIBS": 17884.0,
    "SPARS": 34046.9,
    "SKINS": 39983.7,  # lower skin, the model-wide CQUAD4 peak
    "STIFFENERS": 32980.1,  # CBAR axial -- not comparable to a shell von Mises number
}

# A node displacement magnitude beyond this is unambiguously unphysical
# for this model (tip is ~93 in in the rebuild, ~160 in in the original)
# -- used only to identify which STRINGERS elements are contaminated for
# reporting, not to "clean" a number that's reported anyway.
UNPHYSICAL_DISPLACEMENT_THRESHOLD_IN = 500.0


def percent_difference(rebuilt: float, original: float) -> float:
    """(rebuilt - original) / original, as a percentage. Positive means
    the rebuild reads higher than the original."""
    if original == 0:
        raise ValueError("original must be non-zero")
    return (rebuilt - original) / original * 100.0


def check_reaction_balance(
    applied_resultant: float, reaction_resultant: float, tolerance_fraction: float = 0.01
) -> bool:
    """True if the sum of SPC reaction forces balances the applied load
    resultant to within tolerance_fraction (both signed the same way, so
    a correctly-reacted model has reaction ≈ -applied or reaction ≈
    applied depending on sign convention -- pass whichever the caller's
    data already uses consistently). Global equilibrium (sum of forces
    over the whole structure = 0) is a fundamental property of any valid
    linear static solution, so a large imbalance is a real red flag, not
    a modeling nuance."""
    if applied_resultant == 0:
        raise ValueError("applied_resultant must be non-zero")
    return abs(reaction_resultant - applied_resultant) <= tolerance_fraction * abs(
        applied_resultant
    )


def summarize_rebuilt_model(
    bdf_path: Path, op2_path: Path, unphysical_threshold: float = UNPHYSICAL_DISPLACEMENT_THRESHOLD_IN
) -> dict[str, Any]:
    """Real, from-the-solved-files summary of the rebuilt model: node/
    element counts, tip displacement, and per-component peak CQUAD4/
    CTRIA3 von Mises stress -- with elements touching an unphysically-
    displaced node excluded from the stress search and counted
    separately (not silently dropped)."""
    import numpy as np
    from pyNastran.bdf.bdf import BDF
    from pyNastran.op2.op2 import OP2

    bdf = BDF()
    bdf.read_bdf(str(bdf_path), xref=True)
    op2 = OP2()
    op2.read_op2(str(op2_path))

    disp = op2.displacements[1]
    node_ids = disp.node_gridtype[:, 0]
    mags = np.linalg.norm(disp.data[0, :, :3], axis=1)
    node_to_mag = dict(zip(node_ids.tolist(), mags.tolist()))

    tip_nid = max(bdf.nodes, key=lambda nid: bdf.nodes[nid].get_position()[1])
    tip_displacement = node_to_mag.get(tip_nid)

    suspect_eids = {
        eid
        for eid, elem in bdf.elements.items()
        if any(
            node_to_mag.get(nid, 0.0) > unphysical_threshold for nid in elem.node_ids
        )
    }

    pid_names = {1: "RIBS", 2: "SPARS", 3: "SKINS", 4: "RIB_CAPS", 5: "STRINGERS"}
    peak_stress: dict[str, dict[str, Any]] = {}

    cquad4_stress = op2.op2_results.stress.cquad4_stress.get(1)
    ctria3_stress = op2.op2_results.stress.ctria3_stress.get(1)

    for pid_val, name in pid_names.items():
        for stress_table, etype in ((cquad4_stress, "CQUAD4"), (ctria3_stress, "CTRIA3")):
            if stress_table is None:
                continue
            eids = stress_table.element_node[:, 0]
            vm = stress_table.data[0, :, 7]
            pid_eids = {
                eid
                for eid, elem in bdf.elements.items()
                if elem.pid == pid_val and elem.type == etype
            }
            if not pid_eids:
                continue
            clean_mask = np.isin(eids, list(pid_eids)) & ~np.isin(eids, list(suspect_eids))
            if clean_mask.sum() == 0:
                continue
            sub_eids, sub_vm = eids[clean_mask], vm[clean_mask]
            idx = int(np.argmax(sub_vm))
            entry = peak_stress.setdefault(name, {})
            entry[etype] = {
                "von_mises_psi": float(sub_vm[idx]),
                "element_id": int(sub_eids[idx]),
                # STRINGERS specifically: even after excluding every
                # element that directly touches an unphysically-displaced
                # node, its own reported peak here is STILL in the
                # millions of psi (confirmed directly, not assumed) --
                # the contamination isn't confined to the 26 flagged
                # elements, it runs through the component more broadly.
                # Flagged explicitly rather than silently reported as a
                # normal number.
                "reliable": name != "STRINGERS",
            }

    counts_by_component: dict[str, int] = {}
    for elem in bdf.elements.values():
        counts_by_component[pid_names.get(elem.pid, str(elem.pid))] = (
            counts_by_component.get(pid_names.get(elem.pid, str(elem.pid)), 0) + 1
        )

    return {
        "n_nodes": len(bdf.nodes),
        "n_elements": len(bdf.elements),
        "counts_by_component": counts_by_component,
        "tip_node_id": int(tip_nid),
        "tip_displacement_in": tip_displacement,
        "peak_stress_by_component": peak_stress,
        "n_elements_excluded_as_unphysical": len(suspect_eids),
        "components_touched_by_exclusion": sorted(
            {pid_names.get(bdf.elements[eid].pid, "?") for eid in suspect_eids}
        ),
    }


def main() -> None:
    summary = summarize_rebuilt_model(REBUILT_BDF, REBUILT_OP2)

    print("=" * 78)
    print("REBUILT MODEL SUMMARY")
    print("=" * 78)
    print(json.dumps(summary, indent=2))

    print()
    print("=" * 78)
    print("COMPARISON: rebuilt vs. original")
    print("=" * 78)
    tip_diff = percent_difference(summary["tip_displacement_in"], ORIGINAL_TIP_DISPLACEMENT_IN)
    print(
        f"Tip displacement: {summary['tip_displacement_in']:.1f} in (rebuilt) vs. "
        f"{ORIGINAL_TIP_DISPLACEMENT_IN:.1f} in (original) -- {tip_diff:+.1f}%"
    )
    for name in ("RIBS", "SPARS", "SKINS"):
        rebuilt_entry = summary["peak_stress_by_component"].get(name, {}).get("CQUAD4")
        original_val = ORIGINAL_PEAK_STRESS_PSI.get(name)
        if rebuilt_entry and original_val:
            diff = percent_difference(rebuilt_entry["von_mises_psi"], original_val)
            print(
                f"{name} peak CQUAD4 von Mises: {rebuilt_entry['von_mises_psi']:.1f} psi "
                f"(rebuilt) vs. {original_val:.1f} psi (original) -- {diff:+.1f}%"
            )
    print(
        "STRINGERS: excluded from comparison -- see module docstring "
        f"({summary['n_elements_excluded_as_unphysical']} elements excluded as unphysical, "
        f"all in {summary['components_touched_by_exclusion']})"
    )


if __name__ == "__main__":
    main()
