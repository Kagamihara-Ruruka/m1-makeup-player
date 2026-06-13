from __future__ import annotations

from dataclasses import dataclass

from .models import PlaybackRecord
from .video_source import parse_video_source, permission_block_id


@dataclass(frozen=True)
class SourceReadinessRow:
    stable_key: str
    course_date: str | None
    segment_index: int
    video_name: str
    source_kind: str
    readiness: str
    resolver_required: bool
    has_permission_block: bool
    block_id: str | None
    reason: str

    def to_json(self, *, expose_block_id: bool = False) -> dict[str, object]:
        return {
            "stable_key": self.stable_key,
            "course_date": self.course_date,
            "segment_index": self.segment_index,
            "video_name": self.video_name,
            "source_kind": self.source_kind,
            "readiness": self.readiness,
            "resolver_required": self.resolver_required,
            "has_permission_block": self.has_permission_block,
            "block_id": self.block_id if expose_block_id else redact_identifier(self.block_id),
            "reason": self.reason,
        }


def audit_source_readiness(records: list[PlaybackRecord]) -> list[SourceReadinessRow]:
    rows: list[SourceReadinessRow] = []
    for record in records:
        source = parse_video_source(record.source_ref)
        block_id = permission_block_id(source)
        if source.playable_url:
            readiness = "direct_playable"
            reason = "source is already an http/https URL"
        elif source.source_kind == "notion_attachment_marker" and block_id:
            readiness = "ready_for_token_resolution"
            reason = "Notion attachment marker includes permission block id"
        elif source.source_kind == "notion_attachment_marker":
            readiness = "missing_permission_block"
            reason = "Notion attachment marker is missing permissionRecord.id"
        else:
            readiness = "unsupported_source_shape"
            reason = f"unsupported source kind: {source.source_kind}"
        rows.append(
            SourceReadinessRow(
                stable_key=record.stable_key,
                course_date=record.course_date,
                segment_index=record.segment_index,
                video_name=record.video_name,
                source_kind=source.source_kind,
                readiness=readiness,
                resolver_required=source.requires_resolution,
                has_permission_block=bool(block_id),
                block_id=block_id,
                reason=reason,
            )
        )
    return sorted(rows, key=lambda item: (item.course_date or "", item.segment_index, item.video_name))


def summarize_source_readiness(rows: list[SourceReadinessRow]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        summary[row.readiness] = summary.get(row.readiness, 0) + 1
    return summary


def source_readiness_passes(rows: list[SourceReadinessRow]) -> bool:
    return all(row.readiness in {"direct_playable", "ready_for_token_resolution"} for row in rows)


def redact_identifier(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"
