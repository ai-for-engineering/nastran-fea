# NASA CRM Wingbox -- case study data

This folder itself is gitignored (large binary/model data, ~110MB) -- this
README is the one tracked exception, so provenance survives even though the
data doesn't. Re-download per the instructions below to reproduce it.

## Source

[NASA CRM Wingbox FEM files](https://commonresearchmodel.larc.nasa.gov/fem-file/wingbox-fem-files/)
-- "could be used by anyone without any restrictions" per NASA. Downloaded
2026-08-07:

- `CRM_V15wingbox_1_noHM.zip` -> extracted into `original/V15wingbox/`
- `CRM_Wingbox_FEMMidsurfaces_IGES.zip` -> extracted into `original/IGES_midsurfaces/`

## Folder structure

- `original/` -- exactly as downloaded from NASA, unmodified. If you ever
  need to re-verify something against the source, diff against a fresh
  download rather than trusting this copy has stayed pristine.
- `derived/` -- produced by this project, not by NASA:
  - `CRM_V15_wingbox_1_static.{dat,F06,OP2,ERR}` -- the original deck
    (`original/V15wingbox/CRM_V15wingbox_1_noHM/CRM_V15_wingbox_1.bdf`)
    patched with the OptiStruct->MYSTRAN case-control recipe (see the main
    [README.md](../../README.md)'s "Case study: NASA CRM wingbox" section
    for the exact patch and why it's needed) and solved with MYSTRAN. This
    is the run cited throughout the project's docs (max von Mises ~40,000
    psi on the static "GVW" subcase).

Everything in `derived/` is reproducible from `original/` via
`scripts/mcp_server.py`'s `patch_case_control` + `run_solver` tools, or the
manual recipe in the main README -- it's kept here as a checked, known-good
reference rather than something you must regenerate every time.

## Rebuilding this model from geometry alone (issues #42-#46)

A second, independent way to get a solvable version of this wingbox:
starting from nothing but the CAD geometry in `original/IGES_midsurfaces/`
(five separate component files -- ribs/spars/skins/rib_caps/stringers),
mesh, assign properties, reconstruct boundary conditions and the GVW
load, and solve -- without touching `original/V15wingbox/`'s actual FE
deck at all. The point: test whether the from-geometry pre-processing
pipeline (`scripts/geometry_to_bdf.py`, `scripts/assemble_wingbox_geometry.py`)
can reproduce a genuine, previously-validated result on real aircraft-
structure geometry, not just parse cleanly on a toy example.

Reproduce: `./venv/Scripts/python.exe scripts/build_nasa_crm_from_geometry.py`
(mesh+weld+properties+BC/load+solve, ~15-20 min, mostly MYSTRAN's own
solve time), then `./venv/Scripts/python.exe scripts/compare_rebuilt_vs_original.py`.
Output: `derived/rebuilt_from_geometry*.bdf` (gitignored, like the rest of
this directory).

### What it took

The straightforward-sounding plan -- CAD-level boolean fragment across
the five component files, so shared edges get shared nodes -- was tried
first and abandoned. It hit real, reproducible tooling limits on this
actual geometry (234s to fragment just 2 of the 5 files; the result had
sub-micron sliver edges `gmsh.model.occ.healShapes()` couldn't reliably
clean up). The pipeline instead meshes each component independently and
welds coincident nodes across components afterward -- an approximate,
tolerance-based connection, not a mathematically exact shared curve. Full
story in `scripts/assemble_wingbox_geometry.py`'s module docstring.

Getting from "meshes and merges" to "MYSTRAN solves it and the answer
means something" surfaced four more real, specific bugs -- a transitive
weld that could merge two of one element's own corners, an exact-
coincidence tolerance too tight for Nastran's own small-field BDF write
precision, a genuine indexing bug in a mesh-quality check, and -- the
one that actually mattered most -- `PSHELL` cards missing `MID2` (bending
material), which gives Nastran a membrane-only shell with zero bending
stiffness. MYSTRAN's own `AUTOSPC` found literally every rotational DOF
in the ~70k-node model singular and silently auto-constrained all of
them, "solving" cleanly (no `*ERROR`/`FATAL`) with displacements up to
~1e14 in -- a technically-valid but physically meaningless answer that a
less careful check would have reported as a working rebuild. See
CLAUDE.md's Gotchas for the full list; each is a genuine, reproducible
finding, not a hypothetical.

### Results

With all of that fixed, MYSTRAN solves the assembled deck with zero
`*ERROR`/`FATAL` messages. Comparing against the original's own solved
result (max von Mises ~40,000 psi, ~159.7 in tip displacement, see the
main README and the blog's Model description chapter):

| | Rebuilt | Original | Difference |
|---|---|---|---|
| Nodes | 70,606 | 13,878 | denser mesh |
| Elements | 79,053 | 35,489 | denser mesh |
| Tip displacement | 93.3 in | 159.7 in | -41.6% (stiffer overall) |
| Peak von Mises, Ribs | 79,008 psi | 17,884 psi | +341.8% |
| Peak von Mises, Spars | 88,450 psi | 34,046.9 psi | +159.8% |
| Peak von Mises, Skins | 80,992 psi | 39,983.7 psi | +102.6% |
| Peak von Mises, Stringers | *not reported* | 32,980.1 psi (CBAR) | -- |

**Read this as "same order of magnitude, real modeling differences
explain the rest," not as "matches" or "is wrong."** Plainly stated,
not smoothed over:

- **STRINGERS is excluded, not silently cleaned up.** It's the least
  reliable component in this rebuild by construction: an all-triangle
  mesh (the only component where quad recombination failed and fell back
  -- see CLAUDE.md), a *back-calculated* thickness (1.01 in, chosen to
  preserve the original's total CBAR material volume, not measured from
  anything -- see `scripts/build_nasa_crm_from_geometry.py`'s own
  comment), and the sole owner of every one of the 21 residual poorly-
  connected nodes left over from the approximate weld strategy. Its
  reported peak stress remains in the millions of psi even after
  excluding every element that directly touches an unphysically-displaced
  node -- the contamination runs deeper than a few flagged elements, so
  reporting a "cleaned" number anyway would overstate confidence in it.
- **The rebuild is stiffer overall (lower tip displacement) but reads
  higher local peak stress everywhere else.** Both are real, plausible
  effects of genuine differences, not necessarily errors: RIB_CAPS is
  modeled as its own distinct shell component here (0.167 in, assumed
  equal to ribs -- the original has no separate named group for it at
  all, folded into some other group NASA didn't break out further),
  adding stiffness the original's accounting doesn't isolate the same
  way; a denser, differently-shaped mesh resolves local stress
  concentrations differently (a well-known FE convergence effect, not
  unique to this rebuild); and the weld-based (not CAD-exact) component
  connections are an approximation everywhere, not just at the 21
  flagged nodes.
- **Reaction-force / applied-load equilibrium was not independently
  re-verified for this specific run** -- `SPCFORCES` wasn't requested in
  the case control that was actually solved, and re-solving just to add
  it would cost another ~15-20 min. The applied load's own resultant was
  independently verified directly from the written BDF's FORCE cards
  (249,777.58 lbf vs. a 249,777.6 lbf target) before solving; global
  force balance is a fundamental property of any valid linear static
  solution, not something a converged MYSTRAN run can silently violate,
  but this specific check is a documented gap for anyone extending this
  work, not a claim it was checked.

This isn't a substitute for the original's own validated result -- it's
a genuine test of whether a from-CAD pipeline can get *close*, on a real
aircraft structure, and an honest account of exactly what stood between
"parses" and "means something."
