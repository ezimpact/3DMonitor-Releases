# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path
static=Path(SPECPATH)/"k1-monitor/k1monitor/static"
static_files=[(str(path),str(Path("k1monitor/static")/path.relative_to(static).parent)) for path in static.rglob("*") if path.is_file() and "vendor" not in path.relative_to(static).parts]

hiddenimports = []
hiddenimports += collect_submodules('uvicorn')


a = Analysis(
    ['k1-monitor/run_monitor.py'],
    pathex=[],
    binaries=[],
    datas=static_files+[(str(Path(SPECPATH)/"k1-monitor/k1monitor/update_install.ps1"),"k1monitor")],
    hiddenimports=hiddenimports,
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
    name='3DMonitor',
    icon='k1-monitor/k1monitor/static/k1monitor.ico',
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
