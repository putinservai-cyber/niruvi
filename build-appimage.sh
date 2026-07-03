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
    for lib in libQt6Core libQt6Gui libQt6Widgets libQt6DBus libQt6Network libQt6Svg libQt6Xml; do
        cp -a "$QT6_LIB_DIR/$lib.so"* "$APPDIR/usr/lib64/" 2>/dev/null || true
    done
else
    echo "Warning: Qt6 libraries not found, trying fallback..."
    for lib in libQt6Core libQt6Gui libQt6Widgets libQt6DBus libQt6Network libQt6Svg libQt6Xml; do
        find /usr -name "$lib.so*" -type f,l 2>/dev/null | head -1 | while read -r f; do
            cp -a "$f" "$APPDIR/usr/lib64/" 2>/dev/null || true
        done
    done
fi

echo "==> Copying Qt6 plugins"
QT6_PLUGIN_SRC="/usr/lib64/qt6/plugins"
if [ -d "$QT6_PLUGIN_SRC" ]; then
    mkdir -p "$APPDIR/usr/lib64/qt6/plugins"
    for subdir in platforms imageformats styles; do
        if [ -d "$QT6_PLUGIN_SRC/$subdir" ]; then
            mkdir -p "$APPDIR/usr/lib64/qt6/plugins/$subdir"
            cp -a "$QT6_PLUGIN_SRC/$subdir/"*.so "$APPDIR/usr/lib64/qt6/plugins/$subdir/" 2>/dev/null || true
        fi
    done
fi

echo "==> Bundling shared library dependencies"
_bundle_one_dep() {
    local dep="$1"
    local target_dir="$APPDIR/usr/lib64"
    local basename
    basename="$(basename "$dep")"
    # Skip if already present in any known location
    [ -f "$target_dir/$basename" ] && return 0
    [ -f "$APPDIR/usr/lib64/qt6/plugins/platforms/$basename" ] && return 0
    [ -f "$APPDIR/usr/lib64/qt6/plugins/imageformats/$basename" ] && return 0
    [ -f "$APPDIR/usr/lib64/qt6/plugins/styles/$basename" ] && return 0
    [ -f "$APPDIR/usr/lib64/qt6/plugins/xcbglintegrations/$basename" ] && return 0
    [ -f "$APPDIR/usr/lib64/qt6/plugins/wayland-shell-integration/$basename" ] && return 0
    # Copy the file
    cp -a "$dep" "$target_dir/" 2>/dev/null || return 1
    # If it's a symlink, also copy the real target
    local real
    real="$(readlink -f "$dep")"
    if [ "$real" != "$dep" ] && [ -f "$real" ]; then
        local realbase
        realbase="$(basename "$real")"
        if [ ! -f "$target_dir/$realbase" ]; then
            cp -a "$real" "$target_dir/" 2>/dev/null || true
        fi
    fi
    return 0
}

_bundle_all_elf() {
    local count=0
    # Scan every ELF binary and shared object in the AppDir (Python binary, .so files, plugins)
    find "$APPDIR" -type f \( -name '*.so' -o -name '*.so.*' \) -print0 2>/dev/null | while IFS= read -r -d '' sofile; do
        ldd "$sofile" 2>/dev/null | grep '=> /' | awk '{print $3}' | while IFS= read -r dep; do
            _bundle_one_dep "$dep" && count=$((count+1))
        done
    done
    # Also scan the Python binary itself
    if [ -f "$APPDIR/usr/bin/python3" ]; then
        ldd "$APPDIR/usr/bin/python3" 2>/dev/null | grep '=> /' | awk '{print $3}' | while IFS= read -r dep; do
            _bundle_one_dep "$dep"
        done
    fi
}

# Run up to 5 passes to catch all transitive dependencies
for i in 1 2 3 4 5; do
    before=$(find "$APPDIR/usr/lib64" -type f -name '*.so*' 2>/dev/null | wc -l)
    _bundle_all_elf
    after=$(find "$APPDIR/usr/lib64" -type f -name '*.so*' 2>/dev/null | wc -l)
    echo "    Pass $i: $before -> $after libs"
    [ "$after" -eq "$before" ] && break
done

echo "==> Bundling data files (ICU, XKB, fontconfig, etc.)"
mkdir -p "$APPDIR/usr/share"
for dir in /usr/share/X11/xkb /usr/share/fonts /usr/share/fontconfig /usr/share/icons; do
    [ -d "$dir" ] && cp -r "$dir" "$APPDIR/usr/share/" 2>/dev/null || true
done
# ICU data
for dir in /usr/share/icu /usr/lib64/icu /usr/lib/icu; do
    if [ -d "$dir" ]; then
        mkdir -p "$APPDIR/usr/share/icu"
        cp -r "$dir/"* "$APPDIR/usr/share/icu/" 2>/dev/null || true
        break
    fi
done
# mimeinfo.cache for MIME type detection
[ -f /usr/share/applications/mimeinfo.cache ] && \
    mkdir -p "$APPDIR/usr/share/applications" && \
    cp /usr/share/applications/mimeinfo.cache "$APPDIR/usr/share/applications/" 2>/dev/null || true

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
if [ -d "$ASSET_DIR/audio" ]; then
    mkdir -p "$APPDIR/asset"
    cp -r "$ASSET_DIR/audio" "$APPDIR/asset/"
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
export QT_QPA_PLATFORM_PLUGIN_PATH="$HERE/usr/lib64/qt6/plugins"
export QT_PLUGIN_PATH="$HERE/usr/lib64/qt6/plugins"
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export FONTCONFIG_PATH="$HERE/usr/share/fontconfig"
export NIRUVI_ICON_DIR="$HERE/icons"
exec "$HERE/usr/bin/python3" -m niruvi.app.self_install "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "==> Building AppImage"
"$ASSET_DIR/appimagetool-x86_64.AppImage" "$APPDIR" "$PROJECT_DIR/$APP-x86_64.AppImage"

echo "==> Cleaning up"
rm -rf "$APPDIR"

echo "==> Done: $PROJECT_DIR/$APP-x86_64.AppImage"
