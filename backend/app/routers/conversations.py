import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query
from typing import List, Optional
from app.models.session import ConversationSession, SessionState
from app.services import session_service, ai_service, servicenow as snow

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# Active WebSocket connections: session_id -> WebSocket
_connections: dict = {}


@router.post("", response_model=ConversationSession)
async def create_conversation(user_id: str = Query(...), user_email: str = Query(...)):
    session = session_service.create_session(user_id=user_id, user_email=user_email)
    return session


@router.get("", response_model=List[ConversationSession])
async def list_conversations(user_email: Optional[str] = Query(None)):
    if user_email:
        return session_service.get_sessions_by_user(user_email)
    return session_service.list_all_sessions()


@router.get("/{session_id}", response_model=ConversationSession)
async def get_conversation(session_id: str):
    session = session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/{session_id}/admin-question")
async def admin_question(session_id: str, question: str = Query(...)):
    session = session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    context = {
        "ticket_number": session.ticket_id or "N/A",
        "ticket_type": session.ticket_type or "SR",
        "state": session.state.value,
        "description": session.context.get("description", ""),
    }

    try:
        ai_message = ai_service.relay_admin_question(question, context)
    except Exception:
        ai_message = question

    session_service.add_message(session_id, "admin", question)
    session_service.add_message(session_id, "ai", ai_message)
    session_service.set_state(session_id, SessionState.WAITING_USER)

    if session_id in _connections:
        ws = _connections[session_id]
        try:
            await ws.send_json({
                "type": "admin_question",
                "message": ai_message,
                "original": question,
            })
        except Exception:
            pass

    if session.ticket_id and session.ticket_type:
        try:
            if session.ticket_type == "CH":
                snow.add_comment("change_request", session.ticket_id,
                                 f"[Admin Question via AI Portal]\n{question}", is_work_note=True)
            else:
                ritm = snow.get_ritm_by_request_sysid(session.ticket_id)
                if ritm:
                    snow.add_comment("sc_req_item", ritm["sys_id"],
                                     f"[Admin Question via AI Portal]\n{question}", is_work_note=True)
                else:
                    snow.add_comment("sc_request", session.ticket_id,
                                     f"[Admin Question via AI Portal]\n{question}", is_work_note=True)
        except Exception:
            pass

    return {"status": "ok", "ai_message": ai_message}


@router.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    await websocket.accept()
    _connections[session_id] = websocket

    session = session_service.get_session(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close()
        return

    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "messages": [m.model_dump(mode="json") for m in session.messages],
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"type": "message", "content": data}

            msg_type = payload.get("type", "message")
            content = payload.get("content", "")

            if msg_type == "message" and content:
                session_service.add_message(session_id, "user", content)

                history = [
                    {"role": "assistant" if m.role in ("ai", "system") else "user", "content": m.content}
                    for m in (session.messages or [])[-10:]
                ]

                try:
                    ai_reply = ai_service.generate_ai_response(
                        content, history, session.context
                    )
                except Exception as e:
                    ai_reply = "I'm having trouble connecting to the AI service. Please try again."

                session_service.add_message(session_id, "ai", ai_reply)
                session_service.set_state(session_id, SessionState.WAITING_ADMIN)

                import logging as log
                log.info(f"WS msg: ticket_id={session.ticket_id}, type={session.ticket_type}")

                if session.ticket_id and session.ticket_type:
                    try:
                        user_label = session.user_email or "User"
                        body = f"[{user_label}]: {content}"
                        if session.ticket_type == "CH":
                            snow.add_comment("change_request", session.ticket_id, body)
                        else:
                            ritm = snow.get_ritm_by_request_sysid(session.ticket_id)
                            if ritm:
                                snow.add_comment("sc_req_item", ritm["sys_id"], body)
                            else:
                                snow.add_comment("sc_request", session.ticket_id, body)
                    except Exception as e:
                        import logging
                        logging.error(f"Failed to post WS comment to SN: {e}")

                await websocket.send_json({
                    "type": "ai_response",
                    "message": ai_reply,
                    "user_message": content,
                })

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        _connections.pop(session_id, None)
