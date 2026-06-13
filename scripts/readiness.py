from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from m1_player.mvp_readiness import collect_mvp_readiness  # noqa: E402
from m1_player.runtime_config import load_app_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = collect_mvp_readiness(load_app_config())
    if args.json:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    else:
        print(f"overall_status: {report.overall_status}")
        for gate in report.gates:
            print(f"{gate.status.upper()} {gate.key} [{gate.scope}]: {gate.message}")

    if args.strict and report.blocking_gates:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
