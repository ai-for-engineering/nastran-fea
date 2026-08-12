"""
One-off exploratory script (not production pipeline code -- see spikes/
docstring convention in the main README) to pull "model description" data
for the blog: unit system, global dimensions (span/root chord/tip chord),
material + per-group averaged thickness, and applied load/BC summary, for
each of the three case studies.

Run: ./venv/Scripts/python.exe spikes/model_description_extract.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ses_groups import parse_ses_groups  # noqa: E402

from pyNastran.bdf.bdf import BDF  # noqa: E402


def global_dimensions(bdf: BDF) -> dict:
    # Restrict to nodes actually used by CQUAD4/CTRIA3/CBAR/CBEAM -- excludes
    # floating RBE2/RBE3 independent (reference/interpolation) nodes, which
    # can sit off the real structural surface and distort a chord/span probe.
    surface_nids: set[int] = set()
    for elem in bdf.elements.values():
        if elem.type in ("CQUAD4", "CTRIA3", "CBAR", "CBEAM"):
            surface_nids.update(elem.node_ids)

    all_nids = list(bdf.nodes.keys())
    xyz_all = np.array([bdf.nodes[n].get_position() for n in all_nids])
    keep = [i for i, n in enumerate(all_nids) if n in surface_nids] or list(range(len(all_nids)))
    xyz = xyz_all[keep]

    ranges = xyz.max(axis=0) - xyz.min(axis=0)
    span_axis = int(np.argmax(ranges))
    other_axes = [a for a in range(3) if a != span_axis]
    # chord = largest of the two remaining axes; thickness = smallest
    chord_axis = other_axes[0] if ranges[other_axes[0]] >= ranges[other_axes[1]] else other_axes[1]
    thick_axis = other_axes[1] if chord_axis == other_axes[0] else other_axes[0]

    span_vals = xyz[:, span_axis]
    span_min, span_max = span_vals.min(), span_vals.max()
    span = span_max - span_min

    def chord_at(frac: float) -> float:
        target = span_min + frac * span
        for band in (0.01, 0.02, 0.04, 0.08, 0.15):
            mask = np.abs(span_vals - target) <= band * span
            if mask.sum() >= 4:
                return float(xyz[mask, chord_axis].max() - xyz[mask, chord_axis].min())
        mask = np.abs(span_vals - target) <= 0.15 * span
        return float(xyz[mask, chord_axis].max() - xyz[mask, chord_axis].min()) if mask.any() else float("nan")

    return {
        "span_axis": "XYZ"[span_axis],
        "chord_axis": "XYZ"[chord_axis],
        "thickness_axis": "XYZ"[thick_axis],
        "span": float(span),
        "root_chord": chord_at(0.0),
        "tip_chord": chord_at(1.0),
        "bbox_ranges": {"X": float(ranges[0]), "Y": float(ranges[1]), "Z": float(ranges[2])},
    }


def element_thickness_map(bdf: BDF) -> dict[int, float]:
    """element_id -> PSHELL/PCOMP thickness (None if not a plate/no thickness)."""
    result = {}
    for eid, elem in bdf.elements.items():
        etype = elem.type
        if etype not in ("CQUAD4", "CTRIA3"):
            continue
        pid = elem.pid_ref if elem.pid_ref is not None else bdf.properties.get(elem.pid)
        if pid is None:
            continue
        if pid.type == "PSHELL":
            result[eid] = pid.t
        elif pid.type == "PCOMP":
            result[eid] = pid.Thickness()
    return result


def element_bar_area_map(bdf: BDF) -> dict[int, float]:
    result = {}
    for eid, elem in bdf.elements.items():
        if elem.type not in ("CBAR", "CBEAM"):
            continue
        pid = elem.pid_ref if elem.pid_ref is not None else bdf.properties.get(elem.pid)
        if pid is None:
            continue
        try:
            result[eid] = pid.Area()
        except Exception:
            pass
    return result


def summarize_groups(bdf: BDF, groups: dict[str, set[int]]) -> None:
    thick = element_thickness_map(bdf)
    area = element_bar_area_map(bdf)
    for name, eids in sorted(groups.items()):
        eids = {e for e in eids if e in bdf.elements}
        if not eids:
            continue
        types = {}
        for e in eids:
            types[bdf.elements[e].type] = types.get(bdf.elements[e].type, 0) + 1
        t_vals = [thick[e] for e in eids if e in thick]
        a_vals = [area[e] for e in eids if e in area]
        mats = set()
        for e in eids:
            elem = bdf.elements[e]
            pid = elem.pid_ref if elem.pid_ref is not None else bdf.properties.get(elem.pid)
            if pid is None:
                continue
            mid = getattr(pid, "mid_ref", None) or getattr(pid, "Mid", lambda: None)()
            if mid is not None:
                mats.add(mid if isinstance(mid, int) else mid.mid)
        line = f"  {name:16s} n={len(eids):6d} types={types} materials={mats}"
        if t_vals:
            line += f" avg_t={np.mean(t_vals):.4f} (min={min(t_vals):.4f} max={max(t_vals):.4f})"
        if a_vals:
            line += f" avg_area={np.mean(a_vals):.4f}"
        print(line)


def dump_materials(bdf: BDF) -> None:
    for mid, m in bdf.materials.items():
        if m.type == "MAT1":
            print(f"  MAT1 {mid}: E={m.e:.6g} G={m.g:.6g} nu={m.nu} rho={m.rho:.6g}")
        elif m.type == "MAT8":
            print(
                f"  MAT8 {mid}: E11={m.e11:.6g} E22={m.e22:.6g} G12={m.g12:.6g} "
                f"nu12={m.nu12} rho={m.rho:.6g}"
            )
        else:
            print(f"  {m.type} {mid}: {m}")


def dump_params(bdf: BDF) -> None:
    for key in ("WTMASS", "GRDPNT"):
        if key in bdf.params:
            print(f"  PARAM {key} = {bdf.params[key].values}")


print("=" * 80)
print("NASA CRM WINGBOX")
print("=" * 80)
b1 = BDF()
b1.read_bdf(
    "case_studies/nasa_crm_wingbox/derived/CRM_V15_wingbox_1_static.dat", xref=True
)
print(global_dimensions(b1))
dump_materials(b1)
dump_params(b1)
groups1 = parse_ses_groups(
    "case_studies/nasa_crm_wingbox/original/V15wingbox/CRM_V15wingbox_1_noHM/V15_groups.ses"
)
summarize_groups(b1, groups1)

print()
print("=" * 80)
print("pCRM9")
print("=" * 80)
b2 = BDF()
b2.read_bdf("case_studies/pcrm9_wingbox/original/pCRM9_103_MAIN_FILE.bdf", xref=True)
print(global_dimensions(b2))
dump_materials(b2)
dump_params(b2)
print("PID -> type/thickness-or-area:")
for pid, p in b2.properties.items():
    if p.type == "PSHELL":
        print(f"  PID {pid}: PSHELL t={p.t}")
    elif p.type in ("PBAR", "PBEAM", "PBEAML"):
        try:
            print(f"  PID {pid}: {p.type} area={p.Area()}")
        except Exception as ex:
            print(f"  PID {pid}: {p.type} area=? ({ex})")

print()
print("=" * 80)
print("DLR ISTAR WING")
print("=" * 80)
b3 = BDF()
b3.read_bdf("case_studies/istar_wing/original/ISTAR_Demo_Wing.bdf", xref=True)
print(global_dimensions(b3))
dump_materials(b3)
dump_params(b3)
print(f"n PCOMP properties: {len(b3.properties)}")
thick3 = element_thickness_map(b3)
tv = list(thick3.values())
print(f"shell thickness across all {len(tv)} elements: mean={np.mean(tv):.5f} min={min(tv):.5f} max={max(tv):.5f}")
