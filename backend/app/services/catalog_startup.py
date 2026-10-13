"""
Catalog lexical index initialization at application startup.

This module handles building and populating the lexical index
with catalog data from ServiceNow when the app starts.
"""

import logging
import asyncio
from typing import Optional
from app.services import catalog_lexical_index
from app.services import servicenow as snow
from app.services.mcp_client_service import mcp_client_service

logger = logging.getLogger(__name__)


async def initialize_catalog_index_async(limit: int = 1000, timeout: float = 30.0) -> bool:
    """
    Asynchronously initialize the lexical index at app startup.

    Tries MCP first (if available), falls back to ServiceNow API.

    Args:
        limit: Maximum number of catalog items to fetch
        timeout: Maximum time to wait (seconds)

    Returns:
        True if successful, False if index remains uninitialized
    """
    logger.info(f"[STARTUP] Initializing catalog lexical index (limit={limit})...")

    try:
        # Try to fetch from MCP (preferred, might have caching)
        if mcp_client_service.is_available():
            logger.info("[STARTUP] Attempting to fetch catalog items from MCP")
            try:
                items = await asyncio.wait_for(
                    mcp_client_service.list_catalog_items(limit=limit, offset=0),
                    timeout=timeout
                )
                logger.info(f"[STARTUP] MCP returned {len(items)} catalog items")
            except asyncio.TimeoutError:
                logger.warning(f"[STARTUP] MCP timeout after {timeout}s, falling back to ServiceNow API")
                items = None
            except Exception as e:
                logger.warning(f"[STARTUP] MCP error: {e}, falling back to ServiceNow API")
                items = None
        else:
            logger.info("[STARTUP] MCP not available, using ServiceNow API")
            items = None

        # Fallback: fetch from ServiceNow directly
        if not items:
            logger.info("[STARTUP] Fetching from ServiceNow API...")
            items = await asyncio.to_thread(snow.list_catalog_items, limit=limit)
            if items:
                logger.info(f"[STARTUP] ServiceNow returned {len(items)} catalog items")

        if not items:
            logger.warning("[STARTUP] No catalog items fetched; index will be empty")
            return False

        # Build index
        logger.info(f"[STARTUP] Building lexical index from {len(items)} items...")
        catalog_lexical_index.initialize_index(items)
        logger.info("[STARTUP] ✓ Catalog lexical index initialized successfully")
        return True

    except Exception as e:
        logger.exception(f"[STARTUP] Critical error initializing index: {e}")
        return False


def initialize_catalog_index_sync(limit: int = 1000) -> bool:
    """
    Synchronously initialize the lexical index (blocking).

    Use only if async startup is not available.

    Args:
        limit: Maximum number of catalog items to fetch

    Returns:
        True if successful, False otherwise
    """
    logger.info(f"[STARTUP] Synchronously initializing catalog lexical index (limit={limit})...")

    try:
        # Try ServiceNow API
        logger.info("[STARTUP] Fetching from ServiceNow API...")
        items = snow.list_catalog_items(limit=limit)

        if not items:
            logger.warning("[STARTUP] No catalog items fetched; index will be empty")
            return False

        logger.info(f"[STARTUP] ServiceNow returned {len(items)} catalog items")
        logger.info(f"[STARTUP] Building lexical index...")
        catalog_lexical_index.initialize_index(items)
        logger.info("[STARTUP] ✓ Catalog lexical index initialized successfully")
        return True

    except Exception as e:
        logger.exception(f"[STARTUP] Error initializing index: {e}")
        return False


def get_index_status() -> dict:
    """
    Get status of the lexical index.

    Returns:
        Dict with index_size, token_count, phrase_count, etc.
    """
    index = catalog_lexical_index.get_index()
    return {
        "initialized": len(index.entries) > 0,
        "catalog_items": len(index.entries),
        "tokens": len(index.token_index),
        "phrases": len(index.phrase_index),
        "categories": len(index.category_index),
    }