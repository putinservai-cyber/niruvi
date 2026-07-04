from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QWidget

from niruvi.utils.sound_manager import play as play_sound


class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    TRACK_H_RATIO = 0.4
    HANDLE_D_RATIO = 0.78
    PAD = 2

    def __init__(self, parent=None, initial: bool = False):
        super().__init__(parent)
        self._checked = initial
        self._offset = 0.0
        self.setFixedSize(56, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim: QPropertyAnimation | None = None

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if checked != self._checked:
            self._checked = checked
            self._animate()
            self.toggled.emit(checked)
        elif abs(self._offset - (1.0 if checked else 0.0)) > 0.01:
            self._animate()

    def _animate(self):
        if self._anim:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"offset")
        self._anim.setDuration(120)
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, val: float):
        self._offset = val
        self.update()

    offset = pyqtProperty(float, _get_offset, _set_offset)

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)
        play_sound("toggle")
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        w, h = self.width(), self.height()

        track_h = round(h * self.TRACK_H_RATIO)
        track_y = (h - track_h) // 2
        track_w = w - self.PAD * 2
        track_x = self.PAD
        radius = track_h // 2

        if self._checked:
            track_color = pal.color(QPalette.ColorRole.Highlight)
        else:
            c = pal.color(QPalette.ColorRole.Mid)
            track_color = QColor(c.red(), c.green(), c.blue(), 160)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track_color)
        p.drawRoundedRect(track_x, track_y, track_w, track_h, radius, radius)

        d = round(h * self.HANDLE_D_RATIO)
        handle_y = (h - d) // 2
        min_x = self.PAD + 3
        max_x = w - self.PAD - d - 3
        x = round(min_x + self._offset * (max_x - min_x))

        if self._checked:
            hc = pal.color(QPalette.ColorRole.HighlightedText)
        else:
            hc = pal.color(QPalette.ColorRole.Window)
        p.setPen(QPen(pal.color(QPalette.ColorRole.Midlight), 1))
        p.setBrush(hc)
        p.drawEllipse(x, handle_y, d, d)

        p.end()
