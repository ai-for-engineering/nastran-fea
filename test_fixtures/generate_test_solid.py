"""
Generate a synthetic 3D solid lug and export it as STEP, purely as a stand-in
test fixture for developing the CAD-import pipeline (scripts/04_*).

This is NOT part of the "official" pipeline -- it exists only because we
don't yet have a real, non-proprietary STEP file to develop against. Swap
in an actual CAD file when available; the import pipeline should not care
where the STEP file came from.

Same lug proportions as scripts/01_build_mesh.py (2D version), extruded to
a real solid thickness this time instead of using a PSHELL idealization.
"""
import gmsh

D = 12.0
R = D / 2
W = 3 * D
EDGE_DIST = 1.5 * D
L_TOTAL = 100.0
THICKNESS = 5.0

gmsh.initialize()
gmsh.model.add("test_lug_solid")

rect = gmsh.model.occ.addRectangle(0, -W / 2, 0, L_TOTAL, W)
disk = gmsh.model.occ.addDisk(EDGE_DIST, 0, 0, R, R)
plate_face, _ = gmsh.model.occ.cut([(2, rect)], [(2, disk)])
gmsh.model.occ.synchronize()

solid = gmsh.model.occ.extrude(plate_face, 0, 0, THICKNESS)
gmsh.model.occ.synchronize()

# Small fillet on the two long top/bottom edges of the grip end, to make
# this a slightly less trivial test case for the import/mesh pipeline than
# a pure prism (real CAD from a designer will have fillets, chamfers, etc).
solid_tags = [tag for dim, tag in solid if dim == 3]
edges = gmsh.model.getBoundary([(3, t) for t in solid_tags], combined=False, oriented=False, recursive=True)
fillet_edges = []
for dim, tag in edges:
    if dim != 1:
        continue
    xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(dim, tag)
    is_grip_end_long_edge = (
        abs(xmin - L_TOTAL) < 1e-6 and abs(xmax - L_TOTAL) < 1e-6
        and (ymax - ymin) > 1.0
    )
    if is_grip_end_long_edge:
        fillet_edges.append(tag)

if fillet_edges:
    filleted = gmsh.model.occ.fillet(solid_tags, fillet_edges, [1.0])
    gmsh.model.occ.synchronize()

gmsh.write("test_fixtures/test_lug_solid.step")
print("Wrote test_fixtures/test_lug_solid.step")

volumes = gmsh.model.getEntities(3)
surfaces = gmsh.model.getEntities(2)
print(f"Solid volumes: {len(volumes)}, surfaces: {len(surfaces)}")

gmsh.finalize()
