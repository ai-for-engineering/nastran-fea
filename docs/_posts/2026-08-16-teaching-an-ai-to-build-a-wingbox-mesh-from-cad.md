---
layout: post
title: "Teaching an AI to Build a Wingbox Mesh from Nothing but CAD Geometry"
date: 2026-08-16
author: Mohammed-Amine Bennaiem
excerpt: >-
  Part 2: two paths to a representative FEM of a real wingbox -- through an
  intermediate CAD step (the industry's usual path) versus direct
  parametrization of the geometry -- each tried and compared against a
  real, previously-validated model.
---

**Part 2 of 2** in a series on AI-driven, open-source FEA.
[Part 1](https://ai-for-engineering.github.io/nastran-fea/2026/08/08/ai-driven-fea-nasa-crm-wingbox.html) covered
driving an *already-built* Nastran deck conversationally — load, patch,
solve, extract, visualize. This post covers what has to happen to build
that deck in the first place: deliberately setting the pre-built one
aside and building an FE model of the same structure from nothing but its
geometry — then checking the result against NASA's own solved deck as
ground truth.

## Contents

- [The objective](#the-objective)
- [Two paths to a representative FEM](#two-paths-to-a-representative-fem)
- [Case study n°1: the CAD path — welding independently-meshed components](#case-study-n1-the-cad-path--welding-independently-meshed-components)
  - [Results](#results)
  - [Visual inspection: does it actually look right?](#visual-inspection-does-it-actually-look-right)
  - [Summary](#summary)
  - [The real ceiling: why welding alone can't fully connect this CAD](#the-real-ceiling-why-welding-alone-cant-fully-connect-this-cad)
- [Case study n°2: the direct-parametrization path — shared topology by construction](#case-study-n2-the-direct-parametrization-path--shared-topology-by-construction)
  - [Real dimensions, not guessed](#real-dimensions-not-guessed)
  - [Connectivity result: 0.0% gap](#connectivity-result-00-gap)
  - [Inspection checks vs. the original model](#inspection-checks-vs-the-original-model)
  - [Pushing further: multi-spar, kink, and camber](#pushing-further-multi-spar-kink-and-camber)
  - [What's still open](#whats-still-open)
- [Conclusion](#conclusion)

## The objective

Given a real aircraft structure, build a finite element model that's
actually representative of it: correct topology (ribs, spars, and skins
genuinely connected where they meet, not just visually coincident),
correct properties, and a boundary-condition/load set a solver can run to
a physically meaningful result. Nothing about that objective is specific
to one modeling technique — NASA's CRM wingbox is the concrete structure
used throughout, but the question generalizes to any airframe.

## Two paths to a representative FEM

There are two fundamentally different ways to get from "a real structure"
to "an FE model of it":

1. **Through an intermediate CAD step — the path generally used across the
   industry.** Geometry is authored or received as CAD — surfaces
   representing each rib, spar, and skin, often modeled and exported
   independently — then meshed, and the pieces connected into one
   structure. NASA publishes exactly this kind of input for the CRM
   wingbox: five separate IGES CAD files, alongside — separately — the
   pre-built Nastran deck Part 1 already drives. **Case study n°1** below
   uses only the CAD half of that download, deliberately setting the
   existing deck aside, and checks the result against that deck as ground
   truth.
2. **Direct parametrization of the geometry — skipping CAD authoring
   entirely.** Ribs, spars, and skins are generated as parametric
   surfaces — explicit functions of span, chord, sweep, and the rest —
   directly inside the meshing tool, so the structure's connectivity is a
   property the generator guarantees by construction, rather than
   something recovered from independently-modeled files afterward.
   **Case study n°2** below follows this path.

Same objective, one case study per path — and, as the results below show,
a real difference in how completely each path actually achieves it.

## Case study n°1: the CAD path — welding independently-meshed components

The CAD path, applied to the wingbox: mesh each of NASA's 5 IGES
components, then connect them into one structure. The textbook way to
combine several CAD files into one topologically exact
mesh is an OpenCASCADE boolean fragment (`gmsh.model.occ.fragment`) — tried
first, and abandoned after hitting real, reproducible tooling limits:
fragmenting just 2 of the 5 files (ribs + spars, 91 of ~470 total faces)
took 234 seconds and left sub-micron sliver edges that Gmsh's own
`healShapes()` couldn't reliably clean up. The approach used instead: mesh
each of the 5 components independently, then weld coincident nodes across
components within a distance tolerance — an approximate, tolerance-based
connection, not a mathematically exact shared curve.

Properties, boundary conditions, and the GVW load were reconstructed the
same way as the deck-driven case studies in Part 1 (SPC by Y-band, a
distributed Z-force sized to however many nodes the rebuilt mesh actually
has), and the result solves cleanly in MYSTRAN. A clean solve on its own
isn't proof the model is physically sound, though — the real check is
comparing the numbers and the shape against NASA's own solved reference,
below.

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

Same order of magnitude, with real modeling differences (below) accounting
for the rest. Stringers are excluded from the numeric comparison, not
silently cleaned up: it's the least reliable component in this rebuild by
construction (the one component where quad recombination failed and fell
back to an all-triangle mesh, a *back-calculated* rather than measured
thickness, and the sole owner of every one of the rebuild's 21 residual
poorly-connected nodes). Its peak stress stays in the millions of psi even
after excluding every element touching an unphysically-displaced node —
reporting a "cleaned" number anyway would overstate confidence in it.

### Visual inspection: does it actually look right?

Numbers can agree by coincidence. The more direct check: render both
models from matching camera angles and isolate the same structural
groups, side by side, and look.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_overview_planform.png" alt="Side-by-side planform view comparing the original and rebuilt NASA CRM wingbox meshes" style="max-width:100%;">

*Planform view, both models. Sweep, taper, and root cross-section all
line up. The rebuild's mesh is visibly denser everywhere -- 79,053
elements against 35,489, since `mesh_size` was chosen independently of
NASA's own mesh density.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_overview_iso.png" alt="Side-by-side isometric view comparing the original and rebuilt NASA CRM wingbox meshes" style="max-width:100%;">

*Isometric view -- the camera/framing logic from Part 1 generalizes to
this mesh too, without retuning.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_overview_top.png" alt="Side-by-side thickness-profile view comparing the original and rebuilt NASA CRM wingbox meshes, showing span versus thickness" style="max-width:100%;">

*Span-vs-thickness profile. The one overview angle with a real, visible
discrepancy: the original shows a distinctly cambered, curved upper
surface near the root, while the rebuild reads flatter and more box-like
along most of the span -- plausibly the rebuild's mesh size undersampling
the true NURBS curvature more than NASA's own finer mesh does.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_ribs.png" alt="Side-by-side isolated-ribs comparison between the original and rebuilt NASA CRM wingbox" style="max-width:100%;">

*Ribs isolated. The clearest match in the whole inspection -- same fan
pattern, same 58 rib stations, same taper, same root-to-tip spacing. Just
denser.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_spars.png" alt="Side-by-side isolated-spars comparison between the original and rebuilt NASA CRM wingbox, showing a dense shear-web comb structure in the original that is absent from the rebuild" style="max-width:100%;">

*Spars isolated -- the most significant discrepancy found in this
inspection. The original's "ShearWebs" group (8,880 elements) is a dense
comb of closely-spaced internal webs running between ribs, in addition to
the 2 main leading/trailing-edge spars (1,611 elements). The rebuild's
SPARS component contains only 3 clean, continuous spanwise webs
(front/mid/rear spar) and no periodic shear-tie structure at all. NASA's
downloadable IGES geometry for spars appears to only include the primary
continuous webs, not the rib-spaced shear ties the original FE model
actually has -- a genuine structural coverage gap in the source CAD, not a
rendering artifact, and a plausible partial explanation for the elevated
peak stress at spars/ribs above: less internal stiffening structure in the
rebuild means load concentrates differently than in the original.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_skins.png" alt="Side-by-side isolated-skins comparison between the original and rebuilt NASA CRM wingbox" style="max-width:100%;">

*Skins isolated. Shape matches well; the rebuild's mesh is visibly less
regular -- an unstructured, quad-recombined-from-triangles pattern versus
the original's clean structured grid. A meshing-*style* difference, not a
shape discrepancy.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_stringers.png" alt="Side-by-side comparison of the original's CBAR stiffeners and the rebuild's shell-based stringers" style="max-width:100%;">

*Stiffeners (original, CBAR) vs. stringers (rebuild, shell) -- an already-
expected element-type difference, since the IGES download only provides
these as 2D midsurfaces. Beyond that, the rebuild shows some
crossing/convergence near the root that the original's cleaner fan
doesn't -- plausibly genuine design tapering, or a symptom of this
component's residual connectivity issues. Not disentangled here.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/rebuild_compare_rib_caps.png" alt="Rebuilt rib caps geometry, shown alone since the original model has no equivalent named group" style="max-width:100%;">

*Rib caps -- rebuild only; the original's named groups have no separate
entry for this component at all. Shown mainly to confirm the geometry is
sane: a closed perimeter loop around each of the 58 ribs, consistent with
what a rib-edge reinforcing flange should look like.*

### Summary

Three real, distinct kinds of discrepancy came out of this inspection:

1. **Expected, benign:** mesh density/regularity and the stringers'
   element-type change (shell vs. CBAR) -- both already understood before
   this inspection, now visually confirmed.
2. **A real geometric gap worth flagging:** the missing shear-web comb
   structure in the rebuilt spars -- 8,880 elements' worth of internal
   stiffening present in the original FE model with no counterpart in the
   downloaded IGES geometry. The inspection's most useful finding, and a
   plausible partial driver of the elevated peak-stress readings
   throughout the rebuild.
3. **Unresolved, flagged not chased further:** the flatter apparent camber
   near the root, and the stringer convergence near the root. Both have
   plausible benign explanations and neither was run down to a definitive
   cause here.

### The real ceiling: why welding alone can't fully connect this CAD

The spar/rib gap above is a missing-geometry problem. There's a separate,
subtler one underneath it: even where the CAD *does* cover a boundary,
intersecting parts don't always end up sharing nodes where they physically
meet. With a correctly distance-based weld (matching only genuinely
coincident node pairs, never two corners of the same source element),
**~23% of real near-boundary candidate pairs per component still stay
unweldable** at production tolerance — not a tolerance-tuning shortfall,
but because welding them would collapse a genuine element.

**The root cause is the CAD file, not the algorithm.** Industry
documentation (Ansys/SpaceClaim's own material on multibody parts) is
explicit: "shared topology is the only way to achieve a conformal mesh
where bodies meet," created by *imprinting* one part's boundary curve onto
its neighbor — before either is meshed, not recovered from bare
coordinates afterward. NASA's 5 IGES files are independently-authored,
"dumb" surface exports with no shared topology encoded at all — each
component modeled and exported on its own, so any apparent coincidence at
a rib/spar boundary is a geometric accident, not a topological fact.
Cross-checked against the original reference deck: it uses essentially
zero connector elements between components (20 `RBE3`, all for load/SPC
application, none tying ribs/spars/skins together) — meaning it achieves
connectivity via genuinely shared `GRID`s, which in turn means it was
built from geometry that already had shared topology, not independently-
modeled files welded together after the fact.

That reframes the question. Not "how do we mesh this CAD better" — "what
does the CAD need to look like for a conformal mesh to be possible at
all."

## Case study n°2: the direct-parametrization path — shared topology by construction

The direct-parametrization path, applied to the same wingbox: skip the
CAD step entirely and generate ribs, spars, and skins from *one shared
parametric definition* — rib/spar planes cut against a single
outer-mold-line surface — so a boundary curve is the same computed curve
reused on both sides, not two independent approximations of "the same"
edge that happen to almost line up. Real automated wingbox-generation
research works this way rather than meshing independently-authored
per-component CAD.

Applied here: build every rib, spar, and skin as a parametric surface
directly inside *one* Gmsh OpenCASCADE session — no export/re-import round
trip — then run the exact same `gmsh.model.occ.fragment` operation that
failed on the real IGES files. Proven first on a generic rectangular toy
wingbox (2 spars, N ribs, flat skins): **0.0% connectivity gap**,
fragment+mesh in under a second. That confirmed the *stack* isn't the
limitation, only the input data was — which justified spending more time
on the real planform.

### Real dimensions, not guessed

The toy wingbox proved the mechanism; it isn't the NASA CRM wing. Its real
planform was measured directly from the original solved deck — not copied
from generic published CRM aerodynamic parameters, which describe the full
aircraft OML, not necessarily this wingbox idealization:

| Parameter | Value | Source |
|---|---|---|
| Span | 1,151.32 in | bounding box, structural nodes only |
| Root / tip chord | 291.19 in / 85.31 in (taper 0.293) | Skin group, root/tip probes |
| Leading-edge sweep | 32.35° | leading-edge X shift, root to tip |
| Dihedral | ~6.4° | mid-depth Z shift, root to tip |
| Box depth | 18–20% local chord | thickness-axis range at 5 span stations |
| Front / rear spar position | ~0–11% / ~97–92% chord | `Spars_LETE` group, root-to-tip trend |
| Rib stations | 57 real stations, 0.0–1,146.5 in | connected-component analysis, `RIBS` group |

Getting a real rib count needed one methodological correction worth
noting: clustering `RIBS` group nodes by span position alone produces 314
spurious "stations" — noise from continuous node scatter within a single
rib, not 314 real ribs. Treating each rib as its own *connected component*
of the group's own element-adjacency graph resolves this cleanly to the
real number: 57 ribs, denser near the root and a crank region, settling to
a consistent spacing outboard.

Ribs are flat planes perpendicular to the span axis; spars are ruled quads
between their own root and tip corners; skins — generally not planar on a
tapered, swept wing — use a ruled-surface fit
(`gmsh.model.occ.addSurfaceFilling`) instead of a flat plane wherever a
planarity check on the four corners fails.

### Connectivity result: 0.0% gap

**0.0% connectivity gap** — 5,156 of 5,156 boundary nodes shared, zero
left over — at the real wing's actual scale and complexity (62 input
surfaces, 18,314 nodes, 19,821 elements), with fragment+mesh finishing in
1.4 seconds. The abandoned full-face fragment attempt on the real IGES
files took 234 seconds and still failed on just 2 of 5 components.

**The payoff, stated as a number:** the IGES-welded rebuild's own node
connectivity, even with correctly distance-based welding, still runs
43–99% unwelded per component pair (structural, not a tuning gap). This
parametric rebuild: 0.0%, on a real wing's real planform.

(These figures — 0.0%, 5,156 boundary nodes, 62 input surfaces — describe
the deliberately simplest version of the script: 2 spars, no kink, flat
skins. `spikes/parametric_crm_wingbox.py`'s current default configuration
is the extended one covered later in this post — [see "Pushing further"
below](#pushing-further-multi-spar-kink-and-camber) — and reproduces a
different, larger, and honestly non-zero number. The 2-spar/no-kink/
flat-skin numbers above remain reproducible by collapsing the script's
own `N_SPARS`/`PLANFORM_STATIONS`/camber constants back down; they're
kept here because this section is about proving the base mechanism, which
they still do.)

### Inspection checks vs. the original model

Span, chord, taper, sweep, dihedral, and rib stations match the original
by construction — they were measured *from* it, not independently
derived, so matching those specific numbers isn't the interesting check.
The real question is whether the resulting shape and rib pattern look
right when rendered.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_compare_planform.png" alt="Side-by-side planform comparison between the original NASA CRM wingbox and the parametric rebuild" style="max-width:100%;">

*Planform silhouette, both models. Span, taper, sweep, and root cross-
section proportions line up. Two honest differences visible: the
original's real curved upper skin (already flagged as unmodeled above)
versus the parametric rebuild's flat-panel idealization, and a simpler
tip/root closeout shape here versus the original's more detailed cap
structure.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_compare_ribs.png" alt="Side-by-side ribs comparison between the original NASA CRM wingbox and the parametric rebuild" style="max-width:100%;">

*Ribs isolated, both models. The fan pattern matches closely -- denser
near the root, spreading to a consistent spacing outboard -- confirming
the connected-component extraction got the real rib layout right, not
just the count.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_iso.png" alt="Isometric view of the parametric CRM wingbox rebuild, showing sweep, taper, and dihedral" style="max-width:100%;">

*Isometric view of the parametric rebuild alone -- sweep, taper, and the
measured ~6.4° dihedral all visible in one shot.*

**What's simplified here, stated plainly** (the base, deliberately-minimal
version covered so far in this section — see "Pushing further" below for
what changes after extending it):

- **Only 2 spars.** The original deck's `ShearWebs` group resolves (same
  connected-component analysis) to ~22 *more* continuous internal spanwise
  webs beyond the main front/rear spars — a genuinely multi-spar wingbox,
  not just two. Omitted to keep this proof of concept's complexity
  bounded — the same coverage gap already found in case study n°1.
- **Ribs are flat planes perpendicular to span.** Whether the real
  aircraft's ribs are angled/streamwise in places wasn't checked either
  way.
- **Skins carry no airfoil camber** — a flat-panel idealization, matching
  how this project's other wingbox models already idealize skins, not a
  new simplification introduced here.
- **The planform is a single root-to-tip linear taper/sweep/dihedral** —
  no kink, even though the rib-spacing pattern above already hints one
  exists around 120–324in.
- **No stringers/stiffeners.**

### Pushing further: multi-spar, kink, and camber

The case study above deliberately stopped at the simplest complexity that
still proved the mechanism. `spikes/parametric_crm_wingbox.py` was then
extended to close three of the gaps its own list just named — more spars,
a real spanwise kink, and a curved (cambered) skin — without chasing a
byte-for-byte reproduction of the real wing's full complexity (that stays
future work, alongside BC/load/solve, in
[issue #61](https://github.com/ai-for-engineering/nastran-fea/issues/61)).

**A real kink, not an invented one.** Probing the original solved deck the
same way as the root/tip numbers above, but at Y=224in — inside the
120–324in crank region the rib-spacing pattern had already flagged —
turned up a genuine sweep break: ~15.6° root-to-kink vs. ~36.2°
kink-to-tip (a less-swept inboard glove, more-swept outboard — a common
real wing planform feature, not manufactured to make a point).
`PLANFORM_STATIONS` generalizes the script from a single root-to-tip lerp
to a piecewise-linear interpolation across however many such stations are
given — 3, here (root, kink, tip).

**5 spars, not 2.** `SPAR_CHORDWISE_FRACS` is now a genuine list — front,
3 evenly-spaced internal spars, rear — each interpolated linearly
root-to-tip exactly like the original front/rear pair did. Still far
short of the original deck's own ~22 additional internal shear webs
(measured: 15 distinct clusters at just 10% span, spanning ~35–90% chord,
tapering to a single web by 90% span — a real, uneven, root-concentrated
distribution this evenly-spaced version doesn't attempt to match), but a
real multi-spar parameter now, not a hardcoded pair.

**A modest, engineered camber — and a real lesson in what "shared
topology by construction" actually requires.** The first attempt at
camber bulged each skin's interior via `addSurfaceFilling`'s interior
`pointTags`, leaving the flat boundary wire (and, so the reasoning went,
every rib/spar connection) untouched. Measured result: a **44.6%**
connectivity gap, on a config with only the camber axis turned on (2
spars, no kink) — confirmed to be camber specifically, not a refactor
regression, by isolating each new axis in turn (kink alone: 0.0%,
multi-spar alone: 0.0%, camber alone: 44.6%). The real cause: a rib's own
straight top/bottom edge had always relied on lying entirely inside the
flat skin's 2D interior — true for a flat surface, false the moment that
surface curves. Once curved, the embedding held only at the rib's two
spar-line endpoints, breaking connectivity across that rib's *entire*
chord width, for all 58 ribs simultaneously — a much bigger break than
the "internal spars might lose their cap" gap originally anticipated.

The fix restores "shared topology by construction" for camber too,
instead of leaning on gmsh's fragment to numerically detect coincidence
after the fact (which is what silently broke above): every rib's own
top/bottom boundary is now built as a spline through each spar's own
cambered corner point, and each skin panel — one per rib-to-rib gap now
(57 of them), not one per kink segment — reuses that *exact* spline
object as its own boundary, not a fresh, merely-close curve.

**The real, reported result:** N_SPARS=5, kink at Y=224in, camber 2.5%
top / 1.2% bottom of local chord — 182 input surfaces, 949 after
fragment, fragment+mesh in ~96s, 34,879 nodes / 38,662 elements, and a
**12.5% connectivity gap** (961 of 7,672 boundary nodes). That headline
number hides where it actually is:

| Component | Gap |
|---|---|
| All 58 ribs | 0.0% |
| Front/rear spars | 1.3–1.7% |
| Top/bottom skins | 1.2% |
| 3 internal spars | 37.7–63.7% |

Ribs, front/rear spars, and skins — everything actually on the model's
outer boundary — stay essentially fully connected. The gap concentrates
almost entirely on the 3 internal spars, exactly where the design
predicts it: an internal spar's cap is a straight line between two ribs,
while the skin panel spanning that same gap is a best-fit surface with
only one forced interior point, not guaranteed to reduce to that exact
line everywhere else. The front/rear spars' own small residual traces to
one specific, honestly-diagnosed cause too: the one rib-to-rib gap that
happens to straddle Y=224 (the kink itself) contains a real polyline bend
in the spar that the single smooth skin fit spanning that same gap has no
way to know about. Full diagnosis in the script's own module docstring.

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_extended_planform.png" alt="Planform view of the extended parametric wingbox, showing the kink and multi-spar structure" style="max-width:100%;">

*The extended model's planform. The sweep break at the real Y=224in kink
station is visible as a subtle change in leading-edge angle about a fifth
of the way out from the root.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_extended_internal.png" alt="Internal structure of the extended parametric wingbox with skins hidden, showing 5 spars and 58 ribs" style="max-width:100%;">

*Skins hidden to show the internal structure: 5 continuous spanwise spars
crossing all 58 ribs, sharing real topology at every crossing.*

<img src="https://ai-for-engineering.github.io/nastran-fea/assets/parametric_wingbox_extended_iso.png" alt="Isometric view of the extended parametric wingbox" style="max-width:100%;">

*Isometric view of the extended model. The camber (2.5% top / 1.2%
bottom of local chord) is real in the geometry and mesh — confirmed by
the connectivity numbers above, which only make sense for a genuinely
curved surface — but subtle enough at this chord-to-thickness aspect
ratio not to read as an obvious bulge in a plain, unexaggerated
screenshot, the same way a real wing's camber looks nearly flat from a
casual distance too.*

**What's still simplified, updated honestly after this extension:**

- **5 spars, evenly spaced in chordwise fraction** — still far short of
  the real ~22-web count and its real, uneven, root-concentrated
  distribution (see above).
- **The planform now has one real kink**, not the smooth continuous curve
  the real wing's actual outer mold line has — piecewise-*linear* across
  2 segments, not a spline.
- **Ribs are still flat planes perpendicular to span.** Unchanged from
  the base version above.
- **Camber is now present but engineered, not measured** — a modest
  analytic parabolic bump, not a real airfoil profile. Demonstrates the
  geometric capability (curved, non-ruled skins built from reused
  chordwise cross-section curves), not an aerodynamically accurate outer
  mold line.
- **No stringers/stiffeners.**

### What's still open

No boundary conditions, load, or solve on this model yet — geometry and
mesh connectivity only, so far. The natural next step is the same
BC-reconstruction and GVW-load approach already used for the IGES-welded
rebuild, then a real MYSTRAN solve and comparison. Tracked in
[issue #61](https://github.com/ai-for-engineering/nastran-fea/issues/61).
Full technical detail on everything in this post is in
[issue #59](https://github.com/ai-for-engineering/nastran-fea/issues/59)
and [PR #60](https://github.com/ai-for-engineering/nastran-fea/pull/60).

## Conclusion

Two paths to the same objective, and a clear ranking between them. The CAD
path -- meshing each IGES component independently and welding coincident
nodes afterward, the way the industry generally works -- gets close: same
order of magnitude on tip displacement and peak stress against a real,
previously-validated result. But it has a hard structural ceiling: 43-99%
of near-boundary node pairs per component stay unwelded no matter how the
algorithm is tuned, because the source CAD never encoded shared topology in
the first place. The direct-parametrization path -- building the same
wing's ribs, spars, and skins as parametric surfaces in one fragmented
Gmsh session, using real dimensions measured from the original deck, not a
generic wingbox -- closes that gap completely at its simplest complexity:
0.0%, on a 2-spar, unkinked, flat-skinned version of the real planform.
Pushed further -- 5 spars, a real measured kink, and camber -- the gap
reappears, but far smaller and fully diagnosed: 12.5%, concentrated almost
entirely on the newly-added internal spars, with ribs, main spars, and
skins still at 0.0-1.7%. Even at that higher complexity, still a fraction
of the CAD path's 43-99%.

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
