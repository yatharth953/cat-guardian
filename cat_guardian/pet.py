from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon,
    QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import QMenu, QWidget

from . import native
from .appearance import DEFAULT_CAT_COLOR, FurPalette
from .cat_shapes import (
    CHEEK_LEFT, CHEEK_RIGHT, EYE_H, EYE_LEFT, EYE_RIGHT, EYE_W, FACE_CENTER,
    MOUTH_CENTER, MOUTH_ITEM_X, MOUTH_ITEM_Y, draw_cat_base, draw_idle_face,
    draw_mouth,
)
from .music_moods import NEW_MUSIC_MOODS, DanceBrain, MusicProfile, apply_music_dance

W, H = 176, 182
GROUND = 176
CX = W / 2

PINK = QColor("#dc8f96")

_MOODS_CLOSED_EYES = {"sleep"}
_TAU = math.tau

# Where the stretching paw touches, in widget coordinates, during a close tap.
TAP_PAW_X = 136.0
TAP_PAW_Y = 16.0
HOLD_MS = 800
CLICK_DELAY_MS = 240
CURSOR_REACH = 210.0

# Startup greeting: slide in, raise a paw, say meow, and settle back home.
GREET_ENTER_MS = 650
GREET_RAISE_MS = 450
GREET_MEOW_MS = 800
GREET_RETURN_MS = 750

# Tail end of a confirmed close: glide from the close spot to centre stage,
# shake the head, brag, and walk home. Appended to the running close timeline.
BRAG_ENTER_MS = 520
BRAG_SHAKE_MS = 1900
BRAG_RETURN_MS = 700
BRAG_TEXT = "NO DISTRACTIONS!!!!"
BUBBLE_TAIL = 7

MUSIC_MOODS = (
    {"music_chill", "music_groove", "music_hype", "music_minimal"}
    | set(NEW_MUSIC_MOODS)
)
MUSIC_STYLES = {"auto", "chill", "groovy", "hype", "minimal", "off"}
# Reports arrive every 0.25 s; a longer gap means capture stopped without saying so.
MUSIC_REPORT_TIMEOUT = 2.5
_MUSIC_MOOD_FOR_STYLE = {
    "chill": "music_chill",
    "groovy": "music_groove",
    "hype": "music_hype",
    "minimal": "music_minimal",
}
_MUSIC_STYLE_FOR_MOOD = {value: key for key, value in _MUSIC_MOOD_FOR_STYLE.items()}


def _clamp(value, low, high):
    return low if value < low else high if value > high else value


def _blend(current: float, target: float, factor: float) -> float:
    return current + (target - current) * _clamp(factor, 0.0, 1.0)


