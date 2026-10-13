import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
import httpx
from pydantic import BaseModel, ConfigDict, Field
from app.config import settings
from app.services import llm_client
from app.models.catalog import CatalogItem
from app.services.mcp_client_service import mcp_client_service
from app.services.neo4j_catalog_graph import platform_neo4j_catalog_graph_service as neo4j_catalog_graph_service
from app.services import servicenow as snow
from app.services import catalog_match_service
from app.services.semantic_query_mapper import semantic_search, parse_semantic_intent

def get_client():
    """Sync OpenAI-compatible client (Ollama primary, Groq fallback)."""
    return llm_client.get_sync_client()


class _OpenAIClientProxy:
    def __getattr__(self, name: str):
        return getattr(get_client(), name)


client = _OpenAIClientProxy()


def get_async_client():
    """Async OpenAI-compatible client (Ollama primary, Groq fallback)."""
    return llm_client.get_async_llm_client()


def get_chat_model() -> str:
    return settings.openai_chat_model


def get_embedding_model(default_model: str = "") -> str:
    return settings.openai_embedding_model(default_model)


def is_chat_configured() -> bool:
    return settings.openai_chat_configured


def is_embedding_configured() -> bool:
    return settings.openai_embedding_configured


def _fallback_search_servicenow(query: str, keywords: List[str]) -> List[Dict[str, Any]]:
    """Fallback search using ServiceNow module directly - no MCP required."""
    try:
        logger.info(f"  [FALLBACK] Starting search with query='{query}', keywords={keywords}")
        all_items = snow.list_catalog_items(limit=500)
        logger.info(f"  [FALLBACK] Got {len(all_items)} items from ServiceNow")
        if not all_items:
            return []

        query_lower = query.lower()
        tokens = set(query_lower.split())
        if keywords:
            tokens.update([k.lower() for k in keywords])
        logger.info(f"  [FALLBACK] tokens to match: {tokens}")

        matched = []
        for item in all_items:
            item_name = (item.get("name") or "").lower()
            item_desc = (item.get("short_description") or "").lower()
            item_category = (item.get("category") or "").lower()
            combined = f"{item_name} {item_desc} {item_category}"

            if any(token in combined for token in tokens):
                matched.append({
                    "sys_id": item.get("sys_id", ""),
                    "name": item.get("name", ""),
                    "short_description": item.get("short_description"),
                    "description": item.get("description"),
                    "category": item.get("category"),
                    "icon": item.get("icon"),
                    "price": item.get("price", "0"),
                    "relevance_score": 0.5,
                })

        logger.info(f"  [FALLBACK] Matched {len(matched)} items: {[m.get('name') for m in matched[:5]]}")
        return matched
    except Exception as e:
        logger.warning(f"  [FALLBACK] ServiceNow search failed: {e}")
        return []

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an enterprise AI assistant for a ServiceNow ITSM platform.
Your job is to understand user requests and classify them into service/change requests.

Always respond with valid JSON matching the requested schema.
"""

FINAL_INTERPRET_PROMPT = """Classify this user request and extract search keywords for catalog matching.

User request: "{text}"

Return only these fields:
- intent: short snake_case intent name (e.g. laptop_request, vpn_access, server_upgrade)
- confidence: float 0.0 to 1.0
- ticket_type: "SR" for service requests (hardware, software, access) or "CH" for change requests (infrastructure changes, system modifications)
- entities: object with:
  - item_type: what is being requested
  - urgency: "1" (critical) / "2" (high) / "3" (medium) / "4" (low)
  - quantity: number if applicable
  - specific_details: any specific requirements mentioned
