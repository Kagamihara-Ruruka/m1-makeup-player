from __future__ import annotations

from pathlib import Path

from .local_settings import LOCAL_SETTINGS_PATH, LocalSettings, load_local_settings, save_local_settings
from .notion_api import extract_notion_id


def set_notion_token(token: str, path: str | Path | None = None) -> Path:
    value = token.strip()
    if not value:
        raise ValueError("notion token is empty")
    settings_path = Path(path) if path is not None else LOCAL_SETTINGS_PATH
    current = load_local_settings(settings_path)
    save_local_settings(
        LocalSettings(
            notion_token=value,
            completion_database_id=current.completion_database_id,
            schedule_view_url=current.schedule_view_url,
        ),
        settings_path,
    )
    return settings_path


def set_completion_data_source(value: str, path: str | Path | None = None) -> Path:
    data_source_id = normalize_notion_id_or_text(value)
    if not data_source_id:
        raise ValueError("completion data source is empty")
    settings_path = Path(path) if path is not None else LOCAL_SETTINGS_PATH
    current = load_local_settings(settings_path)
    save_local_settings(
        LocalSettings(
            notion_token=current.notion_token,
            completion_database_id=data_source_id,
            schedule_view_url=current.schedule_view_url,
        ),
        settings_path,
    )
    return settings_path


def set_schedule_view_url(value: str, path: str | Path | None = None) -> Path:
    schedule_view_url = value.strip()
    if not schedule_view_url:
        raise ValueError("schedule view URL is empty")
    settings_path = Path(path) if path is not None else LOCAL_SETTINGS_PATH
    current = load_local_settings(settings_path)
    save_local_settings(
        LocalSettings(
            notion_token=current.notion_token,
            completion_database_id=current.completion_database_id,
            schedule_view_url=schedule_view_url,
        ),
        settings_path,
    )
    return settings_path


def normalize_notion_id_or_text(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    try:
        return extract_notion_id(text)
    except ValueError:
        return text
