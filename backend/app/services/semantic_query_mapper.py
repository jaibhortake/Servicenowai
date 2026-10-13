"""
Semantic Query Mapper - Maps user queries to Neo4j knowledge graph schema.

Maps natural language queries to catalog categories and schemas
by understanding semantic intent through the knowledge graph.
"""

import logging
from typing import Dict, List, Optional, Tuple
from app.services.neo4j_catalog_graph import platform_neo4j_catalog_graph_service as neo4j_catalog_graph_service
from app.services import servicenow as snow

logger = logging.getLogger(__name__)

# Semantic intent mappings: user terms -> catalog categories/keywords
SEMANTIC_INTENT_MAP = {
    # Mobile devices
    "mobile": {"categories": ["mobile", "phone", "device"], "exclude": ["laptop", "desktop"]},
    "phone": {"categories": ["mobile", "phone", "device"], "exclude": ["telephone"]},
    "device": {"categories": ["device", "hardware"], "exclude": []},
    "iphone": {"categories": ["mobile", "phone", "apple"], "exclude": []},
    "android": {"categories": ["mobile", "phone"], "exclude": []},
    "tablet": {"categories": ["mobile", "device"], "exclude": []},
    "watch": {"categories": ["mobile", "device", "wearable"], "exclude": []},

    # Computers
    "laptop": {"categories": ["laptop", "computer", "macbook", "thinkpad", "dell", "lenovo", "apple", "mac", "windows"], "exclude": ["phone", "mobile"]},
    "desktop": {"categories": ["desktop", "computer"], "exclude": ["mobile"]},
    "computer": {"categories": ["computer", "hardware", "laptop", "desktop", "mac", "pc"], "exclude": []},
    "pc": {"categories": ["computer", "desktop", "windows", "laptop"], "exclude": []},
    "mac": {"categories": ["computer", "apple", "macbook", "imac", "mac"], "exclude": []},

    # Software/Apps
    "software": {"categories": ["software", "application", "license"], "exclude": []},
    "app": {"categories": ["software", "application"], "exclude": []},
    "application": {"categories": ["software", "application"], "exclude": []},
    "microsoft": {"categories": ["software", "microsoft"], "exclude": []},
    "adobe": {"categories": ["software", "adobe"], "exclude": []},
    "office": {"categories": ["software", "office"], "exclude": []},

    # Access/Security
    "vpn": {"categories": ["access", "vpn", "security"], "exclude": []},
    "access": {"categories": ["access", "security", "authentication"], "exclude": []},
    "password": {"categories": ["security", "access"], "exclude": []},
    "authentication": {"categories": ["security", "authentication"], "exclude": []},

    # Networking/Infrastructure
    "network": {"categories": ["network", "infrastructure"], "exclude": []},
    "printer": {"categories": ["printer", "hardware"], "exclude": []},
    "monitor": {"categories": ["monitor", "hardware", "display"], "exclude": []},
    "server": {"categories": ["server", "infrastructure"], "exclude": []},
}


def parse_semantic_intent(query: str) -> Dict[str, any]:
    """
    Parse user query to extract semantic intent.

    Maps natural language to catalog categories and schemas.

    Args:
        query: User's natural language query

    Returns:
        Dict with:
        - primary_intent: main semantic category
        - categories: list of relevant catalog categories
        - exclude_categories: categories to exclude
        - keywords: extracted keywords for graph search
    """
    query_lower = query.lower().strip()
    tokens = query_lower.split()

    logger.info(f"[SEMANTIC] Parsing query: '{query}'")
    logger.info(f"[SEMANTIC] Tokens: {tokens}")

    # Find matching intents
    matched_intents = []
    for token in tokens:
        if token in SEMANTIC_INTENT_MAP:
            intent = SEMANTIC_INTENT_MAP[token]
            matched_intents.append({
                "token": token,
                "categories": intent["categories"],
                "exclude": intent["exclude"]
            })
            logger.info(f"[SEMANTIC] Token '{token}' maps to categories: {intent['categories']}")

    # Combine all matched intents
    all_categories = set()
    all_exclude = set()
    primary_intent = None

    for match in matched_intents:
        all_categories.update(match["categories"])
        all_exclude.update(match["exclude"])
        if not primary_intent:
            primary_intent = match["token"]

    # Remove excluded categories
    all_categories = all_categories - all_exclude

    result = {
        "primary_intent": primary_intent or "general_request",
        "categories": sorted(list(all_categories)),
        "exclude_categories": sorted(list(all_exclude)),
        "keywords": tokens,
        "raw_query": query
    }

    logger.info(f"[SEMANTIC] Parsed intent: primary={result.get('primary_intent')}, categories={result.get('categories')}, exclude={result.get('exclude_categories')}")
    return result


