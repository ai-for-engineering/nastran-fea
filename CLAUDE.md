# CLAUDE.md

Operating notes for working in this repo. See `README.md` for setup/usage.

## Scope

This repo (`nastran-fea`) is the first project under the ai-for-engineering
umbrella -- ai-for-engineering is the future company/brand name for exploring
AI applications across engineering disciplines generally; this specific
repo's scope is narrower: AI-driven FEA in native Nastran bulk-data format
only. Don't conflate the two in docs/naming -- "ai-for-engineering" is the
umbrella, not this project's name.

## Project constraint: open source only

This project exists to demonstrate AI-driven FEA workflows using **only
open-source tools**, in **native Nastran bulk-data format** specifically
(not a translated/different syntax). Current stack: Gmsh (meshing) +
pyNastran (BDF/OP2 I/O) + MYSTRAN (solver). Don't suggest commercial
alternatives (Ansys, Abaqus, MSC/NX Nastran, HyperMesh) as part of the
actual pipeline -- they're fine to discuss for context/comparison, but the
deliverable must run on the free stack.

## Environment

- Python venv at `venv/` (not `base` conda env). Always invoke via
  `./venv/Scripts/python.exe` or `./venv/Scripts/pip.exe` explicitly --
  `python`/`pip` on PATH may resolve to a different (Microsoft Store stub or
  miniforge base) interpreter.
- MYSTRAN solver binary lives in `solver/`, gitignored (~24MB). Must be
  downloaded per README before anything can actually solve.
- `gh` CLI is authenticated on this machine (account `mabvscode`, a member/
  owner of the `ai-for-engineering` org). Repo:
  `github.com/ai-for-engineering/nastran-fea` (private).
- Local checkout lives at `Projets/ai-for-engineering/nastran-fea/` -- the
  `ai-for-engineering` parent folder is the umbrella, this repo is one
  project inside it (see Scope above).

## Gotchas

Full explanations for anything setup/usage-relevant live in `README.md`
(case studies section, GUI section, solver section) -- don't duplicate the
rationale here, just the reminder:

