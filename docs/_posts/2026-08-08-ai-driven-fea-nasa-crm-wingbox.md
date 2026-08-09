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

| Element type | Peak stress | Element | Notes |
|---|---|---|---|
| CQUAD4 (skin/shear web) | 39,983.7 psi (von Mises) | 2854 | Governing plate stress |
| CBAR (stringer/cap) | 32,980.1 psi (max direct) | 1559935 | |
| CTRIA3 | 2,794.4 psi (von Mises) | 29402 | |

Tip displacement: **~159.7 in**, at node 9103 — a lot, but this is a
semi-span research model under a design GVW load case, not a certified
aircraft, so treat the absolute number as a sanity check on the pipeline
rather than a design conclusion.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_stress_iso.png" alt="Von Mises stress contour on the NASA CRM wingbox, camera aimed at the governing stress element" style="max-width:100%;">

*Von Mises stress contour, rendered directly from the solved model via
`render_stress_contour` — no manual screenshot, no commercial post-processor.
The camera here isn't a fixed preset: it looks up the governing (highest
von Mises) element and points straight down its outward face normal, so
that element is guaranteed to be visible and unobstructed rather than
potentially hidden behind other geometry.*

## The demo: driving it conversationally

The point of wrapping this in MCP tools is that the workflow becomes a
conversation instead of a sequence of terminal commands. In practice, a
session looks like:

> **Me:** Load the CRM wingbox model and check it's valid.
> **Claude:** *(calls `load_model`)* — parsed cleanly: 35,489 elements,
> counts by type, no warnings.
>
> **Me:** The case control section is missing SOL/CEND, patch it.
> **Claude:** *(calls `patch_case_control`)* — rebuilt the header, preserved
> the existing SPC/LOAD/STRESS requests, wrote the patched deck.
>
> **Me:** Run it through MYSTRAN.
> **Claude:** *(calls `run_solver`)* — solved successfully, no fatal errors
> in the F06.
>
> **Me:** What's the peak stress, and where?
> **Claude:** *(calls `get_max_stress`)* — CQUAD4 governs at 39,983.7 psi
> on element 2854, subcase 1.
>
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
element here (17,884 psi) is a genuinely different, rib-specific peak, not
the model-wide one from the full view above. Getting this working reliably
took a real fix, covered below.*

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
actually lives: 39,983.7 psi on element 2854, the same number
`get_max_stress` reported for the whole model back at the top of this
post.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_skin_upr_stress.png" alt="Von Mises stress contour on the upper skin panel" style="max-width:100%;">

*Upper skin (2,322 CQUAD4 elements) — a different, lower peak (38,947.6
psi), with its own distinct hot spots.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_shearwebs_stress.png" alt="Von Mises stress contour on the shear webs" style="max-width:100%;">

*Shear webs (8,880 elements) — peak 30,575.1 psi.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_spars_lete_stress.png" alt="Von Mises stress contour on the leading- and trailing-edge spars" style="max-width:100%;">

*Leading- and trailing-edge spars (1,611 elements) — two distinct spar
runs, each with its own stress pattern, peak 34,046.9 psi.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_stiffeners_stress.png" alt="Isolated stiffener elements, uncolored since bars have no von Mises value" style="max-width:100%;">

*Stiffeners (14,134 CBAR elements) — no fringe, correctly: bars don't have
a von Mises value (see `get_max_stress`'s `max_stress` vs `von_mises`
distinction), so this shows geometry only rather than pretending otherwise.
This is the group that exposed the bar-stress hang above.*

Every one of these came from the exact same tool call, just swapping which
group name goes into `isolate_groups`.

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
