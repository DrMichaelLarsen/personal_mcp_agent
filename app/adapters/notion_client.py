from __future__ import annotations

import re
from typing import Any


class NotionClient:
    """Thin wrapper over Notion operations."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    def create_page(self, database_id: str, properties: dict[str, Any], children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        raise NotImplementedError("Implement Notion page creation with your preferred client.")

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Implement Notion page update with your preferred client.")

    def get_page(self, page_id: str) -> dict[str, Any]:
        raise NotImplementedError("Implement Notion page fetch with your preferred client.")

    def query_database(self, database_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError("Implement Notion database query with your preferred client.")

    def markdown_to_blocks(self, markdown: str) -> list[dict[str, Any]]:
        def _rich_text(value: str) -> list[dict[str, Any]]:
            parts: list[dict[str, Any]] = []
            pattern = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")
            cursor = 0
            for match in pattern.finditer(value):
                if match.start() > cursor:
                    parts.append({"text": value[cursor:match.start()], "annotations": {}})
                token = match.group(0)
                if token.startswith("**") and token.endswith("**"):
                    parts.append({"text": token[2:-2], "annotations": {"bold": True}})
                elif token.startswith("*") and token.endswith("*"):
                    parts.append({"text": token[1:-1], "annotations": {"italic": True}})
                cursor = match.end()
            if cursor < len(value):
                parts.append({"text": value[cursor:], "annotations": {}})
            return parts or [{"text": value, "annotations": {}}]

        blocks: list[dict[str, Any]] = []
        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("> [!IMPORTANT] "):
                blocks.append({"type": "callout", "text": line[len("> [!IMPORTANT] ") :], "color": "yellow_background", "rich_text": _rich_text(line[len("> [!IMPORTANT] ") :])})
                continue
            if line.startswith("> [!TIP] "):
                blocks.append({"type": "callout", "text": line[len("> [!TIP] ") :], "color": "blue_background", "rich_text": _rich_text(line[len("> [!TIP] ") :] )})
                continue
            if line.startswith("### "):
                blocks.append({"type": "heading_3", "text": line[4:], "rich_text": _rich_text(line[4:])})
            elif line.startswith("## "):
                blocks.append({"type": "heading_2", "text": line[3:], "rich_text": _rich_text(line[3:])})
            elif line.startswith("# "):
                blocks.append({"type": "heading_1", "text": line[2:], "rich_text": _rich_text(line[2:])})
            elif line.startswith("- [ ] "):
                blocks.append({"type": "to_do", "text": line[6:], "checked": False, "rich_text": _rich_text(line[6:])})
            elif line.startswith("- [x] "):
                blocks.append({"type": "to_do", "text": line[6:], "checked": True, "rich_text": _rich_text(line[6:])})
            elif line.startswith("- "):
                blocks.append({"type": "bulleted_list_item", "text": line[2:], "rich_text": _rich_text(line[2:])})
            elif line[:2].isdigit() and ". " in line:
                body = line.split(". ", 1)[1]
                blocks.append({"type": "numbered_list_item", "text": body, "rich_text": _rich_text(body)})
            else:
                blocks.append({"type": "paragraph", "text": line, "rich_text": _rich_text(line)})
        return blocks
