from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubtitleWindowRequest:
    generation_id: int
    record_key: str
    trigger: str
    start_sec: float
    max_duration_sec: float | None


class SubtitleSessionController:
    def __init__(self) -> None:
        self.generation_id = 0
        self.active_request: SubtitleWindowRequest | None = None

    def reset_for_video(self) -> None:
        self.generation_id += 1
        self.active_request = None

    def request_window(
        self,
        record_key: str,
        trigger: str,
        position_sec: float,
        window_sec: float,
        preroll_sec: float = 5.0,
    ) -> SubtitleWindowRequest:
        self.generation_id += 1
        request = SubtitleWindowRequest(
            generation_id=self.generation_id,
            record_key=record_key,
            trigger=trigger,
            start_sec=max(0.0, float(position_sec) - float(preroll_sec)),
            max_duration_sec=float(window_sec),
        )
        self.active_request = request
        return request

    def request_explicit(
        self,
        record_key: str,
        trigger: str,
        start_sec: float = 0.0,
        max_duration_sec: float | None = None,
    ) -> SubtitleWindowRequest:
        self.generation_id += 1
        request = SubtitleWindowRequest(
            generation_id=self.generation_id,
            record_key=record_key,
            trigger=trigger,
            start_sec=max(0.0, float(start_sec)),
            max_duration_sec=None if max_duration_sec is None else float(max_duration_sec),
        )
        self.active_request = request
        return request

    def accepts_result(self, generation_id: int, record_key: str) -> bool:
        request = self.active_request
        return request is not None and request.generation_id == generation_id and request.record_key == record_key
