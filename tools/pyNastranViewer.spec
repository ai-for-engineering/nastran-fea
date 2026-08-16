# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['pynastran_viewer_launcher.py'],
    pathex=[],
    binaries=[('C:/Users/benna/miniforge3/Library/bin/tcl86t.dll', '.'), ('C:/Users/benna/miniforge3/Library/bin/tk86t.dll', '.'), ('C:/Users/benna/miniforge3/Library/bin/liblzma.dll', '.'), ('C:/Users/benna/miniforge3/Library/bin/libbz2.dll', '.')],
    datas=[('C:/Users/benna/miniforge3/Library/lib/tcl8.6', 'tcl8.6'), ('C:/Users/benna/miniforge3/Library/lib/tk8.6', 'tk8.6')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='pyNastranViewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
