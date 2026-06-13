from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RollingPipelinePlan:
    audio_window_sec: float
    playback_rate: float
    decode_elapsed_sec: float
    inference_elapsed_sec: float
    decode_realtime_ratio: float
    inference_realtime_ratio: float
    recommended_decode_workers: int
    recommended_gpu_workers: int
    prefetch_horizon_sec: float
    overlap_sec: float
    expected_decode_capacity_ratio: float
    expected_pipeline_capacity_ratio: float
    can_keep_up: bool
    note: str


def plan_rolling_subtitle_pipeline(
    *,
    audio_window_sec: float,
    decode_elapsed_sec: float,
    inference_elapsed_sec: float,
    playback_rate: float = 1.0,
    safety_factor: float = 1.35,
    min_decode_workers: int = 1,
    max_decode_workers: int = 4,
    prefetch_windows: int = 4,
    overlap_sec: float = 2.0,
) -> RollingPipelinePlan:
    window = max(0.001, float(audio_window_sec))
    playback = max(0.1, float(playback_rate))
    decode_elapsed = max(0.0, float(decode_elapsed_sec))
    inference_elapsed = max(0.0, float(inference_elapsed_sec))
    decode_ratio = decode_elapsed / window
    inference_ratio = inference_elapsed / window
    raw_decode_workers = math.ceil(decode_ratio * playback * safety_factor)
    decode_workers = clamp_int(raw_decode_workers, min_decode_workers, max_decode_workers)
    gpu_workers = 1
    decode_capacity = decode_workers / max(0.001, decode_ratio)
    inference_capacity = gpu_workers / max(0.001, inference_ratio)
    pipeline_capacity = min(decode_capacity, inference_capacity)
    can_keep_up = pipeline_capacity >= playback
    if decode_workers >= max_decode_workers and decode_capacity < playback:
        note = "decode-bound; increase horizon or avoid reopening remote URLs per window"
    elif inference_capacity < playback:
        note = "inference-bound; use CUDA, smaller model, or larger windows"
    else:
        note = "capacity appears sufficient for current playback rate"
    return RollingPipelinePlan(
        audio_window_sec=round(window, 3),
        playback_rate=round(playback, 3),
        decode_elapsed_sec=round(decode_elapsed, 3),
        inference_elapsed_sec=round(inference_elapsed, 3),
        decode_realtime_ratio=round(decode_ratio, 3),
        inference_realtime_ratio=round(inference_ratio, 3),
        recommended_decode_workers=decode_workers,
        recommended_gpu_workers=gpu_workers,
        prefetch_horizon_sec=round(window * max(1, prefetch_windows), 3),
        overlap_sec=round(max(0.0, min(float(overlap_sec), window / 2)), 3),
        expected_decode_capacity_ratio=round(decode_capacity, 3),
        expected_pipeline_capacity_ratio=round(pipeline_capacity, 3),
        can_keep_up=can_keep_up,
        note=note,
    )


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
