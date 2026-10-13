import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.graphrag_citations import extract_record_citations, strip_answer_citation_markers
from app.services.graphrag_service import graphrag_service
from app.services.graphrag_v2 import graphrag_v2_service
from app.services.graphrag_v2.case_workflow import (
    CaseWorkflowState,
    CaseWorkflowUnavailable,
    case_workflow_service,
)
from app.services.graphrag_v2.diagnosis_workflow import (
    DiagnosisState,
    DiagnosisWorkflowUnavailable,
    diagnosis_workflow_service,
)
from app.services.graphrag_v2.embedding_service import graphrag_v2_embedding_service
from app.services.minimal_graphrag_sync_service import minimal_graphrag_sync_service
from app.services.neo4j_catalog_graph import neo4j_catalog_graph_service
from app.services.neo4j_schema_graph import neo4j_schema_graph_service
from app.services.neo4j_ticket_graph import neo4j_ticket_graph_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/graphrag", tags=["graphrag"])


class GraphRAGQueryRequest(BaseModel):
    query: str
    user_sys_id: str | None = None
    user_email: str | None = None
    session_id: str | None = None


class GraphRAGQueryResponse(BaseModel):
    answer: str
    citations: List[Dict[str, str]] = Field(default_factory=list)


class MinimalGraphBootstrapRequest(BaseModel):
    batch_size: int = 25
    max_catalog_items: int | None = None
    max_service_requests: int | None = None
    max_change_requests: int | None = None
    clear_existing: bool = False


class GraphRAGVectorBackfillRequest(BaseModel):
    max_nodes: int | None = None


class GraphRAGDiagnosisStartRequest(BaseModel):
    query: str
    user_sys_id: str | None = None
    user_email: str | None = None
    incident_sys_id: str | None = None
    product_hint: str | None = None


class GraphRAGDiagnosisAnswerRequest(BaseModel):
    question_id: str
    answer_value: str | None = None
    answer_label: str | None = None
    skipped: bool = False


class GraphRAGDiagnosisIssueSelectionRequest(BaseModel):
    issue_id: str
    issue_label: str | None = None


class GraphRAGCaseStartRequest(BaseModel):
    query: str
    user_sys_id: str | None = None
    user_email: str | None = None


class GraphRAGCaseAnswerRequest(BaseModel):
    question_id: str
    answer_value: str | None = None
    answer_label: str | None = None
    skipped: bool = False


class GraphRAGCaseIssueSelectionRequest(BaseModel):
    issue_id: str
    issue_label: str | None = None


class GraphRAGCaseCompleteRequest(BaseModel):
    user_sys_id: str | None = None
    user_email: str | None = None


@router.get("/cases/all", response_model=List[Dict[str, Any]])
async def list_graphrag_cases(
    ticket_type: str | None = None,
    states: str | None = None,
    limit: int = 100,
):
    if not neo4j_ticket_graph_service.is_available():
        raise HTTPException(status_code=503, detail="Neo4j ticket graph is not available")

    normalized_type = str(ticket_type or "").strip().upper() or None
    if normalized_type and normalized_type not in {"SR", "CH", "INC"}:
        raise HTTPException(status_code=400, detail="ticket_type must be SR, CH, or INC")

    state_filters = [
        state.strip()
        for state in str(states or "").split(",")
        if state and state.strip()
    ]

    try:
        return neo4j_ticket_graph_service.list_all_cases(
            ticket_type=normalized_type,
            states=state_filters,
            limit=max(1, min(int(limit or 100), 500)),
        )
    except Exception as exc:
        logger.exception("[GRAPHRAG_CASES] List failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/query", response_model=GraphRAGQueryResponse)
async def graph_rag_query(request: GraphRAGQueryRequest):
    if (
        not neo4j_catalog_graph_service.is_available()
        and not neo4j_schema_graph_service.is_available()
        and not neo4j_ticket_graph_service.is_available()
    ):
        raise HTTPException(status_code=503, detail="Neo4j graph is not available")

    try:
        service = graphrag_v2_service if settings.graphrag_v2_enabled else graphrag_service
        result = await service.query(
            request.query,
            user_context={
                "user_sys_id": request.user_sys_id,
                "user_email": request.user_email,
                "session_id": request.session_id,
            },
        )
        citations = extract_record_citations(
            result,
            instance_url=settings.graphrag_citation_instance_url_resolved,
            scope=settings.graphrag_record_card_scope,
        )
        return GraphRAGQueryResponse(
            answer=strip_answer_citation_markers(result["answer"]),
            citations=citations,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[GRAPHRAG] Query failed")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/diagnosis/start", response_model=DiagnosisState)
async def start_graphrag_diagnosis(request: GraphRAGDiagnosisStartRequest):
    try:
        return await diagnosis_workflow_service.start(
            query=request.query,
            user_email=request.user_email or "",
            user_sys_id=request.user_sys_id or "",
            incident_sys_id=request.incident_sys_id or "",
            product_hint=request.product_hint or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except DiagnosisWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_DIAGNOSIS] Start failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/diagnosis/{session_id}", response_model=DiagnosisState)
