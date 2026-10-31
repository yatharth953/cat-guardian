"""Optional copy-paste deliveries.

While the mode is enabled, this module watches the clipboard for copied files
and installs a local Ctrl+V observer. A paste that lands in File Explorer or
on the desktop emits a compact delivery request; the pet turns that into its
carry-and-drop animation. Text and image clips are ignored, and the observer
only inspects the V key while Ctrl is held. Nothing is stored or sent.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QFileInfo, QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QFileIconProvider

from . import native

IS_WINDOWS = sys.platform == "win32"

# Let Explorer finish its own paste before the cat starts walking.
PASTE_SETTLE_MS = 220
PASTE_COOLDOWN_S = 0.9
# One copy can raise dataChanged more than once; animate it only once.
PICK_COOLDOWN_S = 1.2

EXPLORER_CLASSES = {"cabinetwclass", "explorewclass"}
DESKTOP_CLASSES = {"progman", "workerw"}
# Shell surfaces where a paste has nowhere to land.
SHELL_SURFACE_CLASSES = {
    "shell_traywnd", "shell_secondarytraywnd", "notifyiconoverflowwindow",
    "windows.ui.core.corewindow", "syslistview32",
}

WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
WH_KEYBOARD_LL = 13
VK_CONTROL = 0x11
VK_V = 0x56
LLKHF_INJECTED = 0x10


@dataclass
class PasteDelivery:
    icon: QPixmap | None
    count: int
    cut: bool
    point: QPoint
    target: str
    kind: str


@dataclass
class PastePickup:
    icon: QPixmap | None
    count: int
    cut: bool
    point: QPoint
    kind: str


@dataclass
class _Clip:
    icon: QPixmap | None
    count: int
    cut: bool
    kind: str


def paste_target(info: native.WindowInfo | None) -> str | None:
    """Explorer folder windows and the desktop are the honest file-paste spots.

    Other apps may accept a file paste, but the system gives no reliable
    "paste happened" signal, so the cat stays out of them instead of celebrating
    a paste that never landed.
    """
    if info is None:
        return None
    cls = str(getattr(info, "cls", "") or "").lower()
    if cls in EXPLORER_CLASSES:
        return "explorer"
    if cls in DESKTOP_CLASSES:
        return "desktop"
    return None


def delivery_target(info: native.WindowInfo | None,
                    allow_app: bool = True) -> str | None:
    """Explorer, the desktop, or any other app that could receive a paste.

    Cat Guardian's own windows never arrive here because the foreground
    lookup skips this process. Shell surfaces such as the taskbar are left
    alone, since a paste there has nowhere to land. ``allow_app`` can be
    turned off in Settings to keep the animation to Explorer and the desktop.
    """
    target = paste_target(info)
    if target is not None:
        return target
    if not allow_app:
        return None
    if info is None or not info.exe:
        return None
    cls = str(getattr(info, "cls", "") or "").lower()
    if info.shell or cls in SHELL_SURFACE_CLASSES:
        return None
    return "app"


def _preferred_drop_effect() -> int:
    """Windows stores 2 for a cut (move) and 1 for a copy."""
    if not IS_WINDOWS:
        return 0
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        if not user32.OpenClipboard(None):
            return 0
        try:
            fmt = user32.RegisterClipboardFormatW("Preferred DropEffect")
            if not fmt:
                return 0
            handle = user32.GetClipboardData(fmt)
            if not handle:
                return 0
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return 0
            try:
                value = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value
                return int(value)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        logging.debug("Could not read the clipboard drop effect", exc_info=True)
        return 0


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _CtrlVListener:
    """A low-level keyboard observer that only reports Ctrl+V presses.

    The hook is installed on its own tiny message-loop thread while the mode is
    on. Injected keys are ignored so automation cannot make the cat walk.
    """

    def __init__(self, on_press):
        self._on_press = on_press
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._proc = None
        self._user32 = None
        self._stopping = threading.Event()
        self._ready = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if not IS_WINDOWS or self.running:
            return
        self._stopping.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="cat-guardian-ctrl-v", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stopping.set()
        self._ready.wait(0.4)
        if IS_WINDOWS and self._thread_id:
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                logging.debug("Could not stop the Ctrl+V observer", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        self._thread = None
        self._thread_id = 0
        self._hook = None
        self._proc = None
        self._user32 = None

    def _run(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = wintypes.SHORT
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self._user32 = user32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        try:
            if self._stopping.is_set():
                return
            proc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
            )
            self._proc = proc_type(self._callback)
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
            if not self._hook:
                logging.warning(
                    "Ctrl+V observer could not start (Win32 error %s)",
                    ctypes.get_last_error(),
                )
                return
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            logging.exception("Ctrl+V observer stopped unexpectedly")
        finally:
            if self._hook:
                try:
                    self._user32.UnhookWindowsHookEx(self._hook)
                except Exception:
                    pass
            self._ready.set()

    def _callback(self, n_code, w_param, l_param):
        try:
            if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if (data.vkCode == VK_V
                        and not (data.flags & LLKHF_INJECTED)
                        and self._user32.GetAsyncKeyState(VK_CONTROL) & 0x8000):
                    self._on_press()
        except Exception:
            logging.debug("Ctrl+V observer callback failed", exc_info=True)
        return self._user32.CallNextHookEx(None, n_code, w_param, l_param)


class ClipboardPasteWatcher(QObject):
    """Turns a real file paste into a `PasteDelivery` request."""

    delivery = Signal(object)
    pickup = Signal(object)
    _paste_pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._copied: _Clip | None = None
        self._last_press = 0.0
        self._last_pick_at = 0.0
        # Optional parts of the animation, all on by default.
        self._pickup_enabled = True
        self._other_apps_enabled = True
        self._images_enabled = True
        self._icons = QFileIconProvider()
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self._on_clipboard_changed)
        # Emitted from the hook thread; Qt queues it onto the GUI thread.
        self._paste_pressed.connect(self._on_paste_pressed)
        self._listener = _CtrlVListener(self._paste_pressed.emit)

    def set_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self._enabled and (not enabled or self._listener.running):
            return
        self._enabled = enabled
        if enabled:
            self._copied = None
            self._remember_current_clipboard()
            self._listener.start()
            logging.info("Copy-paste deliveries enabled")
        else:
            self._listener.stop()
            self._copied = None
            logging.info("Copy-paste deliveries disabled")

    def configure(self, *, pickup: bool = True, other_apps: bool = True,
                  images: bool = True):
        """Turn the pickup trip, app targets, or image clips on or off.

        Turning the pickup off keeps clipboard tracking alive, so the drop on
        Ctrl+V still works. Turning images off ignores image clips entirely.
        """
        self._pickup_enabled = bool(pickup)
        self._other_apps_enabled = bool(other_apps)
        self._images_enabled = bool(images)

    def stop(self):
        self._enabled = False
        self._listener.stop()
        self._copied = None

    # ------------------------------------------------------------- clipboard

    @staticmethod
    def _local_paths(mime_data) -> list[str]:
        if mime_data is None or not mime_data.hasUrls():
            return []
        paths: list[str] = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if path and path not in paths:
                paths.append(path)
        return paths

    def _remember_current_clipboard(self):
        try:
            mime = self._clipboard.mimeData()
            self._remember(self._local_paths(mime), mime)
        except Exception:
            logging.debug("Could not read the clipboard contents", exc_info=True)

    def _on_clipboard_changed(self):
        if not self._enabled:
            return
        try:
            mime = self._clipboard.mimeData()
            paths = self._local_paths(mime)
        except Exception:
            mime = None
            paths = []
        self._remember(paths, mime, announce=True)

    def _remember(self, paths: list[str], mime=None, announce: bool = False):
        if not paths:
            self._copied = self._image_clip(mime) if self._images_enabled else None
            if self._copied is not None and announce:
                self._announce_pickup()
            return
        first = paths[0]
        try:
            kind = "folder" if os.path.isdir(first) else "file"
        except OSError:
            kind = "file"
        icon = None
        try:
            candidate = self._icons.icon(QFileInfo(first)).pixmap(64, 64)
            if not candidate.isNull():
                icon = candidate
        except Exception:
            logging.debug("Could not read the copied item's icon", exc_info=True)
        self._copied = _Clip(
            icon=icon,
            count=len(paths),
            cut=_preferred_drop_effect() == 2,
            kind=kind,
        )
        if announce:
            self._announce_pickup()

    def _image_clip(self, mime) -> _Clip | None:
        """A raw image clip (screenshot, copied picture), never a text clip."""
        if mime is None or not mime.hasImage():
            return None
        try:
            data = mime.imageData()
            if not isinstance(data, QImage):
                data = self._clipboard.image()
            if not isinstance(data, QImage) or data.isNull():
                return None
            icon = QPixmap.fromImage(data)
            if icon.isNull():
                return None
            icon = icon.scaled(
                64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            return _Clip(icon=icon, count=1, cut=False, kind="image")
        except Exception:
            logging.debug("Could not read the clipboard image", exc_info=True)
            return None

    def _announce_pickup(self):
        """One pickup per copy action, regardless of how many items it holds."""
        if (not self._enabled or not self._pickup_enabled
                or self._copied is None):
            return
        now = time.monotonic()
        if now - self._last_pick_at < PICK_COOLDOWN_S:
            return
        self._last_pick_at = now
        clip = self._copied
        self.pickup.emit(
            PastePickup(clip.icon, clip.count, clip.cut, QCursor.pos(), clip.kind)
        )
        logging.info(
            "Copy-paste pickup (%s item(s), %s)",
            clip.count, "cut" if clip.cut else "copy",
        )

    # ---------------------------------------------------------------- paste

    def _on_paste_pressed(self):
        if not self._enabled or self._copied is None:
            return
        now = time.monotonic()
        if now - self._last_press < PASTE_COOLDOWN_S:
            return
        self._last_press = now
        QTimer.singleShot(PASTE_SETTLE_MS, self._deliver)

    def _deliver(self):
        if not self._enabled or self._copied is None:
            return
        target = delivery_target(
            native.foreground_window_info(), allow_app=self._other_apps_enabled
        )
        if target is None:
            logging.debug("Paste delivery skipped: foreground window cannot receive it")
            return
        cursor = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        point = QPoint(
            min(max(cursor.x(), area.left() + 6), area.right() - 6),
            min(max(cursor.y() + 14, area.top() + 6), area.bottom() - 6),
        )
        clip = self._copied
        # A cut is consumed by the paste; a copy stays usable for more pastes.
        if clip.cut:
            self._copied = None
        self.delivery.emit(
            PasteDelivery(clip.icon, clip.count, clip.cut, point, target, clip.kind)
        )
        logging.info(
            "Copy-paste delivery to %s (%s item(s), %s)",
            target, clip.count, "cut" if clip.cut else "copy",
        )
