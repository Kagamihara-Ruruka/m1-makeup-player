from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import PlaybackRecord
from .subtitle_resolver import SubtitleResolver


@dataclass(frozen=True)
class SubtitleManifestRow:
    stable_key: str
    course_date: str | None
    segment_index: int
    video_name: str
    status: str
    preferred_markdown_path: str
    preferred_srt_path: str
    preferred_vtt_path: str
    existing_path: str | None
    candidates: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "stable_key": self.stable_key,
            "course_date": self.course_date,
            "segment_index": self.segment_index,
            "video_name": self.video_name,
            "status": self.status,
            "preferred_markdown_path": self.preferred_markdown_path,
            "preferred_srt_path": self.preferred_srt_path,
            "preferred_vtt_path": self.preferred_vtt_path,
            "existing_path": self.existing_path,
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True)
class SubtitlePlaceholderWriteResult:
    written: tuple[str, ...]
    skipped_existing: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "written": list(self.written),
            "skipped_existing": list(self.skipped_existing),
        }


def build_subtitle_manifest(records: list[PlaybackRecord], subtitle_dir: str | Path) -> list[SubtitleManifestRow]:
    resolver = SubtitleResolver(subtitle_dir)
    rows: list[SubtitleManifestRow] = []
    for record in sorted(records, key=lambda item: (item.course_date or "", item.segment_index, item.video_name)):
        candidates = resolver.candidates_for(record)
        existing_path = next((candidate for candidate in candidates if candidate.exists()), None)
        sidecar_base = sidecar_base_path(record, subtitle_dir)
        rows.append(
            SubtitleManifestRow(
                stable_key=record.stable_key,
                course_date=record.course_date,
                segment_index=record.segment_index,
                video_name=record.video_name,
                status="found" if existing_path else "missing",
                preferred_markdown_path=str(sidecar_base.with_suffix(".md")),
                preferred_srt_path=str(sidecar_base.with_suffix(".srt")),
                preferred_vtt_path=str(sidecar_base.with_suffix(".vtt")),
                existing_path=str(existing_path) if existing_path else None,
                candidates=tuple(str(candidate) for candidate in candidates),
            )
        )
    return rows


def write_missing_markdown_placeholders(
    records: list[PlaybackRecord],
    subtitle_dir: str | Path,
    *,
    overwrite: bool = False,
) -> SubtitlePlaceholderWriteResult:
    written: list[str] = []
    skipped: list[str] = []
    subtitle_root = Path(subtitle_dir)
    subtitle_root.mkdir(parents=True, exist_ok=True)
    for row in build_subtitle_manifest(records, subtitle_root):
        target = Path(row.preferred_markdown_path)
        existing = Path(row.existing_path) if row.existing_path else None
        if existing and (existing != target or not overwrite):
            skipped.append(row.existing_path or str(target))
            continue
        if target.exists() and not overwrite:
            skipped.append(str(target))
            continue
        target.write_text(markdown_placeholder(row), encoding="utf-8", newline="\n")
        written.append(str(target))
    return SubtitlePlaceholderWriteResult(tuple(written), tuple(skipped))


def sidecar_base_path(record: PlaybackRecord, subtitle_dir: str | Path) -> Path:
    safe_key = record.stable_key.replace(":", "_")
    return Path(subtitle_dir) / safe_key


def markdown_placeholder(row: SubtitleManifestRow) -> str:
    title = row.video_name
    course_date = row.course_date or "no-date"
    return (
        f"# {title}\n\n"
        f"- stable_key: `{row.stable_key}`\n"
        f"- course_date: `{course_date}`\n"
        f"- segment_index: `{row.segment_index}`\n\n"
        "[00:00:00] 待補字幕\n"
    )
