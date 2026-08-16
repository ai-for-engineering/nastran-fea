---
layout: post
title: "Teaching an AI to Build a Wingbox Mesh from Nothing but CAD Geometry"
date: 2026-08-16
author: Mohammed-Amine Bennaiem
excerpt: >-
  Part 2: starting from raw IGES CAD instead of a pre-built Nastran deck --
  meshing, welding, a real algorithm bug, and why the fix turned out to be
  parametric geometry with shared topology built in from the start.
---

**Part 2 of 2** in a series on AI-driven, open-source FEA.
[Part 1](https://ai-for-engineering.github.io/nastran-fea/2026/08/08/ai-driven-fea-nasa-crm-wingbox.html) covered
driving an *already-built* Nastran deck conversationally — load, patch,
solve, extract, visualize — across three real wing models. This post covers
a harder problem: there is no pre-built deck to start from, only raw CAD
geometry. Meshing it, connecting the pieces, and getting a solver to accept
the result surfaced a real algorithm bug and a deeper lesson about where
mesh connectivity actually has to come from.

## Contents

- [Rebuilding the wingbox from geometry alone](#rebuilding-from-geometry)
  - [Results](#rebuild-results)
  - [Visual inspection: does it actually look right?](#visual-inspection)
  - [Summary](#rebuild-summary)
- [The weld algorithm's real bug — and its real ceiling](#weld-bug-and-ceiling)
- [A parametric rebuild: geometry with shared topology by construction](#parametric-rebuild)
  - [The parametrization philosophy](#parametrization-philosophy)
  - [Real dimensions, not guessed](#real-dimensions)
  - [Building it: the fragment/mesh pipeline](#building-the-pipeline)
  - [Inspection checks vs. the original model](#parametric-inspection)
  - [What's still open](#parametric-still-open)
- [Conclusion](#conclusion)

## Rebuilding the wingbox from geometry alone {: #rebuilding-from-geometry}

Everything in Part 1 starts from NASA's own pre-built finite element deck. A
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

### Results {: #rebuild-results}

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

### Visual inspection: does it actually look right? {: #visual-inspection}

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
portrait shape" effect documented in Part 1 shows up identically on both
models, itself a small confirmation that the camera/framing logic
generalizes rather than being tuned to one mesh.*

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

### Summary {: #rebuild-summary}

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

## The weld algorithm's real bug — and its real ceiling {: #weld-bug-and-ceiling}

The spar/rib inspection above ends on a real, visible gap — but there's a
smaller, less visible one underneath it. Looking closely at the rebuild in
pyNastranGUI, intersecting parts don't always share nodes where they
physically meet. Worth chasing down properly rather than left as a footnote.

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

## A parametric rebuild: geometry with shared topology by construction {: #parametric-rebuild}

### The parametrization philosophy {: #parametrization-philosophy}

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

### Real dimensions, not guessed {: #real-dimensions}

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

### Building it: the fragment/mesh pipeline {: #building-the-pipeline}

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

### Inspection checks vs. the original model {: #parametric-inspection}

Span, chord, taper, sweep, dihedral, and rib stations match the original
by construction here — they were measured *from* it, not independently
derived, so matching those specific numbers isn't the interesting check.
The real questions: does the fragment/mesh pipeline actually connect
everything (answered above, quantitatively), and does the resulting shape
and rib pattern look right when rendered.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_compare_planform.png" alt="Side-by-side planform comparison between the original NASA CRM wingbox and the parametric rebuild" style="max-width:100%;">

*Planform silhouette, both models. Span, taper, sweep, and root cross-
section proportions line up. Two honest differences visible: the
original's real curved upper skin (already flagged as unmodeled in the
inspection above) versus the parametric rebuild's flat-panel idealization,
and a simpler tip/root closeout shape here versus the original's more
detailed cap structure.*

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

### What's still open {: #parametric-still-open}

No boundary conditions, load, or solve on this model yet — geometry and
mesh connectivity only, so far. The natural next step is the same
BC-reconstruction and GVW-load approach already used for the IGES-welded
rebuild, then a real MYSTRAN solve and comparison. Tracked in
[issue #61](https://github.com/ai-for-engineering/nastran-fea/issues/61).
Full technical detail on everything in this post — the weld-algorithm fix,
the CAD-topology research, and the parametric generator — is in
[issue #59](https://github.com/ai-for-engineering/nastran-fea/issues/59)
and [PR #60](https://github.com/ai-for-engineering/nastran-fea/pull/60).

## Conclusion {: #conclusion}

Two attempts at the same problem, and a clear ranking between them. Meshing
each IGES component independently and welding coincident nodes afterward
gets close -- same order of magnitude on tip displacement and peak stress
against a real, previously-validated result -- but has a hard structural
ceiling: 43-99% of near-boundary node pairs per component stay unwelded no
matter how the algorithm is tuned, because the source CAD never encoded
shared topology in the first place. Building the same wing's ribs, spars,
and skins as parametric surfaces in one fragmented Gmsh session instead --
using real dimensions measured from the original deck, not a generic
wingbox -- closes that gap completely: 0.0%, at the actual wing's real
scale and complexity.

The general lesson travels beyond this one wing: mesh connectivity is a
property of how the CAD was *authored*, not something a downstream
tolerance-based algorithm can fully recover. Prefer geometry sources with
shared topology by construction -- a parametric generator, or a real
assembly export with shared faces -- over independently-modeled
per-component files, whenever there's a choice.

What's not done yet: this parametric rebuild has no boundary conditions,
load, or solve -- geometry and connectivity only, tracked in
[issue #61](https://github.com/ai-for-engineering/nastran-fea/issues/61).
For the other half of this pipeline -- driving an already-built deck
through solve, stress extraction, and visualization conversationally --
see [Part 1](https://ai-for-engineering.github.io/nastran-fea/2026/08/08/ai-driven-fea-nasa-crm-wingbox.html).
The [repo](https://github.com/ai-for-engineering/nastran-fea) has the full
implementation, tests, and backlog.
