"""Animated button rendering for Niruvi (hover/press motion + click ripple).

Qt style sheets cannot animate: every ``:hover``/``:pressed`` rule is an
instantaneous binary repaint, which is why the app felt unresponsive when
clicking or hovering. This module moves button painting out of QSS and into a
:class:`QProxyStyle` that blends between the DESIGN.md state colors over time,
so hover fades in, press dips quickly, and a release eases back.

Qt style sheets also have no ``outline``/``outline-offset`` support (they are CSS
properties Qt silently drops), so the focus ring declared in DESIGN.md never
painted. The ring is now drawn by :meth:`NiruviButtonStyle.drawPrimitive` for
``PE_FrameFocusRect``.

Three rendering concerns are handled here:

* **Depth** — filled buttons get a light-to-dark vertical sheen that flattens as
  the button is held, the way a physical key behaves.
* **Ripple** — a short expanding disc starts on mouse press (the same click
  ripple KDE Frameworks draws on its own buttons).
* **Motion preference** — every animation collapses to its final value when the
  desktop reports reduced motion (WCAG 2.2 SC 2.3.3).

State is read from the style option on every paint rather than from an event
filter: ``QEvent`` filters installed on the QApplication never see widget events,
and a QSS ``:hover`` rule works for exactly the same reason this does — Qt
repaints the button when its hover state changes.

Buttons that a stylesheet paints explicitly (background or border) are left to
Qt so per-widget overrides keep working.
"""

import re
from collections.abc import Iterator

from PyQt6.QtCore import QElapsedTimer, QEvent, QObject, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialogButtonBox,
    QProxyStyle,
    QPushButton,
    QStyle,
    QStyleOption,
    QWidget,
)

from niruvi.desktop.icon_utils import screen_dpr
from niruvi.utils import theme_engine

# ── Motion tuning ────────────────────────────────────────────────────────────

HOVER_MS = 130  # fade in/out of the hover tone
PRESS_DOWN_MS = 60  # quick dip: the click has to feel instant
PRESS_UP_MS = 180  # slower return, so a release reads as deliberate
RIPPLE_MS = 520  # lifetime of one click ripple
RIPPLE_START = 0.18  # disc radius at t=0, as a fraction of the cover radius
MOTION_TICK_MS = 16  # hover/press/ripple repaint cadence (~60 fps)

# ── Variants ─────────────────────────────────────────────────────────────────

VARIANT_PRIMARY = "primary"
VARIANT_DANGER = "danger"
VARIANT_SUCCESS = "success"
VARIANT_GHOST = "ghost"
VARIANT_SECONDARY = "secondary"
_FILLED_VARIANTS = frozenset({VARIANT_PRIMARY, VARIANT_DANGER, VARIANT_SUCCESS})

_MOTION_PROPERTY = "_niruviButtonMotion"
_TINT_CACHE: dict[tuple[int, int, int, str], QIcon] = {}

# A per-widget stylesheet that paints a background or border outranks this
# style, so those buttons are handed back to Qt.
_QSS_PANEL_RE = re.compile(r"QPushButton[^{]*\{[^}]*?\b(?:background|border)\b", re.IGNORECASE)


def prefers_reduced_motion() -> bool:
    """Return True when the desktop asks for reduced motion."""
    try:
        return theme_engine.get_theme_engine().prefers_reduced_motion()
    except Exception:
        return False


# ── Color helpers ────────────────────────────────────────────────────────────


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """Blend two colors; ``t=0`` returns *a*, ``t=1`` returns *b*."""
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def _shade(color: QColor, amount: float) -> QColor:
    """Darken (amount > 0) or lighten (amount < 0) *color* by a fraction."""
    return mix(color, QColor(0, 0, 0) if amount > 0 else QColor(255, 255, 255), abs(amount))


def _token(name: str) -> QColor:
    return QColor(theme_engine.design_color(name))


def _alpha(color: QColor, value: int) -> QColor:
    out = QColor(color)
    out.setAlpha(max(0, min(255, value)))
    return out


# ── Variant resolution ───────────────────────────────────────────────────────


