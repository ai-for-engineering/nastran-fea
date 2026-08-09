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

It wasn't quite plug-and-play, though, and the two snags are worth calling
out because they're the kind of thing that separates "the pipeline ran" from
"the pipeline actually works":

**The case control was written for a different solver.** NASA's bulk data
is standard Nastran, but the case control section was authored for Altair
OptiStruct (`ANALYSIS MODES` / `ANALYSIS STATICS`, no `SOL`/`CEND`). MYSTRAN
doesn't understand that syntax. The `patch_case_control` tool detects a
missing `SOL`/`CEND` header and rebuilds one, carrying over whatever
SPC/LOAD/DISPLACEMENT/STRESS requests were already present in the original
file rather than guessing at them.

**I evaluated a second candidate dataset and rejected it for a real solver
gap.** The University of Michigan's uCRM model (CC BY 4.0) was the other
option, but its PSHELL cards each reference four independent MAT2
materials — membrane, bending, shear, and membrane-bending coupling — to
represent smeared stiffened-panel properties. MYSTRAN's PSHELL implementation
rejects a nonzero MID4 (the coupling term). That's not a bug in this
pipeline; it's a genuine capability gap in an open-source solver versus a
commercial one, and it's exactly the kind of thing worth documenting rather
than quietly working around by switching to a more convenient model.

### Case control management, in more depth

