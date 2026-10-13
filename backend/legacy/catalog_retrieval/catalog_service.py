from typing import List, Dict, Any, Optional
from app.services import servicenow as snow
from app.services import ai_service
from app.models.catalog import CatalogItem
import logging

logger = logging.getLogger(__name__)


def search_catalogs(query: str, intent_data: Dict[str, Any]) -> List[CatalogItem]:
    keywords = intent_data.get("search_keywords", [])
    category_hint = _infer_category(intent_data.get("intent", ""))

    logger.info(f"Searching catalogs for query: {query}, category_hint: {category_hint}")
    all_items = snow.list_catalog_items(category=category_hint, limit=100)
    logger.info(f"Found {len(all_items)} items with category hint")
    
    if not all_items:
        logger.info("No items found with category hint, trying without category...")
        all_items = snow.list_catalog_items(limit=100)
        logger.info(f"Found {len(all_items)} items without category")

    if not all_items:
        logger.warning("No catalog items found in ServiceNow")
        return []

    ranked_raw = ai_service.rank_catalogs(query, all_items)
    if not ranked_raw:
        logger.info("AI ranking returned no catalog matches, using deterministic fallback candidates")
        ranked_raw = _fallback_catalog_candidates(query, keywords, all_items, category_hint)

    return [
        CatalogItem(
            sys_id=item.get("sys_id", ""),
            name=item.get("name", ""),
            short_description=item.get("short_description", ""),
            description=item.get("description", ""),
            category=_extract_value(item.get("category")),
            price=_extract_value(item.get("price")),
            relevance_score=item.get("relevance_score", 0.0),
            match_reason=item.get("match_reason"),
        )
        for item in ranked_raw[:8]
    ]


def _extract_value(field: Any) -> Optional[str]:
    if isinstance(field, dict):
        return field.get("display_value") or field.get("value")
    return str(field) if field else None


def _infer_category(intent: str) -> Optional[str]:
    mapping = {
        "laptop": "hardware",
        "computer": "hardware",
        "phone": "hardware",
        "monitor": "hardware",
        "software": "software",
        "license": "software",
        "access": "access",
        "vpn": "access",
        "permission": "access",
        "account": "access",
        "server": "infrastructure",
        "network": "infrastructure",
        "database": "infrastructure",
    }
    intent_lower = intent.lower()
    for keyword, category in mapping.items():
        if keyword in intent_lower:
            return category
    return None


def _fallback_catalog_candidates(
    query: str,
    keywords: List[str],
    all_items: List[Dict[str, Any]],
    category_hint: Optional[str],
) -> List[Dict[str, Any]]:
    tokens = _tokenize(query)
    for keyword in keywords:
        tokens.update(_tokenize(keyword))
    if category_hint:
        tokens.update(_category_tokens(category_hint))

    scored: List[Dict[str, Any]] = []
    for item in all_items:
        haystack_parts = [
            str(item.get("name", "")),
            str(item.get("short_description", "")),
            str(item.get("description", "")),
            _extract_value(item.get("category")) or "",
        ]
        haystack = " ".join(haystack_parts).lower()
        score = 0.0

        for token in tokens:
            if token and token in haystack:
                score += 0.18
                if token in str(item.get("name", "")).lower():
                    score += 0.12

        if category_hint:
            category_value = (_extract_value(item.get("category")) or "").lower()
            if category_hint in category_value:
                score += 0.25

        if score > 0:
            fallback_item = dict(item)
            fallback_item["relevance_score"] = min(score, 0.74)
            fallback_item["match_reason"] = "Suggested from catalog keywords and category hints."
            scored.append(fallback_item)

    if scored:
        return sorted(scored, key=lambda item: item.get("relevance_score", 0.0), reverse=True)

    if category_hint:
        category_words = _category_tokens(category_hint)
        broad_matches = []
        for item in all_items:
            haystack = " ".join(
                [
                    str(item.get("name", "")),
                    str(item.get("short_description", "")),
                    str(item.get("description", "")),
                    _extract_value(item.get("category")) or "",
                ]
            ).lower()
            if any(word in haystack for word in category_words):
                broad_item = dict(item)
                broad_item["relevance_score"] = 0.35
                broad_item["match_reason"] = f"Broad match for {category_hint} request."
                broad_matches.append(broad_item)
        if broad_matches:
            return broad_matches

    return []


def _tokenize(text: str) -> set[str]:
    return {token.strip().lower() for token in text.split() if len(token.strip()) >= 3}


def _category_tokens(category_hint: str) -> set[str]:
    mapping = {
        "hardware": {"hardware", "laptop", "computer", "macbook", "notebook", "desktop", "device"},
        "software": {"software", "license", "application", "tool"},
        "access": {"access", "permission", "account", "vpn", "login"},
        "infrastructure": {"server", "database", "network", "infra", "infrastructure"},
    }
    return mapping.get(category_hint, {category_hint})
