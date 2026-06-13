from __future__ import annotations

import importlib.util
import os
import shutil
import site
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Any

from .models import PlaybackRecord
from .subtitle import load_subtitle
from .subtitle_resolver import safe_filename_stem


SUPPORTED_GENERATED_SUFFIXES = (".srt", ".vtt", ".md")
CUDA_RUNTIME_DIRS_ENV = "M1_CUDA_RUNTIME_DIRS"
_CUDA_DLL_DIRECTORY_HANDLES: list[object] = []
DEFAULT_TECHNICAL_HOTWORDS = (
    "Kubernetes, K8S, k8s, kubectl, kubelet, kube-proxy, Pod, Deployment, Service, Ingress, "
    "ConfigMap, Secret, StatefulSet, DaemonSet, Job, CronJob, CoreDNS, etcd, control plane, "
    "worker node, container, image, namespace, YAML, Helm, Postgres, PostgreSQL, API Server, "
    "MySQL, SQL, transaction, index, query, schema, Docker, Linux, TCP, IP, HTTP, DNS, TLS, "
    "load balancer, cache, message queue, Redis, API, REST, RPC, object oriented, design pattern, "
    "factory, strategy, observer, singleton, thread, process, memory, filesystem, compiler"
)
DEFAULT_INITIAL_PROMPT = (
    "這是中文計算機科學與軟體工程課程逐字稿，主題可能包含 Kubernetes、資料庫、網路、"
    "作業系統、設計模式、容器、雲端、後端、API、SQL、YAML 等專有名詞。"
)


class SubtitleGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SubtitleGenerationOptions:
    model_size: str = "medium"
    language: str | None = "zh"
    device: str = "auto"
    compute_type: str = "auto"
    batch_size: int = 8
    beam_size: int = 5
    vad_filter: bool = True
    overwrite: bool = False
    output_suffix: str = ".srt"
    max_duration_sec: float | None = None
    initial_prompt: str | None = DEFAULT_INITIAL_PROMPT
    hotwords: str | None = DEFAULT_TECHNICAL_HOTWORDS


@dataclass(frozen=True)
class SubtitleGenerationDependencyStatus:
    faster_whisper_available: bool
    cuda_runtime_available: bool

    @property
    def ready(self) -> bool:
        return self.faster_whisper_available

    @property
    def message(self) -> str:
        if not self.faster_whisper_available:
            return "missing faster-whisper; run pip install -r requirements.txt"
        if self.cuda_runtime_available:
            return "faster-whisper available; CUDA runtime available"
        return "faster-whisper available; CUDA runtime missing, CPU fallback active"


@dataclass(frozen=True)
class GeneratedSubtitleSegment:
    index: int
    start_sec: float
    end_sec: float
    text: str


@dataclass(frozen=True)
class TranscriptionRun:
    segments: list[GeneratedSubtitleSegment]
    decode_elapsed_sec: float
    inference_elapsed_sec: float


@dataclass(frozen=True)
class SubtitleGenerationResult:
    record_key: str
    status: str
    subtitle_path: str | None
    cue_count: int
    elapsed_sec: float
    message: str
    model_size: str | None = None
    device: str | None = None
    compute_type: str | None = None
    decode_elapsed_sec: float | None = None
    inference_elapsed_sec: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"generated", "skipped_existing"}


def subtitle_generation_dependency_status() -> SubtitleGenerationDependencyStatus:
    return SubtitleGenerationDependencyStatus(
        faster_whisper_available=importlib.util.find_spec("faster_whisper") is not None,
        cuda_runtime_available=cuda_runtime_available(),
    )


def cuda_runtime_available() -> bool:
    ensure_cuda_runtime_dirs()
    if shutil.which("cublas64_12.dll"):
        return True
    if all(_dll_exists_in_candidate_dirs(name) for name in ("cublas64_12.dll", "cudnn64_9.dll")):
        return True
    return False


