"""
Hybrid Catalog Retrieval: LLM + Lexical Index

Combines OpenAI-based semantic ranking with lexical indexing for robust catalog retrieval.

Strategy:
1. PRIMARY: Use LLM ranking (semantic understanding) when available
2. FALLBACK: Use lexical index when LLM unavailable (faster, deterministic)
3. ENRICHMENT: Use lexical index to suggest keywords for LLM
4. CONFIDENCE: Lexical scores help detect when LLM confidence is questionable
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from app.services import catalog_lexical_index
from app.services.mcp_client_service import mcp_client_service
from app.services import servicenow as snow

logger = logging.getLogger(__name__)


async def search_catalog_hybrid(
    query: str,
    intent_data: Dict[str, Any],
    use_llm: bool = True,
) -> List[Dict[str, Any]]:
    """
    Hybrid search combining lexical index and LLM ranking.

    Args:
        query: User's natural language query
        intent_data: Dict with search_keywords, intent, etc.
        use_llm: Whether to attempt LLM ranking (default True)

    Returns:
        List of ranked catalog items with relevance_score and match_reason.
    """
    logger.info(f"[HYBRID] Starting search: query='{query}', use_llm={use_llm}")

    # Step 1: Get candidates from lexical index first (fast)
    lexical_results = catalog_lexical_index.search_index(query)
    logger.info(f"[HYBRID] Lexical index returned {len(lexical_results)} candidates")

    # Step 2: Fetch full catalog items from ServiceNow/MCP
    candidate_sys_ids = {r["sys_id"] for r in lexical_results}
    search_keywords = intent_data.get("search_keywords", [])

    try:
        # Prefer MCP when available
        if mcp_client_service.is_available():
            logger.info("[HYBRID] Using MCP for full catalog fetch")
            # MCP search may return more items; filter to candidates + additional broad matches
            all_items = await mcp_client_service.search_catalog(query, intent_data)
        else:
            logger.info("[HYBRID] Using ServiceNow API fallback")
            all_items = await asyncio.to_thread(snow.list_catalog_items, limit=100, query=query)
    except Exception as e:
        logger.exception("[HYBRID] Error fetching from MCP/ServiceNow")
        # Fall back to just lexical candidates
        all_items = []

    # Ensure lexical candidates are included
    candidate_by_id = {item.get("sys_id"): item for item in all_items}
    for lex_result in lexical_results:
        sys_id = lex_result["sys_id"]
        if sys_id not in candidate_by_id:
            # Reconstruct from lexical entry
            entry = catalog_lexical_index.get_index().entries.get(sys_id)
            if entry:
                candidate_by_id[sys_id] = {
                    "sys_id": sys_id,
                    "name": entry.name,
                    "short_description": entry.short_description,
                    "description": entry.description,
                    "category": entry.category,
                }

    candidates = list(candidate_by_id.values())
    logger.info(f"[HYBRID] Total candidates for ranking: {len(candidates)}")

    # Step 3: Rank candidates
    if use_llm and candidates:
        ranked = _rank_with_llm_and_lexical(query, candidates, lexical_results)
    else:
        ranked = _rank_lexical_only(candidates, lexical_results)

    logger.info(f"[HYBRID] Returning {len(ranked)} ranked results")
    return ranked


def _rank_with_llm_and_lexical(
    query: str,
    candidates: List[Dict[str, Any]],
    lexical_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Rank candidates using LLM, with lexical scores as tie-breaker and sanity check.

    Falls back to lexical-only if LLM ranking fails.
    """
    from app.services import ai_service

    logger.info(f"[RANK-LLM] Ranking {len(candidates)} items with OpenAI")

    try:
        # Get LLM rankings
        ranked_by_llm = ai_service.rank_catalogs(query, candidates)

        # Enhance with lexical scores for validation and tie-breaking
        lexical_map = {r["sys_id"]: r["score"] for r in lexical_results}

        for item in ranked_by_llm:
            sys_id = item.get("sys_id")
            lexical_score = lexical_map.get(sys_id, 0.0)
            item["_lexical_score"] = lexical_score
            item["_llm_score"] = item.get("relevance_score", 0.0)

            # Log potential mismatches (LLM says yes, lexical says no)
            if item.get("relevance_score", 0.0) >= 0.7 and lexical_score < 0.3:
                logger.warning(
                    f"[RANK-LLM] Mismatch: {item.get('name')} "
                    f"(LLM={item.get('relevance_score'):.2f}, lexical={lexical_score:.2f})"
                )

        return ranked_by_llm[:20]

    except Exception as e:
        logger.exception("[RANK-LLM] LLM ranking failed, falling back to lexical")
        return _rank_lexical_only(candidates, lexical_results)


def _rank_lexical_only(
    candidates: List[Dict[str, Any]],
    lexical_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Rank using only lexical index scores."""
    logger.info(f"[RANK-LEX] Ranking {len(candidates)} items lexically")

    # Build score map from lexical results
    score_map = {r["sys_id"]: r["score"] for r in lexical_results}

    # Assign scores to all candidates
    for item in candidates:
        sys_id = item.get("sys_id")
        score = score_map.get(sys_id, 0.0)
        item["relevance_score"] = score
        item["match_reason"] = f"Lexical match score: {score:.2f}"

    # Sort by score descending
    ranked = sorted(candidates, key=lambda x: x.get("relevance_score", 0.0), reverse=True)
    return ranked[:20]


def rebuild_index_from_servicenow(limit: int = 1000):
    """
    Rebuild the lexical index by fetching all catalog items from ServiceNow.

    Call this at startup or periodically to keep the index fresh.
    """
    logger.info(f"[REBUILD] Fetching up to {limit} catalog items from ServiceNow")

    try:
        items = snow.list_catalog_items(limit=limit)
        logger.info(f"[REBUILD] Fetched {len(items)} items")
        catalog_lexical_index.initialize_index(items)
        logger.info(f"[REBUILD] Index rebuild complete")
    except Exception as e:
        logger.exception("[REBUILD] Failed to rebuild index")
        raise


# Example usage pseudocode:
#
# async def interpret_with_hybrid(text: str):
#     """Interpret user request using hybrid retrieval."""
#     parsed = await ai_service.interpret_request_async(text)
#     results = await search_catalog_hybrid(text, parsed)
#     return results