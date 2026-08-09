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
implements a bounded subset of commercial MSC/NX Nastran: this case study
exercises `SOL 101` (linear statics) only. Not supported or incomplete:
nonlinear statics/dynamics, transient/frequency response, thermal,
aeroelasticity, design optimization, and a narrower materials/element
library than commercial Nastran.

The MAT2/MID4 rejection above (uCRM) is one concrete instance of that
boundary.

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

#### Tip displacement

Tip displacement: **~159.7 in (4,056 mm)** at node 9103, from
`render_stress_contour`'s `result="displacement"` parameter, which colors
by nodal translational displacement magnitude instead of stress:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_displacement_iso.png" alt="Displacement magnitude contour on the NASA CRM wingbox, showing smooth bending from root to tip" style="max-width:100%;">

*Displacement magnitude contour — smooth, monotonic bending from the fixed
root (blue, ~0 in) to the 159.7 in tip peak (orange, node 9103).*

#### Stress contour

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_stress_iso.png" alt="Von Mises stress contour on the NASA CRM wingbox, camera aimed at the governing stress element" style="max-width:100%;">

*Von Mises stress contour via `render_stress_contour`. Camera auto-aims at
the governing element's outward face normal, guaranteeing visibility rather
than risking occlusion under a fixed preset.*

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
  gaps (e.g. PSHELL/MID4, above) — check before assuming a given model runs
  on the open-source stack.

## What's next

First end-to-end pass: load, patch, solve, extract, visualize —
conversational, native Nastran format, open-source throughout. The
[repo](https://github.com/ai-for-engineering/nastran-fea) has the
implementation, tests, and backlog.
