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
echo "==> Site packages: $SITE_PACKAGES_SRC"

echo "==> Creating AppDir structure"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib64/python$PYTHON_VERSION/site-packages"

echo "==> Copying Python binary"
cp "$(which $PYTHON_BIN)" "$APPDIR/usr/bin/python3"
if command -v strip &>/dev/null; then
    strip "$APPDIR/usr/bin/python3" 2>/dev/null || true
fi

echo "==> Copying Python standard library (pruned)"
PYLIB="$APPDIR/usr/lib64/python$PYTHON_VERSION"
cp -r "$PYTHON_LIB_DIR"/* "$PYLIB/" 2>/dev/null || true

# Remove bundled site-packages (we copy our own later)
rm -rf "$PYLIB/site-packages/"*

# Prune Python stdlib — only test suites, doc tools, and Tk (unused with PyQt6)
for dir in test turtledemo idlelib lib2to3 ensurepip venv tkinter turtle \
    distutils pydoc_data __phello__; do
    rm -rf "$PYLIB/$dir" 2>/dev/null || true
done

# Remove .pyc, __pycache__
find "$PYLIB" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$PYLIB" -name '*.pyc' -delete 2>/dev/null || true

# Strip .so files if available
if command -v strip &>/dev/null; then
    find "$PYLIB" -name '*.so' -exec strip --strip-unneeded {} \; 2>/dev/null || true
fi

echo "==> Copying libpython"
lp="$(find /usr -name "$LIBPYTHON_PATH" -type f,l 2>/dev/null | head -1)"
if [ -n "$lp" ]; then
    cp -a "$lp" "$APPDIR/usr/lib64/"
    REAL="$(readlink -f "$lp")"
    [ "$REAL" != "$lp" ] && cp -a "$REAL" "$APPDIR/usr/lib64/" 2>/dev/null || true
fi

echo "==> Copying PyQt6 and sip"
for pkg in PyQt6; do
    [ -d "$SITE_PACKAGES_SRC/$pkg" ] && cp -r "$SITE_PACKAGES_SRC/$pkg" "$PYLIB/site-packages/"
done
cp -r "$SITE_PACKAGES_SRC/sip"* "$PYLIB/site-packages/" 2>/dev/null || true

# Prune PyQt6: remove QML (not needed), examples, and .pyi type stubs
rm -rf "$PYLIB/site-packages/PyQt6/Qt6/qml" 2>/dev/null || true
find "$PYLIB/site-packages/PyQt6" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$PYLIB/site-packages/PyQt6" -name '*.pyi' -delete 2>/dev/null || true
# Strip oversized .so in PyQt6
find "$PYLIB/site-packages/PyQt6" -name '*.so' -exec strip --strip-unneeded {} \; 2>/dev/null || true

echo "==> Copying niruvi package"
cp -r "$PROJECT_DIR/niruvi" "$PYLIB/site-packages/"
find "$PYLIB/site-packages/niruvi" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$PYLIB/site-packages/niruvi" -name '*.pyc' -delete 2>/dev/null || true

echo "==> Copying Qt6 shared libraries (minimal)"
if [ -n "$QT6_LIB_DIR" ]; then
    for lib in libQt6Core libQt6Gui libQt6Widgets libQt6DBus libQt6Network libQt6Svg; do
        cp -aL "$QT6_LIB_DIR/$lib.so"* "$APPDIR/usr/lib64/" 2>/dev/null || true
    done
    strip --strip-unneeded "$APPDIR/usr/lib64"/libQt6*.so.* 2>/dev/null || true
fi

echo "==> Copying Qt6 plugins (only essential)"
QT6_PLUGIN_SRC="/usr/lib64/qt6/plugins"
if [ -d "$QT6_PLUGIN_SRC" ]; then
    mkdir -p "$APPDIR/usr/lib64/qt6/plugins"
    # Platforms: only xcb (Wayland plugin is huge and often incompatible)
    if [ -d "$QT6_PLUGIN_SRC/platforms" ]; then
        mkdir -p "$APPDIR/usr/lib64/qt6/plugins/platforms"
        for f in libqxcb.so; do
            [ -f "$QT6_PLUGIN_SRC/platforms/$f" ] && cp -a "$QT6_PLUGIN_SRC/platforms/$f" "$APPDIR/usr/lib64/qt6/plugins/platforms/"
        done
    fi
    # Image formats: only common ones
    if [ -d "$QT6_PLUGIN_SRC/imageformats" ]; then
        mkdir -p "$APPDIR/usr/lib64/qt6/plugins/imageformats"
        for f in libqgif.so libqico.so libqjpeg.so libqsvg.so libqtiff.so libqwebp.so libqwbmp.so; do
            [ -f "$QT6_PLUGIN_SRC/imageformats/$f" ] && cp -a "$QT6_PLUGIN_SRC/imageformats/$f" "$APPDIR/usr/lib64/qt6/plugins/imageformats/"
        done
    fi
    # Styles: only needed for Fusion style
    if [ -d "$QT6_PLUGIN_SRC/styles" ]; then
        mkdir -p "$APPDIR/usr/lib64/qt6/plugins/styles"
        cp -a "$QT6_PLUGIN_SRC/styles/"*.so "$APPDIR/usr/lib64/qt6/plugins/styles/" 2>/dev/null || true
    fi
    strip --strip-unneeded "$APPDIR/usr/lib64/qt6/plugins"/*/*.so 2>/dev/null || true
