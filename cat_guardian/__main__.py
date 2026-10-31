from __future__ import annotations

import logging
import sys
from urllib.parse import urlparse

from PySide6.QtCore import QFileInfo, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileIconProvider,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)

from cat_guardian import native
from cat_guardian.always_music import AlwaysMusicWatcher
from cat_guardian.appearance import (
    DEFAULT_CAT_COLOR, HEX_RE, PRESETS, CatPreview, color_name, normalize_color,
)
from cat_guardian.apps import discover_apps
from cat_guardian.bridge import BridgeServer
from cat_guardian.music import BeatSound, MusicMonitor
from cat_guardian.paste import ClipboardPasteWatcher
from cat_guardian.pet import CatGuardian, cat_icon
from cat_guardian.settings import (
    APP_NAME, LOG_PATH, load_settings, normalize_apps, normalize_domains, save_settings,
)
from cat_guardian.sounds import MeowSound
from cat_guardian.watcher import AppWatcher

# The window close request goes out as the cat lands its pounce, and an app
# that ignores it is ended two seconds later. The whole sequence stays far
# inside the ten-second close window used for browser tabs. Exits are polled
# so the confirmation lands while the cat is still on the window.
APP_CLOSE_SYNC_MS = 1200
APP_CLOSE_GRACE_MS = 2000
APP_CLOSE_POLL_MS = 200

DARK_STYLE = """
QDialog { background: #141A21; }
QLabel { color: #D6E2EA; }
QLabel#dialogTitle { color: #F2F7FA; font-size: 15px; font-weight: 600; }
QLabel#subtle { color: #8FA2B0; }
QLabel#error { color: #FF9C8F; }
QLabel#coatName { color: #F2F7FA; font-size: 13px; font-weight: 600; }
QLabel#coatHex { color: #8FA2B0; font-family: Consolas, monospace; }
QLineEdit { background: #0D1319; color: #E8F1F7; border: 1px solid #2B3945;
            border-radius: 6px; padding: 6px 8px; selection-background-color: #2F7F70; }
QLineEdit:focus { border-color: #57C9B5; }
QPushButton { background: #1D2730; color: #DCE7EE; border: 1px solid #33424F;
              border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { background: #26333E; }
QPushButton:default { background: #2F7F70; border-color: #3E9C89; color: #F4FFFB; }
QPushButton:default:hover { background: #389381; }
QSpinBox { background: #0D1319; color: #E8F1F7; border: 1px solid #2B3945;
           border-radius: 6px; padding: 4px 6px; }
QCheckBox { color: #D6E2EA; }
"""


def _swatch_style(hex_value: str, selected: bool) -> str:
    light = QColor(hex_value).lightnessF() > 0.62
    text = "#0B1116" if light else "#F7FBFF"
    border = "2px solid #F2F7FA" if selected else "1px solid rgba(255,255,255,60)"
    return (
        f"QPushButton {{ background: {hex_value}; border: {border}; border-radius: 8px;"
        f" color: {text}; font-weight: 700; padding: 0; }}"
    )


