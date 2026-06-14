from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize saved decode concurrency profile JSON files.")
    parser.add_argument("paths", nargs="*", help="Profile JSON files.")
    parser.add_argument("--glob", default="tmp/decode_profile*.json", help="Glob used when paths are omitted.")
    parser.add_argument("--playback-rate", type=float, default=8.0)
    parser.add_argument("--output", help="Optional Markdown output path.")
    args = parser.parse_args()

    paths = [Path(path) for path in args.paths] or [Path(path) for path in glob.glob(args.glob)]
    paths = sorted(path for path in paths if path.exists())
    if not paths:
        print("no profile files found", file=sys.stderr)
        return 1
    lines = build_markdown(paths, args.playback_rate)
    text = "\n".join(lines) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


def build_markdown(paths: list[Path], playback_rate: float) -> list[str]:
    lines = [
        "# Decode Profile Summary",
        "",
        "| file | rows | best capacity | best 8x candidate | mean handshake | mean loop | recommendation |",
        "| --- | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("results", [])
        best_capacity = best_row(rows, key="capacity_ratio")
        best_candidate = best_playback_candidate(rows, playback_rate, payload.get("safety_factor", 1.35))
        handshake_mean = mean_of(rows, "mean_handshake_sec")
        loop_mean = mean_of(rows, "mean_decode_loop_sec")
        recommendation = recommendation_label(payload, playback_rate)
        lines.append(
            "| "
            f"{path.as_posix()} | "
            f"{len(rows)} | "
            f"{format_row(best_capacity)} | "
            f"{format_row(best_candidate)} | "
            f"{format_seconds(handshake_mean)} | "
            f"{format_seconds(loop_mean)} | "
            f"{recommendation} |"
        )
    return lines


def best_playback_candidate(rows: list[dict[str, Any]], playback_rate: float, safety_factor: float) -> dict[str, Any] | None:
    required = playback_rate * safety_factor
    candidates = [
        row
        for row in rows
        if row.get("failure_count") == 0
        and float(row.get("capacity_ratio") or 0.0) >= required
        and (row.get("p95_decode_sec") is None or float(row["p95_decode_sec"]) <= float(row["window_sec"]))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (float(row["window_sec"]), int(row["concurrency"]), -float(row["capacity_ratio"])))
    return candidates[0]


def best_row(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: float(row.get(key) or 0.0))


def mean_of(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    if not values:
        return None
    return sum(values) / len(values)


def recommendation_label(payload: dict[str, Any], playback_rate: float) -> str:
    for item in payload.get("recommended", []):
        if float(item.get("playback_rate", 0.0)) != playback_rate:
            continue
        overall = item.get("overall")
        if not overall:
            return "no viable"
        return format_row(overall)
    return "not tested"


def format_row(row: dict[str, Any] | None) -> str:
    if row is None:
        return "none"
    return (
        f"{row.get('window_sec')}s x "
        f"c{row.get('concurrency')} "
        f"{row.get('capacity_ratio')}x"
    )


def format_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}s"


if __name__ == "__main__":
    raise SystemExit(main())
