from typing import Dict, Optional, List
from datetime import datetime
from app.models.session import ConversationSession, Message, SessionState
import uuid

# In-memory store — replace with Redis/DB for production
_sessions: Dict[str, ConversationSession] = {}


def create_session(user_id: str, user_email: str) -> ConversationSession:
    session = ConversationSession(user_id=user_id, user_email=user_email)
    _sessions[session.session_id] = session
    return session


def get_session(session_id: str) -> Optional[ConversationSession]:
    return _sessions.get(session_id)


def get_sessions_by_user(user_email: str) -> List[ConversationSession]:
    return [s for s in _sessions.values() if s.user_email == user_email]


def get_session_by_ticket(ticket_id: str) -> Optional[ConversationSession]:
    for s in _sessions.values():
        if s.ticket_id == ticket_id:
            return s
    return None


def update_session(session: ConversationSession) -> ConversationSession:
    session.last_activity = datetime.utcnow()
    _sessions[session.session_id] = session
    return session


def add_message(session_id: str, role: str, content: str, metadata: dict = None) -> Optional[Message]:
    session = _sessions.get(session_id)
    if not session:
        return None
    msg = Message(role=role, content=content, metadata=metadata or {})
    session.messages.append(msg)
    session.last_activity = datetime.utcnow()
    return msg


def set_ticket(session_id: str, ticket_id: str, ticket_type: str) -> bool:
    session = _sessions.get(session_id)
    if not session:
        return False
    session.ticket_id = ticket_id
    session.ticket_type = ticket_type
    session.last_activity = datetime.utcnow()
    return True


def set_state(session_id: str, state: SessionState) -> bool:
    session = _sessions.get(session_id)
    if not session:
        return False
    session.state = state
    session.last_activity = datetime.utcnow()
    return True


def list_all_sessions() -> List[ConversationSession]:
    return list(_sessions.values())
