import asyncio
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from app.models.ticket import CreateSRRequest, CreateCHRequest, CreateIncidentRequest, TicketResponse, UpdateTicketRequest
from app.services import ticket_service, session_service, servicenow as snow
from app.services.neo4j_ticket_graph import platform_neo4j_ticket_graph_service as neo4j_ticket_graph_service

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


@router.get("", response_model=List[dict])
async def list_tickets(user_sys_id: Optional[str] = Query(None)):
    try:
        return ticket_service.list_tickets(user_sys_id=user_sys_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/sr", response_model=TicketResponse)
async def create_sr(req: CreateSRRequest, session_id: Optional[str] = Query(None)):
    try:
        ticket = await asyncio.wait_for(
            asyncio.to_thread(ticket_service.create_sr, req),
            timeout=45.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="ServiceNow request timed out")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    if session_id:
        session_service.set_ticket(session_id, ticket.sys_id, "SR")
        if ticket.ritm_sys_id:
            session = session_service.get_session(session_id)
            if session:
                session.ritm_sys_id = ticket.ritm_sys_id
                session_service.update_session(session)
        session_service.add_message(session_id, "system",
                                    f"Service Request {ticket.number} created successfully.")
    return ticket


@router.post("/ch", response_model=TicketResponse)
async def create_ch(req: CreateCHRequest, session_id: Optional[str] = Query(None)):
    try:
        ticket = await asyncio.wait_for(
            asyncio.to_thread(ticket_service.create_ch, req),
            timeout=45.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="ServiceNow request timed out")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    if session_id:
        session_service.set_ticket(session_id, ticket.sys_id, "CH")
        session_service.add_message(session_id, "system",
                                    f"Change Request {ticket.number} created successfully.")
    return ticket


@router.post("/inc", response_model=TicketResponse)
async def create_incident(req: CreateIncidentRequest, session_id: Optional[str] = Query(None)):
    try:
        ticket = await asyncio.wait_for(
            asyncio.to_thread(ticket_service.create_incident, req),
            timeout=45.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="ServiceNow incident creation timed out")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    if session_id:
        session_service.set_ticket(session_id, ticket.sys_id, "INC")
        session_service.add_message(session_id, "system",
                                    f"Incident {ticket.number} created successfully.")
    return ticket


@router.get("/graph/status", response_model=dict)
async def ticket_graph_status():
    return neo4j_ticket_graph_service.status()


@router.post("/graph/sync-all", response_model=dict)
async def sync_all_tickets_to_graph(
    batch_size: int = Query(50, ge=1, le=200),
    max_items: Optional[int] = Query(None, ge=1, le=5000),
):
    if not neo4j_ticket_graph_service.is_available():
        raise HTTPException(status_code=503, detail="Neo4j ticket graph is not available")

    try:
        return ticket_service.sync_all_tickets_to_graph(batch_size=batch_size, max_items=max_items)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")


@router.post("/{ticket_type}/{sys_id}/graph/sync", response_model=dict)
async def sync_ticket_to_graph(ticket_type: str, sys_id: str):
    if ticket_type.upper() not in ("SR", "CH", "INC"):
        raise HTTPException(status_code=400, detail="ticket_type must be SR, CH, or INC")
    if not neo4j_ticket_graph_service.is_available():
        raise HTTPException(status_code=503, detail="Neo4j ticket graph is not available")

    try:
        synced = ticket_service.sync_ticket_to_graph(sys_id, ticket_type.upper())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    if not synced:
        raise HTTPException(status_code=404, detail="Ticket could not be synced")
    return {"synced": True, "ticket_type": ticket_type.upper(), "ticket_sys_id": sys_id}


@router.get("/{ticket_type}/{sys_id}", response_model=TicketResponse)
async def get_ticket(ticket_type: str, sys_id: str):
    if ticket_type.upper() not in ("SR", "CH", "INC"):
        raise HTTPException(status_code=400, detail="ticket_type must be SR, CH, or INC")
    result = ticket_service.get_ticket(sys_id, ticket_type.upper())
    if not result:
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        ticket_service.sync_ticket_to_graph(sys_id, ticket_type.upper())
    except Exception:
        pass
    return result


@router.patch("/{ticket_type}/{sys_id}", response_model=TicketResponse)
async def update_ticket(ticket_type: str, sys_id: str, req: UpdateTicketRequest):
    if ticket_type.upper() != "INC":
        raise HTTPException(status_code=400, detail="Only incident updates are supported")
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(ticket_service.update_ticket, sys_id, ticket_type.upper(), req.model_dump()),
            timeout=45.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="ServiceNow ticket update timed out")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    if not result:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return result
