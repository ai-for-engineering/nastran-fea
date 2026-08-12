"""
Mesh a single midsurface IGES/STEP component into a solver-ready Nastran
BDF -- the first slice of a from-CAD pre-processing pipeline, using Gmsh
(already a project dependency, see requirements.txt) for the CAD import
and 2D meshing, and pyNastran for the bulk-data cards.

Scope, deliberately: **one geometry file in, one PSHELL/one MAT1 out.**
Two things ruled that in over a full multi-part assembly:

1. Gmsh's own IGES/STEP -> Nastran export (`gmsh.write(".bdf")`) only
   writes GRID + CTRIA3 -- no PSHELL/MAT1/SPC/LOAD at all, and one PID per
   source CAD face rather than a meaningful property. This module's job is
   exactly filling that gap for one component: pyNastran adds a single
   PSHELL (`thickness`) and MAT1 (`material`), consistent with the
   per-group averaged thickness this project already extracts for a case
   study's blog write-up (see spikes/model_description_extract.py).
2. NASA's own CRM wingbox IGES download splits the assembly into five
   separate per-component files (ribs/spars/skins/stringers/rib caps).
   Meshing them independently, as this module does, leaves them
   topologically *disconnected* at their real-world shared edges --
   ribs don't share nodes with the spars they're riveted to. The correct
   fix is an OpenCASCADE boolean fragment/glue across all five files
   before meshing, but that's a genuinely slow and CAD-fragile operation
   on this real geometry: a direct test (ribs + spars only, 91 of the
   full assembly's ~470 faces) took 234 seconds for just those two. Doing
   that for all five components, robustly, is out of scope here and
   logged as a known gap -- the same honest-caveat treatment this project
   already gives the MYSTRAN CBEAM/PSHELL-MID4 solver gaps (see README).

Units: Gmsh meshes in the geometry file's own native units (the NASA CRM
IGES midsurfaces are in mm, while the project's existing NASA CRM BDF is
in inches) -- `unit_scale` multiplies every meshed coordinate before it's
written, so the output BDF can match whatever unit system the caller
needs it in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# gmsh element type codes (fixed by the Gmsh element-type table; not a
# project convention) for the two 2D element shapes this module supports.
_GMSH_TRIANGLE3 = 2
_GMSH_QUAD4 = 3


@dataclass
class MaterialProperties:
    """Minimal isotropic (MAT1) material definition."""

    mid: int
    e: float
    g: float
    nu: float
    rho: float = 0.0


@dataclass
class GeometryToBdfResult:
    success: bool
    bdf_path: Path
    n_nodes: int
    n_cquad4: int
    n_ctria3: int
    bounding_box: dict[str, dict[str, float]]
    pshell_id: int
    material: MaterialProperties
    warnings: list[str] = field(default_factory=list)


def mesh_geometry_to_bdf(
    geometry_path: str | Path,
    output_bdf_path: str | Path,
    mesh_size: float,
    thickness: float,
    material: MaterialProperties | dict[str, Any],
    *,
    unit_scale: float = 1.0,
    quad_dominant: bool = True,
    pshell_id: int = 1,
) -> GeometryToBdfResult:
    """Import one IGES/STEP midsurface, mesh it in 2D, and write GRID +
    CQUAD4/CTRIA3 + one PSHELL + one MAT1 to a Nastran BDF.

    Args:
        geometry_path: an .iges/.igs/.step/.stp file -- anything Gmsh's
            OpenCASCADE kernel can import. Must contain 2D surfaces
            (a midsurface/shell geometry); a solid-only file raises.
        output_bdf_path: where to write the resulting BDF.
        mesh_size: target element size, in the geometry file's own native
            units (NOT unit_scale'd) -- Gmsh meshes before the coordinate
            scale is applied.
        thickness: PSHELL thickness, in the *output* BDF's units (i.e.
            already unit_scale'd if relevant -- this is a shell property,
            not a coordinate, so unit_scale never touches it automatically).
        material: MaterialProperties, or an equivalent dict of the same
            fields (mid/e/g/nu/rho).
        unit_scale: multiplies every meshed node coordinate before writing
            (e.g. 1/25.4 to convert a millimeter geometry file into an
            inch-based BDF). Does not affect mesh_size (see above) or
            thickness.
        quad_dominant: ask Gmsh to recombine triangles into quads where it
            can (matching CQUAD4-heavy real Nastran wingbox models);
            leftover triangles where a clean recombination isn't possible
            still come out as CTRIA3, not an error.
        pshell_id: property ID for the single PSHELL every element
            references.

    Returns:
        GeometryToBdfResult with node/element counts, the meshed
        (unit_scale'd) bounding box, and any warnings (e.g. unsupported
        higher-order gmsh element types that were skipped).

    Raises:
        FileNotFoundError: geometry_path doesn't exist.
        ValueError: the imported geometry has no 2D surfaces to mesh.
    """
    import gmsh
    from pyNastran.bdf.bdf import BDF

    geometry_path = Path(geometry_path)
    output_bdf_path = Path(output_bdf_path)
    if not geometry_path.is_file():
        raise FileNotFoundError(f"geometry file not found: {geometry_path}")
    if isinstance(material, dict):
        material = MaterialProperties(**material)

    warnings: list[str] = []

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add(geometry_path.stem)
        gmsh.model.occ.importShapes(str(geometry_path))
        gmsh.model.occ.synchronize()

        surfaces = gmsh.model.getEntities(2)
        if not surfaces:
            raise ValueError(
                f"no 2D surfaces found in {geometry_path} -- expected a "
                "midsurface/shell geometry, got a solid-only or empty file"
            )

        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size / 5.0)
        if quad_dominant:
            gmsh.option.setNumber("Mesh.RecombineAll", 1)
            gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 2)
        gmsh.model.mesh.generate(2)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        elem_types, elem_tags_list, elem_node_tags_list = gmsh.model.mesh.getElements(2)
    finally:
        # gmsh keeps process-global state (gmsh.initialize/finalize aren't
        # reentrant-safe across concurrent calls) -- always tear down even
        # if meshing raised, so a caller retrying in the same process (e.g.
        # the MCP server, one long-lived process across many tool calls)
        # doesn't inherit a half-torn-down model.
        gmsh.finalize()

    xyz = node_coords.reshape(-1, 3) * unit_scale

    bdf = BDF()
    bdf.add_mat1(material.mid, material.e, material.g, material.nu, rho=material.rho)
    bdf.add_pshell(pshell_id, mid1=material.mid, t=thickness)

    for tag, pos in zip(node_tags, xyz):
        bdf.add_grid(int(tag), pos.tolist())

    n_cquad4 = 0
    n_ctria3 = 0
    for etype, etags, enodes in zip(elem_types, elem_tags_list, elem_node_tags_list):
        if etype == _GMSH_TRIANGLE3:
            nodes_per_elem = 3
        elif etype == _GMSH_QUAD4:
            nodes_per_elem = 4
        else:
            warnings.append(
                f"skipped {len(etags)} element(s) of unsupported gmsh type "
                f"{etype} (only linear tri/quad shells are handled)"
            )
            continue
        enodes = enodes.reshape(-1, nodes_per_elem)
        for eid, nids in zip(etags, enodes):
            nids_int = [int(n) for n in nids]
            if nodes_per_elem == 3:
                bdf.add_ctria3(int(eid), pshell_id, nids_int)
                n_ctria3 += 1
            else:
                bdf.add_cquad4(int(eid), pshell_id, nids_int)
                n_cquad4 += 1

    output_bdf_path.parent.mkdir(parents=True, exist_ok=True)
    # size=8 (not 16): the project has previously seen size=16 overflow on
    # high-precision floats -- see CLAUDE.md.
    bdf.write_bdf(str(output_bdf_path), size=8, enddata=True)

    bounding_box = {
        axis: {"min": float(xyz[:, i].min()), "max": float(xyz[:, i].max())}
        for i, axis in enumerate("xyz")
    }

    return GeometryToBdfResult(
        success=True,
        bdf_path=output_bdf_path,
        n_nodes=len(node_tags),
        n_cquad4=n_cquad4,
        n_ctria3=n_ctria3,
        bounding_box=bounding_box,
        pshell_id=pshell_id,
        material=material,
        warnings=warnings,
    )
