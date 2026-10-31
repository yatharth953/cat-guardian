from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from .appearance import DEFAULT_CAT_COLOR, normalize_color

APP_NAME = "Cat Guardian"

MUSIC_STYLES = ("auto", "chill", "groovy", "hype", "minimal", "off")

DEFAULT_SETTINGS = {
    "enabled": True,
    "blocked_domains": ["instagram.com"],
    "blocked_apps": [],
    "pet_name": "Miso",
    "close_tabs": True,
    "close_apps": False,
    "cat_pos": None,
    "cat_color": DEFAULT_CAT_COLOR,
    "sleep_delay": 8.0,
    "sleep_zzz": True,
    "paste_mode": False,
    "paste_pickup": True,
    "paste_other_apps": True,
    "paste_images": True,
    "music_enabled": True,
    "music_style": "auto",
    "music_dance_catalog": True,
    "music_intensity": 55,
    "music_sensitivity": 55,
    "music_delay": 6.0,
    "music_sfx": False,
    "music_always_apps": [],
    "music_always_domains": [],
}


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "CatGuardian"
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = data_dir()
SETTINGS_PATH = DATA_DIR / "settings.json"
LOG_PATH = DATA_DIR / "cat_guardian.log"


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        save_settings(dict(DEFAULT_SETTINGS))
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        merged = {**DEFAULT_SETTINGS, **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
        merged["blocked_domains"] = normalize_domains(merged.get("blocked_domains"))
        merged["blocked_apps"] = normalize_apps(merged.get("blocked_apps"))
        merged["cat_pos"] = valid_pos(merged.get("cat_pos"))
        merged["cat_color"] = normalize_color(merged.get("cat_color"))
        merged["sleep_zzz"] = bool(merged.get("sleep_zzz", True))
        merged["sleep_delay"] = valid_sleep_delay(merged.get("sleep_delay"))
        merged["paste_mode"] = bool(merged.get("paste_mode", False))
        merged["paste_pickup"] = bool(merged.get("paste_pickup", True))
        merged["paste_other_apps"] = bool(merged.get("paste_other_apps", True))
        merged["paste_images"] = bool(merged.get("paste_images", True))
        merged["music_enabled"] = bool(merged.get("music_enabled", True))
        merged["music_style"] = valid_music_style(merged.get("music_style"))
        merged["music_dance_catalog"] = bool(merged.get("music_dance_catalog", True))
        merged["music_intensity"] = valid_percent(merged.get("music_intensity"), 55)
        merged["music_sensitivity"] = valid_percent(merged.get("music_sensitivity"), 55)
        merged["music_delay"] = valid_music_delay(merged.get("music_delay"))
        merged["music_sfx"] = bool(merged.get("music_sfx", False))
        merged["music_always_apps"] = normalize_apps(merged.get("music_always_apps"))
        merged["music_always_domains"] = normalize_domains(merged.get("music_always_domains"))
        return merged
    except Exception:
        logging.exception("Could not read settings; using defaults")
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def normalize_domains(domains) -> list[str]:
    clean = []
    for item in domains or []:
        value = str(item).strip().lower()
        value = value.removeprefix("https://").removeprefix("http://").split("/")[0].strip(".")
        if value and value not in clean and " " not in value:
            clean.append(value)
    return clean


def normalize_apps(apps) -> list[str]:
    clean = []
    for item in apps or []:
        value = str(item).strip().lower().replace("\\", "/").split("/")[-1]
        if value and value not in clean and " " not in value:
            clean.append(value)
    return clean


def domain_matches(url: str, blocked: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in blocked)


def valid_pos(value) -> list[int] | None:
    try:
        x, y = int(value[0]), int(value[1])
        return [x, y]
    except Exception:
        return None


def valid_sleep_delay(value) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 8.0
    return round(min(max(seconds, 3.0), 120.0), 1)


def valid_music_style(value) -> str:
    style = str(value or "auto").lower()
    return style if style in MUSIC_STYLES else "auto"


def valid_percent(value, default: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return min(max(number, 0), 100)


def valid_music_delay(value) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 6.0
    return round(min(max(seconds, 2.0), 30.0), 1)
