import logging
from typing import Any, Dict, List, Optional
from app.models.catalog import CatalogItem
from app.services.mcp_client_service import mcp_client_service

logger = logging.getLogger(__name__)

HIGH_CONFIDENCE_THRESHOLD = 0.75
LOW_CONFIDENCE_THRESHOLD = 0.45


async def match_catalog(query: str, intent_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Match user query to catalog items using hybrid approach.

    Prioritizes lexical indexing for speed and determinism,
    but can be enhanced with LLM-based ranking when confidence is low.
    """
    logger.info(f"[MATCH] Starting catalog match for query='{query}'")

    # Use MCP with semantic filtering (primary approach)
    # Semantic filtering removes irrelevant items before scoring
    try:
        raw_items = await mcp_client_service.search_catalog(query, intent_data)
        items = [CatalogItem(**item) for item in raw_items]
        logger.info(f"[MATCH] MCP returned {len(items)} items (semantically filtered)")
    except Exception as e:
        logger.exception(f"[MATCH] MCP/ServiceNow error: {e}")
        items = []

    if not items:
        return {
            "suggested_catalogs": [],
            "selected_catalog": None,
            "clarification_needed": True,
            "clarification_question": "I could not find a matching catalog item yet. Could you describe the product, access, or service you need in a little more detail?",
            "match_method": "none",
        }

    selected = _best_match(items)

    if not selected or selected.relevance_score < LOW_CONFIDENCE_THRESHOLD:
        return {
            "suggested_catalogs": items,
            "selected_catalog": None,
            "clarification_needed": True,
            "clarification_question": "I found a few possible catalog items, but the request is still ambiguous. Which of these best matches what you need?",
            "match_method": "llm",
        }

    return {
        "suggested_catalogs": items,
        "selected_catalog": selected if selected.relevance_score >= HIGH_CONFIDENCE_THRESHOLD else None,
        "clarification_needed": False,
        "clarification_question": None,
        "match_method": "llm",
    }


def _best_match(items: List[CatalogItem]) -> Optional[CatalogItem]:
    """Return the highest-scoring item."""
    if not items:
        return None
    return max(items, key=lambda item: item.relevance_score)