The one-liner above ("the case control section was authored for a different
solver") is easy to skim past, so it's worth actually walking through what
case control is and what the patch does to it.

Case control is the part of a Nastran deck that sits above the bulk data
and says what to *do* with the model it describes: which SPC (constraint)
set and which LOAD set apply, which subcase(s) to run, and what results to
output (`DISPLACEMENT`, `STRESS`, and so on). The bulk data defines the
model; case control tells the solver which pieces of it to actually use for
a given run. The NASA CRM deck ships with two subcases:

```
SUBCASE 1
  LABEL Modes_Rigid
  ANALYSIS MODES
  SPC = 2
  METHOD(STRUCTURE) = 1
SUBCASE 2
  LABEL GVW
  ANALYSIS STATICS
  SPC = 2
  LOAD = 3
```

`ANALYSIS MODES`/`ANALYSIS STATICS` and the missing `SOL`/`CEND` header are
OptiStruct syntax -- MYSTRAN expects `SOL 101` (linear statics) plus a
`CEND`-delimited case control block using `=`-style requests instead. So
`patch_case_control` doesn't try to translate OptiStruct's syntax
line-for-line; it takes a narrower, more honest approach: scan every header
line for the handful of request keywords it actually knows about
(`SPC`, `MPC`, `LOAD`, `DISPLACEMENT`, `STRESS`, `SPCFORCES`, `ECHO`),
keep whatever values it finds (falling back to sensible defaults for
`ECHO`/`DISPLACEMENT`/`STRESS` if a value wasn't present), and emit one flat
`SOL 101`/`CEND` block from them:

```
SOL 101
CEND
ECHO = NONE
SPC = 2
LOAD = 3
DISPLACEMENT = ALL
STRESS = ALL
```

Worth being explicit about what that means: **the two original subcases
collapse into one.** Whatever `SPC`/`LOAD` values were present anywhere in
the original header get merged into a single global case control -- there's
no attempt to preserve multiple subcases or run the `Modes_Rigid` normal
modes extraction (`ANALYSIS MODES`/`METHOD(STRUCTURE)`) at all. For this
case study that's fine, since GVW statics is the subcase we actually care
about and SOL 101 doesn't do normal modes in the first place -- but it's a
real, deliberate scope limit of this patch recipe, not a general
OptiStruct-to-MYSTRAN case-control translator. A deck relying on multiple
distinct subcases each needing their own SPC/LOAD combination would need a
smarter patch than this one.

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

### Sizing up the model

"50+ ribs, dual spars, and stringers" is easy to write and hard to picture.
Before getting into results, it's worth actually looking at what this
model is -- rendered straight from the BDF via `render_model_view`, no
CAD tool involved, at a few different angles chosen to reveal different
things about it:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_overview_top.png" alt="Top-down planform view of the full NASA CRM wingbox mesh" style="max-width:100%;">

*Top (`camera="top"`) -- the planform: sweep, taper, and just how dense the
shell mesh actually is toward the root, where the structure carries the
most load.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_overview_iso.png" alt="Isometric view of the full NASA CRM wingbox mesh, showing depth and taper" style="max-width:100%;">

*Iso (`camera="iso"`) -- the same structure with actual depth: the boxy,
multi-cell root (where ribs, spars, and stringers are all packed close
together) tapering to a thin, simple tip.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_overview_side.png" alt="Edge-on side view of the full NASA CRM wingbox mesh, showing it as a thin tapering profile" style="max-width:100%;">

*Side (`camera="side"`) -- edge-on, the wingbox all but disappears into a
thin curved sliver. That's not a rendering problem; it's a real, useful
fact about the geometry -- a wingbox is, structurally, a thin-walled
shell box, and this view is the most honest picture of just how thin.*

Also worth showing what *doesn't* add information here: `camera="front"`
(pyNastranGUI's un-rotated default view) turns out to look almost
identical to the top view above, just rolled and re-zoomed slightly. For a
structure this flat -- span vastly exceeding both chord and thickness --
"front" and "top" end up looking down nearly the same axis. That's a
genuine property of this model's proportions, not a bug in the camera
logic; a boxier structure (a fuselage section, say) would actually
differentiate the two.

## What's actually being applied: loads and boundary conditions

Before trusting any stress number, the first thing a stress engineer
actually wants to know is what's constraining and loading the model --
not just "it solved." So the pipeline has a tool for exactly that question:
`describe_loads_and_boundary_conditions`, which reads a BDF's SPC/SPC1
(following SPCADD combinations, if any) and FORCE/MOMENT-type cards
(following LOAD combinations and their scale factors) and summarizes them
per subcase, instead of leaving you to grep through thousands of bulk-data
lines by hand.

On the CRM wingbox's static "GVW" subcase, it reports:

- **Boundary conditions (SPC set 2, 196 constrained nodes):** 140 nodes
  fixed in all three translations (DOF `123`), 56 fixed in Z-translation
  only (DOF `3`). No rotational DOF is constrained anywhere in the model --
  consistent with a shell/bar idealization that doesn't rely on rotational
  stiffness at its support nodes, rather than a literal built-in-cantilever
  wall.
- **Loads (LOAD set 3, 12,238 FORCE cards):** every single card carries the
  identical magnitude, 20.41 lbf (90.8 N), applied in the +Z direction at a
  different grid point -- a uniform nodal-load approximation of a
  distributed pressure/inertia load rather than a handful of concentrated
  point loads. Summed, the resultant is **249,777.6 lbf (1,111.0 kN)**,
  purely vertical, zero net moment contribution from any single card's
  direction.

That last number is a good sanity check in its own right: a wing structure
under a uniform upward load in the hundred-thousand-pound range is exactly
the shape of load you'd expect from a "GVW" (gross weight) static case, and
seeing it fall cleanly out of summing 12,238 individual FORCE cards is a
much stronger confidence check than just watching MYSTRAN exit cleanly.

### Results

With the case control patched, MYSTRAN solves the static "GVW" subcase
cleanly. Peak stresses, per element type (deliberately reported separately —
bar direct stress and plate von Mises aren't the same physical quantity and
shouldn't be blended into one number):

| Element type | Peak stress | Component | Element | Notes |
|---|---|---|---|---|
| CQUAD4 (skin/shear web) | 39,983.7 psi (275.7 MPa) | von Mises | 2854 | Governing plate stress |
| CBAR (stringer/cap) | 32,980.1 psi (227.4 MPa) | axial | 1559935 | See note below |
| CTRIA3 | 2,794.4 psi (19.3 MPa) | von Mises | 29402 | |

The CBAR row's "Component" is worth being precise about, since "max
direct stress" on its own doesn't say what it actually is.
`get_max_stress` reports whichever of a bar's direct-stress columns
(axial, bending at four cross-section recovery points, or the combined
axial+bending extreme) has the largest magnitude, at whichever end (A or
B) it occurs -- not a single fixed quantity. For this governing element,
that turns out to be pure **axial** stress: every bending-stress column
(`s1a`-`s4a`, `s1b`-`s4b`) is exactly zero in the solved F06, consistent
with this model's stringers/caps behaving as pin-ended truss members
under this load case rather than beams in bending. Axial stress has no
"end" (it doesn't vary along a bar with no distributed axial load), which
is why the table doesn't list one -- a different model, or a different
governing element in this one, could just as easily land on bending at
end A or B instead, and the tool reports that distinction rather than
hiding it behind one blended number.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_stress_iso.png" alt="Von Mises stress contour on the NASA CRM wingbox, camera aimed at the governing stress element" style="max-width:100%;">

*Von Mises stress contour, rendered directly from the solved model via
`render_stress_contour` — no manual screenshot, no commercial post-processor.
The camera here isn't a fixed preset: it looks up the governing (highest
von Mises) element and points straight down its outward face normal, so
that element is guaranteed to be visible and unobstructed rather than
potentially hidden behind other geometry.*

### Tip displacement

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
rib-specific peak, not the model-wide one from the full view above.
Getting this working reliably took a real fix, covered next.*

## A real bug, and how it got fixed

Isolating a small subset of a large model while also loading full-model
stress results used to just hang — pairing a filtered-down geometry (a
few thousand elements) with the ORIGINAL, untrimmed OP2 (results for all
~35,000) sent pyNastranGUI down a path that didn't complete even after
240 seconds. The fix wasn't to wait longer or hide more of the model in
the GUI itself (also confirmed slow, separately) — it was to trim the OP2
file itself, in Python, down to exactly the elements being isolated,
before pyNastranGUI ever sees it. Geometry and results end up the same
size, the mismatch that caused the slow path disappears, and the same
case that wouldn't finish in 240 seconds now loads in about 12.

That fix was verified against one group (the ribs). Before trusting it,
I rendered every named group in the model's `.ses` file the same way —
which turned up two more real bugs, both specific to element types the
ribs test hadn't exercised:

- **An all-bar group (`Stiffeners`, 14,134 CBAR elements) hung too**, even
  with the OP2 already trimmed. pyNastranGUI turned out to synthesize its
  own bar-stress-derived "vonMises" case internally even though CBARs don't
  have a true von Mises value, and applying that synthesized case to a
  large all-bar selection was the actual slow path. Since a bar element was
  never going to get a meaningful von Mises fringe anyway, the fix was to
  stop trying: check first whether a real plate von Mises result exists in
  what's being loaded, and skip the fringe attempt entirely if not.
- **A mass-point group (`LUMPED_MASS`) isn't made of elements at all** —
  those IDs are `CONM2` mass points, tracked separately from the regular
  elements in the deck. Isolating it matched zero real elements, which
  produced a technically-empty OP2 that pyNastran's own reader rejected as
  a fatal error. Now it's caught up front with a clear message instead.

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
This is the group that exposed the bar-stress hang above.*

For this group specifically, the relevant 1D-element output is **axial**
stress, not bending: `get_max_stress` reports the governing stiffener
(element 1559935) at 32,980.1 psi, `component: "axial"`, and every
bending-recovery-point column (`s1a`-`s4a`, `s1b`-`s4b`) is exactly zero
for it in the solved F06 -- consistent with these stringers/caps behaving
as pin-ended truss members under this load case. A true per-element color
fringe for that value isn't rendered here, deliberately: pyNastranGUI's
own bar-stress fringing is the same GUI-synthesized pseudo-vonMises path
that caused the hang above, not a direct read of the axial-stress column,
so extending the same fringe mechanism to bars would reintroduce that risk
for comparatively little payoff over the number `get_max_stress` already
reports precisely.

Every one of these came from the exact same tool call, just swapping which
group name goes into `isolate_groups`.

### Peak stress by component

Pulling all of those isolated-group numbers into one place -- each row is
the same `get_max_stress` call, just against that group's trimmed OP2
instead of the full model's:

| Component | Elements | Governing type | Peak stress | Element |
|---|---|---|---|---|
| Skin, lower | 2,322 | CQUAD4 (von Mises) | 39,983.7 psi (275.7 MPa) | 2854 |
| Skin, upper | 2,322 | CQUAD4 (von Mises) | 38,947.6 psi (268.5 MPa) | 1587 |
| Skin, upper | 2,322 | CTRIA3 (von Mises) | 2,794.4 psi (19.3 MPa) | 29402 |
| Skin, lower | 2,322 | CTRIA3 (von Mises) | 1,707.5 psi (11.8 MPa) | 29405 |
| Spars (LE/TE) | 1,611 | CQUAD4 (von Mises) | 34,046.9 psi (234.7 MPa) | 16107 |
| Stiffeners | 14,134 | CBAR (axial) | 32,980.1 psi (227.4 MPa) | 1559935 |
| Shear webs | 8,880 | CQUAD4 (von Mises) | 30,575.1 psi (210.8 MPa) | 26459 |
| Ribs | 6,220 | CQUAD4 (von Mises) | 17,884.0 psi (123.3 MPa) | 20740 |

Two things worth noting that aren't obvious from the per-group renders
alone: the model-wide peaks reported at the top of this post (39,983.7 psi
CQUAD4, 32,980.1 psi CBAR, 2,794.4 psi CTRIA3) each live in a *different*
component -- lower skin, stiffeners, and upper skin respectively -- not
all in the same "worst" part of the structure. And the lower skin, despite
carrying the single highest stress in the whole model, isn't uniformly the
worst component: its own CTRIA3 peak (1,707.5 psi) is lower than the upper
skin's (2,794.4 psi). A single "peak stress" number for the whole model
tells you the worst point; it doesn't tell you which components are
actually driving that, and this table is the difference.

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
