from __future__ import annotations

from dataclasses import dataclass

from .models import LessonStatus, PlaybackRecord


@dataclass(frozen=True)
class ProgressOverview:
    total_records: int
    completed_count: int
    in_progress_count: int
    not_started_count: int
    review_count: int
    missing_count: int
    queued_writebacks: int
    average_progress_percent: float
    completed_percent: float

    def to_json(self) -> dict[str, object]:
        return {
            "total_records": self.total_records,
            "completed_count": self.completed_count,
            "in_progress_count": self.in_progress_count,
            "not_started_count": self.not_started_count,
            "review_count": self.review_count,
            "missing_count": self.missing_count,
            "queued_writebacks": self.queued_writebacks,
            "average_progress_percent": self.average_progress_percent,
            "completed_percent": self.completed_percent,
        }

    def to_text(self) -> str:
        return "\n".join(
            (
                "補課總覽",
                f"影片總數：{self.total_records}",
                f"完成：{self.completed_count}（{self.completed_percent:.2f}%）",
                f"補課中：{self.in_progress_count}",
                f"未開始：{self.not_started_count}",
                f"需重看：{self.review_count}",
                f"來源消失：{self.missing_count}",
                f"平均進度：{self.average_progress_percent:.2f}%",
                f"待回寫完成紀錄：{self.queued_writebacks}",
            )
        )


def collect_progress_overview(records: list[PlaybackRecord], queued_writebacks: int = 0) -> ProgressOverview:
    total = len(records)
    completed = count_status(records, LessonStatus.COMPLETED)
    in_progress = count_status(records, LessonStatus.IN_PROGRESS)
    not_started = count_status(records, LessonStatus.NOT_STARTED)
    review = count_status(records, LessonStatus.REVIEW)
    missing = count_status(records, LessonStatus.MISSING)
    average_progress = 0.0
    if total:
        average_progress = round(sum(clamp_percent(record.progress_percent) for record in records) / total, 2)
    completed_percent = round(completed / total * 100.0, 2) if total else 0.0
    return ProgressOverview(
        total_records=total,
        completed_count=completed,
        in_progress_count=in_progress,
        not_started_count=not_started,
        review_count=review,
        missing_count=missing,
        queued_writebacks=max(0, int(queued_writebacks)),
        average_progress_percent=average_progress,
        completed_percent=completed_percent,
    )


def count_status(records: list[PlaybackRecord], status: LessonStatus) -> int:
    return sum(1 for record in records if record.status == status)


def clamp_percent(value: float) -> float:
    return min(100.0, max(0.0, float(value)))
