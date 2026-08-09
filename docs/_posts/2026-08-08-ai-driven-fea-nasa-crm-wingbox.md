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

I'm an aerospace stress engineer. Day to day, that means building finite
element models, running them through Nastran, and turning stress results
into substantiation that a structure won't fail. It also means a lot of
manual, repetitive work: patching case control decks, babysitting solver
runs, hunting through result files for the one number that governs a
design. This post is about the first experiment in **ai-for-engineering**:
can an AI assistant drive that workflow conversationally, on a real
structure, using only open-source tools?

## The problem

Commercial FEA tools (Nastran itself, Ansys, Abaqus) are the industry
standard for good reasons, but they're also closed, expensive, and not
something you can casually wire up to an AI assistant and experiment with
on a weekend. If the goal is to explore what AI-driven structural analysis
actually looks like — not a toy demo, a real workflow on a real model — it
needs a stack that's transparent enough to script against and open enough
that anyone can reproduce it.

## The approach

The stack for this project is deliberately all open-source, and it works
in **native Nastran bulk-data format** rather than a translated or
simplified syntax:

- **[Gmsh](https://gmsh.info/)** for meshing/CAD, where geometry needs to be
  generated rather than consumed as-is
- **[pyNastran](https://pynastran-git.readthedocs.io/)** for BDF/OP2
  I/O — reading and writing the actual Nastran deck format, parsing
  solver results
- **[MYSTRAN](https://www.mystran.com/)** as the solver — an open-source
  Nastran-compatible FEA solver

Wrapping that pipeline is an [MCP](https://modelcontextprotocol.io/) server
(`scripts/mcp_server.py` in the repo) that exposes it as tool calls: load a
model, patch its case control, run the solver, pull peak stresses, render a
screenshot. That's what lets a client like Claude drive the whole thing
conversationally instead of me running scripts by hand.

## The case study: NASA's Common Research Model wingbox

Toy models are easy. To actually stress-test the pipeline (and be honest
about where it breaks), I used a real, publicly-licensed aerospace
structure: the wingbox from NASA's
[Common Research Model](https://commonresearchmodel.larc.nasa.gov/fem-file/wingbox-fem-files/)
— a full-scale semi-span wingbox with 50+ ribs, dual spars, and stringers,
isotropic aluminum, built from MAT1/CQUAD4/CBAR cards. NASA publishes it
for anyone to use without restriction, and it's genuinely MYSTRAN-compatible
bulk data — a good, unglamorous stand-in for the kind of model I'd actually
see at work.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_overview_iso.png" alt="Isometric view of the full NASA CRM wingbox mesh, showing depth and taper" style="max-width:100%;">

*Isometric view, rendered straight from the BDF via `render_model_view` — no
CAD tool involved: the boxy, multi-cell root, where ribs, spars, and
stringers are all packed close together, tapering to a thin, simple tip.*

It wasn't quite plug-and-play, though, and the two snags are worth calling
out because they're the kind of thing that separates "the pipeline ran" from
"the pipeline actually works":

**The case control was written for a different solver.** NASA's bulk data
is standard Nastran, but the case control section was authored for Altair
OptiStruct (`ANALYSIS MODES` / `ANALYSIS STATICS`, no `SOL`/`CEND`) across
two subcases -- a normal-modes extraction and a "GVW" statics run -- and
MYSTRAN doesn't understand that syntax. The `patch_case_control` tool
detects the missing `SOL`/`CEND` header, scans for the handful of request
keywords it actually knows about (`SPC`, `LOAD`, `DISPLACEMENT`, `STRESS`,
and a few others), and rebuilds a single flat `SOL 101`/`CEND` block from
whatever values it finds -- which means the two original subcases collapse
into one merged case control. That's fine here, since GVW statics is the
subcase that actually matters and `SOL 101` doesn't do normal modes anyway,
but it's a deliberate scope limit of this patch recipe, not a general
OptiStruct-to-MYSTRAN translator -- a deck relying on multiple subcases each
needing their own distinct SPC/LOAD combination would need a smarter patch.

**I evaluated a second candidate dataset and rejected it for a real solver
gap.** The University of Michigan's uCRM model (CC BY 4.0) was the other
option, but its PSHELL cards each reference four independent MAT2
materials — membrane, bending, shear, and membrane-bending coupling — to
represent smeared stiffened-panel properties. MYSTRAN's PSHELL implementation
rejects a nonzero MID4 (the coupling term). That's not a bug in this
pipeline; it's a genuine capability gap in an open-source solver versus a
commercial one, and it's exactly the kind of thing worth documenting rather
than quietly working around by switching to a more convenient model.

### MYSTRAN: background and where it falls short of commercial Nastran

Every solved F06 in this project carries the same attribution line:

```
MYSTRAN developed by Dr Bill Case
*** Please report any problems to mystransolver@gmail.com ***
```

MYSTRAN is a community-maintained, open-source Nastran-compatible solver
([source/releases](https://github.com/MystranSolver/MYSTRANSolver)) --
free to use, and genuinely reads/writes Nastran bulk-data syntax, which is
exactly why it fits this project's open-source-only constraint. It's worth
being upfront, though, about what "Nastran-compatible" means in practice
here: MYSTRAN implements a meaningful but *bounded* subset of what
commercial MSC/NX Nastran do. This case study only exercises `SOL 101`
(linear statics); MYSTRAN also supports normal modes, but the deep bench of
solution sequences commercial Nastran ships -- nonlinear statics/dynamics,
transient and frequency response, thermal, aeroelasticity, design
optimization, and a much broader materials/element library -- either isn't
there or isn't as complete. That's a reasonable trade for a
volunteer-maintained open-source project, not a criticism of it, but it's
the kind of thing worth knowing before assuming any given commercial-Nastran
deck will "just work" on the open-source stack.

The concrete example already surfaced above (the uCRM/MAT2 rejection) is
one instance of that boundary: MYSTRAN's `PSHELL` implementation rejects a
nonzero `MID4` (the membrane-bending coupling term used to smear a
stiffened panel into one equivalent anisotropic shell property). Commercial
Nastran supports the fully coupled form; MYSTRAN currently doesn't. It's a
real capability gap, not a bug in this pipeline -- and exactly the kind of
gap worth checking for before committing to a model, rather than
discovering it after a solve fails.

## What's actually being applied: loads and boundary conditions

Before trusting any stress number, the first thing a stress engineer
actually wants to know is what's constraining and loading the model --
not just "it solved." So the pipeline has a tool for exactly that question:
`describe_loads_and_boundary_conditions`, which reads a BDF's SPC/SPC1
(following SPCADD combinations, if any) and FORCE/MOMENT-type cards
(following LOAD combinations and their scale factors) and summarizes them
per subcase, instead of leaving you to grep through thousands of bulk-data
lines by hand.

On the CRM wingbox's static "GVW" subcase, it reports 196 constrained nodes
and 12,238 FORCE cards -- but "196 nodes" and "12,238 cards" don't say
*where* on the structure those actually sit, so it's worth cross-checking
against the nodes' physical coordinates rather than taking the counts at
face value.

- **Boundary conditions (SPC set 2) sit at the root joint, not spread along
  the span.** All 196 constrained nodes fall within the first ~120 in of
  the wing's ~1,151 in semi-span, and they split into two distinct rib
  stations rather than one: 140 nodes exactly at the symmetry-plane root
  rib (Y ≈ 0) are fixed in all three translations (DOF `123`), and a
  further 56 nodes at a second rib roughly 120 in outboard (Y ≈ 120) are
  fixed in Z-translation only (DOF `3`). That's a two-rib support scheme --
  full fixity at the true root, plus an additional vertical-only restraint
  one rib bay further out -- not a single idealized wall. No rotational
  DOF is constrained anywhere, consistent with a shell/bar idealization
  that doesn't rely on rotational stiffness at its supports.
- **Loads (LOAD set 3) are not an aerodynamic pressure on the skin --
  they're spread almost uniformly across the entire structure.** Every one
  of the 12,238 FORCE cards carries the identical magnitude, 20.41 lbf
  (90.8 N), in the +Z direction, at a different grid point. Cross-checking
  which elements those loaded nodes actually belong to shows the same
  ~88-89% node coverage on *every* named component -- lower skin, upper
  skin, ribs, shear webs, spars, and stringers alike -- not concentrated on
  the upper/lower skin panels the way a real aerodynamic pressure map
  would be (which would only touch the outer mold line, and would scale
  with each panel's local area rather than being identical everywhere).
  That points to this being a distributed, mass-proportional load standing
  in for the aircraft's own structural weight -- consistent with the "GVW"
  (gross weight) label -- rather than a modeled external pressure
  distribution. Summed, the resultant is **249,777.6 lbf (1,111.0 kN)**,
  purely vertical, zero net moment contribution from any single card's
  direction.

That last number is a good sanity check in its own right: a wing structure
under a uniform upward load in the hundred-thousand-pound range is exactly
the shape of load you'd expect from a "GVW" (gross weight) static case, and
seeing it fall cleanly out of summing 12,238 individual FORCE cards is a
much stronger confidence check than just watching MYSTRAN exit cleanly.

### Results

With the case control patched, MYSTRAN solves the static "GVW" subcase
cleanly.

#### Tip displacement

Tip displacement: **~159.7 in (4,056 mm)**, at node 9103 — a lot, but this
is a semi-span research model under a design GVW load case, not a
certified aircraft, so treat the absolute number as a sanity check on the
pipeline rather than a design conclusion.

A bare number doesn't convey *how* the structure is deflecting, though --
whether it's smooth bending, concentrated near the tip, or something odder.
`render_stress_contour` isn't limited to von Mises stress; its `result`
parameter also accepts `"displacement"`, coloring by nodal translational
displacement magnitude instead:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_displacement_iso.png" alt="Displacement magnitude contour on the NASA CRM wingbox, showing smooth bending from root to tip" style="max-width:100%;">

*Displacement magnitude contour (`render_stress_contour(..., result="displacement")`)
— smooth, monotonic bending from an essentially-fixed root (blue, ~0 in) to
the same 159.7 in peak at node 9103 (orange) that the bare number above
reports. No local kinks or discontinuities, which is itself a useful sanity
check: a real modeling error (an unintended pin joint, a missing
constraint) often shows up as a visible jump in a displacement contour
before it ever shows up as a wrong number.*

#### Stress contour

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_stress_iso.png" alt="Von Mises stress contour on the NASA CRM wingbox, camera aimed at the governing stress element" style="max-width:100%;">

*Von Mises stress contour, rendered directly from the solved model via
`render_stress_contour` — no manual screenshot, no commercial post-processor.
The camera here isn't a fixed preset: it looks up the governing (highest
von Mises) element and points straight down its outward face normal, so
that element is guaranteed to be visible and unobstructed rather than
potentially hidden behind other geometry.*

#### Peak stress by component

Peak stresses are reported per structural component and per element type,
deliberately kept separate rather than blended into one number -- bar
direct stress and plate von Mises aren't the same physical quantity, and a
single model-wide "peak stress" doesn't say which part of the structure is
actually driving it:

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

The Stiffeners row's stress measure is worth being precise about, since
"peak direct stress" on its own doesn't say what it actually is.
`get_max_stress` reports whichever of a bar's direct-stress columns
(axial, bending at four cross-section recovery points, or the combined
axial+bending extreme) has the largest magnitude, at whichever end (A or
B) it occurs -- not a single fixed quantity. For this governing element,
that turns out to be pure **axial** stress: every bending-stress column
(`s1a`-`s4a`, `s1b`-`s4b`) is exactly zero in the solved F06, consistent
with this model's stringers/caps behaving as pin-ended truss members
under this load case rather than beams in bending. Axial stress has no
"end" (it doesn't vary along a bar with no distributed axial load) -- a
different model, or a different governing element in this one, could just
as easily land on bending at end A or B instead, and the tool reports that
distinction rather than hiding it behind one blended number.

Two things worth noting that aren't obvious from the table alone: the
overall model-wide peak per element type (39,983.7 psi CQUAD4, 32,980.1 psi
CBAR, 2,794.4 psi CTRIA3) each live in a *different* component -- lower
skin, stiffeners, and upper skin respectively -- not all in the same
"worst" part of the structure. And the lower skin, despite carrying the
single highest stress in the whole model, isn't uniformly the worst
component: its own CTRIA3 peak (1,707.5 psi) is lower than the upper
skin's (2,794.4 psi). A single "peak stress" number for the whole model
tells you the worst point; it doesn't tell you which components are
actually driving that, and this table is the difference.

## The demo: driving it conversationally

The point of wrapping this in MCP tools is that the workflow becomes a
conversation instead of a sequence of terminal commands. In practice, a
session breaks down into five steps.

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

That last step is the newest piece of the pipeline: named-group isolation.
The NASA download ships a `.ses` file (a Patran/HyperMesh session file, not
Nastran format) defining named element groups — ribs, spars, skins,
stringers. A small parser (`ses_groups.py`) reads those group definitions,
and the render tools can hide or isolate a group by name, auto-framing the
camera on whatever's left:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_ribs_isolated.png" alt="Ribs isolated from the rest of the wingbox assembly, fanned out for readability" style="max-width:100%;">

*All 6,220 rib elements, isolated from the other ~29,000 elements in the
assembly. A straight-on view here would perfectly overlap every parallel
rib into one; `camera="auto"` instead aims for their shared face normal,
tilted enough to fan them out so each one is individually distinguishable.*

Isolating a sub-component is far more useful with its own stress contour on
top, not just bare geometry:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_ribs_stress.png" alt="Von Mises stress contour on just the isolated ribs" style="max-width:100%;">

*The same isolated ribs, now colored by von Mises stress —
`render_stress_contour(..., isolate_groups=["RIBS"])`. The governing
element here (17,884 psi / 123.3 MPa) is a genuinely different,
rib-specific peak, not the model-wide one from the full view above. The
mechanics behind isolating a group like this cleanly are covered next.*

## Isolating results by component

Named-group isolation is only useful if postprocessing actually respects
what kind of group it's looking at, so it's worth explaining how
`isolate_groups` handles a named group once it's been resolved to a set of
elements:

- **Geometry and results get trimmed together.** Isolating a subset of the
  model for `render_stress_contour` doesn't just filter the displayed
  geometry -- it also trims the OP2 results file down to exactly that same
  element set before pyNastranGUI loads it, so geometry and results stay
  the same size instead of pairing a filtered-down view with the
  full-model results.
- **Bar elements don't get a von Mises fringe.** CBARs report axial and
  bending direct stress, not von Mises -- there's no real per-element von
  Mises value to color by for an all-bar group like `Stiffeners` (14,134
  CBAR elements). The tool checks whether a genuine plate von Mises result
  exists in what's being loaded and skips the fringe entirely if not,
  rather than rendering a value that isn't physically meaningful.
- **Non-element groups are called out explicitly.** Not every named group
  in the `.ses` file is made of elements -- `LUMPED_MASS` is `CONM2` mass
  points, tracked separately from the regular element set in the deck.
  Isolating it resolves to zero renderable elements, and that's reported
  as a clear, specific message rather than attempted as a render.

Here's every real (non-mass) group in the model, each with its own
correctly zoomed, correctly framed stress contour — the actual point of
all of this:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_skin_lwr_stress.png" alt="Von Mises stress contour on the lower skin panel" style="max-width:100%;">

*Lower skin (2,322 CQUAD4 elements) — this is where the model-wide peak
actually lives: 39,983.7 psi (275.7 MPa) on element 2854, the same number
`get_max_stress` reported for the whole model back at the top of this
post.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_skin_upr_stress.png" alt="Von Mises stress contour on the upper skin panel" style="max-width:100%;">

*Upper skin (2,322 CQUAD4 elements) — a different, lower peak (38,947.6
psi / 268.5 MPa), with its own distinct hot spots.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_shearwebs_stress.png" alt="Von Mises stress contour on the shear webs" style="max-width:100%;">

*Shear webs (8,880 elements) — peak 30,575.1 psi (210.8 MPa).*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_spars_lete_stress.png" alt="Von Mises stress contour on the leading- and trailing-edge spars" style="max-width:100%;">

*Leading- and trailing-edge spars (1,611 elements) — two distinct spar
runs, each with its own stress pattern, peak 34,046.9 psi (234.7 MPa).*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_stiffeners_stress.png" alt="Isolated stiffener elements, uncolored since bars have no von Mises value" style="max-width:100%;">

*Stiffeners (14,134 CBAR elements) — no fringe, correctly: bars don't have
a von Mises value (see `get_max_stress`'s `max_stress` vs `von_mises`
distinction), so this shows geometry only rather than pretending otherwise.
This is the all-bar group the von-Mises check above exists for.*

For this group specifically, the relevant 1D-element output is **axial**
stress, not bending: `get_max_stress` reports the governing stiffener
(element 1559935) at 32,980.1 psi, `component: "axial"`, and every
bending-recovery-point column (`s1a`-`s4a`, `s1b`-`s4b`) is exactly zero
for it in the solved F06 -- consistent with these stringers/caps behaving
as pin-ended truss members under this load case. A true per-element color
fringe for that value isn't rendered here, deliberately: pyNastranGUI's
own bar-stress fringing is a GUI-synthesized pseudo-vonMises value, not a
direct read of the axial-stress column, so extending the same fringe
mechanism to bars would trade accuracy for comparatively little payoff
over the number `get_max_stress` already reports precisely.

Every one of these came from the exact same tool call, just swapping which
group name goes into `isolate_groups`. The peak-stress number for each of
these components -- same `get_max_stress` call, just against that group's
trimmed OP2 instead of the full model's -- is what's tabulated in the
"Peak stress by component" table earlier in this post.

## Honest caveats

In the spirit of not overselling this: a few things this pipeline
explicitly does **not** do.

- **This isn't a certified stress-substantiation process.** Real
  aerospace sign-off needs allowables, buckling and fatigue checks, hand-calc
  cross-verification, and a human engineer's judgment on top of raw FEA
  output. This pipeline gets you from geometry to peak stress fast; it
  doesn't replace the engineering judgment layered on top.
- **Rendering needs an active desktop session.** The visualization tools
  script a real pyNastranGUI window — `QT_QPA_PLATFORM=offscreen` breaks
  VTK's OpenGL context on Windows, so this is non-interactive, not
  headless. Fine for a local workflow or a CI runner with a display; not
  something you'd run on a bare server today.
- **MYSTRAN has real capability gaps** versus commercial Nastran, like the
  PSHELL/MID4 limitation above. Worth checking before assuming any given
  model will just run on the open-source stack.

## What's next

This was the first end-to-end pass: load, patch, solve, extract, visualize,
all conversational, all in native Nastran format, all open-source. The
[repo](https://github.com/ai-for-engineering/nastran-fea) has the full
implementation, tests, and backlog if you want to dig in or reproduce it
yourself.
