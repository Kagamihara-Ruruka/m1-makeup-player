from __future__ import annotations

from .mvp_readiness import MvpReadinessReport
from .setup_guide import build_setup_guide


def readiness_display_text(report: MvpReadinessReport) -> str:
    lines = [
        f"MVP 狀態：{status_label(report.overall_status)}",
        "",
        "檢查項目：",
    ]
    for gate in report.gates:
        lines.append(f"- {gate_status_label(gate.status)} {gate.key}: {gate.message}")
    next_actions = report.settings.get("next_actions")
    if isinstance(next_actions, list) and next_actions:
        lines.extend(["", "下一步："])
        for action in next_actions:
            lines.append(f"- {action}")
    guide = build_setup_guide(report.settings)
    lines.extend(["", guide.to_text()])
    return "\n".join(lines)


def status_label(status: str) -> str:
    labels = {
        "external_setup_required": "需要外部設定",
        "usable_with_warnings": "可用但有警告",
        "ready_for_real_notion_trial": "可進行真實 Notion 試跑",
    }
    return labels.get(status, status)


def gate_status_label(status: str) -> str:
    labels = {
        "pass": "PASS",
        "warning": "WARNING",
        "blocked": "BLOCKED",
    }
    return labels.get(status, status.upper())
