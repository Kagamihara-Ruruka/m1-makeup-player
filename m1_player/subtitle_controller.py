from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Callable

from .subtitle import SubtitleCue, active_cue
from .subtitle_generation import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_TECHNICAL_HOTWORDS,
    SubtitleGenerationOptions,
)
from .subtitle_session import SubtitleSessionController, SubtitleWindowRequest


@dataclass(frozen=True)
class SubtitleGenerationPlan:
    request: SubtitleWindowRequest
    options: SubtitleGenerationOptions


@dataclass(frozen=True)
class SubtitleDispatchDecision:
    action: str
    plan: SubtitleGenerationPlan | None = None


class SubtitleController:
    def __init__(
        self,
        session: SubtitleSessionController | None = None,
        options_factory: Callable[[], SubtitleGenerationOptions] | None = None,
        window_sec_factory: Callable[[], float] | None = None,
    ) -> None:
        self.session = session or SubtitleSessionController()
        self.options_factory = options_factory or subtitle_generation_options_from_env
        self.window_sec_factory = window_sec_factory or timeline_subtitle_window_sec_from_env
        self._running_generation_id: int | None = None
        self._deferred_plan: SubtitleGenerationPlan | None = None

    @property
    def active_request(self) -> SubtitleWindowRequest | None:
        return self.session.active_request

    @property
    def running_generation_id(self) -> int | None:
        return self._running_generation_id

    @property
    def deferred_plan(self) -> SubtitleGenerationPlan | None:
        return self._deferred_plan

    def has_pending_work(self) -> bool:
        return self._running_generation_id is not None or self._deferred_plan is not None

    def reset_for_video(self) -> None:
        self.session.reset_for_video()
        self._running_generation_id = None
        self._deferred_plan = None

    def accepts_result(
        self,
        generation_id: int,
        record_key: str,
        current_record_key: str | None = None,
    ) -> bool:
        if current_record_key is not None and current_record_key != record_key:
            return False
        return self.session.accepts_result(generation_id, record_key)

    def dispatch_plan(self, plan: SubtitleGenerationPlan) -> SubtitleDispatchDecision:
        request = plan.request
        if not self.session.accepts_result(request.generation_id, request.record_key):
            return SubtitleDispatchDecision("skip", None)
        if self._running_generation_id is None:
            self._running_generation_id = request.generation_id
            return SubtitleDispatchDecision("start", plan)
        if self._running_generation_id == request.generation_id:
            return SubtitleDispatchDecision("skip", None)
        self._deferred_plan = plan
        return SubtitleDispatchDecision("defer", plan)

    def finish_running_generation(
        self,
        generation_id: int,
        current_record_key: str | None = None,
    ) -> SubtitleGenerationPlan | None:
        if self._running_generation_id != generation_id:
            return None
        self._running_generation_id = None
        next_plan = self._deferred_plan
        self._deferred_plan = None
        if next_plan is None:
            return None
        request = next_plan.request
        if not self.accepts_result(
            request.generation_id,
            request.record_key,
            current_record_key=current_record_key,
        ):
            return None
        self._running_generation_id = request.generation_id
        return next_plan

    def release_running_generation(self, generation_id: int) -> None:
        if self._running_generation_id == generation_id:
            self._running_generation_id = None

    def explicit_plan(
        self,
        record_key: str,
        trigger: str = "manual",
        start_sec: float = 0.0,
        max_duration_sec: float | None = None,
        overwrite: bool | None = None,
    ) -> SubtitleGenerationPlan:
        request = self.session.request_explicit(
            record_key,
            trigger=trigger,
            start_sec=start_sec,
            max_duration_sec=max_duration_sec,
        )
        return self.plan_for_request(request, overwrite=overwrite)

    def timeline_plan(
        self,
        record_key: str,
        cues: list[SubtitleCue],
        position_sec: float,
        trigger: str = "playback_timeline",
        force: bool = False,
    ) -> SubtitleGenerationPlan | None:
        target_sec = max(0.0, float(position_sec))
        if not force and not subtitle_cues_need_generation(cues) and subtitle_cues_cover_position(cues, target_sec):
            return None
        request = self.session.request_window(
            record_key,
            trigger=trigger,
            position_sec=target_sec,
            window_sec=self.window_sec_factory(),
            preroll_sec=subtitle_timeline_preroll_sec(trigger),
        )
        return self.plan_for_request(request, overwrite=True)

    def plan_for_request(
        self,
        request: SubtitleWindowRequest,
        overwrite: bool | None = None,
    ) -> SubtitleGenerationPlan:
        options = self.options_factory()
        options = replace(
            options,
            start_sec=request.start_sec,
            max_duration_sec=request.max_duration_sec,
            overwrite=options.overwrite if overwrite is None else bool(overwrite),
            output_stem_suffix=f"g{request.generation_id:05d}",
        )
        return SubtitleGenerationPlan(request=request, options=options)


def subtitle_cues_need_generation(cues: list[SubtitleCue]) -> bool:
    if not cues:
        return True
    return all(is_placeholder_subtitle_text(cue.text) for cue in cues)


def subtitle_cues_cover_position(cues: list[SubtitleCue], position_sec: float) -> bool:
    return active_cue(cues, position_sec) is not None


def is_placeholder_subtitle_text(value: str) -> bool:
    normalized = value.strip()
    return normalized == "待補字幕"


def subtitle_generation_options_from_env() -> SubtitleGenerationOptions:
    batch_size = _positive_int(os.environ.get("M1_WHISPER_BATCH_SIZE"), 8)
    beam_size = _positive_int(os.environ.get("M1_WHISPER_BEAM_SIZE"), 5)
    language = os.environ.get("M1_WHISPER_LANGUAGE", "zh").strip() or None
    return SubtitleGenerationOptions(
        model_size=os.environ.get("M1_WHISPER_MODEL", "medium").strip() or "medium",
        language=language,
        device=os.environ.get("M1_WHISPER_DEVICE", "auto").strip() or "auto",
        compute_type=os.environ.get("M1_WHISPER_COMPUTE_TYPE", "auto").strip() or "auto",
        batch_size=batch_size,
        beam_size=beam_size,
        overwrite=os.environ.get("M1_WHISPER_OVERWRITE", "").strip().lower() in {"1", "true", "yes"},
        output_suffix=os.environ.get("M1_WHISPER_OUTPUT_FORMAT", ".srt").strip() or ".srt",
        initial_prompt=_optional_env_text("M1_WHISPER_INITIAL_PROMPT", DEFAULT_INITIAL_PROMPT),
        hotwords=_optional_env_text("M1_WHISPER_HOTWORDS", DEFAULT_TECHNICAL_HOTWORDS),
    )


def timeline_subtitle_window_sec_from_env() -> float:
    try:
        return max(30.0, float(os.environ.get("M1_TIMELINE_SUBTITLE_WINDOW_SEC", "180")))
    except (TypeError, ValueError):
        return 180.0


def subtitle_timeline_preroll_sec(trigger: str) -> float:
    if trigger in {"playback_prefetch_timeline", "speed_change_timeline"}:
        return 0.0
    return 5.0


def _positive_int(value: str | None, default: int) -> int:
    try:
        return max(1, int(str(value)))
    except (TypeError, ValueError):
        return default


def _optional_env_text(name: str, default: str | None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        value = default
    if value is None:
        return None
    text = str(value).strip()
    return text or None