class AppearanceDialog(QDialog):
    """Pick the cat's coat without touching the rest of the desktop."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cat appearance")
        self.setMinimumWidth(376)
        self.setStyleSheet(DARK_STYLE)
        self._syncing = False
        self.pending_color = normalize_color(color)
        self.selected_color = self.pending_color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(9)

        title = QLabel("Cat appearance")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        subtle = QLabel("Choose a coat for your desktop cat. Apply updates it right away.")
        subtle.setObjectName("subtle")
        subtle.setWordWrap(True)
        layout.addWidget(subtle)

        self.preview = CatPreview(self.pending_color)
        layout.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)

        self.name_label = QLabel()
        self.name_label.setObjectName("coatName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)
        self.hex_label = QLabel()
        self.hex_label.setObjectName("coatHex")
        self.hex_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hex_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        self._swatches: dict[str, QPushButton] = {}
        for index, (name, hex_value) in enumerate(PRESETS):
            button = QPushButton()
            button.setFixedSize(38, 26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{name} · {hex_value}")
            button.clicked.connect(lambda _=False, value=hex_value: self._pick(value))
            self._swatches[hex_value] = button
            grid.addWidget(button, index // 6, index % 6)
        layout.addLayout(grid)

        custom_row = QHBoxLayout()
        custom_row.setSpacing(8)
        custom_label = QLabel("Custom hex")
        custom_row.addWidget(custom_label)
        self.hex_input = QLineEdit()
        self.hex_input.setMaxLength(7)
        self.hex_input.setPlaceholderText("#66D9EF")
        self.hex_input.textChanged.connect(self._on_text_changed)
        self.hex_input.returnPressed.connect(self._apply)
        custom_row.addWidget(self.hex_input, 1)
        layout.addLayout(custom_row)

        self.error_label = QLabel("")
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        reset = QPushButton("Reset to Default")
        cancel = QPushButton("Cancel")
        apply_button = QPushButton("Apply")
        apply_button.setDefault(True)
        reset.clicked.connect(self._reset)
        cancel.clicked.connect(self.reject)
        apply_button.clicked.connect(self._apply)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(apply_button)
        layout.addLayout(buttons)

        self._refresh(self.pending_color)

    def _refresh(self, color: str, update_input: bool = True):
        self.pending_color = normalize_color(color)
        self.preview.set_color(self.pending_color)
        self.name_label.setText(color_name(self.pending_color))
        self.hex_label.setText(self.pending_color)
        self._syncing = True
        if update_input:
            self.hex_input.setText(self.pending_color)
        self._syncing = False
        for hex_value, button in self._swatches.items():
            selected = hex_value.upper() == self.pending_color
            button.setText("✓" if selected else "")
            button.setStyleSheet(_swatch_style(hex_value, selected))

    def _pick(self, color: str):
        self.error_label.hide()
        self._refresh(color)

    def _on_text_changed(self, text: str):
        if self._syncing:
            return
        candidate = text.strip()
        if candidate and not candidate.startswith("#"):
            candidate = "#" + candidate
        if HEX_RE.match(candidate):
            self.error_label.hide()
            self._refresh(candidate, update_input=False)

    def _typed_color(self) -> str | None:
        candidate = self.hex_input.text().strip()
        if candidate and not candidate.startswith("#"):
            candidate = "#" + candidate
        return candidate.upper() if HEX_RE.match(candidate) else None

    def _reset(self):
        self.error_label.hide()
        self._refresh(DEFAULT_CAT_COLOR)

    def _apply(self):
        color = self._typed_color()
        if color is None:
            self.error_label.setText(
                "Use a six-digit hex color such as #66D9EF (0–9, A–F)."
            )
            self.error_label.show()
            self.hex_input.setFocus()
            return
        self.selected_color = color
        super().accept()


class GestureInfoDialog(QDialog):
    def __init__(self):
        super().__init__(None)
        self.setWindowTitle(f"{APP_NAME} — mouse gestures")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(430)
        self.setStyleSheet(DARK_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        title = QLabel("Mouse gestures")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        intro = QLabel(
            "The cat sleeps on your desktop until you interact with it. "
            "These gestures work whenever the desktop cat is visible."
        )
        intro.setObjectName("subtle")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        gestures = [
            ("Double-click", "An excited jump-and-spin, then a happy landing."),
            ("Press and hold for 0.8 s", "Petting: contented closed eyes, kneading paws, and a soft purr."),
            ("Drag", "The cat follows the cursor with a walking animation; release it to sit down."),
            ("Circle the cursor around the cat", "The cat chases the cursor through one smooth loop and returns."),
            ("Flick upward near the cat", "A small parabolic hop with a gentle landing."),
        ]
        for number, (action, detail) in enumerate(gestures, start=1):
            row = QLabel(
                f"<b style='color:#F2F7FA'>{number}. {action}</b><br>"
                f"<span style='color:#9FB2C0'>{detail}</span>"
            )
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setWordWrap(True)
            row.setContentsMargins(0, 3, 0, 3)
            layout.addWidget(row)

        note = QLabel(
            "A single click wakes the cat without starting a full petting session. "
            "Right-click the cat for its own menu. A blocking visit cannot be interrupted."
        )
        note.setObjectName("subtle")
        note.setWordWrap(True)
        layout.addWidget(note)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)


class SettingsDialog(QDialog):
    preview_requested = Signal(str, int)

    def __init__(self, settings: dict, music_status: str = ""):
        super().__init__(None)
        self.setWindowTitle("Cat Guardian settings")
        self.setMinimumSize(440, 320)
        self.setSizeGripEnabled(True)
        self.settings = dict(settings)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 16, 18, 8)
        layout.setSpacing(8)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)

        intro = QLabel(
            "The cat lives on your desktop. It sleeps when ignored, wakes when you "
            "hover, click, or pet it, and can be dragged anywhere. It only climbs "
            "onto a window when a blocked site or app is in front of you. "
            "Everything stays on this PC."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.enabled = QCheckBox("Enable distraction protection")
        self.enabled.setChecked(bool(settings.get("enabled", True)))
        layout.addWidget(self.enabled)

        self.close_tabs = QCheckBox("Close matching browser tabs")
        self.close_tabs.setChecked(bool(settings.get("close_tabs", True)))
        layout.addWidget(self.close_tabs)

        self.close_apps = QCheckBox("Close matching applications")
        self.close_apps.setChecked(bool(settings.get("close_apps", False)))
        layout.addWidget(self.close_apps)

        sleep_row = QHBoxLayout()
        sleep_row.addWidget(QLabel("Return to sleep after"))
        self.sleep_delay = QSpinBox()
        self.sleep_delay.setRange(3, 120)
        self.sleep_delay.setSuffix(" s")
        self.sleep_delay.setValue(int(round(float(settings.get("sleep_delay", 8.0)))))
        self.sleep_delay.setToolTip("Idle time after the last interaction before the cat dozes off.")
        sleep_row.addWidget(self.sleep_delay)
        sleep_row.addStretch(1)
        layout.addLayout(sleep_row)

        self.sleep_zzz = QCheckBox("Show drifting “z” marks while sleeping")
        self.sleep_zzz.setChecked(bool(settings.get("sleep_zzz", True)))
        layout.addWidget(self.sleep_zzz)

        paste_title = QLabel("Copy-paste deliveries")
        paste_title.setStyleSheet("font-weight: 600; margin-top: 6px;")
        layout.addWidget(paste_title)

        self.paste_mode = QCheckBox(
            "Animate copied files and images when I copy and paste them"
        )
        self.paste_mode.setChecked(bool(settings.get("paste_mode", False)))
        layout.addWidget(self.paste_mode)

        paste_hint = QLabel(
            "While this is on, the cat grabs the copied or cut file (or image) "
            "when the clipboard changes, then carries it to the point where you "
            "press Ctrl+V and drops it. Explorer, the desktop, and other app "
            "windows are animated; text, emoji, and rich-text clips are ignored. "
            "Each copy action plays once, no matter how many files it holds. "
            "The three helpers below can be switched off independently. The "
            "Ctrl+V check runs locally, stores nothing, and is removed when the "
            "mode is off. Right-click Paste and Shift+Insert are not seen."
        )
        paste_hint.setWordWrap(True)
        paste_hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(paste_hint)

        self.paste_pickup = QCheckBox("Walk over and grab items when I copy or cut")
        self.paste_pickup.setChecked(bool(settings.get("paste_pickup", True)))
        self.paste_pickup.setToolTip(
            "Off: the cat stays where it is when the clipboard changes. "
            "The drop animation on Ctrl+V still works."
        )
        self.paste_other_apps = QCheckBox("Also drop inside other application windows")
        self.paste_other_apps.setChecked(bool(settings.get("paste_other_apps", True)))
        self.paste_other_apps.setToolTip(
            "Off: only File Explorer and the desktop animate a paste."
        )
        self.paste_images = QCheckBox("Include copied images")
        self.paste_images.setChecked(bool(settings.get("paste_images", True)))
        self.paste_images.setToolTip(
            "Off: copied images are ignored. Files and folders still animate."
        )
        paste_options = QVBoxLayout()
        paste_options.setContentsMargins(22, 0, 0, 0)
        paste_options.addWidget(self.paste_pickup)
        paste_options.addWidget(self.paste_other_apps)
        paste_options.addWidget(self.paste_images)
        layout.addLayout(paste_options)
        self._paste_options = (
            self.paste_pickup, self.paste_other_apps, self.paste_images,
        )
        self.paste_mode.toggled.connect(self._update_paste_controls)
        self._update_paste_controls()

        music_title = QLabel("Music reactions")
        music_title.setStyleSheet("font-weight: 600; margin-top: 6px;")
        layout.addWidget(music_title)

        self.music_enabled = QCheckBox("Dance when music is playing on this PC")
        self.music_enabled.setChecked(bool(settings.get("music_enabled", True)))
        layout.addWidget(self.music_enabled)

        music_hint = QLabel(
            "Detection runs offline on the system playback mix. Lectures, calls, "
            "podcasts, and ordinary speech stay non-music. Audio is analysed in "
            "memory and never saved, recorded, or sent anywhere."
        )
        music_hint.setWordWrap(True)
        music_hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(music_hint)

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Animation style"))
        self.music_style = QComboBox()
        for value, label in (
            ("auto", "Auto Vibe"), ("chill", "Chill"), ("groovy", "Groovy"),
            ("hype", "Hype"), ("minimal", "Minimal"), ("off", "Off"),
        ):
            self.music_style.addItem(label, value)
        index = self.music_style.findData(str(settings.get("music_style", "auto")))
        self.music_style.setCurrentIndex(max(0, index))
        style_row.addWidget(self.music_style, 1)
        layout.addLayout(style_row)

        self.music_dance_catalog = QCheckBox(
            "Use the full dance catalog in Auto Vibe"
        )
        self.music_dance_catalog.setChecked(
            bool(settings.get("music_dance_catalog", True))
        )
        self.music_dance_catalog.setToolTip(
            "On: Auto Vibe rotates through 14 dances. Off: Auto Vibe uses only "
            "the original Chill, Groovy, and Hype choices."
        )
        layout.addWidget(self.music_dance_catalog)
        catalog_hint = QLabel(
            "Off: Auto Vibe picks only the original three moves. "
            "Chill, Groovy, Hype, and Minimal are unaffected."
        )
        catalog_hint.setWordWrap(True)
        catalog_hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(catalog_hint)

        intensity_row = QHBoxLayout()
        intensity_row.addWidget(QLabel("Animation intensity"))
        self.music_intensity = QSlider(Qt.Orientation.Horizontal)
        self.music_intensity.setRange(0, 100)
        self.music_intensity.setValue(int(settings.get("music_intensity", 55)))
        self._intensity_value = QLabel(str(self.music_intensity.value()))
        self._intensity_value.setMinimumWidth(28)
        self.music_intensity.valueChanged.connect(
            lambda value: self._intensity_value.setText(str(value))
        )
        intensity_row.addWidget(self.music_intensity, 1)
        intensity_row.addWidget(self._intensity_value)
        layout.addLayout(intensity_row)

        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(QLabel("Detection sensitivity"))
        self.music_sensitivity = QSlider(Qt.Orientation.Horizontal)
        self.music_sensitivity.setRange(0, 100)
        self.music_sensitivity.setValue(int(settings.get("music_sensitivity", 55)))
        self._sensitivity_value = QLabel(str(self.music_sensitivity.value()))
        self._sensitivity_value.setMinimumWidth(28)
        self.music_sensitivity.valueChanged.connect(
            lambda value: self._sensitivity_value.setText(str(value))
        )
        sensitivity_row.addWidget(self.music_sensitivity, 1)
        sensitivity_row.addWidget(self._sensitivity_value)
        layout.addLayout(sensitivity_row)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Wait before dancing"))
        self.music_delay = QSpinBox()
        self.music_delay.setRange(2, 30)
        self.music_delay.setSuffix(" s")
        self.music_delay.setValue(int(round(float(settings.get("music_delay", 6.0)))))
        self.music_delay.setToolTip(
            "Music must stay confidently classified for this long before the cat wakes."
        )
        delay_row.addWidget(self.music_delay)
        delay_row.addStretch(1)
        layout.addLayout(delay_row)

        self.music_sfx = QCheckBox("Soft tick on strong beats (low volume)")
        self.music_sfx.setChecked(bool(settings.get("music_sfx", False)))
        layout.addWidget(self.music_sfx)

        preview_row = QHBoxLayout()
        self.music_status = QLabel(f"Audio: {music_status or 'Not started'}")
        self.music_status.setStyleSheet("color: palette(mid);")
        self.music_status.setWordWrap(True)
        preview_row.addWidget(self.music_status, 1)
        self.preview_button = QPushButton("Preview animation")
        self.preview_button.setToolTip("Show the selected dance for a few seconds without music.")
        self.preview_button.clicked.connect(self._preview_music)
        preview_row.addWidget(self.preview_button)
        layout.addLayout(preview_row)

        always_title = QLabel("Always treat as music")
        always_title.setStyleSheet("font-weight: 600; margin-top: 6px;")
        layout.addWidget(always_title)

        always_hint = QLabel(
            "Sound from these sources skips the detector, so the cat dances even "
            "when a track is quiet, beatless, or read as speech. Apps match the "
            "process name Windows reports; websites match the active tab's host "
            "and need the browser extension."
        )
        always_hint.setWordWrap(True)
        always_hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(always_hint)

        pick_row = QHBoxLayout()
        self.always_app_pick = QComboBox()
        self.always_app_pick.addItem("Choose an installed or running app…", "")
        pick_row.addWidget(self.always_app_pick, 1)
        add_music_app = QPushButton("Add app")
        add_music_app.clicked.connect(self._add_always_app)
        pick_row.addWidget(add_music_app)
        layout.addLayout(pick_row)

        apps_hint = QLabel("Always-music apps (one process name per line):")
        apps_hint.setWordWrap(True)
        layout.addWidget(apps_hint)
        self.always_apps = QTextEdit()
        self.always_apps.setPlaceholderText("spotify.exe\nmusicbee.exe")
        self.always_apps.setMaximumHeight(66)
        self.always_apps.setPlainText("\n".join(settings.get("music_always_apps", [])))
        layout.addWidget(self.always_apps)

        sites_hint = QLabel("Always-music websites (one host per line):")
        sites_hint.setWordWrap(True)
        layout.addWidget(sites_hint)
        self.always_domains = QTextEdit()
        self.always_domains.setPlaceholderText("music.youtube.com\nsoundcloud.com")
        self.always_domains.setMaximumHeight(66)
        self.always_domains.setPlainText("\n".join(settings.get("music_always_domains", [])))
        layout.addWidget(self.always_domains)

        self._music_controls = (
            self.music_intensity, self.music_sensitivity, self.music_delay,
            self.music_sfx,
        )
        self.music_enabled.toggled.connect(self._update_music_controls)
        self.music_style.currentIndexChanged.connect(self._update_music_controls)
        self._update_music_controls()

        sites_label = QLabel("Blocked websites (one domain per line, e.g. instagram.com):")
        sites_label.setWordWrap(True)
        layout.addWidget(sites_label)
        self.domains = QTextEdit()
        self.domains.setPlainText("\n".join(settings.get("blocked_domains", [])))
        self.domains.setMaximumHeight(110)
        layout.addWidget(self.domains)

        apps_label = QLabel("Blocked applications — tick the apps the cat should interrupt:")
        apps_label.setWordWrap(True)
        layout.addWidget(apps_label)

        search_row = QHBoxLayout()
        self.app_search = QLineEdit()
        self.app_search.setPlaceholderText("Search installed and running apps…")
        self.app_search.setClearButtonEnabled(True)
        self.app_search.textChanged.connect(self._filter_apps)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(lambda: self._load_apps(refresh=True))
        search_row.addWidget(self.app_search, 1)
        search_row.addWidget(refresh_button)
        layout.addLayout(search_row)

        self.app_list = QListWidget()
        self.app_list.setMinimumHeight(150)
        self.app_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.app_list.setIconSize(QSize(20, 20))
        self.app_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.app_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._icons = QFileIconProvider()
        layout.addWidget(self.app_list, 1)

        manual_row = QHBoxLayout()
        self.manual_app = QLineEdit()
        self.manual_app.setPlaceholderText("Add a process name manually, e.g. steam.exe")
        self.manual_app.returnPressed.connect(self._add_manual_app)
        add_button = QPushButton("Add")
        add_button.clicked.connect(self._add_manual_app)
        manual_row.addWidget(self.manual_app, 1)
        manual_row.addWidget(add_button)
        layout.addLayout(manual_row)

        hint = QLabel(
            "A matching app is asked to close when the cat's pounce lands, about a "
            "second into its visit; the cat itself only plays the animation. An app "
            "that ignores the request is ended 2 seconds later, and one close event "
            "always finishes inside 10 seconds. Leave nothing ticked to keep app "
            "blocking off."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)

        footer = QHBoxLayout()
        footer.setContentsMargins(18, 8, 18, 14)
        footer.addStretch()
        cancel = QPushButton("Cancel")
        save = QPushButton("Save settings")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(save)
        outer.addLayout(footer)

        self._load_apps()
        self._fit_to_screen()

    def _fit_to_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(600, 720)
            return
        available = screen.availableGeometry()
        self.resize(
            min(620, max(460, available.width() - 120)),
            min(720, max(400, int(available.height() * 0.8))),
        )

    def _update_music_controls(self, *_):
        style = self.music_style.currentData()
        on = self.music_enabled.isChecked()
        for widget in self._music_controls:
            widget.setEnabled(on)
        self.music_style.setEnabled(True)
        self.music_dance_catalog.setEnabled(on and style == "auto")
        self.preview_button.setEnabled(style != "off")

    def _update_paste_controls(self, *_):
        on = self.paste_mode.isChecked()
        for widget in self._paste_options:
            widget.setEnabled(on)

    def _preview_music(self):
        style = self.music_style.currentData()
        if style and style != "off":
            self.preview_requested.emit(style, self.music_intensity.value())

    def _add_always_app(self):
        exe = str(self.always_app_pick.currentData() or "").strip()
        if not exe:
            return
        lines = [line for line in self.always_apps.toPlainText().splitlines() if line.strip()]
        if exe.lower() not in {line.strip().lower() for line in lines}:
            lines.append(exe)
            self.always_apps.setPlainText("\n".join(lines))

    def _make_item(self, name: str, exe: str, path: str, checked: bool) -> QListWidgetItem:
        label = exe if name.lower() == exe else f"{name}  —  {exe}"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, exe)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        if path:
            item.setIcon(self._icons.icon(QFileInfo(path)))
            item.setToolTip(path)
        return item

    def _load_apps(self, refresh: bool = False):
        wanted = set(self.settings.get("blocked_apps", []))
        for index in range(self.app_list.count()):
            item = self.app_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                wanted.add(item.data(Qt.ItemDataRole.UserRole))
        self.app_list.clear()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            entries = discover_apps(refresh=refresh)
        finally:
            QApplication.restoreOverrideCursor()
        known = set()
        for entry in entries:
            known.add(entry.exe)
            self.app_list.addItem(
                self._make_item(entry.name, entry.exe, entry.path, entry.exe in wanted)
            )
        for exe in sorted(wanted - known):
            self.app_list.addItem(self._make_item(exe, exe, "", True))
        selected = str(self.always_app_pick.currentData() or "")
        self.always_app_pick.clear()
        self.always_app_pick.addItem("Choose an installed or running app…", "")
        for entry in entries:
            self.always_app_pick.addItem(f"{entry.name} — {entry.exe}", entry.exe)
        if selected:
            index = self.always_app_pick.findData(selected)
            if index >= 0:
                self.always_app_pick.setCurrentIndex(index)
        self._filter_apps()

    def _filter_apps(self, *_):
        term = self.app_search.text().strip().lower()
        for index in range(self.app_list.count()):
            item = self.app_list.item(index)
            item.setHidden(bool(term) and term not in item.text().lower())

    def _add_manual_app(self):
        values = normalize_apps([self.manual_app.text()])
        if not values:
            return
        exe = values[0]
        if "." not in exe:
            exe += ".exe"
        for index in range(self.app_list.count()):
            item = self.app_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == exe:
                item.setCheckState(Qt.CheckState.Checked)
                self.app_list.scrollToItem(item)
                self.manual_app.clear()
                self._filter_apps()
                return
        item = self._make_item(exe, exe, "", True)
        self.app_list.insertItem(0, item)
        self.app_list.scrollToItem(item)
        self.manual_app.clear()
        self._filter_apps()

    def accept(self):
        self.settings["enabled"] = self.enabled.isChecked()
        self.settings["close_tabs"] = self.close_tabs.isChecked()
        self.settings["close_apps"] = self.close_apps.isChecked()
        self.settings["blocked_domains"] = normalize_domains(self.domains.toPlainText().splitlines())
        self.settings["blocked_apps"] = normalize_apps([
            self.app_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.app_list.count())
            if self.app_list.item(index).checkState() == Qt.CheckState.Checked
        ])
        self.settings["sleep_delay"] = float(self.sleep_delay.value())
        self.settings["sleep_zzz"] = self.sleep_zzz.isChecked()
        self.settings["paste_mode"] = self.paste_mode.isChecked()
        self.settings["paste_pickup"] = self.paste_pickup.isChecked()
        self.settings["paste_other_apps"] = self.paste_other_apps.isChecked()
        self.settings["paste_images"] = self.paste_images.isChecked()
        self.settings["music_enabled"] = self.music_enabled.isChecked()
        self.settings["music_style"] = str(self.music_style.currentData() or "auto")
        self.settings["music_dance_catalog"] = self.music_dance_catalog.isChecked()
        self.settings["music_intensity"] = int(self.music_intensity.value())
        self.settings["music_sensitivity"] = int(self.music_sensitivity.value())
        self.settings["music_delay"] = float(self.music_delay.value())
        self.settings["music_sfx"] = self.music_sfx.isChecked()
        self.settings["music_always_apps"] = normalize_apps(
            self.always_apps.toPlainText().splitlines()
        )
        self.settings["music_always_domains"] = normalize_domains(
            self.always_domains.toPlainText().splitlines()
        )
        current = load_settings()
        current.update({
            "enabled": self.settings["enabled"],
            "close_tabs": self.settings["close_tabs"],
            "close_apps": self.settings["close_apps"],
            "blocked_domains": self.settings["blocked_domains"],
            "blocked_apps": self.settings["blocked_apps"],
            "sleep_delay": self.settings["sleep_delay"],
            "sleep_zzz": self.settings["sleep_zzz"],
            "paste_mode": self.settings["paste_mode"],
            "paste_pickup": self.settings["paste_pickup"],
            "paste_other_apps": self.settings["paste_other_apps"],
            "paste_images": self.settings["paste_images"],
            "music_enabled": self.settings["music_enabled"],
            "music_style": self.settings["music_style"],
            "music_dance_catalog": self.settings["music_dance_catalog"],
            "music_intensity": self.settings["music_intensity"],
            "music_sensitivity": self.settings["music_sensitivity"],
            "music_delay": self.settings["music_delay"],
            "music_sfx": self.settings["music_sfx"],
            "music_always_apps": self.settings["music_always_apps"],
            "music_always_domains": self.settings["music_always_domains"],
        })
        save_settings(current)
        self.settings = current
        super().accept()


class CatGuardianApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.settings = load_settings()
        self.pet = CatGuardian(self.settings)
        self.pet.position_saved.connect(self._save_position)
        self.pet.settings_requested.connect(self.open_settings)
        self.pet.protection_toggle_requested.connect(self.toggle_protection)
        self.pet.quit_requested.connect(app.quit)
        self.pet.notify.connect(self._notify)
        self._meow_sound = MeowSound()
        self.pet.meow.connect(self._meow_sound.play)

        self.tray = QSystemTrayIcon(cat_icon(64, self.settings.get("cat_color")), self)
        self.tray.setToolTip(APP_NAME)
        self._build_tray_menu()
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.bridge: BridgeServer | None = None
        try:
            self.bridge = BridgeServer(
                animates_provider=lambda: not self.pet.hidden_by_user
            )
            self.bridge.signals.distraction.connect(self._on_site_distraction)
            self.bridge.signals.close_result.connect(self.pet.close_result)
        except OSError:
            logging.exception("Could not start the local bridge")
            QTimer.singleShot(600, lambda: self._notify(
                APP_NAME,
                "Port 8765 is busy — website blocking is off until it is free.",
            ))

        self.watcher = AppWatcher(lambda: self.pet.settings)
        self.watcher.distraction.connect(self._on_app_distraction)

        self.paste = ClipboardPasteWatcher(self)
        self.paste.delivery.connect(self._on_paste_delivery)
        self.paste.pickup.connect(self._on_paste_pickup)
        self.paste.set_enabled(bool(self.settings.get("paste_mode", False)))
        self._apply_paste_options()

        self._music_status_text = "Off"
        self._beat_sound = BeatSound()
        self.music = MusicMonitor()
        self.music.report.connect(self.pet.music_report)
        self.music.beat.connect(self._on_music_beat)
        self.music.status.connect(self._on_music_status)

        self.always = AlwaysMusicWatcher(lambda: self.pet.settings)
        self.always.changed.connect(self.pet.set_always_music)
        if self.bridge is not None:
            self.bridge.signals.music_source.connect(self.always.set_web_source)

        self.pet.show_cat()
        QTimer.singleShot(250, self.pet.startup_greeting)
        self._sync_music_monitor()

    # ------------------------------------------------------------------ tray

    def _build_tray_menu(self):
        menu = QMenu()
        self.tray_menu = menu
        self.enabled_action = menu.addAction(self._enabled_text())
        self.enabled_action.triggered.connect(self.toggle_protection)
        menu.addSeparator()
        settings_action = menu.addAction("Settings…")
        settings_action.triggered.connect(self.open_settings)
        appearance_action = menu.addAction("Cat Appearance…")
        appearance_action.triggered.connect(self.open_appearance)
        gestures_action = menu.addAction("Info / Mouse Gestures…")
        gestures_action.triggered.connect(self.show_gestures)
        menu.addSeparator()
        self.music_status_action = menu.addAction("Music: Off")
        self.music_status_action.setEnabled(False)
        self.music_toggle_action = menu.addAction("Music reactions")
        self.music_toggle_action.setCheckable(True)
        self.music_toggle_action.setChecked(bool(self.settings.get("music_enabled", True)))
        self.music_toggle_action.triggered.connect(self.toggle_music)
        preview_action = menu.addAction("Preview music animation")
        preview_action.triggered.connect(self.preview_music)
        menu.addSeparator()
        self.visibility_action = menu.addAction("Hide cat")
        self.visibility_action.triggered.connect(self.toggle_visibility)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.app.quit)
        self.tray.setContextMenu(menu)

    def _enabled_text(self) -> str:
        return "Pause protection" if self.settings.get("enabled") else "Enable protection"

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.pet.show_cat()
            self.visibility_action.setText("Hide cat")

    def toggle_visibility(self):
        if self.pet.isVisible() and not self.pet.hidden_by_user:
            self.pet.hide_cat()
            self.visibility_action.setText("Show cat")
        else:
            self.pet.show_cat()
            self.visibility_action.setText("Hide cat")

    def toggle_protection(self):
        self.settings = load_settings()
        self.settings["enabled"] = not self.settings.get("enabled", True)
        save_settings(self.settings)
        self.pet.apply_settings(self.settings)
        self.enabled_action.setText(self._enabled_text())
        self.tray.showMessage(
            APP_NAME,
            "Protection enabled" if self.settings["enabled"] else "Protection paused",
        )
        logging.info("Protection enabled=%s", self.settings["enabled"])

    def open_settings(self):
        dialog = SettingsDialog(load_settings(), self._music_status_text)
        dialog.preview_requested.connect(
            lambda style, intensity: self.pet.preview_music(style, intensity, 10)
        )
        if dialog.exec():
            self.settings = load_settings()
            self.pet.apply_settings(self.settings)
            self.enabled_action.setText(self._enabled_text())
            self._sync_music_monitor()
            self.always.refresh(force=True)
            self.paste.set_enabled(bool(self.settings.get("paste_mode", False)))
            self._apply_paste_options()
            self.tray.showMessage(APP_NAME, "Settings saved locally")

    def _apply_paste_options(self):
        self.paste.configure(
            pickup=self.settings.get("paste_pickup", True),
            other_apps=self.settings.get("paste_other_apps", True),
            images=self.settings.get("paste_images", True),
        )

    def _sync_music_monitor(self):
        enabled = (
            bool(self.settings.get("music_enabled", True))
            and self.settings.get("music_style", "auto") != "off"
        )
        if enabled:
            self.music.start(self.settings)
        else:
            self.music.stop()
            self.pet.clear_music()
        self.music_toggle_action.setChecked(enabled)

    def _on_music_status(self, code: str, text: str):
        self._music_status_text = text
        self.music_status_action.setText(f"Music: {text}")
        self.tray.setToolTip(f"{APP_NAME} — music: {text}")

    def _on_music_beat(self, event):
        self.pet.music_beat(event)
        if self.settings.get("music_sfx") and self.pet.music_dancing:
            self._beat_sound.play(True, getattr(event, "strength", 0.5))

    def toggle_music(self, checked: bool):
        current = load_settings()
        current["music_enabled"] = bool(checked)
        if checked and current.get("music_style") == "off":
            current["music_style"] = "auto"
        save_settings(current)
        self.settings = current
        self.pet.apply_settings(current)
        self._sync_music_monitor()
        self.always.refresh(force=True)
        self.tray.showMessage(
            APP_NAME,
            "Music reactions on" if checked else "Music reactions off",
        )
        logging.info("Music reactions enabled=%s", checked)

    def preview_music(self):
        style = self.settings.get("music_style", "auto")
        if style == "off":
            style = "groovy"
        self.pet.preview_music(
            style, int(self.settings.get("music_intensity", 55)), 10
        )
        self.tray.showMessage(APP_NAME, "Previewing the dance for 10 seconds")

    def open_appearance(self):
        dialog = AppearanceDialog(load_settings().get("cat_color", DEFAULT_CAT_COLOR))
        if not dialog.exec():
            return
        current = load_settings()
        current["cat_color"] = dialog.selected_color
        save_settings(current)
        self.settings = current
        self.pet.apply_settings(current)
        self.tray.setIcon(cat_icon(64, dialog.selected_color))
        self.tray.showMessage(
            APP_NAME, f"Coat updated — {color_name(dialog.selected_color)}",
            QSystemTrayIcon.MessageIcon.Information, 2200,
        )
        logging.info("Cat color set to %s", dialog.selected_color)

    def show_gestures(self):
        GestureInfoDialog().exec()

    def _notify(self, title: str, body: str):
        self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 2600)

    def _save_position(self, x: int, y: int):
        self.pet.settings["cat_pos"] = [int(x), int(y)]
        current = load_settings()
        current["cat_pos"] = [int(x), int(y)]
        save_settings(current)
        self.settings["cat_pos"] = current["cat_pos"]

    # ------------------------------------------------------------ distractors

    def _on_site_distraction(self, event):
        host = urlparse(event.url).hostname or "blocked site"
        self.pet.distraction(
            kind="site", label=host, info=event.info, ticket=event.ticket,
            tab_index=event.tab_index, tab_count=event.tab_count, close=event.close,
        )
        logging.info(
            "Site distraction: %s tab=%s close=%s",
            host, event.tab_id, event.ticket is not None,
        )

    def _on_app_distraction(self, info):
        closing = bool(self.pet.settings.get("close_apps"))
        # When the app is closed at once there is no window to perch on, so the
        # cat plays its visit on the desktop while the close happens behind it.
        self.pet.distraction(
            kind="app", label=info.exe,
            info=None if closing else info, targetless=closing,
        )
        logging.info("App distraction: %s", info.exe)
        if closing:
            # The animation is presentation only. The close is timed to the
            # pounce and is still bounded well inside the ten-second window.
            QTimer.singleShot(
                APP_CLOSE_SYNC_MS, lambda: self._close_app(info)
            )

    def _on_paste_delivery(self, delivery):
        # The pet refuses the request itself when it is busy, greeting, hidden,
        # or off the desktop; the setting is checked again here in case the
        # mode was turned off between the paste and this queued callback.
        if not self.settings.get("paste_mode"):
            return
        if (delivery.target == "app"
                and not self.settings.get("paste_other_apps", True)):
            return
        if delivery.kind == "image" and not self.settings.get("paste_images", True):
            return
        self.pet.paste_delivery(
            icon=delivery.icon, count=delivery.count, cut=delivery.cut,
            point=delivery.point, kind=delivery.kind,
        )

    def _on_paste_pickup(self, pickup):
        # Same gating as a delivery: the pet decides whether it is free.
        if (not self.settings.get("paste_mode")
                or not self.settings.get("paste_pickup", True)):
            return
        self.pet.paste_pickup(
            icon=pickup.icon, count=pickup.count, cut=pickup.cut,
            point=pickup.point, kind=pickup.kind,
        )

    def _close_app(self, info):
        if native.window_pid(info.hwnd) != info.pid:
            logging.info("App close skipped: %s window is already gone", info.exe)
            return
        native.post_close(info.hwnd)
        logging.info("Close requested for %s (pid %s)", info.exe, info.pid)
        QTimer.singleShot(APP_CLOSE_POLL_MS, lambda: self._wait_close_app(info, 1))

    def _wait_close_app(self, info, polls):
        """Catch the exit mid-visit so the brag grows out of the close."""
        if not native.is_process_running(info.pid):
            self.pet.confirm_close("app")
            return
        if polls * APP_CLOSE_POLL_MS >= APP_CLOSE_GRACE_MS:
            self._force_close_app(info)
            return
        QTimer.singleShot(
            APP_CLOSE_POLL_MS, lambda: self._wait_close_app(info, polls + 1)
        )

    def _force_close_app(self, info):
        if not native.is_process_running(info.pid):
            # The app accepted the close request; let the cat brag about it.
            self.pet.confirm_close("app")
            return
        if native.terminate_process(info.pid, info.exe):
            logging.info("%s ignored the close request; process ended", info.exe)
            self.pet.confirm_close("app")
        else:
            logging.info("Could not end %s (pid %s)", info.exe, info.pid)

    def shutdown(self):
        self.paste.stop()
        self.always.stop()
        self.music.stop()
        self.pet.shutdown()
        if self.bridge is not None:
            self.bridge.stop()


def main():
    if sys.platform != "win32":
        print("Cat Guardian is designed for Windows 10/11; other platforms load the pet only.")

    logging.basicConfig(
        filename=str(LOG_PATH), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    guardian = CatGuardianApp(app)
    app.aboutToQuit.connect(guardian.shutdown)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