fi

echo "==> Bundling shared library dependencies (1 pass, dedup)"
_bundle_one_dep() {
    local dep="$1"
    local target_dir="$APPDIR/usr/lib64"
    local basename
    basename="$(basename "$dep")"
    [ -f "$target_dir/$basename" ] && return 0
    [ -f "$APPDIR/usr/lib64/qt6/plugins/platforms/$basename" ] && return 0
    [ -f "$APPDIR/usr/lib64/qt6/plugins/imageformats/$basename" ] && return 0
    [ -f "$APPDIR/usr/lib64/qt6/plugins/styles/$basename" ] && return 0
    cp -aL "$dep" "$target_dir/" 2>/dev/null || return 1
    real="$(readlink -f "$dep")"
    if [ "$real" != "$dep" ] && [ -f "$real" ]; then
        rb="$(basename "$real")"
        [ ! -f "$target_dir/$rb" ] && cp -a "$real" "$target_dir/" 2>/dev/null || true
    fi
    return 0
}

# Single pass ELF scanning
find "$APPDIR" -type f \( -name '*.so' -o -name '*.so.*' \) -print0 2>/dev/null \
    | while IFS= read -r -d '' sofile; do
    ldd "$sofile" 2>/dev/null | grep '=> /' | awk '{print $3}' \
        | while IFS= read -r dep; do
        _bundle_one_dep "$dep"
    done
done
ldd "$APPDIR/usr/bin/python3" 2>/dev/null | grep '=> /' | awk '{print $3}' \
    | while IFS= read -r dep; do
    _bundle_one_dep "$dep"
done

# Strip all .so files to reduce size
echo "==> Stripping debug symbols from .so files"
find "$APPDIR/usr/lib64" -name '*.so*' -exec strip --strip-unneeded {} \; 2>/dev/null || true

echo "==> Bundling data files (minimal)"
mkdir -p "$APPDIR/usr/share"
# XKB — essential for keyboard input
if [ -d "/usr/share/X11/xkb" ]; then
    mkdir -p "$APPDIR/usr/share/X11"
    cp -r /usr/share/X11/xkb "$APPDIR/usr/share/X11/"
fi
# Fontconfig — small and essential
if [ -d "/usr/share/fontconfig" ]; then
    cp -r /usr/share/fontconfig "$APPDIR/usr/share/"
fi
# Minimal font subset — 5M max
if [ -d "/usr/share/fonts" ]; then
    mkdir -p "$APPDIR/usr/share/fonts"
    for pattern in "cantarell" "noto-sans" "dejavu" "liberation" "droid"; do
        for fmt in otf ttf woff woff2; do
            while IFS= read -r -d '' f; do
                subdir="$(basename "$(dirname "$f")")"
                mkdir -p "$APPDIR/usr/share/fonts/$subdir"
                cp -a "$f" "$APPDIR/usr/share/fonts/$subdir/"
            done < <(find /usr/share/fonts -iname "*${pattern}*.$fmt" -type f -print0 2>/dev/null)
        done
    done
fi
# Minimal icons — only bundle Phosphor icons (our custom set)
if [ -d "$ASSET_DIR/icons" ]; then
    cp -r "$ASSET_DIR/icons" "$APPDIR/"
fi

echo "==> Copying AppDir assets"
cp "$ASSET_DIR/niruvi.desktop" "$APPDIR/"
cp "$ASSET_DIR/niruvi.png" "$APPDIR/"
cp "$ASSET_DIR/niruvi.svg" "$APPDIR/"
if [ -f "$ASSET_DIR/LICENSE" ]; then
    cp "$ASSET_DIR/LICENSE" "$APPDIR/LICENSE"
fi
if [ -d "$ASSET_DIR/audio" ]; then
    mkdir -p "$APPDIR/asset"
    cp -r "$ASSET_DIR/audio" "$APPDIR/asset/"
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
export NIRUVI_ICON_DIR="$HERE/icons"
exec "$HERE/usr/bin/python3" -m niruvi.app.self_install "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "==> Final AppDir size:"
du -sh "$APPDIR"

echo "==> Copying AppStream metadata"
mkdir -p "$APPDIR/usr/share/metainfo"
cp "$ASSET_DIR/niruvi.appdata.xml" "$APPDIR/usr/share/metainfo/"

echo "==> Building AppImage"
UPDATE_INFO="gh-releases-zsync|putinservai-cyber|niruvi|latest|${APP}-x86_64.AppImage.zsync"
"$ASSET_DIR/appimagetool-x86_64.AppImage" \
    --update-info "$UPDATE_INFO" \
    "$APPDIR" "$PROJECT_DIR/$APP-x86_64.AppImage"

echo "==> Cleaning up"
rm -rf "$APPDIR"

echo "==> Done: $PROJECT_DIR/$APP-x86_64.AppImage"
