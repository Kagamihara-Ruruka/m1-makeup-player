from __future__ import annotations

from dataclasses import dataclass

from .models import LessonStatus, PlaybackRecord
from .writeback import WritebackOutbox


@dataclass(frozen=True)
class CompletionQueueResult:
    status: str
    queued: bool
    message: str


def queue_completion_event(
    record: PlaybackRecord,
    outbox: WritebackOutbox,
    duration_sec: float | None = None,
) -> CompletionQueueResult:
    if record.status == LessonStatus.COMPLETED and record.completed_at:
        return CompletionQueueResult(
            status="already_completed",
            queued=False,
            message="record already completed",
        )

    record.mark_completed(duration_sec)
    if outbox.has_event("completed", record.stable_key):
        return CompletionQueueResult(
            status="already_queued",
            queued=False,
            message="completion event already queued",
        )

    outbox.append_completion(record)
    return CompletionQueueResult(
        status="queued",
        queued=True,
        message="completion event queued",
    )
