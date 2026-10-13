from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from enum import Enum


class TicketState(str, Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING_INFO = "waiting_info"
    RESOLVED = "resolved"
    CLOSED = "closed"


class RiskLevel(str, Enum):
    HIGH = "1"
    MEDIUM = "2"
    LOW = "3"
    VERY_LOW = "4"


class CreateSRRequest(BaseModel):
    catalog_item_id: str
    requested_for: str  # sys_id of user
    short_description: str
    description: Optional[str] = None
    urgency: Optional[str] = "3"
    impact: Optional[str] = "3"
    variables: Dict[str, Any] = {}


class CreateCHRequest(BaseModel):
    short_description: str
    description: Optional[str] = None
    risk: RiskLevel = RiskLevel.LOW
    impact: str = "3"
    change_type: str = "normal"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    requested_by: Optional[str] = None
    assignment_group: Optional[str] = None


class CreateIncidentRequest(BaseModel):
    short_description: str
    description: Optional[str] = None
    caller_id: Optional[str] = None
    impact: str = "3"
    urgency: str = "3"
    priority: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    assignment_group: Optional[str] = None
    cmdb_ci: Optional[str] = None
    work_notes: Optional[str] = None
    comments: Optional[str] = None


class AddCommentRequest(BaseModel):
    ticket_sys_id: str
    ticket_table: str  # sc_request | change_request
    comment: str
    is_work_note: bool = False
    user_name: Optional[str] = None  # display name to prefix comment with


class UpdateTicketRequest(BaseModel):
    state: Optional[str] = None
    incident_state: Optional[str] = None
    close_code: Optional[str] = None
    close_notes: Optional[str] = None
    resolution_notes: Optional[str] = None
    root_cause: Optional[str] = None
    workaround: Optional[str] = None


class TicketResponse(BaseModel):
    sys_id: str
    number: str
    short_description: str
    state: str
    opened_at: Optional[str] = None
    assigned_to: Optional[str] = None
    ticket_type: str  # SR | CH
    ritm_sys_id: Optional[str] = None  # sc_req_item sys_id for SR comments
    raw: Dict[str, Any] = {}
