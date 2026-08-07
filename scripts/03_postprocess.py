"""
Read MYSTRAN's OP2 output via pyNastran, find the max von Mises stress
in the plate, and report a margin of safety against Ti-6Al-4V yield.
"""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from pyNastran.op2.op2 import OP2

FTY = 880.0  # MPa, Ti-6Al-4V annealed RT tensile yield (see 02_build_bdf.py)
OP2_FILE = "models/lug_model.OP2"

op2 = OP2()
op2.read_op2(OP2_FILE)

subcase = 1
stress = op2.op2_results.stress.cquad4_stress[subcase]

# von Mises is stored per element, per layer (top/bottom fiber for shells)
von_mises = stress.data[0, :, 7]   # ovm column, layer-averaged view below
element_ids = stress.element_node[:, 0]

# cquad4_stress groups 2 rows per element (top/bottom fiber) when stress_bits
# indicate fiber output; take the max over both fibers per element.
n_elem = len(np.unique(element_ids))
von_mises_per_elem = {}
for eid, vm in zip(element_ids, von_mises):
    von_mises_per_elem[eid] = max(von_mises_per_elem.get(eid, 0.0), vm)

max_eid = max(von_mises_per_elem, key=von_mises_per_elem.get)
max_vm = von_mises_per_elem[max_eid]

disp = op2.displacements[subcase]
max_disp_mag = np.max(np.linalg.norm(disp.data[0, :, :3], axis=1))

print(f"Number of elements with stress results: {n_elem}")
print(f"Max von Mises stress: {max_vm:.1f} MPa, at element {max_eid}")
print(f"Max nodal displacement magnitude: {max_disp_mag:.4f} mm")
print()
print(f"Ti-6Al-4V Fty = {FTY} MPa")
ms = FTY / max_vm - 1
print(f"Margin of Safety (yield, von Mises) = {ms:.2f}")
print()
print("NOTE: this is an FEA-derived von Mises margin only, for pipeline")
print("validation. A real lug certification would also require classical")
print("bearing, net-section tension, and shear-out checks (Niu/Bruhn),")
print("which are NOT covered by this check.")

# --- Stress contour plot ---
mesh = np.load("models/lug_mesh.npz", allow_pickle=True)
node_ids = mesh["node_ids"]
node_xyz = mesh["node_xyz"]
quads = mesh["quads"]
node_index = {int(n): i for i, n in enumerate(node_ids)}

eid_to_vm = von_mises_per_elem
face_colors = np.array([eid_to_vm[i + 1] for i in range(len(quads))])
polys = [[node_xyz[node_index[int(n)]][:2] for n in quad] for quad in quads]

fig, ax = plt.subplots(figsize=(11, 5))
coll = PolyCollection(polys, array=face_colors, cmap="turbo", edgecolors="none")
ax.add_collection(coll)
ax.set_xlim(node_xyz[:, 0].min(), node_xyz[:, 0].max())
ax.set_ylim(node_xyz[:, 1].min(), node_xyz[:, 1].max())
ax.set_aspect("equal")
ax.set_xlabel("x (mm)")
ax.set_ylabel("y (mm)")
ax.set_title(f"Lug von Mises stress (MPa) -- max {max_vm:.1f} MPa @ elem {max_eid}, MS={ms:.2f}")
cbar = fig.colorbar(coll, ax=ax, label="von Mises stress (MPa)")
fig.tight_layout()
fig.savefig("results/lug_stress_contour.png", dpi=150)
print("\nSaved stress contour to results/lug_stress_contour.png")
