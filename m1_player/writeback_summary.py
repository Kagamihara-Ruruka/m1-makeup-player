from __future__ import annotations

from dataclasses import dataclass

from .writeback import WritebackEvent, WritebackOutbox
from .writeback_sink import CompletionWritebackSink


@dataclass(frozen=True)
class WritebackOutboxSummary:
    event_count: int
    writeback_mode: str
    lines: tuple[str, ...]

    def to_text(self) -> str:
        return "\n".join(self.lines)


def collect_writeback_outbox_summary(outbox: WritebackOutbox, sink: CompletionWritebackSink) -> WritebackOutboxSummary:
    events = outbox.load_events()
    mode = "apply_possible" if sink.configured() else "dry_run_only"
    lines = [f"完成回寫：{mode_label(mode)}", f"待送出事件：{len(events)}"]
    for event in sorted(events, key=event_sort_key):
        lines.append(event_summary_line(event))
    return WritebackOutboxSummary(
        event_count=len(events),
        writeback_mode=mode,
        lines=tuple(lines),
    )


def mode_label(mode: str) -> str:
    labels = {
        "apply_possible": "可送出到 Notion",
        "dry_run_only": "乾跑模式",
    }
    return labels.get(mode, mode)


def event_summary_line(event: WritebackEvent) -> str:
    date = event.course_date or "no-date"
    return (
        f"- {date} P{event.segment_index:02d} {event.video_name} "
        f"{event.progress_percent:.1f}% status={event.status}"
    )


def event_sort_key(event: WritebackEvent) -> tuple[str, int, str, str]:
    return (
        event.course_date or "",
        event.segment_index,
        event.video_name,
        event.stable_key,
    )