def _smooth(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ease_out(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3


def _wrap_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= _TAU
    while angle < -math.pi:
        angle += _TAU
    return angle


def _heart_path(cx: float, cy: float, size: float) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(cx, cy + size * 0.68)
    path.cubicTo(cx - size * 1.25, cy - size * 0.2, cx - size * 0.55, cy - size * 1.1, cx, cy - size * 0.38)
    path.cubicTo(cx + size * 0.55, cy - size * 1.1, cx + size * 1.25, cy - size * 0.2, cx, cy + size * 0.68)
    return path


def _spark_path(cx: float, cy: float, size: float) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(cx, cy - size)
    path.lineTo(cx + size * 0.28, cy - size * 0.28)
    path.lineTo(cx + size, cy)
    path.lineTo(cx + size * 0.28, cy + size * 0.28)
    path.lineTo(cx, cy + size)
    path.lineTo(cx - size * 0.28, cy + size * 0.28)
    path.lineTo(cx - size, cy)
    path.lineTo(cx - size * 0.28, cy - size * 0.28)
    path.closeSubpath()
    return path


@dataclass
class Particle:
    kind: str
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    size: float


@dataclass
class Segment:
    dur: float
    start: QPoint
    end: QPoint
    mood: str
    arc: float = 0.0
    smooth: bool = True
    contact: float | None = None
    contact_fired: bool = False


@dataclass
class Visual:
    bob: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    lean: float = 0.0
    head_tilt: float = 0.0
    jitter: float = 0.0
    perp: float = 0.2
    eye_mode: str = "open"
    eye_open: float = 1.0
    blush: float = 0.0
    paw_l: QPointF = field(default_factory=lambda: QPointF(0, 0))
    paw_r: QPointF = field(default_factory=lambda: QPointF(0, 0))
    tail: str = "sway"
    swat: float = 0.0
    yawn: float = 0.0
    look: QPointF = field(default_factory=lambda: QPointF(0, 0))
    spin: float = 0.0
    reach: float = 0.0
    paw_target: QPointF = field(default_factory=lambda: QPointF(TAP_PAW_X, TAP_PAW_Y))
    purr: float = 0.0
    flash: float = 0.0
    question: float = 0.0
    drop: float = 0.0
    carry: float = 0.0
    carry_y: float = 0.0


class SpeechBubble(QWidget):
    """Callout above the cat's head, wide enough for a full sentence.

    The pet window is only as wide as the cat, so a longer line is drawn in
    this separate always-on-top, mouse-transparent window instead.
    """

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text = ""
        self._font = QFont("Segoe UI", 10)
        self._font.setWeight(QFont.Weight.DemiBold)
        self.setFont(self._font)

    def say(self, text: str, anchor: QPoint) -> None:
        self._text = text
        metrics = QFontMetrics(self._font)
        width = int(metrics.horizontalAdvance(text)) + 26
        height = int(metrics.height()) + 12
        self.resize(width, height + BUBBLE_TAIL)
        self._place(anchor, width, height)
        self.show()
        self.raise_()
        try:
            hwnd = int(self.winId())
            native.set_click_through(hwnd, True)
            native.send_to_top(hwnd)
        except Exception:
            logging.debug("Speech bubble layering failed", exc_info=True)
        self.update()

    def _place(self, anchor: QPoint, width: int, height: int) -> None:
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        x = int(_clamp(anchor.x() - width / 2, area.left() + 4,
                       area.right() - width - 4))
        y = int(_clamp(anchor.y() - height - BUBBLE_TAIL + 1, area.top() + 4,
                       area.bottom() - height - BUBBLE_TAIL))
        self.move(x, y)

    def dismiss(self) -> None:
        self._text = ""
        self.hide()

    def paintEvent(self, event):
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        width = float(self.width())
        height = float(self.height() - BUBBLE_TAIL)
        body = QRectF(1, 1, width - 2, height - 2)
        bubble = QPainterPath()
        bubble.addRoundedRect(body, 11, 11)
        cx = width / 2
        bubble.addPolygon(QPolygonF([
            QPointF(cx - 6, height - 1),
            QPointF(cx + 6, height - 1),
            QPointF(cx, height + BUBBLE_TAIL - 1),
        ]))
        p.setPen(QPen(QColor(86, 104, 116), 1.1))
        p.setBrush(QColor(18, 24, 30, 240))
        p.drawPath(bubble)
        p.setFont(self._font)
        p.setPen(QColor(233, 243, 237))
        p.drawText(body, Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


class CatGuardian(QWidget):
    position_saved = Signal(int, int)
    settings_requested = Signal()
    protection_toggle_requested = Signal()
    quit_requested = Signal()
    notify = Signal(str, str)
    meow = Signal()

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings
        self._pet_name = settings.get("pet_name", "Miso")
        self._pal = FurPalette(settings.get("cat_color", DEFAULT_CAT_COLOR))
        self._sleep_delay = _clamp(float(settings.get("sleep_delay", 8.0)), 3.0, 120.0)
        self._sleep_zzz = bool(settings.get("sleep_zzz", True))
        self._music_style = self._valid_style(settings.get("music_style", "auto"))
        self._music_catalog = bool(settings.get("music_dance_catalog", True))
        self._music_intensity = _clamp(
            float(settings.get("music_intensity", 55)) / 100.0, 0.0, 1.0
        )

        self.setWindowTitle("Cat Guardian")
        # No WindowStaysOnBottomHint: Qt would fight every SetWindowPos during
        # the greeting, and removing the hint at that point recreates the
        # window. Native helpers keep the cat on the desktop layer instead.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setFixedSize(W, H)

        self._mode = "desktop"
        self._busy = False
        self._busy_since = 0.0
        self._target_hwnd = 0
        self._target_rect = None
        self._target_check_at = 0.0
        self._user_hidden = False
        self._queued = None
        self._ticket = None
        self._close_ticket_id = ""
        self._close_failed = False
        self._close_result_seen = False
        self._close_aborted = False
        self._close_contact_at = 0.0
        self._tap_paw = None
        self._tap_side = 1
        self._warn_mode = False

        self._birth = time.monotonic()
        self._last_tick = self._birth
        # Idle means asleep: wake for a moment after launch, then doze off.
        self._last_interaction = self._birth - max(0.0, self._sleep_delay - 2.0)
        self._mood = "idle"
        self._mood_since = self._birth
        self._mood_until: float | None = None
        self._next_gaze = self._birth + random.uniform(2.0, 4.0)
        self._hop_start: float | None = None

        self._hovered = False
        self._down = False
        self._dragging = False
        self._press_global = QPoint()
        self._press_pos = QPoint()
        self._last_global = QPoint()
        self._last_move_t = self._birth
        self._velocity = QPointF(0, 0)
        self._slide_anim: QPropertyAnimation | None = None
        self._menu_open = False

        self._look = QPointF(0, 0)
        self._look_target = QPointF(0, 0)
        self._ear = 0.25
        self._ear_flick_start: float | None = None
        self._next_ear_flick = self._birth + random.uniform(3.0, 8.0)
        self._eye_close = 0.0
        self._sleep_pose = 0.0
        self._sleep_settle_start: float | None = None
        self._next_sleep_settle = self._birth + random.uniform(5.0, 9.0)
        self._tail_flick_start: float | None = None
        self._next_tail_flick = self._birth + random.uniform(6.0, 12.0)

        self._blink_start: float | None = None
        self._next_blink = self._birth + random.uniform(2.0, 5.0)

        self._particles: list[Particle] = []
        self._next_heart = 0.0
        self._next_dust = 0.0
        self._next_zzz = 0.0

        self._bubble_text = ""
        self._bubble_until = 0.0

        self._timeline: list[Segment] = []
        self._seg_i = 0
        self._seg_t0 = 0.0
        self._dir = 1
        self._desk_pos = QPoint()

        # One-shot launch greeting; cancelled by distractions or user actions.
        self._greeting = False
        self._greet_home = QPoint()
        self._greet_target = QPoint()
        self._greet_anim: QPropertyAnimation | None = None

        # The brag is the tail end of the close visit, not a separate animation.
        self._brag_planned = False
        self._speech: SpeechBubble | None = None

        # Optional copy-paste delivery: a short desktop-to-paste-point walk.
        self._paste_busy = False
        self._paste_picking = False
        self._paste_icon: QPixmap | None = None
        self._paste_kind = "file"
        self._paste_count = 1
        self._paste_cut = False
        self._paste_landed = False

        self._pet_count = 0
        self._last_pet = 0.0
        self._pet_side = 1

        # Mouse gestures. Only one interaction may own the cat at a time.
        self._gesture: str | None = None
        self._gesture_until = 0.0
        self._gesture_block_until = 0.0
        self._trail: list[tuple[float, float, float, float, float]] = []
        self._chase: dict | None = None
        self._flick_start: float | None = None
        self._spin_start: float | None = None
        self._hold_side = 1

        # Music: the classifier reports a hysteretic "music" state; the cat
        # owns only presentation here so no two animation loops fight.
        self._music_live = False
        self._classifier_music = False
        self._always_music = False
        self._always_label = ""
        self._music_active = False
        self._music_energy = 0.4
        self._music_confidence = 0.0
        self._music_beat_strength = 0.0
        self._music_tempo = 112.0
        self._music_detail = ""
        self._music_report_at = 0.0
        self._music_started_at = 0.0
        self._beat_phase = 0.0
        self._beat_pulse = 0.0
        self._beat_count = 0
        self._last_beat_at = 0.0
        self._music_jump_start: float | None = None
        self._last_music_jump = 0.0
        self._auto_mood = "music_groove"
        self._auto_eval_at = 0.0
        self._dance_brain = DanceBrain()
        self._demo_until = 0.0
        self._demo_style = ""
        self._demo_intensity = 0.55

        self._fade_anim: QPropertyAnimation | None = None

        self.move(self._initial_pos())

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(16)
        self._cursor_timer.timeout.connect(self._sample_cursor)

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(HOLD_MS)
        self._hold_timer.timeout.connect(self._begin_hold)

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(CLICK_DELAY_MS)
        self._click_timer.timeout.connect(self._click_wake)

        self._z_timer = QTimer(self)
        self._z_timer.setInterval(3000)
        self._z_timer.timeout.connect(self._enforce_bottom)
        self._z_timer.start()

    # ------------------------------------------------------------------ setup

    def _initial_pos(self) -> QPoint:
        saved = self.settings.get("cat_pos")
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        if saved:
            return self._clamp_pos(QPoint(int(saved[0]), int(saved[1])))
        if area is None:
            return QPoint(100, 100)
        return QPoint(area.right() - W - 42, area.bottom() - H - 48)

    def _clamp_pos(self, pos: QPoint) -> QPoint:
        center = pos + QPoint(W // 2, H // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is None:
            return pos
        area = screen.availableGeometry()
        x = _clamp(pos.x(), area.left(), area.right() - W + 1)
        y = _clamp(pos.y(), area.top(), area.bottom() - H + 1)
        return QPoint(int(x), int(y))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_native)

    def _apply_native(self):
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        native.apply_no_activate(hwnd)
        native.set_click_through(hwnd, self._mode in ("overlay", "greeting"))
        if self._mode == "desktop":
            native.send_to_bottom(hwnd)
        else:
            native.send_to_top(hwnd)

    def _enforce_bottom(self):
        if not self.isVisible():
            return
        try:
            native.set_click_through(
                int(self.winId()), self._mode in ("overlay", "greeting")
            )
        except Exception:
            pass
        if self._mode == "greeting":
            if self._greeting:
                try:
                    native.send_to_top(int(self.winId()))
                except Exception:
                    pass
            else:
                self._enter_desktop()
            return
        if self._mode == "overlay":
            try:
                native.send_to_top(int(self.winId()))
            except Exception:
                pass
            if not self._busy and not self._timeline:
                self._return_to_desktop()
            return
        try:
            native.send_to_bottom(int(self.winId()))
        except Exception:
            pass

    # ------------------------------------------------------------------ modes
    #
    # Window flags stay fixed for the lifetime of the window and z-order is
    # changed with SetWindowPos instead. setWindowFlag() recreates the native
    # window, and the recreated window silently stopped delivering mouse
    # presses, so dragging and petting died after the first greeting.

    def _enter_desktop(self):
        if self._mode == "desktop":
            return
        self._mode = "desktop"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.show()
        QTimer.singleShot(0, self._apply_native)

    def _enter_overlay(self):
        if self._mode == "overlay":
            return
        self._mode = "overlay"
        # The overlay never needs clicks; let them pass through to the browser.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.show()
        self.raise_()
        QTimer.singleShot(0, self._apply_native)

    def _enter_greeting(self):
        if self._mode == "greeting":
            return
        self._mode = "greeting"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.show()
        self.raise_()
        QTimer.singleShot(0, self._apply_native)

    def show_cat(self):
        if self._greeting:
            self._cancel_greeting()
        self._dismiss_brag()
        self._user_hidden = False
        if self._busy:
            # A blocking visit is already visible; return to the desktop after.
            return
        self._cancel_gestures()
        self._enter_desktop()
        self.move(self._clamp_pos(self.pos()))
        was_visible = self.isVisible() and self.windowOpacity() > 0.5
        self.show()
        if not was_visible:
            self.setWindowOpacity(0.0)
            self._fade_to(1.0, 220)
        self._mood = "idle"
        self._mood_since = time.monotonic()
        self._mood_until = None
        self._last_interaction = time.monotonic()
        self._enforce_bottom()

    def hide_cat(self):
        self._cancel_greeting()
        self._dismiss_brag()
        self._user_hidden = True
        self._cancel_gestures()
        self.hide()

    # --------------------------------------------------------------- greeting

    def startup_greeting(self):
        """Come to the middle of the screen, raise a paw, meow, and settle back home."""
        if (self._greeting or self._busy or self._user_hidden
                or self._mode != "desktop"):
            return
        self._greeting = True
        self._greet_home = QPoint(self.pos())
        self._greet_target = self._greeting_center()
        self._cancel_gestures()
        self._set_mood("wake", 0.8)
        self._last_interaction = time.monotonic()
        self._enter_greeting()
        QTimer.singleShot(120, self._greet_move_in)
        logging.info("Startup greeting started")

    def _greeting_center(self) -> QPoint:
        center = self.pos() + QPoint(W // 2, H // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is None:
            return self.pos()
        area = screen.availableGeometry()
        return self._clamp_pos(
            QPoint(area.center().x() - W // 2, area.center().y() - H // 2)
        )

    def _greet_move_in(self):
        if not self._greeting:
            return
        self._animate_greeting(self._greet_target, GREET_ENTER_MS, self._greet_wave)

    def _greet_wave(self):
        if not self._greeting:
            return
        self._set_mood("greet")
        self._next_heart = time.monotonic() + 0.45
        QTimer.singleShot(GREET_RAISE_MS, self._greet_speak)

    def _greet_speak(self):
        if not self._greeting:
            return
        self._show_bubble("Meow!", 1.5)
        self.meow.emit()
        QTimer.singleShot(GREET_MEOW_MS, self._greet_return)

    def _greet_return(self):
        if not self._greeting:
            return
        self._set_mood("walk")
        self._dir = 1 if self._greet_home.x() >= self.x() else -1
        self._animate_greeting(self._greet_home, GREET_RETURN_MS, self._finish_greeting)

    def _finish_greeting(self):
        if not self._greeting:
            return
        self._greeting = False
        self._greet_anim = None
        self._enter_desktop()
        self.move(self._clamp_pos(self._greet_home))
        self.setWindowOpacity(1.0)
        self._set_mood("idle")
        self._last_interaction = time.monotonic()
        for delay in (120, 600, 1400):
            QTimer.singleShot(delay, self._enforce_bottom)
        logging.info("Startup greeting finished")

    def _cancel_greeting(self):
        if not self._greeting and self._mode != "greeting":
            return
        self._greeting = False
        if self._greet_anim is not None:
            self._greet_anim.stop()
            self._greet_anim = None
        if self._mode == "greeting":
            self._enter_desktop()
            self.move(self._clamp_pos(self._greet_home))
            self.setWindowOpacity(1.0)
            self._set_mood("idle")
            self._last_interaction = time.monotonic()
            self._enforce_bottom()
        logging.info("Startup greeting cancelled")

    def _animate_greeting(self, target: QPoint, ms: int, done):
        if self._greet_anim is not None:
            self._greet_anim.stop()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(ms)
        anim.setStartValue(self.pos())
        anim.setEndValue(self._clamp_pos(target))
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.finished.connect(done)
        self._greet_anim = anim
        anim.start()

    # --------------------------------------------------------- paste delivery

    def paste_delivery(self, icon=None, count: int = 1, cut: bool = False,
                       point: QPoint | None = None, kind: str = "file") -> bool:
        """Carry a copied item's icon to the paste point and drop it there.

        Presentation only: the system has already performed the real paste.
        This never interrupts a greeting or a protection visit, and it stays
        out of the way while the cat is hidden.
        """
        if (not self.settings.get("paste_mode")
                or self._busy or self._greeting
                or self._user_hidden or self._mode != "desktop"
                or point is None):
            return False
        now = time.monotonic()
        self._cancel_gestures()
        self._dismiss_brag()
        self._busy = True
        self._busy_since = now
        self._last_interaction = now
        self._brag_planned = False
        self._desk_pos = QPoint(self.x(), self.y())
        self._target_hwnd = 0
        self._target_rect = None
        self._ticket = None
        self._close_ticket_id = ""
        self._close_result_seen = True
        self._close_aborted = False
        self._tap_paw = None
        self._warn_mode = False

        self._paste_busy = True
        self._paste_picking = False
        self._paste_icon = icon
        self._paste_kind = kind if kind in ("file", "folder", "image") else "file"
        self._paste_count = max(1, int(count))
        self._paste_cut = bool(cut)
        self._paste_landed = False

        home = QPoint(self.x(), self.y())
        target = self._delivery_pos(point)
        distance = math.hypot(target.x() - home.x(), target.y() - home.y())
        walk = _clamp(distance / 1500.0, 0.22, 0.95)
        self._dir = 1 if target.x() >= home.x() else -1
        self._timeline = [
            Segment(0.45, home, home, "pickup"),
            Segment(walk, home, target, "carry_walk", arc=16),
            Segment(0.42, target, target, "drop"),
            Segment(walk, target, home, "walk", arc=18),
        ]
        self._seg_i = 0
        self._seg_t0 = now
        self._particles.clear()
        self.setWindowOpacity(0.0)
        self._enter_overlay()
        self._fade_to(1.0, 160)
        self._set_mood("pickup")
        logging.info(
            "Paste delivery started (%s item(s), %s)",
            self._paste_count, "cut" if self._paste_cut else "copy",
        )
        return True

    def _delivery_pos(self, point: QPoint) -> QPoint:
        """Top-left that puts the cat's drop point on the paste location."""
        screen = (QGuiApplication.screenAt(point) or self.screen()
                  or QGuiApplication.primaryScreen())
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        x = _clamp(point.x() - CX, area.left(), area.right() - W + 1)
        y = _clamp(point.y() - (GROUND - 12), area.top(), area.bottom() - H + 1)
        return QPoint(int(x), int(y))

    def paste_pickup(self, icon=None, count: int = 1, cut: bool = False,
                     point: QPoint | None = None, kind: str = "file") -> bool:
        """Walk to the copy point, grab the copied item, and slip away.

        This is the copy half of the delivery. It is presentation only, plays
        once per copy action, and never interrupts a visit or a greeting.
        """
        if (not self.settings.get("paste_mode")
                or self._busy or self._greeting
                or self._user_hidden or self._mode != "desktop"
                or point is None):
            return False
        now = time.monotonic()
        self._cancel_gestures()
        self._dismiss_brag()
        self._busy = True
        self._busy_since = now
        self._last_interaction = now
        self._brag_planned = False
        self._desk_pos = QPoint(self.x(), self.y())
        self._target_hwnd = 0
        self._target_rect = None
        self._ticket = None
        self._close_ticket_id = ""
        self._close_result_seen = True
        self._close_aborted = False
        self._tap_paw = None
        self._warn_mode = False

        self._paste_busy = True
        self._paste_picking = True
        self._paste_icon = icon
        self._paste_kind = kind if kind in ("file", "folder", "image") else "file"
        self._paste_count = max(1, int(count))
        self._paste_cut = bool(cut)
        self._paste_landed = False

        home = QPoint(self.x(), self.y())
        target = self._delivery_pos(point)
        distance = math.hypot(target.x() - home.x(), target.y() - home.y())
        walk = _clamp(distance / 1500.0, 0.22, 0.95)
        self._dir = 1 if target.x() >= home.x() else -1
        self._timeline = [
            Segment(walk, home, target, "walk", arc=14),
            Segment(0.45, target, target, "pickup"),
            Segment(0.42, target, target, "carry_walk"),
        ]
        self._seg_i = 0
        self._seg_t0 = now
        self._particles.clear()
        self.setWindowOpacity(0.0)
        self._enter_overlay()
        self._fade_to(1.0, 160)
        self._set_mood("walk")
        logging.info(
            "Paste pickup started (%s item(s), %s)",
            self._paste_count, "cut" if self._paste_cut else "copy",
        )
        return True

    # ------------------------------------------------------------------- brag

    def confirm_close(self, kind: str = "site") -> None:
        """Keep the running visit going after a confirmed close.

        The brag is appended to the close animation that is already playing,
        so the cat flows from the close pose to the centre instead of resetting
        and celebrating on its own.
        """
        if (not self.settings.get("enabled") or self._user_hidden
                or not self._busy or self._mode != "overlay"
                or not self._timeline or self._close_aborted):
            return
        self._extend_for_brag(kind)

    def _extend_for_brag(self, kind: str = "site") -> None:
        if self._brag_planned:
            return
        self._brag_planned = True
        now = time.monotonic()
        current = self._timeline[self._seg_i]
        if current.mood in ("pounce", "react", "smug"):
            # Let the closing beat settle now instead of holding its full beat.
            current.dur = min(current.dur, (now - self._seg_t0) + 0.18)
        tail = self._timeline[-1]
        if tail is not current and tail.mood in ("react", "smug"):
            # A waiting tail (the app-close hold) is not needed any more.
            tail.dur = min(tail.dur, 0.18)
        start = tail.end
        centre = self._greeting_center()
        home = self._clamp_pos(self._desk_pos)
        self._timeline.extend([
            Segment(BRAG_ENTER_MS / 1000.0, start, centre, "walk", arc=18),
            Segment(BRAG_SHAKE_MS / 1000.0, centre, centre, "proud"),
            Segment(BRAG_RETURN_MS / 1000.0, centre, home, "walk", arc=22),
        ])
        logging.info("Brag added to the %s close visit", kind)

    def _say_brag(self, text: str):
        if self._speech is None:
            self._speech = SpeechBubble()
        anchor = self.mapToGlobal(QPoint(int(CX), 34))
        self._speech.say(text, anchor)

    def _dismiss_brag(self):
        if self._speech is not None:
            self._speech.dismiss()

    @property
    def hidden_by_user(self) -> bool:
        return self._user_hidden

    @property
    def mood(self) -> str:
        return self._mood

    @property
    def music_dancing(self) -> bool:
        return self._music_active and self._mood in MUSIC_MOODS

    def apply_settings(self, settings: dict):
        self.settings = settings
        self._pet_name = settings.get("pet_name", "Miso")
        self._pal = FurPalette(settings.get("cat_color", DEFAULT_CAT_COLOR))
        self._sleep_delay = _clamp(float(settings.get("sleep_delay", 8.0)), 3.0, 120.0)
        self._sleep_zzz = bool(settings.get("sleep_zzz", True))
        self._music_style = self._valid_style(settings.get("music_style", "auto"))
        catalog = bool(settings.get("music_dance_catalog", True))
        if catalog != self._music_catalog:
            # Apply a catalog change on the next tick, not after the current dance.
            self._auto_eval_at = 0.0
        self._music_catalog = catalog
        self._music_intensity = _clamp(
            float(settings.get("music_intensity", 55)) / 100.0, 0.0, 1.0
        )
        if not settings.get("music_enabled", True) or self._music_style == "off":
            self.clear_music()
        if not settings.get("enabled") and self._queued is not None:
            ticket = self._queued.get("ticket")
            if ticket is not None and not ticket.resolved:
                ticket.close_now()
            self._queued = None
        self.update()

    def current_color(self) -> str:
        return self._pal.hex

    @staticmethod
    def _valid_style(value) -> str:
        style = str(value or "auto").lower()
        return style if style in MUSIC_STYLES else "auto"

    # ---------------------------------------------------------------- music

    def music_report(self, report):
        """Receives a compact classification result from the audio thread."""
        self._music_confidence = float(getattr(report, "confidence", 0.0))
        self._music_energy = _clamp(float(getattr(report, "intensity", 0.4)), 0.0, 1.0)
        self._music_detail = str(getattr(report, "detail", ""))
        self._music_beat_strength = _clamp(
            float(getattr(report, "beat_strength", 0.0)), 0.0, 1.0
        )
        tempo = float(getattr(report, "tempo", 0.0))
        if 45.0 <= tempo <= 210.0:
            self._music_tempo = _blend(self._music_tempo, tempo, 0.25)
        state = str(getattr(report, "state", "uncertain"))
        allowed = self._music_allowed()
        self._classifier_music = state == "music" and allowed
        self._music_live = allowed and (self._classifier_music or self._always_music)
        self._music_report_at = time.monotonic()
        self._refresh_music_active()

    def set_always_music(self, active: bool, label: str = ""):
        """A pre-selected app or site is playing; dance without the classifier.

        The flag only overrides the classifier's state. Volume, tempo, and
        staleness still come from the live reports, so a dead capture or a
        silent source never leaves the cat dancing.
        """
        active = bool(active)
        self._always_music = active
        self._always_label = str(label or "") if active else ""
        self._music_live = self._music_allowed() and (
            self._classifier_music or self._always_music
        )
        self._refresh_music_active()

    def _music_allowed(self) -> bool:
        return (bool(self.settings.get("music_enabled", True))
                and self._music_style != "off")

    def music_beat(self, event):
        if not self._music_active or self._music_style == "off":
            return
        now = time.monotonic()
        strength = _clamp(float(getattr(event, "strength", 0.5)), 0.0, 1.0)
        intensity = _clamp(float(getattr(event, "intensity", self._music_energy)), 0.0, 1.0)
        tempo = float(getattr(event, "tempo", self._music_tempo))
        if 45.0 <= tempo <= 210.0:
            self._music_tempo = _blend(self._music_tempo, tempo, 0.35)
        self._beat_phase = 0.0
        self._beat_pulse = max(self._beat_pulse, strength)
        self._beat_count += 1
        self._last_beat_at = now
        style = self._music_effective_style()
        if style not in ("groovy", "hype"):
            return
        cooldown = 3.0 if style == "hype" else 6.0
        gate = 0.45 if style == "hype" else 0.68
        if strength >= gate and intensity >= 0.35 and now - self._last_music_jump >= cooldown:
            self._music_jump_start = now
            self._last_music_jump = now
            self._spawn_dust(2)

    def preview_music(self, style: str | None = None, intensity: int | None = None,
                      seconds: float = 10.0):
        """Dance without any music; useful for choosing a style."""
        chosen = self._valid_style(style if style is not None else self._music_style)
        if chosen == "off":
            return
        now = time.monotonic()
        self._demo_style = chosen
        self._demo_intensity = _clamp(
            (self._music_intensity if intensity is None else float(intensity) / 100.0),
            0.0, 1.0,
        )
        self._demo_until = now + max(3.0, float(seconds))
        self._music_energy = max(0.55, self._music_energy)
        self._music_tempo = 112.0
        self._auto_mood = "music_groove"
        self._auto_eval_at = now + 8.0
        self._refresh_music_active()
        logging.info("Previewing music style %s for %.0f s", chosen, seconds)

    def clear_music(self):
        # The watcher re-confirms the source when reactions are turned back on;
        # keeping a stale flag here would dance to silence for one report.
        self._music_live = False
        self._classifier_music = False
        self._always_music = False
        self._always_label = ""
        self._demo_until = 0.0
        self._demo_style = ""
        self._refresh_music_active()

    def _refresh_music_active(self):
        demo = time.monotonic() < self._demo_until
        active = self._music_live or demo
        if active == self._music_active:
            return
        self._music_active = active
        if active:
            self._activate_music()
        else:
            self._deactivate_music()

    def _activate_music(self):
        now = time.monotonic()
        self._music_started_at = now
        self._beat_phase = 0.0
        self._beat_pulse = 0.0
        self._auto_eval_at = now + (0.0 if self._music_live else 8.0)
        if self._mood == "sleep":
            self._set_mood("wake", 0.85)
        logging.info("Music reactions active (%s)", self._music_effective_style())

    def _deactivate_music(self):
        self._music_jump_start = None
        if self._mood in MUSIC_MOODS:
            self._set_mood("idle")
            # Let the cat settle for a moment, then doze off.
            self._last_interaction = time.monotonic() - max(0.0, self._sleep_delay - 3.0)
        logging.info("Music reactions stopped")

    def _music_ready(self) -> bool:
        return (
            self._music_active
            and self._music_allowed()
            and self._mode == "desktop"
            and not self._busy
            and not self._down
            and not self._dragging
            and not self._menu_open
            and not self._user_hidden
            and self._gesture is None
        )

    def _music_effective_style(self) -> str:
        if time.monotonic() < self._demo_until and self._demo_style:
            return self._demo_style
        if self._music_style == "auto":
            return _MUSIC_STYLE_FOR_MOOD.get(self._auto_mood, "groovy")
        return self._music_style

    def _music_mood_name(self) -> str:
        if (self._music_style == "auto"
                and time.monotonic() >= self._demo_until
                and self._auto_mood in MUSIC_MOODS):
            return self._auto_mood
        return _MUSIC_MOOD_FOR_STYLE.get(self._music_effective_style(), "music_groove")

    def _music_intensity_now(self) -> float:
        if time.monotonic() < self._demo_until:
            return _clamp(self._demo_intensity * (0.55 + 0.45 * self._music_energy), 0.15, 1.0)
        return _clamp(self._music_intensity * (0.50 + 0.50 * self._music_energy), 0.12, 1.0)

    # -------------------------------------------------------------- interaction

    def enterEvent(self, event):
        self._hovered = True
        self._last_interaction = time.monotonic()
        if self._busy or self._mode != "desktop":
            return
        self._update_look()
        if self._gesture is not None:
            return
        if self._mood == "sleep":
            self._set_mood("wake", 0.9)
        elif self._mood in ("idle", "land", "sit"):
            self._set_mood("curious")
        elif self._mood == "curious":
            self._hop_start = time.monotonic()

    def leaveEvent(self, event):
        self._hovered = False
        self._look_target = QPointF(0, 0)
        if self._gesture is None and self._mood in ("curious", "wake"):
            self._set_mood("idle")

    def _update_look(self):
        if not self._hovered and self._gesture is None:
            return
        local = self.mapFromGlobal(QCursor.pos())
        dx = (local.x() - CX) / 64.0
        dy = (local.y() - 94) / 64.0
        self._look_target = QPointF(_clamp(dx, -1, 1), _clamp(dy, -1, 1))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._busy:
            return
        now = time.monotonic()
        self._click_timer.stop()
        self._cancel_motion_gesture()
        self._down = True
        self._dragging = False
        self._press_global = event.globalPosition().toPoint()
        self._press_pos = self.pos()
        self._last_global = self._press_global
        self._last_move_t = now
        self._velocity = QPointF(0, 0)
        self._last_interaction = now
        self._trail.clear()
        center = self.mapToGlobal(QPoint(int(CX), 94))
        self._hold_side = 1 if self._press_global.x() >= center.x() else -1
        if self._slide_anim is not None:
            self._slide_anim.stop()
        if self._mood == "sleep":
            self._set_mood("wake", 0.7)
        elif self._mood in ("idle", "curious", "land", "sit", "pet") or self._mood in MUSIC_MOODS:
            self._set_mood("curious")
        self._hold_timer.start()

    def mouseMoveEvent(self, event):
        if not self._down:
            self._update_look()
            return
        gp = event.globalPosition().toPoint()
        delta = gp - self._press_global
        if not self._dragging and delta.manhattanLength() >= 6:
            self._dragging = True
            self._hold_timer.stop()
            self._gesture = "drag"
            self._trail.clear()
            self._cursor_timer.stop()
            self._set_mood("walk")
        if self._dragging:
            now = time.monotonic()
            dt = max(now - self._last_move_t, 0.001)
            self._velocity = QPointF(
                (gp.x() - self._last_global.x()) / dt,
                (gp.y() - self._last_global.y()) / dt,
            )
            self._last_global = gp
            self._last_move_t = now
            target = self._clamp_pos(self._press_pos + delta)
            self.move(target)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._down:
            return
        self._down = False
        self._hold_timer.stop()
        now = time.monotonic()
        self._last_interaction = now
        gesture = self._gesture
        if self._dragging:
            self._dragging = False
            self._gesture = None
            self._gesture_block_until = now + 0.5
            self._spawn_dust(3)
            gliding = self._clamp_pos(self.pos() + QPoint(
                int(_clamp(self._velocity.x() * 0.09, -70, 70)),
                int(_clamp(self._velocity.y() * 0.09, -70, 70)),
            ))
            if gliding != self.pos():
                self._slide_anim = QPropertyAnimation(self, b"pos", self)
                self._slide_anim.setDuration(230)
                self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._slide_anim.setStartValue(self.pos())
                self._slide_anim.setEndValue(gliding)
                self._slide_anim.finished.connect(self._after_drag)
                self._slide_anim.start()
            else:
                self._after_drag()
            self._set_mood("sit", 0.95)
        elif gesture == "hold":
            self._gesture = None
            self._gesture_block_until = now + 0.45
            self._spawn_heart()
            self._set_mood("curious" if self._hovered else "idle")
        elif gesture in ("spin", "chase", "flick"):
            return
        else:
            self._click_timer.start()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._busy:
            return
        if self._gesture in ("spin", "chase", "flick"):
            return
        now = time.monotonic()
        if now < self._gesture_block_until:
            return
        self._click_timer.stop()
        self._hold_timer.stop()
        self._start_spin()

    def _after_drag(self):
        self.position_saved.emit(self.x(), self.y())

    def _pet(self):
        if self._busy or self._mode != "desktop" or self._gesture is not None:
            return
        now = time.monotonic()
        if now - self._last_pet < 1.1:
            self._pet_count = min(self._pet_count + 1, 5)
        else:
            self._pet_count = 1
        self._last_pet = now
        self._last_interaction = now
        self._pet_side = random.choice((-1, 1))
        self._set_mood("pet", 2.4)
        for _ in range(2 + self._pet_count):
            self._spawn_heart()
        self._next_heart = now + 0.42

    def _click_wake(self):
        """A plain click wakes the cat briefly; it does not start full petting."""
        if self._busy or self._mode != "desktop" or self._gesture is not None:
            return
        now = time.monotonic()
        self._last_interaction = now
        self._update_look()
        was_asleep = self._eye_close > 0.45 or self._mood == "sleep"
        self._blink_start = None
        if was_asleep:
            self._set_mood("wake", 1.1)
        else:
            self._set_mood("curious", 1.4)

    # -------------------------------------------------------------- gestures

    def _begin_hold(self):
        if (not self._down or self._dragging or self._busy or self._gesture is not None
                or self._mode != "desktop"):
            return
        now = time.monotonic()
        self._gesture = "hold"
        self._last_interaction = now
        self._pet_side = self._hold_side
        self._set_mood("pet")
        self._next_heart = now + 0.55
        self._spawn_heart()

    def _start_spin(self):
        now = time.monotonic()
        self._gesture = "spin"
        self._spin_start = now
        self._gesture_until = now + 0.72
        self._last_interaction = now
        self._trail.clear()
        self._cursor_timer.stop()
        self._set_mood("spin", 0.72)
        for _ in range(2):
            self._spawn_heart()

    def _start_chase(self, direction: int):
        if self._busy or self._mode != "desktop" or self._gesture is not None:
            return
        now = time.monotonic()
        start = QPoint(self.x(), self.y())
        center = start + QPoint(W // 2, H // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        room_x = min(start.x() - area.left(), area.right() - W - start.x())
        room_y = min(start.y() - area.top(), area.bottom() - H - start.y())
        radius = _clamp(min(room_x, room_y) * 0.8, 14.0, 46.0)
        self._chase = {
            "start": start,
            "r": radius,
            "dir": 1 if direction >= 0 else -1,
            "t0": now,
            "dur": 1.15,
        }
        self._gesture = "chase"
        self._gesture_until = now + 1.15
        self._last_interaction = now
        self._trail.clear()
        self._cursor_timer.stop()
        self._set_mood("chase")
        self._update_look()

    def _update_chase(self, now: float):
        chase = self._chase
        if chase is None:
            self._gesture = None
            return
        u = _clamp((now - chase["t0"]) / chase["dur"], 0.0, 1.0)
        ease = _smooth(u)
        angle = -math.pi / 2 + chase["dir"] * _TAU * ease
        radius = chase["r"] * math.sin(math.pi * ease)
        x = chase["start"].x() + radius * math.cos(angle)
        y = chase["start"].y() + radius * math.sin(angle) * 0.75
        self.move(int(round(x)), int(round(y)))
        self._dir = 1 if math.cos(angle) >= 0 else -1
        if u >= 1.0:
            self.move(chase["start"])
            self._chase = None
            self._gesture = None
            self._gesture_block_until = now + 0.9
            self._last_interaction = now
            self._set_mood("curious" if self._hovered else "idle")

    def _start_flick(self):
        if self._busy or self._mode != "desktop" or self._gesture is not None:
            return
        now = time.monotonic()
        self._flick_start = now
        self._gesture = "flick"
        self._gesture_until = now + 0.62
        self._last_interaction = now
        self._trail.clear()
        self._cursor_timer.stop()
        self._set_mood("flick", 0.62)
        self._update_look()

    def _update_flick(self, now: float):
        start = self._flick_start
        if start is None or now - start < 0.58:
            return
        self._flick_start = None
        self._gesture = None
        self._gesture_block_until = now + 0.6
        self._spawn_dust(2)
        self._set_mood("land", 0.34)

    def _cancel_motion_gesture(self):
        """A new press takes over from spin/chase/flick without leaving them running."""
        if self._gesture in ("spin", "chase", "flick"):
            self._gesture = None
            self._chase = None
            self._flick_start = None
            self._gesture_block_until = time.monotonic() + 0.4

    def _cancel_gestures(self):
        self._hold_timer.stop()
        self._click_timer.stop()
        self._cursor_timer.stop()
        self._trail.clear()
        self._gesture = None
        self._chase = None
        self._flick_start = None
        self._spin_start = None
        self._gesture_block_until = time.monotonic() + 0.4

    def _cursor_center(self) -> QPoint:
        return self.mapToGlobal(QPoint(int(CX), 94))

    def _cursor_near(self) -> bool:
        center = self._cursor_center()
        cursor = QCursor.pos()
        return abs(cursor.x() - center.x()) <= CURSOR_REACH and abs(cursor.y() - center.y()) <= CURSOR_REACH

    def _update_cursor_sampler(self, now: float):
        eligible = (
            not self._busy
            and self._mode == "desktop"
            and not self._down
            and not self._menu_open
            and self._gesture is None
            and not self._user_hidden
            and now >= self._gesture_block_until
        )
        if not eligible:
            if self._cursor_timer.isActive():
                self._cursor_timer.stop()
            self._trail.clear()
            return
        if self._cursor_near():
            if not self._cursor_timer.isActive():
                self._cursor_timer.start()
        else:
            if self._cursor_timer.isActive():
                self._cursor_timer.stop()
            self._trail.clear()

    def _sample_cursor(self):
        if self._busy or self._gesture is not None or self._down:
            return
        now = time.monotonic()
        center = self._cursor_center()
        cursor = QCursor.pos()
        self._feed_cursor_sample(now, cursor.x() - center.x(), cursor.y() - center.y())

    def _feed_cursor_sample(self, now: float, dx: float, dy: float):
        """Track cursor polar coordinates and recognise a loop or an upward flick."""
        distance = math.hypot(dx, dy)
        if distance < 22 or distance > 190 or now < self._gesture_block_until:
            self._trail.clear()
            return
        self._trail.append((now, math.atan2(dy, dx), distance, dx, dy))
        if len(self._trail) > 180:
            del self._trail[:-180]
        while self._trail and now - self._trail[0][0] > 2.4:
            self._trail.pop(0)

        recent = [s for s in self._trail if now - s[0] <= 0.30]
        if len(recent) >= 3:
            duration = recent[-1][0] - recent[0][0]
            travel_y = recent[-1][4] - recent[0][4]
            travel_x = abs(recent[-1][3] - recent[0][3])
            arc = 0.0
            for prev, cur in zip(recent, recent[1:]):
                arc += abs(_wrap_angle(cur[1] - prev[1]))
            if (0.06 <= duration <= 0.30 and travel_y <= -70
                    and abs(travel_y) > travel_x * 2.2 and arc < 1.35):
                speed = math.hypot(travel_x, travel_y) / duration
                if speed >= 420:
                    self._start_flick()
                    return

        if len(self._trail) < 12:
            return
        duration = self._trail[-1][0] - self._trail[0][0]
        if not 0.45 <= duration <= 2.4:
            return
        total = 0.0
        for prev, cur in zip(self._trail, self._trail[1:]):
            total += _wrap_angle(cur[1] - prev[1])
        radii = [s[2] for s in self._trail]
        mean_radius = sum(radii) / len(radii)
        if (abs(total) >= _TAU * 0.9 and max(radii) - min(radii) <= 55
                and 28 <= mean_radius <= 165):
            self._start_chase(1 if total > 0 else -1)

    def contextMenuEvent(self, event):
        if self._busy:
            return
        self._menu_open = True
        self._trail.clear()
        self._cursor_timer.stop()
        menu = QMenu(self)
        pet_action = menu.addAction(f"Pet {self._pet_name}")
        menu.addSeparator()
        settings_action = menu.addAction("Settings…")
        pause_action = menu.addAction(
            "Pause protection" if self.settings.get("enabled") else "Enable protection"
        )
        menu.addSeparator()
        hide_action = menu.addAction("Hide cat")
        quit_action = menu.addAction("Quit")
        try:
            chosen = menu.exec(event.globalPos())
        finally:
            self._menu_open = False
            self._gesture_block_until = time.monotonic() + 0.35
        if chosen == pet_action:
            self._pet()
        elif chosen == settings_action:
            self.settings_requested.emit()
        elif chosen == pause_action:
            self.protection_toggle_requested.emit()
        elif chosen == hide_action:
            self.hide_cat()
        elif chosen == quit_action:
            self.quit_requested.emit()
        self._enforce_bottom()

    # -------------------------------------------------------------- distraction

    def distraction(self, kind: str = "site", label: str = "", info=None,
                    ticket=None, tab_index=None, tab_count=None, close=False,
                    targetless=False):
        if ticket is not None and (not self.settings.get("enabled") or self._user_hidden):
            # Without a visible cat there is nothing to animate; close at once.
            ticket.close_now()
            return
        if not self.settings.get("enabled"):
            return
        if self._greeting:
            self._cancel_greeting()
        self._dismiss_brag()
        if self._busy:
            if self._queued is not None:
                old = self._queued.get("ticket")
                if old is not None:
                    old.close_now()
            self._queued = {
                "kind": kind, "label": label, "info": info, "ticket": ticket,
                "tab_index": tab_index, "tab_count": tab_count, "close": close,
            }
            return
        self._cancel_gestures()
        now = time.monotonic()
        if ticket is not None and ticket.resolved:
            return
        self._busy = True
        self._busy_since = now
        self._last_interaction = now
        self._brag_planned = False
        self._desk_pos = QPoint(self.x(), self.y())
        # `targetless` is used when the app is closed at once: there is no
        # window left to perch on, so the cat still plays its full visit on the
        # desktop instead of aborting when the target disappears.
        if info is None and not targetless:
            info = native.foreground_window_info()
        self._target_hwnd = (0 if targetless
                             else info.hwnd if info is not None and not info.shell else 0)
        if (ticket is not None and self._target_hwnd
                and not native.is_foreground(self._target_hwnd)):
            ticket.close_now()
            self._return_to_desktop()
            return

        area, target = self._stage_area(info)
        start = QPoint(self.x(), self.y())
        self._ticket = ticket
        self._close_ticket_id = ticket.request_id if ticket is not None else ""
        self._close_failed = False
        self._close_result_seen = ticket is None
        self._close_aborted = False
        self._close_contact_at = 0.0
        self._tap_paw = None
        self._warn_mode = ticket is None
        self._target_rect = (
            (info.left, info.top, info.right, info.bottom)
            if info is not None and not info.shell else None
        )
        self._target_check_at = now + 0.25

        tap_pos = None
        tap_paw = None
        close_point = None
        if ticket is not None:
            minimized = bool(self._target_hwnd and native.is_minimized(self._target_hwnd))
            if target is not None and not minimized:
                close_point = self._close_button_point(target, tab_index, tab_count)
            if close_point is not None:
                tap_x = int(_clamp(close_point.x() - TAP_PAW_X, area.left(), area.right() - W))
                tap_y = int(_clamp(close_point.y() - TAP_PAW_Y, area.top(), area.bottom() - H))
                tap_pos = QPoint(tap_x, tap_y)
                tap_paw = QPointF(
                    _clamp(close_point.x() - tap_x, 60.0, 166.0),
                    _clamp(close_point.y() - tap_y, 8.0, 90.0),
                )

        if ticket is not None and tap_pos is not None:
            self._tap_paw = tap_paw
            self._tap_side = 1
            run_y = area.bottom() - H - 8
            run_end = QPoint(
                int(_clamp(tap_pos.x(), area.left() + 4, area.right() - W - 4)), run_y
            )
            approach = QPoint(
                tap_pos.x(),
                int(_clamp(tap_pos.y() + 46, area.top(), area.bottom() - H)),
            )
            distance = math.hypot(run_end.x() - start.x(), run_end.y() - start.y())
            run_dur = _clamp(distance / 1700.0, 0.3, 0.85)
            self._dir = 1 if close_point.x() >= start.x() + CX else -1
            self._timeline = [
                Segment(0.20, start, start, "alert", arc=10),
                Segment(run_dur, start, run_end, "run", smooth=False),
                Segment(0.55, run_end, approach, "leap", arc=46),
                Segment(0.24, approach, tap_pos, "climb"),
                Segment(0.22, tap_pos, tap_pos, "orient"),
                Segment(0.24, tap_pos, tap_pos, "raise"),
                Segment(0.30, tap_pos, tap_pos, "tap", contact=0.64),
                Segment(0.40, tap_pos, tap_pos, "react"),
                Segment(0.60, tap_pos, tap_pos, "smug"),
            ]
        elif ticket is not None:
            # The browser is minimized, hidden, or its tab strip is unreachable:
            # do a small "shoo" where the cat stands instead of faking a button.
            self._dir = 1
            self._timeline = [
                Segment(0.22, start, start, "alert", arc=10),
                Segment(0.85, start, start, "pounce", contact=0.5),
                Segment(0.55, start, start, "smug"),
            ]
            self._show_bubble("Closing it anyway", 2.0)
        else:
            center_x = target.center().x() if target is not None else area.center().x()
            lo_x, hi_x = area.left() + 6, area.right() - W - 6
            if target is not None and target.width() > W + 24:
                lo_x = max(lo_x, target.left() + 8)
                hi_x = min(hi_x, target.right() - W - 8)
            perch_x = _clamp(center_x - W / 2, lo_x, hi_x)
            if target is not None:
                perch_y = _clamp(target.top() + 8, area.top() + 4, area.bottom() - H - 4)
                run_y = _clamp(target.bottom() - H + 12, area.top() + 4, area.bottom() - H - 4)
            else:
                perch_y = area.top() + 26
                run_y = area.bottom() - H - 10
            run_end = QPoint(int(perch_x), int(run_y))
            perch = QPoint(int(perch_x), int(perch_y))
            distance = math.hypot(run_end.x() - start.x(), run_end.y() - start.y())
            run_dur = _clamp(distance / 1900.0, 0.32, 0.72)
            self._dir = 1 if perch.x() >= start.x() else -1
            # A closing app is confirmed asynchronously; the cat holds its pose
            # until then so the brag can grow out of this same visit.
            hold = 2.2 if targetless else 0.8
            self._timeline = [
                Segment(0.22, start, start, "alert", arc=12),
                Segment(run_dur, start, run_end, "run", smooth=False),
                Segment(0.5, run_end, perch, "leap", arc=62),
                Segment(0.55, perch, perch, "pounce"),
                Segment(hold, perch, perch, "smug"),
            ]

        self._seg_i = 0
        self._seg_t0 = now
        self._particles.clear()

        self.setWindowOpacity(0.0)
        self._enter_overlay()
        self._fade_to(1.0, 170)
        if kind == "app":
            text = f"Back to work, {label}!" if label else "Back to work!"
        else:
            text = f"Caught {label}" if label else "Caught one!"
        if ticket is None or tap_pos is not None:
            self._show_bubble(text, 2.6)
        self._set_mood("alert")
        logging_label = label or kind
        notify_text = (
            f"{self._pet_name} caught {logging_label}" if kind == "site"
            else f"{self._pet_name} spotted {logging_label}"
        )
        self.notify.emit("Cat Guardian", notify_text)

    def _close_button_point(self, target: QRect | None,
                            tab_index=None, tab_count=None) -> QPoint | None:
        """Estimate the active tab's close (x) area inside a Chromium window.

        Browser extensions cannot read the native tab-strip geometry, so this
        combines the window rect with the tab index/count reported by the
        extension. When that is not reliable, the window close button is used.
        The real close is always performed by the extension, never by a click.
        """
        if target is None or target.width() < 520 or target.height() < 260:
            return None
        if tab_index is not None and tab_count:
            try:
                count = max(1, min(int(tab_count), 60))
                index = min(max(int(tab_index), 0), count - 1)
            except (TypeError, ValueError):
                return QPoint(target.right() - 24, target.top() + 20)
            available = max(220.0, target.width() - 150.0)
            tab_width = min(240.0, available / count)
            x = target.left() + 6 + index * tab_width + tab_width - 15
            y = target.top() + 20
            if target.left() + 40 <= x <= target.right() - 90:
                return QPoint(int(x), int(y))
        return QPoint(target.right() - 24, target.top() + 20)

    def _stage_area(self, info):
        """Return the logical work area and the target window rect in Qt coords.

        Win32 reports physical pixels while Qt works in logical ones, so the
        foreground rect is mapped through the matching screen's DPR.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        target = None
        if info is not None and info.width > 120:
            bounds = native.monitor_bounds(
                (info.left + info.right) // 2, (info.top + info.bottom) // 2
            )
            if bounds is not None:
                mon_rect, work_rect = bounds
                match = self._screen_for(mon_rect) or screen
                if match is not None:
                    screen = match
                    area = self._to_logical(work_rect, mon_rect, screen)
                    if not info.shell:
                        target = self._to_logical(
                            (info.left, info.top, info.right, info.bottom), mon_rect, screen
                        )
        return area, target

    @staticmethod
    def _screen_for(mon_rect):
        width = mon_rect[2] - mon_rect[0]
        height = mon_rect[3] - mon_rect[1]
        best, best_err = None, None
        for screen in QGuiApplication.screens():
            geom = screen.geometry()
            dpr = screen.devicePixelRatio() or 1.0
            err = abs(geom.width() * dpr - width) + abs(geom.height() * dpr - height)
            if best_err is None or err < best_err:
                best, best_err = screen, err
        return best

    @staticmethod
    def _to_logical(rect, mon_rect, screen):
        dpr = screen.devicePixelRatio() or 1.0
        geom = screen.geometry()
        left = geom.left() + round((rect[0] - mon_rect[0]) / dpr)
        top = geom.top() + round((rect[1] - mon_rect[1]) / dpr)
        right = geom.left() + round((rect[2] - mon_rect[0]) / dpr)
        bottom = geom.top() + round((rect[3] - mon_rect[1]) / dpr)
        return QRect(left, top, max(1, right - left), max(1, bottom - top))

    def _update_timeline(self, now: float):
        if not self._timeline:
            return
        while self._timeline:
            seg = self._timeline[self._seg_i]
            progress = (now - self._seg_t0) / seg.dur if seg.dur > 0 else 1.0
            if (seg.contact is not None and not seg.contact_fired
                    and progress >= seg.contact):
                seg.contact_fired = True
                self._paw_contact()
            if progress >= 1.0:
                self.move(seg.end)
                self._seg_i += 1
                if self._seg_i >= len(self._timeline):
                    self._timeline = []
                    self._finish_sequence()
                    return
                self._seg_t0 = now
                nxt = self._timeline[self._seg_i]
                if nxt.mood == "walk" and nxt.end.x() != nxt.start.x():
                    self._dir = 1 if nxt.end.x() > nxt.start.x() else -1
                self._set_mood(nxt.mood)
                continue
            t = _clamp(progress, 0.0, 1.0)
            e = t * t * (3 - 2 * t) if seg.smooth else t
            x = seg.start.x() + (seg.end.x() - seg.start.x()) * e
            y = seg.start.y() + (seg.end.y() - seg.start.y()) * e - seg.arc * math.sin(math.pi * t)
            self.move(int(round(x)), int(round(y)))
            break

    def _paw_contact(self):
        self._close_contact_at = time.monotonic()
        if self._ticket is not None and not self._ticket.resolved:
            self._ticket.reach_contact()
            logging.info("Paw contact reached for close request %s", self._close_ticket_id)
        self._spawn_spark()

    def _finish_sequence(self):
        if self._ticket is not None and not self._ticket.resolved:
            self._ticket.close_now()
            logging.info("Sequence ended before contact; closing %s now", self._close_ticket_id)
        self._fade_to(0.0, 200, self._return_to_desktop)

    def _abort_overlay(self):
        """Duck back to the desktop if the target window lost focus."""
        if self._ticket is not None and not self._ticket.resolved:
            self._ticket.close_now()
            logging.info("Animation aborted; closing %s immediately", self._close_ticket_id)
        # The tab still closes, but the cat was interrupted, so it does not brag.
        self._close_aborted = True
        self._timeline = []
        self._seg_i = 0
        self._fade_to(0.0, 130, self._return_to_desktop)

    def _return_to_desktop(self):
        if self._ticket is not None and not self._ticket.resolved:
            self._ticket.close_now()
        if (self._close_ticket_id and self._ticket is not None
                and not self._close_result_seen):
            logging.info("No close result reported for %s", self._close_ticket_id)
        self._ticket = None
        self._tap_paw = None
        self._target_rect = None
        self._dismiss_brag()
        self._brag_planned = False
        self._paste_busy = False
        self._paste_picking = False
        self._paste_icon = None
        self._paste_count = 1
        self._paste_cut = False
        queued = self._queued
        self._queued = None
        self.setWindowOpacity(0.0)
        self._enter_desktop()
        self.move(self._clamp_pos(self._desk_pos))
        self._fade_to(1.0, 240)
        self._busy = False
        self._set_mood("idle")
        self._last_interaction = time.monotonic()
        if self._user_hidden:
            self.hide()
        elif queued is not None:
            QTimer.singleShot(600, lambda: self.distraction(**queued))

    def _fade_to(self, value: float, ms: int, done=None):
        if self._fade_anim is not None:
            self._fade_anim.stop()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(value)
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        if done is not None:
            anim.finished.connect(done)
        self._fade_anim = anim
        anim.start()

    # ------------------------------------------------------------------- mood

    def _set_mood(self, mood: str, duration: float | None = None):
        previous = self._mood
        self._mood = mood
        self._mood_since = time.monotonic()
        self._mood_until = self._mood_since + duration if duration else None
        if mood == "proud" and previous != "proud" and self._brag_planned:
            if not self._user_hidden:
                self._say_brag(BRAG_TEXT)
        elif previous == "proud" and mood != "proud":
            self._dismiss_brag()
        if mood == "drop" and self._paste_busy:
            self._show_bubble("Moved!" if self._paste_cut else "Delivered!", 1.3)
        if mood == "pickup" and self._paste_busy and self._paste_picking:
            self._show_bubble("Moving it!" if self._paste_cut else "Got it!", 1.1)
        if mood in ("pet", "walk", "land", "sit", "wake", "spin", "chase",
                    "flick", "tap", "react", "confused", "greet", "proud",
                    "pickup", "carry_walk", "drop"):
            self._last_interaction = self._mood_since
        interval = 80 if mood == "sleep" else 16
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)

    def _show_bubble(self, text: str, seconds: float):
        self._bubble_text = text
        self._bubble_until = time.monotonic() + seconds

    def close_result(self, request_id: str, ok: bool, reason: str = ""):
        if not request_id or request_id != self._close_ticket_id:
            return
        self._close_result_seen = True
        if ok:
            logging.info("Close request %s succeeded", request_id)
            self.confirm_close("site")
            return
        self._close_failed = True
        message = {
            "gone": "It closed itself!",
            "navigated": "It got away!",
            "close_failed": "Couldn't close it",
        }.get(reason, "Missed it!")
        if self._busy and self._mode == "overlay":
            self._show_bubble(message, 1.8)
        else:
            self._set_mood("confused", 1.5)
            self._show_bubble(message, 1.5)
        logging.info("Close request %s failed: %s", request_id, reason or "unknown")

    def shutdown(self):
        self._cancel_greeting()
        self._dismiss_brag()
        if self._speech is not None:
            self._speech.close()
            self._speech = None
        self._cancel_gestures()
        self._music_live = False
        self._classifier_music = False
        self._always_music = False
        self._always_label = ""
        self._music_active = False
        self._demo_until = 0.0
        self._paste_busy = False
        self._paste_picking = False
        self._paste_icon = None
        self._click_timer.stop()
        self._hold_timer.stop()
        self._cursor_timer.stop()
        self._timer.stop()
        self._z_timer.stop()
        if self._fade_anim is not None:
            self._fade_anim.stop()
        if self._slide_anim is not None:
            self._slide_anim.stop()
        if self._ticket is not None and not self._ticket.resolved:
            self._ticket.close_now()
        if self._queued is not None:
            queued_ticket = self._queued.get("ticket")
            if queued_ticket is not None and not queued_ticket.resolved:
                queued_ticket.close_now()
        self._queued = None
        self._timeline = []

    # ------------------------------------------------------------------- loop

    def _tick(self):
        now = time.monotonic()
        dt = min(now - self._last_tick, 0.12)
        self._last_tick = now

        if self._busy and self._mode == "overlay" and self._timeline:
            if self._target_hwnd and now - self._busy_since > 0.3:
                if not native.is_foreground(self._target_hwnd):
                    self._abort_overlay()
                elif self._ticket is not None and now >= self._target_check_at:
                    self._target_check_at = now + 0.3
                    if native.is_minimized(self._target_hwnd):
                        logging.info("Target window minimized; ending the visit")
                        self._abort_overlay()
                    else:
                        rect = native.window_rect(self._target_hwnd)
                        if rect is not None and self._target_rect is not None:
                            drift = max(abs(a - b) for a, b in zip(rect, self._target_rect))
                            if drift > 10:
                                logging.info("Target window moved; ending the visit")
                                self._abort_overlay()

        if self._busy and self._gesture is not None:
            self._cancel_gestures()

        if self._gesture == "chase":
            self._update_chase(now)
        elif self._gesture == "flick":
            self._update_flick(now)
        elif self._gesture == "spin" and now >= self._gesture_until:
            self._gesture = None
            self._spin_start = None
            self._gesture_block_until = now + 0.5
        elif self._gesture == "hold" and not self._down:
            self._gesture = None
            self._gesture_block_until = now + 0.35

        if self._timeline:
            self._update_timeline(now)
        elif self._mood_until is not None and now >= self._mood_until:
            self._mood_until = None
            next_mood = "curious" if self._hovered else "idle"
            if self._mood == "wake" and not self._hovered:
                next_mood = "idle"
            self._set_mood(next_mood)

        self._update_music(now, dt)
        if (self._music_ready() and self._mood in ("idle", "curious")
                and (self._mood_until is None or now >= self._mood_until)):
            self._set_mood(self._music_mood_name())

        if (not self._busy and self._mode == "desktop" and not self._hovered
                and not self._down and not self._dragging and not self._menu_open
                and self._gesture is None and self._mood == "idle"
                and not self._music_active
                and now - self._last_interaction > self._sleep_delay):
            self._set_mood("sleep")

        if self._mood == "sleep":
            if now >= self._next_sleep_settle:
                self._next_sleep_settle = now + random.uniform(6.0, 12.0)
                self._sleep_settle_start = now
            if now >= self._next_tail_flick:
                self._next_tail_flick = now + random.uniform(7.0, 14.0)
                self._tail_flick_start = now
        target_closed = 1.0 if self._mood == "sleep" else 0.0
        self._eye_close = _blend(self._eye_close, target_closed, dt * (4.5 if target_closed else 7.0))
        self._sleep_pose = _blend(self._sleep_pose, target_closed, dt * (2.4 if target_closed else 5.0))

        self._update_cursor_sampler(now)
        self._update_gaze(now)
        self._update_ears(now, dt)
        self._update_blink(now)
        self._update_particles(now, dt)
        self._update_particle_spawners(now)

        self.update()

    def _update_music(self, now: float, dt: float):
        if (self._music_live
                and now - self._music_report_at > MUSIC_REPORT_TIMEOUT):
            # Capture died or was stopped without a final report; never dance to silence.
            self._music_live = False
            self._refresh_music_active()
        if self._demo_until and now >= self._demo_until:
            self._demo_until = 0.0
            self._demo_style = ""
            self._refresh_music_active()
        if not self._music_active:
            return
        self._beat_phase = (self._beat_phase + dt * self._music_tempo / 60.0) % 1.0
        self._beat_pulse = max(0.0, self._beat_pulse - dt * 3.2)
        if self._music_jump_start is not None and now - self._music_jump_start > 0.62:
            self._music_jump_start = None
        if now >= self._auto_eval_at:
            if self._music_style == "auto" and self._music_catalog:
                self._auto_mood, wait = self._dance_brain.choose(
                    MusicProfile(
                        tempo=self._music_tempo,
                        energy=self._music_energy,
                        beat=self._music_beat_strength,
                        confidence=self._music_confidence,
                        intensity=self._music_intensity,
                    ),
                    now=now,
                    current=self._auto_mood,
                )
                self._auto_eval_at = now + wait
            else:
                self._auto_eval_at = now + 15.0
                drive = _clamp(self._music_energy * (0.55 + 0.45 * self._music_intensity), 0.0, 1.0)
                if drive < 0.30 or self._music_tempo < 84:
                    choice = "music_chill"
                elif drive > 0.62 or (self._music_tempo > 126 and drive > 0.40):
                    choice = "music_hype"
                else:
                    choice = "music_groove"
                self._auto_mood = choice
        if self._mood in MUSIC_MOODS:
            wanted = self._music_mood_name()
            if wanted != self._mood:
                self._set_mood(wanted)

    def _update_gaze(self, now: float):
        mood = self._mood
        if mood == "sleep":
            self._look_target = QPointF(0, 0.5)
        elif mood in ("run", "leap"):
            self._look_target = QPointF(0.8 * self._dir, -0.2)
        elif mood in ("alert", "pounce"):
            self._look_target = QPointF(0.2 * self._dir, -0.3)
        elif mood in ("orient", "raise", "tap", "react"):
            self._look_target = QPointF(0.85, -0.95)
        elif mood == "smug":
            self._look_target = QPointF(0.1, -0.2)
        elif mood == "confused":
            self._look_target = QPointF(-0.55, -0.15)
        elif mood in MUSIC_MOODS:
            self._look_target = QPointF(
                math.sin(self._beat_phase * _TAU) * 0.16,
                -0.08 if mood == "music_hype" else 0.05,
            )
        elif mood == "greet":
            self._look_target = QPointF(0.0, -0.12)
        elif mood == "proud":
            self._look_target = QPointF(0.0, -0.25)
        elif mood in ("pickup", "drop"):
            self._look_target = QPointF(0.15, 0.85)
        elif mood in ("drag", "walk", "carry_walk", "chase", "flick"):
            self._update_look()
        elif self._hovered and mood in ("curious", "pet"):
            self._update_look()
        elif now >= self._next_gaze:
            self._next_gaze = now + random.uniform(2.4, 6.0)
            if mood in ("idle", "land", "sit"):
                self._look_target = QPointF(random.uniform(-0.7, 0.7), random.uniform(-0.4, 0.5))
        self._look = QPointF(
            _blend(self._look.x(), self._look_target.x(), 0.18),
            _blend(self._look.y(), self._look_target.y(), 0.18),
        )

    def _update_ears(self, now: float, dt: float):
        targets = {
            "idle": 0.2, "curious": 1.0, "pet": 0.35, "drag": 0.5,
            "land": 0.3, "run": 0.35, "leap": 0.05, "alert": 1.0,
            "pounce": 0.9, "smug": 0.55, "sleep": 0.0, "wake": 0.85,
            "walk": 0.5, "sit": 0.25, "spin": 1.0, "chase": 0.8,
            "flick": 0.1, "orient": 0.9, "raise": 0.9, "tap": 0.95,
            "react": 0.95, "confused": 0.6, "greet": 0.95, "proud": 0.95,
            "pickup": 0.75, "carry_walk": 0.6, "drop": 0.8,
            "music_chill": 0.22, "music_groove": 0.65,
            "music_hype": 0.9, "music_minimal": 0.35,
            "music_zen": 0.18, "music_sway": 0.35, "music_strut": 0.7,
            "music_paw": 0.55, "music_wave": 0.85, "music_bounce": 0.7,
            "music_twirl": 0.8, "music_hop": 0.9, "music_headbang": 0.95,
            "music_dip": 0.4,
        }
        target = targets.get(self._mood, 0.2)
        if self._hovered and self._mood in ("idle", "land", "sit"):
            target = 0.85
        self._ear = _blend(self._ear, target, dt * 9)
        if self._mood in ("idle", "sleep") and now >= self._next_ear_flick:
            self._next_ear_flick = now + (
                random.uniform(6.0, 14.0) if self._mood == "sleep"
                else random.uniform(4.0, 9.0)
            )
            self._ear_flick_start = now

    def _update_blink(self, now: float):
        if self._mood in ("sleep", "pet", "orient", "raise", "tap"):
            return
        if now >= self._next_blink:
            self._next_blink = now + random.uniform(2.2, 6.5)
            self._blink_start = now

    def _blink_amount(self, now: float) -> float:
        if self._blink_start is None:
            return 0.0
        elapsed = now - self._blink_start
        if elapsed < 0.075:
            value = elapsed / 0.075
        elif elapsed < 0.12:
            value = 1.0
        elif elapsed < 0.2:
            value = 1.0 - (elapsed - 0.12) / 0.08
        else:
            self._blink_start = None
            value = 0.0
        return _clamp(value, 0.0, 1.0)

    # -------------------------------------------------------------- particles

    def _update_particles(self, now: float, dt: float):
        alive = []
        for pt in self._particles:
            pt.life -= dt
            if pt.life <= 0:
                continue
            pt.x += pt.vx * dt
            pt.y += pt.vy * dt
            if pt.kind == "dust":
                pt.vx *= 0.93
                pt.vy += 26 * dt
            elif pt.kind == "heart":
                pt.vy *= 0.995
                pt.vx += math.sin(now * 6 + pt.y) * 6 * dt
            elif pt.kind == "zzz":
                pt.vx += math.sin(now * 2 + pt.y) * 8 * dt
            elif pt.kind == "spark":
                pt.vx *= 0.9
                pt.vy *= 0.9
            alive.append(pt)
        self._particles = alive[:60]

    def _update_particle_spawners(self, now: float):
        if self._mood == "run" and now >= self._next_dust:
            self._next_dust = now + 0.065
            self._spawn_dust(2)
        if (self._mood == "drop" and self._paste_busy and not self._paste_landed
                and now - self._mood_since >= 0.32):
            self._paste_landed = True
            self._spawn_dust(5)
        if self._mood in ("pet", "greet") and now >= self._next_heart:
            self._next_heart = now + (0.5 if self._mood == "greet" else
                                      0.68 if self._gesture == "hold" else 0.42)
            self._spawn_heart()
        if self._mood == "sleep" and self._sleep_zzz and now >= self._next_zzz:
            self._next_zzz = now + 3.6
            self._particles.append(Particle(
                "zzz", CX + 58 + random.uniform(-5, 5), 46,
                random.uniform(1.5, 5), -13, 2.3, 2.3, random.uniform(7, 9),
            ))

    def _spawn_spark(self):
        target = self._tap_paw or QPointF(TAP_PAW_X, TAP_PAW_Y)
        for _ in range(7):
            angle = random.uniform(0, _TAU)
            speed = random.uniform(18, 64)
            self._particles.append(Particle(
                "spark",
                target.x() + random.uniform(-3, 3),
                target.y() + random.uniform(-3, 3),
                math.cos(angle) * speed,
                math.sin(angle) * speed,
                0.34, 0.34,
                random.uniform(1.6, 2.8),
            ))

    def _spawn_heart(self):
        self._particles.append(Particle(
            "heart",
            CX + 40 + random.uniform(-22, 22),
            86 + random.uniform(-12, 8),
            random.uniform(-10, 10),
            random.uniform(-46, -30),
            1.2, 1.2,
            random.uniform(4.0, 7.0),
        ))

    def _spawn_dust(self, count: int):
        for _ in range(count):
            self._particles.append(Particle(
                "dust",
                CX + random.uniform(-40, 40),
                GROUND - random.uniform(0, 6),
                -self._dir * random.uniform(14, 46),
                random.uniform(-22, -6),
                0.5, 0.5,
                random.uniform(3.0, 6.0),
            ))

    # --------------------------------------------------------------- rendering

    def _music_visual(self, v: Visual, now: float, mood: str):
        style = _MUSIC_STYLE_FOR_MOOD.get(mood, "groovy")
        phase = self._beat_phase
        wave = math.sin(phase * _TAU)
        counter = math.sin(phase * _TAU + math.pi / 2)
        scale = self._music_intensity_now()
        pulse = _clamp(self._beat_pulse, 0.0, 1.0)
        v.eye_open = 1.0
        v.blush = 0.12 + 0.10 * scale

        if style == "minimal":
            v.head_tilt = 1.8 * wave
            v.bob += 0.7 + 0.8 * counter
            v.tail = "sway"
        elif style == "chill":
            v.eye_mode = "half"
            v.eye_open = 0.55
            v.head_tilt = 2.8 * wave + 1.6
            v.lean = 0.9 * counter
            v.bob += 1.9 + 0.7 * counter
            v.drop = 2.0
            tap = (self._beat_count % 4 == 0 and pulse > 0.35)
            v.paw_l = QPointF(0, 2)
            v.paw_r = QPointF(0, 2 - (4.5 * scale if tap else 0.0))
            v.tail = "wrap"
            v.blush = 0.08
        elif style == "hype":
            v.head_tilt = 6.5 * wave
            v.lean = 3.6 * counter
            v.bob += 1.8 + 3.2 * abs(wave) * (0.5 + 0.5 * scale)
            v.paw_l = QPointF(0, -11.0 * max(0.0, wave) * scale)
            v.paw_r = QPointF(0, -11.0 * max(0.0, -wave) * scale)
            v.tail = "up"
            v.eye_open = 1.12
            squash = 0.035 * pulse
            v.sx *= 1.0 + squash
            v.sy *= 1.0 - squash
        else:  # groovy
            v.head_tilt = 4.4 * wave
            v.lean = 2.4 * counter
            v.bob += 1.2 + 1.7 * abs(wave) * (0.45 + 0.55 * scale)
            v.paw_l = QPointF(0, -7.0 * max(0.0, wave) * scale)
            v.paw_r = QPointF(0, -7.0 * max(0.0, -wave) * scale)
            v.tail = "up"
            v.eye_open = 1.03
            squash = 0.02 * pulse
            v.sx *= 1.0 + squash
            v.sy *= 1.0 - squash

        if self._music_jump_start is not None:
            u = (now - self._music_jump_start) / 0.55
            if 0.0 <= u < 1.0:
                v.bob -= (26.0 + 8.0 * scale) * math.sin(math.pi * u)
                if u < 0.15:
                    v.sx *= 1.0 + 0.03 * (u / 0.15)
                    v.sy *= 1.0 - 0.03 * (u / 0.15)
            elif u >= 1.0:
                self._music_jump_start = None

    def _visual(self, now: float) -> Visual:
        v = Visual()
        mood = self._mood
        t = now - self._mood_since
        look = self._look
        v.look = look
        v.perp = self._ear
        v.bob = math.sin(now * 2.0) * 1.2

        if mood == "idle":
            v.head_tilt = look.x() * 2.0
        elif mood in NEW_MUSIC_MOODS:
            apply_music_dance(
                v, mood, now=now, phase=self._beat_phase,
                pulse=self._beat_pulse, scale=self._music_intensity_now(),
                beat_count=self._beat_count, jump_start=self._music_jump_start,
            )
        elif mood in MUSIC_MOODS:
            self._music_visual(v, now, mood)
        elif mood == "curious":
            v.bob += math.sin(now * 3.1) * 0.9
            v.head_tilt = look.x() * 5.0 + math.sin(now * 1.5) * 0.8
            v.blush = 0.18
            v.tail = "up"
        elif mood == "pet":
            holding = self._gesture == "hold"
            v.eye_mode = "happy"
            side = self._pet_side
            v.head_tilt = side * (4.5 + math.sin(now * 7.0) * 1.2)
            v.lean = side * 1.2
            v.jitter = math.sin(now * (30.0 if holding else 38.0)) * (0.55 if holding else 0.8)
            v.blush = 1.0
            v.purr = 1.0 if holding else 0.55
            v.tail = "up"
            knead = math.sin(now * 9.0) * 1.6
            v.paw_l = QPointF(-2, -6 + knead)
            v.paw_r = QPointF(2, -6 - knead)
        elif mood == "greet":
            wave = math.sin(now * 7.6)
            v.eye_mode = "happy"
            v.head_tilt = 4.0 + wave * 1.8
            v.lean = 1.4
            v.bob += math.sin(now * 5.0) * 1.5
            v.blush = 0.85
            v.paw_l = QPointF(-2, -1)
            v.paw_r = QPointF(5 + wave * 1.6, -22 - 4.5 * wave)
            v.tail = "sway"
        elif mood == "proud":
            # Exactly three left-right swings so the head lands level.
            beat = t * _TAU * 3.0 / (BRAG_SHAKE_MS / 1000.0)
            v.eye_mode = "happy"
            v.head_tilt = 10.0 * math.sin(beat)
            v.lean = 1.2 * math.sin(beat + math.pi)
            v.bob += math.sin(now * 9.0) * 1.1
            v.blush = 0.9
            v.paw_l = QPointF(-1, -5)
            v.paw_r = QPointF(1, -5)
            v.tail = "up"
        elif mood == "walk":
            v.eye_open = 1.05
            v.head_tilt = _clamp(self._velocity.x() * 0.16, -6, 6)
            v.lean = _clamp(self._velocity.x() * 0.16, -4.5, 4.5)
            v.bob = math.sin(now * 15.0) * 1.8
            v.paw_l = QPointF(0, -max(0.0, math.sin(now * 13.0)) * 6)
            v.paw_r = QPointF(0, -max(0.0, math.sin(now * 13.0 + math.pi)) * 6)
            v.tail = "sway"
        elif mood == "pickup":
            k = _smooth(_clamp(t / 0.45, 0.0, 1.0))
            v.eye_open = 1.04
            v.head_tilt = 4.0 * k
            v.drop = 4.0 * k
            v.paw_l = QPointF(-1, -3)
            v.paw_r = QPointF(1, -3)
            v.tail = "up"
            v.carry = k
        elif mood == "carry_walk":
            v.eye_open = 1.04
            v.head_tilt = _clamp(self._velocity.x() * 0.12, -5, 5) - 3.0
            v.lean = _clamp(self._velocity.x() * 0.16, -4.5, 4.5)
            v.bob = math.sin(now * 15.0) * 1.8
            v.paw_l = QPointF(0, -max(0.0, math.sin(now * 13.0)) * 6)
            v.paw_r = QPointF(0, -max(0.0, math.sin(now * 13.0 + math.pi)) * 6)
            v.tail = "up"
            v.drop = 2.0
            v.carry = 1.0
        elif mood == "drop":
            k = _clamp(t / 0.42, 0.0, 1.0)
            fall = _smooth(_clamp(k / 0.75, 0.0, 1.0))
            v.eye_open = 1.04
            v.head_tilt = 3.0 - 5.0 * k
            v.drop = 3.5 * math.sin(math.pi * k)
            v.bob += 1.2 * math.sin(math.pi * k)
            v.paw_l = QPointF(-1, -2)
            v.paw_r = QPointF(1, -2)
            v.tail = "up"
            v.blush = 0.35 * k
            v.carry = 1.0 - _clamp((k - 0.82) / 0.18, 0.0, 1.0)
            v.carry_y = 46.0 * fall
        elif mood == "sit":
            k = math.exp(-t * 7.0) * math.sin(t * 22.0)
            v.sx = 1.0 + 0.10 * k
            v.sy = 1.0 - 0.10 * k
            v.bob += 1.2
            v.head_tilt = 2.0 + look.x() * 2.0
            v.paw_l = QPointF(-1, 1)
            v.paw_r = QPointF(1, 1)
            v.tail = "wrap"
        elif mood == "land":
            k = math.exp(-t * 6.0) * math.sin(t * 24.0)
            v.sx = 1.0 + 0.14 * k
            v.sy = 1.0 - 0.14 * k
            v.eye_open = 1.05
        elif mood == "run":
            v.lean = 3.5 * self._dir
            v.bob = math.sin(now * 24.0) * 2.4
            v.paw_l = QPointF(0, -max(0.0, math.sin(now * 22.0)) * 8)
            v.paw_r = QPointF(0, -max(0.0, math.sin(now * 22.0 + math.pi)) * 8)
            v.eye_open = 0.95
            v.tail = "stream"
        elif mood == "chase":
            v.lean = 4.0 * self._dir
            v.bob = math.sin(now * 26.0) * 2.6
            v.paw_l = QPointF(0, -max(0.0, math.sin(now * 24.0)) * 9)
            v.paw_r = QPointF(0, -max(0.0, math.sin(now * 24.0 + math.pi)) * 9)
            v.eye_open = 1.1
            v.tail = "stream"
        elif mood == "leap":
            v.sx, v.sy = 0.96, 1.07
            v.lean = 5.0 * self._dir
            v.paw_l = QPointF(0, -9)
            v.paw_r = QPointF(0, -9)
            v.eye_open = 1.12
            v.tail = "stream"
        elif mood == "climb":
            v.sx, v.sy = 0.94, 1.08
            v.lean = 2.0
            v.head_tilt = 4.0
            v.paw_l = QPointF(0, -max(0.0, math.sin(now * 18.0)) * 7)
            v.paw_r = QPointF(0, -max(0.0, math.sin(now * 18.0 + math.pi)) * 7)
            v.tail = "stream"
        elif mood == "alert":
            v.eye_open = 1.14
            v.bob += math.sin(now * 30.0) * 0.7
            v.tail = "up"
        elif mood == "pounce":
            k = math.sin(_clamp(t / 0.55, 0.0, 1.0) * math.pi)
            v.swat = k
            v.eye_open = 1.05
            v.lean = -4.0 * self._dir
            v.paw_r = QPointF(7 * self._dir, -22 - 8 * k)
            v.tail = "up"
            v.bob -= 3.0 * k
        elif mood == "orient":
            v.eye_open = 1.12
            v.head_tilt = 5.0
            v.lean = 2.0
            v.tail = "up"
            v.bob += math.sin(now * 6.0) * 0.4
        elif mood == "raise":
            k = _ease_out(_clamp(t / 0.24, 0.0, 1.0))
            v.reach = 0.85 * k
            v.paw_target = self._tap_paw or QPointF(TAP_PAW_X, TAP_PAW_Y)
            v.sx, v.sy = 0.96 - 0.02 * k, 1.04 + 0.04 * k
            v.head_tilt = 5.0
            v.eye_open = 1.12
            v.tail = "up"
        elif mood == "tap":
            u = _clamp(t / 0.30, 0.0, 1.0)
            if u < 0.30:
                pull = math.sin(math.pi * (u / 0.30))
                reach = 0.85 - 0.24 * pull
                v.bob += 1.4 * pull
                v.lean = -1.2
            elif u < 0.72:
                reach = 0.61 + 0.39 * _ease_out((u - 0.30) / 0.42)
                v.lean = 2.6
                v.head_tilt = 6.5
            else:
                reach = 1.0 - 0.16 * _clamp((u - 0.72) / 0.28, 0.0, 1.0)
                v.lean = 2.0
                v.head_tilt = 5.0
            v.reach = reach
            v.paw_target = self._tap_paw or QPointF(TAP_PAW_X, TAP_PAW_Y)
            v.eye_open = 1.14
            v.tail = "up"
            if 0.62 <= u <= 0.80:
                v.flash = 1.0 - abs(u - 0.68) / 0.12
                v.sx, v.sy = 1.035, 0.972
                v.lean = 3.0
        elif mood == "react":
            k = _ease_out(_clamp(t / 0.40, 0.0, 1.0))
            v.reach = 0.84 * (1.0 - k)
            v.paw_target = self._tap_paw or QPointF(TAP_PAW_X, TAP_PAW_Y)
            squash = math.exp(-t * 8.0) * math.sin(t * 26.0)
            v.sx = 1.0 + 0.09 * squash
            v.sy = 1.0 - 0.09 * squash
            v.head_tilt = 3.0
            v.eye_mode = "happy"
            v.blush = 0.55
            v.flash = max(0.0, 1.0 - t / 0.28)
            v.tail = "up"
        elif mood == "smug":
            if self._close_failed:
                v.eye_mode = "half"
                v.head_tilt = -6.0
                v.question = 1.0
                v.tail = "sway"
            else:
                v.eye_mode = "half"
                v.head_tilt = -4.0
                v.paw_r = QPointF(0, -13)
                v.jitter = math.sin(now * 2.2) * 0.3
        elif mood == "confused":
            k = _clamp(t / 0.3, 0.0, 1.0)
            v.eye_mode = "half"
            v.head_tilt = -7.0
            v.question = math.sin(math.pi * k) if t < 0.3 else 1.0
            v.tail = "sway"
            v.bob += math.sin(now * 3.0) * 0.5
        elif mood == "spin":
            u = _clamp(t / 0.72, 0.0, 1.0)
            v.spin = 360.0 * _smooth(_clamp(t / 0.58, 0.0, 1.0))
            if t < 0.58:
                v.bob -= 23.0 * math.sin(math.pi * _clamp(t / 0.58, 0.0, 1.0))
            else:
                k = math.exp(-(t - 0.58) * 9.0) * math.sin((t - 0.58) * 26.0)
                v.sx = 1.0 + 0.12 * k
                v.sy = 1.0 - 0.12 * k
            v.eye_mode = "happy"
            v.blush = 0.8
            v.paw_l = QPointF(-1, -7)
            v.paw_r = QPointF(1, -7)
            v.tail = "up"
            if u >= 1.0:
                v.spin = 360.0
        elif mood == "flick":
            k = _clamp(t / 0.58, 0.0, 1.0)
            v.bob -= 30.0 * math.sin(math.pi * k)
            if k < 0.12:
                v.sx, v.sy = 1.05, 0.94
            elif k > 0.86:
                v.sx, v.sy = 1.06, 0.95
            else:
                v.sx, v.sy = 0.97, 1.05
            v.paw_l = QPointF(0, -10)
            v.paw_r = QPointF(0, -10)
            v.eye_open = 1.15
            v.tail = "stream"
        elif mood == "sleep":
            pose = _clamp(self._sleep_pose, 0.0, 1.0)
            breath = math.sin(now * 1.15)
            v.bob = (4.6 + breath * 1.3) * pose + math.sin(now * 2.0) * 1.2 * (1.0 - pose)
            v.sy = 1.0 + breath * 0.013 * pose
            v.sx = 1.0 - breath * 0.006 * pose
            v.head_tilt = 6.0 * pose + look.x() * 2.0 * (1.0 - pose)
            v.paw_l = QPointF(0, 2 * pose)
            v.paw_r = QPointF(0, 2 * pose)
            v.blush = 0.08 * pose
            v.tail = "wrap" if pose > 0.45 else "sway"
            v.drop = (6.0 + breath * 0.8) * pose
            if self._sleep_settle_start is not None:
                elapsed = now - self._sleep_settle_start
                if elapsed < 1.0:
                    settle = math.sin(math.pi * elapsed)
                    v.head_tilt += 1.5 * settle
                    v.bob += 0.6 * settle
                    v.drop += 0.8 * settle
                else:
                    self._sleep_settle_start = None
        elif mood == "wake":
            k = _clamp(t / 0.9, 0.0, 1.0)
            v.yawn = math.sin(k * math.pi)
            v.head_tilt = -3.0
            v.drop = 5.0 * (1.0 - _smooth(k))
            v.paw_l = QPointF(-3, -2)
            v.paw_r = QPointF(3, -2)
            v.tail = "up"

        blink = self._blink_amount(now)
        if blink > 0 and v.eye_mode == "open":
            v.eye_open *= 1.0 - blink
            if blink > 0.62:
                v.eye_mode = "closed"

        if self._eye_close > 0.01:
            v.eye_open *= max(0.0, 1.0 - self._eye_close)
            if self._eye_close > 0.7:
                v.eye_mode = "closed"

        if self._hop_start is not None:
            elapsed = now - self._hop_start
            if elapsed < 0.36:
                v.bob -= 7.0 * math.sin(math.pi * elapsed / 0.36)
            else:
                self._hop_start = None

        if mood == "curious" and self._hovered:
            v.tail = "up"
        return v

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        now = time.monotonic()
        v = self._visual(now)
        mirrored = self._dir < 0 and v.reach <= 0.02

        self._draw_shadow(p, v)
        p.save()
        p.translate(0, v.bob)
        p.translate(CX, GROUND)
        p.scale(v.sx, v.sy)
        p.translate(-CX, -GROUND)
        p.translate(CX, GROUND)
        p.rotate(v.lean + v.head_tilt * 0.18)
        p.translate(-CX, -GROUND)
        if v.spin:
            p.translate(CX, 112)
            p.rotate(v.spin)
            p.translate(-CX, -112)
        p.translate(v.jitter, 0)
        if mirrored:
            p.translate(W, 0)
            p.scale(-1, 1)

        self._draw_motion(p, v)
        if v.reach > 0.02:
            self._draw_reach_arm(p, v)
        draw_cat_base(p, self._pal, self._tail_angle(v, now), self._leg_poses(v))
        self._draw_face(p, v, now)
        if v.reach > 0.02:
            self._draw_reach_paw(p, v)
        if v.purr > 0:
            self._draw_purr(p, v, now)
        p.restore()

        if v.carry > 0.01:
            self._draw_carried(p, v, mirrored, v.bob)
        self._draw_particles(p, now)
        if v.question > 0:
            self._draw_question(p, v)
        self._draw_bubble(p, now)
        p.end()

    # ------------------------------------------------------------------ shapes

    def _draw_shadow(self, p: QPainter, v: Visual):
        alpha = int(64 * (1.0 - _clamp(v.bob / 14.0, 0.0, 0.45)))
        width = 148.0 * _clamp(v.sx, 0.7, 1.3)
        grad = QRadialGradient(QPointF(CX, GROUND - 2), width * 0.56)
        grad.setColorAt(0.0, QColor(0, 0, 0, alpha))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QRectF(CX - width / 2, GROUND - 10, width, 16))

    def _draw_motion(self, p: QPainter, v: Visual):
        if self._mood not in ("run", "leap"):
            return
        alpha = 90 if self._mood == "run" else 55
        p.setPen(QPen(QColor(186, 204, 214, alpha), 2.0,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        base_x = 9 if self._dir > 0 else W - 9
        for index, y in enumerate((124, 140, 156)):
            length = 9 + (index % 2) * 6
            p.drawLine(QPointF(base_x, y), QPointF(base_x - self._dir * length, y))

    def _tail_angle(self, v: Visual, now: float) -> float:
        sway = math.sin(now * 1.7) * 4.0 + math.sin(now * 2.9) * 1.2
        if self._mood in MUSIC_MOODS:
            style = self._music_effective_style()
            amplitude = 9.0 if style == "hype" else 6.0 if style == "groovy" else 3.5
            sway = (math.sin(self._beat_phase * _TAU) * amplitude
                    * (0.45 + 0.55 * self._music_intensity_now())
                    + math.sin(now * 1.9) * 1.2)
        if self._mood == "sleep":
            sway *= 0.35
            if self._tail_flick_start is not None:
                elapsed = now - self._tail_flick_start
                if elapsed < 0.7:
                    sway += math.sin(math.pi * elapsed / 0.7) * 5.0
                else:
                    self._tail_flick_start = None
        base = {"up": 22.0, "stream": 32.0, "wrap": -20.0, "hang": -34.0}.get(v.tail, 0.0)
        return base + _clamp(sway, -11.0, 11.0)

    @staticmethod
    def _leg_poses(v: Visual) -> dict[str, QPointF]:
        left, right = v.paw_l, v.paw_r
        return {
            "a": QPointF(left.x() * 0.9, left.y()),
            "b": QPointF(left.x() * 0.55, left.y() * 0.65),
            "c": QPointF(right.x() * 0.18, right.y() * 0.18),
            "d": QPointF(right.x(), right.y()),
        }

    @staticmethod
    def _reach_paw(v: Visual) -> QPointF:
        shoulder = QPointF(126.0, 142.0)
        target = v.paw_target
        k = _clamp(v.reach, 0.0, 1.0)
        return QPointF(
            shoulder.x() + (target.x() - shoulder.x()) * k,
            shoulder.y() + (target.y() - shoulder.y()) * k,
        )

    def _reach_elbow(self, v: Visual) -> QPointF:
        paw = self._reach_paw(v)
        return QPointF(
            _clamp(paw.x() + 26.0, 150.0, 166.0),
            _clamp(min(118.0, paw.y() + 74.0), 92.0, 122.0),
        )

    def _draw_reach_arm(self, p: QPainter, v: Visual):
        pal = self._pal
        elbow = self._reach_elbow(v)
        paw = self._reach_paw(v)
        arm = QPainterPath(elbow)
        arm.lineTo(paw)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(pal.edge, 15.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(arm)
        p.setPen(QPen(pal.base, 13.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(arm)

    def _draw_reach_paw(self, p: QPainter, v: Visual):
        pal = self._pal
        paw = self._reach_paw(v)
        elbow = self._reach_elbow(v)
        angle = math.degrees(math.atan2(paw.y() - elbow.y(), paw.x() - elbow.x()))
        p.save()
        p.translate(paw)
        p.rotate(angle + 90.0)
        p.setBrush(pal.base)
        p.setPen(QPen(pal.soft_edge, 0.9))
        p.drawRoundedRect(QRectF(-8.5, -6.0, 17.0, 12.0), 5.6, 5.6)
        p.restore()

        if v.flash > 0:
            p.setPen(QPen(QColor(pal.spark), 2.0 + 1.4 * v.flash,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for angle, length in ((-150, 9.0), (-105, 12.0), (-60, 9.0), (-15, 7.0)):
                p.save()
                p.translate(paw)
                p.rotate(angle)
                p.drawLine(QPointF(0, -10), QPointF(0, -10 - length * v.flash))
                p.restore()

        if v.swat > 0:
            p.setPen(QPen(QColor(pal.spark), 2.3, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            for angle, length in ((-58, 9.0), (-24, 13.0), (10, 10.0)):
                p.save()
                p.translate(paw + QPointF(4.0, 2.0))
                p.rotate(angle)
                p.drawLine(QPointF(0, 0), QPointF(0, -length))
                p.restore()

    def _draw_purr(self, p: QPainter, v: Visual, now: float):
        phase = (now * 0.9) % 1.0
        alpha = int(48 * v.purr * (1.0 - phase * 0.7))
        if alpha <= 0:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, alpha), 1.3,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for side in (-1, 1):
            for i in range(2):
                x = CX + side * (72 - i * 5 + phase * 3)
                y = 124 - i * 7
                p.drawArc(QRectF(x - 5, y - 5, 10, 10),
                          (20 if side > 0 else 160) * 16, 120 * 16)

    def _draw_question(self, p: QPainter, v: Visual):
        alpha = int(220 * _clamp(v.question, 0.0, 1.0))
        font = QFont("Segoe UI", 15)
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)
        p.setPen(QColor(255, 204, 120, alpha))
        p.drawText(QRectF(136, 24, 30, 30), Qt.AlignmentFlag.AlignCenter, "?")

    def _draw_face(self, p: QPainter, v: Visual, now: float):
        pal = self._pal
        p.save()
        p.translate(FACE_CENTER)
        p.rotate(v.head_tilt)
        p.translate(-FACE_CENTER)

        lx = v.look.x() * 1.6
        ly = v.look.y() * 1.1
        eye_open = _clamp(v.eye_open, 0.0, 1.2)

        for base in (EYE_LEFT, EYE_RIGHT):
            ex = base.x() + lx
            ey = base.y() + ly
            if v.eye_mode in ("closed", "happy"):
                width, height = (8.6, 6.4) if v.eye_mode == "happy" else (7.6, 5.4)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(pal.ink, 1.7, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap))
                p.drawArc(QRectF(ex - width / 2, ey - height / 2 + 0.6, width, height),
                          20 * 16, 140 * 16)
                continue

            if v.eye_mode == "half":
                rect = QRectF(ex - EYE_W / 2, ey - 1.1, EYE_W, 2.9)
                p.setPen(QPen(pal.ink, 1.1, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(ex - 3.4, ey - 1.5), QPointF(ex + 3.4, ey - 1.5))
            else:
                height = max(1.1, EYE_H * eye_open)
                rect = QRectF(ex - EYE_W / 2, ey - height / 2, EYE_W, height)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(pal.ink)
            p.drawEllipse(rect)

        if v.blush > 0.02:
            blush = QColor(pal.blush)
            blush.setAlpha(int(58 * _clamp(v.blush, 0.0, 1.0)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(blush)
            p.drawEllipse(QRectF(CHEEK_LEFT.x() - 5 + lx, CHEEK_LEFT.y() - 2.4 + ly, 10, 4.8))
            p.drawEllipse(QRectF(CHEEK_RIGHT.x() - 4 + lx, CHEEK_RIGHT.y() - 2.4 + ly, 10, 4.8))

        mouth = QPointF(MOUTH_CENTER.x() + lx * 0.8, MOUTH_CENTER.y() + ly * 0.8)
        if v.yawn > 0.12:
            size = 5.6
            rect = QRectF(mouth.x() - size / 2, mouth.y() - 0.6, size,
                          size * 0.8 + 2.2 * v.yawn)
            p.setBrush(QColor(90, 44, 52))
            p.setPen(QPen(QColor(60, 30, 36), 0.8))
            p.drawEllipse(rect)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(PINK)
            p.drawEllipse(QRectF(rect.center().x() - 1.6, rect.bottom() - 2.4, 3.2, 1.8))
        else:
            draw_mouth(p, pal, mouth, 1.0, 1.25)
        p.restore()

    def _draw_carried(self, p: QPainter, v: Visual, mirrored: bool, bob: float):
        alpha = _clamp(v.carry, 0.0, 1.0)
        if alpha <= 0.02:
            return
        size = 26.0 * (0.86 + 0.14 * alpha)
        cx = (W - MOUTH_ITEM_X) if mirrored else MOUTH_ITEM_X
        cy = MOUTH_ITEM_Y + v.carry_y + bob
        rect = QRectF(cx - size / 2, cy - size / 2, size, size)
        p.save()
        p.setOpacity(alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QRectF(rect.left() + 2, rect.bottom() - 3, size * 0.82, 5))
        icon = self._paste_icon
        if icon is not None and not icon.isNull():
            p.drawPixmap(rect, icon, QRectF(icon.rect()))
        else:
            self._draw_plain_icon(p, rect, self._paste_kind)
        if self._paste_count > 1:
            badge = QRectF(rect.right() - 10, rect.bottom() - 10, 15, 15)
            p.setBrush(QColor(47, 127, 112))
            p.setPen(QPen(QColor(15, 20, 25), 1.0))
            p.drawEllipse(badge)
            font = QFont("Segoe UI", 7)
            font.setWeight(QFont.Weight.Bold)
            p.setFont(font)
            p.setPen(QColor(255, 255, 255))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(self._paste_count))
        p.restore()

    def _draw_plain_icon(self, p: QPainter, rect: QRectF, kind: str):
        if kind == "folder":
            body = QRectF(rect.left() + 1, rect.top() + 8, rect.width() - 2, rect.height() - 9)
            tab = QRectF(rect.left() + 2, rect.top() + 4, rect.width() * 0.46, 7)
            p.setBrush(QColor("#E8B34B"))
            p.setPen(QPen(QColor(138, 98, 30), 1.0))
            p.drawRoundedRect(tab, 2, 2)
            p.drawRoundedRect(body, 3, 3)
            p.setPen(QPen(QColor(255, 236, 190, 120), 1.0))
            p.drawLine(QPointF(body.left() + 3, body.top() + 5),
                       QPointF(body.right() - 3, body.top() + 5))
            return
        if kind == "image":
            frame = QRectF(rect.left() + 3, rect.top() + 4,
                           rect.width() - 6, rect.height() - 8)
            p.setBrush(QColor("#F4F7FA"))
            p.setPen(QPen(QColor(120, 136, 148), 1.0))
            p.drawRoundedRect(frame, 2.5, 2.5)
            p.setBrush(QColor("#8CC8E8"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(frame.left() + frame.width() * 0.68,
                                  frame.top() + frame.height() * 0.30), 3.0, 3.0)
            p.setBrush(QColor("#7FBF9A"))
            p.drawPolygon(QPolygonF([
                QPointF(frame.left() + 3, frame.bottom() - 3),
                QPointF(frame.left() + frame.width() * 0.42,
                        frame.top() + frame.height() * 0.46),
                QPointF(frame.right() - 3, frame.bottom() - 3),
            ]))
            return
        page = QRectF(rect.left() + 4, rect.top() + 2, rect.width() - 8, rect.height() - 4)
        p.setBrush(QColor("#F4F7FA"))
        p.setPen(QPen(QColor(140, 154, 166), 1.0))
        p.drawRoundedRect(page, 2.5, 2.5)
        p.setBrush(QColor("#CBD6DE"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([
            QPointF(page.right() - 8, page.top()),
            QPointF(page.right(), page.top() + 8),
            QPointF(page.right() - 8, page.top() + 8),
        ]))
        p.setPen(QPen(QColor(150, 164, 176), 1.2,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for offset in (13, 17, 21):
            p.drawLine(QPointF(page.left() + 4, page.top() + offset),
                       QPointF(page.right() - 5, page.top() + offset))

    def _draw_particles(self, p: QPainter, now: float):
        p.setPen(Qt.PenStyle.NoPen)
        for pt in self._particles:
            amount = _clamp(pt.life / pt.max_life, 0.0, 1.0)
            alpha = int(235 * min(1.0, amount * 3.0))
            if pt.kind == "heart":
                p.setBrush(QColor(226, 108, 124, alpha))
                p.drawPath(_heart_path(pt.x, pt.y, pt.size))
            elif pt.kind == "dust":
                p.setBrush(QColor(200, 210, 220, int(alpha * 0.55)))
                p.drawEllipse(QRectF(pt.x - pt.size / 2, pt.y - pt.size / 2, pt.size, pt.size))
            elif pt.kind == "zzz":
                font = QFont("Segoe UI", int(pt.size))
                font.setWeight(QFont.Weight.Bold)
                p.setFont(font)
                zzz = QColor(self._pal.zzz)
                zzz.setAlpha(int(alpha * 0.72))
                p.setPen(zzz)
                p.drawText(QRectF(pt.x - 10, pt.y - 12, 24, 24),
                           Qt.AlignmentFlag.AlignCenter, "z")
                p.setPen(Qt.PenStyle.NoPen)
            elif pt.kind == "spark":
                spark = QColor(self._pal.spark)
                spark.setAlpha(alpha)
                p.setBrush(spark)
                p.drawPath(_spark_path(pt.x, pt.y, pt.size))

    def _draw_bubble(self, p: QPainter, now: float):
        if not self._bubble_text:
            return
        remaining = self._bubble_until - now
        if remaining <= 0:
            self._bubble_text = ""
            return
        alpha = 255 if remaining > 0.4 else int(255 * remaining / 0.4)

        font = QFont("Segoe UI", 9)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        metrics = QFontMetrics(font)
        text = metrics.elidedText(self._bubble_text, Qt.TextElideMode.ElideRight, W - 30)
        width = metrics.horizontalAdvance(text) + 20
        height = 23.0
        x = (W - width) / 2
        y = 4.0

        bubble = QPainterPath()
        bubble.addRoundedRect(QRectF(x, y, width, height), 10, 10)
        tail = QPolygonF([QPointF(CX - 5, y + height - 0.5),
                          QPointF(CX + 5, y + height - 0.5),
                          QPointF(CX, y + height + 6)])
        bubble.addPolygon(tail)

        p.setPen(QPen(QColor(86, 104, 116, alpha), 1.1))
        p.setBrush(QColor(18, 24, 30, int(alpha * 0.94)))
        p.drawPath(bubble)

        p.setPen(QColor(233, 243, 237, alpha))
        p.drawText(QRectF(x, y, width, height), Qt.AlignmentFlag.AlignCenter, text)


def cat_icon(size: int = 64, color: str | None = None) -> QIcon:
    pal = FurPalette(color or DEFAULT_CAT_COLOR)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / 176.0
    p.translate((size - 176 * scale) / 2, (size - 182 * scale) / 2 - 20 * scale)
    p.scale(scale, scale)
    draw_cat_base(p, pal)
    draw_idle_face(p, pal)
    p.end()
    return QIcon(pm)