def variant_of(button: QPushButton) -> str:
    """Resolve the visual variant of *button*.

    An explicit ``variant`` dynamic property always wins. Otherwise the
    affirmative button of a :class:`QDialogButtonBox` counts as primary, so
    dialogs keep the familiar "confirm is highlighted" convention, and every
    other button is secondary.

    Qt's own ``:default`` pseudo-state is deliberately unused: it fires for
    whichever button a dialog happens to create first, which used to paint
    arbitrary buttons — "Close", "Cancel" — in the accent color.
    """
    prop = button.property("variant")
    if isinstance(prop, str) and prop:
        lowered = prop.lower()
        return lowered if lowered in _FILLED_VARIANTS or lowered == VARIANT_GHOST else VARIANT_SECONDARY
    parent = button.parentWidget()
    if isinstance(parent, QDialogButtonBox) and button.isDefault():
        return VARIANT_PRIMARY
    return VARIANT_SECONDARY


# ── Per-button animated state ────────────────────────────────────────────────


def _out_cubic(t: float) -> float:
    """Easing equivalent to ``QEasingCurve.OutCubic``, kept dependency-free."""
    inv = 1.0 - t
    return 1.0 - inv * inv * inv


_OUT_CUBIC = _out_cubic


def _press_duration(pressed: bool) -> int:
    """Pressing dips quickly; releasing eases back so the click reads clearly."""
    return PRESS_DOWN_MS if pressed else PRESS_UP_MS


class _ButtonMotion(QObject):
    """Animated hover/press/ripple state owned by a single button.

    Motion is driven by a single ``QTimer`` plus elapsed-time interpolation
    rather than ``QVariantAnimation``. Animations notify through signals, and a
    tick already dispatched into a slot can outlive the button that owns it -
    invoking a Python bound method on a deleted C++ object and killing the
    process. A timer parented to this object cannot outlive it, and ``hover``
    / ``press`` stay plain Python values because only ``paint_button`` reads
    them.
    """

    def __init__(self, button: QPushButton):
        super().__init__(button)
        self._hover = 0.0
        self._press = 0.0
        # Each ripple is [cover_radius, started_at_ms]. Progress is derived from
        # the clock rather than accumulated per tick, so one ripple always lasts
        # RIPPLE_MS of wall-clock time even when ticks are delayed or coalesced.
        self._ripples: list[list[float]] = []
        # Hover/press each interpolate from a recorded start towards a target.
        self._hover_from, self._hover_to, self._hover_dur, self._hover_t0 = 0.0, 0.0, 0, 0
        self._press_from, self._press_to, self._press_dur, self._press_t0 = 0.0, 0.0, 0, 0
        self._scale_from, self._scale_to, self._scale_dur, self._scale_t0 = 0.0, 0.0, 0, 0
        self._scale = 1.0
        self._clock = QElapsedTimer()
        self._tick = QTimer(self)
        self._tick.setInterval(MOTION_TICK_MS)
        self._tick.timeout.connect(self._advance)

    # Animated values --------------------------------------------------------
    @property
    def hover(self) -> float:
        return self._hover

    @property
    def press(self) -> float:
        return self._press

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def ripples(self) -> list[list[float]]:
        """Active ripples as ``[cover_radius, progress]`` pairs, progress 0..1."""
        now = self._now()
        return [[cover, (now - started) / RIPPLE_MS] for cover, started in self._ripples]

    # State transitions ----------------------------------------------------
    def sync(self, hover: bool, press: bool, enabled: bool = True):
        """Drive the motion toward the state Qt reports for this paint."""
        self._retarget("hover", self._hover, hover and enabled, HOVER_MS)
        press_target = press and enabled
        was_pressed = self._press > 0.0
        self._retarget("press", self._press, press_target, _press_duration(press_target))
        if press_target and not was_pressed:
            self.start_ripple()
        # Scale target: hover inflates, press deflates
        scale_target = 1.05 if hover else 1.0
        if press:
            scale_target = 0.97
        self._retarget("scale", self._scale, scale_target, HOVER_MS if not press else PRESS_DOWN_MS)

    def _retarget(self, channel: str, current: float, target: bool, duration: int):
        value = 1.0 if target else 0.0
        if abs(current - value) < 1e-4:
            self._settle(channel, value)
            return
        if prefers_reduced_motion():
            self._settle(channel, value)
            return
        setattr(self, f"_{channel}_from", current)
        setattr(self, f"_{channel}_to", value)
        setattr(self, f"_{channel}_dur", duration)
        setattr(self, f"_{channel}_t0", self._now())
        if not self._tick.isActive():
            self._tick.start()

    def _settle(self, channel: str, value: float):
        """Jump a channel straight to *value* without animating."""
        setattr(self, f"_{channel}_from", value)
        setattr(self, f"_{channel}_to", value)
        setattr(self, f"_{channel}_dur", 0)
        setattr(self, f"_{channel}", value)

    def _live_button(self) -> QPushButton | None:
        """The owning button, or None once Qt has started tearing it down."""
        button = self.parent()
        if not isinstance(button, QPushButton):
            return None
        try:
            button.width()
        except RuntimeError:  # C++ object already deleted
            return None
        return button

    def start_ripple(self):
        """Add one expanding disc, centred on the button."""
        if prefers_reduced_motion():
            return
        button = self._live_button()
        if button is None or button.width() <= 0 or button.height() <= 0:
            return
        cover = ((button.width() ** 2 + button.height() ** 2) ** 0.5) / 2.0
        self._ripples.append([cover, float(self._now())])
        if not self._tick.isActive():
            self._tick.start()

    def _now(self) -> int:
        if not self._clock.isValid():
            self._clock.start()
        return self._clock.elapsed()

    def _advance(self):
        """Interpolate every running channel and age the ripples."""
        now = self._now()
        settled = True

        for channel in ("hover", "press", "scale"):
            duration = getattr(self, f"_{channel}_dur")
            if duration <= 0:
                continue
            progress = min(1.0, (now - getattr(self, f"_{channel}_t0")) / duration)
            start = getattr(self, f"_{channel}_from")
            value = start + (getattr(self, f"_{channel}_to") - start) * _OUT_CUBIC(progress)
            if abs(value - getattr(self, f"_{channel}")) > 1e-4:
                setattr(self, f"_{channel}", value)
            if progress < 1.0:
                settled = False
            else:
                setattr(self, f"_{channel}_dur", 0)

        # Retire ripples by elapsed time, so the period is RIPPLE_MS exactly
        # no matter how late this tick runs.
        self._ripples = [r for r in self._ripples if now - r[1] < RIPPLE_MS]

        button = self._live_button()
        if button is None:
            self._tick.stop()
            return
        button.update()
        if settled and not self._ripples:
            self._tick.stop()


