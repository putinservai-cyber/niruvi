#!/bin/bash
set -e

APP="Niruvi"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ASSET_DIR="$PROJECT_DIR/asset"
APPDIR="$PROJECT_DIR/$APP.AppDir"

# Detect Python version dynamically
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_LIB_DIR="$($PYTHON_BIN -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')"
PYTHON_LIB_PARENT="$(dirname "$PYTHON_LIB_DIR")"
SITE_PACKAGES_SRC="$($PYTHON_BIN -c 'import PyQt6, os; print(os.path.dirname(os.path.dirname(PyQt6.__file__)))' 2>/dev/null)" || SITE_PACKAGES_SRC="$($PYTHON_BIN -c 'import sysconfig; print(sysconfig.get_path("platlib"))')"
LIBPYTHON_PATH="$($PYTHON_BIN -c 'import sysconfig; print(sysconfig.get_config_var("INSTSONAME") or "")')"

if [ -z "$LIBPYTHON_PATH" ]; then
    LIBPYTHON_PATH="libpython$PYTHON_VERSION.so.1.0"
fi

# Find the actual Qt6 lib directory
QT6_LIB_DIR=""
for d in /usr/lib64 /usr/lib /usr/lib/x86_64-linux-gnu; do
    if ls "$d"/libQt6Core* 1>/dev/null 2>&1; then
        QT6_LIB_DIR="$d"
        break
    fi
done

echo "==> Python: $PYTHON_VERSION"
echo "==> Python lib dir: $PYTHON_LIB_DIR"
echo "==> Site packages: $SITE_PACKAGES_SRC"
echo "==> Qt6 lib dir: ${QT6_LIB_DIR:-not found}"

echo "==> Creating AppDir structure"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages"

echo "==> Copying Python binary"
cp "$(which $PYTHON_BIN)" "$APPDIR/usr/bin/python3"

echo "==> Copying Python standard library"
cp -r "$PYTHON_LIB_DIR"/* "$APPDIR/usr/lib64/python$PYTHON_VERSION/" 2>/dev/null || \
cp -r "$PYTHON_LIB_PARENT"/* "$APPDIR/usr/lib64/" 2>/dev/null || true

rm -rf "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/"*
for dir in test turtledemo idlelib lib2to3 ensurepip venv tkinter turtle; do
    rm -rf "$APPDIR/usr/lib64/python$PYTHON_VERSION/$dir" 2>/dev/null || true
done

find "$APPDIR/usr/lib64/python$PYTHON_VERSION" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$APPDIR/usr/lib64/python$PYTHON_VERSION" -name '*.pyc' -delete 2>/dev/null || true

echo "==> Copying libpython"
find /usr -name "$LIBPYTHON_PATH" -type f,l 2>/dev/null | head -1 | while read -r lp; do
    cp -a "$lp" "$APPDIR/usr/lib64/"
    # Also copy any symlink targets
    REAL="$(readlink -f "$lp")"
    if [ "$REAL" != "$lp" ]; then
        cp -a "$REAL" "$APPDIR/usr/lib64/" 2>/dev/null || true
    fi
done

echo "==> Copying PyQt6 and sip"
cp -r "$SITE_PACKAGES_SRC/PyQt6" "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/" 2>/dev/null || true
cp -r "$SITE_PACKAGES_SRC/pyqt6/"* "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/" 2>/dev/null || true
cp -r "$SITE_PACKAGES_SRC/sip"* "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/" 2>/dev/null || true

echo "==> Copying niruvi package"
cp -r "$PROJECT_DIR/niruvi" "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/"
find "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages/niruvi" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> Copying Qt6 shared libraries"
if [ -n "$QT6_LIB_DIR" ]; then
    for lib in libQt6Core libQt6Gui libQt6Widgets libQt6DBus; do
        cp -a "$QT6_LIB_DIR/$lib.so"* "$APPDIR/usr/lib64/" 2>/dev/null || true
    done
else
    echo "Warning: Qt6 libraries not found, trying fallback..."
    for lib in libQt6Core libQt6Gui libQt6Widgets libQt6DBus; do
        find /usr -name "$lib.so*" -type f,l 2>/dev/null | head -1 | while read -r f; do
            cp -a "$f" "$APPDIR/usr/lib64/" 2>/dev/null || true
        done
    done
fi

echo "==> Copying AppDir assets"
cp "$ASSET_DIR/niruvi.desktop" "$APPDIR/"
cp "$ASSET_DIR/niruvi.png" "$APPDIR/"
cp "$ASSET_DIR/niruvi.svg" "$APPDIR/"
if [ -f "$ASSET_DIR/LICENSE" ]; then
    cp "$ASSET_DIR/LICENSE" "$APPDIR/LICENSE"
fi
if [ -d "$ASSET_DIR/icons" ]; then
    cp -r "$ASSET_DIR/icons" "$APPDIR/"
fi
if [ -d "$ASSET_DIR/screenshot" ]; then
    cp -r "$ASSET_DIR/screenshot" "$APPDIR/screenshot"
fi

echo "==> Creating AppRun"
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONHOME="$HERE/usr"
export LD_LIBRARY_PATH="$HERE/usr/lib64:$LD_LIBRARY_PATH"
export NIRUVI_ICON_DIR="$HERE/icons"
exec "$HERE/usr/bin/python3" -m niruvi.self_install "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "==> Building AppImage"
"$ASSET_DIR/appimagetool-x86_64.AppImage" "$APPDIR" "$PROJECT_DIR/$APP-x86_64.AppImage"

echo "==> Cleaning up"
rm -rf "$APPDIR"

echo "==> Done: $PROJECT_DIR/$APP-x86_64.AppImage"
