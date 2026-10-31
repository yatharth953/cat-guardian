"""Small, defensive Win32 helpers used to keep the cat on the desktop layer.

The wallpaper/WorkerW trick used by older desktop pets no longer renders on
current Windows 11 builds, so the cat lives as a normal tool window pinned to
the bottom of the z-order (visible on the desktop, covered by applications)
and only becomes topmost while it is handling a distraction.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

SHELL_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "SysListView32",
    "Windows.UI.Core.CoreWindow",
}

HWND_BOTTOM = 1
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020

WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
MONITOR_DEFAULTTONEAREST = 0x00000002


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    exe: str
    title: str
    left: int
    top: int
    right: int
    bottom: int
    shell: bool
    cls: str = ""

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    _get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    _set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    _get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    _get_long.restype = ctypes.c_ssize_t
    _set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _set_long.restype = ctypes.c_ssize_t

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_pid(hwnd: int) -> int:
    if not IS_WINDOWS or not hwnd:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def process_image_path(pid: int) -> str:
    """Full image path of a process, or an empty string when it is inaccessible."""
    if not IS_WINDOWS or not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _process_name(pid: int) -> str:
    return os.path.basename(process_image_path(pid))


def is_process_running(pid: int) -> bool:
    if not IS_WINDOWS or not pid:
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def terminate_process(pid: int, expected_exe: str = "") -> bool:
    """End one process, and only while it still runs the expected image.

    The image check matters because a pid can be recycled between detection
    and the force-close following a window that ignored WM_CLOSE.
    """
    if not IS_WINDOWS or not pid:
        return False
    if expected_exe:
        path = process_image_path(pid)
        if not path or os.path.basename(path).lower() != expected_exe.lower():
            return False
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def monitor_bounds(x: int, y: int) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    """Return (monitor_rect, work_rect) in physical pixels around point (x, y)."""
    if not IS_WINDOWS:
        return None
    handle = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    if not handle:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return None
    m = info.rcMonitor
    w = info.rcWork
    return (
        (m.left, m.top, m.right, m.bottom),
        (w.left, w.top, w.right, w.bottom),
    )


def foreground_window_info() -> WindowInfo | None:
    if not IS_WINDOWS:
        return None
    hwnd = int(user32.GetForegroundWindow() or 0)
    if not hwnd:
        return None
    pid = window_pid(hwnd)
    if pid == os.getpid():
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    cls = _class_name(hwnd)
    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        exe=_process_name(pid),
        title=_window_title(hwnd),
        left=rect.left,
        top=rect.top,
        right=rect.right,
        bottom=rect.bottom,
        shell=cls in SHELL_CLASSES,
        cls=cls,
    )


def visible_window_titles() -> list[tuple[int, str]]:
    """(pid, title) for visible top-level windows with a title, in z-order."""
    if not IS_WINDOWS:
        return []
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _param):
        if user32.IsWindowVisible(hwnd):
            title = _window_title(hwnd)
            if title.strip():
                found.append((window_pid(hwnd), title))
        return True

    user32.EnumWindows(collect, 0)
    return found


def is_foreground(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    return foreground_hwnd() == int(hwnd)


def foreground_hwnd() -> int:
    if not IS_WINDOWS:
        return 0
    return int(user32.GetForegroundWindow() or 0)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Physical-pixel window rect, used to notice the target moving away."""
    if not IS_WINDOWS or not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def is_minimized(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    return bool(user32.IsIconic(hwnd))


def apply_no_activate(hwnd: int) -> None:
    """Clicks and hover must never raise the cat above normal windows."""
    if not IS_WINDOWS or not hwnd:
        return
    style = _get_long(hwnd, GWL_EXSTYLE)
    wanted = style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    if wanted != style:
        _set_long(hwnd, GWL_EXSTYLE, wanted)


def set_click_through(hwnd: int, enabled: bool) -> None:
    """Keep WS_EX_TRANSPARENT in sync with the pet's mode.

    Qt sets this bit for WA_TransparentForMouseEvents but does not always
    clear it again on a long-lived tool window, which would leave the cat
    permanently unclickable after a greeting or a blocking visit.
    """
    if not IS_WINDOWS or not hwnd:
        return
    style = _get_long(hwnd, GWL_EXSTYLE)
    wanted = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
    if wanted != style:
        _set_long(hwnd, GWL_EXSTYLE, wanted)


def send_to_bottom(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    user32.SetWindowPos(
        hwnd, HWND_BOTTOM, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def send_to_top(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def post_close(hwnd: int) -> None:
    """Ask a window to close gracefully; the app decides whether to save."""
    if not IS_WINDOWS or not hwnd:
        return
    if user32.IsWindow(hwnd):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