def ensure_cuda_runtime_dirs() -> None:
    runtime_dirs = [path for path in cuda_runtime_candidate_dirs() if any(path.glob("*.dll"))]
    prepend_runtime_dirs_to_path(runtime_dirs)
    if not hasattr(os, "add_dll_directory"):
        return
    for path in runtime_dirs:
        path_text = str(path)
        if any(str(getattr(handle, "path", "")) == path_text for handle in _CUDA_DLL_DIRECTORY_HANDLES):
            continue
        try:
            _CUDA_DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(path_text))
        except OSError:
            continue


def prepend_runtime_dirs_to_path(paths: list[Path]) -> None:
    current_parts = os.environ.get("PATH", "").split(os.pathsep)
    current_lower = {part.lower() for part in current_parts}
    new_parts = [str(path) for path in paths if str(path).lower() not in current_lower]
    if new_parts:
        os.environ["PATH"] = os.pathsep.join(new_parts + current_parts)


def cuda_runtime_candidate_dirs() -> list[Path]:
    candidates: list[Path] = []
    for raw_dir in os.environ.get(CUDA_RUNTIME_DIRS_ENV, "").split(os.pathsep):
        if raw_dir.strip():
            candidates.append(Path(raw_dir.strip()))
    candidate_roots = [
        os.environ.get("CUDA_PATH"),
        os.environ.get("CUDA_HOME"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA",
    ]
    for root in candidate_roots:
        if not root:
            continue
        root_path = Path(root)
        candidates.append(root_path / "bin")
        if root_path.name.upper() == "CUDA":
            candidates.extend(root_path.glob(r"v*\bin"))
    for package_root in python_nvidia_runtime_roots():
        candidates.extend(
            [
                package_root / "cublas" / "bin",
                package_root / "cudnn" / "bin",
                package_root / "cuda_nvrtc" / "bin",
            ]
        )
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        path_key = str(path).lower()
        if path_key in seen:
            continue
        seen.add(path_key)
        unique.append(path)
    return unique


def python_nvidia_runtime_roots() -> list[Path]:
    roots: list[Path] = []
    search_roots = [Path(path) for path in site.getsitepackages()]
    search_roots.append(Path(sys.prefix) / "Lib" / "site-packages")
    for root in search_roots:
        nvidia_root = root / "nvidia"
        if nvidia_root.exists():
            roots.append(nvidia_root)
    return roots


def _dll_exists_in_candidate_dirs(filename: str) -> bool:
    return any((path / filename).exists() for path in cuda_runtime_candidate_dirs())


def subtitle_output_path(
    record: PlaybackRecord,
    subtitle_dir: str | Path,
    suffix: str = ".srt",
) -> Path:
    suffix = normalize_generated_suffix(suffix)
    stable_stem = safe_filename_stem(record.stable_key.replace(":", "_"))
    return Path(subtitle_dir) / f"{stable_stem}{suffix}"


def normalize_generated_suffix(value: str) -> str:
    suffix = value.strip().lower()
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    if suffix not in SUPPORTED_GENERATED_SUFFIXES:
        raise ValueError(f"unsupported subtitle output suffix: {value}")
    return suffix


def generate_subtitle_sidecar(
    record: PlaybackRecord,
    media_ref: str,
    subtitle_dir: str | Path,
    options: SubtitleGenerationOptions | None = None,
) -> SubtitleGenerationResult:
    options = options or SubtitleGenerationOptions()
    output_path = subtitle_output_path(record, subtitle_dir, options.output_suffix)
    started = time.perf_counter()
    if output_path.exists() and not options.overwrite:
        cues = load_subtitle(output_path)
        return SubtitleGenerationResult(
            record_key=record.stable_key,
            status="skipped_existing",
            subtitle_path=str(output_path),
            cue_count=len(cues),
            elapsed_sec=round(time.perf_counter() - started, 3),
            message="subtitle sidecar already exists",
        )
    if not media_ref.strip():
        raise SubtitleGenerationError("empty media reference")
    dependency = subtitle_generation_dependency_status()
    if not dependency.ready:
        raise SubtitleGenerationError(dependency.message)

    errors: list[str] = []
    for device, compute_type in _runtime_candidates(options):
        try:
            transcription = transcribe_media_with_timing(
                media_ref,
                options=options,
                device=device,
                compute_type=compute_type,
            )
        except Exception as exc:  # noqa: BLE001 - fallback across compute backends is intentional.
            errors.append(f"{device}/{compute_type}: {exc}")
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_subtitle_segments(output_path, transcription.segments)
        return SubtitleGenerationResult(
            record_key=record.stable_key,
            status="generated",
            subtitle_path=str(output_path),
            cue_count=len(transcription.segments),
            elapsed_sec=round(time.perf_counter() - started, 3),
            message="subtitle sidecar generated",
            model_size=options.model_size,
            device=device,
            compute_type=compute_type,
            decode_elapsed_sec=transcription.decode_elapsed_sec,
            inference_elapsed_sec=transcription.inference_elapsed_sec,
        )
    raise SubtitleGenerationError("; ".join(errors) or "transcription failed")


def transcribe_media(
    media_ref: str,
    options: SubtitleGenerationOptions,
    device: str,
    compute_type: str,
) -> list[GeneratedSubtitleSegment]:
    return transcribe_media_with_timing(media_ref, options, device, compute_type).segments


def transcribe_media_with_timing(
    media_ref: str,
    options: SubtitleGenerationOptions,
    device: str,
    compute_type: str,
) -> TranscriptionRun:
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    decode_started = time.perf_counter()
    audio = decode_audio_window(media_ref, max_duration_sec=options.max_duration_sec)
    decode_elapsed_sec = round(time.perf_counter() - decode_started, 3)
    inference_started = time.perf_counter()
    model = WhisperModel(options.model_size, device=device, compute_type=compute_type)
    kwargs = {
        "language": options.language,
        "vad_filter": options.vad_filter,
        "beam_size": options.beam_size,
        "condition_on_previous_text": False,
        "without_timestamps": False,
        "initial_prompt": options.initial_prompt,
        "hotwords": options.hotwords,
    }
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    batch_size = options.batch_size
    segments_iter = None
    if batch_size > 1:
        try:
            from faster_whisper import BatchedInferencePipeline  # type: ignore[import-not-found]

            batched_model = BatchedInferencePipeline(model=model)
            segments_iter, _info = batched_model.transcribe(
                audio,
                batch_size=max(1, int(batch_size)),
                **kwargs,
            )
        except TypeError:
            segments_iter = None
    if segments_iter is None:
        segments_iter, _info = model.transcribe(audio, **kwargs)
    segments = generated_segments_from_faster_whisper(segments_iter)
    inference_elapsed_sec = round(time.perf_counter() - inference_started, 3)
    return TranscriptionRun(
        segments=segments,
        decode_elapsed_sec=decode_elapsed_sec,
        inference_elapsed_sec=inference_elapsed_sec,
    )


def decode_audio_window(
    media_ref: str,
    max_duration_sec: float | None = None,
    sample_rate: int = 16_000,
) -> Any:
    import av  # type: ignore[import-not-found]
    import numpy as np  # type: ignore[import-not-found]

    container = av.open(media_ref)
    try:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            raise SubtitleGenerationError("media has no audio stream")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        chunks = []
        decoded_samples = 0
        max_samples = int(max_duration_sec * sample_rate) if max_duration_sec and max_duration_sec > 0 else None
        for packet in container.demux(stream):
            for frame in packet.decode():
                for resampled in resampler.resample(frame):
                    array = resampled.to_ndarray().reshape(-1).astype("float32") / 32768.0
                    if max_samples is not None:
                        remaining = max_samples - decoded_samples
                        if remaining <= 0:
                            return _concat_audio_chunks(chunks)
                        array = array[:remaining]
                    if array.size:
                        chunks.append(array)
                        decoded_samples += int(array.size)
                    if max_samples is not None and decoded_samples >= max_samples:
                        return _concat_audio_chunks(chunks)
        return _concat_audio_chunks(chunks)
    finally:
        container.close()


def _concat_audio_chunks(chunks: list[Any]) -> Any:
    import numpy as np  # type: ignore[import-not-found]

    if not chunks:
        return np.zeros(0, dtype="float32")
    return np.concatenate(chunks).astype("float32", copy=False)


def generated_segments_from_faster_whisper(segments: Iterable[object]) -> list[GeneratedSubtitleSegment]:
    generated: list[GeneratedSubtitleSegment] = []
    for index, segment in enumerate(segments, 1):
        start = _segment_float(segment, "start", 0.0)
        end = _segment_float(segment, "end", start + 2.0)
        text = str(getattr(segment, "text", "")).strip()
        if not text:
            continue
        if end <= start:
            end = start + 0.5
        generated.append(GeneratedSubtitleSegment(index=len(generated) + 1, start_sec=start, end_sec=end, text=text))
    return generated


def write_subtitle_segments(path: str | Path, segments: list[GeneratedSubtitleSegment]) -> None:
    output_path = Path(path)
    suffix = normalize_generated_suffix(output_path.suffix)
    if suffix == ".srt":
        text = render_srt(segments)
    elif suffix == ".vtt":
        text = render_vtt(segments)
    else:
        text = render_markdown_transcript(segments)
    output_path.write_text(text, encoding="utf-8", newline="\n")


def render_srt(segments: list[GeneratedSubtitleSegment]) -> str:
    blocks = []
    for index, segment in enumerate(segments, 1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_timestamp(segment.start_sec)} --> {format_srt_timestamp(segment.end_sec)}",
                    segment.text,
                ]
            )
        )
    return "\n\n".join(blocks).strip() + "\n"


