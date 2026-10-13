import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import anyio
import httpx
from mcp.types import CallToolResult, TextContent

from app.config import settings
from app.services import servicenow as snow
from app.services.semantic_query_mapper import parse_semantic_intent

logger = logging.getLogger(__name__)

REQUIRED_TOOLS = {
    "list_catalog_changes",
    "list_catalog_items",
    "get_catalog_item",
    "list_catalog_item_variables",
    "list_service_requests_for_graph",
    "get_service_request_for_graph",
    "get_request_item_for_graph",
    "list_change_requests_for_graph",
    "get_change_request_for_graph",
}


class MCPClientService:
    def __init__(self) -> None:
        self._http_client: Optional[httpx.AsyncClient] = None
        self._lock = anyio.Lock()
        self._tools_cache: List[str] = list(REQUIRED_TOOLS)
        self._connected_url: Optional[str] = None
        self._reconnect_task: Optional[Any] = None
        self._stop_reconnect = False

    async def startup(self) -> None:
        async with self._lock:
            if self._http_client is not None:
                logger.debug("MCP client already connected, skipping startup")
                return

            if not settings.mcp_http_url:
                logger.warning("MCP_HTTP_URL not configured, MCP will be unavailable")
                self._start_reconnect_loop()
                return

            try:
                logger.info(f"Attempting to connect to MCP at {settings.mcp_http_url}")
                async with httpx.AsyncClient() as client:
                    await self._verify_http_endpoint(client)

                # Create persistent client
                self._http_client = httpx.AsyncClient(timeout=30.0)
                self._connected_url = settings.mcp_http_url
                self._stop_reconnect = True  # Stop the reconnect loop on successful connection
                if self._reconnect_task is not None:
                    self._reconnect_task.cancel()
                    try:
                        await self._reconnect_task
                    except Exception:
                        pass

                logger.info(
                    "✓ Connected to ServiceNow MCP via HTTP at %s (tools: %s)",
                    settings.mcp_http_url,
                    ", ".join(self._tools_cache),
                )
            except Exception as e:
                logger.exception(f"Failed to connect to ServiceNow MCP via HTTP at {settings.mcp_http_url}: {str(e)}")
                self._start_reconnect_loop()

    async def _verify_http_endpoint(self, client: httpx.AsyncClient) -> None:
        base_url = (settings.mcp_http_url or "").rstrip("/")
        checks = [
            ("GET", f"{base_url}/health", {200}),
            ("HEAD", f"{base_url}/sse", {200, 405}),
        ]
        last_error: Optional[Exception] = None

        for method, url, ok_statuses in checks:
            try:
                response = await client.request(method, url, timeout=5.0)
                if response.status_code in ok_statuses:
                    logger.debug("MCP connection test %s %s -> %s", method, url, response.status_code)
                    return
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to verify MCP endpoint at {base_url}")

    def _start_reconnect_loop(self) -> None:
        if self._reconnect_task is None and not self._stop_reconnect:
            try:
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())
                logger.info("Started MCP reconnection loop (will retry every 30 seconds)")
            except Exception as e:
                logger.warning("Failed to start reconnection loop: %s", str(e))

    async def shutdown(self) -> None:
        self._stop_reconnect = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except Exception:
                pass
        async with self._lock:
            if self._http_client is not None:
                await self._http_client.aclose()
            self._http_client = None
            self._tools_cache = []
            self._connected_url = None

    async def _reconnect_loop(self) -> None:
        while not self._stop_reconnect:
            try:
                await asyncio.sleep(30)
                if self._http_client is None and not self._stop_reconnect:
                    logger.info("Attempting to reconnect to MCP...")
                    await self.startup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("MCP reconnection attempt failed: %s", str(e))

    def is_available(self) -> bool:
        return self._http_client is not None

    async def status(self) -> Dict[str, Any]:
        return {
            "connected": self._http_client is not None,
            "transport": "http",
            "url": self._connected_url,
            "tools": list(self._tools_cache),
        }

    async def list_tools(self) -> List[str]:
        if self._http_client is None:
            raise RuntimeError("MCP client session is not initialized")
        return list(self._tools_cache)

    async def search_catalog(self, query: str, intent_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        keywords = _coerce_keywords(intent_data.get("search_keywords"))
        logger.info("🎯 MCP SEARCH: query='%s', keywords=%s", query, keywords)

        # Parse semantic intent to get category filters
        semantic_intent = parse_semantic_intent(query)
        allowed_categories = semantic_intent.get("categories", [])
        excluded_categories = semantic_intent.get("exclude_categories", [])
        logger.info("  Semantic filters - allowed: %s, excluded: %s", allowed_categories, excluded_categories)

        raw_items = await self._collect_search_candidates(query, keywords)
        logger.info("  Collected %d candidates from ServiceNow", len(raw_items))

        # Apply semantic category filtering BEFORE scoring
        if allowed_categories or excluded_categories:
            filtered_items = []
            query_lower = query.lower()
            for item in raw_items:
                item_name_lower = (item.get("name", "") or "").lower()
                item_desc_lower = (item.get("short_description", "") or "").lower()
                item_category_lower = (item.get("category", "") or "").lower()
                item_text = f"{item_name_lower} {item_desc_lower} {item_category_lower}"

                # Check if excluded
                if any(excl in item_text for excl in excluded_categories):
                    logger.debug(f"  Filtering out (excluded): {item.get('name')} - matches excluded category")
                    continue

                # Check if allowed (if allowed_categories specified)
                if allowed_categories:
                    # Match if any allowed category OR original query term is in item text
                    has_category_match = any(cat in item_text for cat in allowed_categories)
                    has_query_match = query_lower in item_name_lower
                    if not (has_category_match or has_query_match):
                        logger.debug(f"  Filtering out (semantic): {item.get('name')} - no match in allowed categories or query term")
                        continue

                filtered_items.append(item)

            logger.info(f"  Semantic filtering: {len(raw_items)} → {len(filtered_items)} items")
            raw_items = filtered_items

        normalized = [_normalize_catalog_item(item, query, keywords) for item in raw_items]
        logger.info(f"  Normalized {len(normalized)} items")

        # Semantic filtering already removed irrelevant items
        # Return all items that passed semantic filtering (no limit)
        logger.info(f"  Returning {len(normalized)} semantically-filtered results")
        return normalized

    async def search_catalog_raw(self, query: str, intent_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Raw search that trusts ServiceNow's native search without hardcoded semantic filtering."""
        keywords = _coerce_keywords(intent_data.get("search_keywords"))
        logger.info("🎯 MCP RAW SEARCH: query='%s', keywords=%s", query, keywords)

        # Collect candidates via ServiceNow's native search
        raw_items = await self._collect_search_candidates(query, keywords)
        logger.info("  Collected %d raw candidates from ServiceNow", len(raw_items))

        # Normalize without semantic filtering - trust ServiceNow's results
        normalized = [_normalize_catalog_item_raw(item, query, keywords) for item in raw_items]
        logger.info(f"  Normalized {len(normalized)} items (raw, no filtering)")

        # Sort by ServiceNow's native order (which is by relevance)
        logger.info(f"  Returning {len(normalized)} raw results (sorted by ServiceNow relevance)")
        return normalized

    async def list_catalog_items(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        return await self._list_catalog_items(query=None, limit=limit, offset=offset)

    async def list_catalog_changes(
        self,
        since: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {"limit": limit}
        if since:
            arguments["since"] = since

        result = await self.call_tool("list_catalog_changes", arguments)
        payload = self._parse_json_result(result) or {}
        return payload if isinstance(payload, dict) else {}

    async def get_catalog_form(self, sys_id: str) -> Optional[Dict[str, Any]]:
        # Call both API endpoints in parallel (was sequential before)
        item_task = self.call_tool("get_catalog_item", {"item_id": sys_id})
        variables_task = self.call_tool(
            "list_catalog_item_variables",
            {
                "catalog_item_id": sys_id,
                "include_details": True,
            },
        )

        item_result, variable_result = await asyncio.gather(item_task, variables_task)

        item_payload = self._parse_json_result(item_result) or {}
        if not item_payload or not item_payload.get("success"):
            return None

        variable_payload = self._parse_json_result(variable_result) or {}
        variables = variable_payload.get("variables", []) if isinstance(variable_payload, dict) else []

        return {
            "item": _normalize_catalog_item(item_payload.get("data") or {}, "", []),
            "variables": [self._normalize_catalog_variable(variable) for variable in variables],
        }

    async def list_service_requests_for_graph(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        result = await self.call_tool("list_service_requests_for_graph", {"limit": limit, "offset": offset})
        payload = self._parse_json_result(result) or {}
        return payload.get("records", []) if isinstance(payload, dict) else []

    async def get_service_request_for_graph(self, sys_id: str) -> Optional[Dict[str, Any]]:
        result = await self.call_tool("get_service_request_for_graph", {"sys_id": sys_id})
        payload = self._parse_json_result(result) or {}
        return payload if isinstance(payload, dict) and payload.get("ticket") else None

    async def get_request_item_for_graph(self, sys_id: str) -> Optional[Dict[str, Any]]:
        result = await self.call_tool("get_request_item_for_graph", {"sys_id": sys_id})
        payload = self._parse_json_result(result) or {}
        return payload.get("request_item") if isinstance(payload, dict) else None

    async def list_change_requests_for_graph(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        result = await self.call_tool("list_change_requests_for_graph", {"limit": limit, "offset": offset})
        payload = self._parse_json_result(result) or {}
        return payload.get("records", []) if isinstance(payload, dict) else []

    async def get_change_request_for_graph(self, sys_id: str) -> Optional[Dict[str, Any]]:
        result = await self.call_tool("get_change_request_for_graph", {"sys_id": sys_id})
        payload = self._parse_json_result(result) or {}
        return payload if isinstance(payload, dict) and payload.get("ticket") else None

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> CallToolResult:
        if self._http_client is None:
            raise RuntimeError("MCP client session is not initialized")

        try:
            logger.debug("call_tool: name=%s, arguments=%s", name, arguments)
            response = await self._http_client.post(
                f"{settings.mcp_http_url}/tools/{name}",
                json=arguments or {},
                timeout=30.0
            )
            response.raise_for_status()

            result_data = response.json()
            logger.debug("call_tool: result keys=%s", list(result_data.keys()) if isinstance(result_data, dict) else type(result_data))
            if not result_data.get("success"):
                error_msg = result_data.get("error", f"MCP tool '{name}' failed")
                raise RuntimeError(error_msg)

            result_text = result_data.get("result", "")
            return CallToolResult(
                isError=False,
                content=[TextContent(type="text", text=result_text)]
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            logger.error(f"MCP connection lost: {name} - {str(e)}")
            self._http_client = None
            self._start_reconnect_loop()
            raise
        except Exception as e:
            logger.error(f"MCP tool call failed: {name} - {str(e)}", exc_info=True)
            raise

    async def _collect_search_candidates(self, query: str, keywords: List[str]) -> List[Dict[str, Any]]:
        deduped: Dict[str, Dict[str, Any]] = {}

        for candidate_query in _build_search_queries(query, keywords):
            items = await self._list_catalog_items(query=candidate_query, limit=25, offset=0)
            _merge_catalog_items(deduped, items)
            logger.info(f"  [MCP] Candidate '{candidate_query}': {len(items)} items, total: {len(deduped)}")

        items = await self._list_catalog_items(query=None, limit=50, offset=0)
        logger.info(f"  [MCP] Broad search (no query): {len(items)} items")
        initial_count = len(deduped)
        _merge_catalog_items(deduped, items)
        logger.info(f"  Broad search added: {len(deduped) - initial_count} new items (total: {len(deduped)})")

        if deduped:
            sample = list(deduped.values())[0]
            logger.info(f"  [MCP] First 3 item sys_ids: {list(deduped.keys())[:3]}")
            logger.info(f"  [MCP] Sample item: name='{sample.get('name')}', sys_id='{sample.get('sys_id')}'")

        return list(deduped.values())

    async def _list_catalog_items(self, query: Optional[str], limit: int, offset: int) -> List[Dict[str, Any]]:
        arguments: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": True,
        }
        if query:
            arguments["query"] = query

        logger.info(f"  [MCP] _list_catalog_items: query={query}, limit={limit}")
        result = await self.call_tool("list_catalog_items", arguments)
        payload = self._parse_json_result(result) or {}
        items = payload.get("items", []) if isinstance(payload, dict) else []
        logger.info(f"  [MCP] _list_catalog_items returned {len(items)} items, payload keys: {list(payload.keys())}")
        return items

    def _parse_json_result(self, result: CallToolResult) -> Any:
        if result.structuredContent is not None:
            logger.info(f"  [MCP] Got structuredContent")
            return result.structuredContent

        text = self._extract_text(result)
        if not text:
            logger.warning("  [MCP] No text in result")
            return None
        try:
            parsed = json.loads(text)
            logger.info(f"  [MCP] Parsed JSON with {len(parsed) if isinstance(parsed, (dict, list)) else 0} items")
            return parsed
        except json.JSONDecodeError:
            logger.warning("MCP tool returned non-JSON text payload: %s", text[:500])
            return text

    def _normalize_catalog_variable(self, variable: Dict[str, Any]) -> Dict[str, Any]:
        normalized = _normalize_catalog_variable(variable)
        if normalized["type"] not in {"select", "radio"}:
            return normalized

        try:
            choices = snow.list_variable_choices(normalized["sys_id"])
        except Exception:
            logger.exception("Failed to load choices for catalog variable %s", normalized["sys_id"])
            return normalized

        normalized["choices"] = [
            {
                "label": str(choice.get("text") or choice.get("value") or ""),
                "value": str(choice.get("value") or choice.get("text") or ""),
            }
            for choice in choices
            if choice.get("text") or choice.get("value")
        ]
        return normalized

    @staticmethod
    def _extract_text(result: CallToolResult) -> str:
        chunks: List[str] = []
        for block in result.content:
            if isinstance(block, TextContent):
                chunks.append(block.text)
                continue

            block_type = getattr(block, "type", None)
            block_text = getattr(block, "text", None)
            if block_type == "text" and isinstance(block_text, str):
                chunks.append(block_text)
                continue

            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "".join(chunks).strip()


def _normalize_catalog_item(item: Dict[str, Any], query: str, keywords: List[str]) -> Dict[str, Any]:
    name = str(item.get("name", "") or "")
    short_description = str(item.get("short_description", "") or "")
    description = str(item.get("description", "") or "")
    category = _ref_display(item.get("category")) or _as_optional_string(item.get("category_name")) or _ref_value(item.get("category")) or ""
    category_sys_id = _as_optional_string(item.get("category_sys_id")) or _ref_value(item.get("category"))
    category_name = _as_optional_string(item.get("category_name")) or _ref_display(item.get("category"))

    # Simplified: all semantically-filtered items are valid matches
    # No complex scoring needed - semantic filtering handles relevance
    score = 1.0 if query else 0.8

    return {
        "sys_id": str(item.get("sys_id", "")),
        "name": name,
        "display_name": _as_optional_string(item.get("display_name") or name),
        "short_description": _as_optional_string(short_description),
        "description": _as_optional_string(description),
        "category": _as_optional_string(category),
        "category_sys_id": category_sys_id,
        "category_name": category_name or _as_optional_string(category),
        "icon": _as_optional_string(item.get("picture") or item.get("icon")),
        "price": _as_optional_string(item.get("price")),
        "search_text": _search_text(name, short_description, description, category, category_name),
        "relevance_score": 1.0,
    }


def _normalize_catalog_item_raw(item: Dict[str, Any], query: str, keywords: List[str]) -> Dict[str, Any]:
    """Normalize catalog item for raw search - preserves results without filtering."""
    name = str(item.get("name", "") or "")
    short_description = str(item.get("short_description", "") or "")
    description = str(item.get("description", "") or "")
    category = _ref_display(item.get("category")) or _as_optional_string(item.get("category_name")) or _ref_value(item.get("category")) or ""
    category_sys_id = _as_optional_string(item.get("category_sys_id")) or _ref_value(item.get("category"))
    category_name = _as_optional_string(item.get("category_name")) or _ref_display(item.get("category"))

    # Return with default score - will be reranked by LLM later
    return {
        "sys_id": str(item.get("sys_id", "")),
        "name": name,
        "display_name": _as_optional_string(item.get("display_name") or name),
        "short_description": _as_optional_string(short_description),
        "description": _as_optional_string(description),
        "category": _as_optional_string(category),
        "category_sys_id": category_sys_id,
        "category_name": category_name or _as_optional_string(category),
        "icon": _as_optional_string(item.get("picture") or item.get("icon")),
        "price": _as_optional_string(item.get("price")),
        "search_text": _search_text(name, short_description, description, category, category_name),
        "relevance_score": 0.5,  # Default, will be reranked
    }


def _normalize_catalog_variable(variable: Dict[str, Any]) -> Dict[str, Any]:
    depends_on = variable.get("depends_on")
    depends_on_value = None
    if depends_on:
        if isinstance(depends_on, dict):
            # Try to extract sys_id from dict: try 'value' first, then 'sys_id', then 'link'
            depends_on_value = (
                depends_on.get("value") or
                depends_on.get("sys_id") or
                depends_on.get("display_value")  # Fallback to display_value if available
            )
        else:
            depends_on_value = str(depends_on) if depends_on else None

    inactive_on_value = variable.get("inactive_on_value")
    active_on_value = variable.get("active_on_value")

    # If active_on_value not explicitly set but we have depends_on,
    # active_on_value might be needed for conditional visibility
    if depends_on_value and not active_on_value:
        # Try to extract from alternative fields
        active_on_value = variable.get("active_condition") or variable.get("question_choice")

    return {
        "sys_id": str(variable.get("sys_id", "")),
        "name": str(variable.get("name") or variable.get("sys_id") or ""),
        "label": str(variable.get("question_text") or variable.get("label") or variable.get("name") or "Field"),
        "type": _map_variable_type(variable.get("type")),
        "mandatory": _is_true(variable.get("mandatory")),
        "default_value": _as_optional_string(variable.get("default_value")),
        "depends_on": depends_on_value,
        "inactive_on_value": _as_optional_string(inactive_on_value),
        "active_on_value": _as_optional_string(active_on_value),
        "choices": [],
    }


def _map_variable_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip().lower()
    mapping = {
        "1": "text",
        "2": "textarea",
        "3": "select",
        "4": "checkbox",
        "5": "select",
        "6": "reference",
        "7": "checkbox",
        "8": "radio",
        "9": "date",
        "10": "datetime",
        "11": "label",
        "single line text": "text",
        "multi line text": "textarea",
        "multiple choice": "select",
        "select box": "select",
        "checkbox": "checkbox",
        "reference": "reference",
        "date": "date",
        "date/time": "datetime",
    }
    return mapping.get(value, "text")


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _as_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ref_value(field: Any) -> Optional[str]:
    if isinstance(field, dict):
        return _as_optional_string(field.get("value") or field.get("sys_id"))
    return _as_optional_string(field)


def _ref_display(field: Any) -> Optional[str]:
    if isinstance(field, dict):
        return _as_optional_string(field.get("display_value") or field.get("name") or field.get("label"))
    return _as_optional_string(field)


def _search_text(*parts: Any) -> Optional[str]:
    text = " ".join(str(part or "").strip() for part in parts if str(part or "").strip()).lower()
    return text or None


def _coerce_keywords(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _build_search_queries(query: str, keywords: List[str]) -> List[str]:
    queries: List[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = " ".join(candidate.strip().split())
        key = normalized.lower()
        if not normalized or key in seen:
            return
        seen.add(key)
        queries.append(normalized)

    add(query)
    for keyword in keywords:
        add(keyword)

    tokens = _tokenize_for_search(query)
    for keyword in keywords:
        for token in _tokenize_for_search(keyword):
            if token not in tokens:
                tokens.append(token)

    # Add synonym tokens
    expanded_tokens = list(tokens)
    for token in tokens:
        synonyms = _CATEGORY_SYNONYMS.get(token, [])
        for syn in synonyms:
            if syn not in expanded_tokens:
                expanded_tokens.append(syn)

    for size in (3, 2, 1):
        for phrase in _phrases(expanded_tokens, size):
            add(phrase)

    return queries[:12]


def _tokenize_for_search(text: str) -> List[str]:
    cleaned = []
    for raw in text.lower().replace("/", " ").replace("-", " ").split():
        token = raw.strip(".,:;!?()[]{}'\"")
        if len(token) < 3 or token in _STOP_WORDS:
            continue
        cleaned.append(token)
    return cleaned


def _phrases(tokens: List[str], size: int) -> List[str]:
    if len(tokens) < size:
        return []
    return [" ".join(tokens[i : i + size]) for i in range(len(tokens) - size + 1)]


def _merge_catalog_items(target: Dict[str, Dict[str, Any]], items: List[Dict[str, Any]]) -> None:
    for item in items:
        sys_id = str(item.get("sys_id", "")).strip()
        if not sys_id:
            continue
        target.setdefault(sys_id, item)


def _category_hint_terms(tokens: List[str]) -> List[str]:
    related = []
    for token in tokens:
        related.extend(_CATEGORY_SYNONYMS.get(token, []))
    return related


def _should_expand_catalog_search(query: str, keywords: List[str], candidate_count: int) -> bool:
    if candidate_count == 0:
        return True

    tokens = _tokenize_for_search(query)
    for keyword in keywords:
        for token in _tokenize_for_search(keyword):
            if token not in tokens:
                tokens.append(token)

    if candidate_count < 3 and len(tokens) <= 2:
        return True

    return any(token in _GENERIC_REQUEST_TERMS for token in tokens) and candidate_count < 5


_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "have",
    "need",
    "want",
    "please",
    "new",
    "get",
    "give",
    "help",
    "employee",
    "user",
    "my",
    "our",
}


_CATEGORY_SYNONYMS = {
    "laptop": ["hardware", "computer", "device"],
    "mobile": ["phone", "cell", "device", "wireless"],
    "phone": ["mobile", "cell", "device", "wireless"],
    "vpn": ["access", "network", "remote"],
    "access": ["permission", "account", "login"],
    "software": ["application", "license", "tool"],
    "monitor": ["hardware", "display", "device"],
}

_GENERIC_REQUEST_TERMS = {
    "mobile",
    "phone",
    "laptop",
    "monitor",
    "software",
    "access",
    "account",
    "vpn",
    "server",
    "database",
    "hardware",
    "device",
}


mcp_client_service = MCPClientService()
