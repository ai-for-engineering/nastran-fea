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