- MYSTRAN's exit code / "terminated normally" message is not trustworthy on
  its own -- always go through `scripts/run_solver.py`, never shell out to
  the solver directly (full story: README + the script's own docstring).
- MYSTRAN's `PSHELL` can't take a nonzero `MID4` -- see README's case-studies
  section (uCRM vs NASA CRM) before assuming an anisotropic shell model will
  just run.
- A `PSHELL` with `mid2` (bending material) left blank is a real, silent
  trap, not a harmless default: Nastran gives that shell membrane-only
  stiffness, zero bending. Confirmed the hard way rebuilding the NASA CRM
  wingbox from geometry (`scripts/build_nasa_crm_from_geometry.py`) --
  MYSTRAN's own `AUTOSPC` found literally every rotational DOF in the
  entire ~70k-node model singular and silently auto-constrained all of
  them (visible in the F06's own `*INFORMATION: AUTOSPC Summary` block,
  not a soft-to-miss log line) rather than raising a hard error, producing
  a deck that "solved" cleanly (no `*ERROR`/`FATAL`) with physically
  nonsensical displacements (up to ~1e14 in). Always set `mid2`/`mid3` to
  the same material as `mid1` for a simple isotropic shell -- the original
  NASA CRM deck does exactly this on every one of its own `PSHELL` cards,
  easy to miss since it doesn't stand out from `mid1` at a glance. A
  correctly-conditioned solve of a model this size is also genuinely
  slower (real bending stiffness = a much less trivially-reduced linear
  system) -- budget significantly more than `run_solver`'s 600s default
  timeout (confirmed needing ~15-20 min for this specific model).
- OptiStruct-authored decks (no `SOL`/`CEND`, `ANALYSIS MODES/STATICS`
  syntax) need their case control rebuilt -- exact patch recipe is in
  README's NASA CRM case-study section.
- pyNastranGUI's dependency versions (VTK pin, etc.) are documented in
  README's GUI section -- check there before reinstalling anything.
- Gmsh's OpenCASCADE boolean fragment (`gmsh.model.occ.fragment`) -- the
  textbook way to glue multiple independently-authored midsurface CAD
  files into one topologically connected mesh -- is genuinely slow *and*
  unreliable on real aircraft-structure geometry, not just large synthetic
  models: fragmenting just 2 of the NASA CRM wingbox's 5 component IGES
  files (ribs + spars, 91 of ~470 total faces) took 234 seconds and left
  sub-micron sliver edges that `gmsh.model.occ.healShapes()` couldn't
  reliably clean up (ineffective at small tolerances, crashed outright at
  larger ones). `scripts/assemble_wingbox_geometry.py` abandoned this
  approach entirely in favor of meshing each component independently and
  welding coincident nodes afterward (a plain coordinate-tolerance
  operation, no CAD engine involved) -- see its module docstring.
- A gmsh mesh-generation failure with the message "1D mesh cannot be
  divided by 2" is NOT necessarily a sign of degenerate/unhealable CAD --
  confirmed it can also be a pure quad-recombination *parity* failure that
  hits a single, perfectly clean geometry file with no merging involved at
  all (`CRM_ribs.igs` alone: fine at `mesh_size=150`, fails this way at
  `mesh_size=200`, triangulates fine at *both* sizes). `CRM_stringers.igs`
  was first misdiagnosed as having inherent unfixable degenerate geometry
  (all 3 available stringer file variants failed identically) before
  realizing recombination alone was the cause. Check whether
  `Mesh.RecombineAll` is involved before concluding a geometry file itself
  is broken -- `scripts/geometry_to_bdf.py`'s `_mesh_single_geometry`
  now retries once with recombination off automatically.
- A geometry file's own units and a target BDF's units are not guaranteed
  to match, and nothing about the file format says which was used. The
  NASA CRM wingbox's IGES midsurfaces are in mm; its own existing solved
  BDF is in inches -- confirmed by cross-checking a meshed IGES file's
  bounding box (mm) against the real deck's (inches) after applying a
  1/25.4 `unit_scale`, which landed the span within 0.05 in of the real
  model's 1,151.3 in. Always verify with a real dimension, don't assume.
- NASA's downloadable IGES geometry for the CRM wingbox is not a complete
  structural description -- confirmed by direct visual comparison (see the
  blog's "Rebuilding the wingbox from geometry alone" section):
  `CRM_spars.igs` contains only the 3 main continuous spanwise spar webs
  (front/mid/rear). The original FE deck's "ShearWebs" group (8,880
  elements -- more than the main spars themselves) is a dense internal
  comb of rib-spaced shear ties that has no counterpart anywhere in the
  IGES download. A from-geometry rebuild will be missing this internal
  stiffening structure regardless of meshing/welding quality -- verified
  from two different camera angles before concluding it's a real gap, not
  an occlusion artifact.
- Never invoke `pyNastran.gui.gui` as a raw shell command bounded only by a
  generic command-timeout -- that kind of timeout detaches/backgrounds the
  wait, it doesn't kill the process, so a slow render leaks a live Qt
  process indefinitely (discovered firsthand: a debug postscript run this
  way outlived its 180s timeout and sat idle for hours before being
  noticed). Always go through (or mirror) `scripts/mcp_server.py`'s
  `_run_pynastrangui`, which wraps the call in Python's
  `subprocess.run(..., timeout=...)` -- that genuinely kills the child on
  timeout.

Things with no README equivalent (code-level, not usage-level):

- pyNastran's `write_bdf`: use `size=8, enddata=True` explicitly --
  `size=16` has produced field overflow on high-precision floats before.
- MYSTRAN's real-number parser wants a decimal point (`1.0E5`, not `1E5`).
- pyNastran OP2 stress arrays: use
  `op2.op2_results.stress.cquad4_stress[subcase]` (not the deprecated
  `op2.cquad4_stress`). `von_mises` is column index 7; `element_node[:, 0]`
  gives element IDs (each element appears twice, once per shell fiber).
- pyNastran BDF combination cards live in their own dicts, separate from
  the cards they combine: `SPCADD` is in `model.spcadds`, not `model.spcs`
  (which only has `SPC`/`SPC1`); `LOAD` combinations are in
  `model.load_combinations`, not `model.loads` (which only has
  `FORCE`/`MOMENT`/...). Checking only the latter silently returns nothing
  for a deck that uses combinations -- both dicts need checking, keyed the
  same way (by set id).
- To find pyNastranGUI's actual internal result-case names (for adding a
  new `render_stress_contour` fringe type), run a one-off postscript that
  loops `self.result_cases.items()` and writes out each `resname` --
  known names so far: `'vonMises'`, `'Displacement T_XYZ'` (translational
  magnitude), `'Displacement R_XYZ'` (rotational). See the Gotcha above on
  never invoking pyNastranGUI directly via a raw, timeout-bounded shell
  command -- run this the same safe way.

## Workflow conventions

- **Branch + PR, not direct commits to `master`** for anything nontrivial.
  Backlog is tracked as GitHub issues; reference/close them from PRs.
- **Session wrap-up, before switching away from a work session:**
  1. Open work state (in-progress tasks, decisions made) belongs in GitHub
     issues/PRs, not in this file or in Claude's own memory -- it decays
     fast and GitHub is already the source of truth.
  2. A genuinely durable, repo-level fact learned this session (a library
     quirk, a solver limitation, a convention) -- update this file. Edit an
     existing bullet if one's related; don't just append a near-duplicate.
  3. A lesson about *how to collaborate* (a correction, a confirmed
     approach) that would apply beyond this repo -- that's Claude's memory,
     not this file.
  4. Skim for staleness: a Gotchas bullet superseded by a later fix, a repo
     layout entry for something deleted -- fix or remove it now rather than
     letting this file grow monotonically.
