"""Parametric rebuild of the NASA CRM wingbox's own real planform -- not a
generic toy box (see parametric_wingbox_conformal_mesh.py for that
earlier, deliberately-minimal proof of concept). Every root/tip dimension
below was measured directly from the ORIGINAL solved deck
(`case_studies/nasa_crm_wingbox/derived/CRM_V15_wingbox_1_static.dat`) via
`spikes/extract_crm_planform.py` -- span, root/tip chord, leading-edge
sweep, dihedral, box-depth taper, front/rear spar chordwise fraction, and
all 57 real (non-uniformly-spaced -- denser near the root/crank region,
~20.9in outboard) rib stations. See that script's own output for the raw
numbers this module's constants are transcribed from.

The point, per issue #59/CLAUDE.md's Gotchas: the real NASA CRM wingbox's
5 downloaded IGES files have no shared topology encoded at all (each
component independently modeled/exported), which is *why* the from-IGES
rebuild (`assemble_wingbox_geometry.py`) can only approximately weld
components together, never achieve full conformal connectivity. THIS
script builds the wing's ribs/spars/skins as one topologically fragmented
assembly, directly in a single gmsh OpenCASCADE session (no export/
re-import round trip) -- so touching parts share real topology by
construction, at this wing's own real planform, not a simplified box.

--- Extension: multi-spar, spanwise kink, and skin camber ---

The first version of this script (see git history) proved the mechanism
at a deliberately bounded complexity: 2 spars (front/rear only), a single
root-to-tip LINEAR planform (no kink), and flat ruled-quad skins. This
version generalizes all three, closer to (not matching -- see "Scope"
below) the real wingbox's actual complexity:

1. N_SPARS spars (not just 2), at chordwise fractions interpolated
   linearly root-to-tip each (SPAR_CHORDWISE_FRACS) -- a genuine list, not
   a hardcoded pair.
2. PLANFORM_STATIONS is now a list of >=2 stations (root, an added kink,
   tip), each carrying its own chord/leading-edge-X/dihedral-Z/box-depth,
   interpolated PIECEWISE-LINEARLY between consecutive stations -- not a
   single root-to-tip lerp. The kink station (Y=224in) is itself a real
   measurement (see extract_crm_planform.py's probing method, extended to
   this Y), landing inside the real 120-324in crank region the original
   analysis already flagged via denser rib spacing there. It reveals a
   real sweep break: ~15.6 deg root-to-kink vs. ~36.2 deg kink-to-tip --
   not invented, a genuine feature of this wing's planform.
3. Skins carry a modest analytic camber bump (SKIN camber, not a real
   airfoil profile -- see CAMBER_TOP_FRAC/CAMBER_BOTTOM_FRAC), and every
   spar shares its own chordwise position (see _T_VALUES) with the
   camber's own chordwise sample grid -- see "Camber design" below for why
   that reuse is the key to keeping connectivity intact.

Every quantity defining a straight panel edge must be genuinely linear in
Y over the segment that edge spans (the ORIGINAL version's own hard-won
bug -- see the comment above PLANFORM_STATIONS' precompute loop). With a
kink, "the whole span" is no longer one such segment -- it's two
(root-to-kink, kink-to-tip) -- so spars are built as one ruled quad PER
SEGMENT (N_SPARS * 2 panels total), not one whole-span quad.

### Camber design -- why a first attempt at this made connectivity WORSE

A first version of this camber added purely-interior `pointTags` support
points to otherwise-unchanged, whole-span-segment flat skin panels
(bulging the surface's interior while leaving its boundary wire alone).
That measured a real 44.6% connectivity gap even for a 2-spar, no-kink
model -- confirmed via a controlled test collapsing this script's
constants back to the exact original 2-spar/no-kink/no-camber config
(reproduces the original's 0.0% / 62 surfaces / 5,156 boundary nodes
exactly, ruling out a refactor regression) and then re-enabling only one
axis at a time: kink alone and multi-spar alone were BOTH still 0.0%;
camber alone reproduced the 44.6% gap on its own.

Root cause, found the same empirical way as this project's other gotchas
(see CLAUDE.md): the ORIGINAL flat/ruled skin's connectivity to a RIB's
own straight front-to-rear top/bottom edge (and to an internal spar's
straight edge) relied on that whole straight EDGE being exactly EMBEDDED
in the flat skin surface's 2D interior -- true for a flat/ruled surface
(linear in the chordwise parameter), false the moment the surface curves.
Once camber bulges the skin, a rib's flat top/bottom edge no longer lies
on the now-curved surface anywhere except its two spar-line endpoints --
breaking connectivity across THAT RIB'S ENTIRE CHORD WIDTH, for every one
of the 58 ribs. Tightening addSurfaceFilling's own tol2d/tol3d/tolAng
(testing the alternate hypothesis that the filling algorithm was merely
failing to reproduce its OWN input boundary curve under strong interior
constraints) only moved the number from 44.6% to 41.8% -- confirming the
real mechanism is the embedded-line assumption breaking, not a filling
tolerance issue.

The fix applied below restores "shared topology by construction" for
camber specifically, rather than relying on gmsh's fragment to
numerically detect coincidence after the fact (which is what silently
broke above): every rib's own top/bottom boundary is built as an
`occ.addSpline` through N_SPARS cambered chordwise points (reusing
spar_x_at/spar_z_at -- the SAME functions that place every spar's own
corners, so a spar's corner point and the matching point on its
neighboring rib's spline are computed identically). Each SKIN panel is
then built per RIB-TO-RIB gap (57 gaps, not 2 kink segments), and its
boundary wire REUSES the exact same spline curve tags from its two
bounding ribs (via a negative tag for the reversed direction) -- not a
fresh, independently-built curve that merely happens to be geometrically
close. Reusing the identical OCC curve guarantees exact topological
sharing between a rib and its adjacent skin panels, the same "shared
topology by construction" principle this whole script already relies on
for spar/rib sharing.

Internal spar caps ALSO now follow the camber offset (previously they
stayed at the flat baseline) -- safe to do because each spar's chordwise
fraction t_i is a fixed constant (not itself a function of Y), so
CAMBER_*_FRAC * chord(y) * bump(t_i) is still exactly piecewise-linear in
Y (a constant times an already-piecewise-linear function), preserving the
straight-panel-edge invariant. This makes every spar's edge land exactly
on its neighboring ribs' cambered corner points too. The one remaining,
real, honestly-measured gap: an internal spar's edge is still a STRAIGHT
line between two ribs, while the skin panel between those same two ribs
is a `addSurfaceFilling` FIT (not guaranteed to reduce to that exact
straight line) -- each skin panel gets one interior `pointTags` point per
internal spar, at the panel's Y-midpoint, evaluated with that same
spar_x_at/spar_z_at pair, to pull the fit tight to the true line; the
residual after that is this run's real, reported connectivity number,
concentrated at internal-spar-to-skin panel interiors only.

A real run (N_SPARS=5, KINK_Y=224) lands at a 12.5% overall gap (961 of
7,672 boundary nodes) -- but that number by itself hides where it
actually is: every one of the 58 ribs is 0.0%, front/rear spars and both
skins are ~1.2-1.7%, and essentially the whole gap (37.7%-63.7% each) is
on the 3 internal spars, exactly where the paragraph above says to expect
it. The front/rear spars' own small residual (traced to specific node
coordinates, not assumed) turned out to have a second, distinct cause
worth naming honestly: it's clustered at Y=224-236in -- inside the one
rib-to-rib gap (RIB_STATIONS 223.0 to 239.4) that happens to straddle
KINK_Y=224 itself. SPAR panels are still built per PLANFORM SEGMENT (2
of them), so within that one gap the spar's true edge has a real
polyline bend at Y=224; the skin panel spanning that same gap is a single
smooth fit between its two bounding rib splines (at Y=223.0 and 239.4)
with no knowledge of the bend sitting inside it. A real, small,
geometrically-explained residual, not fixed here -- left as an honest
example of the same "every straight edge needs to be built at the
resolution its own bends actually happen at" lesson this module keeps
re-learning at finer and finer grain.

Scope, stated plainly (matching this project's own honesty convention),
what's STILL simplified after this extension:
- Ribs are still modeled as flat planes perpendicular to the span axis
  (Y) -- the real aircraft's ribs may be angled/streamwise in places (not
  verified either way here); a structural simplification, not a measured
  fact. (Their top/bottom edges are no longer straight lines -- they
  follow the camber profile -- but the rib itself is still a single
  constant-Y plane.)
- N_SPARS spars (5 by default: front, 3 internal, rear), evenly spaced in
  chordwise fraction between front and rear -- still far short of the
  original deck's own ~22 additional internal ShearWebs (connected-
  component analysis in extract_crm_planform.py), and evenly spaced
  rather than matching their real, uneven, root-concentrated distribution
  (measured: 15 distinct clusters at 10% span spanning ~35-90% chord,
  tapering to a single web by 90% span -- see that script's own probe
  output). A genuine multi-spar parameter now, not a byte-for-byte
  reproduction of the real web count/layout.
- Camber is an engineered parabolic bump (see _camber_bump), not a real
  measured airfoil profile -- demonstrates the geometric capability
  (curved, non-ruled skins built from reused chordwise cross-section
  curves), not an aerodynamically accurate outer mold line.
- No stringers/stiffeners.

Run: ./venv/Scripts/python.exe spikes/parametric_crm_wingbox.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# --- Real planform parameters, measured from the original solved deck ---
# (see spikes/extract_crm_planform.py's output; all lengths in inches,
# this model's own native unit, matching the original deck).
SPAN = 1151.32
ROOT_CHORD = 291.19
TIP_CHORD = 85.31
LE_ROOT_X = 1035.23  # leading-edge X at the root probe
LE_SWEEP_DEG = 32.35  # leading-edge sweep, root-to-tip probe (whole-span
                       # average -- kept for the tip station's derived
                       # LE_X below; superseded as a *modeling* input by
                       # the two real per-segment sweeps the kink reveals)

# Dihedral: real mid-depth Z shift from root to tip (measured, not assumed).
Z_MID_ROOT = 134.08
Z_MID_TIP = 262.88

# Box depth (thickness axis range) as a fraction of local chord -- real
# extracted values ranged 15.1-20.1% outboard of the root closeout region
# (the true root probe read 34.9%, an artifact of root closeout structure,
# not representative of the general spanwise trend -- excluded here in
# favor of the more representative 25%-span value as the "root" endpoint).
BOX_DEPTH_FRAC_ROOT = 0.18
BOX_DEPTH_FRAC_TIP = 0.20

# Front/rear spar chordwise position as a fraction of local chord, at root
# and tip (real extracted trend; root values were noisy right at the exact
# root-closeout probe, so representative endpoints are used).
FRONT_SPAR_FRAC_ROOT = 0.02
FRONT_SPAR_FRAC_TIP = 0.11
REAR_SPAR_FRAC_ROOT = 0.97
REAR_SPAR_FRAC_TIP = 0.92

# --- New: a real measured kink/crank station ---
# Probed the same way as the root/tip values above (see
# extract_crm_planform.py's chord_and_le_at), extended to Y=224in -- inside
# the real 120-324in crank region the original rib-spacing analysis already
# flagged (RIB_STATIONS below is visibly denser/irregular from 0-323.9in,
# settling to a consistent ~21-22in spacing outboard). This single extra
# station is enough to reveal a real sweep break: root-to-kink sweep works
# out to ~15.6 deg, kink-to-tip to ~36.2 deg -- a genuine feature of this
# wing (a less-swept inboard glove, more-swept outboard), not invented to
# make a point.
KINK_Y = 224.0
KINK_CHORD = 268.01
KINK_LE_X = 1097.886
KINK_Z_MID = 165.19
KINK_BOX_DEPTH_FRAC = 0.207

# --- New: multi-spar and camber parameters ---
# N_SPARS is the genuine tunable parameter (see SPAR_CHORDWISE_FRACS and
# _T_VALUES below) -- 5 gives front + 3 internal + rear, enough to make
# the multi-spar wingbox visually and structurally obvious without chasing
# the real ~22-web count (see module docstring's Scope section).
N_SPARS = 5

# Camber amplitude, as a fraction of LOCAL chord, at the box's own
# chordwise mid-point (peak of the parabolic bump -- see _camber_bump).
# Asymmetric (top > bottom), loosely evocative of a supercritical airfoil's
# flatter lower surface -- not fit to any real aerodynamic data.
CAMBER_TOP_FRAC = 0.025
CAMBER_BOTTOM_FRAC = 0.012

# All 57 real rib spanwise (Y) stations -- transcribed directly from
# extract_crm_planform.py's connected-component analysis of the original
# deck's own RIBS group, not evenly spaced (denser near the root/crank
# region 120-324in, ~20.9in spacing outboard).
RIB_STATIONS = [
    0.0, 24.2, 48.2, 72.3, 96.3, 120.3, 141.2, 156.5, 172.7, 189.6, 206.1,
    223.0, 239.4, 256.4, 273.0, 290.1, 306.8, 323.9, 346.5, 369.0, 391.5,
    413.9, 436.0, 457.7, 478.8, 499.7, 520.6, 541.5, 562.3, 583.2, 604.1,
    625.0, 645.9, 666.7, 687.6, 708.5, 729.4, 750.3, 771.1, 792.0, 812.9,
    833.8, 854.7, 875.5, 896.4, 917.3, 938.2, 959.1, 979.9, 1000.8, 1021.7,
    1042.6, 1063.5, 1084.3, 1105.2, 1126.1, 1146.5,
    SPAN,  # tip-closure rib: the real rib list's own last station (1146.5)
           # is ~4.8in short of the true tip (measured from the full
           # bounding box), leaving the spar/skin panels' tip edge
           # unclosed -- confirmed as the exact, sole cause of the last
           # 24 (of 5128) unwelded boundary nodes, all at Y=SPAN.
]

MESH_SIZE = 8.0
OUTPUT_BDF = REPO_ROOT / "spikes" / "output" / "parametric_crm_wingbox.bdf"


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _camber_bump(t: float) -> float:
    """Parabolic bump, 0 at t=0/1 (the front/rear-most chordwise
    fraction), peak 1 at t=0.5 (box mid-chord). Zero at the endpoints is
    the key property that keeps a cambered skin's own outer boundary
    (shared with the front/rear spar edges) identical to the
    flat-baseline case."""
    return 4.0 * t * (1.0 - t)


# Every quantity that defines the STRAIGHT EDGE of a spar panel (built
# from just its two segment-endpoint corner points -- see build_and_mesh)
# must itself be exactly linear in Y *within that segment*. A rib's own
# chordwise profile point, at a given spar's t_i, is evaluated from the
# SAME piecewise-linear functions at its own Y station, so it lands
# exactly on that spar's edge only if the function is genuinely linear
# across whichever segment the rib falls in. Computing a chordwise
# position as fraction(y) * chord(y) -- two functions each linear (or
# piecewise-linear) in Y -- is genuinely QUADRATIC in Y, not linear:
# confirmed as a real bug in this script's first version, not a meshing
# artifact (raising MESH_SIZE resolution didn't help) -- every rib except
# the root (where quadratic and linear coincide by construction) landed 0
# nodes shared with either spar, only recovering once "fraction * chord"
# was replaced with precomputed root/tip values interpolated directly.
# The fix generalizes the same way here, now also covering the cambered Z:
# every derived absolute quantity (a spar's X position, a spar's cambered
# Z) is computed ONCE per planform station, multiplying fraction * chord
# (or bump(t_i) * chord) exactly once at that station's own fixed Y, then
# only ever LINEARLY interpolated between two already-computed station
# values -- never recomputed as a product of two functions evaluated at
# an arbitrary Y. bump(t_i) is safe to multiply in at each station because
# t_i is a fixed per-spar CONSTANT, not itself a function of Y.
LE_TIP_X = LE_ROOT_X + SPAN * math.tan(math.radians(LE_SWEEP_DEG))

PLANFORM_STATIONS: list[dict] = [
    {"y": 0.0, "chord": ROOT_CHORD, "le_x": LE_ROOT_X,
     "z_mid": Z_MID_ROOT, "box_depth_frac": BOX_DEPTH_FRAC_ROOT},
    {"y": KINK_Y, "chord": KINK_CHORD, "le_x": KINK_LE_X,
     "z_mid": KINK_Z_MID, "box_depth_frac": KINK_BOX_DEPTH_FRAC},
    {"y": SPAN, "chord": TIP_CHORD, "le_x": LE_TIP_X,
     "z_mid": Z_MID_TIP, "box_depth_frac": BOX_DEPTH_FRAC_TIP},
]

# Chordwise fraction, 0 (front) to 1 (rear), of each of N_SPARS spars --
# also reused directly as the camber profile's own chordwise sample grid
# (see module docstring's "Camber design" section for why that reuse is
# what keeps a spar's corner and its neighboring rib's spline point
# identical).
_T_VALUES = [i / (N_SPARS - 1) for i in range(N_SPARS)]

# Chordwise fraction (root, tip) for each of N_SPARS spars, front-to-rear --
# a genuine list now, not a hardcoded front/rear pair. Internal spars are
# spaced evenly between the measured front/rear fractions (no equivalent
# per-spar measurement exists for a 5-spar idealization -- see module
# docstring's Scope section on the real, unevenly-distributed ShearWebs).
SPAR_CHORDWISE_FRACS: list[tuple[float, float]] = [
    (
        _lerp(FRONT_SPAR_FRAC_ROOT, REAR_SPAR_FRAC_ROOT, t),
        _lerp(FRONT_SPAR_FRAC_TIP, REAR_SPAR_FRAC_TIP, t),
    )
    for t in _T_VALUES
]

# Precompute every station's derived absolute quantities exactly ONCE (see
# the comment block above) -- each spar's absolute X, and each spar's own
# cambered Z bounds (identical to the flat box baseline for the front/rear
# -most spars, since bump(0)=bump(1)=0) -- so nothing downstream ever
# multiplies two Y-dependent functions together at an arbitrary Y.
for _station in PLANFORM_STATIONS:
    _y = _station["y"]
    _root_tip_t = _y / SPAN  # global root/tip interpolant for spar fracs
    _station["spar_x"] = [
        _station["le_x"] + _lerp(frac_root, frac_tip, _root_tip_t) * _station["chord"]
        for frac_root, frac_tip in SPAR_CHORDWISE_FRACS
    ]
    _depth = _station["box_depth_frac"] * _station["chord"]
    _z_bottom_flat = _station["z_mid"] - _depth / 2.0
    _z_top_flat = _station["z_mid"] + _depth / 2.0
    _station["spar_z_bottom"] = [
        _z_bottom_flat - CAMBER_BOTTOM_FRAC * _station["chord"] * _camber_bump(t)
        for t in _T_VALUES
    ]
    _station["spar_z_top"] = [
        _z_top_flat + CAMBER_TOP_FRAC * _station["chord"] * _camber_bump(t)
        for t in _T_VALUES
    ]


def _piecewise_station(y: float, getter) -> float:
    """Evaluate `getter(station)` piecewise-linearly in Y across
    PLANFORM_STATIONS -- the direct generalization of the original
    script's single root-to-tip _lerp to >=2 segments. `getter` pulls one
    already-precomputed scalar field out of a station dict (e.g. its
    chord, or one spar's absolute X) -- never a product of two fields."""
    stations = PLANFORM_STATIONS
    if y <= stations[0]["y"]:
        return getter(stations[0])
    for i in range(len(stations) - 1):
        y0, y1 = stations[i]["y"], stations[i + 1]["y"]
        if y <= y1 or i == len(stations) - 2:
            t = 0.0 if y1 == y0 else (y - y0) / (y1 - y0)
            return getter(stations[i]) + (getter(stations[i + 1]) - getter(stations[i])) * t
    return getter(stations[-1])


def chord_at(y: float) -> float:
    return _piecewise_station(y, lambda s: s["chord"])


def spar_x_at(y: float, spar_idx: int) -> float:
    return _piecewise_station(y, lambda s: s["spar_x"][spar_idx])


def spar_z_at(y: float, spar_idx: int) -> tuple[float, float]:
    """(bottom, top) Z of the given spar's cap at Y -- cambered for an
    internal spar, identical to the flat box baseline for the front/rear
    -most spar."""
    return (
        _piecewise_station(y, lambda s: s["spar_z_bottom"][spar_idx]),
        _piecewise_station(y, lambda s: s["spar_z_top"][spar_idx]),
    )


def front_spar_x_at(y: float) -> float:
    return spar_x_at(y, 0)


def rear_spar_x_at(y: float) -> float:
    return spar_x_at(y, N_SPARS - 1)


def _spar_component_name(spar_idx: int) -> str:
    if spar_idx == 0:
        return "SPAR_FRONT"
    if spar_idx == N_SPARS - 1:
        return "SPAR_REAR"
    return f"SPAR_INT{spar_idx}"


def _is_planar(corners: list[tuple[float, float, float]], tol: float = 1e-3) -> bool:
    """True if 4 corner points are (near-)coplanar. A tapered+swept wing's
    spar, bounded by 2 straight lines with different sweep rates across a
    kink, is generically NOT planar -- confirmed necessary here (unlike
    the earlier simple rectangular-box spike, where every surface was
    trivially planar): OCC's addPlaneSurface rejects a non-planar wire
    outright, so this determines which panels need addSurfaceFilling's
    ruled-surface fit instead."""
    import numpy as np

    p0, p1, p2, p3 = (np.array(c) for c in corners)
    n = np.cross(p1 - p0, p2 - p0)
    n_norm = np.linalg.norm(n)
    if n_norm < 1e-9:
        return True
    n = n / n_norm
    return abs(float(np.dot(p3 - p0, n))) < tol * max(
        np.linalg.norm(p1 - p0), np.linalg.norm(p2 - p0), 1.0
    )


def build_and_mesh() -> dict:
    import gmsh
    import numpy as np

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("parametric_crm_wingbox")
        occ = gmsh.model.occ

        def add_quad_surface(corners: list[tuple[float, float, float]]) -> int:
            pts = [occ.addPoint(*c) for c in corners]
            lines = [occ.addLine(pts[i], pts[(i + 1) % 4]) for i in range(4)]
            loop = occ.addCurveLoop(lines)
            if _is_planar(corners):
                return occ.addPlaneSurface([loop])
            return occ.addSurfaceFilling(loop)

        def chordwise_profile_points(y: float, is_top: bool) -> list[int]:
            """N_SPARS OCC points at this Y, front-to-rear, at each spar's
            own cambered Z -- becomes a rib's boundary spline AND (reused
            directly, not rebuilt) each neighboring skin panel's matching
            boundary edge."""
            z_idx = 1 if is_top else 0
            return [
                occ.addPoint(spar_x_at(y, i), y, spar_z_at(y, i)[z_idx])
                for i in range(N_SPARS)
            ]

        component_of_input: dict[int, str] = {}
        input_dimtags: list[tuple[int, int]] = []

        def register(tag: int, name: str) -> None:
            component_of_input[tag] = name
            input_dimtags.append((2, tag))

        n_segments = len(PLANFORM_STATIONS) - 1

        # SPARS: N_SPARS of them, each built as one ruled quad PER
        # PLANFORM SEGMENT (root-to-kink, kink-to-tip, ...) -- not one
        # whole-span quad, since a whole-span straight line can no longer
        # represent a kinked planform (see module docstring). Each
        # segment's own quad is still provably straight-edged, since both
        # spar_x_at and spar_z_at are genuinely linear in Y within a
        # single segment.
        for spar_idx in range(N_SPARS):
            name = _spar_component_name(spar_idx)
            for seg in range(n_segments):
                y0 = PLANFORM_STATIONS[seg]["y"]
                y1 = PLANFORM_STATIONS[seg + 1]["y"]
                zb0, zt0 = spar_z_at(y0, spar_idx)
                zb1, zt1 = spar_z_at(y1, spar_idx)
                x0 = spar_x_at(y0, spar_idx)
                x1 = spar_x_at(y1, spar_idx)
                corners = [(x0, y0, zb0), (x1, y1, zb1), (x1, y1, zt1), (x0, y0, zt0)]
                register(add_quad_surface(corners), name)

        # RIBS: flat (constant-Y) planes, but no longer simple rectangles
        # -- their top/bottom boundary is now a spline through N_SPARS
        # cambered chordwise points (chordwise_profile_points), matching
        # every spar's own corner at this Y exactly. Each rib's two
        # splines (top/bottom) are kept (not just their tags) for reuse
        # as-is by the neighboring SKIN panels below -- reusing the exact
        # same OCC curve, not a fresh approximately-coincident one, is
        # what keeps rib/skin connectivity exact under camber (see module
        # docstring's "Camber design" section).
        rib_profiles: list[tuple[list[int], list[int]]] = []  # (bottom_pts, top_pts) per rib
        rib_splines: list[tuple[int, int]] = []  # (bottom_spline, top_spline) per rib
        for i, y in enumerate(RIB_STATIONS):
            bottom_pts = chordwise_profile_points(y, is_top=False)
            top_pts = chordwise_profile_points(y, is_top=True)
            rib_profiles.append((bottom_pts, top_pts))
            bottom_spline = occ.addSpline(bottom_pts)
            top_spline = occ.addSpline(top_pts)
            rib_splines.append((bottom_spline, top_spline))
            front_vert = occ.addLine(top_pts[0], bottom_pts[0])
            rear_vert = occ.addLine(bottom_pts[-1], top_pts[-1])
            loop = occ.addCurveLoop([bottom_spline, rear_vert, -top_spline, front_vert])
            register(occ.addSurfaceFilling(loop), f"RIB_{i}")

        # SKINS: top and bottom, one panel PER RIB-TO-RIB GAP (57 gaps),
        # not per planform segment -- each panel's boundary REUSES its two
        # bounding ribs' own splines directly (a negative tag reverses
        # direction without creating a new curve), so it's topologically
        # identical to those ribs' edges, not merely close to them. One
        # interior support point per INTERNAL spar (excluding front/rear,
        # already on the boundary), at the panel's Y-midpoint, pulls the
        # fit tight to that spar's own true (straight) edge -- the
        # remaining, real, honestly-measured gap this run reports is
        # exactly the residual between that fit and the spar's straight
        # line off the midpoint.
        for is_top, name in [(False, "SKIN_BOTTOM"), (True, "SKIN_TOP")]:
            z_idx = 1 if is_top else 0
            spline_idx = 1 if is_top else 0
            for i in range(len(RIB_STATIONS) - 1):
                y0, y1 = RIB_STATIONS[i], RIB_STATIONS[i + 1]
                pts0 = rib_profiles[i][1 if is_top else 0]
                pts1 = rib_profiles[i + 1][1 if is_top else 0]
                spline0 = rib_splines[i][spline_idx]
                spline1 = rib_splines[i + 1][spline_idx]
                rear_edge = occ.addLine(pts0[-1], pts1[-1])
                front_edge = occ.addLine(pts1[0], pts0[0])
                loop = occ.addCurveLoop([spline0, rear_edge, -spline1, front_edge])
                y_mid = (y0 + y1) / 2.0
                interior_tags = [
                    occ.addPoint(spar_x_at(y_mid, j), y_mid, spar_z_at(y_mid, j)[z_idx])
                    for j in range(1, N_SPARS - 1)
                ]
                register(occ.addSurfaceFilling(loop, pointTags=interior_tags), name)

        occ.synchronize()
        n_input_surfaces = len(input_dimtags)

        t_fragment_start = time.time()
        out, out_map = occ.fragment(input_dimtags, [])
        occ.synchronize()
        fragment_seconds = time.time() - t_fragment_start

        surface_component: dict[int, str] = {}
        for i, dimtag in enumerate(input_dimtags):
            comp_name = component_of_input[dimtag[1]]
            for new_dim, new_tag in out_map[i]:
                if new_dim == 2:
                    surface_component[new_tag] = comp_name
        n_output_surfaces = len(surface_component)

        gmsh.option.setNumber("Mesh.MeshSizeMax", MESH_SIZE)
        gmsh.option.setNumber("Mesh.MeshSizeMin", MESH_SIZE / 2)
        gmsh.option.setNumber("Mesh.RecombineAll", 1)
        t_mesh_start = time.time()
        gmsh.model.mesh.generate(2)
        mesh_seconds = time.time() - t_mesh_start

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        xyz_by_tag = {
            int(t): (node_coords[3 * i], node_coords[3 * i + 1], node_coords[3 * i + 2])
            for i, t in enumerate(node_tags)
        }

        elements_by_component: dict[str, list[list[int]]] = {}
        for surf_tag, comp_name in surface_component.items():
            elem_types, _elem_tags_list, elem_node_tags_list = gmsh.model.mesh.getElements(2, surf_tag)
            for etype, enodes in zip(elem_types, elem_node_tags_list):
                npe = {2: 3, 3: 4}.get(etype)
                if npe is None:
                    continue
                enodes = np.array(enodes).reshape(-1, npe)
                elements_by_component.setdefault(comp_name, []).extend(
                    [int(n) for n in row] for row in enodes
                )

        return {
            "n_input_surfaces": n_input_surfaces,
            "n_output_surfaces": n_output_surfaces,
            "fragment_seconds": fragment_seconds,
            "mesh_seconds": mesh_seconds,
            "xyz_by_tag": xyz_by_tag,
            "elements_by_component": elements_by_component,
        }
    finally:
        gmsh.finalize()


def write_bdf(result: dict) -> None:
    from pyNastran.bdf.bdf import BDF

    bdf = BDF(debug=False)
    bdf.add_mat1(1, 1.0e7, 3.8e6, 0.31, rho=0.101)
    thickness_by_prefix = {
        "SPAR": 0.410,
        "SKIN": 0.159,
        "RIB": 0.167,
    }
    pid_by_component = {name: i + 1 for i, name in enumerate(sorted(result["elements_by_component"]))}
    for name, pid in pid_by_component.items():
        prefix = next((p for p in thickness_by_prefix if name.startswith(p)), None)
        t = thickness_by_prefix.get(prefix, 0.15)
        bdf.add_pshell(pid, mid1=1, t=t, mid2=1, mid3=1)

    for tag, xyz in result["xyz_by_tag"].items():
        bdf.add_grid(tag, list(xyz))

    eid = 1
    for name, elems in result["elements_by_component"].items():
        pid = pid_by_component[name]
        for nids in elems:
            if len(nids) == 3:
                bdf.add_ctria3(eid, pid, nids)
            else:
                bdf.add_cquad4(eid, pid, nids)
            eid += 1

    OUTPUT_BDF.parent.mkdir(parents=True, exist_ok=True)
    bdf.write_bdf(str(OUTPUT_BDF), size=8, enddata=True)


def check_connectivity(result: dict) -> None:
    from collections import Counter

    nodes_by_component = {
        name: {n for elem in elems for n in elem}
        for name, elems in result["elements_by_component"].items()
    }
    all_other_nodes = {
        name: set().union(*(nodes_by_component[o] for o in nodes_by_component if o != name))
        for name in nodes_by_component
    }
    total_boundary = 0
    total_unwelded = 0
    for name, elems in result["elements_by_component"].items():
        edge_count = Counter()
        for nids in elems:
            n = len(nids)
            for i in range(n):
                edge_count[frozenset((nids[i], nids[(i + 1) % n]))] += 1
        boundary_nodes = {n for edge, cnt in edge_count.items() if cnt == 1 for n in edge}
        unwelded = boundary_nodes - all_other_nodes[name]
        total_boundary += len(boundary_nodes)
        total_unwelded += len(unwelded)
    print(f"Total boundary nodes: {total_boundary}, never shared with a neighbor: "
          f"{total_unwelded} ({100*total_unwelded/max(total_boundary,1):.1f}% gap)")


def main() -> None:
    print(f"N_SPARS={N_SPARS}, kink at Y={KINK_Y}in, camber top/bottom="
          f"{CAMBER_TOP_FRAC*100:.1f}%/{CAMBER_BOTTOM_FRAC*100:.1f}% local chord")
    result = build_and_mesh()
    n_elems = sum(len(v) for v in result["elements_by_component"].values())
    print(f"Input surfaces: {result['n_input_surfaces']}, "
          f"post-fragment surfaces: {result['n_output_surfaces']}")
    print(f"fragment: {result['fragment_seconds']:.1f}s, mesh: {result['mesh_seconds']:.1f}s")
    print(f"Total nodes: {len(result['xyz_by_tag'])}, total elements: {n_elems}")
    check_connectivity(result)
    write_bdf(result)
    print(f"\nWrote {OUTPUT_BDF}")


if __name__ == "__main__":
    main()
