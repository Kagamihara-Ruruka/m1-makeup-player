from __future__ import annotations

import os
from pathlib import Path

from .config import AppConfig
from .local_settings import LOCAL_SETTINGS_PATH, load_local_settings
from .progress import ProgressStore
from .resolved_url_cache import ResolvedUrlCache
from .runtime_config import schedule_view_url_with_source
from .writeback import WritebackOutbox


def collect_settings_status(config: AppConfig, local_settings_path: str | Path | None = None) -> dict[str, object]:
    settings_path = Path(local_settings_path) if local_settings_path is not None else LOCAL_SETTINGS_PATH
    local_settings = load_local_settings(settings_path)
    token_source, token = notion_token_with_source(local_settings.notion_token, settings_path)
    schedule_view_url, schedule_view_source = schedule_view_url_with_source(local_settings, settings_path)
    completion_id = local_settings.completion_database_id
    has_token = bool(token)
    has_completion_id = bool(completion_id)

    url_cache = ResolvedUrlCache(config.resolved_url_cache)
    url_cache.load()

    store = ProgressStore(config.progress_cache)
    store.load()

    outbox = WritebackOutbox(config.writeback_outbox)
    planned_sync_backend = "official_notion_api" if has_token else "notion_mcp_fallback"
    next_actions = []
    if not has_token:
        next_actions.append("run scripts/set_token.py to enable official Notion API sync and attachment URL resolution")
    if not has_completion_id:
        if has_token:
            next_actions.append("run scripts/bootstrap_completion_database.py --parent-from-schedule --apply --save")
        next_actions.append("run scripts/set_completion_database.py with the completion data source URL or id")
    if has_token and has_completion_id:
        next_actions.append("run scripts/scan_schedule.py --max-pages 5 --timeout-sec 45 --json, then scripts/resolve_sources.py --show-reason")

    return {
        "settings_path": str(settings_path),
        "notion_token": {
            "status": "configured" if has_token else "missing",
            "source": token_source,
            "redacted": redact_secret(token),
        },
        "sync_backend": planned_sync_backend,
        "planned_sync_backend": planned_sync_backend,
        "last_sync": store.metadata.to_json(),
        "schedule_view": {
            "status": "configured" if schedule_view_url else "missing",
            "source": schedule_view_source,
            "redacted_id": redact_identifier(schedule_view_url),
        },
        "attachment_resolution": "enabled" if has_token else "disabled_missing_token",
        "completion_data_source": {
            "status": "configured" if has_completion_id else "missing",
            "redacted_id": redact_identifier(completion_id),
        },
        "writeback_mode": "apply_possible" if has_token and has_completion_id else "dry_run_only",
        "cache_records": len(store.records),
        "resolved_url_cache": url_cache.stats(),
        "queued_writeback_events": outbox.count_events(),
        "next_actions": next_actions,
    }


def notion_token_with_source(local_token: str | None, local_settings_path: str | Path | None = None) -> tuple[str, str | None]:
    if os.environ.get("M1_NOTION_TOKEN"):
        return "environment:M1_NOTION_TOKEN", os.environ["M1_NOTION_TOKEN"]
    if os.environ.get("NOTION_TOKEN"):
        return "environment:NOTION_TOKEN", os.environ["NOTION_TOKEN"]
    if local_token:
        return str(Path(local_settings_path) if local_settings_path is not None else LOCAL_SETTINGS_PATH), local_token
    return "missing", None


def redact_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def redact_identifier(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"
