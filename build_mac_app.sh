#!/bin/bash
# Copyright (C) 2026 Kiran
# SPDX-License-Identifier: AGPL-3.0-or-later
# Builds "PDF Redactor.app" (and a .dmg) that runs on Macs with nothing else installed.
# Run once, on any Mac:
#     ./build_mac_app.sh                          # large AI model (best names, ~1 GB app)
#     MODEL=en_core_web_sm ./build_mac_app.sh     # small model (~300 MB app)
# Uses a Python 3.10-3.14 if you have one; otherwise downloads a private Python 3.12
# just for this build (your system Python is not touched).
set -euo pipefail
cd "$(dirname "$0")"

MODEL="${MODEL:-en_core_web_lg}"
SPACY_MODEL_VERSION="3.8.0"
HERE="$(pwd)"

# Python must be 3.10-3.14 (spaCy has no ready-made packages for newer versions yet) and have Tk.
usable() {
  [ -n "$1" ] && [ -x "$(command -v "$1" 2>/dev/null)" ] &&
    "$1" -c "import sys, tkinter; assert (3, 10) <= sys.version_info[:2] <= (3, 14)" 2>/dev/null
}

# 1. Find a suitable Python (newest supported first)
PY=""
CANDIDATES=("${PYTHON:-}")
for v in 3.13 3.12 3.14 3.11 3.10; do
  CANDIDATES+=("/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
               "/opt/homebrew/opt/python@$v/bin/python$v" "/usr/local/opt/python@$v/bin/python$v"
               "python$v")
done
CANDIDATES+=(python3)
for c in "${CANDIDATES[@]}"; do
  if usable "$c"; then PY="$(command -v "$c")"; break; fi
done

# 2. None found: fetch a private Python 3.12 (with Tk) using uv
if [ -z "$PY" ]; then
  echo "==> No Python 3.10-3.14 with Tk found (yours may be too new, e.g. 3.15)."
  echo "==> Downloading a private Python 3.12 for this build only..."
  UV="$(command -v uv 2>/dev/null || true)"
  if [ -z "$UV" ]; then
    BOOT="$(command -v python3 2>/dev/null || true)"
    if [ -n "$BOOT" ] && "$BOOT" -m venv "$HERE/.uv-tool" >/dev/null 2>&1 &&
       "$HERE/.uv-tool/bin/python" -m pip install --quiet --only-binary=:all: uv; then
      UV="$HERE/.uv-tool/bin/uv"
    else
      curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HERE/.uv-tool/bin" INSTALLER_NO_MODIFY_PATH=1 sh
      UV="$HERE/.uv-tool/bin/uv"
    fi
  fi
  export UV_PYTHON_INSTALL_DIR="$HERE/.uv-python"
  "$UV" python install --no-bin 3.12
  PY="$("$UV" python find 3.12)"
  if ! usable "$PY"; then
    echo "Couldn't set up a Python with Tk automatically."
    echo "Please install Python 3.13 from https://www.python.org/downloads/macos/ and run this again."
    exit 1
  fi
fi
echo "==> Using $PY ($("$PY" --version))"

# 3. Clean, isolated build environment - ready-made packages only, never compile from source
rm -rf .build-venv build dist
"$PY" -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
PIP="python -m pip install --quiet --only-binary=:all:"
python -m pip install --quiet --upgrade pip
$PIP "pymupdf>=1.25" "spacy>=3.8,<3.9" "zxing-cpp>=2.2" numpy "pyinstaller>=6.10"
echo "==> Downloading AI model $MODEL"
$PIP "https://github.com/explosion/spacy-models/releases/download/${MODEL}-${SPACY_MODEL_VERSION}/${MODEL}-${SPACY_MODEL_VERSION}-py3-none-any.whl"

# 4. Check everything works before packaging
echo "==> Self-test (source)"
python pdf_redactor.py --selftest

# 5. App icon (built-in macOS tools)
if [ -f icon.png ] && command -v iconutil >/dev/null; then
  rm -rf icon.iconset && mkdir icon.iconset
  for s in 16 32 128 256 512; do
    sips -z $s $s icon.png --out "icon.iconset/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2)); sips -z $d $d icon.png --out "icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns icon.iconset -o icon.icns && rm -rf icon.iconset
fi

# 6. Build the app
echo "==> Building app (a few minutes)"
MODEL="$MODEL" pyinstaller --noconfirm --clean --log-level WARN pdf_redactor.spec

APP="dist/PDF Redactor.app"
echo "==> Self-test (packaged app)"
"$APP/Contents/MacOS/PDF Redactor" --selftest

# 7. Disk image for copying to other Macs
echo "==> Making disk image"
rm -rf dist/dmg && mkdir dist/dmg && cp -R "$APP" dist/dmg/ && ln -s /Applications dist/dmg/Applications
hdiutil create -quiet -volname "PDF Redactor" -srcfolder dist/dmg -ov -format UDZO "dist/PDF-Redactor-$(uname -m).dmg"
rm -rf dist/dmg

deactivate
echo
echo "Done:"
echo "  App:        $APP   (drag it to Applications)"
echo "  Disk image: dist/PDF-Redactor-$(uname -m).dmg   (copy this to other Macs)"
echo "Built for $(uname -m) Macs ($( [ "$(uname -m)" = arm64 ] && echo 'Apple Silicon' || echo 'Intel'))."
