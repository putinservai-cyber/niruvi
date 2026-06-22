#!/bin/bash
set -e

APP="Niruvi"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ASSET_DIR="$PROJECT_DIR/asset"
APPDIR="$PROJECT_DIR/$APP.AppDir"
PYTHON_VERSION="3.14"
SITE_PACKAGES_SRC="/usr/lib64/python$PYTHON_VERSION/site-packages"

echo "==> Creating AppDir structure"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages"

echo "==> Copying Python binary"
cp "/usr/bin/python3" "$APPDIR/usr/bin/"

echo "==> Copying Python standard library"
cp -r "/usr/lib64/python$PYTHON_VERSION/"* "$APPDIR/usr/lib64/python$PYTHON_VERSION/"
rm -rf "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/"*
for dir in test turtledemo idlelib lib2to3 ensurepip venv; do
    rm -rf "$APPDIR/usr/lib64/python$PYTHON_VERSION/$dir" 2>/dev/null || true
done
for dir in tkinter turtle; do
    rm -rf "$APPDIR/usr/lib64/python$PYTHON_VERSION/$dir" 2>/dev/null || true
done

find "$APPDIR/usr/lib64/python$PYTHON_VERSION" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$APPDIR/usr/lib64/python$PYTHON_VERSION" -name '*.pyc' -delete 2>/dev/null || true

echo "==> Copying libpython"
cp "/usr/lib64/libpython$PYTHON_VERSION.so.1.0" "$APPDIR/usr/lib64/"

echo "==> Copying PyQt6 and sip"
cp -r "$SITE_PACKAGES_SRC/PyQt6" "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/"
cp -r "$SITE_PACKAGES_SRC/pyqt6-"* "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/" 2>/dev/null || true
cp -r "$SITE_PACKAGES_SRC/pyqt6_sip"* "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/" 2>/dev/null || true

echo "==> Copying niruvi package"
cp -r "$PROJECT_DIR/niruvi" "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/"
rm -rf "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/niruvi/__pycache__"

echo "==> Copying Qt6 shared libraries"
for lib in libQt6Core libQt6Gui libQt6Widgets libQt6DBus; do
    cp -a "/lib64/$lib.so"* "$APPDIR/usr/lib64/" 2>/dev/null || true
done

echo "==> Copying AppDir assets"
cp "$ASSET_DIR/niruvi.desktop" "$APPDIR/"
cp "$ASSET_DIR/niruvi.png" "$APPDIR/"
cp "$ASSET_DIR/niruvi.svg" "$APPDIR/"
if [ -d "$ASSET_DIR/icons" ]; then
    cp -r "$ASSET_DIR/icons" "$APPDIR/"
fi

echo "==> Creating AppRun"
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONHOME="$HERE/usr"
export LD_LIBRARY_PATH="$HERE/usr/lib64:$LD_LIBRARY_PATH"
export NIRUVI_ICON_DIR="$HERE/asset/icons"
exec "$HERE/usr/bin/python3" -m niruvi.self_install "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "==> Building AppImage"
"$ASSET_DIR/appimagetool-x86_64.AppImage" "$APPDIR" "$PROJECT_DIR/$APP-x86_64.AppImage"

echo "==> Cleaning up"
rm -rf "$APPDIR"

echo "==> Done: $PROJECT_DIR/$APP-x86_64.AppImage"
