"""One-off exploratory script (see spikes/ docstring convention) to pull
real planform parameters for the NASA CRM wingbox from the ORIGINAL solved
deck -- span, root/tip chord, sweep, real rib spanwise stations, real spar
chordwise fractions, box-depth taper -- so a parametric rebuild
(spikes/parametric_crm_wingbox.py) is grounded in this model's own actual
dimensions, not guessed or copied from the public CRM wing's published
aerodynamic parameters (which describe the full OML, not necessarily this
specific wingbox idealization).

Run: ./venv/Scripts/python.exe spikes/extract_crm_planform.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ses_groups import parse_ses_groups  # noqa: E402

from pyNastran.bdf.bdf import BDF  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
BDF_PATH = REPO_ROOT / "case_studies" / "nasa_crm_wingbox" / "derived" / "CRM_V15_wingbox_1_static.dat"
SES_PATH = (
    REPO_ROOT / "case_studies" / "nasa_crm_wingbox" / "original" / "V15wingbox"
    / "CRM_V15wingbox_1_noHM" / "V15_groups.ses"
)


def main() -> None:
    bdf = BDF(debug=False)
    bdf.read_bdf(str(BDF_PATH), xref=True)
    groups = parse_ses_groups(str(SES_PATH))

    def xyz_of(nids: set[int]) -> np.ndarray:
        return np.array([bdf.nodes[n].get_position() for n in nids if n in bdf.nodes])

    # Confirm axis convention directly rather than assuming -- span should
    # be the by-far largest bounding-box dimension.
    all_nids = set(bdf.nodes.keys())
    xyz_all = xyz_of(all_nids)
    ranges = xyz_all.max(axis=0) - xyz_all.min(axis=0)
    span_axis = int(np.argmax(ranges))
    print(f"bbox ranges (X,Y,Z): {ranges}, span axis = {'XYZ'[span_axis]}")
    y = xyz_all[:, span_axis]
    y_min, y_max = float(y.min()), float(y.max())
    span = y_max - y_min
    print(f"span ({'XYZ'[span_axis]}): {y_min:.2f} to {y_max:.2f} = {span:.2f} in")

    chord_axis = 0 if span_axis != 0 else 1
    thick_axis = 3 - span_axis - chord_axis
    print(f"chord axis = {'XYZ'[chord_axis]}, thickness axis = {'XYZ'[thick_axis]}")

    print(f"\ngroups found: { {k: len(v) for k, v in groups.items()} }")

    # --- Ribs: real spanwise stations, not assumed evenly spaced ---
    ribs_xyz = xyz_of(groups.get("RIBS", set()))
    rib_y = ribs_xyz[:, span_axis]
    # Cluster into stations: sort unique-ish Y values with a tolerance.
    order = np.argsort(rib_y)
    sorted_y = rib_y[order]
    stations = []
    cluster = [sorted_y[0]]
    for v in sorted_y[1:]:
        if v - cluster[-1] > 1.0:  # > 1 inch gap -> new station
            stations.append(np.mean(cluster))
            cluster = [v]
        else:
            cluster.append(v)
    stations.append(np.mean(cluster))
    print(f"\nRibs: {len(stations)} real spanwise stations (in):")
    print([round(float(s), 1) for s in stations])
    if len(stations) > 1:
        spacings = np.diff(stations)
        print(f"  spacing: min={spacings.min():.1f} max={spacings.max():.1f} mean={spacings.mean():.1f}")

    # --- Chord at root/tip (and a few intermediate stations) from Skin group ---
    skin_xyz = xyz_of(groups.get("Skin_LWR", set()) | groups.get("Skin_UPR", set()))
    skin_y = skin_xyz[:, span_axis]
    skin_x = skin_xyz[:, chord_axis]

    def chord_and_le_at(y_target: float, band_frac: float = 0.03) -> tuple[float, float]:
        band = band_frac * span
        mask = np.abs(skin_y - y_target) <= band
        if mask.sum() < 4:
            mask = np.abs(skin_y - y_target) <= band * 3
        xs = skin_x[mask]
        return float(xs.max() - xs.min()), float(xs.min())  # chord, leading-edge X

    print("\nChord + leading-edge X at stations:")
    probe_fracs = [0.0, 0.25, 0.5, 0.75, 1.0]
    probe_results = []
    for f in probe_fracs:
        y_t = y_min + f * span
        c, le = chord_and_le_at(y_t)
        probe_results.append((f, y_t, c, le))
        print(f"  frac={f:.2f} Y={y_t:8.2f}  chord={c:7.2f}  LE_X={le:7.2f}")

    # Sweep from leading-edge X shift between root and tip probes.
    (f0, y0, c0, le0), (f1, y1, c1, le1) = probe_results[0], probe_results[-1]
    sweep_deg = np.degrees(np.arctan2(le1 - le0, y1 - y0))
    print(f"\nLeading-edge sweep (root to tip probe): {sweep_deg:.2f} deg")
    print(f"Root chord ~ {c0:.2f} in, tip chord ~ {c1:.2f} in, taper ratio ~ {c1/c0:.3f}")

    # --- Spars: real chordwise position as fraction of local chord ---
    # Group is named "Spars_LETE" in this model's own .ses -- empirically
    # check how many distinct chordwise clusters it actually resolves to
    # per station rather than assuming a specific spar count from the name.
    spars_xyz = xyz_of(groups.get("Spars_LETE", set()))
    spars_y = spars_xyz[:, span_axis]
    spars_x = spars_xyz[:, chord_axis]
    print("\nSpar chordwise fractions at a few spanwise stations:")
    for f in [0.1, 0.5, 0.9]:
        y_t = y_min + f * span
        band = 0.03 * span
        mask = np.abs(spars_y - y_t) <= band
        if mask.sum() < 3:
            mask = np.abs(spars_y - y_t) <= band * 3
        xs = np.sort(np.unique(np.round(spars_x[mask], 1)))
        c, le = chord_and_le_at(y_t)
        # Cluster spar X positions at this station into distinct spars.
        clusters = []
        cur = [xs[0]] if len(xs) else []
        for v in xs[1:]:
            if v - cur[-1] > 0.05 * c:
                clusters.append(np.mean(cur))
                cur = [v]
            else:
                cur.append(v)
        if cur:
            clusters.append(np.mean(cur))
        fracs = [(cl - le) / c for cl in clusters]
        print(f"  frac={f:.2f} Y={y_t:8.2f}  spar X positions -> chord fractions: "
              f"{[round(fr, 3) for fr in fracs]}")

    # --- ShearWebs: CLAUDE.md notes this is the dense internal comb with
    # no IGES counterpart -- check its own chordwise clustering too, to see
    # whether it's actually where the 3rd (mid) spar lives, or something
    # else entirely (rib-spaced local ties, per its own name).
    shearwebs_xyz = xyz_of(groups.get("ShearWebs", set()))
    if len(shearwebs_xyz):
        sw_y = shearwebs_xyz[:, span_axis]
        sw_x = shearwebs_xyz[:, chord_axis]
        print("\nShearWebs chordwise clustering at a few stations (for comparison):")
        for f in [0.1, 0.5, 0.9]:
            y_t = y_min + f * span
            band = 0.03 * span
            mask = np.abs(sw_y - y_t) <= band
            xs = np.sort(np.unique(np.round(sw_x[mask], 1)))
            if len(xs) == 0:
                print(f"  frac={f:.2f} Y={y_t:8.2f}  (no nearby ShearWebs nodes)")
                continue
            clusters = []
            cur = [xs[0]]
            for v in xs[1:]:
                if v - cur[-1] > 5.0:
                    clusters.append(np.mean(cur))
                    cur = [v]
                else:
                    cur.append(v)
            clusters.append(np.mean(cur))
            c, le = chord_and_le_at(y_t)
            fracs = [(cl - le) / c for cl in clusters]
            print(f"  frac={f:.2f} Y={y_t:8.2f}  n_clusters={len(clusters)}  "
                  f"fracs={[round(fr, 3) for fr in fracs]}")

    # --- Box depth (thickness axis range) at root/tip, as fraction of local chord ---
    print("\nBox depth (thickness) at stations, as %chord:")
    struct_xyz = xyz_of(all_nids)
    struct_y = struct_xyz[:, span_axis]
    struct_z = struct_xyz[:, thick_axis]
    for f in probe_fracs:
        y_t = y_min + f * span
        band = 0.03 * span
        mask = np.abs(struct_y - y_t) <= band
        if mask.sum() < 4:
            mask = np.abs(struct_y - y_t) <= band * 3
        zs = struct_z[mask]
        depth = float(zs.max() - zs.min())
        c, _ = chord_and_le_at(y_t)
        print(f"  frac={f:.2f} Y={y_t:8.2f}  depth={depth:6.2f} in  ({100*depth/c:.1f}% chord)")

    # --- Dihedral: mid-depth Z shift with span ---
    print("\nMid-depth Z at root/tip probes (dihedral check):")
    for f in [0.0, 1.0]:
        y_t = y_min + f * span
        band = 0.03 * span
        mask = np.abs(struct_y - y_t) <= band
        zs = struct_z[mask]
        print(f"  frac={f:.2f} Y={y_t:8.2f}  Z mid={float(np.mean(zs)):7.2f}  "
              f"Z range=[{float(zs.min()):.2f}, {float(zs.max()):.2f}]")


if __name__ == "__main__":
    main()
