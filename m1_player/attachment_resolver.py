from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from .local_settings import load_local_settings
from .resolved_url_cache import ResolvedUrlCache
from .video_source import VideoSourceInfo, permission_block_id


NOTION_API_VERSION = "2026-03-11"


@dataclass(frozen=True)
class AttachmentResolution:
    playable_url: str | None
    status: str
    reason: str
    expires_at: str | None = None
    cache_hit: bool = False

    @property
    def resolved(self) -> bool:
        return bool(self.playable_url)


class NotionAttachmentResolver:
    def __init__(
        self,
        token: str | None = None,
        cache: ResolvedUrlCache | None = None,
        block_fetcher: Callable[[str], dict[str, Any]] | None = None,
        timeout_sec: int = 30,
        local_settings_path: str | Path | None = None,
    ) -> None:
        settings_token = (
            load_local_settings(local_settings_path).notion_token
            if local_settings_path is not None
            else load_local_settings().notion_token
        )
        self.token = (
            token
            or os.environ.get("M1_NOTION_TOKEN")
            or os.environ.get("NOTION_TOKEN")
            or settings_token
        )
        self.cache = cache
        self.block_fetcher = block_fetcher
        self.timeout_sec = timeout_sec

    def resolve(self, source: VideoSourceInfo) -> AttachmentResolution:
        if source.playable_url:
            return AttachmentResolution(source.playable_url, "resolved", "source already playable")
        if source.source_kind != "notion_attachment_marker":
            return AttachmentResolution(None, "unsupported", f"unsupported source kind: {source.source_kind}")
        if self.cache:
            self.cache.load()
            entry = self.cache.get_valid(source)
            if entry:
                return AttachmentResolution(
                    entry.playable_url,
                    "resolved_from_cache",
                    "resolved URL cache hit",
                    expires_at=entry.expires_at,
                    cache_hit=True,
                )
        if not self.token:
            return AttachmentResolution(None, "missing_token", "set M1_NOTION_TOKEN or NOTION_TOKEN to enable Notion API resolution")
        block_id = permission_block_id(source)
        if not block_id:
            return AttachmentResolution(None, "missing_block_id", "permission record does not contain a block id")
        try:
            payload = self._fetch_block_payload(block_id)
        except Exception as exc:  # noqa: BLE001 - resolver reports API boundary failures as data.
            return AttachmentResolution(None, "api_error", str(exc))
        url, expires_at = _find_notion_file_url(payload)
        if not url:
            return AttachmentResolution(None, "url_not_found", "Notion API response did not expose a file URL")
        if self.cache:
            self.cache.load()
            self.cache.put(source, url, expires_at)
            self.cache.save()
        return AttachmentResolution(url, "resolved", "resolved through Notion API block fetch", expires_at=expires_at)

    def _fetch_block_payload(self, block_id: str) -> dict[str, Any]:
        if self.block_fetcher:
            return self.block_fetcher(block_id)
        response = requests.get(
            f"https://api.notion.com/v1/blocks/{block_id}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_API_VERSION,
            },
            timeout=self.timeout_sec,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Notion API returned {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Notion API returned non-object JSON")
        return payload

def _find_notion_file_url(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, dict):
        object_type = value.get("type")
        if isinstance(object_type, str):
            typed = value.get(object_type)
            if isinstance(typed, dict):
                file_entry = typed.get("file")
                if isinstance(file_entry, dict) and isinstance(file_entry.get("url"), str):
                    return str(file_entry["url"]), str(file_entry["expiry_time"]) if file_entry.get("expiry_time") else None
                external_entry = typed.get("external")
                if isinstance(external_entry, dict) and isinstance(external_entry.get("url"), str):
                    return str(external_entry["url"]), None
        direct_url = value.get("url")
        if isinstance(direct_url, str) and direct_url.startswith(("http://", "https://")):
            return direct_url, str(value["expiry_time"]) if value.get("expiry_time") else None
        for child in value.values():
            found, expires_at = _find_notion_file_url(child)
            if found:
                return found, expires_at
    elif isinstance(value, list):
        for child in value:
            found, expires_at = _find_notion_file_url(child)
            if found:
                return found, expires_at
    return None, None
