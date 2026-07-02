from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QSize, Qt
from PyQt6.QtGui import QPixmap, QIcon, QImage, QPainter

try:
    from PyQt6.QtSvg import QSvgRenderer
    HAS_QSVG = True
except ImportError:
    HAS_QSVG = False


def to_png_bytes(data: bytes) -> bytes | None:
    if data[:4] == b'\x89PNG':
        return data

    if _is_svg_data(data):
        converted = _svg_to_png(data)
        if converted and converted[:4] == b'\x89PNG':
            return converted
        return None

    image = QImage()
    if image.loadFromData(data):
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        result = bytes(buf.data())
        if result[:4] == b'\x89PNG':
            return result
    return None


def get_pixmap_from_data(data: bytes, size: int = 64) -> QPixmap | None:
    png = to_png_bytes(data)
    if png:
        pixmap = QPixmap()
        if pixmap.loadFromData(png):
            return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return None


def get_pixmap_from_file(path: str, size: int = 64) -> QPixmap | None:
    try:
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    except Exception:
        pass
    return None


def save_icon_to_png(data: bytes, dest_path: str) -> bool:
    png = to_png_bytes(data)
    if png:
        try:
            Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(dest_path).write_bytes(png)
            return True
        except OSError:
            pass
    return False


def _svg_to_png(data: bytes, target_size: int = 256) -> bytes | None:
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
            result = bytes(buf.data())
            if result[:4] == b'\x89PNG':
                return result
        except Exception:
            pass

    try:
        import subprocess
        rsvg = subprocess.run(
            ["rsvg-convert", "-w", str(target_size), "-h", str(target_size), "-f", "png"],
            input=data, capture_output=True, timeout=10,
        )
        if rsvg.returncode == 0 and rsvg.stdout[:4] == b'\x89PNG':
            return rsvg.stdout
    except Exception:
        pass

    return data


def _is_svg_data(data: bytes) -> bool:
    try:
        head = data[:200].decode('utf-8', errors='ignore').strip().lower()
        return head.startswith("<?xml") or head.startswith("<svg")
    except Exception:
        return False
