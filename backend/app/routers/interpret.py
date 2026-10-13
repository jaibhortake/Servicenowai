import asyncio
import logging
from fastapi import APIRouter, HTTPException
from app.models.catalog import InterpretRequest, InterpretResponse
from app.services import ai_service, session_service
from app.services.mcp_client_service import mcp_client_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/interpret", tags=["interpret"])


@router.post("", response_model=InterpretResponse)
async def interpret(req: InterpretRequest):
    try:
        # Add timeout to prevent hanging if OpenAI is slow (increased to 60s for LLM reranking)
        result = await asyncio.wait_for(
            ai_service.interpret_request_async(req.text),
            timeout=60.0
        )
    except asyncio.TimeoutError:
        logger.error(f"[INTERPRET] TimeoutError after 60s for: {req.text}", exc_info=True)
        # Return fallback response with catalog search results via MCP
        suggested = []
        keywords = req.text.split()
        try:
            if mcp_client_service.is_available():
                intent_data = {"search_keywords": keywords}
                suggested = await mcp_client_service.search_catalog_raw(req.text, intent_data)
                logger.info(f"[INTERPRET] Fallback search returned {len(suggested)} catalogs")
        except Exception as search_err:
            logger.error(f"[INTERPRET] TimeoutError - search failed: {search_err}", exc_info=True)
        result = {
            "intent": "general_request",
            "confidence": 0.5,
            "entities": {},
            "ticket_type": "SR",
            "search_keywords": keywords,
            "suggested_catalogs": suggested,
        }
    except Exception as e:
        logger.error(f"[INTERPRET] Exception ({type(e).__name__}): {str(e)}", exc_info=True)
        # Return fallback instead of 502, so form can still load
        suggested = []
        keywords = req.text.split()
        try:
            if mcp_client_service.is_available():
                intent_data = {"search_keywords": keywords}
                suggested = await mcp_client_service.search_catalog_raw(req.text, intent_data)
                logger.info(f"[INTERPRET] Exception - Fallback search returned {len(suggested)} catalogs")
        except Exception as search_err:
            logger.error(f"[INTERPRET] Exception - search failed: {search_err}", exc_info=True)
        result = {
            "intent": "general_request",
            "confidence": 0.5,
            "entities": {},
            "ticket_type": "SR",
            "search_keywords": keywords,
            "suggested_catalogs": suggested,
        }

    if req.session_id:
        session_service.add_message(
            req.session_id, "user", req.text,
            {"intent": result.get("intent"), "entities": result.get("entities")}
        )

    suggested = result.get("suggested_catalogs", [])
    logger.info(f"[INTERPRET] Building response: intent={result.get('intent')}, suggested_count={len(suggested)}")
    if suggested:
        logger.info(f"[INTERPRET] First item sample: {suggested[0]}")

    try:
        return InterpretResponse(
            intent=result.get("intent", "general_request"),
            confidence=result.get("confidence", 0.8),
            entities=result.get("entities", {}),
            ticket_type=result.get("ticket_type", "SR"),
            suggested_catalogs=suggested,
            selected_catalog=result.get("selected_catalog"),
            clarification_needed=result.get("clarification_needed", False),
            clarification_question=result.get("clarification_question"),
        )
    except Exception as e:
        logger.error(f"[INTERPRET] Response validation failed: {e}")
        if suggested:
            logger.error(f"[INTERPRET] First suggested item: {suggested[0]}")
            for i, cat in enumerate(suggested[:3]):
                try:
                    CatalogItem.model_validate(cat)
                    logger.info(f"[INTERPRET] Item {i} validates OK")
                except Exception as ve:
                    logger.error(f"[INTERPRET] Item {i} validation error: {ve}")
        raise
