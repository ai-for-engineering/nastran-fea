"""
Build a 2D mesh for a straight lug (rectangular plate with a pin hole)
using Gmsh's OCC kernel, and tag boundary groups needed later for BCs/loads.

Geometry convention:
    x = 0        : free edge nearest the pin hole
    x = L_TOTAL  : fixed/grip end (SPC applied here in the next script)
    hole center  : (EDGE_DIST, 0)
    y = +-W/2    : free edges (top/bottom)

Lug proportions follow common aerospace practice: W/D = 3, e/D = 1.5.
"""
import gmsh
import numpy as np
import matplotlib.pyplot as plt

# --- Parameters (mm) ---
D = 12.0                  # hole diameter
R = D / 2
W = 3 * D                 # plate width
EDGE_DIST = 1.5 * D        # hole center to pin-end free edge
L_TOTAL = 100.0            # plate length, pin-end to grip end

MESH_SIZE_FINE = 1.0       # near hole
MESH_SIZE_COARSE = 4.0     # far field

OUT_DIR = "models"

gmsh.initialize()
gmsh.model.add("lug")

rect = gmsh.model.occ.addRectangle(0, -W / 2, 0, L_TOTAL, W)
disk = gmsh.model.occ.addDisk(EDGE_DIST, 0, 0, R, R)
plate, _ = gmsh.model.occ.cut([(2, rect)], [(2, disk)])
gmsh.model.occ.synchronize()

surf_tag = plate[0][1]

# --- Classify boundary curves by bounding box ---
boundary = gmsh.model.getBoundary([(2, surf_tag)], combined=False, oriented=False)
hole_curves = []
fixed_end_curves = []
other_curves = []

for dim, tag in boundary:
    xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(dim, tag)
    is_small_bbox = (xmax - xmin) < (2.5 * R) and (ymax - ymin) < (2.5 * R)
    near_hole_center = abs((xmin + xmax) / 2 - EDGE_DIST) < R and abs((ymin + ymax) / 2) < R
    if is_small_bbox and near_hole_center:
        hole_curves.append(tag)
    elif abs(xmin - L_TOTAL) < 1e-6 and abs(xmax - L_TOTAL) < 1e-6:
        fixed_end_curves.append(tag)
    else:
        other_curves.append(tag)

assert hole_curves, "Failed to identify hole boundary curve(s)"
assert fixed_end_curves, "Failed to identify fixed-end curve"

gmsh.model.addPhysicalGroup(1, hole_curves, name="hole_boundary")
gmsh.model.addPhysicalGroup(1, fixed_end_curves, name="fixed_end")
gmsh.model.addPhysicalGroup(2, [surf_tag], name="plate")

# --- Mesh sizing: fine near hole, coarse far field ---
dist_field = gmsh.model.mesh.field.add("Distance")
gmsh.model.mesh.field.setNumbers(dist_field, "CurvesList", hole_curves)
gmsh.model.mesh.field.setNumber(dist_field, "Sampling", 100)

thresh_field = gmsh.model.mesh.field.add("Threshold")
gmsh.model.mesh.field.setNumber(thresh_field, "InField", dist_field)
gmsh.model.mesh.field.setNumber(thresh_field, "SizeMin", MESH_SIZE_FINE)
gmsh.model.mesh.field.setNumber(thresh_field, "SizeMax", MESH_SIZE_COARSE)
gmsh.model.mesh.field.setNumber(thresh_field, "DistMin", R)
gmsh.model.mesh.field.setNumber(thresh_field, "DistMax", 5 * R)

gmsh.model.mesh.field.setAsBackgroundMesh(thresh_field)
gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)

gmsh.model.mesh.setRecombine(2, surf_tag)  # prefer quads (CQUAD4) over tris
gmsh.model.mesh.generate(2)

# --- Extract mesh data ---
node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
node_id_to_xyz = {int(t): node_coords[3 * i:3 * i + 3] for i, t in enumerate(node_tags)}

elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements(dim=2)
quads, tris = [], []
for etype, tags, nodes in zip(elem_types, elem_tags, elem_node_tags):
    if etype == 3:  # 4-node quad
        quads = np.array(nodes, dtype=int).reshape(-1, 4)
    elif etype == 2:  # 3-node tri
        tris = np.array(nodes, dtype=int).reshape(-1, 3)

hole_node_tags = set()
for tag in hole_curves:
    nt, _, _ = gmsh.model.mesh.getNodes(1, tag, includeBoundary=True)
    hole_node_tags.update(int(t) for t in nt)

fixed_node_tags = set()
for tag in fixed_end_curves:
    nt, _, _ = gmsh.model.mesh.getNodes(1, tag, includeBoundary=True)
    fixed_node_tags.update(int(t) for t in nt)

print(f"Nodes: {len(node_tags)}")
print(f"Quad elements: {len(quads)}")
print(f"Tri elements:  {len(tris) if len(tris) else 0}")
print(f"Hole boundary nodes: {len(hole_node_tags)}")
print(f"Fixed-end nodes: {len(fixed_node_tags)}")

np.savez(
    f"{OUT_DIR}/lug_mesh.npz",
    node_ids=np.array(list(node_id_to_xyz.keys())),
    node_xyz=np.array(list(node_id_to_xyz.values())),
    quads=quads,
    tris=tris if len(tris) else np.empty((0, 3), dtype=int),
    hole_node_ids=np.array(sorted(hole_node_tags)),
    fixed_node_ids=np.array(sorted(fixed_node_tags)),
    params=dict(D=D, R=R, W=W, EDGE_DIST=EDGE_DIST, L_TOTAL=L_TOTAL),
)
gmsh.write(f"{OUT_DIR}/lug_mesh.msh")
gmsh.finalize()

# --- Plot for visual sanity check ---
fig, ax = plt.subplots(figsize=(10, 5))
for quad in quads:
    pts = np.array([node_id_to_xyz[n] for n in quad])
    pts = np.vstack([pts, pts[0]])
    ax.plot(pts[:, 0], pts[:, 1], "b-", linewidth=0.4)
for tri in tris if len(tris) else []:
    pts = np.array([node_id_to_xyz[n] for n in tri])
    pts = np.vstack([pts, pts[0]])
    ax.plot(pts[:, 0], pts[:, 1], "g-", linewidth=0.4)

hole_pts = np.array([node_id_to_xyz[n] for n in hole_node_tags])
ax.scatter(hole_pts[:, 0], hole_pts[:, 1], c="red", s=8, zorder=5, label="hole boundary (pin load)")
fixed_pts = np.array([node_id_to_xyz[n] for n in fixed_node_tags])
ax.scatter(fixed_pts[:, 0], fixed_pts[:, 1], c="orange", s=8, zorder=5, label="fixed end (SPC)")

ax.set_aspect("equal")
ax.set_title(f"Lug mesh: {len(quads)} quads, {len(tris) if len(tris) else 0} tris, {len(node_tags)} nodes")
ax.set_xlabel("x (mm)")
ax.set_ylabel("y (mm)")
ax.legend(loc="upper right")
fig.tight_layout()
fig.savefig(f"{OUT_DIR}/lug_mesh_preview.png", dpi=150)
print(f"Saved preview to {OUT_DIR}/lug_mesh_preview.png")
