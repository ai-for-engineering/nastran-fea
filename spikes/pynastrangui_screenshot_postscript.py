"""
Proof-of-concept postscript for driving pyNastranGUI non-interactively to
produce a stress/model screenshot -- spike for GitHub issue #8.

pyNastranGUI accepts `-p SCRIPT.py` ("postscript", runs after geometry/
results are loaded). The script is exec'd with `self` bound to the running
MainWindow instance, giving full access to its Python API: load/results
methods, camera control, screenshotting, and element visibility.

Usage (from the repo root, with venv active and a model+results available):

    ./venv/Scripts/python.exe -m pyNastran.gui.gui \
        -i path/to/model.dat -o path/to/results.OP2 -f nastran \
        -p spikes/pynastrangui_screenshot_postscript.py

Set OUTPUT_PNG below (or edit this file) to control where the screenshot
lands. Findings/gotchas from this spike -- including why QT_QPA_PLATFORM
=offscreen does NOT work here, and a performance concern with hide_eids on
the full-size wingbox model -- are written up on issue #8, not repeated here.
"""

OUTPUT_PNG = "spike_screenshot.png"

self.on_take_screenshot(OUTPUT_PNG, show_msg=False)

import sys
sys.exit(0)
