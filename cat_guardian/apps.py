"""Discover installed and running applications for the settings checklist."""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from . import native

IS_WINDOWS = sys.platform == "win32"

# Noise that users never want to block, plus the pet's own interpreter.
SKIP_EXES = {
    "explorer.exe", "python.exe", "pythonw.exe", "py.exe", "pyw.exe",
    "unins000.exe", "uninstall.exe", "setup.exe", "update.exe",
    "crashpad_handler.exe", "werfault.exe", "backgroundtaskhost.exe",
    "applicationframehost.exe", "searchhost.exe", "textinputhost.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe", "lockapp.exe",
    "dwm.exe", "conhost.exe", "dllhost.exe", "taskhostw.exe", "sihost.exe",
}
GENERIC_LINK_NAMES = {"uninstall", "readme", "website", "help", "help center", "changelog"}

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
S_FALSE = 1


@dataclass(frozen=True)
class AppEntry:
    name: str
    exe: str
    path: str = ""


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


if IS_WINDOWS:
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ole32 = ctypes.WinDLL("ole32", use_last_error=True)

    _user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _ole32.CoInitializeEx.restype = ctypes.c_long
    _ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    _ole32.CoCreateInstance.restype = ctypes.c_long
    _ole32.CoUninitialize.argtypes = []
    _ole32.CoUninitialize.restype = None

_CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
_IID_SHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
_IID_PERSISTFILE = "{0000010b-0000-0000-C000-000000000046}"

_cache: list[AppEntry] | None = None


def discover_apps(refresh: bool = False) -> list[AppEntry]:
    """Installed (Start Menu) and currently running apps, deduplicated by exe."""
    global _cache
    if _cache is None or refresh:
        try:
            _cache = _scan()
        except Exception:
            logging.exception("Application scan failed")
            _cache = _cache or []
    return list(_cache)


def _scan() -> list[AppEntry]:
    if not IS_WINDOWS:
        return []
    found: dict[str, AppEntry] = {}
    for entry in _start_menu_apps():
        found.setdefault(entry.exe, entry)
    for entry in _running_apps():
        found.setdefault(entry.exe, entry)
    return sorted(found.values(), key=lambda item: item.name.lower())


def _guid(text: str) -> GUID:
    value = uuid.UUID(text)
    result = GUID()
    result.Data1 = value.time_low
    result.Data2 = value.time_mid
    result.Data3 = value.time_hi_version
    for index, byte in enumerate(value.bytes[8:]):
        result.Data4[index] = byte
    return result


def _method(ptr, index: int, argtypes):
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return proto(table[index])


def _create_shelllink(clsid, shelllink_iid) -> ctypes.c_void_p | None:
    link = ctypes.c_void_p()
    if _ole32.CoCreateInstance(
        ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
        ctypes.byref(shelllink_iid), ctypes.byref(link),
    ) != 0 or not link:
        return None
    return link


def _shelllink_path(link, lnk: Path, persist_iid) -> str:
    persist = ctypes.c_void_p()
    if _method(link, 0, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)])(
        link, ctypes.byref(persist_iid), ctypes.byref(persist)
    ) != 0 or not persist:
        return ""
    try:
        if _method(persist, 5, [wintypes.LPCWSTR, wintypes.DWORD])(persist, str(lnk), 0) != 0:
            return ""
        buffer = ctypes.create_unicode_buffer(1024)
        if _method(link, 3, [wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD])(
            link, buffer, 1024, None, 0
        ) != 0:
            return ""
        return buffer.value
    finally:
        _method(persist, 2, [])(persist)


def _start_menu_apps() -> list[AppEntry]:
    dirs = []
    for base in (os.environ.get("ProgramData"), os.environ.get("APPDATA")):
        if base:
            candidate = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if candidate.is_dir():
                dirs.append(candidate)
    if not dirs:
        return []
    windir = (os.environ.get("WINDIR") or r"C:\Windows").lower() + "\\"

    initialized = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED) in (0, S_FALSE)

    clsid = _guid(_CLSID_SHELLLINK)
    shelllink_iid = _guid(_IID_SHELLLINKW)
    persist_iid = _guid(_IID_PERSISTFILE)
    entries: list[AppEntry] = []
    seen: set[str] = set()
    link = _create_shelllink(clsid, shelllink_iid)
    try:
        if link is None:
            return []
        for folder in dirs:
            for lnk in folder.rglob("*.lnk"):
                name = lnk.stem.strip()
                if not name or name.lower() in GENERIC_LINK_NAMES:
                    continue
                try:
                    target = _shelllink_path(link, lnk, persist_iid)
                except Exception:
                    continue
                if not target:
                    continue
                target_path = Path(target)
                if target_path.suffix.lower() != ".exe":
                    continue
                exe = target_path.name.lower()
                if exe in SKIP_EXES or exe.startswith("unins") or exe in seen:
                    continue
                if str(target_path).lower().startswith(windir):
                    continue
                seen.add(exe)
                entries.append(AppEntry(name=name, exe=exe, path=str(target_path)))
    finally:
        if link is not None:
            _method(link, 2, [])(link)
        if initialized:
            _ole32.CoUninitialize()
    return entries


def _visible_titles() -> dict[int, str]:
    titles: dict[int, str] = {}

    def callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value and pid.value not in titles:
            buffer = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if title:
                titles[pid.value] = title
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return titles


def _running_apps() -> list[AppEntry]:
    titles = _visible_titles()
    windir = (os.environ.get("WINDIR") or r"C:\Windows").lower() + "\\"
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (0, -1, None):
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    results: list[AppEntry] = []
    seen: set[str] = set()
    try:
        if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            exe = entry.szExeFile.lower()
            pid = entry.th32ProcessID
            if exe not in SKIP_EXES and exe not in seen and pid in titles:
                path = native.process_image_path(pid)
                if path and not path.lower().startswith(windir):
                    seen.add(exe)
                    name = titles[pid][:60].strip()
                    results.append(AppEntry(name=name, exe=exe, path=path))
            if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(snapshot)
    return results
