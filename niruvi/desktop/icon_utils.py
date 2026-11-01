import logging
import os
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QSize, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QPixmap

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtSvg import QSvgRenderer

    HAS_QSVG = True
except ImportError:
    HAS_QSVG = False

# Minimum source resolution we consider worth rasterizing for a given logical
# size. Anything smaller is treated as a low-quality source and upscaled only
# because no better artwork exists.
MIN_RASTER_PX = 32

# Bounded LRU caches. Keyed by (path, mtime, size, dpr) / (digest, size, dpr) so
# that editing an icon on disk invalidates its entry automatically.
_FILE_CACHE: OrderedDict[tuple, QPixmap] = OrderedDict()
_DATA_CACHE: OrderedDict[tuple, QPixmap] = OrderedDict()
_CACHE_LIMIT = 256


def _dpr() -> float:
    """Device pixel ratio of the primary screen, or 1.0 when headless."""
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 1.0
    try:
        ratio = float(screen.devicePixelRatio())
    except (TypeError, ValueError):
        return 1.0
    return ratio if ratio > 0 else 1.0


def screen_dpr() -> float:
    """Public accessor for the primary screen's device pixel ratio."""
    return _dpr()


def _cache_get(cache: OrderedDict, key: tuple) -> QPixmap | None:
    pixmap = cache.get(key)
    if pixmap is not None:
        cache.move_to_end(key)
    return pixmap


def _cache_put(cache: OrderedDict, key: tuple, pixmap: QPixmap) -> None:
    cache[key] = pixmap
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)


def clear_icon_cache() -> None:
    """Drop every cached pixmap (call when the display scale changes)."""
    _FILE_CACHE.clear()
    _DATA_CACHE.clear()


def _scaled(pixmap: QPixmap, size: int, dpr: float) -> QPixmap:
    """Scale to `size` logical px at `dpr` resolution, preserving aspect ratio.

    QPixmap.setDevicePixelRatio tells Qt that the backing store is `dpr` times
    larger than the widget it paints into, which is what keeps artwork sharp on
    HiDPI screens instead of being stretched from a half-resolution bitmap.
    """
    target = max(1, round(size * dpr))
    scaled = pixmap.scaled(
        target,
        target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    return scaled


def to_png_bytes(data: bytes) -> bytes | None:
    if data[:4] == b"\x89PNG":
        return data

    if _is_svg_data(data):
        converted = _svg_to_png(data)
        if converted and converted[:4] == b"\x89PNG":
            return converted
        return None

    image = QImage()
    if image.loadFromData(data):
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        result = buf.data().data()
        if result[:4] == b"\x89PNG":
            return result
    return None


def get_pixmap_from_data(data: bytes, size: int = 64) -> QPixmap | None:
    import hashlib

    dpr = _dpr()
    key = (hashlib.sha1(data).hexdigest(), size, dpr)
    cached = _cache_get(_DATA_CACHE, key)
    if cached is not None:
        return cached

    png = to_png_bytes(data)
    if not png:
        return None
    pixmap = QPixmap()
    if not pixmap.loadFromData(png):
        return None
    result = _scaled(pixmap, size, dpr)
    _cache_put(_DATA_CACHE, key, result)
    return result


def get_pixmap_from_file(path: str, size: int = 64) -> QPixmap | None:
    """Load `path` as a `size`-logical-pixel pixmap, HiDPI aware and cached.

    Returns None (never raises) when the file is missing or undecodable so a
    broken icon can never take the application down.
    """
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        logger.debug("Icon file unavailable: %s", path)
        return None

    dpr = _dpr()
    key = (path, stat.st_mtime_ns, stat.st_size, size, dpr)
    cached = _cache_get(_FILE_CACHE, key)
    if cached is not None:
        return cached

    try:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            logger.debug("Undecodable icon: %s", path)
            return None
    except Exception as e:
        logger.debug("Failed to load pixmap from %s: %s", path, e, exc_info=True)
        return None

    # An SVG source renders crisply at any size, so prefer it over a raster that
    # may be tiny. get_pixmap_from_file already handles that via Qt's SVG
    # image plugin; this branch just guards against upscaling artifacts.
    result = _scaled(pixmap, size, dpr)
    _cache_put(_FILE_CACHE, key, result)
    return result


def save_icon_to_png(data: bytes, dest_path: str) -> bool:
    png = to_png_bytes(data)
    if png:
        try:
            target = Path(dest_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write so a crash mid-write cannot leave a truncated icon
            # that later renders as a corrupt file.
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(png)
            tmp.replace(target)
            return True
        except OSError:
            pass
    return False


# Fallback chain used when an application has no usable artwork of its own.
GENERIC_APP_ICON_NAMES = ("package-x-generic", "application-x-archive", "applications-system")


def generic_app_icon(size: int = 32):
    """Return the shared generic-application QIcon (never null)."""
    from niruvi.utils import get_icon

    for name in GENERIC_APP_ICON_NAMES:
        icon = get_icon(name)
        if icon and not icon.isNull():
            return icon
    return get_icon("package-x-generic")


def app_icon(path: str | os.PathLike | None, size: int = 32):
    """Resolve an application icon, falling back gracefully.

    Order: the file at `path` (any format Qt can decode) -> theme generic app
    icon. Never returns a null QIcon, so callers can always paint something.
    """
    if path:
        pixmap = get_pixmap_from_file(str(path), size)
        if pixmap is not None and not pixmap.isNull():
            from PyQt6.QtGui import QIcon

            icon = QIcon(pixmap)
            if not icon.isNull():
                return icon
    return generic_app_icon(size)


def _svg_to_png(data: bytes, target_size: int = 256) -> bytes | None:
    """Rasterize SVG bytes to PNG at a usable resolution.

    Renders at max(target_size, declared size) so that an SVG advertising a
    16x16 default size is not permanently baked into a low-resolution PNG.
    """
    if HAS_QSVG:
        try:
            renderer = QSvgRenderer(data)
            if not renderer.isValid():
                raise RuntimeError("invalid SVG")

            size = renderer.defaultSize()
            if size.width() <= 0 or size.height() <= 0:
                view_box = renderer.viewBox()
                if view_box.isValid() and view_box.width() > 0 and view_box.height() > 0:
                    size = view_box.size()
                else:
                    size = QSize(target_size, target_size)
            else:
                longest = max(size.width(), size.height())
                if longest < target_size:
                    scale = target_size / longest
                    size = QSize(
                        max(1, round(size.width() * scale)),
                        max(1, round(size.height() * scale)),
                    )

            image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            try:
                renderer.render(painter)
            finally:
                painter.end()

            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buf, "PNG")
            result = buf.data().data()
            if result[:4] == b"\x89PNG":
                return result
        except Exception as e:
            logger.debug("SVG-to-PNG via Qt failed: %s", e, exc_info=True)

    try:
        import subprocess

        rsvg = subprocess.run(
            ["rsvg-convert", "-w", str(target_size), "-h", str(target_size), "-f", "png"],
            input=data,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if rsvg.returncode == 0 and rsvg.stdout[:4] == b"\x89PNG":
            return rsvg.stdout
    except Exception as e:
        logger.debug("SVG-to-PNG via rsvg-convert failed: %s", e, exc_info=True)

    return None


def _is_svg_data(data: bytes) -> bool:
    try:
        head = data[:200].decode("utf-8", errors="ignore").strip().lower()
        return head.startswith("<?xml") or head.startswith("<svg")
    except Exception as e:
        logger.debug("Failed to detect SVG data: %s", e, exc_info=True)
        return False
