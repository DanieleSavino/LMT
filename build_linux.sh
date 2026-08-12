#!/usr/bin/env bash
#
# build_linux.sh - builds LMT into a distributable AppImage.
#
# Usage:
#   chmod +x build_linux.sh
#   ./build_linux.sh
#
# Run this from the project root (the folder containing main.py and
# lmt.spec). Output ends up at ./LMT-x86_64.AppImage - that's the
# single file you hand to users. They chmod +x it (or right-click ->
# Properties -> "Allow executing as program") and double-click.
#
# Build this on the OLDEST glibc-based distro you need to support
# (e.g. Ubuntu 22.04), not a bleeding-edge one - AppImages are
# forward-compatible but not backward-compatible with glibc.

set -euo pipefail

APP_NAME="LMT"
BUILD_DIR="build_env"
APPDIR="AppDir"

echo "== 1/4: Python venv + dependencies =="
python3 -m venv "$BUILD_DIR"
source "$BUILD_DIR/bin/activate"
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
pip install pyinstaller --quiet

echo "== 2/4: PyInstaller freeze =="
pyinstaller lmt.spec --noconfirm

if [ ! -f "dist/$APP_NAME/$APP_NAME" ]; then
    echo "ERROR: expected dist/$APP_NAME/$APP_NAME - PyInstaller build failed or name mismatch." >&2
    exit 1
fi

echo "== 3/4: Assemble AppDir =="
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r "dist/$APP_NAME/"* "$APPDIR/usr/bin/"

cat > "$APPDIR/lmt.desktop" <<EOF
[Desktop Entry]
Name=LMT
Comment=Le Mans Ultimate telemetry viewer
Exec=LMT
Icon=lmt
Type=Application
Categories=Utility;Game;
EOF

# Use assets/icon.png if you've added one; otherwise fall back to a
# plain placeholder so linuxdeploy has something to embed. Swap this
# out for a real icon whenever you have one - cosmetic only.
if [ -f "assets/icon.png" ]; then
    cp "assets/icon.png" "$APPDIR/lmt.png"
elif command -v convert >/dev/null 2>&1; then
    convert -size 256x256 xc:steelblue "$APPDIR/lmt.png"
else
    echo "WARNING: no assets/icon.png and no ImageMagick 'convert' found -" \
         "AppImage will build with no icon. Add assets/icon.png and rerun to fix."
fi

echo "== 4/4: Build AppImage =="
if [ ! -f "linuxdeploy-x86_64.AppImage" ]; then
    echo "Downloading linuxdeploy (one-time)..."
    wget -q "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage"
    chmod +x linuxdeploy-x86_64.AppImage
fi

ICON_ARGS=()
if [ -f "$APPDIR/lmt.png" ]; then
    ICON_ARGS=(--icon-file "$APPDIR/lmt.png")
fi

./linuxdeploy-x86_64.AppImage \
    --appdir "$APPDIR" \
    --executable "$APPDIR/usr/bin/$APP_NAME" \
    --desktop-file "$APPDIR/lmt.desktop" \
    "${ICON_ARGS[@]}" \
    --output appimage

# linuxdeploy names the output after the .desktop file's Name= field;
# normalize it to a predictable filename.
OUT_FILE=$(ls ./*.AppImage 2>/dev/null | grep -v linuxdeploy | head -n1 || true)
if [ -n "$OUT_FILE" ] && [ "$OUT_FILE" != "./${APP_NAME}-x86_64.AppImage" ]; then
    mv "$OUT_FILE" "./${APP_NAME}-x86_64.AppImage"
fi

echo
echo "Done: ${APP_NAME}-x86_64.AppImage"
echo "Test it with: ./${APP_NAME}-x86_64.AppImage"
