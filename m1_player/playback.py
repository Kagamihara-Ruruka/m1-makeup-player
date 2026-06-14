from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class PlaybackCore(Protocol):
    def available(self) -> bool:
        ...

    def describe(self) -> str:
        ...

    def load(self, url: str) -> None:
        ...

    def play(self) -> None:
        ...

    def pause(self) -> None:
        ...

    def toggle_pause(self) -> None:
        ...

    def seek(self, position_sec: float) -> None:
        ...

    def set_speed(self, speed: float) -> None:
        ...

    def set_fullscreen(self, enabled: bool) -> None:
        ...

    def load_subtitle(self, subtitle_path: str) -> None:
        ...

    def set_subtitle_visible(self, enabled: bool) -> None:
        ...

    def position_sec(self) -> float | None:
        ...

    def duration_sec(self) -> float | None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class MpvAvailability:
    mpv_path: str | None

    @property
    def available(self) -> bool:
        return bool(self.mpv_path)


def find_mpv() -> MpvAvailability:
    configured = os.environ.get("M1_MPV_PATH")
    if configured and Path(configured).exists():
        return MpvAvailability(configured)
    path_mpv = shutil.which("mpv")
    if path_mpv:
        return MpvAvailability(path_mpv)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        winget_root = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
        matches = sorted(winget_root.glob("mpv-player.mpv-CI.MSVC_*\\mpv.exe"))
        if matches:
            return MpvAvailability(str(matches[-1]))
    return MpvAvailability(None)


class MissingPlaybackCore:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def available(self) -> bool:
        return False

    def describe(self) -> str:
        return self.reason

    def load(self, url: str) -> None:
        raise RuntimeError(self.reason)

    def play(self) -> None:
        raise RuntimeError(self.reason)

    def pause(self) -> None:
        raise RuntimeError(self.reason)

    def toggle_pause(self) -> None:
        raise RuntimeError(self.reason)

    def seek(self, position_sec: float) -> None:
        raise RuntimeError(self.reason)

    def set_speed(self, speed: float) -> None:
        raise RuntimeError(self.reason)

    def set_fullscreen(self, enabled: bool) -> None:
        raise RuntimeError(self.reason)

    def load_subtitle(self, subtitle_path: str) -> None:
        raise RuntimeError(self.reason)

    def set_subtitle_visible(self, enabled: bool) -> None:
        raise RuntimeError(self.reason)

    def position_sec(self) -> float | None:
        return None

    def duration_sec(self) -> float | None:
        return None

    def close(self) -> None:
        return


class MpvIpcPlaybackCore:
    def __init__(self, mpv_path: str, pipe_name: str | None = None) -> None:
        self.mpv_path = mpv_path
        self.pipe_name = pipe_name or f"m1_mpv_{uuid.uuid4().hex}"
        self.pipe_path = rf"\\.\pipe\{self.pipe_name}"
        self.proc: subprocess.Popen[str] | None = None
        self.pipe: Any | None = None
        self.request_id = 1
        self.window_id: int | None = None

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return f"mpv IPC: {self.mpv_path}"

    def set_window_id(self, window_id: int) -> None:
        if window_id > 0:
            self.window_id = int(window_id)

    def ensure_started(self) -> None:
        if self.proc and self.proc.poll() is None and self.pipe:
            return
        self.proc = subprocess.Popen(
            mpv_start_args(self.mpv_path, self.pipe_path, window_id=self.window_id),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.pipe = self._connect_pipe()

    def _connect_pipe(self) -> Any:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                return open(self.pipe_path, "r+b", buffering=0)
            except OSError:
                time.sleep(0.1)
        raise TimeoutError(f"mpv IPC pipe not ready: {self.pipe_path}")

    def load(self, url: str) -> None:
        self.ensure_started()
        self.command(["loadfile", url, "replace"])

    def play(self) -> None:
        self.set_property("pause", False)

    def pause(self) -> None:
        self.set_property("pause", True)

    def toggle_pause(self) -> None:
        paused = self.get_property("pause")
        self.set_property("pause", not bool(paused))

    def seek(self, position_sec: float) -> None:
        self.command(["seek", max(0.0, float(position_sec)), "absolute"])

    def set_speed(self, speed: float) -> None:
        self.set_property("speed", clamp_playback_speed(speed))

    def set_fullscreen(self, enabled: bool) -> None:
        self.set_property("fullscreen", bool(enabled))

    def load_subtitle(self, subtitle_path: str) -> None:
        path = Path(subtitle_path)
        if not path.exists():
            raise FileNotFoundError(str(path))
        self.command(["sub-add", str(path), "select"])

    def set_subtitle_visible(self, enabled: bool) -> None:
        self.set_property("sub-visibility", bool(enabled))

    def position_sec(self) -> float | None:
        return _as_float(self.get_property("time-pos"))

    def duration_sec(self) -> float | None:
        return _as_float(self.get_property("duration"))

    def get_property(self, name: str) -> Any:
        result = self.command(["get_property", name], allow_property_unavailable=True)
        return result.get("data")

    def set_property(self, name: str, value: Any) -> None:
        self.command(["set_property", name, value])

    def command(self, command: list[Any], allow_property_unavailable: bool = False) -> dict[str, Any]:
        self.ensure_started()
        if self.pipe is None:
            raise RuntimeError("mpv IPC pipe is not connected")
        request_id = self.request_id
        self.request_id += 1
        payload = {"command": command, "request_id": request_id}
        self.pipe.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        while True:
            line = self.pipe.readline()
            if not line:
                raise RuntimeError("mpv IPC pipe closed")
            message = json.loads(line.decode("utf-8", errors="strict"))
            if message.get("request_id") == request_id:
                if allow_property_unavailable and message.get("error") == "property unavailable":
                    return {"data": None, "error": "property unavailable"}
                if message.get("error") not in (None, "success"):
                    raise RuntimeError(json.dumps(message, ensure_ascii=False))
                return message

    def close(self) -> None:
        if self.pipe:
            self.pipe.close()
            self.pipe = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


def mpv_start_args(mpv_path: str, pipe_path: str, window_id: int | None = None) -> list[str]:
    args = [
        mpv_path,
        "--idle=yes",
        "--input-terminal=no",
        f"--input-ipc-server={pipe_path}",
        "--keep-open=yes",
        "--cache=yes",
        "--force-seekable=yes",
        "--demuxer-max-bytes=50MiB",
        "--audio-pitch-correction=yes",
    ]
    if window_id is not None and window_id > 0:
        args.insert(2, f"--wid={int(window_id)}")
    else:
        args.insert(2, "--force-window=yes")
    return args


def create_default_playback_core() -> PlaybackCore:
    availability = find_mpv()
    if not availability.available or not availability.mpv_path:
        return MissingPlaybackCore("找不到 mpv.exe。請安裝 mpv，或設定 M1_MPV_PATH 指向 mpv.exe。")
    return MpvIpcPlaybackCore(availability.mpv_path)


def clamp_playback_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError):
        value = 1.0
    return min(8.0, max(0.25, value))


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
