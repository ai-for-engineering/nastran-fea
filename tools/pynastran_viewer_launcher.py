import os
import sys

if getattr(sys, "frozen", False):
    bundle_dir = sys._MEIPASS
    os.environ.setdefault("TCL_LIBRARY", os.path.join(bundle_dir, "tcl8.6"))
    os.environ.setdefault("TK_LIBRARY", os.path.join(bundle_dir, "tk8.6"))

import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

VENV_PYTHON = Path(r"C:\Users\benna\OneDrive\Bureau\Projets\ai-for-engineering\nastran-fea\venv\Scripts\python.exe")


def browse_bdf():
    path = filedialog.askopenfilename(
        title="Select Nastran model file",
        filetypes=[("Nastran BDF/DAT", "*.bdf *.dat *.nas"), ("All files", "*.*")],
    )
    if path:
        bdf_var.set(path)


def browse_op2():
    path = filedialog.askopenfilename(
        title="Select Nastran results file (optional)",
        filetypes=[("Nastran OP2", "*.op2"), ("All files", "*.*")],
    )
    if path:
        op2_var.set(path)


def launch():
    bdf_path = bdf_var.get().strip()
    op2_path = op2_var.get().strip()

    if not bdf_path:
        messagebox.showerror("pyNastran Viewer", "Please select a .bdf/.dat model file.")
        return
    if not Path(bdf_path).is_file():
        messagebox.showerror("pyNastran Viewer", f"Model file not found:\n{bdf_path}")
        return
    if op2_path and not Path(op2_path).is_file():
        messagebox.showerror("pyNastran Viewer", f"Results file not found:\n{op2_path}")
        return
    if not VENV_PYTHON.is_file():
        messagebox.showerror(
            "pyNastran Viewer",
            "Could not find the Python environment with pyNastran installed:\n"
            f"{VENV_PYTHON}\n\nReinstall the nastran-fea project venv.",
        )
        return

    args = [str(VENV_PYTHON), "-m", "pyNastran.gui.gui", "-i", bdf_path, "-f", "nastran"]
    if op2_path:
        args += ["-o", op2_path]

    try:
        subprocess.Popen(
            args,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as exc:
        messagebox.showerror("pyNastran Viewer", f"Failed to launch pyNastran GUI:\n{exc}")
        return

    root.destroy()


root = tk.Tk()
root.title("pyNastran Model Viewer")
root.resizable(False, False)

pad = {"padx": 10, "pady": 6}

tk.Label(root, text="Model file (.bdf / .dat):").grid(row=0, column=0, columnspan=3, sticky="w", **pad)
bdf_var = tk.StringVar()
tk.Entry(root, textvariable=bdf_var, width=55).grid(row=1, column=0, columnspan=2, padx=(10, 0), pady=2)
tk.Button(root, text="Browse...", command=browse_bdf).grid(row=1, column=2, padx=10, pady=2)

tk.Label(root, text="Results file (.op2) - optional:").grid(row=2, column=0, columnspan=3, sticky="w", **pad)
op2_var = tk.StringVar()
tk.Entry(root, textvariable=op2_var, width=55).grid(row=3, column=0, columnspan=2, padx=(10, 0), pady=2)
tk.Button(root, text="Browse...", command=browse_op2).grid(row=3, column=2, padx=10, pady=2)

tk.Button(root, text="Open in pyNastran GUI", command=launch, width=28).grid(
    row=4, column=0, columnspan=3, pady=14
)

root.mainloop()
