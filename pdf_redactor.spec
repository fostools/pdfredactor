# PDF Redactor - offline PDF redaction tool
# Copyright (C) 2026 Kiran
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file.
# PyInstaller recipe for PDF Redactor.
# Used by build_mac_app.sh - you normally don't run this directly.
import os
import sys

from PyInstaller.utils.hooks import collect_all

MODEL = os.environ.get("MODEL", "en_core_web_lg")

datas, binaries, hiddenimports = [], [], []
for pkg in ("spacy", "thinc", "srsly", "catalogue", "confection", "spacy_legacy", "spacy_loggers",
            "weasel", "cymem", "preshed", "murmurhash", "blis", "wasabi", "langcodes", "language_data",
            MODEL, "zxingcpp", "pymupdf", "numpy"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

datas += [("data", "data"), ("tessdata", "tessdata")]

# Standalone Pythons (e.g. the one uv downloads) keep Tcl/Tk as separate libraries
# that PyInstaller doesn't always find on its own - bundle them explicitly.
import glob
import sysconfig
_libdir = sysconfig.get_config_var("LIBDIR") or os.path.join(sys.base_prefix, "lib")
for _pat in ("libtcl*", "libtk*"):
    for _f in glob.glob(os.path.join(_libdir, _pat)):
        if _f.endswith((".so", ".dylib")) or ".so." in _f:
            binaries.append((_f, "."))

a = Analysis(
    ["pdf_redactor.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["redact_core"],
    excludes=["torch", "tensorflow", "cupy", "matplotlib", "IPython", "jupyter", "pytest",
              "pandas", "scipy", "PIL", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PDF Redactor",
          console=False, argv_emulation=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="PDF Redactor", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PDF Redactor.app",
        icon="icon.icns" if os.path.exists("icon.icns") else None,
        bundle_identifier="local.pdfredactor",
        info_plist={
            "CFBundleName": "PDF Redactor",
            "CFBundleDisplayName": "PDF Redactor",
            "CFBundleShortVersionString": "1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,
        },
    )
