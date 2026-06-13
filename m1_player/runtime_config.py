from __future__ import annotations

import os
from pathlib import Path

from .config import DEFAULT_SCHEDULE_VIEW_URL, AppConfig
from .local_settings import LOCAL_SETTINGS_PATH, LocalSettings, load_local_settings


def load_app_config(local_settings_path: str | Path | None = None) -> AppConfig:
    schedule_view_url, _source = schedule_view_url_with_source(local_settings_path=local_settings_path)
    return AppConfig(schedule_view_url=schedule_view_url)


def schedule_view_url_with_source(
    settings: LocalSettings | None = None,
    local_settings_path: str | Path | None = None,
) -> tuple[str, str]:
    env_value = os.environ.get("M1_SCHEDULE_VIEW_URL")
    if env_value and env_value.strip():
        return env_value.strip(), "environment:M1_SCHEDULE_VIEW_URL"
    settings_path = Path(local_settings_path) if local_settings_path is not None else LOCAL_SETTINGS_PATH
    settings = settings or load_local_settings(settings_path)
    if settings.schedule_view_url:
        return settings.schedule_view_url, str(settings_path)
    return DEFAULT_SCHEDULE_VIEW_URL, "default"