def render_vtt(segments: list[GeneratedSubtitleSegment]) -> str:
    body = render_srt(segments).replace(",", ".")
    return "WEBVTT\n\n" + "\n".join(line for line in body.splitlines() if not line.isdigit()).strip() + "\n"


def render_markdown_transcript(segments: list[GeneratedSubtitleSegment]) -> str:
    lines = ["# 逐字稿", ""]
    for segment in segments:
        lines.append(f"[{format_markdown_timestamp(segment.start_sec)} --> {format_markdown_timestamp(segment.end_sec)}] {segment.text}")
    return "\n".join(lines).strip() + "\n"


def format_srt_timestamp(value: float) -> str:
    value = max(0.0, float(value))
    total_ms = int(round(value * 1000))
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    seconds = total_ms // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def format_markdown_timestamp(value: float) -> str:
    return format_srt_timestamp(value).replace(",", ".")


def _runtime_candidates(options: SubtitleGenerationOptions) -> list[tuple[str, str]]:
    if options.device != "auto":
        compute_type = _default_compute_type(options.device, options.compute_type)
        return [(options.device, compute_type)]
    has_cuda_runtime = cuda_runtime_available()
    if options.compute_type != "auto":
        candidates = [("cpu", options.compute_type)]
        if has_cuda_runtime:
            candidates.insert(0, ("cuda", options.compute_type))
        return candidates
    candidates = [("cpu", "int8")]
    if has_cuda_runtime:
        candidates.insert(0, ("cuda", "float16"))
    return candidates


def _default_compute_type(device: str, compute_type: str) -> str:
    if compute_type != "auto":
        return compute_type
    if device == "cuda":
        return "float16"
    return "int8"


def _segment_float(segment: object, field: str, default: float) -> float:
    try:
        return float(getattr(segment, field))
    except (TypeError, ValueError):
        return default