- clarification_needed: true only if the request is genuinely ambiguous
- clarification_question: question to ask if clarification_needed is true
- search_keywords: CRITICAL - array of 2-4 keywords that best describe what the user wants (e.g., for "mobile device" use ["mobile", "phone", "device"]; for "laptop" use ["laptop", "computer"]; for "vpn access" use ["vpn", "access", "remote"])
"""


class InterpretResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    confidence: float
    entities: "InterpretEntities"
    ticket_type: str
    clarification_needed: bool = False
    clarification_question: Optional[str] = None
    search_keywords: List[str] = Field(default_factory=list)


class InterpretResultFull(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    confidence: float
    entities: "InterpretEntities"
    ticket_type: str
    clarification_needed: bool = False
    clarification_question: Optional[str] = None
    search_keywords: List[str] = Field(default_factory=list)
    suggested_catalogs: List[CatalogItem] = Field(default_factory=list)
    selected_catalog: Optional[CatalogItem] = None


class InterpretEntities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_type: Optional[str] = None
    urgency: Optional[str] = None
    quantity: Optional[str] = None
    specific_details: Optional[str] = None


LOW_CONFIDENCE_THRESHOLD = 0.45
HIGH_CONFIDENCE_THRESHOLD = 0.75


def interpret_request(text: str) -> Dict[str, Any]:
    raise RuntimeError("interpret_request is async; call await interpret_request_async(...) instead")


async def interpret_request_async(text: str) -> Dict[str, Any]:
    logger.info(f"🟢 INTERPRET_REQUEST START: '{text}'")

    async def _run_classify() -> InterpretResult:
        classify_messages = [
            {"role": "system", "content": "You are an enterprise IT service desk classifier. Return structured JSON only."},
            {"role": "user", "content": FINAL_INTERPRET_PROMPT.format(text=text)},
        ]
        async_client = get_async_client()
        completion = await async_client.beta.chat.completions.parse(
            model=settings.openai_chat_model,
            messages=classify_messages,
            temperature=0.1,
            response_format=InterpretResult,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("OpenAI did not return a parsed interpret response")
        return parsed

    logger.info("  Running intent classification first to extract search keywords...")
    parsed = await _run_classify()
    search_keywords = _coerce_keywords(parsed.search_keywords)
    logger.info(f"  🔍 LLM EXTRACTED: intent={parsed.intent}, keywords={search_keywords}, raw_keywords={parsed.search_keywords}")

    intent_data = {"search_keywords": search_keywords}

    raw_results = []
    mcp_available = mcp_client_service.is_available()
    logger.info(f"  [RAW SEARCH] MCP available: {mcp_available}")

    try:
        if mcp_available:
            raw_results = await mcp_client_service.search_catalog_raw(text, intent_data)
            logger.info(f"  [RAW SEARCH] MCP returned {len(raw_results)} items")
    except Exception as e:
        logger.warning(f"  [RAW SEARCH] MCP search failed: {e}")

    if not raw_results:
        raw_results = _fallback_search_servicenow(text, search_keywords)
        logger.info(f"  [RAW SEARCH] Fallback returned {len(raw_results)} items")

    if not raw_results:
        logger.warning("  [RAW SEARCH] All search methods returned no results")
        ranked_results = []
    else:
        # Fast path: Skip LLM reranking, use ServiceNow's native ordering
        # This returns results immediately without waiting for LLM
        ranked_results = raw_results
        logger.info(f"  [FAST PATH] Returning {len(ranked_results)} items without LLM reranking")

    result = parsed.model_dump()
    result["suggested_catalogs"] = [
        CatalogItem(
            sys_id=item.get("sys_id", ""),
            name=item.get("name", ""),
            short_description=item.get("short_description"),
            description=item.get("description"),
            category=item.get("category"),
            icon=item.get("icon"),
            price=item.get("price", "0"),
            relevance_score=item.get("relevance_score", 0.5),
            match_reason=item.get("match_reason", "ServiceNow match")
        ).model_dump()
        for item in ranked_results[:10]
    ]
    result["selected_catalog"] = None
    result["clarification_needed"] = len(ranked_results) > 1 if ranked_results else True

    if result["suggested_catalogs"]:
        logger.info(f"  Top 5 results: {[(c.get('name'), c.get('relevance_score')) for c in result['suggested_catalogs'][:5]]}")

    result = _normalize_interpret_result(result)
    logger.info(f"🟢 INTERPRET_REQUEST END: {len(result.get('suggested_catalogs', []))} catalogs, selected: {result.get('selected_catalog') is not None}")
    return result

RANK_PROMPT = """You are ranking service catalog items by relevance to a user query.

User query: "{query}"

Catalog items:
{items}

CRITICAL: You MUST return a JSON array with an entry for EVERY SINGLE item listed above.
For each item, include:
- sys_id: the catalog item's sys_id (EXACTLY as provided in the input)
- relevance_score: float 0.0 to 1.0 (higher = more relevant)
- reason: one sentence explaining the relevance score

IMPORTANT: Include items with 0.0 relevance score if they don't match the query.
Sort by relevance_score descending.
The output MUST be a valid JSON array with the same number of entries as the input items.
Respond ONLY with valid JSON array, no markdown.
"""

CONVERSATION_PROMPT = """You are an enterprise IT service desk AI assistant.
You help users with their service requests and relay admin questions in a professional, friendly manner.

