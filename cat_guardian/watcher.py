from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import native


class AppWatcher(QObject):
    """Watches the foreground window for configured distracting applications."""

    # native.WindowInfo
    distraction = Signal(object)

    def __init__(self, settings_provider, parent=None):
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._last_seen: dict[str, float] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self._check)
        self._timer.start()

    def _check(self):
        try:
            settings = self._settings_provider()
            if not settings.get("enabled"):
                return
            blocked = {str(name).lower() for name in settings.get("blocked_apps", [])}
            if not blocked:
                return
            info = native.foreground_window_info()
            if not info or info.shell or not info.exe:
                return
            exe = info.exe.lower()
            if exe not in blocked:
                return
            now = time.monotonic()
            if now - self._last_seen.get(exe, 0.0) < 90:
                return
            self._last_seen[exe] = now
            self.distraction.emit(info)
        except Exception:
            import logging
            logging.exception("App watcher check failed")
