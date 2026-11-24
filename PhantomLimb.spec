# -*- mode: python ; coding: utf-8 -*-

import sys
import os

a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'), ('static', 'static'), ('ml', 'ml')],
    # These are the modules referenced by the persisted models. Keeping this
    # list focused avoids bundling optional torch/matplotlib plugins.
    hiddenimports=[
        'sklearn.base', 'sklearn.ensemble._forest', 'sklearn.preprocessing._data',
        'sklearn.tree._tree', 'sklearn.utils', 'joblib', 'flask_login',
        'requests', 'sqlite3', 'numpy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'tensorflow', 'matplotlib', 'PIL',
        'openpyxl', 'pyarrow', 'numba', 'llvmlite', 'onnxruntime', 'av',
        'sqlalchemy', 'lxml',
    ],
    noarchive=False,
    optimize=0,
)

# When building under Rosetta from Apple's universal system Python, PyInstaller
# may select the ARM framework slice. The override keeps cross-architecture
# builds honest; normal native builds do not need it.
python_library_override = os.environ.get('PHANTOMLIMB_PYTHON_LIBRARY') if sys.platform == 'darwin' else None
if python_library_override:
    a.binaries = [
        (
            destination,
            python_library_override if destination.endswith('Python3.framework/Versions/3.9/Python3') else source,
            typecode,
        )
        for destination, source, typecode in a.binaries
    ]

pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    # Use a normal onedir app bundle on macOS. It keeps ARM and Intel native
    # libraries separate so they can be merged into a valid universal app.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='PhantomLimb',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='PhantomLimb',
    )
    app = BUNDLE(
        coll,
        name='PhantomLimb.app',
        icon=None,
        bundle_identifier='com.ae490.phantomlimb',
    )
else:
    # Keep Windows as a true one-file executable.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        exclude_binaries=False,
        name='PhantomLimb',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
