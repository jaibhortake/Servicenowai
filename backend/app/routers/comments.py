from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from typing import Optional
from app.models.ticket import AddCommentRequest
from app.services import session_service, servicenow as snow, ticket_service
from app.models.session import SessionState
from app.services.websocket_service import websocket_service
from app.services.comment_polling_service import comment_polling_service

router = APIRouter(prefix="/api/comments", tags=["comments"])


@router.post("/{sys_id}")
async def add_comment(sys_id: str, req: AddCommentRequest, session_id: Optional[str] = Query(None)):
    body = f"[{req.user_name}]: {req.comment}" if req.user_name and not req.is_work_note else req.comment
    ticket_type = str(req.ticket_table or "").upper()
    try:
        if ticket_type == "CH":
            snow.add_comment("change_request", sys_id, body, req.is_work_note)
        elif ticket_type == "INC":
            snow.add_comment("incident", sys_id, body, req.is_work_note)
        else:
            ritm = snow.get_ritm_by_request_sysid(sys_id)
            if ritm:
                snow.add_comment("sc_req_item", ritm["sys_id"], body, req.is_work_note)
            else:
                snow.add_comment("sc_request", sys_id, body, req.is_work_note)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if session_id:
        session = session_service.get_session(session_id)
        if session:
            role = "admin" if req.is_work_note else "user"
            session_service.add_message(session_id, role, req.comment)
            if req.is_work_note:
                session_service.set_state(session_id, SessionState.WAITING_USER)

    try:
        ticket_service.sync_ticket_to_graph(sys_id, ticket_type or "SR")
    except Exception:
        pass

    return {"status": "ok"}


@router.get("/{sys_id}")
async def get_comments(
    sys_id: str,
    ticket_type: str = Query("SR"),
    include_work_notes: bool = Query(False),
):
    try:
        normalized_type = str(ticket_type or "SR").upper()
        comments = []
        if normalized_type == "CH":
            comments = snow.get_comments("change_request", sys_id)
            work_notes = snow.get_work_notes("change_request", sys_id) if include_work_notes else []
        elif normalized_type == "INC":
            comments = snow.get_comments("incident", sys_id)
            work_notes = snow.get_work_notes("incident", sys_id) if include_work_notes else []
        else:
            ritm = snow.get_ritm_by_request_sysid(sys_id)
            if ritm:
                comments = snow.get_comments("sc_req_item", ritm["sys_id"])
                work_notes = snow.get_work_notes("sc_req_item", ritm["sys_id"]) if include_work_notes else []
            else:
                comments = snow.get_comments("sc_request", sys_id)
                work_notes = snow.get_work_notes("sc_request", sys_id) if include_work_notes else []

        if include_work_notes:
            return work_notes
        return comments
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.websocket("/ws/{sys_id}")
async def comments_websocket(websocket: WebSocket, sys_id: str):
    await websocket.accept()
    # Get ticket_type from query parameters
    ticket_type = str(websocket.query_params.get("ticket_type", "SR") or "SR").upper()
    table = "change_request" if ticket_type == "CH" else "incident" if ticket_type == "INC" else "sc_request"

    websocket_service.register(sys_id, websocket)
    comment_polling_service.watch(sys_id, table)

    import logging
    logging.info(f"[WS] Watching ticket {sys_id} (table: {table})")

    try:
        while True:
            # Keep connection alive; ignore incoming messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_service.unregister(sys_id, websocket)
        comment_polling_service.unwatch(sys_id)
        logging.info(f"[WS] Stopped watching ticket {sys_id}")