async def get_graphrag_diagnosis(session_id: str):
    state = diagnosis_workflow_service.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Diagnosis session not found")
    return state


@router.post("/diagnosis/{session_id}/answer", response_model=DiagnosisState)
async def answer_graphrag_diagnosis(session_id: str, request: GraphRAGDiagnosisAnswerRequest):
    try:
        return await diagnosis_workflow_service.answer(
            session_id=session_id,
            question_id=request.question_id,
            answer_value=request.answer_value or "",
            answer_label=request.answer_label or "",
            skipped=request.skipped,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Diagnosis session not found")
    except DiagnosisWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_DIAGNOSIS] Answer failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/diagnosis/{session_id}/select-issue", response_model=DiagnosisState)
async def select_graphrag_diagnosis_issue(session_id: str, request: GraphRAGDiagnosisIssueSelectionRequest):
    try:
        return await diagnosis_workflow_service.select_issue(
            session_id=session_id,
            issue_id=request.issue_id,
            issue_label=request.issue_label or "",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Diagnosis session not found")
    except DiagnosisWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_DIAGNOSIS] Issue selection failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/diagnosis/{session_id}/complete", response_model=DiagnosisState)
async def complete_graphrag_diagnosis(session_id: str):
    try:
        return diagnosis_workflow_service.complete(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Diagnosis session not found")
    except DiagnosisWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_DIAGNOSIS] Complete failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/diagnosis/{session_id}/cancel", response_model=DiagnosisState)
async def cancel_graphrag_diagnosis(session_id: str):
    try:
        return diagnosis_workflow_service.cancel(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Diagnosis session not found")
    except DiagnosisWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_DIAGNOSIS] Cancel failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/cases/start", response_model=CaseWorkflowState)
async def start_graphrag_case(request: GraphRAGCaseStartRequest):
    try:
        return await case_workflow_service.start(
            query=request.query,
            user_email=request.user_email or "",
            user_sys_id=request.user_sys_id or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except CaseWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_CASE] Start failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/cases/{session_id}/answer", response_model=CaseWorkflowState)
async def answer_graphrag_case(session_id: str, request: GraphRAGCaseAnswerRequest):
    try:
        return await case_workflow_service.answer(
            session_id=session_id,
            question_id=request.question_id,
            answer_value=request.answer_value or "",
            answer_label=request.answer_label or "",
            skipped=request.skipped,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Case workflow session not found")
    except CaseWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_CASE] Answer failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/cases/{session_id}/select-issue", response_model=CaseWorkflowState)
async def select_graphrag_case_issue(session_id: str, request: GraphRAGCaseIssueSelectionRequest):
    try:
        return await case_workflow_service.select_issue(
            session_id=session_id,
            issue_id=request.issue_id,
            issue_label=request.issue_label or "",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Case workflow session not found")
    except CaseWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_CASE] Issue selection failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/cases/{session_id}/complete", response_model=CaseWorkflowState)
async def complete_graphrag_case(session_id: str, request: GraphRAGCaseCompleteRequest | None = None):
    try:
        payload = request or GraphRAGCaseCompleteRequest()
        return await case_workflow_service.complete(
            session_id=session_id,
            user_email=payload.user_email or "",
            user_sys_id=payload.user_sys_id or "",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Case workflow session not found")
    except CaseWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_CASE] Complete failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/cases/{session_id}/cancel", response_model=CaseWorkflowState)
async def cancel_graphrag_case(session_id: str):
    try:
        return case_workflow_service.cancel(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Case workflow session not found")
    except CaseWorkflowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[GRAPHRAG_CASE] Cancel failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/cases/{session_id}", response_model=CaseWorkflowState)
async def get_graphrag_case(session_id: str):
    state = case_workflow_service.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Case workflow session not found")
    return state


@router.get("/graph/minimal/status", response_model=dict)
async def minimal_graph_status():
    return minimal_graphrag_sync_service.status()


@router.post("/graph/minimal/clear", response_model=dict)
async def clear_minimal_graph(include_schema_metadata: bool = True):
    return minimal_graphrag_sync_service.clear_minimal_graph(include_schema_metadata=include_schema_metadata)


@router.post("/graph/minimal/bootstrap", response_model=dict)
async def bootstrap_minimal_graph(request: MinimalGraphBootstrapRequest):
    return await minimal_graphrag_sync_service.bootstrap_from_mcp(
        batch_size=request.batch_size,
        max_catalog_items=request.max_catalog_items,
        max_service_requests=request.max_service_requests,
        max_change_requests=request.max_change_requests,
        clear_existing=request.clear_existing,
    )


@router.get("/vector/status", response_model=dict)
async def graphrag_vector_status():
    return graphrag_v2_embedding_service.status()


@router.post("/vector/backfill", response_model=dict)
async def graphrag_vector_backfill(request: GraphRAGVectorBackfillRequest):
    return graphrag_v2_embedding_service.backfill(max_nodes=request.max_nodes)
