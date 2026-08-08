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
> **Claude:** *(calls `render_stress_contour`, then `render_model_view`
> with `isolate_groups=["RIBS"]`)* — two renders follow.

That last step is the newest piece of the pipeline: named-group isolation.
The NASA download ships a `.ses` file (a Patran/HyperMesh session file, not
Nastran format) defining named element groups — ribs, spars, skins,
stringers. A small parser (`ses_groups.py`) reads those group definitions,
and the render tools can hide or isolate a group by name, auto-framing the
camera on whatever's left:

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/wingbox_ribs_isolated.png" alt="Ribs isolated from the rest of the wingbox assembly" style="max-width:100%;">

*All 6,220 rib elements, isolated from the other ~29,000 elements in the
assembly, camera auto-framed on the isolated subset. (Color here is by node
ID — an artifact of the default view when there's no stress result loaded,
not a result itself.)*

## Honest caveats

In the spirit of not overselling this: a few things this pipeline
explicitly does **not** do, and one thing it does slower than I'd like.

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
- **Isolating a small subset of a large model while loading full-model
  stress results is slow enough to be impractical right now** — it can
  hang rather than complete in a reasonable time. The workaround is to
  isolate geometry only (`render_model_view`, no results) when you need a
  tight view on a small group, and use the full model for stress contours.
- **MYSTRAN has real capability gaps** versus commercial Nastran, like the
  PSHELL/MID4 limitation above. Worth checking before assuming any given
  model will just run on the open-source stack.

## What's next

This was the first end-to-end pass: load, patch, solve, extract, visualize,
all conversational, all in native Nastran format, all open-source. The
[repo](https://github.com/ai-for-engineering/nastran-fea) has the full
implementation, tests, and backlog if you want to dig in or reproduce it
yourself.
