from __future__ import annotations

import sys
import argparse
import math
import struct
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.playback import create_default_playback_core, find_mpv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--ipc-smoke", action="store_true")
    args = parser.parse_args()
    availability = find_mpv()
    print(f"mpv_available={availability.available}")
    print(f"mpv_path={availability.mpv_path or ''}")
    core = create_default_playback_core()
    print(f"core={core.describe()}")
    if args.ipc_smoke and availability.available:
        sample_path = ROOT / "tmp" / "mpv_smoke.wav"
        write_sample_wav(sample_path)
        try:
            core.load(str(sample_path))
            core.play()
            deadline = 30
            for _ in range(deadline):
                duration = core.duration_sec()
                position = core.position_sec()
                print(f"ipc_smoke position={position} duration={duration}")
                if duration and duration > 0:
                    break
                time.sleep(0.1)
            else:
                print("ipc_smoke=duration_unavailable")
                return 3
            print("ipc_smoke=PASS")
        finally:
            core.close()
    return 2 if args.strict and not availability.available else 0


def write_sample_wav(path: Path) -> None:
    path.parent.mkdir(exist_ok=True)
    sample_rate = 44100
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(sample_rate):
            sample = int(12000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            handle.writeframes(struct.pack("<h", sample))


if __name__ == "__main__":
    raise SystemExit(main())
