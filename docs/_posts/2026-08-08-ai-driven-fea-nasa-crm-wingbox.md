---
layout: post
title: "Teaching an AI to Run a Real Wingbox Stress Analysis, in Native Nastran, Open Source Only"
date: 2026-08-08
author: Mohammed-Amine Bennaiem
excerpt: >-
  A conversational AI drives a full open-source FEA pipeline — Gmsh,
  pyNastran, MYSTRAN — through a real NASA wingbox model, in native
  Nastran bulk data, no commercial solver involved.
---

I'm an aerospace stress engineer. The job involves building finite element
models, solving them in Nastran, and substantiating that a structure won't
fail — plus a lot of repetitive overhead: patching case control decks,
babysitting solver runs, extracting the one number that governs a design.
This post documents the first experiment under **ai-for-engineering**: can
an AI assistant drive that workflow conversationally, on a real structure,
using only open-source tools.

## The problem

Commercial FEA tools (Nastran, Ansys, Abaqus) are closed and expensive —
not something to casually wire up to an AI assistant. Exploring what
AI-driven structural analysis actually looks like, on a real model rather
than a toy demo, requires a stack transparent enough to script against and
open enough for anyone to reproduce.

## The approach

The stack is entirely open-source and works in **native Nastran bulk-data
format**, not a translated or simplified syntax:

