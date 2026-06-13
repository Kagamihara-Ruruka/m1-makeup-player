from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .notion_api import NotionApiClient, extract_notion_id, first_data_source_id
from .writeback_schema_check import EXPECTED_COMPLETION_PROPERTIES, check_completion_data_source_schema


DEFAULT_COMPLETION_DATABASE_TITLE = "m_1 補課完成紀錄"


@dataclass(frozen=True)
class CompletionDatabaseBootstrapResult:
    database_id: str
    data_source_id: str
    schema_status: str
    saved_to_settings: bool

    def to_json(self) -> dict[str, object]:
        return {
            "database_id": self.database_id,
            "data_source_id": self.data_source_id,
            "schema_status": self.schema_status,
            "saved_to_settings": self.saved_to_settings,
        }


def build_completion_database_payload(
    parent_page_or_workspace: str,
    *,
    title: str = DEFAULT_COMPLETION_DATABASE_TITLE,
    inline: bool = True,
) -> dict[str, Any]:
    return {
        "parent": parent_payload(parent_page_or_workspace),
        "title": [{"type": "text", "text": {"content": title}}],
        "is_inline": inline,
        "initial_data_source": {
            "properties": completion_database_request_properties(),
        },
    }


def completion_database_request_properties() -> dict[str, Any]:
    return {
        expected.name: notion_create_property_schema(expected.allowed_types[0])
        for expected in EXPECTED_COMPLETION_PROPERTIES
    }


def notion_create_property_schema(property_type_name: str) -> dict[str, Any]:
    if property_type_name == "title":
        return {"title": {}}
    if property_type_name == "url":
        return {"url": {}}
    if property_type_name == "number":
        return {"number": {"format": "number"}}
    if property_type_name == "select":
        return {
            "select": {
                "options": [
                    {"name": "未開始", "color": "gray"},
                    {"name": "補課中", "color": "blue"},
                    {"name": "已完成", "color": "green"},
                ]
            }
        }
    if property_type_name == "date":
        return {"date": {}}
    if property_type_name == "rich_text":
        return {"rich_text": {}}
    return {property_type_name: {}}


def parent_payload(parent_page_or_workspace: str) -> dict[str, Any]:
    value = parent_page_or_workspace.strip()
    if value.lower() == "workspace":
        return {"type": "workspace", "workspace": True}
    return {"type": "page_id", "page_id": extract_notion_id(value)}


def parent_from_schedule_database(client: NotionApiClient, schedule_view_url: str) -> dict[str, Any]:
    schedule_id = extract_notion_id(schedule_view_url)
    try:
        database = client.retrieve_database(schedule_id)
    except RuntimeError:
        data_source = client.retrieve_data_source(schedule_id)
        parent = data_source.get("parent")
        if not isinstance(parent, dict) or parent.get("type") != "database_id":
            raise RuntimeError("schedule data source parent is not a database") from None
        database_id = parent.get("database_id")
        if not isinstance(database_id, str):
            raise RuntimeError("schedule data source parent database id missing")
        database = client.retrieve_database(database_id)
    parent = database.get("parent")
    if not isinstance(parent, dict):
        raise RuntimeError("schedule database parent missing")
    if parent.get("type") == "page_id" and isinstance(parent.get("page_id"), str):
        return {"type": "page_id", "page_id": str(parent["page_id"])}
    if parent.get("type") == "workspace":
        return {"type": "workspace", "workspace": True}
    raise RuntimeError(f"unsupported schedule database parent: {parent.get('type')}")


def extract_first_created_data_source_id(database_payload: dict[str, Any]) -> str:
    data_sources = database_payload.get("data_sources")
    if isinstance(data_sources, list) and data_sources:
        first = data_sources[0]
        if isinstance(first, dict) and isinstance(first.get("id"), str):
            return str(first["id"])
    database_id = database_payload.get("id")
    if isinstance(database_id, str):
        return database_id
    raise RuntimeError("created database response did not include a data source id")


def create_completion_database(
    client: NotionApiClient,
    parent_page_or_workspace: str,
    *,
    title: str = DEFAULT_COMPLETION_DATABASE_TITLE,
    inline: bool = True,
    save_to_settings: bool = False,
) -> CompletionDatabaseBootstrapResult:
    database = client.create_database(
        build_completion_database_payload(parent_page_or_workspace, title=title, inline=inline)
    )
    database_id = str(database["id"])
    data_source_id = first_data_source_id(client, extract_first_created_data_source_id(database))
    schema = client.retrieve_data_source(data_source_id)
    schema_check = check_completion_data_source_schema(schema)
    return CompletionDatabaseBootstrapResult(
        database_id=database_id,
        data_source_id=data_source_id,
        schema_status=schema_check.status,
        saved_to_settings=save_to_settings,
    )