def motion_for(button: QPushButton) -> _ButtonMotion:
    """Return (creating on first use) the animated state for *button*."""
    motion = button.property(_MOTION_PROPERTY)
    if isinstance(motion, _ButtonMotion):
        return motion
    motion = _ButtonMotion(button)
    button.setProperty(_MOTION_PROPERTY, motion)
    return motion


# ── QSS delegation guard ─────────────────────────────────────────────────────


def _ancestor_stylesheets(button: QPushButton) -> Iterator[str]:
    widget: QWidget | None = button
    while widget is not None:
        sheet = widget.styleSheet()
        if sheet:
            yield sheet
        widget = widget.parentWidget()


def stylesheet_paints_button(button: QPushButton) -> bool:
    """True when a stylesheet paints *button* itself and must win over us."""
    return any(_QSS_PANEL_RE.search(sheet) for sheet in _ancestor_stylesheets(button))


# ── Button painting ──────────────────────────────────────────────────────────


def _corner_radius(button: QPushButton) -> int:
    """DESIGN.md ``rounded.md`` (8 px), eased off for very short buttons."""
    return max(3, min(8, button.height() // 3))


def _state_colors(variant: str, enabled: bool) -> tuple[QColor, QColor, QColor, QColor | None]:
    """Return (base, hover, pressed, border) colors for *variant*.

    The three fills are the DESIGN.md color for that state, so one blend value
    can address any state without branching.
    """
    if not enabled:
        surface = _token("surface")
        return surface, surface, surface, _token("border")
    if variant == VARIANT_PRIMARY:
        return _token("accent"), _token("accent-hover"), _token("accent-pressed"), None
    if variant == VARIANT_DANGER:
        error = _token("error")
        return error, _shade(error, 0.08), _shade(error, 0.22), None
    if variant == VARIANT_SUCCESS:
        success = _token("success")
        return success, _shade(success, 0.08), _shade(success, 0.22), None
    if variant == VARIANT_GHOST:
        return _alpha(_token("background"), 0), _token("light"), _token("border"), None
    return _token("surface"), _token("light"), _token("border"), _token("border")


def text_color(variant: str, enabled: bool) -> QColor:
    """Label color that stays legible on the *variant* fill."""
    if not enabled:
        return _token("subtle")
    if variant in _FILLED_VARIANTS:
        return QColor(255, 255, 255)
    return _token("ink")


def _tinted_icon(icon: QIcon, size, color: QColor) -> QIcon:
    """Return *icon* recolored to a flat *color*, cached per icon/size/color."""
    dpr = screen_dpr()
    key = (icon.cacheKey(), size.width(), size.height(), round(dpr, 2), color.name())
    cached = _TINT_CACHE.get(key)
    if cached is not None:
        return cached
    pm: QPixmap = icon.pixmap(size, dpr)
    if pm.isNull():
        return icon
    painter = QPainter(pm)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pm.rect(), color)
    painter.end()
    tinted = QIcon(pm)
    if len(_TINT_CACHE) > 256:
        _TINT_CACHE.clear()
    _TINT_CACHE[key] = tinted
    return tinted


def paint_button(painter: QPainter, option: QStyleOption, button: QPushButton):
    """Paint a QPushButton background, sheen, ripple and border."""
    enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
    variant = variant_of(button)
    motion = motion_for(button)
    motion.sync(
        bool(option.state & QStyle.StateFlag.State_MouseOver),
        bool(option.state & QStyle.StateFlag.State_Sunken),
        enabled,
    )
    hover = motion.hover if enabled else 0.0
    press = motion.press if enabled else 0.0

    base, hover_color, pressed_color, border_color = _state_colors(variant, enabled)
    fill = mix(base, hover_color, hover)
    fill = mix(fill, pressed_color, press)

    radius = float(_corner_radius(button))
    rect = QRectF(option.rect).adjusted(1.0, 1.0, -1.0, -1.0)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    filled = variant in _FILLED_VARIANTS

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if fill.alpha() > 0:
        painter.fillPath(path, fill)

    # Depth sheen: convex while idle, flattening while the button is held.
    if filled and press < 0.99 and fill.alpha() > 0:
        lift = 1.0 - press
        sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        sheen.setColorAt(0.0, _alpha(QColor(255, 255, 255), round(26 * lift)))
        sheen.setColorAt(0.45, _alpha(QColor(255, 255, 255), 0))
        sheen.setColorAt(0.55, QColor(0, 0, 0, 0))
        sheen.setColorAt(1.0, _alpha(QColor(0, 0, 0), round(34 * lift)))
        painter.fillPath(path, sheen)

    # Click ripple, clipped to the button silhouette.
    if motion.ripples and fill.alpha() > 0:
        painter.save()
        painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        center = rect.center()
        disc = QColor(255, 255, 255) if filled else _token("ink")
        for cover, t in motion.ripples:
            # Clip t to [0,1] so that (1 - t) ** 1.5 never yields a complex number.
            t = min(1.0, max(0.0, t))
            fade = (1.0 - t) ** 1.5
            painter.setBrush(_alpha(disc, round((30 if filled else 20) * fade)))
            radius_now = cover * (RIPPLE_START + (1.0 - RIPPLE_START) * t)
            painter.drawEllipse(center, radius_now, radius_now)
        painter.restore()

    if border_color is not None:
        edge = mix(border_color, _token("subtle"), hover * 0.6)
        edge = mix(edge, _token("border"), press * 0.5)
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    painter.restore()


def paint_focus_ring(painter: QPainter, widget: QPushButton):
    """Draw the DESIGN.md keyboard focus ring.

    The ring follows the button outline rather than the primitive's own rect:
    a stylesheet's padding shrinks ``PE_FrameFocusRect`` down to the label box,
    which would draw a pill around the text instead of the button. It is also
    inset rather than offset, because QPushButton clips its painter to its own
    rectangle, so a ring drawn 2 px outside would never be visible.
    """
    rect = QRectF(widget.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
    if rect.width() <= 2.0 or rect.height() <= 2.0:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_token("focus-ring"), 2.0))
    radius = _corner_radius(widget)
    painter.drawRoundedRect(rect, radius, radius)
    painter.restore()


