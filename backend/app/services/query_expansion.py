"""
LLM-Assisted Catalog-Aware Query Expansion

The LLM sees the actual catalog vocabulary (tokens in the inverted index)
and expands the user query by picking from that vocabulary only.
This ensures every expansion term is guaranteed to exist in the index,
preventing domain drift and semantic hallucination.

Flow:
  1. User: "I need a mobile"
  2. Extract vocab from index: [cisco, jabber, softphone, phone, iphone, ...]
  3. LLM sees vocab + query, picks: ["phone", "softphone", "iphone"]
  4. Post-filter removes any LLM hallucinations not in vocab
  5. search_index(..., extra_tokens=["phone", "softphone", "iphone"])
  6. Richer token set → better matching
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Tuple

from app.services import ai_service
from app.services import catalog_lexical_index
from app.config import settings

logger = logging.getLogger(__name__)

# In-memory cache: normalized_query → (timestamp, List[str])
_expansion_cache: Dict[str, Tuple[float, List[str]]] = {}
CACHE_TTL = 300  # 5 minutes


EXPANSION_PROMPT = """You are an IT service catalog assistant.
A user made this request: "{query}"

Below is the vocabulary of terms that exist in our IT service catalog:
{catalog_vocab}

Your task: from the catalog vocabulary above, select 3-8 terms most relevant
to what the user wants. Return ONLY terms from the list above — do NOT invent new words.

Important:
- Only return terms from the vocabulary list
- Do not include terms already in the user query
- Focus on terms that help find the right catalog item
- Return as JSON array, e.g. ["phone", "softphone", "device"]

User request: "{query}"
"""


def get_catalog_vocabulary(max_terms: int = 300) -> List[str]:
    """
    Extract all unique tokens from the lexical index.

    These are catalog-derived tokens that are guaranteed to hit index entries.
    Limited to max_terms to keep LLM prompt size reasonable.

    Returns:
        List of unique vocabulary tokens from the index
    """
    try:
        index = catalog_lexical_index.get_index()
        if not index.entries:
            logger.info("[EXPANSION] Index empty, vocabulary is empty")
            return []

        # Get all tokens from the inverted index
        all_tokens = sorted(index.token_index.keys())

        # Cap at max_terms to keep prompt within limits
        vocab = all_tokens[:max_terms]
        logger.debug(f"[EXPANSION] Extracted {len(vocab)} vocab terms from index")
        return vocab

    except Exception as e:
        logger.exception(f"[EXPANSION] Error extracting vocabulary: {e}")
        return []


async def expand_query(query: str) -> List[str]:
    """
    Expand user query using LLM, grounded in actual catalog vocabulary.

    The LLM is shown the catalog vocabulary and asked to pick terms from it
    that match the user's intent. Every returned term is post-filtered to ensure
    it exists in the index — no hallucinations can slip through.

    Args:
        query: Raw user query (e.g., "I need a mobile")

    Returns:
        List of expansion terms (lowercase, deduplicated), all guaranteed to
        exist in the index. Empty list on any failure (graceful degradation).
    """
    if not query or not query.strip():
        return []

    # Normalize cache key
    cache_key = query.strip().lower()

    # Check cache first (5-min TTL)
    if cache_key in _expansion_cache:
        ts, cached_terms = _expansion_cache[cache_key]
        if time.time() - ts < CACHE_TTL:
            logger.debug(f"[EXPANSION] Cache hit for '{query}' → {cached_terms}")
            return cached_terms

    logger.info(f"[EXPANSION] Expanding query: '{query}'")

    # Get catalog vocabulary (all tokens guaranteed to hit index)
    vocab = get_catalog_vocabulary(max_terms=300)
    if not vocab:
        logger.info("[EXPANSION] No vocabulary available, returning empty expansion")
        _expansion_cache[cache_key] = (time.time(), [])
        return []

    # Prepare LLM prompt with actual catalog vocabulary
    vocab_str = ", ".join(vocab[:200])  # Show top 200 terms for clarity
    prompt = EXPANSION_PROMPT.format(query=query, catalog_vocab=vocab_str)

    try:
        # Call LLM to expand query
        logger.debug(f"[EXPANSION] Calling LLM with query='{query}', vocab_size={len(vocab)}")
        async_client = ai_service.get_async_client()
        completion = await async_client.chat.completions.create(
            model=settings.openai_chat_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an IT service catalog assistant. Return ONLY valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,  # Low temperature for deterministic expansion
            max_tokens=100,  # Short response, just a JSON array
        )

        raw_response = completion.choices[0].message.content.strip()
        logger.debug(f"[EXPANSION] LLM response: {raw_response[:200]}")

        # Parse JSON array from response
        try:
            # Handle markdown code block wrapping
            if raw_response.startswith("```"):
                raw_response = raw_response.split("```")[1]
                if raw_response.startswith("json"):
                    raw_response = raw_response[4:]
            raw_response = raw_response.strip()

            expansion_terms = json.loads(raw_response)
            if not isinstance(expansion_terms, list):
                logger.warning(f"[EXPANSION] LLM did not return array: {raw_response[:100]}")
                _expansion_cache[cache_key] = (time.time(), [])
                return []
        except json.JSONDecodeError as e:
            logger.warning(f"[EXPANSION] Failed to parse LLM response as JSON: {e}")
            _expansion_cache[cache_key] = (time.time(), [])
            return []

        # Post-filter: only keep terms that exist in the catalog vocabulary
        valid_vocab = set(vocab)
        filtered_expansions = []
        for term in expansion_terms:
            term_lower = str(term).strip().lower()
            if term_lower and term_lower in valid_vocab and term_lower not in query.lower():
                filtered_expansions.append(term_lower)

        filtered_expansions = list(dict.fromkeys(filtered_expansions))  # Deduplicate order-preserving
        logger.info(
            f"[EXPANSION] Expanded '{query}' → {filtered_expansions} "
            f"(raw: {expansion_terms}, filtered: {len(filtered_expansions)}/{len(expansion_terms)})"
        )

        # Cache result
        _expansion_cache[cache_key] = (time.time(), filtered_expansions)
        return filtered_expansions

    except asyncio.TimeoutError:
        logger.error(f"[EXPANSION] LLM timeout for query '{query}'")
        _expansion_cache[cache_key] = (time.time(), [])
        return []

    except Exception as e:
        logger.exception(f"[EXPANSION] Error calling LLM: {e}")
        _expansion_cache[cache_key] = (time.time(), [])
        return []


def clear_cache():
    """
    Clear expansion cache.

    Call this when the lexical index is rebuilt, since vocabulary has changed.
    """
    global _expansion_cache
    _expansion_cache.clear()
    logger.info("[EXPANSION] Cache cleared")


def get_cache_status() -> dict:
    """Get cache status for debugging."""
    now = time.time()
    fresh_count = sum(1 for ts, _ in _expansion_cache.values() if now - ts < CACHE_TTL)
    return {
        "cache_size": len(_expansion_cache),
        "fresh_entries": fresh_count,
        "stale_entries": len(_expansion_cache) - fresh_count,
    }

