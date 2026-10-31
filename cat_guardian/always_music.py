"""User-selected sources whose audio always counts as music.

The classifier in :mod:`cat_guardian.music` remains authoritative for
everything else. This module only answers one narrow question: is one of the
pre-selected apps or websites currently playing sound? If so, the pet is told
to dance even while the classifier is unsure or calls it speech.

Apps are matched against Windows audio sessions, so a music player may sit in
the background or behind other windows. A configured music site also names its
desktop player, so ``spotify.com`` covers Spotify.exe as well as the web player.
Websites are matched from the optional browser extension (any audible tab) or,
when it is not installed, by comparing the titles of audible browser windows
with the configured hosts. Those titles are compared in memory only; a
non-matching title is never stored, sent, or logged. Only allowlisted sources
ever reach this module.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import sys
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from . import native
from .settings import domain_matches

IS_WINDOWS = sys.platform == "win32"

# A WASAPI session at or above this peak is producing sound. The floor is far
# below music levels and only rejects digital silence.
AUDIBLE_PEAK = 0.002
SESSION_POLL_MS = 1500
EXE_CACHE_SECONDS = 15.0
# A website keeps counting after its last beacon only while the browser still
# has an audio session. This bounds the damage if the extension disappears.
WEB_SOURCE_TTL = 600.0

BROWSER_AUDIO_EXES = {
    "chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe",
    "opera.exe", "chromium.exe", "firefox.exe",
}

# A configured site also identifies its desktop player, so the same allowlist
# entry works for the web player and the installed app.
SERVICE_APPS = {
    "spotify.com": ("spotify.exe", "spotifylauncher.exe"),
    "music.apple.com": ("applemusic.exe", "itunes.exe"),
    "deezer.com": ("deezer.exe",),
    "tidal.com": ("tidal.exe",),
    "soundcloud.com": ("soundcloud.exe",),
    "music.amazon.com": ("amazon music.exe",),
    "music.amazon.in": ("amazon music.exe",),
}

# Host prefixes that describe access rather than the service itself.
_GENERIC_HOST_LABELS = frozenset({
    "www", "open", "play", "web", "listen", "m", "new", "go",
})

_CLSCTX_ALL = 0x17
_COINIT_APARTMENTTHREADED = 0x2
_SESSION_ACTIVE = 1
_ERENDER = 0
_ECONSOLE = 0

_CLSID_DEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_DEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_SESSION_MANAGER2 = "{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"
_IID_SESSION_CONTROL2 = "{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}"
_IID_METER = "{C02216F6-8C67-4B5B-9D00-D008E73E0064}"


@dataclass(frozen=True)
class AudioSession:
    pid: int
    peak: float
    system: bool = False


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    value = uuid.UUID(text)
    result = _GUID()
    result.Data1 = value.time_low
    result.Data2 = value.time_mid
    result.Data3 = value.time_hi_version
    for index, byte in enumerate(value.bytes[8:]):
        result.Data4[index] = byte
    return result


def _proto(index: int, *argtypes):
    """Bind one COM vtable entry; the interface pointer is `this`."""
    signature = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)

    def call(pointer, *args):
        table = ctypes.cast(
            pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        return signature(table[index])(pointer, *args)

    return call


if IS_WINDOWS:
    _ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    _ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    _ole32.CoCreateInstance.restype = ctypes.c_long
    _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _ole32.CoInitializeEx.restype = ctypes.c_long
    _ole32.CoUninitialize.argtypes = []
    _ole32.CoUninitialize.restype = None

    _RELEASE = _proto(2)
    _QUERY_INTERFACE = _proto(
        0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    _GET_DEFAULT_ENDPOINT = _proto(
        4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
    )
    # IMMDevice: QueryInterface, AddRef, Release, Activate, OpenPropertyStore, …
    _ACTIVATE = _proto(
        3, ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _GET_SESSION_ENUMERATOR = _proto(5, ctypes.POINTER(ctypes.c_void_p))
    _GET_COUNT = _proto(3, ctypes.POINTER(ctypes.c_int))
    _GET_SESSION = _proto(4, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
    _GET_STATE = _proto(3, ctypes.POINTER(ctypes.c_int))
    _GET_PROCESS_ID = _proto(14, ctypes.POINTER(wintypes.DWORD))
    _IS_SYSTEM_SOUNDS = _proto(15)
    _GET_PEAK = _proto(3, ctypes.POINTER(ctypes.c_float))

    _CLSID_ENUMERATOR = _guid(_CLSID_DEVICE_ENUMERATOR)
    _IID_ENUMERATOR = _guid(_IID_DEVICE_ENUMERATOR)
    _IID_SESSION_MANAGER2 = _guid(_IID_SESSION_MANAGER2)
    _IID_SESSION_CONTROL2 = _guid(_IID_SESSION_CONTROL2)
    _IID_METER = _guid(_IID_METER)

_exe_cache: dict[int, tuple[float, str]] = {}


def _release(pointer) -> None:
    if pointer is not None and pointer.value:
        try:
            _RELEASE(pointer)
        finally:
            pointer.value = None


def _exe_for(pid: int) -> str:
    now = time.monotonic()
    cached = _exe_cache.get(pid)
    if cached and now - cached[0] < EXE_CACHE_SECONDS:
        return cached[1]
    path = native.process_image_path(pid) if pid else ""
    exe = os.path.basename(path).lower() if path else ""
    if len(_exe_cache) > 256:
        _exe_cache.clear()
    _exe_cache[pid] = (now, exe)
    return exe


def _session_info(control) -> AudioSession | None:
    control2 = ctypes.c_void_p()
    meter = ctypes.c_void_p()
    try:
        if (_QUERY_INTERFACE(control, ctypes.byref(_IID_SESSION_CONTROL2),
                             ctypes.byref(control2)) < 0 or not control2):
            return None
        state = ctypes.c_int()
        if _GET_STATE(control2, ctypes.byref(state)) < 0 or state.value != _SESSION_ACTIVE:
            return None
        # IsSystemSoundsSession returns S_OK for the system notification stream.
        if _IS_SYSTEM_SOUNDS(control2) == 0:
            return None
        pid = wintypes.DWORD()
        if _GET_PROCESS_ID(control2, ctypes.byref(pid)) < 0:
            return None
        if (_QUERY_INTERFACE(control, ctypes.byref(_IID_METER),
                             ctypes.byref(meter)) < 0 or not meter):
            return None
        peak = ctypes.c_float()
        if _GET_PEAK(meter, ctypes.byref(peak)) < 0:
            return None
        return AudioSession(int(pid.value), float(peak.value))
    finally:
        _release(meter)
        _release(control2)


def audio_sessions() -> list[AudioSession] | None:
    """Active render sessions on the default output, or None when unreadable."""
    if not IS_WINDOWS:
        return None
    owned = False
    enumerator = ctypes.c_void_p()
    device = ctypes.c_void_p()
    manager = ctypes.c_void_p()
    sessions = ctypes.c_void_p()
    try:
        hresult = _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        # S_FALSE means COM was already initialised on this thread; the thread
        # (usually Qt's) owns that initialisation, so only undo our own.
        owned = hresult == 0
        if hresult < 0 and hresult != -2147417850:  # RPC_E_CHANGED_MODE
            return None
        if (_ole32.CoCreateInstance(
                ctypes.byref(_CLSID_ENUMERATOR), None, _CLSCTX_ALL,
                ctypes.byref(_IID_ENUMERATOR), ctypes.byref(enumerator),
        ) < 0 or not enumerator):
            return None
        if (_GET_DEFAULT_ENDPOINT(enumerator, _ERENDER, _ECONSOLE,
                                  ctypes.byref(device)) < 0 or not device):
            return None
        if (_ACTIVATE(device, ctypes.byref(_IID_SESSION_MANAGER2), _CLSCTX_ALL,
                      None, ctypes.byref(manager)) < 0 or not manager):
            return None
        if (_GET_SESSION_ENUMERATOR(manager, ctypes.byref(sessions)) < 0
                or not sessions):
            return None
        count = ctypes.c_int()
        if _GET_COUNT(sessions, ctypes.byref(count)) < 0:
            return None
        found: list[AudioSession] = []
        for index in range(max(0, count.value)):
            control = ctypes.c_void_p()
            if _GET_SESSION(sessions, index, ctypes.byref(control)) < 0 or not control:
                continue
            try:
                info = _session_info(control)
            finally:
                _release(control)
            if info is not None:
                found.append(info)
        return found
    except Exception:
        logging.debug("Audio session probe failed", exc_info=True)
        return None
    finally:
        for pointer in (sessions, manager, device, enumerator):
            _release(pointer)
        if owned:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass


def audible_apps(threshold: float = AUDIBLE_PEAK,
                 own_pid: int | None = None) -> dict[str, float] | None:
    """Map executable name to its loudest active session peak."""
    sessions = audio_sessions()
    if sessions is None:
        return None
    own = os.getpid() if own_pid is None else own_pid
    playing: dict[str, float] = {}
    for session in sessions:
        if session.system or session.pid == own or session.peak < threshold:
            continue
        exe = _exe_for(session.pid)
        if exe:
            playing[exe] = max(playing.get(exe, 0.0), session.peak)
    return playing


def matching_app(allowed, playing: dict[str, float]) -> str:
    """The first allowlisted executable that is currently producing sound."""
    for name in sorted(str(value).lower() for value in allowed or []):
        if playing.get(name, 0.0) > 0.0:
            return name
    return ""


def matching_site(host: str, allowed) -> str:
    host = str(host or "").strip().lower().rstrip(".")
    if not host:
        return ""
    return host if domain_matches(f"https://{host}", list(allowed or [])) else ""


def site_tokens(host: str) -> list[str]:
    """Identify a configured host inside a window title.

    ``spotify.com`` becomes ``["spotify"]`` and ``music.youtube.com`` becomes
    ``["music", "youtube"]`` so a title must mention every meaningful label;
    ``youtube.com`` alone still matches ordinary YouTube pages.
    """
    host = str(host or "").strip().lower().rstrip(".")
    labels = [part for part in host.split(".") if part]
    if len(labels) < 2:
        return []
    tokens = []
    for label in labels[:-1]:
        if label in _GENERIC_HOST_LABELS:
            continue
        token = re.sub(r"[^a-z0-9]", "", label)
        if len(token) >= 3:
            tokens.append(token)
    return tokens


def matching_web_title(allowed, titles) -> str:
    """The configured host whose labels all appear in an audible browser title."""
    normalized = [
        re.sub(r"[^a-z0-9]", "", str(title).casefold())
        for title in titles or []
    ]
    if not any(normalized):
        return ""
    for raw in allowed or []:
        host = str(raw).strip().lower().rstrip(".")
        tokens = site_tokens(host)
        if tokens and any(
            text and all(token in text for token in tokens) for text in normalized
        ):
            return host
    return ""


def service_apps(allowed) -> list[str]:
    """Desktop executables implied by the configured music sites."""
    names: set[str] = set()
    for raw in allowed or []:
        host = str(raw).strip().lower().rstrip(".")
        for domain, exes in SERVICE_APPS.items():
            if host == domain or host.endswith("." + domain):
                names.update(exes)
    return sorted(names)


def browser_window_titles(playing: dict[str, float]) -> list[str]:
    """Titles from windows of browsers that are currently producing sound."""
    audible = {exe for exe in playing or {} if exe in BROWSER_AUDIO_EXES}
    if not audible or not IS_WINDOWS:
        return []
    titles: list[str] = []
    for pid, title in native.visible_window_titles():
        if _exe_for(pid) in audible:
            titles.append(title)
    return titles


class AlwaysMusicWatcher(QObject):
    """Polls for allowlisted audio sources and reports state changes once."""

    # active, label (executable or hostname)
    changed = Signal(bool, str)

    def __init__(self, settings_provider, probe=None, title_probe=None,
                 parent=None, autostart=True):
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._probe = probe or audible_apps
        # Injected probes keep tests off the real window list.
        self._title_probe = title_probe or (
            browser_window_titles if probe is None else None
        )
        self._web_host = ""
        self._web_audible = False
        self._web_at = 0.0
        self._last_active = False
        self._last_label = ""
        self._timer = QTimer(self)
        self._timer.setInterval(SESSION_POLL_MS)
        self._timer.timeout.connect(self.poll)
        if autostart:
            self._timer.start()

    def stop(self):
        self._timer.stop()

    def set_web_source(self, host: str, audible: bool):
        """Called from the bridge when the active tab changes."""
        self._web_host = str(host or "").strip().lower().rstrip(".")
        self._web_audible = bool(audible)
        self._web_at = time.monotonic()
        self.poll()

    def refresh(self, force: bool = False):
        self.poll(force=force)

    def poll(self, force: bool = False):
        try:
            settings = self._settings_provider() or {}
            enabled = (bool(settings.get("music_enabled", True))
                       and str(settings.get("music_style", "auto")) != "off")
            apps = settings.get("music_always_apps", []) or []
            sites = settings.get("music_always_domains", []) or []
            if not enabled or (not apps and not sites):
                self._apply(False, "", force)
                return
            playing = self._probe()
            if playing is None:
                playing = {}
            active, label = self._decide(playing, apps, sites)
            self._apply(active, label, force)
        except Exception:
            logging.exception("Always-music check failed")
            self._apply(False, "", force)

    def _titles(self, playing: dict[str, float]) -> list[str]:
        if self._title_probe is None:
            return []
        try:
            return self._title_probe(playing) or []
        except Exception:
            logging.debug("Browser title probe failed", exc_info=True)
            return []

    def _decide(self, playing: dict[str, float], apps, sites) -> tuple[bool, str]:
        # An explicit app, or the desktop player named by a configured site.
        app = matching_app(apps, playing)
        if not app:
            app = matching_app(service_apps(sites), playing)
        if app:
            return True, app
        # Both web paths need the browser itself to be rendering audio, so a
        # stale beacon or title after playback stopped cannot keep the cat up.
        browser_audio = any(exe in playing for exe in BROWSER_AUDIO_EXES)
        if not browser_audio:
            return False, ""
        # The extension's beacon names audible tabs, including background ones.
        if (sites and self._web_host and self._web_audible
                and time.monotonic() - self._web_at <= WEB_SOURCE_TTL):
            host = matching_site(self._web_host, sites)
            if host:
                return True, host
        # Without a current beacon, an audible browser window's title still
        # identifies the site; titles never leave this process.
        host = matching_web_title(sites, self._titles(playing))
        return (True, host) if host else (False, "")

    def _apply(self, active: bool, label: str, force: bool):
        if not force and active == self._last_active and label == self._last_label:
            return
        changed = active != self._last_active or label != self._last_label
        self._last_active = active
        self._last_label = label
        if changed:
            logging.info("Always-music source %s", label if active else "cleared")
        self.changed.emit(active, label)


__all__ = [
    "AUDIBLE_PEAK", "AlwaysMusicWatcher", "AudioSession", "audio_sessions",
    "audible_apps", "browser_window_titles", "matching_app", "matching_site",
    "matching_web_title", "service_apps", "site_tokens",
]