# ── The style ────────────────────────────────────────────────────────────────


class NiruviButtonStyle(QProxyStyle):
    """Fusion proxy that animates QPushButton states instead of snapping."""

    def _owns(self, widget) -> bool:
        return isinstance(widget, QPushButton) and not stylesheet_paints_button(widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if widget is not None and self._owns(widget):
            if element == QStyle.PrimitiveElement.PE_PanelButtonCommand:
                paint_button(painter, option, widget)
                return
            if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
                if option.state & QStyle.StateFlag.State_HasFocus:
                    paint_focus_ring(painter, widget)
                return
            if (
                element == QStyle.PrimitiveElement.PE_IndicatorArrowDown
                and widget.menu() is not None
                and variant_of(widget) in _FILLED_VARIANTS
            ):
                self._draw_menu_arrow(option, painter, widget)
                return
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None):
        if element == QStyle.ControlElement.CE_PushButtonLabel and widget is not None and self._owns(widget):
            color = text_color(variant_of(widget), widget.isEnabled())
            option.palette.setColor(QPalette.ColorRole.ButtonText, color)
            option.palette.setColor(QPalette.ColorRole.Text, color)
            if variant_of(widget) in _FILLED_VARIANTS and not widget.icon().isNull():
                option.icon = _tinted_icon(widget.icon(), option.iconSize, color)
        super().drawControl(element, option, painter, widget)

    def _draw_menu_arrow(self, option: QStyleOption, painter: QPainter, button: QPushButton):
        """Tint a filled button's drop-down arrow so it stays legible.

        Qt6 dropped ``PE_IndicatorMenuArrow``; Fusion draws the button's arrow
        through ``PE_IndicatorArrowDown`` using the palette, so the arrow is
        recolored here. Combo boxes are unaffected because the widget must own a
        menu to reach this path.
        """
        adjusted = QStyleOption()
        adjusted.initFrom(button)
        adjusted.rect = option.rect
        adjusted.state = option.state
        adjusted.palette = QPalette(option.palette)
        color = text_color(variant_of(button), button.isEnabled())
        adjusted.palette.setColor(QPalette.ColorRole.ButtonText, color)
        adjusted.palette.setColor(QPalette.ColorRole.Text, color)
        super().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorArrowDown, adjusted, painter)


