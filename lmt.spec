# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for LMT.
# Build (on Windows, inside the project folder that contains main.py):
#     pyinstaller lmt.spec
#
# Output: dist/LMT/LMT.exe  (onedir build, recommended)
#
# Onedir vs onefile:
#   This spec builds "onedir" (a folder with the exe + libs) rather than
#   onefile. Onedir starts noticeably faster because onefile has to
#   self-extract to a temp folder on every launch — with scipy/pandas/Qt
#   in the bundle that extraction step adds a few seconds each time.
#   Ship the whole dist/LMT folder (or wrap it with Inno Setup,
#   see installer.iss) rather than trying to hand users a single .exe.

import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# duckdb ships a compiled native extension module; be explicit about
# pulling in all of it (binaries + data + hidden imports) rather than
# relying solely on auto-detection.
duckdb_datas, duckdb_binaries, duckdb_hidden = collect_all('duckdb')

# Pick an icon per platform if you drop one in assets/ (all optional -
# the build works fine with none, PyInstaller just falls back to a
# generic icon). Windows wants .ico, macOS wants .icns; Linux ignores
# this field entirely (AppImage/.desktop supply their own icon).
if sys.platform == 'win32':
    app_icon = 'assets/icon.ico' if __import__('os').path.exists('assets/icon.ico') else None
elif sys.platform == 'darwin':
    app_icon = 'assets/icon.icns' if __import__('os').path.exists('assets/icon.icns') else None
else:
    app_icon = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=duckdb_binaries,
    datas=duckdb_datas,
    hiddenimports=duckdb_hidden + [
        'pyqtgraph.imageview',
        'scipy.special.cython_special',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # PySide6 pulls in modules this app doesn't use; trimming them
        # cuts a meaningful chunk off the bundle size.
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia',
        'PySide6.QtNetwork',
        'PySide6.QtPdf',
        'PySide6.Qt3DCore',
        'matplotlib',
        'tkinter',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LMT',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,       # GUI app: no console window
    icon=app_icon,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LMT',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='LMT.app',
        icon=app_icon,
        bundle_identifier='com.example.lmt',
        info_plist={
            'NSHighResolutionCapable': 'True',
            'CFBundleShortVersionString': '1.0.0',
        },
    )
