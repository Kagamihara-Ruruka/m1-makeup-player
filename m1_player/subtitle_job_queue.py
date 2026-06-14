from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable

from m1_player.subtitle_rolling_scheduler import SubtitleWindowJob


@dataclass(frozen=True)
class SubtitleJobState:
    job: SubtitleWindowJob
    status: str = "pending"
    claimed_by: str | None = None
    claimed_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None


class RollingSubtitleJobQueue:
    def __init__(self, jobs: Iterable[SubtitleWindowJob]) -> None:
        self._states = {
            job.job_id: SubtitleJobState(job=job)
            for job in sorted(jobs, key=lambda item: (item.priority, item.start_sec, item.end_sec, item.job_id))
        }

    @property
    def states(self) -> tuple[SubtitleJobState, ...]:
        return tuple(self._states.values())

    def pending_count(self) -> int:
        return sum(1 for state in self._states.values() if state.status == "pending")

    def running_count(self) -> int:
        return sum(1 for state in self._states.values() if state.status == "running")

    def completed_count(self) -> int:
        return sum(1 for state in self._states.values() if state.status == "completed")

    def failed_count(self) -> int:
        return sum(1 for state in self._states.values() if state.status == "failed")

    def claim_next(
        self,
        worker_id: str,
        lane: str | None = None,
        fallback_lanes: Iterable[str] = (),
    ) -> SubtitleJobState | None:
        lane_order = [lane] if lane is not None else [None]
        lane_order.extend(fallback_lanes)
        for candidate_lane in lane_order:
            selected = self._select_pending(candidate_lane)
            if selected is not None:
                return self._claim(selected, worker_id)
        return None

    def _select_pending(self, lane: str | None) -> SubtitleJobState | None:
        pending = [
            state
            for state in self._states.values()
            if state.status == "pending" and (lane is None or state.job.lane == lane)
        ]
        if not pending:
            return None
        pending.sort(key=lambda state: (state.job.priority, state.job.start_sec, state.job.end_sec, state.job.job_id))
        return pending[0]

    def _claim(self, selected: SubtitleJobState, worker_id: str) -> SubtitleJobState:
        updated = replace(
            selected,
            status="running",
            claimed_by=worker_id,
            claimed_at=now_iso(),
            error_message=None,
        )
        self._states[selected.job.job_id] = updated
        return updated

    def complete(self, job_id: str) -> SubtitleJobState:
        state = self._require_job(job_id)
        updated = replace(state, status="completed", completed_at=now_iso(), error_message=None)
        self._states[job_id] = updated
        return updated

    def fail(self, job_id: str, message: str) -> SubtitleJobState:
        state = self._require_job(job_id)
        updated = replace(state, status="failed", completed_at=now_iso(), error_message=message)
        self._states[job_id] = updated
        return updated

    def requeue_failed(self, job_id: str) -> SubtitleJobState:
        state = self._require_job(job_id)
        if state.status != "failed":
            return state
        updated = replace(
            state,
            status="pending",
            claimed_by=None,
            claimed_at=None,
            completed_at=None,
            error_message=None,
        )
        self._states[job_id] = updated
        return updated

    def to_payload(self) -> dict[str, object]:
        return {
            "pending_count": self.pending_count(),
            "running_count": self.running_count(),
            "completed_count": self.completed_count(),
            "failed_count": self.failed_count(),
            "jobs": [state_to_payload(state) for state in self.states],
        }

    def _require_job(self, job_id: str) -> SubtitleJobState:
        try:
            return self._states[job_id]
        except KeyError as exc:
            raise KeyError(f"unknown subtitle job: {job_id}") from exc


def state_to_payload(state: SubtitleJobState) -> dict[str, object]:
    return {
        "job_id": state.job.job_id,
        "lane": state.job.lane,
        "priority": state.job.priority,
        "status": state.status,
        "claimed_by": state.claimed_by,
        "claimed_at": state.claimed_at,
        "completed_at": state.completed_at,
        "error_message": state.error_message,
        "start_sec": state.job.start_sec,
        "end_sec": state.job.end_sec,
        "decode_start_sec": state.job.decode_start_sec,
        "decode_end_sec": state.job.decode_end_sec,
        "asr_device": state.job.asr_device,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
