from __future__ import annotations

from dataclasses import dataclass

from .attachment_resolver import AttachmentResolution
from .video_source import VideoSourceInfo, is_http_stream_url


@dataclass(frozen=True)
class PlayabilityStatus:
    state: str
    can_play: bool
    playable_url: str | None
    player_label: str
    loading_hint: str | None = None
    log_lines: tuple[str, ...] = ()


def evaluate_playability(
    *,
    video_name: str,
    source: VideoSourceInfo,
    resolution: AttachmentResolution,
    playback_available: bool,
    playback_description: str,
) -> PlayabilityStatus:
    playable_url = source.playable_url or resolution.playable_url
    source_name = source.filename_hint or video_name
    resolver_line = f"resolver status: {resolution.status} - {resolution.reason}"

    if not playable_url:
        if source.requires_resolution and resolution.status == "missing_token":
            return PlayabilityStatus(
                state="notion_token_required",
                can_play=False,
                playable_url=None,
                player_label=(
                    "目前不能直接播放\n"
                    f"{video_name}\n"
                    "需要 Notion API token 才能解析影片網址"
                ),
                log_lines=(
                    f"video source needs resolver: kind={source.source_kind} name={source_name}",
                    resolver_line,
                ),
            )
        if source.requires_resolution:
            return PlayabilityStatus(
                state="resolver_failed",
                can_play=False,
                playable_url=None,
                player_label=(
                    "目前不能直接播放\n"
                    f"{video_name}\n"
                    f"resolver={resolution.status}"
                ),
                log_lines=(
                    f"video source needs resolver: kind={source.source_kind} name={source_name}",
                    resolver_line,
                ),
            )
        return PlayabilityStatus(
            state="unsupported_source",
            can_play=False,
            playable_url=None,
            player_label=(
                "目前不能直接播放\n"
                f"{video_name}\n"
                f"source_kind={source.source_kind}"
            ),
            log_lines=(f"unsupported video source: kind={source.source_kind} name={source_name}",),
        )

    if not is_http_stream_url(playable_url):
        return PlayabilityStatus(
            state="blocked_non_stream_url",
            can_play=False,
            playable_url=None,
            player_label=(
                "MVP streaming policy blocked this media source\n"
                f"{video_name}\n"
                "Only http/https stream URLs may be sent to mpv."
            ),
            log_lines=(
                f"blocked non-stream playable URL: kind={source.source_kind} name={source_name}",
                resolver_line,
            ),
        )

    if not playback_available:
        return PlayabilityStatus(
            state="playback_unavailable",
            can_play=False,
            playable_url=playable_url,
            player_label=(
                "播放器不可用\n"
                f"{video_name}\n" +
                playback_description
            ),
            log_lines=(playback_description,),
        )

    lines: tuple[str, ...] = ()
    if source.requires_resolution:
        lines = (resolver_line,)
        loading_hint = (
            "正在解析 Notion 串流，首次載入大型 MP4 可能需要約 10 秒完成索引讀取。"
            if resolution.status in {"resolved", "resolved_from_cache"}
            else None
        )
    else:
        loading_hint = None
    return PlayabilityStatus(
        state="ready",
        can_play=True,
        playable_url=playable_url,
        player_label=(
            f"mpv 正在載入 Notion 串流\n{video_name}\n請稍候，等待 duration / position 出現"
            if loading_hint
            else f"mpv 播放中\n{video_name}"
        ),
        loading_hint=loading_hint,
        log_lines=lines,
    )
