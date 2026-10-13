from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
import uuid


class SessionState(str, Enum):
    OPEN = "OPEN"
    WAITING_USER = "WAITING_USER"
    WAITING_ADMIN = "WAITING_ADMIN"
    RESOLVED = "RESOLVED"


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str  # "user" | "admin" | "ai" | "system"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    user_email: str
    ticket_id: Optional[str] = None
    ticket_type: Optional[str] = None  # "SR" | "CH"
    ritm_sys_id: Optional[str] = None  # sc_req_item sys_id for SR comments
    state: SessionState = SessionState.OPEN
    context: Dict[str, Any] = Field(default_factory=dict)
    messages: List[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity: datetime = Field(default_factory=datetime.utcnow)
