"""Small reusable widgets shared across Niruvi's dialogs.

Kept intentionally tiny: each helper exists because the same pattern was being
re-implemented (and subtly broken) in several places.
"""

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPaintEvent
from PyQt6.QtWidgets import QLabel, QSizePolicy, QWidget

from niruvi.utils import icon_pixmap

# ── Icon metrics ───────────────────────────────────────────────────────────────
# One place to answer "how big is an icon in this context?" so the list, the
# toolbar, the menus and the dialogs stop drifting apart.

ICON_SIZE_INLINE = 16  # inside text rows, list columns, status lines
ICON_SIZE_MENU = 16  # QAction text for menu bars and context menus
ICON_SIZE_TOOLBAR = 24  # toolbars and dialog button rows
ICON_SIZE_CARD = 32  # installed-application rows
ICON_SIZE_EMPTY = 72  # empty-state illustrations
ICON_SIZE_HERO = 96  # headers, About/logo presentation

ICON_BOX_CARD = 36  # widget box around a ICON_SIZE_CARD pixmap (slight breathing room)


class ElidingLabel(QLabel):
    """A QLabel that elides its text instead of forcing the layout wider.

    Long application names and long paths are common in an AppImage manager;
    without this they stretch the row (and with it the horizontal scrollbar)
    instead of truncating with an ellipsis. The untruncated string stays
    available via ``full_text`` and should be set as the tooltip by callers.
    """

    def __init__(self, text: str = "", mode=Qt.TextElideMode.ElideRight, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._full_text = text
        self._mode = mode
        self._elided = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:
        self._full_text = text or ""
        self._update_elide()
        super().setText(self._elided)

    def text(self) -> str:
        return self._full_text

    @property
    def full_text(self) -> str:
        """The untruncated text (Qt's text() always returns what is painted)."""
        return self._full_text

    def setTextElideMode(self, mode) -> None:
        self._mode = mode
        self._update_elide()

    def _update_elide(self) -> None:
        metrics = self.fontMetrics()
        self._elided = metrics.elidedText(self._full_text, self._mode, max(0, self.width()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elide()
        # Avoid recursing: setText() re-enters resizeEvent on some styles.
        if self._elided != super().text():
            super().setText(self._elided)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(min(hint.width(), 400), hint.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().sizeHint().height())


class KeyValueRow(QWidget):
    """A read-only label/value pair with sensible wrapping and alignment.

    Used by the info/storage dialogs so key/value columns line up instead of
    each row choosing its own padding.
    """

    def __init__(self, key: str, value: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QHBoxLayout, QLabel

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.key_label = QLabel(f"{key}:")
        self.key_label.setStyleSheet("font-weight: 600;")
        self.key_label.setMinimumWidth(120)
        self.key_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.key_label)

        self.value_label = QLabel(value)
        self.value_label.setWordWrap(True)
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.value_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.value_label, 1)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)
        self.setToolTip(value)

    def set_value_pixmap(self, pixmap) -> None:
        self.value_label.setPixmap(pixmap)


class PlaceholderWidget(QWidget):
    """Paints an icon + headline + hint + optional action into empty space.

    Replaces ad-hoc "nothing here yet" labels with a state that says what is
    missing and what the user can do next.
    """

    def __init__(self, headline: str = "", hint: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QLabel, QVBoxLayout

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        self.headline_label = QLabel(headline)
        self.headline_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.headline_label.setWordWrap(True)
        layout.addWidget(self.headline_label)

        self.hint_label = QLabel(hint)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: palette(placeholder-text);")
        self.hint_label.setVisible(bool(hint))
        layout.addWidget(self.hint_label)

        self.action_container = QWidget()
        self.action_layout = QHBoxLayoutCentered(self.action_container)
        layout.addWidget(self.action_container)

    def set_icon(self, icon, size: int = 72) -> None:
        self.icon_label.setPixmap(icon_pixmap(icon, size))
        self.icon_label.setVisible(not icon.isNull())

    def set_text(self, headline: str, hint: str = "") -> None:
        self.headline_label.setText(headline)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))

    def add_action(self, widget) -> None:
        self.action_container.setVisible(True)
        self.action_layout.addWidget(widget)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)


def QHBoxLayoutCentered(parent):
    from PyQt6.QtWidgets import QHBoxLayout

    layout = QHBoxLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return layout
