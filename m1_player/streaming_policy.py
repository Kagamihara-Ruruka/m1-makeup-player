from __future__ import annotations

from dataclasses import dataclass

from .models import PlaybackRecord
from .resolved_url_cache import ResolvedUrlCache
from .video_source import is_http_stream_url, parse_video_source


@dataclass(frozen=True)
class StreamingSourcePolicyRow:
    stable_key: str
    course_date: str | None
    segment_index: int
    video_name: str
    source_kind: str
    policy_status: str
    allowed: bool
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "stable_key": self.stable_key,
            "course_date": self.course_date,
            "segment_index": self.segment_index,
            "video_name": self.video_name,
            "source_kind": self.source_kind,
            "policy_status": self.policy_status,
            "allowed": self.allowed,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StreamingCachePolicyRow:
    key: str
    source_kind: str
    filename_hint: str | None
    policy_status: str
    allowed: bool
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "key": self.key,
            "source_kind": self.source_kind,
            "filename_hint": self.filename_hint,
            "policy_status": self.policy_status,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def audit_streaming_sources(records: list[PlaybackRecord]) -> list[StreamingSourcePolicyRow]:
    rows: list[StreamingSourcePolicyRow] = []
    for record in records:
        source = parse_video_source(record.source_ref)
        if source.playable_url and is_http_stream_url(source.playable_url):
            policy_status = "direct_http_stream"
            allowed = True
            reason = "source is an http/https stream URL"
        elif source.source_kind == "notion_attachment_marker":
            policy_status = "notion_attachment_stream_resolution"
            allowed = True
            reason = "source is a Notion attachment marker; resolver may fetch a short-lived stream URL"
        else:
            policy_status = "blocked_non_stream_source"
            allowed = False
            reason = f"non-stream source is not allowed by MVP policy: {source.source_kind}"
        rows.append(
            StreamingSourcePolicyRow(
                stable_key=record.stable_key,
                course_date=record.course_date,
                segment_index=record.segment_index,
                video_name=record.video_name,
                source_kind=source.source_kind,
                policy_status=policy_status,
                allowed=allowed,
                reason=reason,
            )
        )
    return sorted(rows, key=lambda item: (item.course_date or "", item.segment_index, item.video_name))


def audit_resolved_url_cache(cache: ResolvedUrlCache) -> list[StreamingCachePolicyRow]:
    rows: list[StreamingCachePolicyRow] = []
    cache.load()
    for entry in sorted(cache.entries.values(), key=lambda item: item.key):
        if is_http_stream_url(entry.playable_url):
            policy_status = "short_lived_http_stream"
            allowed = True
            reason = "resolved URL cache stores a stream URL reference only"
        else:
            policy_status = "blocked_cached_non_stream_url"
            allowed = False
            reason = "resolved URL cache must not store local paths or non-http media locations"
        rows.append(
            StreamingCachePolicyRow(
                key=entry.key,
                source_kind=entry.source_kind,
                filename_hint=entry.filename_hint,
                policy_status=policy_status,
                allowed=allowed,
                reason=reason,
            )
        )
    return rows


def summarize_streaming_policy(
    source_rows: list[StreamingSourcePolicyRow],
    cache_rows: list[StreamingCachePolicyRow],
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in source_rows:
        summary[row.policy_status] = summary.get(row.policy_status, 0) + 1
    for row in cache_rows:
        summary[row.policy_status] = summary.get(row.policy_status, 0) + 1
    return summary


def streaming_policy_passes(
    source_rows: list[StreamingSourcePolicyRow],
    cache_rows: list[StreamingCachePolicyRow],
) -> bool:
    return all(row.allowed for row in source_rows) and all(row.allowed for row in cache_rows)