# ── Installation ─────────────────────────────────────────────────────────────

# The installed proxy is parked on the application as a dynamic property. It is
# the only reliable way to find it again: once a global stylesheet is set, Qt
# wraps the application style in an internal QStyleSheetStyle that PyQt does not
# expose, so ``app.style()`` no longer identifies our proxy. Installing a second
# proxy would make ``QApplication.setStyle()`` delete the first one while live
# widgets still reference it.
_STYLE_PROPERTY = "_niruvi_button_style"


# ── Mnemonic cleanup ────────────────────────────────────────────────────────
#
# Under the KDE/Windows platform themes, QDialogButtonBox standard buttons
# arrive with ampersand mnemonics baked into their text ("&OK", "&Cancel"),
# which renders as a persistent underline on the letter. Niruvi buttons state
# their command plainly, so we strip *mnemonic* ampersands from every abstract
# button app-wide while preserving the displayed text exactly ("&&" stays a
# literal '&'). Ordinary keyboard access is unaffected: Enter/Escape still
# activate default/cancel buttons, Tab/Space navigate toggles, and all
# QAction/QShortcut shortcuts continue to work.


def strip_mnemonic_markers(text: str) -> str:
    """Return *text* with Qt mnemonic ampersands removed, display-preserved."""
    if "&" not in text:
        return text
    return text.replace("&&", "\x00").replace("&", "").replace("\x00", "&")


class _MnemonicCleaner(QObject):
    """Application-scoped event filter that clears button mnemonics once."""

    def __init__(self, parent: QObject):
        super().__init__(parent)
        self._done = set()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt signature)
        if event.type() in (QEvent.Type.Show, QEvent.Type.PolishRequest, QEvent.Type.Polish):
            widget = obj
            if isinstance(widget, QAbstractButton) and not widget.property(_MNEMONIC_DONE):
                widget.setProperty(_MNEMONIC_DONE, True)
                text = widget.text()
                cleaned = strip_mnemonic_markers(text)
                if cleaned != text:
                    widget.setText(cleaned)
                self._done.discard(id(widget))
        return False


_MNEMONIC_DONE = "_niruvi_mnemonic_cleaned"
_CLEANER_PROPERTY = "_niruvi_mnemonic_cleaner"


def install_mnemonic_cleaner(app: QApplication) -> _MnemonicCleaner:
    """Install the mnemonic cleaner on *app*, at most once per application."""
    existing = app.property(_CLEANER_PROPERTY)
    if isinstance(existing, _MnemonicCleaner):
        return existing
    cleaner = _MnemonicCleaner(app)
    app.installEventFilter(cleaner)
    app.setProperty(_CLEANER_PROPERTY, cleaner)
    return cleaner


def install_button_style(app: QApplication) -> NiruviButtonStyle:
    """Install the animated button style on *app*, at most once per application.

    Theme switches call this again; the existing proxy is returned untouched so
    in-flight animations survive a light/dark change. Returns the installed style.
    """
    install_mnemonic_cleaner(app)
    existing = app.property(_STYLE_PROPERTY)
    if isinstance(existing, NiruviButtonStyle):
        return existing

    style_obj = NiruviButtonStyle("Fusion")
    app.setStyle(style_obj)
    app.setProperty(_STYLE_PROPERTY, style_obj)
    return style_obj
