from __future__ import annotations

import re
from typing import Any

import httpx


class NotionClient:
    """Thin wrapper over Notion operations."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self.base_url = "https://api.notion.com/v1"
        self.version = "2022-06-28"
        self._database_schema_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("Notion API key is not configured. Set PPMCP_NOTION_API_KEY.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=30.0) as client:
            response = client.request(method, url, headers=self._headers(), json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise RuntimeError(f"Notion API error {exc.response.status_code} on {path}: {detail}") from exc
        return response.json()

    def _encode_property(
        self,
        name: str,
        value: Any,
        expected_type: str | None = None,
        property_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        lower_name = name.lower().strip()

        def _looks_like_notion_id(raw: str) -> bool:
            return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", raw, re.IGNORECASE))

        if value is None:
            return None

        if expected_type:
            return self._encode_property_by_type(expected_type, value, property_schema)

        relation_like = {
            "project",
            "area",
            "parent",
            "parent project",
            "parent area",
            "goal",
            "dependency_of",
            "depends_on",
            "contexts",
            "context",
        }
        date_like = {"scheduled", "deadline", "due date", "target deadline"}
        number_like = {"budget", "importance", "time required", "estimated minutes", "score", "ai_cost"}

        if lower_name in date_like and isinstance(value, str):
            return {"date": {"start": value}}

        if lower_name in relation_like:
            if isinstance(value, str):
                return {"relation": [{"id": value}]} if _looks_like_notion_id(value) else {"relation": []}
            if isinstance(value, list):
                rels = [{"id": item} for item in value if isinstance(item, str) and _looks_like_notion_id(item)]
                return {"relation": rels}
            return {"relation": []}

        if lower_name in {"assigned", "assignee", "owner"}:
            if isinstance(value, str):
                return {"people": [{"id": value}]} if _looks_like_notion_id(value) else {"people": []}
            if isinstance(value, list):
                ppl = [{"id": item} for item in value if isinstance(item, str) and _looks_like_notion_id(item)]
                return {"people": ppl}
            return {"people": []}

        if lower_name in {"tags", "tag"}:
            if isinstance(value, list):
                return {"multi_select": [{"name": item} for item in value if isinstance(item, str) and item.strip()]}
            if isinstance(value, str) and value.strip():
                return {"multi_select": [{"name": value.strip()}]}
            return {"multi_select": []}

        if lower_name in {"phone", "phone number"}:
            return {"phone_number": str(value)}

        if lower_name in number_like:
            if isinstance(value, (int, float)):
                return {"number": value}
            if isinstance(value, str):
                try:
                    return {"number": float(value)}
                except ValueError:
                    return None
            return None

        if isinstance(value, bool):
            return {"checkbox": value}
        if isinstance(value, (int, float)):
            return {"number": value}
        if isinstance(value, list):
            if all(isinstance(item, str) for item in value):
                return {"multi_select": [{"name": item} for item in value]}
            return {"rich_text": [{"type": "text", "text": {"content": str(value)}}]}
        if isinstance(value, str):
            stripped = value.strip()
            if lower_name.endswith("url") or lower_name == "url":
                return {"url": stripped}
            if re.match(r"^\d{4}-\d{2}-\d{2}(t\d{2}:\d{2}(:\d{2})?)?", stripped, re.IGNORECASE):
                return {"date": {"start": stripped}}
            if lower_name in {"status", "state"}:
                return {"status": {"name": stripped}}
            if lower_name in {"priority"}:
                return {"select": {"name": stripped}}
            if len(stripped) <= 150:
                return {"title": [{"type": "text", "text": {"content": stripped}}]}
            return {"rich_text": [{"type": "text", "text": {"content": stripped[:2000]}}]}
        return {"rich_text": [{"type": "text", "text": {"content": str(value)}}]}

    def _encode_property_by_type(
        self,
        expected_type: str,
        value: Any,
        property_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        expected = expected_type.strip().lower()

        def _id_list(raw: Any) -> list[dict[str, str]]:
            if isinstance(raw, str):
                raw = [raw]
            if not isinstance(raw, list):
                return []
            out: list[dict[str, str]] = []
            for item in raw:
                if isinstance(item, str) and re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", item, re.IGNORECASE):
                    out.append({"id": item})
            return out

        if expected == "title":
            return {"title": [{"type": "text", "text": {"content": str(value)[:2000]}}]}
        if expected == "rich_text":
            return {"rich_text": [{"type": "text", "text": {"content": str(value)[:2000]}}]}
        if expected == "number":
            if isinstance(value, (int, float)):
                return {"number": value}
            if isinstance(value, str):
                try:
                    return {"number": float(value)}
                except ValueError:
                    return None
            return None
        if expected in {"select", "status"}:
            if not isinstance(value, str) or not value.strip():
                return None
            option_name = value.strip()
            options = []
            if property_schema:
                inner = property_schema.get(expected) or {}
                options = [opt.get("name") for opt in inner.get("options", []) if isinstance(opt, dict) and opt.get("name")]
            if options and option_name not in options:
                return None
            return {expected: {"name": option_name}}
        if expected == "multi_select":
            if isinstance(value, str):
                values = [value]
            elif isinstance(value, list):
                values = [item for item in value if isinstance(item, str)]
            else:
                values = []
            return {"multi_select": [{"name": item.strip()} for item in values if item.strip()]}
        if expected == "date":
            if isinstance(value, str) and value.strip():
                return {"date": {"start": value.strip()}}
            if isinstance(value, dict) and value.get("start"):
                return {"date": {"start": value.get("start"), "end": value.get("end")}}
            return None
        if expected == "people":
            return {"people": _id_list(value)}
        if expected == "relation":
            return {"relation": _id_list(value)}
        if expected == "checkbox":
            return {"checkbox": bool(value)}
        if expected == "url":
            return {"url": str(value)}
        if expected == "email":
            return {"email": str(value)}
        if expected == "phone_number":
            return {"phone_number": str(value)}

        # Not writable or unknown types (formula, rollup, created_time, etc.)
        return None

    def _encode_properties(
        self,
        properties: dict[str, Any],
        expected_types: dict[str, str] | None = None,
        property_schemas: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        encoded: dict[str, Any] = {}
        for key, value in properties.items():
            if not key:
                continue
            if expected_types is not None and key not in expected_types:
                # Skip unmapped properties to avoid Notion validation errors when
                # user schema differs from defaults (e.g. Notes/Source ID missing).
                continue
            expected_type = expected_types.get(key) if expected_types else None
            prop = self._encode_property(
                key,
                value,
                expected_type=expected_type,
                property_schema=property_schemas.get(key) if property_schemas else None,
            )
            if prop is not None:
                encoded[key] = prop
        return encoded

    def _get_database_schema(self, database_id: str) -> dict[str, dict[str, Any]]:
        if database_id in self._database_schema_cache:
            return self._database_schema_cache[database_id]
        raw = self._request("GET", f"/databases/{database_id}")
        properties = raw.get("properties", {})
        schema = {name: definition for name, definition in properties.items() if isinstance(definition, dict)}
        self._database_schema_cache[database_id] = schema
        return schema

    def _get_database_property_types(self, database_id: str) -> dict[str, str]:
        schema = self._get_database_schema(database_id)
        mapping: dict[str, str] = {}
        for name, definition in schema.items():
            ptype = definition.get("type")
            if ptype:
                mapping[name] = ptype
        return mapping

    def _get_parent_database_id(self, page_id: str) -> str | None:
        raw = self._request("GET", f"/pages/{page_id}")
        parent = raw.get("parent") or {}
        return parent.get("database_id")

    def _normalize_property(self, value: dict[str, Any]) -> Any:
        ptype = value.get("type")
        if not ptype:
            if "title" in value:
                ptype = "title"
            elif "rich_text" in value:
                ptype = "rich_text"
            elif "status" in value:
                ptype = "status"
            elif "select" in value:
                ptype = "select"
            elif "multi_select" in value:
                ptype = "multi_select"
            elif "relation" in value:
                ptype = "relation"
            elif "date" in value:
                ptype = "date"
            elif "number" in value:
                ptype = "number"
            elif "checkbox" in value:
                ptype = "checkbox"
            elif "url" in value:
                ptype = "url"
            elif "formula" in value:
                ptype = "formula"
            elif "rollup" in value:
                ptype = "rollup"

        if ptype == "title":
            parts = value.get("title", [])
            return "".join(part.get("plain_text", "") for part in parts)
        if ptype == "rich_text":
            parts = value.get("rich_text", [])
            return "".join(part.get("plain_text", "") for part in parts)
        if ptype == "status":
            status = value.get("status")
            return (status or {}).get("name")
        if ptype == "select":
            sel = value.get("select")
            return (sel or {}).get("name")
        if ptype == "multi_select":
            return [item.get("name") for item in value.get("multi_select", []) if item.get("name")]
        if ptype == "relation":
            ids = [item.get("id") for item in value.get("relation", []) if item.get("id")]
            if not ids:
                return None
            if len(ids) == 1:
                return ids[0]
            return ids
        if ptype == "people":
            return [item.get("id") for item in value.get("people", []) if item.get("id")]
        if ptype == "date":
            date_value = value.get("date")
            return (date_value or {}).get("start")
        if ptype == "number":
            return value.get("number")
        if ptype == "checkbox":
            return value.get("checkbox")
        if ptype == "url":
            return value.get("url")
        if ptype == "phone_number":
            return value.get("phone_number")
        if ptype == "email":
            return value.get("email")
        if ptype == "formula":
            formula = value.get("formula") or {}
            ftype = formula.get("type")
            if ftype == "number":
                return formula.get("number")
            if ftype == "string":
                return formula.get("string")
            if ftype == "boolean":
                return formula.get("boolean")
            if ftype == "date":
                date_value = formula.get("date") or {}
                return date_value.get("start")
            return formula
        if ptype == "rollup":
            rollup = value.get("rollup") or {}
            rtype = rollup.get("type")
            if rtype == "number":
                return rollup.get("number")
            if rtype == "date":
                date_value = rollup.get("date") or {}
                return date_value.get("start")
            if rtype == "array":
                arr = rollup.get("array") or []
                normalized_arr = [self._normalize_property(item) if isinstance(item, dict) else item for item in arr]
                if not normalized_arr:
                    return None
                if len(normalized_arr) == 1:
                    return normalized_arr[0]
                return normalized_arr
            return rollup
        return value

    def _normalize_page(self, raw: dict[str, Any]) -> dict[str, Any]:
        props = raw.get("properties", {})
        normalized_props = {key: self._normalize_property(value) for key, value in props.items()}
        title = ""
        for value in props.values():
            if value.get("type") == "title":
                title = "".join(part.get("plain_text", "") for part in value.get("title", []))
                break
        return {
            "id": raw.get("id"),
            "url": raw.get("url"),
            "title": title,
            "properties": normalized_props,
        }

    def create_page(self, database_id: str, properties: dict[str, Any], children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        property_schemas = self._get_database_schema(database_id)
        property_types = self._get_database_property_types(database_id)
        payload: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": self._encode_properties(properties, property_types, property_schemas),
        }
        children_to_send = None
        if children:
            children_to_send = children
            encoded_children = self._encode_blocks(children_to_send)
            chunks = self._chunk_blocks(encoded_children, max_blocks=100)
            if chunks:
                payload["children"] = chunks[0]
        raw = self._request("POST", "/pages", payload)
        if children and raw.get("id"):
            if children_to_send is None:
                children_to_send = []
            encoded_children = self._encode_blocks(children_to_send)
            chunks = self._chunk_blocks(encoded_children, max_blocks=100)
            for chunk in chunks[1:]:
                self._append_children(raw["id"], chunk)
        normalized = self._normalize_page(raw)
        if children_to_send:
            normalized["children"] = children_to_send
        return normalized

    def _chunk_blocks(self, blocks: list[dict[str, Any]], max_blocks: int = 100) -> list[list[dict[str, Any]]]:
        if max_blocks <= 0:
            return [blocks] if blocks else []
        return [blocks[i : i + max_blocks] for i in range(0, len(blocks), max_blocks)]

    def _append_children(self, page_id: str, encoded_blocks: list[dict[str, Any]]) -> None:
        if not encoded_blocks:
            return
        self._request("PATCH", f"/blocks/{page_id}/children", {"children": encoded_blocks})

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        db_id = self._get_parent_database_id(page_id)
        property_types = self._get_database_property_types(db_id) if db_id else None
        property_schemas = self._get_database_schema(db_id) if db_id else None
        payload = {"properties": self._encode_properties(properties, property_types, property_schemas)}
        raw = self._request("PATCH", f"/pages/{page_id}", payload)
        return self._normalize_page(raw)

    def get_page(self, page_id: str) -> dict[str, Any]:
        raw = self._request("GET", f"/pages/{page_id}")
        return self._normalize_page(raw)

    def query_database(self, database_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {}
        if filters:
            query_text = filters.get("query")
            if isinstance(query_text, str) and query_text.strip():
                payload["filter"] = {
                    "or": [
                        {
                            "property": "Name",
                            "title": {"contains": query_text},
                        }
                    ]
                }
            else:
                and_filters: list[dict[str, Any]] = []
                for prop, value in filters.items():
                    if prop == "query" or value is None:
                        continue
                    if isinstance(value, str):
                        if re.match(r"^\d{4}-\d{2}-\d{2}", value):
                            and_filters.append({"property": prop, "date": {"equals": value}})
                        else:
                            and_filters.append(
                                {
                                    "or": [
                                        {"property": prop, "rich_text": {"equals": value}},
                                        {"property": prop, "title": {"equals": value}},
                                        {"property": prop, "select": {"equals": value}},
                                        {"property": prop, "status": {"equals": value}},
                                    ]
                                }
                            )
                    elif isinstance(value, bool):
                        and_filters.append({"property": prop, "checkbox": {"equals": value}})
                    elif isinstance(value, int | float):
                        and_filters.append({"property": prop, "number": {"equals": value}})
                if and_filters:
                    payload["filter"] = {"and": and_filters}

        raw = self._request("POST", f"/databases/{database_id}/query", payload)
        return [self._normalize_page(item) for item in raw.get("results", [])]

    def _encode_blocks(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        notion_blocks: list[dict[str, Any]] = []
        for block in blocks:
            btype = block.get("type", "paragraph")
            rich = block.get("rich_text") or [{"text": block.get("text", ""), "annotations": {}}]
            rich_text = []
            for item in rich:
                text_payload = {"content": item.get("text", "")[:2000]}
                if item.get("link"):
                    text_payload["link"] = {"url": item.get("link")}
                rich_text.append(
                    {
                        "type": "text",
                        "text": text_payload,
                        "annotations": {
                            "bold": item.get("annotations", {}).get("bold", False),
                            "italic": item.get("annotations", {}).get("italic", False),
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "default",
                        },
                    }
                )
            if btype == "callout":
                notion_blocks.append(
                    {
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "rich_text": rich_text,
                            "icon": {"emoji": "💡"},
                            "color": block.get("color", "default"),
                        },
                    }
                )
            elif btype == "to_do":
                notion_blocks.append(
                    {
                        "object": "block",
                        "type": "to_do",
                        "to_do": {
                            "rich_text": rich_text,
                            "checked": bool(block.get("checked", False)),
                            "color": "default",
                        },
                    }
                )
            elif btype in {"heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "paragraph"}:
                notion_blocks.append(
                    {
                        "object": "block",
                        "type": btype,
                        btype: {
                            "rich_text": rich_text,
                            "color": "default",
                        },
                    }
                )
            else:
                notion_blocks.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": rich_text,
                            "color": "default",
                        },
                    }
                )
        return notion_blocks

    def markdown_to_blocks(self, markdown: str) -> list[dict[str, Any]]:
        def _rich_text(value: str) -> list[dict[str, Any]]:
            def _parse_emphasis_and_urls(segment: str) -> list[dict[str, Any]]:
                chunks: list[dict[str, Any]] = []
                pattern = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|https?://[^\s)\]>\"]+)")
                cursor = 0
                for match in pattern.finditer(segment):
                    if match.start() > cursor:
                        chunks.append({"text": segment[cursor:match.start()], "annotations": {}})
                    token = match.group(0)
                    if token.startswith("**") and token.endswith("**"):
                        chunks.append({"text": token[2:-2], "annotations": {"bold": True}})
                    elif token.startswith("*") and token.endswith("*"):
                        chunks.append({"text": token[1:-1], "annotations": {"italic": True}})
                    else:
                        chunks.append({"text": token, "annotations": {}, "link": token})
                    cursor = match.end()
                if cursor < len(segment):
                    chunks.append({"text": segment[cursor:], "annotations": {}})
                return chunks

            parts: list[dict[str, Any]] = []
            link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
            cursor = 0
            for match in link_pattern.finditer(value):
                if match.start() > cursor:
                    parts.extend(_parse_emphasis_and_urls(value[cursor:match.start()]))
                parts.append({"text": match.group(1), "annotations": {}, "link": match.group(2)})
                cursor = match.end()
            if cursor < len(value):
                parts.extend(_parse_emphasis_and_urls(value[cursor:]))
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