def query_knowledge_graph_by_intent(semantic_intent: Dict, graph_service=None) -> List[Dict]:
    """
    Query catalog items matching semantic intent - comprehensive search.

    Returns ALL matching items from ServiceNow API that match any category in the semantic intent.
    This ensures users see all relevant items with broader matching.
    """
    primary_intent = semantic_intent.get("primary_intent", "")
    categories = semantic_intent.get("categories", [])
    exclude_categories = semantic_intent.get("exclude_categories", [])

    if not primary_intent:
        logger.warning("[SEMANTIC] No primary intent extracted")
        return []

    logger.info(f"[SEMANTIC] Searching for items matching: primary='{primary_intent}', categories={categories}")

    graph_terms = [primary_intent] + categories
    graph_terms = [term.strip().lower() for term in graph_terms if term and term.strip()]

    catalog_graph_service = graph_service or neo4j_catalog_graph_service
    if catalog_graph_service.is_available() and graph_terms:
        try:
            graph_results = catalog_graph_service.search_catalog_semantic(
                " ".join(graph_terms),
                graph_terms,
            )
            if exclude_categories:
                exclude_lower = [exc.lower() for exc in exclude_categories]
                graph_results = [
                    item for item in graph_results
                    if not any(
                        exc in f"{(item.get('name') or '').lower()} {(item.get('short_description') or '').lower()} {(item.get('category') or '').lower()}"
                        for exc in exclude_lower
                    )
                ]
            logger.info(
                "[SEMANTIC] Graph semantic search returned %d items for primary=%s categories=%s",
                len(graph_results),
                primary_intent,
                categories,
            )
            if graph_results:
                return graph_results
        except Exception as e:
            logger.error(f"[SEMANTIC] Graph semantic search failed: {e}")

    try:
        # Get all catalog items
        all_items = snow.list_catalog_items(limit=500)
        logger.info(f"[SEMANTIC] Retrieved {len(all_items)} total items from ServiceNow")

        # Filter to items containing any category or primary intent in name or description
        filtered_items = []
        primary_lower = primary_intent.lower()
        categories_lower = [cat.lower() for cat in categories]

        matched_count = 0
        for item in all_items:
            item_name = (item.get("name") or "").lower()
            item_desc = (item.get("short_description") or "").lower()
            combined = f"{item_name} {item_desc}"

            # Skip if has excluded categories
            if exclude_categories and any(exc.lower() in combined for exc in exclude_categories):
                continue

            # Include if primary intent OR any category is found
            if primary_lower in combined or any(cat in combined for cat in categories_lower):
                matched_count += 1
                filtered_items.append(item)
                if matched_count <= 10:
                    logger.info(f"[SEMANTIC] Matched item {matched_count}: {item.get('name')}")

        logger.info(f"[SEMANTIC] After filtering: {len(filtered_items)} total items match intent={primary_lower} or categories={categories_lower}")
        return filtered_items

    except Exception as e:
        logger.error(f"[SEMANTIC] Error querying catalogs: {e}")
        return []


def semantic_search(query: str, graph_service=None) -> Tuple[List[Dict], Dict]:
    """
    Full semantic search pipeline.

    1. Parse user query for semantic intent
    2. Query knowledge graph by intent
    3. Return results with semantic context

    Args:
        query: User's natural language query

    Returns:
        Tuple of (results_list, semantic_context)
    """
    # Step 1: Parse semantic intent
    intent = parse_semantic_intent(query)

    # Step 2: Query knowledge graph
    results = query_knowledge_graph_by_intent(intent, graph_service=graph_service)

    # Step 3: Return with context
    return results, intent
