import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.vectorrag_v2 import vectorrag_v2_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vectorrag", tags=["vectorrag"])


class VectorRAGQueryRequest(BaseModel):
    query: str
    user_sys_id: str | None = None
    user_email: str | None = None
    session_id: str | None = None


class VectorRAGQueryResponse(BaseModel):
    answer: str


@router.post("/query", response_model=VectorRAGQueryResponse)
async def vectorrag_query(request: VectorRAGQueryRequest):
    try:
        result = await vectorrag_v2_service.query(
            request.query,
            user_context={
                "user_sys_id": request.user_sys_id,
                "user_email": request.user_email,
                "session_id": request.session_id,
            },
        )
        return VectorRAGQueryResponse(answer=str(result.get("answer") or ""))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[VECTORRAG] Query failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/reindex", response_model=dict)
async def vectorrag_reindex():
    try:
        return vectorrag_v2_service.reindex()
    except Exception as exc:
        logger.exception("[VECTORRAG] Reindex failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/status", response_model=dict)
async def vectorrag_status():
    try:
        return vectorrag_v2_service.status()
    except Exception as exc:
        logger.exception("[VECTORRAG] Status failed")
        raise HTTPException(status_code=502, detail=str(exc))