- **[Gmsh](https://gmsh.info/)** for meshing/CAD, where geometry needs to be
  generated rather than consumed as-is
- **[pyNastran](https://pynastran-git.readthedocs.io/)** for BDF/OP2
  I/O — reading and writing the actual Nastran deck format, parsing
  solver results
- **[MYSTRAN](https://www.mystran.com/)** as the solver — an open-source
  Nastran-compatible FEA solver

An [MCP](https://modelcontextprotocol.io/) server (`scripts/mcp_server.py`)
wraps the pipeline as tool calls — load model, patch case control, run
solver, extract peak stress, render — letting a client like Claude drive it
conversationally instead of running scripts by hand.

## The case study: NASA's Common Research Model wingbox

The case study: the wingbox from NASA's
[Common Research Model](https://commonresearchmodel.larc.nasa.gov/fem-file/wingbox-fem-files/)
— a full-scale semi-span assembly with 50+ ribs, dual spars, and stringers,
isotropic aluminum (MAT1/CQUAD4/CBAR). Public domain, and genuinely
MYSTRAN-compatible bulk data: representative of a production model rather
than a toy example.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_overview_planform.png" alt="Elevated planform view of the full NASA CRM wingbox mesh, showing sweep, taper, and root cross-section" style="max-width:100%;">

*Rendered directly from the BDF via `render_model_view`, camera="planform"
-- an angle tuned to match NASA's own CRM wingbox FEM description figures:
span laid out horizontally, elevated just enough to reveal the root's
multi-cell box cross-section and leading edge as depth cues.*

### Model description

**Units:** US customary -- inches, lbf, psi. `PARAM WTMASS 0.00259` (≈
1/386.4 in/s²) converts the deck's lbm/in³ weight density into the
consistent mass units MYSTRAN's mass matrix needs.

**Global dimensions** (the structural wingbox, spar-to-spar -- not the
full aircraft OML chord):

| | |
|---|---|
| Semi-span | 1,151.3 in (29.24 m) |
| Root chord | 291.2 in (7.40 m) |
| Tip chord | 76.3 in (1.94 m) |

**Material and properties, averaged per named group** (groups from the
NASA download's own `.ses` file, see below):

| Group | Element type | Count | Material | Avg. thickness / area | Range |
|---|---|---|---|---|---|
| Ribs | CQUAD4 | 6,220 | Aluminum (MAT1 1) | 0.167 in (4.24 mm) | 0.065-0.25 in |
| Shear webs | CQUAD4 | 8,880 | Aluminum (MAT1 1) | 0.409 in (10.4 mm) | 0.10-0.75 in |
| Skin, upper | CQUAD4/CTRIA3 | 2,322 | Aluminum (MAT1 1) | 0.159 in (4.05 mm) | 0.065-0.25 in |
| Skin, lower | CQUAD4/CTRIA3 | 2,322 | Aluminum (MAT1 1) | 0.159 in (4.05 mm) | 0.065-0.25 in |
| Spars (LE/TE) | CQUAD4 | 1,611 | Aluminum (MAT1 1) | 0.410 in (10.4 mm) | 0.10-0.75 in |
| Stiffeners | CBAR | 14,134 | Aluminum (MAT1 2) | 0.584 in² area (377 mm²) | -- |

MAT1 1/2 (same properties, separate IDs for shells vs. bars): E = 1.0x10⁷
psi (69.0 GPa), G = 3.8x10⁶ psi (26.2 GPa), ν = 0.31, ρ = 0.101 lbm/in³
(2,796 kg/m³).

**Applied load and boundary conditions** (subcase "GVW" -- cross-checked
against NASA's own FEM description in detail further down):

- **SPC set 2:** 140 nodes at the root rib (Y ≈ 0) fixed in translations
  T1/T2/T3; 56 nodes at a second rib ~120 in outboard fixed in T3 only.
- **LOAD set 3:** 12,238 FORCE cards, 20.41 lbf each, uniform +Z, one per
  grid point spread across every named component. Resultant: 249,777.6
  lbf (1,111.0 kN) vertical, zero net moment.

Two issues surfaced before the model would solve:

**Case control translated from OptiStruct to SOL 101.** NASA's bulk data is
standard Nastran, but the case control section was written for Altair
OptiStruct (`ANALYSIS MODES`/`ANALYSIS STATICS`, no `SOL`/`CEND`).
`patch_case_control` rebuilds a `SOL 101`/`CEND` block from the existing
SPC/LOAD/output requests, merging the deck's two subcases into one.

**MYSTRAN capability gap: MAT2 with a coupling term.** The alternative uCRM
dataset (University of Michigan, CC BY 4.0) uses PSHELL/MAT2 with a nonzero
MID4 (membrane-bending coupling) to represent smeared stiffened panels.
MYSTRAN's PSHELL rejects nonzero MID4 — a solver limitation, not a pipeline
bug. The NASA CRM dataset avoids this.

### MYSTRAN: scope and limitations

Every solved F06 carries the same attribution:

```
MYSTRAN developed by Dr Bill Case
*** Please report any problems to mystransolver@gmail.com ***
```

MYSTRAN ([source/releases](https://github.com/MystranSolver/MYSTRANSolver))
is a community-maintained, open-source Nastran-compatible solver. It
implements a bounded subset of commercial MSC/NX Nastran: `SOL 101`
(linear statics, the NASA CRM wingbox above) and `SOL 103` (normal modes,
the DLR ISTAR wing below) are both exercised in this project. Not
supported or incomplete: nonlinear statics/dynamics, transient/frequency
response, thermal, aeroelasticity, design optimization, and a narrower
materials/element library than commercial Nastran.

Two concrete instances of that narrower library, both found by trying to
run real third-party decks rather than assumed up front: the MAT2/MID4
rejection above (uCRM), and `CBEAM`/`PBEAM`/`PBEAML` not being supported
at all (pCRM9, below -- confirmed directly against MYSTRAN's own bundled
manual, which documents `CBAR`/`PBAR` but neither of those).

## What's actually being applied: loads and boundary conditions

Before trusting a stress result, a stress engineer needs to know what's
constraining and loading the model. `describe_loads_and_boundary_conditions`
reads a BDF's SPC/SPC1 (following SPCADD combinations) and FORCE/MOMENT-type
cards (following LOAD combinations) and summarizes them per subcase.

On the "GVW" subcase: 196 constrained nodes, 12,238 FORCE cards. Node and
card counts don't say where on the structure they apply, so the coordinates
were cross-checked directly against NASA's own
[wingbox FEM description](https://commonresearchmodel.larc.nasa.gov/wp-content/uploads/sites/7/2014/02/CRM_wingboxFEM_description_1.pdf):

- **Boundary conditions (SPC set 2): root joint, two rib stations.** 140
  nodes at the symmetry-plane root rib (Y ≈ 0) are fixed in all three
  translations (DOF `123`). A further 56 nodes at a second rib ~120 in
  outboard (Y ≈ 120, full semi-span ≈ 1,151 in) are fixed in Z only (DOF
  `3`). NASA's description documents exactly this: "simple cantilever at
  the root with simulated pressure vessel attach lug fittings at
  body-fairing intersections" — the two rib stations found here are that
  cantilever root plus the attach-lug support point.
- **Loads (LOAD set 3): the "GVW" static-strength sizing case.** NASA's
  description defines GVW as gross vehicle weight (~500,000 lbm design
  weight for this transport-category model) and this subcase as the
  baseline static-strength check: a "conservative uniform SLD (spanwise
  load distribution)" applied at a gross vehicle weight of 500 kips, Mach
  0.85, FL350. All 12,238 FORCE cards carry the identical magnitude, 20.41
  lbf (90.8 N), +Z direction, one per grid point, spread uniformly across
  every named component — skins, ribs, shear webs, spars, stiffeners.
  Resultant: **249,777.6 lbf (1,111.0 kN)**, vertical, zero net moment —
  roughly half the 500-kip GVW criterion, consistent with a semi-span model
  carrying one wing's share of the aircraft's weight.

### Results

MYSTRAN solves the patched "GVW" subcase cleanly.

Every render from here on carries a real 3D axis triad (bottom-right,
top-right when the legend needs that corner instead), and is framed
automatically: the model's own projected silhouette is sized to fill the
frame rather than leaving the large, inconsistent margins a plain camera
reset (and the hand-tuned zoom multipliers that used to compensate for it)
left behind. Camera orientation is read off the geometry itself, not
hardcoded per model: span is whichever axis has the largest bounding-box
range, thickness the smallest, chord whatever's left; root is whichever
end of the span axis has the bigger chord x thickness cross-section (a
real wing tapers). The governing-element camera itself uses the same
detection -- it picks whichever of {thickness, chord} the governing
element's own outward normal aligns with more strongly as the dominant
viewing axis (guaranteeing visibility) and rolls the camera so root always
lands on the left. That replaces an earlier approach that aimed at one of
8 fixed isometric octants, weighting span equally with the other two axes
-- which is exactly what let it occasionally rotate this long wing into an
almost-vertical portrait view.

#### Tip displacement

Tip displacement: **~159.7 in (4,056 mm)** at node 9103, from
`render_stress_contour`'s `result="displacement"` parameter, which colors
by nodal translational displacement magnitude instead of stress:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_displacement_iso.png" alt="Displacement magnitude contour on the NASA CRM wingbox, showing smooth bending from root to tip" style="max-width:100%;">

*Displacement magnitude contour — smooth, monotonic bending from the fixed
root (left, blue, ~0 in) to the 159.7 in tip peak (right, orange, node
9103).*

#### Stress contour

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_stress_iso.png" alt="Von Mises stress contour on the NASA CRM wingbox, camera aimed at the governing stress element" style="max-width:100%;">

*Von Mises stress contour via `render_stress_contour`. Camera aims at the
governing element's outward face normal, guaranteeing visibility, while
keeping span horizontal and root on the left instead of the portrait
rotation a naive isometric-octant search can produce.*

#### Peak stress by component

Peak stress by component and element type — bar direct stress and plate
von Mises are physically distinct and not blended into one number:

| Component | Element type | Peak stress | Stress measure | Element ID |
|---|---|---|---|---|
| Skin, lower | CQUAD4 | 39,983.7 psi (275.7 MPa) | von Mises | 2854 |
| Skin, upper | CQUAD4 | 38,947.6 psi (268.5 MPa) | von Mises | 1587 |
| Spars (LE/TE) | CQUAD4 | 34,046.9 psi (234.7 MPa) | von Mises | 16107 |
| Stiffeners | CBAR | 32,980.1 psi (227.4 MPa) | axial | 1559935 |
| Shear webs | CQUAD4 | 30,575.1 psi (210.8 MPa) | von Mises | 26459 |
| Ribs | CQUAD4 | 17,884.0 psi (123.3 MPa) | von Mises | 20740 |
| Skin, upper | CTRIA3 | 2,794.4 psi (19.3 MPa) | von Mises | 29402 |
| Skin, lower | CTRIA3 | 1,707.5 psi (11.8 MPa) | von Mises | 29405 |

The Stiffeners row needs qualification: `get_max_stress` reports whichever
bar direct-stress column (axial, bending at 4 recovery points, or combined)
is largest, at whichever end (A/B). For element 1559935, that's pure
**axial** stress — every bending column (`s1a`-`s4a`, `s1b`-`s4b`) is zero,
consistent with the stringers/caps acting as pin-ended truss members under
this load. Axial has no "end" (uniform along the bar with no distributed
axial load); a different governing element could just as easily be bending
at A or B.

The model-wide peak per element type (39,983.7 psi CQUAD4, 32,980.1 psi
CBAR, 2,794.4 psi CTRIA3) lands in three different components — lower skin,
stiffeners, upper skin. The lower skin, despite the highest overall stress,
isn't uniformly governing: its CTRIA3 peak (1,707.5 psi) is below the upper
skin's (2,794.4 psi). A single whole-model number identifies the worst
point, not which component drives it.

## The demo: driving it conversationally

Wrapping the pipeline in MCP tools turns the workflow into a conversation.
A session breaks into five steps.

### Step 1: load and validate

> **Me:** Load the CRM wingbox model and check it's valid.
> **Claude:** *(calls `load_model`)* — parsed cleanly: 35,489 elements,
> counts by type, no warnings.

### Step 2: patch case control

> **Me:** The case control section is missing SOL/CEND, patch it.
> **Claude:** *(calls `patch_case_control`)* — rebuilt the header, preserved
> the existing SPC/LOAD/STRESS requests, wrote the patched deck.

### Step 3: solve

> **Me:** Run it through MYSTRAN.
> **Claude:** *(calls `run_solver`)* — solved successfully, no fatal errors
> in the F06.

### Step 4: extract peak stress

> **Me:** What's the peak stress, and where?
> **Claude:** *(calls `get_max_stress`)* — CQUAD4 governs at 39,983.7 psi
> (275.7 MPa) on element 2854, subcase 1.

### Step 5: visualize and isolate

> **Me:** Show me a stress contour, and isolate just the ribs so I can see
> how they're loaded.
> **Claude:** *(calls `render_model_view` with `isolate_groups=["RIBS"]`,
> then `render_stress_contour` the same way)* — two renders follow.

Named-group isolation: the NASA download ships a `.ses` file (Patran/
HyperMesh session format) defining named element groups — ribs, spars,
skins, stringers. `ses_groups.py` parses these; the render tools hide or
isolate a group by name and auto-frame the camera:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_ribs_isolated.png" alt="Ribs isolated from the rest of the wingbox assembly, fanned out for readability" style="max-width:100%;">

*All 6,220 rib elements, isolated from the other ~29,000. `camera="auto"`
aims at their shared face normal, tilted to fan out otherwise-overlapping
parallel ribs.*

With a stress contour instead of bare geometry:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_ribs_stress.png" alt="Von Mises stress contour on just the isolated ribs" style="max-width:100%;">

*Same isolated ribs, colored by von Mises stress. Governing element: 17,884
psi (123.3 MPa) — a rib-specific peak, distinct from the model-wide one
above.*

## Isolating results by component

How `isolate_groups` handles a resolved element set:

- **Geometry and results are trimmed together.** Isolating for
  `render_stress_contour` trims the OP2 to the same element subset before
  pyNastranGUI loads it, so geometry and results match in size.
- **Bar elements fringe by axial stress, not von Mises.** CBARs have no
  per-element von Mises value, but `result="axial"` colors by their real
  per-element axial-stress result instead (e.g. `Stiffeners`, 14,134 CBAR).
  pyNastranGUI already computes and stores this value; it's just not
  exposed under a descriptive case name the way von Mises or displacement
  are, so selecting it means matching the case's own method label
  ("Stress XX") rather than a resname substring search.
- **Non-element groups are flagged explicitly.** `LUMPED_MASS` is `CONM2`
  mass points, not elements. Isolating it resolves to zero elements,
  reported as a specific message rather than attempted as a render.

Every real (non-mass) group, each rendered with a correctly framed stress
contour. The flat panel groups below (skins, shear webs, spars) use
`camera="planform"` -- the same NASA-report-style angle as the case-study
overview, which reads cleanly for a single flat component isolated on its
own:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_skin_lwr_stress.png" alt="Von Mises stress contour on the lower skin panel" style="max-width:100%;">

*Lower skin (2,322 CQUAD4) — model-wide peak: 39,983.7 psi (275.7 MPa),
element 2854.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_skin_upr_stress.png" alt="Von Mises stress contour on the upper skin panel" style="max-width:100%;">

*Upper skin (2,322 CQUAD4) — peak 38,947.6 psi (268.5 MPa).*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_shearwebs_stress.png" alt="Von Mises stress contour on the shear webs" style="max-width:100%;">

*Shear webs (8,880 elements) — peak 30,575.1 psi (210.8 MPa).*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_spars_lete_stress.png" alt="Von Mises stress contour on the leading- and trailing-edge spars" style="max-width:100%;">

*Leading- and trailing-edge spars (1,611 elements) — peak 34,046.9 psi
(234.7 MPa).*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_stiffeners_stress.png" alt="Axial stress contour on the isolated stiffener elements" style="max-width:100%;">

*Stiffeners (14,134 CBAR) — colored by axial stress (`result="axial"`).
Peak: 32,980.1 psi (227.4 MPa), element 1559935; the bipolar scale (min
-31,611.7 psi) reflects axial stress carrying a sign (tension/compression),
unlike von Mises. This group keeps `camera="auto"` rather than
`"planform"` -- a planform angle looks nearly straight down the length of
these mostly-spanwise bars, collapsing them into an unreadable overlapping
mess; `"auto"` fits the frame to the actual selection instead.*

Every render above used the same `render_stress_contour` call, varying
`isolate_groups`, `camera` (`"planform"` for the flat panels,
`"auto"` for the all-bar Stiffeners group), and `result` (`"axial"` in
place of the default `"von_mises"`, also for Stiffeners). Per-component
peak stresses (same `get_max_stress` call against each group's trimmed
OP2) are tabulated in
Peak stress by component above.

## A second case study: does the camera logic generalize?

Every camera/zoom decision above was tuned against one model. Testing it
against a second, independently-authored wing -- different mesh, different
author, different unit system -- is a better check than re-running the
same case study again. Two were tried.

### pCRM9: a real solver-compatibility gap, found honestly

[pCRM9](https://zenodo.org/records/6390714) (TU Delft / University of
Michigan CRM-derived geometry, CC-BY 4.0) parses and renders cleanly
through the same camera/zoom pipeline -- and in the process exposed a real
bug the pipeline's own tests never caught: isolating a single small panel
from this model revealed that `_write_filtered_bdf` left orphaned `GRID`
nodes in place, which leaked into the zoom auto-fit's measurement and
undersized the render. It went unnoticed against the NASA CRM wingbox
because every isolated group tested there (ribs, skin panels) already
spans nearly the whole span, so the bug had nothing to bite on. Fixed with
pyNastran's own `remove_unused` utility.

#### Model description

**Units:** mm / N / tonne / MPa -- stress in MPa, mass in tonne, density
in tonne/mm³. Deliberately different from the NASA CRM wingbox's
inches/lbf/psi, part of what makes this a real generality check rather
than a rerun of the same numbers.

**Global dimensions** (structural wingbox, spar-to-spar):

| | |
|---|---|
| Span | 26,281.5 mm (26.28 m) |
| Root chord | 5,959 mm (5.96 m) |
| Tip chord | 682 mm (0.68 m) |

**Material:** all aluminum, MAT1 1: E = 69,000 MPa (69.0 GPa), G = 26,538.5
MPa (26.5 GPa), ν = 0.30, ρ = 2.7x10⁻⁹ tonne/mm³ (2,700 kg/m³).

**Properties, averaged per group.** This deck ships no named-group file
like the NASA CRM wingbox's `.ses` -- groups below are inferred
geometrically (panel normal direction) and from the PSHELL/PBEAML property
ID ranges the original authors already used to organize the deck (`$ Top
skin` / `$ Lower skin` / `$ Front spar` / `$ Rear spar` comments precede
the property block, in that order, matching the 100s/200s/300s/400s PID
ranges 1:1):

| Group | Element type | Count | Avg. thickness / area | Range |
|---|---|---|---|---|
| Top skin | CQUAD4/CTRIA3 | 597 | 11.20 mm | 6.95-16.35 mm |
| Lower skin | CQUAD4/CTRIA3 | 597 | 10.81 mm | 4.73-17.05 mm |
| Front spar | CQUAD4/CTRIA3 | 88 | 5.76 mm | 4.92-6.47 mm |
| Rear spar | CQUAD4/CTRIA3 | 82 | 5.60 mm | 4.29-6.48 mm |
| Ribs | CQUAD4/CTRIA3 | 1,148 | 15.00 mm (uniform) | -- |
| Stringers | CBEAM | 1,125 | 250 mm² area (uniform) | -- |
| Spar caps | CBEAM | 22 | 2,500 mm² area (uniform) | -- |

**Applied load and boundary conditions:**

- **SPC set 1:** 67 grid points across the inboard wing (Y ≈ 0-10.7 m,
  ~41% of span) fixed in all 6 DOF -- not a single root line, consistent
  with representing a wing-body carry-through/attachment region rather
  than an idealized point cantilever.
- **No static LOAD card anywhere in the deck** -- the original analysis is
  `SOL 103` (normal modes), so there's nothing to sum a resultant from. A
  `CONM2` lumped mass totaling 68.3 tonne (an MTOW fuel/mass distribution,
  per the case study's source) is present but is inertial, not an applied
  load.

Solving it is a different story. MYSTRAN 19.0.0's own bundled manual
doesn't document `CBEAM`, `PBEAM`, or `PBEAML` at all -- confirmed
directly against the manual, not inferred from a parse error alone.
~31% of this model's elements (1,147 `CBEAM` spar-cap/stiffener elements)
use exactly those cards. Converting them to MYSTRAN's supported
`CBAR`/`PBAR` is possible -- pyNastran computes exact area and bending
inertia for the library cross-section directly, and a standard textbook
formula gets a reasonable torsion constant -- but it's a big enough change
to the original model that it's logged as a known gap rather than solved
silently, the same treatment the uCRM `PSHELL`/`MID4` gap already got
above.

### The DLR ISTAR wing: composite shells, real normal modes

[The ISTAR demonstrator wing](https://zenodo.org/records/7017137) (DLR,
CC-BY 4.0) is a genuinely different construction: 1,574 CQUAD4 elements,
each with its own multi-layer GFRP composite layup (`PCOMP` + `MAT8`) --
no isotropic material and no bar element anywhere in the deck. It solves
cleanly, and its own original analysis is `SOL 103` (normal modes), not a
static stress case -- exercising a genuinely different part of the
pipeline than either wing above.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/istar_wing_overview.png" alt="Elevated planform view of the DLR ISTAR composite demonstrator wing" style="max-width:100%;">

*Rendered via `render_model_view`, camera="planform" -- the same fixed
preset tuned against the NASA CRM wingbox, applied unchanged to a wing a
fraction of the size, from a different institution, in different units.*

#### Model description

**Units:** SI -- meters, N, kg, Pa.

**Global dimensions:**

| | |
|---|---|
| Span (structural, to the last meshed row) | 0.647 m |
| Root chord | 0.266 m |
| Tip chord | 0.070 m |

The case study's own tip-displacement discussion below quotes Y = 0.685 m
for the wingtip -- that's `RBE3` independent node 10001, a synthetic
interpolation point slightly beyond the last physically meshed row, not
the structural span itself; the two numbers describe different things.

**Material and properties, averaged per group.** Every element carries its
own `PCOMP` (1,574 unique layups, no two elements share one) drawn from 17
orthotropic `MAT8` materials -- fiber-dominated plies from E11 ≈ 42 GPa up
to 207 GPa, down to near-zero-stiffness resin/core plies. Like pCRM9, this
deck ships no named-group file, so groups below are classified
geometrically by panel normal direction (span/chord/thickness axis --
the same natural-axis detection `render_model_view`'s camera logic uses):

| Group | Element type | Count | Avg. total laminate thickness |
|---|---|---|---|
| Skin (upper + lower) | CQUAD4 | 1,260 | 5.09 mm (uniform) |
| Ribs | CQUAD4 | 224 | 1.00 mm (uniform) |
| Spar webs | CQUAD4 | 90 | 0.44 mm (uniform) |

**Applied load and boundary conditions:**

- **SPC set 3:** a single grid, node 200000 -- not a physical wing node
  but a synthetic reference point -- fixed in all 6 DOF. An `RBE2` rigidly
  ties it to the 30 physical grids of the root cross-section, so the wing
  is effectively cantilevered there.
- **Original deck (`SOL 103`):** no static load at all -- eigenvalue
  extraction only.
- **Derived static run** (this project's own addition, `SOL 101`, not from
  the original authors): one `FORCE` card, 50 N in +Z at GRID 10001, the
  `RBE3` independent node at the wingtip (see "A static run too" below for
  why that specific point).

A new tool, `get_normal_modes`, reports each extracted mode's natural
frequency directly from the OP2's eigenvalues (`sqrt(eigenvalue) /
(2*pi)`, not pyNastran's own confusingly-named `mode_cycles` attribute --
confirmed by cross-checking against a real F06's printed columns that it
actually holds radians/s for this result type, not Hz, despite the name).
The original dataset ships a real MSC Nastran 2018.2 run of the identical
deck, giving a genuine independent check rather than just "the solver
exited 0": MYSTRAN's frequencies agree with MSC's to 6 significant figures
on every one of the 15 extracted modes.

| Mode | Frequency (Hz) |
|---|---|
| 1 | 9.171 |
| 2 | 31.710 |
| 3 | 56.227 |
| 4 | 69.472 |
| 5 | 107.975 |

`render_stress_contour`'s `result="mode_shape"` colors by one specific
mode's eigenvector displacement -- `mode_number` picks which one, since
(unlike stress or displacement) every mode shares the same result-case
name in the OP2, differentiated only by which mode's data a given case
actually holds:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/istar_wing_mode1.png" alt="First bending mode shape of the DLR ISTAR wing, 9.17 Hz" style="max-width:100%;">

*Mode 1, 9.17 Hz -- first bending: displacement grows monotonically from
the fixed root to the free tip.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/istar_wing_mode2.png" alt="Second mode shape of the DLR ISTAR wing, 31.71 Hz, showing a diagonal deflection gradient across the wing rather than a simple root-to-tip pattern" style="max-width:100%;">

*Mode 2, 31.71 Hz -- a visibly different shape: the peak deflection band
runs diagonally across the wing rather than concentrating at the tip,
consistent with a coupled bending-torsion mode.*

(pyNastranGUI's own on-screen label under each render says "freq = ...
Hz" -- that's the same `mode_cycles` mislabeling mentioned above, actually
radians/s. Use `get_normal_modes`'s own value, not the caption.)

### A static run too

The ISTAR deck ships only the normal-modes case above -- no `FORCE` or
`PLOAD*` card anywhere in it. Running a static case meant adding one:
`SOL 101`, the same `SPC`, and a single 50 N force in +Z at GRID 10001 --
the independent node of an `RBE3` (`RBE3 10001 ... 1366 1380 ...`) the
original modelers already placed at the wingtip (Y=0.685, this model's own
span max) to interpolate a load or sensor reading onto the surrounding tip
patch. Reusing that point rather than picking a node ourselves keeps the
load at the one location the model's own GVT-style setup already intended
for exactly this. The 50 N magnitude is our own choice, sized for a
visible but not absurd deflection on a wing this small and this flexible
(an aeroelastic-tailoring research wing, by design).

Peak tip displacement: **45.2 mm** at GRID 10001 (6.6% of the 685 mm
span) -- large, but this wing is intentionally very flexible, not a
modeling error.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/istar_wing_static_displacement.png" alt="Static displacement contour on the DLR ISTAR wing under a 50 N wingtip force" style="max-width:100%;">

*Displacement magnitude, smooth and monotonic root to tip -- the expected
shape for a cantilever under a tip load.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/istar_wing_static_stress.png" alt="Static von Mises stress contour on the DLR ISTAR wing under a 50 N wingtip force" style="max-width:100%;">

*Von Mises stress, peak 85.8 MPa. The hot spot sits near Y ~ 0.47
(~70% span) -- next to another `RBE3` interpolation patch, not at the
physical root. Rigid-element attachment points are a known place for a
model to report a locally elevated stress that isn't the real
root-governing value; treat this peak as a modeling-artifact caveat, not
a structural conclusion.*

Getting this far needed two real fixes, not just a new render:
`get_max_stress` raised `AttributeError` against this deck's composite
(PCOMP) stress table, which indexes by `.element_layer` (one row per ply)
instead of `.element`/`.element_node` like every other stress result
tested so far -- fixed generically in `_element_ids_for`, not special-
cased to this one model. Separately, this deck's rigid elements
(`RBE2`/`RBE3`), its own coordinate systems, and its per-ply material
orientations made pyNastranGUI create over a dozen extra actors
(`Coord 511`, `mcid ply=1..20`, `rigid_lines`, `SPC=3`, ...) that produced
an unreadable render (oversized overlapping text, a badly mis-framed
model) -- fixed by hiding everything except the actual mesh, rather than
hardcoding yet another actor name.

## Rebuilding the wingbox from geometry alone

Everything above starts from NASA's own pre-built finite element deck. A
harder, more interesting test: starting from nothing but the CAD geometry
NASA also publishes (five separate IGES midsurface files -- ribs, spars,
skins, rib caps, stringers) -- mesh it, assign properties, reconstruct
boundary conditions and the GVW load, and solve, without touching the
original FE deck at all. Does a from-CAD pipeline get *close* to a real,
previously-validated result on an actual aircraft structure?

The short version: yes, same order of magnitude, with real modeling
differences accounting for the rest -- and getting there needed real
engineering, not just wiring tools together. The textbook approach (an
OpenCASCADE boolean fragment gluing all five files into one topologically
exact mesh) was tried first and abandoned after hitting real, reproducible
tooling limits: 234 seconds to fragment just 2 of the 5 files, and the
result still had unhealable sub-micron sliver edges. The pipeline instead
meshes each component independently and welds coincident nodes across
components afterward -- an approximate, tolerance-based connection, not a
mathematically exact shared curve.

Getting from "meshes and merges" to "MYSTRAN solves it and the answer
means something" surfaced four more real bugs. The one that mattered
most: `PSHELL` cards missing `MID2` (bending material) gave the shells
membrane-only stiffness -- MYSTRAN's own `AUTOSPC` found literally every
rotational DOF in the ~70,000-node model singular and silently
auto-constrained all of them, "solving" cleanly with displacements up to
~1e14 in. A technically-valid, physically meaningless answer that a less
careful check would have reported as a working rebuild. Full technical
detail, including the other three bugs, is in the
[repo's issue/PR history](https://github.com/ai-for-engineering/nastran-fea/issues/47)
and `case_studies/nasa_crm_wingbox/README.md`.

### Results

| | Rebuilt | Original |
|---|---|---|
| Nodes | 70,606 | 13,878 |
| Elements | 79,053 | 35,489 |
| Tip displacement | 93.3 in | 159.7 in (-41.6%) |
| Peak von Mises, Ribs | 79,008 psi | 17,884 psi (+341.8%) |
| Peak von Mises, Spars | 88,450 psi | 34,046.9 psi (+159.8%) |
| Peak von Mises, Skins | 80,992 psi | 39,983.7 psi (+102.6%) |
| Peak von Mises, Stringers | *excluded, see below* | 32,980.1 psi (CBAR) |

Stringers are excluded from the numeric comparison, not silently cleaned
up: it's the least reliable component in this rebuild by construction
(the one component where quad recombination failed and fell back to an
all-triangle mesh, a *back-calculated* rather than measured thickness,
and the sole owner of every one of the rebuild's 21 residual
poorly-connected nodes). Its peak stress stays in the millions of psi
even after excluding every element that touches an unphysically-displaced
node -- reporting a "cleaned" number anyway would overstate confidence in
it.

### Visual inspection: does it actually look right?

Numbers can agree by coincidence. The more direct check: render both
models from matching camera angles and isolate the same structural
groups, side by side, and look.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_overview_planform.png" alt="Side-by-side planform view comparing the original and rebuilt NASA CRM wingbox meshes" style="max-width:100%;">

*Planform view, both models. Sweep, taper, and root box cross-section all
line up. The rebuild's mesh is visibly denser everywhere -- 79,053
elements against 35,489, since `mesh_size` was chosen independently of
NASA's own mesh density, not tuned to match it.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_overview_iso.png" alt="Side-by-side isometric view comparing the original and rebuilt NASA CRM wingbox meshes" style="max-width:100%;">

*Isometric view -- the same "iso rotates a long swept wing into a tall
portrait shape" effect documented earlier in this post shows up
identically on both models, itself a small confirmation that the
camera/framing logic generalizes rather than being tuned to one mesh.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_overview_top.png" alt="Side-by-side thickness-profile view comparing the original and rebuilt NASA CRM wingbox meshes, showing span versus thickness" style="max-width:100%;">

*Span-vs-thickness profile. This is the one overview angle with a real,
visible discrepancy: the original shows a distinctly cambered, curved
upper surface near the root, while the rebuild reads comparatively
flatter and more box-like along most of the span. Plausible cause: the
rebuild's `mesh_size` (150 mm) undersamples the true NURBS curvature of
the IGES surfaces more than NASA's own finer, hand-tuned mesh does --
worth a finer local mesh size as a follow-up check, not yet done here.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_ribs.png" alt="Side-by-side isolated-ribs comparison between the original and rebuilt NASA CRM wingbox" style="max-width:100%;">

*Ribs isolated, `camera="auto"` on both. The clearest match in the whole
inspection -- same fan pattern, same 58 rib stations, same taper, same
root-to-tip spacing. Just denser.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_spars.png" alt="Side-by-side isolated-spars comparison between the original and rebuilt NASA CRM wingbox, showing a dense shear-web comb structure in the original that is absent from the rebuild" style="max-width:100%;">

*Spars isolated -- the most significant discrepancy found in this
inspection. The original's "ShearWebs" group (8,880 elements) is a dense
comb of closely-spaced internal webs running between ribs, in addition to
the 2 main leading/trailing-edge spars (1,611 elements). The rebuild's
SPARS component -- verified from two different camera angles, not just
one occlusion-prone view -- contains only 3 clean, continuous spanwise
webs (front/mid/rear spar) and no periodic shear-tie structure at all.
NASA's downloadable IGES geometry for spars appears to only include the
primary continuous webs, not the rib-spaced shear ties the original FE
model actually has. This is a genuine structural coverage gap in the
source CAD, not a rendering artifact -- and a plausible partial explanation
for the elevated peak stress at spars/ribs above: less internal
stiffening structure in the rebuild means load concentrates differently
than in the original.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_skins.png" alt="Side-by-side isolated-skins comparison between the original and rebuilt NASA CRM wingbox" style="max-width:100%;">

*Skins isolated (upper + lower together on both sides, since the rebuild
doesn't split them the way the original's named groups do). Shape
matches well; the rebuild's mesh is visibly less regular -- an
unstructured, quad-recombined-from-triangles pattern versus the
original's clean structured grid. A meshing-*style* difference, not a
shape discrepancy.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_stringers.png" alt="Side-by-side comparison of the original's CBAR stiffeners and the rebuild's shell-based stringers" style="max-width:100%;">

*Stiffeners (original, CBAR) vs. stringers (rebuild, shell). The element-
type difference is already-documented and expected -- the original models
these as 1D bars, the rebuild as shell strips, since the IGES download
only provides them as 2D midsurfaces. Beyond that expected difference,
the rebuild shows some crossing/convergence near the root that the
original's cleaner fan doesn't -- plausibly genuine design tapering
(stringers terminating before the root attachment, a real detail in many
aircraft structures) or a visual symptom of this component's
already-documented residual connectivity issues. Not disentangled here.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_rib_caps.png" alt="Rebuilt rib caps geometry, shown alone since the original model has no equivalent named group" style="max-width:100%;">

*Rib caps -- rebuild only; the original's named groups have no separate
entry for this at all (see the case study README for how its thickness
was assumed rather than measured). Shown here mainly to confirm the
geometry itself is sane: a closed perimeter loop around each of the 58
ribs, consistent with what a rib-edge reinforcing flange should actually
look like.*

### Summary

Three real, distinct kinds of discrepancy came out of this inspection,
worth telling apart:

1. **Expected, benign:** mesh density and regularity (denser,
   less-structured rebuild mesh throughout) and the stringers'
   element-type change (shell vs. CBAR) -- both already understood before
   this inspection, now visually confirmed rather than just inferred from
   counts.
2. **A real geometric gap worth flagging:** the missing shear-web comb
   structure in the rebuilt spars -- 8,880 elements' worth of internal
   stiffening present in the original FE model with no counterpart in the
   downloaded IGES geometry. This is the inspection's most useful finding,
   and a plausible partial driver of the elevated peak-stress readings
   throughout the rebuild.
3. **Unresolved, flagged not chased further:** the flatter apparent
   camber in the thickness-profile view, and the stringer convergence
   near the root. Both are visible, both have plausible benign
   explanations (mesh-size curvature undersampling; genuine design
   tapering), and neither was run down to a definitive cause here.

## A third rebuild: parametric geometry, built for meshing

The spar/rib inspection above ends on a real, visible gap — but there's a
smaller, less visible one underneath it. Looking closely at the rebuild in
pyNastranGUI, intersecting parts don't always share nodes where they
physically meet. Worth chasing down properly rather than left as a footnote.

### The weld algorithm had a real bug — and a real ceiling above it

First, an actual bug: `_weld_coincident_nodes`'s conflict-avoidance check
rejected a cross-component weld whenever a component merely *appeared* in
both clusters, not based on real distance. Once one RIBS node claimed a
nearby SPARS node, every *other* RIBS node along that same seam got
blocked from welding to *any* SPARS node — even a distinct, unclaimed one
right next to it. A many-(fine mesh)-to-one-(coarse mesh) density mismatch
between independently-meshed components is normal, not a sign of distinct
physical points. Fixed with a real-distance check, plus an explicit guard
against merging two corners of the *same* raw element regardless of
distance (a first attempt using distance alone let that happen instead,
spiking degenerate elements from ~150 to 12,900+ before being caught).

Even after the fix, ~23% of real near-boundary candidate pairs are still
structurally unweldable at production tolerance — not a tolerance
shortfall, but because welding them would collapse a genuine element. That
sent the investigation looking for a root cause instead of tuning a number.

**The root cause is the CAD file, not the algorithm.** Industry documentation
(Ansys/SpaceClaim's own material on multibody parts) is explicit: "shared
topology is the only way to achieve a conformal mesh where bodies meet,"
created by *imprinting* one part's boundary curve onto its neighbor —
before either is meshed, not recovered from bare coordinates afterward.
NASA's 5 IGES files are independently-authored, "dumb" surface exports with
no shared topology encoded at all — each component modeled and exported on
its own, so any apparent coincidence at a rib/spar boundary is a geometric
accident, not a topological fact. Cross-checked against the original
reference deck: it uses essentially zero connector elements between
components (20 `RBE3`, all for load/SPC application, none tying
ribs/spars/skins together) — meaning it achieves connectivity via
genuinely shared `GRID`s, which in turn means it was built from geometry
that already had shared topology, not independently-modeled files welded
together after the fact.

That reframes the question. Not "how do we mesh this CAD better" — "what
does the CAD need to look like for a conformal mesh to be possible at
all."

### The parametrization philosophy

Real automated wingbox-generation research doesn't mesh independently
authored per-component CAD. It generates ribs, spars, and skins from *one
shared parametric definition* — rib/spar planes cut against a single
outer-mold-line surface — so a boundary curve is the same computed curve
reused on both sides, not two independent approximations of "the same"
edge that happen to almost line up.

Applied here: build every rib, spar, and skin as a parametric surface
directly inside *one* Gmsh OpenCASCADE session — no export/re-import round
trip, no per-file tolerance loss — then run the exact same
`gmsh.model.occ.fragment` operation that failed on the real IGES files
(234 seconds for just 2 of 5 components, unhealable sub-micron slivers).
Proven first on a generic rectangular toy wingbox (2 spars, N ribs, flat
skins) before committing to the real wing's actual complexity: **0.0%
connectivity gap**, fragment+mesh in under a second. That result is what
justified spending more time on the real planform rather than stopping at
"seems to work" — confirms the *stack* isn't the limitation, only the
input data was.

### Real dimensions, not guessed

The generic toy wingbox proved the mechanism; it isn't the NASA CRM wing.
`spikes/extract_crm_planform.py` measures the real planform directly from
the *original solved deck* — not copied from generic published CRM
aerodynamic parameters, which describe the full aircraft OML, not
necessarily this specific wingbox idealization:

| Parameter | Value | Source |
|---|---|---|
| Span | 1,151.32 in | bounding box, structural nodes only |
| Root / tip chord | 291.19 in / 85.31 in (taper 0.293) | Skin group, root/tip probes |
| Leading-edge sweep | 32.35° | leading-edge X shift, root to tip |
| Dihedral | ~6.4° | mid-depth Z shift, root to tip |
| Box depth | 18–20% local chord | thickness-axis range at 5 span stations |
| Front / rear spar position | ~0–11% / ~97–92% chord | `Spars_LETE` group, root-to-tip trend |
| Rib stations | 57 real stations, 0.0–1,146.5 in | connected-component analysis, `RIBS` group |

The rib count needed a real fix along the way: a first pass simply
clustered `RIBS` group nodes by span position, which produced **314**
spurious "stations" — noise from continuous node scatter within a single
(possibly non-planar) rib, not 314 real ribs. Treating each rib as its own
*connected component* of the group's own element graph — nodes reachable
from each other through shared element edges — resolved this cleanly to
the real number: 57 ribs, denser near the root (~24 in spacing) and a
crank region (~15–17 in), settling to a consistent ~20.9 in spacing
outboard. Real data, not a smoothed average.

### Building it: the fragment/mesh pipeline

Ribs are flat planes perpendicular to the span axis; spars are ruled quads
between their own root and tip corners (provably planar — bounded by two
parallel, purely-vertical lines at Y=0 and Y=span). Skins are generally
*not* planar: on a tapered wing, the front and rear spars sweep at
different rates, so the quad connecting them is a twisted ruled surface,
not a flat rectangle — handled with `gmsh.model.occ.addSurfaceFilling`
(a ruled-surface fit) wherever a planarity check on the four corners fails,
falling back to `addPlaneSurface` when it doesn't.

Two more real bugs surfaced getting from "meshes" to "actually
conformal":

**A quadratic-vs-linear mismatch.** A spar's chordwise position was
computed as `fraction(Y) * chord(Y)` — two functions that are each linear
in Y, multiplied together, which is *quadratic* in Y. But the spar panel
itself was built from just its root and tip corner points — a straight,
linear edge. Every rib except the root landed **zero** shared nodes with
either spar as a result (root worked because linear and quadratic
coincide there by construction). Confirmed this wasn't a mesh-resolution
issue first (lowering `MESH_SIZE` made it slightly worse, not better) before
finding the real cause. Fixed by precomputing each edge's root/tip values
once and interpolating linearly everywhere — matching how the straight-edge
panels are actually built, not re-deriving from a percent-chord formula at
arbitrary Y.

**An unclosed tip.** The real rib list's last station (1,146.5 in) is
~4.8 in short of the true tip (1,151.32 in, from the full bounding box),
leaving the spar/skin panels' tip edge open with nothing to close it.
Fixed with one more rib exactly at the true tip station.

Result: **0.0% connectivity gap** — 5,156 of 5,156 boundary nodes shared,
zero left over — at the real wing's actual scale and complexity (62 input
surfaces, 18,314 nodes, 19,821 elements), with fragment+mesh finishing in
1.4 seconds. The abandoned full-face fragment attempt on the real IGES
files took 234 seconds and still failed on just 2 of 5 components.

### Inspection checks vs. the original model

Span, chord, taper, sweep, dihedral, and rib stations match the original
by construction here — they were measured *from* it, not independently
derived, so matching those specific numbers isn't the interesting check.
The real questions: does the fragment/mesh pipeline actually connect
everything (answered above, quantitatively), and does the resulting shape
and rib pattern look right when rendered.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_compare_planform.png" alt="Side-by-side planform comparison between the original NASA CRM wingbox and the parametric rebuild" style="max-width:100%;">

*Planform silhouette, both models. Span, taper, sweep, and root cross-
section proportions line up. Two honest differences visible: the
original's real curved upper skin (the same camber this post's earlier
inspection already flagged as unmodeled) versus the parametric rebuild's
flat-panel idealization, and a simpler tip/root closeout shape here versus
the original's more detailed cap structure.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_compare_ribs.png" alt="Side-by-side ribs comparison between the original NASA CRM wingbox and the parametric rebuild" style="max-width:100%;">

*Ribs isolated, both models. The fan pattern matches closely — denser
near the root, spreading to a consistent spacing outboard — confirming
the connected-component extraction got the real rib layout right, not
just the count.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_iso.png" alt="Isometric view of the parametric CRM wingbox rebuild, showing sweep, taper, and dihedral" style="max-width:100%;">

*Isometric view of the parametric rebuild alone — sweep, taper, and the
measured ~6.4° dihedral all visible in one shot.*

**The actual payoff, stated as a number:** the IGES-welded rebuild's own
node connectivity, even after fixing the weld algorithm's real bug above,
still runs 43–99% unwelded per component pair (structural, not a tuning
gap). This parametric rebuild: 0.0%, on a real wing's real planform.

**What's simplified here, stated plainly:**

- **Only 2 spars.** The original deck's `ShearWebs` group resolves (same
  connected-component analysis) to ~22 *more* continuous internal spanwise
  webs beyond the main front/rear spars — a genuinely multi-spar wingbox,
  not just two. Omitted to keep this proof of concept's fragment/mesh
  complexity bounded; a documented gap, not a silent one.
- **Ribs are flat planes perpendicular to span.** Whether the real
  aircraft's ribs are angled/streamwise in places wasn't checked either
  way.
- **Skins carry no airfoil camber** — a flat-panel idealization, matching
  how this project's other wingbox models already idealize skins (a
  structural model, not an aerodynamic OML), not a new simplification
  introduced here.
- **No stringers/stiffeners.**

### What's still open

No boundary conditions, load, or solve on this model yet — geometry and
mesh connectivity only, so far. The natural next step is the same
BC-reconstruction and GVW-load approach already used for the IGES-welded
rebuild, then a real MYSTRAN solve and comparison. Full technical detail —
the weld-algorithm fix, the CAD-topology research, and this parametric
generator — is in
[issue #59](https://github.com/ai-for-engineering/nastran-fea/issues/59)
and [PR #60](https://github.com/ai-for-engineering/nastran-fea/pull/60).

## Honest caveats

This pipeline explicitly does **not**:

- **Replace certified stress substantiation.** Real sign-off needs
  allowables, buckling/fatigue checks, hand-calc cross-verification, and
  engineering judgment on top of raw FEA output. This pipeline gets from
  geometry to peak stress fast; it doesn't replace that layer.
- **Run headless.** Visualization scripts a real pyNastranGUI window;
  `QT_QPA_PLATFORM=offscreen` breaks VTK's OpenGL context on Windows.
  Non-interactive, not headless — needs an active desktop or a CI runner
  with a display.
- **Match commercial Nastran's solver scope.** MYSTRAN has real capability
  gaps (PSHELL/MID4 and CBEAM/PBEAM/PBEAML, both above) — check before
  assuming a given model runs on the open-source stack.

## What's next

First end-to-end pass: load, patch, solve, extract, visualize —
conversational, native Nastran format, open-source throughout. The
[repo](https://github.com/ai-for-engineering/nastran-fea) has the
implementation, tests, and backlog.
