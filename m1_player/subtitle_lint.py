from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .subtitle import SubtitleCue, load_subtitle


@dataclass(frozen=True)
class SubtitleLintIssue:
    code: str
    severity: str
    cue_index: int | None
    message: str

    def to_json(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "cue_index": self.cue_index,
            "message": self.message,
        }


@dataclass(frozen=True)
class SubtitleLintResult:
    path: str
    status: str
    cue_count: int
    issues: tuple[SubtitleLintIssue, ...]

    @property
    def passes(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "status": self.status,
            "cue_count": self.cue_count,
            "passes": self.passes,
            "issues": [issue.to_json() for issue in self.issues],
        }


def lint_subtitle_file(
    path: str | Path,
    *,
    max_cue_duration_sec: float = 45.0,
    max_text_chars: int = 240,
) -> SubtitleLintResult:
    subtitle_path = Path(path)
    try:
        cues = load_subtitle(subtitle_path)
    except Exception as exc:  # noqa: BLE001 - lint result should preserve parse failures.
        return SubtitleLintResult(
            path=str(subtitle_path),
            status="parse_error",
            cue_count=0,
            issues=(
                SubtitleLintIssue(
                    code="subtitle_parse_error",
                    severity="error",
                    cue_index=None,
                    message=str(exc),
                ),
            ),
        )
    issues = lint_cues(
        cues,
        max_cue_duration_sec=max_cue_duration_sec,
        max_text_chars=max_text_chars,
    )
    status = "pass" if not issues else "warning" if all(issue.severity == "warning" for issue in issues) else "fail"
    return SubtitleLintResult(str(subtitle_path), status, len(cues), tuple(issues))


def lint_cues(
    cues: list[SubtitleCue],
    *,
    max_cue_duration_sec: float = 45.0,
    max_text_chars: int = 240,
) -> list[SubtitleLintIssue]:
    issues: list[SubtitleLintIssue] = []
    if not cues:
        return [
            SubtitleLintIssue(
                code="subtitle_empty",
                severity="warning",
                cue_index=None,
                message="subtitle file exists but contains no cues",
            )
        ]
    previous_start = -1.0
    previous_end = -1.0
    for cue in cues:
        if not cue.text.strip():
            issues.append(
                SubtitleLintIssue(
                    code="subtitle_blank_text",
                    severity="error",
                    cue_index=cue.index,
                    message="cue text is blank",
                )
            )
        if cue.start_sec < previous_start or cue.start_sec < previous_end:
            issues.append(
                SubtitleLintIssue(
                    code="subtitle_non_monotonic_time",
                    severity="error",
                    cue_index=cue.index,
                    message="cue starts before a previous cue has ended",
                )
            )
        if cue.end_sec <= cue.start_sec:
            issues.append(
                SubtitleLintIssue(
                    code="subtitle_invalid_time_range",
                    severity="error",
                    cue_index=cue.index,
                    message="cue end time must be greater than start time",
                )
            )
        if cue.end_sec - cue.start_sec > max_cue_duration_sec:
            issues.append(
                SubtitleLintIssue(
                    code="subtitle_long_cue_duration",
                    severity="warning",
                    cue_index=cue.index,
                    message=f"cue duration exceeds {max_cue_duration_sec:g} seconds",
                )
            )
        if len(cue.text) > max_text_chars:
            issues.append(
                SubtitleLintIssue(
                    code="subtitle_long_cue_text",
                    severity="warning",
                    cue_index=cue.index,
                    message=f"cue text exceeds {max_text_chars} characters",
                )
            )
        previous_start = cue.start_sec
        previous_end = cue.end_sec
    return issues