Current conversation context:
- Ticket: {ticket_number} ({ticket_type})
- State: {state}
- Description: {description}

Admin question to relay to user: "{admin_question}"

Rephrase this question professionally and helpfully for the end user. Be concise and clear.
Respond with just the rephrased message text, no JSON."""


RANK_BATCH_SIZE = 25


def _rank_batch(query: str, batch: List[Dict[str, Any]]) -> tuple[dict, dict]:
    """Rank a single batch of items. Returns (score_map, reason_map)."""
    items_text = json.dumps([
        {"sys_id": c.get("sys_id"), "name": c.get("name"), "description": c.get("short_description", "")}
        for c in batch
    ], indent=2)
    prompt = RANK_PROMPT.format(query=query, items=items_text)
    response = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        ranked = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse OpenAI batch response: {raw[:200]}")
        ranked = []

    score_map = {item["sys_id"]: item.get("relevance_score", 0.0) for item in ranked}
    reason_map = {item["sys_id"]: item.get("reason") for item in ranked}

    # Assign 0.0 to any items OpenAI dropped from this batch
    for c in batch:
        sid = c.get("sys_id")
        if sid not in score_map:
            score_map[sid] = 0.0
            reason_map[sid] = None

    return score_map, reason_map


def rank_catalogs(query: str, catalogs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not catalogs:
        return []

    score_map: dict = {}
    reason_map: dict = {}

    # Split into batches so every item gets a proper LLM score
    batches = [catalogs[i:i + RANK_BATCH_SIZE] for i in range(0, len(catalogs), RANK_BATCH_SIZE)]
    logger.info(f"  RANK_CATALOGS: Ranking {len(catalogs)} items in {len(batches)} parallel batches of {RANK_BATCH_SIZE}")

    # Run all batches in parallel using a thread pool
    with ThreadPoolExecutor(max_workers=len(batches)) as executor:
        futures = [executor.submit(_rank_batch, query, batch) for batch in batches]
        for batch_idx, future in enumerate(futures):
            batch_scores, batch_reasons = future.result()
            score_map.update(batch_scores)
            reason_map.update(batch_reasons)
            logger.debug(f"  Batch {batch_idx + 1}/{len(batches)}: scored {len(batches[batch_idx])} items")

    # Apply scores to all catalog items
    for c in catalogs:
        c["relevance_score"] = score_map.get(c.get("sys_id"), 0.0)
        c["match_reason"] = reason_map.get(c.get("sys_id"))

    scored = sorted(catalogs, key=lambda x: x.get("relevance_score", 0), reverse=True)
    logger.info(f"  RANK_CATALOGS: Top 5 results:")
    for c in scored[:5]:
        logger.info(f"    {c.get('name')}: {c.get('relevance_score')} - {c.get('match_reason')}")
    return scored


_RANK_CONCURRENCY = asyncio.Semaphore(4)


async def _rank_batch_async(query: str, batch: List[Dict[str, Any]]) -> tuple[dict, dict]:
    """Async version of _rank_batch — uses the lazy AsyncOpenAI client."""
    items_text = json.dumps([
        {"sys_id": c.get("sys_id"), "name": c.get("name"), "description": c.get("short_description", "")}
        for c in batch
    ], indent=2)
    prompt = RANK_PROMPT.format(query=query, items=items_text)
    async_client = get_async_client()
    async with _RANK_CONCURRENCY:
        response = await async_client.chat.completions.create(
            model=settings.openai_chat_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        ranked = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse OpenAI batch response: {raw[:200]}")
        ranked = []

    score_map = {item["sys_id"]: item.get("relevance_score", 0.0) for item in ranked}
    reason_map = {item["sys_id"]: item.get("reason") for item in ranked}

    for c in batch:
        sid = c.get("sys_id")
        if sid not in score_map:
            score_map[sid] = 0.0
            reason_map[sid] = None
    return score_map, reason_map


async def rank_catalogs_async(query: str, catalogs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Non-blocking version of rank_catalogs for async callers (FastAPI handlers)."""
    if not catalogs:
        return []

    batches = [catalogs[i:i + RANK_BATCH_SIZE] for i in range(0, len(catalogs), RANK_BATCH_SIZE)]
    logger.info(f"  RANK_CATALOGS_ASYNC: Ranking {len(catalogs)} items in {len(batches)} batches (semaphore=4)")

    results = await asyncio.gather(*(_rank_batch_async(query, b) for b in batches))

    score_map: dict = {}
    reason_map: dict = {}
    for batch_scores, batch_reasons in results:
        score_map.update(batch_scores)
        reason_map.update(batch_reasons)

    for c in catalogs:
        c["relevance_score"] = score_map.get(c.get("sys_id"), 0.0)
        c["match_reason"] = reason_map.get(c.get("sys_id"))

    scored = sorted(catalogs, key=lambda x: x.get("relevance_score", 0), reverse=True)
    logger.info(f"  RANK_CATALOGS_ASYNC: Top 5 results:")
    for c in scored[:5]:
        logger.info(f"    {c.get('name')}: {c.get('relevance_score')} - {c.get('match_reason')}")
    return scored


