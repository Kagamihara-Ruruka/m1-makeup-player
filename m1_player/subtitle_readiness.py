from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import PlaybackRecord
from .subtitle_resolver import SubtitleResolver


@dataclass(frozen=True)
class SubtitleReadinessRow:
    stable_key: str
    course_date: str | None
    segment_index: int
    video_name: str
    status: str
    subtitle_path: str | None
    cue_count: int
    candidates: tuple[str, ...]
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "stable_key": self.stable_key,
            "course_date": self.course_date,
            "segment_index": self.segment_index,
            "video_name": self.video_name,
            "status": self.status,
            "subtitle_path": self.subtitle_path,
            "cue_count": self.cue_count,
            "candidates": list(self.candidates),
            "reason": self.reason,
        }


def audit_subtitle_readiness(records: list[PlaybackRecord], subtitle_dir: str | Path) -> list[SubtitleReadinessRow]:
    resolver = SubtitleResolver(subtitle_dir)
    rows = []
    for record in sorted(records, key=lambda item: (item.course_date or "", item.segment_index, item.video_name)):
        candidates = tuple(str(candidate) for candidate in resolver.candidates_for(record))
        try:
            path, cues = resolver.load_for(record)
        except Exception as exc:  # noqa: BLE001 - readiness should report malformed local subtitle files.
            rows.append(
                SubtitleReadinessRow(
                    stable_key=record.stable_key,
                    course_date=record.course_date,
                    segment_index=record.segment_index,
                    video_name=record.video_name,
                    status="error",
                    subtitle_path=None,
                    cue_count=0,
                    candidates=candidates,
                    reason=str(exc),
                )
            )
            continue
        if path is None:
            rows.append(
                SubtitleReadinessRow(
                    stable_key=record.stable_key,
                    course_date=record.course_date,
                    segment_index=record.segment_index,
                    video_name=record.video_name,
                    status="missing",
                    subtitle_path=None,
                    cue_count=0,
                    candidates=candidates,
                    reason="no local subtitle file matched candidate paths",
                )
            )
            continue
        status = "found" if cues else "empty"
        reason = "subtitle cues loaded" if cues else "subtitle file exists but has no cues"
        rows.append(
            SubtitleReadinessRow(
                stable_key=record.stable_key,
                course_date=record.course_date,
                segment_index=record.segment_index,
                video_name=record.video_name,
                status=status,
                subtitle_path=str(path),
                cue_count=len(cues),
                candidates=candidates,
                reason=reason,
            )
        )
    return rows


def summarize_subtitle_readiness(rows: list[SubtitleReadinessRow]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        summary[row.status] = summary.get(row.status, 0) + 1
    return summary


def subtitle_readiness_passes(rows: list[SubtitleReadinessRow]) -> bool:
    return bool(rows) and all(row.status == "found" for row in rows)
