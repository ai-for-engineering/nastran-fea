"""
Build the Nastran BDF deck for the lug: GRID/CQUAD4/PSHELL/MAT1,
SPC1 at the fixed (grip) end, and a cosine-distributed pin bearing
load on the hole boundary.

Load convention:
    External pin load pulls the lug in the -x direction (away from
    the grip at x = L_TOTAL). The pin bears against the hole wall on
    the side FACING the free edge (theta = 180 deg, measured from the
    hole center, 0 deg = +x), which is the classical Bruhn/Niu
    assumption: the pin contact is opposite the ligament that carries
    load into the fixed structure. Pressure is cosine-distributed over
    the loaded 180 deg arc (theta in [90, 270] deg) and zero elsewhere.

    ASSUMPTION TO VALIDATE: this is a simplified pin-bearing idealization.
    A real certification stress report would also check net-section
    tension, shear-out, and hoop tension per classical lug methods
    (Niu / Bruhn) -- the FEA von Mises margin computed downstream is
    illustrative, not a substitute for those checks.
"""
import numpy as np
from pyNastran.bdf.bdf import BDF, CaseControlDeck

# --- Material: Ti-6Al-4V, annealed, room temperature (typical handbook values) ---
E = 113800.0      # MPa
NU = 0.31
G = E / (2 * (1 + NU))
RHO = 4.43e-9     # tonne/mm^3 (consistent mm-N-tonne-s unit system)
FTY = 880.0       # MPa, tensile yield (typical annealed Ti-6Al-4V)
FTU = 950.0       # MPa, tensile ultimate

T = 5.0           # mm, plate thickness
P_TOTAL = 10000.0  # N, placeholder pin load magnitude (pulling in -x)

MESH_FILE = "models/lug_mesh.npz"
OUT_BDF = "models/lug_model.bdf"

data = np.load(MESH_FILE, allow_pickle=True)
node_ids = data["node_ids"]
node_xyz = data["node_xyz"]
quads = data["quads"]
hole_node_ids = data["hole_node_ids"]
fixed_node_ids = data["fixed_node_ids"]
params = data["params"].item()

model = BDF()
model.sol = 101

for nid, xyz in zip(node_ids, node_xyz):
    model.add_grid(int(nid), xyz.tolist())

mid = 1
model.add_mat1(mid, E, G, NU, rho=RHO)

pid = 1
model.add_pshell(pid, mid1=mid, t=T, mid2=mid, mid3=mid)

for i, quad in enumerate(quads):
    eid = i + 1
    model.add_cquad4(eid, pid, [int(n) for n in quad])

spc_id = 1
model.add_spc1(spc_id, "123456", [int(n) for n in fixed_node_ids])

# --- Pin bearing load ---
xc, yc = params["EDGE_DIST"], 0.0
R = params["R"]
p0 = 2 * P_TOTAL / (np.pi * R * T)  # peak bearing pressure, MPa

node_index = {int(n): i for i, n in enumerate(node_ids)}
angles = []
for nid in hole_node_ids:
    x, y = node_xyz[node_index[int(nid)]][:2]
    theta = np.arctan2(y - yc, x - xc)
    angles.append((int(nid), theta))
angles.sort(key=lambda kv: kv[1])

load_id = 1
n = len(angles)
total_fx = total_fy = 0.0
for i, (nid, theta) in enumerate(angles):
    theta_prev = angles[i - 1][1]
    theta_next = angles[(i + 1) % n][1]
    dtheta_prev = (theta - theta_prev) % (2 * np.pi)
    dtheta_next = (theta_next - theta) % (2 * np.pi)
    ds = R * (dtheta_prev + dtheta_next) / 2

    phi = ((theta - np.pi) + np.pi) % (2 * np.pi) - np.pi  # angle from loaded-arc centerline (theta=180deg)
    if abs(phi) <= np.pi / 2:
        p = p0 * np.cos(phi)
        f_mag = p * T * ds  # N
        fx = f_mag * np.cos(theta)
        fy = f_mag * np.sin(theta)
        model.add_force(load_id, nid, 1.0, [fx, fy, 0.0])
        total_fx += fx
        total_fy += fy

print(f"Target pin load: Fx = {-P_TOTAL:.1f} N")
print(f"Applied resultant:  Fx = {total_fx:.1f} N, Fy = {total_fy:.1f} N")

cc = CaseControlDeck([
    "SUBCASE 1",
    "  SPC = 1",
    "  LOAD = 1",
    "  DISPLACEMENT(PLOT) = ALL",
    "  STRESS(PLOT,CENTER) = ALL",
])
model.case_control_deck = cc
model.add_param("POST", [-1])

model.write_bdf(OUT_BDF, size=8, enddata=True)
print(f"Wrote {OUT_BDF}")
print(f"Material: Ti-6Al-4V, E={E} MPa, Fty={FTY} MPa, Ftu={FTU} MPa (typical annealed, RT)")
