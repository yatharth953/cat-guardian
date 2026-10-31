"""Double-click launcher for Cat Guardian.

Windows opens .py files with the system Python, which does not know about the
project's dependencies. This bootstrap re-runs itself inside the project
virtualenv (without a console window) and reports startup errors in a dialog
instead of closing silently.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_SCRIPTS = ROOT / ".venv" / "Scripts"
LAUNCH_ENV = "CAT_GUARDIAN_VENV_LAUNCH"


def _alert(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Cat Guardian", 0x10)
    except Exception:
        pass


def _venv_python() -> Path | None:
    for name in ("pythonw.exe", "python.exe"):
        candidate = VENV_SCRIPTS / name
        if candidate.exists():
            return candidate
    return None


def _relaunch_in_venv() -> bool:
    if os.environ.get(LAUNCH_ENV):
        return False
    try:
        in_venv = Path(sys.executable).resolve().parent == VENV_SCRIPTS.resolve()
    except Exception:
        in_venv = False
    if in_venv:
        return False
    exe = _venv_python()
    if exe is None:
        return False
    env = {**os.environ, LAUNCH_ENV: "1"}
    subprocess.Popen(
        [str(exe), str(Path(__file__).resolve())],
        cwd=str(ROOT),
        env=env,
        close_fds=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    return True


def _report(detail: str) -> None:
    try:
        from cat_guardian.settings import LOG_PATH

        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"\nLauncher failure:\n{detail}\n")
    except Exception:
        pass
    _alert("Cat Guardian could not start.\n\n" + detail.strip()[-1200:])


def main() -> None:
    if _relaunch_in_venv():
        return
    sys.path.insert(0, str(ROOT))
    try:
        from cat_guardian.__main__ import main as run_app
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            _alert(
                "Cat Guardian needs its dependencies.\n\n"
                "Open PowerShell in this folder and run:\n\n"
                "py -m venv .venv\n"
                r".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
            )
        else:
            _report(traceback.format_exc())
        return
    except Exception:
        _report(traceback.format_exc())
        return
    try:
        run_app()
    except Exception:
        _report(traceback.format_exc())


if __name__ == "__main__":
    main()
