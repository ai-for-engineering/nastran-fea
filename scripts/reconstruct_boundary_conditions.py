"""
Reconstruct boundary conditions and a distributed static load on a BDF
purely geometrically, by node Y-coordinate -- for models (like a from-
geometry rebuild, see assemble_wingbox_geometry.py) that have entirely
different GRID IDs and node counts than whatever original deck they're
being compared against, so the original's exact SPC/FORCE node lists
can't be copied over literally.

Two operations:

- `add_spc_by_y_band`: fix every GRID node within a Y-tolerance band of a
  target spanwise station in the given DOF components. Used for both a
  root cantilever (Y=0) and an intermediate rib-station support (Y!=0) --
  see build_nasa_crm_from_geometry.py for how the actual NASA CRM wingbox
  rebuild picks its target stations and tolerances (verified against the
  real rebuilt mesh's own node distribution, not assumed).
- `add_uniform_z_load`: distribute a target total resultant force in +Z
  evenly across every GRID node as individual FORCE cards. Deliberately
  preserves the original's *total resultant*, not its literal per-node
  magnitude -- the rebuilt mesh's GRID count differs from whatever
  original it's being compared against, so matching the per-node value
  instead would silently change the total applied load.
"""
from __future__ import annotations

from typing import Any


def add_spc_by_y_band(
    bdf: Any,
    spc_id: int,
    y_target: float,
    y_tolerance: float,
    components: str,
) -> int:
    """Add one SPC1 card fixing every GRID node within y_tolerance of
    y_target (in Y) in the given DOF components (e.g. "123" or "3").

    Returns the number of nodes constrained.

    Raises:
        ValueError: no nodes fall within the band -- a silently-empty SPC
            would leave the model unconstrained rather than fail loudly.
    """
    nids = [
        nid
        for nid, node in bdf.nodes.items()
        if abs(node.get_position()[1] - y_target) <= y_tolerance
    ]
    if not nids:
        raise ValueError(
            f"no GRID nodes found within {y_tolerance} of Y={y_target} -- "
            "check the target station actually exists in this mesh"
        )
    bdf.add_spc1(spc_id, components, nids)
    return len(nids)


def add_uniform_z_load(bdf: Any, load_id: int, total_force_z: float) -> dict[str, Any]:
    """Add one FORCE card per GRID node, magnitude total_force_z / n_nodes
    in +Z, so the sum reproduces total_force_z exactly (up to floating
    point) regardless of how many nodes the mesh actually has.

    Returns {"n_nodes": ..., "per_node_force": ..., "resultant": ...}.

    Raises:
        ValueError: bdf has no nodes at all.
    """
    nids = list(bdf.nodes.keys())
    if not nids:
        raise ValueError("bdf has no GRID nodes to distribute a load across")
    per_node_force = total_force_z / len(nids)
    for nid in nids:
        bdf.add_force(load_id, nid, per_node_force, [0.0, 0.0, 1.0])
    return {
        "n_nodes": len(nids),
        "per_node_force": per_node_force,
        "resultant": per_node_force * len(nids),
    }
