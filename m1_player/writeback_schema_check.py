from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExpectedNotionProperty:
    name: str
    allowed_types: tuple[str, ...]
    required: bool
    note: str


@dataclass(frozen=True)
class SchemaCheckIssue:
    severity: str
    property_name: str
    expected: str
    actual: str
    message: str


@dataclass(frozen=True)
class SchemaCheckResult:
    status: str
    required_count: int
    optional_count: int
    issues: tuple[SchemaCheckIssue, ...]

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


EXPECTED_COMPLETION_PROPERTIES: tuple[ExpectedNotionProperty, ...] = (
    ExpectedNotionProperty("影片名稱", ("title",), True, "completion record title"),
    ExpectedNotionProperty("課程頁 URL", ("url",), True, "source Notion course page URL"),
    ExpectedNotionProperty("段落序號", ("number",), True, "video index inside a course page"),
    ExpectedNotionProperty("最後播放秒數", ("number",), True, "last playback position"),
    ExpectedNotionProperty("進度百分比", ("number",), True, "completion progress percentage"),
    ExpectedNotionProperty("補課狀態", ("select",), True, "completion status"),
    ExpectedNotionProperty("最後更新時間", ("date",), True, "writeback event time"),
    ExpectedNotionProperty("課程日期", ("date",), False, "course date copied from schedule page"),
    ExpectedNotionProperty("完整補課時間", ("date",), False, "completion timestamp"),
    ExpectedNotionProperty("影片 block id", ("rich_text",), False, "Notion video block reference"),
    ExpectedNotionProperty("影片來源", ("rich_text",), False, "video source reference"),
    ExpectedNotionProperty("字幕路徑", ("rich_text",), False, "local subtitle path reference"),
    ExpectedNotionProperty("影片總長秒數", ("number",), False, "known video duration"),
)


def check_completion_data_source_schema(payload: dict[str, Any]) -> SchemaCheckResult:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return SchemaCheckResult(
            status="fail",
            required_count=required_property_count(),
            optional_count=optional_property_count(),
            issues=(
                SchemaCheckIssue(
                    "error",
                    "<properties>",
                    "object",
                    type(properties).__name__,
                    "Notion data source payload does not contain a properties object.",
                ),
            ),
        )
    issues: list[SchemaCheckIssue] = []
    for expected in EXPECTED_COMPLETION_PROPERTIES:
        actual = properties.get(expected.name)
        if not isinstance(actual, dict):
            severity = "error" if expected.required else "warning"
            issues.append(
                SchemaCheckIssue(
                    severity,
                    expected.name,
                    "|".join(expected.allowed_types),
                    "missing",
                    missing_property_message(expected),
                )
            )
            continue
        actual_type = property_type(actual)
        if actual_type not in expected.allowed_types:
            severity = "error" if expected.required else "warning"
            issues.append(
                SchemaCheckIssue(
                    severity,
                    expected.name,
                    "|".join(expected.allowed_types),
                    actual_type or "unknown",
                    type_mismatch_message(expected, actual_type),
                )
            )
    return SchemaCheckResult(
        status=schema_status(issues),
        required_count=required_property_count(),
        optional_count=optional_property_count(),
        issues=tuple(issues),
    )


def result_to_payload(result: SchemaCheckResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "required_count": result.required_count,
        "optional_count": result.optional_count,
        "issues": [
            {
                "severity": issue.severity,
                "property_name": issue.property_name,
                "expected": issue.expected,
                "actual": issue.actual,
                "message": issue.message,
            }
            for issue in result.issues
        ],
    }


def expected_completion_data_source_fixture(data_source_id: str = "m1-completion-data-source-template") -> dict[str, Any]:
    return {
        "object": "data_source",
        "id": data_source_id,
        "properties": {
            expected.name: notion_property_schema(expected.allowed_types[0])
            for expected in EXPECTED_COMPLETION_PROPERTIES
        },
    }


def completion_data_source_markdown_table() -> str:
    lines = [
        "| 欄位 | 型別 | 必填 | 用途 |",
        "| --- | --- | --- | --- |",
    ]
    for expected in EXPECTED_COMPLETION_PROPERTIES:
        required = "是" if expected.required else "否"
        lines.append(
            f"| {expected.name} | {'/'.join(expected.allowed_types)} | {required} | {expected.note} |"
        )
    return "\n".join(lines)


def notion_property_schema(property_type_name: str) -> dict[str, Any]:
    if property_type_name == "title":
        return {"type": "title", "title": {}}
    if property_type_name == "url":
        return {"type": "url", "url": {}}
    if property_type_name == "number":
        return {"type": "number", "number": {"format": "number"}}
    if property_type_name == "select":
        return {"type": "select", "select": {"options": [{"name": "已完成"}]}}
    if property_type_name == "date":
        return {"type": "date", "date": {}}
    if property_type_name == "rich_text":
        return {"type": "rich_text", "rich_text": {}}
    return {"type": property_type_name, property_type_name: {}}


def property_type(value: dict[str, Any]) -> str | None:
    type_value = value.get("type")
    if isinstance(type_value, str):
        return type_value
    for candidate in ("title", "url", "number", "select", "date", "rich_text"):
        if candidate in value:
            return candidate
    return None


def schema_status(issues: list[SchemaCheckIssue]) -> str:
    if any(issue.severity == "error" for issue in issues):
        return "fail"
    if issues:
        return "warning"
    return "pass"


def required_property_count() -> int:
    return sum(1 for item in EXPECTED_COMPLETION_PROPERTIES if item.required)


def optional_property_count() -> int:
    return sum(1 for item in EXPECTED_COMPLETION_PROPERTIES if not item.required)


def missing_property_message(expected: ExpectedNotionProperty) -> str:
    if expected.required:
        return f"Required completion writeback property is missing: {expected.name}."
    return f"Optional completion writeback property is missing: {expected.name}."


def type_mismatch_message(expected: ExpectedNotionProperty, actual_type: str | None) -> str:
    actual_text = actual_type or "unknown"
    return (
        f"Completion writeback property {expected.name} has type {actual_text}; "
        f"expected {' or '.join(expected.allowed_types)}."
    )
