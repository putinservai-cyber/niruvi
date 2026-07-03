from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QPainter, QPalette, QPen
from PyQt6.QtWidgets import QWidget


class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, parent=None, initial: bool = False):
        super().__init__(parent)
        self._checked = initial
        self._handle_pos = 0.0
        self.setFixedSize(48, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim: QPropertyAnimation | None = None

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if checked != self._checked:
            self._checked = checked
            self._animate()
            self.toggled.emit(checked)
        elif self._handle_pos != (1.0 if checked else 0.0):
            self._animate()

    def _animate(self):
        if self._anim:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"handle_pos")
        self._anim.setDuration(150)
        self._anim.setStartValue(self._handle_pos)
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.start()

    def _get_handle_pos(self) -> float:
        return self._handle_pos

    def _set_handle_pos(self, val: float):
        self._handle_pos = val
        self.update()

    handle_pos = pyqtProperty(float, _get_handle_pos, _set_handle_pos)

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pal = self.palette()
        w, h = self.width(), self.height()
        track_h = h * 0.625
        track_y = (h - track_h) / 2
        radius = track_h / 2

        if self._checked:
            track_color = pal.color(QPalette.ColorRole.Highlight)
        else:
            track_color = pal.color(QPalette.ColorRole.Dark)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(
            int((w - w * 0.85) / 2), int(track_y), int(w * 0.85), int(track_h), int(radius), int(radius)
        )

        margin = 2
        handle_size = h - margin * 2
        track_start = (w - w * 0.85) / 2
        track_end = (w + w * 0.85) / 2
        handle_x = track_start + margin + self._handle_pos * (track_end - track_start - handle_size - margin * 2)
        handle_color = pal.color(QPalette.ColorRole.Light)
        painter.setBrush(handle_color)
        painter.setPen(QPen(pal.color(QPalette.ColorRole.Mid), 1))
        painter.drawEllipse(int(handle_x), int(margin), int(handle_size), int(handle_size))

        painter.end()
