from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import STATE_DIR


LOCAL_SETTINGS_PATH = STATE_DIR / "local_settings.json"


@dataclass(frozen=True)
class LocalSettings:
    notion_token: str | None = None
    completion_database_id: str | None = None
    schedule_view_url: str | None = None


def load_local_settings(path: str | Path = LOCAL_SETTINGS_PATH) -> LocalSettings:
    settings_path = Path(path)
    if not settings_path.exists():
        return LocalSettings()
    data = json.loads(settings_path.read_text(encoding="utf-8", errors="strict"))
    if not isinstance(data, dict):
        return LocalSettings()
    return LocalSettings(
        notion_token=_nonempty(data.get("notion_token")),
        completion_database_id=_nonempty(data.get("completion_database_id")),
        schedule_view_url=_nonempty(data.get("schedule_view_url")),
    )


def save_local_settings(settings: LocalSettings, path: str | Path = LOCAL_SETTINGS_PATH) -> None:
    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "notion_token": settings.notion_token,
        "completion_database_id": settings.completion_database_id,
        "schedule_view_url": settings.schedule_view_url,
    }
    settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _nonempty(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
