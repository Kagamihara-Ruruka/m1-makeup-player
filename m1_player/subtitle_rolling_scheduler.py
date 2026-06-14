from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from m1_player.subtitle import SubtitleCue


@dataclass(frozen=True)
class CoveredRange:
    start_sec: float
    end_sec: float


@dataclass(frozen=True)
class SubtitleWindowJob:
    job_id: str
    lane: str
    priority: int
    start_sec: float
    end_sec: float
    decode_start_sec: float
    decode_end_sec: float
    overlap_sec: float
    decode_worker_slot: int
    asr_device: str
    reason: str


@dataclass(frozen=True)
class RollingSubtitleSchedule:
    playback_position_sec: float
    playback_rate: float
    duration_sec: float
    window_sec: float
    overlap_sec: float
    headless_worker_count: int
    backfill_partition_count: int
    future_horizon_sec: float
    future_window_strategy: str
    future_base_window_sec: float
    future_max_window_sec: float
    jobs: tuple[SubtitleWindowJob, ...]

    @property
    def future_jobs(self) -> tuple[SubtitleWindowJob, ...]:
        return tuple(job for job in self.jobs if job.lane == "future_gpu")

    @property
    def backfill_jobs(self) -> tuple[SubtitleWindowJob, ...]:
        return tuple(job for job in self.jobs if job.lane == "backfill_cpu")

    def to_payload(self) -> dict[str, object]:
        return {
            "playback_position_sec": self.playback_position_sec,
            "playback_rate": self.playback_rate,
            "duration_sec": self.duration_sec,
            "window_sec": self.window_sec,
            "overlap_sec": self.overlap_sec,
            "headless_worker_count": self.headless_worker_count,
            "backfill_partition_count": self.backfill_partition_count,
            "future_horizon_sec": self.future_horizon_sec,
            "future_window_strategy": self.future_window_strategy,
            "future_base_window_sec": self.future_base_window_sec,
            "future_max_window_sec": self.future_max_window_sec,
            "future_job_count": len(self.future_jobs),
            "backfill_job_count": len(self.backfill_jobs),
            "jobs": [job_to_payload(job) for job in self.jobs],
        }


def plan_rolling_subtitle_windows(
    *,
    playback_position_sec: float,
    duration_sec: float,
    covered_ranges: Iterable[CoveredRange | tuple[float, float]] = (),
    playback_rate: float = 1.0,
    window_sec: float = 60.0,
    overlap_sec: float = 3.0,
    headless_worker_count: int = 3,
    future_horizon_sec: float | None = None,
    future_window_strategy: str = "fixed",
    future_base_window_sec: float | None = None,
    future_max_window_sec: float | None = None,
    backfill_partition_count: int | None = None,
    coverage_threshold: float = 0.85,
) -> RollingSubtitleSchedule:
    duration = max(0.0, float(duration_sec))
    position = clamp(float(playback_position_sec), 0.0, duration)
    window = max(1.0, float(window_sec))
    overlap = clamp(float(overlap_sec), 0.0, window / 2)
    playback = max(0.1, float(playback_rate))
    workers = max(1, int(headless_worker_count))
    partitions = max(1, int(backfill_partition_count or workers + 1))
    horizon = float(future_horizon_sec) if future_horizon_sec is not None else window * 4
    horizon = max(window, horizon)
    strategy = normalize_future_strategy(future_window_strategy)
    base_window = max(1.0, float(future_base_window_sec) if future_base_window_sec else window / 4)
    max_future_window = max(base_window, float(future_max_window_sec) if future_max_window_sec else window * 2)
    ranges = normalize_covered_ranges(covered_ranges)
    jobs: list[SubtitleWindowJob] = []
    jobs.extend(
        build_future_jobs(
            position=position,
            duration=duration,
            window=window,
            overlap=overlap,
            horizon=horizon,
            workers=workers,
            ranges=ranges,
            coverage_threshold=coverage_threshold,
            strategy=strategy,
            base_window=base_window,
            max_future_window=max_future_window,
        )
    )
    jobs.extend(
        build_backfill_jobs(
            position=position,
            duration=duration,
            overlap=overlap,
            workers=workers,
            partitions=partitions,
            ranges=ranges,
            coverage_threshold=coverage_threshold,
        )
    )
    jobs.sort(key=lambda job: (job.priority, job.start_sec, job.end_sec, job.job_id))
    return RollingSubtitleSchedule(
        playback_position_sec=round(position, 3),
        playback_rate=round(playback, 3),
        duration_sec=round(duration, 3),
        window_sec=round(window, 3),
        overlap_sec=round(overlap, 3),
        headless_worker_count=workers,
        backfill_partition_count=partitions,
        future_horizon_sec=round(horizon, 3),
        future_window_strategy=strategy,
        future_base_window_sec=round(base_window, 3),
        future_max_window_sec=round(max_future_window, 3),
        jobs=tuple(jobs),
    )


def covered_ranges_from_cues(cues: Iterable[SubtitleCue]) -> tuple[CoveredRange, ...]:
    return normalize_covered_ranges((cue.start_sec, cue.end_sec) for cue in cues)