- Delegated/autonomous work uses the `Agent` tool with
  `isolation: "worktree"` -- it works in an isolated git worktree and
  branch, then opens its own PR. Worktrees don't have their own `venv/` or
  `solver/` (gitignored, not checked out) -- point agents at the main
  checkout's absolute paths for those when they need to actually run
  something, but keep the code itself portable (no hardcoded personal paths).
- Clean up worktrees after merging (`git worktree remove`) before deleting
  the branch, or branch deletion fails.

## Repo layout

- `scripts/run_solver.py` -- generic MYSTRAN invocation wrapper (see
  Gotchas); not tied to any specific model
- `scripts/mcp_server.py` -- MCP server wrapping the pipeline
  (mesh_geometry_to_bdf/mesh_assembly_to_bdf/load_model/
  patch_case_control/run_solver/get_max_stress/render_model_view/
  render_stress_contour) as tool calls; see README's MCP server section.
  Imports `geometry_to_bdf.py`, `assemble_wingbox_geometry.py`,
  `run_solver.py`, and `ses_groups.py` rather than duplicating them.
  `scripts/test_mcp_server.py` has the smoke tests.
- `scripts/geometry_to_bdf.py` -- pre-processing: Gmsh (OpenCASCADE) IGES/
  STEP import + 2D meshing of a single midsurface component into a
  GRID/CQUAD4/CTRIA3 + PSHELL + MAT1 BDF. Its `_mesh_single_geometry`
  helper is shared with `assemble_wingbox_geometry.py` below (multi-
  component), including an automatic fallback to an all-triangle mesh if
  quad recombination fails (a real, confirmed-non-CAD-related gmsh gotcha
  -- see Gotchas above). `scripts/test_geometry_to_bdf.py` has the tests,
  using a synthetic on-the-fly STEP rectangle rather than the real
  (gitignored) NASA CRM IGES files.
- `scripts/assemble_wingbox_geometry.py` -- pre-processing, multi-part:
  meshes several IGES/STEP components independently and welds coincident
  nodes across components into one connected BDF, one PSHELL per
  component. Deliberately NOT a CAD-level boolean-fragment merge -- see
  its module docstring for why that was tried first and abandoned, and
  for the real transitive-union-find bug (two distinct same-component
  nodes could collapse into one GRID) its conflict-aware weld algorithm
  fixes. Validated end to end against the real, full 5-component NASA CRM
  wingbox IGES download. `scripts/test_assemble_wingbox_geometry.py` has
  the tests, synthetic (adjacent/disjoint rectangles) for speed/CI.
- `scripts/build_nasa_crm_from_geometry.py` -- the NASA CRM wingbox's own
  case-specific driver script wiring `assemble_wingbox_geometry.py` +
  `reconstruct_boundary_conditions.py` + `run_solver.py` together end to
  end (mesh -> weld -> real per-group properties -> BC/GVW load -> case
  control -> solve). Validated: MYSTRAN solves the result with zero
  `*ERROR`/`FATAL` messages. See `case_studies/nasa_crm_wingbox/README.md`'s
  "Rebuilding this model from geometry alone" section for the full story
  and results, and the blog post's matching section for a side-by-side
  visual inspection against the original.
- `scripts/reconstruct_boundary_conditions.py` -- generic (not NASA-CRM-
  specific) helpers for a from-geometry rebuild whose GRID IDs don't
  correspond to any original deck: `add_spc_by_y_band` fixes nodes within
  a Y-tolerance of a target station, `add_uniform_z_load` distributes a
  target *total* resultant force evenly (not a copied per-node value)
  across however many nodes the rebuilt mesh actually has.
- `scripts/compare_rebuilt_vs_original.py` -- physical-level (not node-by-
  node -- meaningless across different meshes) comparison of a rebuilt
  model against a real solved reference: tip displacement, peak stress by
  component, with `percent_difference`/`check_reaction_balance` as pure,
  tested helper functions.
- `scripts/ses_groups.py` -- parser for Patran/HyperMesh `.ses` session
  files' named element-group definitions (NOT Nastran format -- some case
  studies ship one as a bonus alongside the actual deck). See its docstring
  and issue #9. `scripts/test_ses_groups.py` has the tests.
- `case_studies/nasa_crm_wingbox/` -- the current (only) case study, a real
  publicly-licensed NASA structural assembly (mostly gitignored -- see
  README for source/license and the OptiStruct→MYSTRAN case-control patch;
  `original/` vs `derived/` split and provenance documented in its own
  tracked README.md)
- `solver/` -- the MYSTRAN binary, gitignored, download per README
- `spikes/` -- exploratory proof-of-concept scripts from research spikes
  (not production code) -- e.g. `pynastrangui_screenshot_postscript.py`
  from issue #8's headless-rendering investigation
- `docs/` -- GitHub Pages source (Jekyll, `minima` theme; posts live in
  `docs/_posts/`); see README's Blog section