def relay_admin_question(admin_question: str, context: Dict[str, Any]) -> str:
    prompt = CONVERSATION_PROMPT.format(
        ticket_number=context.get("ticket_number", "N/A"),
        ticket_type=context.get("ticket_type", "SR"),
        state=context.get("state", "open"),
        description=context.get("description", ""),
        admin_question=admin_question,
    )
    response = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


def generate_ai_response(user_message: str, conversation_history: List[Dict[str, str]], context: Dict[str, Any]) -> str:
    messages = [
        {"role": "system", "content": (
            "You are an enterprise IT service desk AI assistant. Help the user with their service request. "
            "Be professional, concise, and helpful. Current context: " + json.dumps(context)
        )}
    ]
    messages.extend(conversation_history[-10:])
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
        temperature=0.4,
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()


def _coerce_keywords(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _enrich_keywords_from_graph(query: str, keywords: List[str]) -> List[str]:
    base: List[str] = []
    seen = set()

    for keyword in keywords:
        normalized = keyword.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            base.append(normalized)

    if neo4j_catalog_graph_service.is_available():
        hints = neo4j_catalog_graph_service.suggest_search_keywords(query, base, limit=8)
        for hint in hints:
            normalized = hint.strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                base.append(normalized)

    return base[:12]


def _normalize_interpret_result(result: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("🟡 NORMALIZE_INTERPRET_RESULT START")
    raw_catalogs = result.get("suggested_catalogs", [])
    logger.info(f"  Raw catalogs count: {len(raw_catalogs)}")
    suggested_catalogs: List[CatalogItem] = []
    for idx, item in enumerate(raw_catalogs if isinstance(raw_catalogs, list) else []):
        try:
            logger.debug(f"  Item {idx}: {item}")
            validated = CatalogItem.model_validate(item)
            suggested_catalogs.append(validated)
            logger.debug(f"  ✓ Item {idx} validated: {validated.name} (score: {validated.relevance_score})")
        except Exception as e:
            logger.warning(f"  ❌ Skipping invalid suggested catalog item {idx}: {item}\n  Error: {e}")

    selected_catalog = result.get("selected_catalog")
    selected = None
    if selected_catalog:
        try:
            selected = CatalogItem.model_validate(selected_catalog)
        except Exception:
            logger.warning("Ignoring invalid selected catalog payload: %r", selected_catalog)

    if selected is None and suggested_catalogs:
        best = max(suggested_catalogs, key=lambda item: item.relevance_score)
        if best.relevance_score >= HIGH_CONFIDENCE_THRESHOLD:
            selected = best

    if suggested_catalogs and selected is None:
        best = max(suggested_catalogs, key=lambda item: item.relevance_score)
        if best.relevance_score < LOW_CONFIDENCE_THRESHOLD:
            result["clarification_needed"] = True
            result["clarification_question"] = (
                result.get("clarification_question")
                or "I found a few possible catalog items, but the request is still ambiguous. Which of these best matches what you need?"
            )

    if (
        result.get("ticket_type") == "SR"
        and not suggested_catalogs
        and selected is None
    ):
        result["clarification_needed"] = True
        result["clarification_question"] = (
            result.get("clarification_question")
            or "I could not find a matching catalog item yet. Could you describe the product, access, or service you need in a little more detail?"
        )

    result["suggested_catalogs"] = [item.model_dump() for item in suggested_catalogs]
    result["selected_catalog"] = selected.model_dump() if selected else None
    result["entities"] = result.get("entities") or {}
    result["search_keywords"] = _coerce_keywords(result.get("search_keywords"))
    return result