def build_future_jobs(
    *,
    position: float,
    duration: float,
    window: float,
    overlap: float,
    horizon: float,
    workers: int,
    ranges: tuple[CoveredRange, ...],
    coverage_threshold: float,
    strategy: str,
    base_window: float,
    max_future_window: float,
) -> list[SubtitleWindowJob]:
    jobs: list[SubtitleWindowJob] = []
    end_limit = min(duration, position + horizon)
    if end_limit <= position:
        return jobs
    spans = build_future_spans(
        position=position,
        end_limit=end_limit,
        window=window,
        strategy=strategy,
        base_window=base_window,
        max_future_window=max_future_window,
    )
    for index, (start, end) in enumerate(spans):
        if end <= start or range_is_covered(start, end, ranges, coverage_threshold):
            continue
        jobs.append(
            make_job(
                job_id=f"future_{index:03d}",
                lane="future_gpu",
                priority=index,
                start=start,
                end=end,
                duration=duration,
                overlap=overlap,
                decode_worker_slot=index % workers,
                asr_device="cuda",
                reason=f"future playback head ({strategy})",
            )
        )
    return jobs


def build_future_spans(
    *,
    position: float,
    end_limit: float,
    window: float,
    strategy: str,
    base_window: float,
    max_future_window: float,
) -> tuple[tuple[float, float], ...]:
    if strategy == "fixed":
        count = int(math.ceil((end_limit - position) / window))
        return tuple(
            (position + index * window, min(end_limit, position + (index + 1) * window))
            for index in range(count)
        )
    spans: list[tuple[float, float]] = []
    cursor = position
    fib_previous = 1
    fib_current = 1
    while cursor < end_limit:
        length = min(max_future_window, base_window * fib_previous)
        next_cursor = min(end_limit, cursor + length)
        spans.append((cursor, next_cursor))
        cursor = next_cursor
        fib_previous, fib_current = fib_current, fib_previous + fib_current
    return tuple(spans)


def build_backfill_jobs(
    *,
    position: float,
    duration: float,
    overlap: float,
    workers: int,
    partitions: int,
    ranges: tuple[CoveredRange, ...],
    coverage_threshold: float,
) -> list[SubtitleWindowJob]:
    if position <= 0:
        return []
    jobs: list[SubtitleWindowJob] = []
    partition_width = position / partitions
    for index in range(partitions):
        start = index * partition_width
        end = position if index == partitions - 1 else (index + 1) * partition_width
        start = clamp(start, 0.0, duration)
        end = clamp(end, 0.0, duration)
        if end <= start or range_is_covered(start, end, ranges, coverage_threshold):
            continue
        jobs.append(
            make_job(
                job_id=f"backfill_{index:03d}",
                lane="backfill_cpu",
                priority=100 + index,
                start=start,
                end=end,
                duration=duration,
                overlap=overlap,
                decode_worker_slot=index % workers,
                asr_device="cpu",
                reason="pre-position subtitle gap",
            )
        )
    return jobs


def make_job(
    *,
    job_id: str,
    lane: str,
    priority: int,
    start: float,
    end: float,
    duration: float,
    overlap: float,
    decode_worker_slot: int,
    asr_device: str,
    reason: str,
) -> SubtitleWindowJob:
    return SubtitleWindowJob(
        job_id=job_id,
        lane=lane,
        priority=priority,
        start_sec=round(start, 3),
        end_sec=round(end, 3),
        decode_start_sec=round(clamp(start - overlap, 0.0, duration), 3),
        decode_end_sec=round(clamp(end + overlap, 0.0, duration), 3),
        overlap_sec=round(overlap, 3),
        decode_worker_slot=decode_worker_slot,
        asr_device=asr_device,
        reason=reason,
    )


def normalize_covered_ranges(
    ranges: Iterable[CoveredRange | tuple[float, float]],
) -> tuple[CoveredRange, ...]:
    cleaned: list[CoveredRange] = []
    for item in ranges:
        if isinstance(item, CoveredRange):
            start, end = item.start_sec, item.end_sec
        else:
            start, end = item
        start = max(0.0, float(start))
        end = max(0.0, float(end))
        if end > start:
            cleaned.append(CoveredRange(start, end))
    if not cleaned:
        return ()
    cleaned.sort(key=lambda item: (item.start_sec, item.end_sec))
    merged: list[CoveredRange] = [cleaned[0]]
    for item in cleaned[1:]:
        previous = merged[-1]
        if item.start_sec <= previous.end_sec:
            merged[-1] = CoveredRange(previous.start_sec, max(previous.end_sec, item.end_sec))
        else:
            merged.append(item)
    return tuple(merged)


def normalize_future_strategy(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in {"fixed", "fibonacci"}:
        raise ValueError(f"unsupported future window strategy: {value}")
    return normalized


def range_is_covered(
    start: float,
    end: float,
    ranges: tuple[CoveredRange, ...],
    threshold: float,
) -> bool:
    width = end - start
    if width <= 0:
        return True
    covered = 0.0
    for item in ranges:
        left = max(start, item.start_sec)
        right = min(end, item.end_sec)
        if right > left:
            covered += right - left
    return (covered / width) >= threshold


def job_to_payload(job: SubtitleWindowJob) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "lane": job.lane,
        "priority": job.priority,
        "start_sec": job.start_sec,
        "end_sec": job.end_sec,
        "decode_start_sec": job.decode_start_sec,
        "decode_end_sec": job.decode_end_sec,
        "overlap_sec": job.overlap_sec,
        "decode_worker_slot": job.decode_worker_slot,
        "asr_device": job.asr_device,
        "reason": job.reason,
    }


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
