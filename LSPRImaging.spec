# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

# lspr_ui's vendored Tabler icon SVGs are package-data, not Python source, so
# PyInstaller won't pick them up automatically from hiddenimports alone.
lspr_ui_datas = collect_data_files('lspr_ui', includes=['icon_assets/*.svg'])

a = Analysis(
    ['src\\main.py'],
    pathex=[],
    binaries=[],
    datas=lspr_ui_datas,
    hiddenimports=[
        'lucide',
        'zarr',
        'numcodecs',
        'ome_zarr',
        'imagecodecs',
    ],
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
    name='LSPRImaging',
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
