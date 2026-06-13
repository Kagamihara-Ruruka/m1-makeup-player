from __future__ import annotations

import html
import json
from dataclasses import dataclass
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class VideoSourceInfo:
    raw_ref: str
    playable_url: str | None
    filename_hint: str | None
    source_kind: str
    requires_resolution: bool
    attachment_source: str | None = None
    permission_record: dict[str, object] | None = None

    @property
    def is_playable(self) -> bool:
        return bool(self.playable_url)


def parse_video_source(raw_ref: str) -> VideoSourceInfo:
    value = html.unescape(raw_ref)
    parsed = urlparse(value)
    if is_http_stream_url(value):
        return VideoSourceInfo(
            raw_ref=raw_ref,
            playable_url=value,
            filename_hint=_filename_from_url(value),
            source_kind="http",
            requires_resolution=False,
        )
    marker = _parse_notion_file_marker(value)
    if marker:
        attachment_source = str(marker.get("source", ""))
        filename = _filename_from_attachment_source(attachment_source)
        return VideoSourceInfo(
            raw_ref=raw_ref,
            playable_url=None,
            filename_hint=filename,
            source_kind="notion_attachment_marker",
            requires_resolution=True,
            attachment_source=attachment_source,
            permission_record=marker.get("permissionRecord") if isinstance(marker.get("permissionRecord"), dict) else None,
        )
    return VideoSourceInfo(
        raw_ref=raw_ref,
        playable_url=None,
        filename_hint=None,
        source_kind=parsed.scheme or "unknown",
        requires_resolution=True,
    )


def _parse_notion_file_marker(value: str) -> dict[str, object] | None:
    if not value.startswith("file://"):
        return None
    payload = unquote(value.removeprefix("file://"))
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _filename_from_url(value: str) -> str | None:
    tail = unquote(urlparse(value).path.rstrip("/").split("/")[-1])
    return tail or None


def _filename_from_attachment_source(value: str) -> str | None:
    if value.startswith("attachment:"):
        parts = value.split(":", 2)
        if len(parts) == 3:
            return parts[2]
    return None


def permission_block_id(source: VideoSourceInfo) -> str | None:
    record = source.permission_record or {}
    value = record.get("id")
    return str(value) if value else None


def is_http_stream_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}
