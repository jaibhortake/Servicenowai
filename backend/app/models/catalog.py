from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Literal


class CatalogItem(BaseModel):
    sys_id: str
    name: str
    display_name: Optional[str] = None
    short_description: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    category_sys_id: Optional[str] = None
    category_name: Optional[str] = None
    icon: Optional[str] = None
    price: Optional[str] = None
    search_text: Optional[str] = None
    relevance_score: float = 0.0
    match_reason: Optional[str] = None


class CatalogVariableChoice(BaseModel):
    label: str
    value: str


class CatalogVariable(BaseModel):
    sys_id: str
    name: str
    label: str
    type: str = "text"
    mandatory: bool = False
    default_value: Optional[str] = None
    choices: List[CatalogVariableChoice] = []
    depends_on: Optional[str] = None
    inactive_on_value: Optional[str] = None
    active_on_value: Optional[str] = None


class CatalogFormResponse(BaseModel):
    item: CatalogItem
    variables: List[CatalogVariable] = []


class InterpretRequest(BaseModel):
    text: str
    session_id: Optional[str] = None
    user_email: Optional[str] = None


class InterpretResponse(BaseModel):
    intent: str
    confidence: float
    entities: Dict[str, Any]
    ticket_type: Literal["SR", "CH"]
    suggested_catalogs: List[CatalogItem] = []
    selected_catalog: Optional[CatalogItem] = None
    clarification_needed: bool = False
    clarification_question: Optional[str] = None


class RankCatalogRequest(BaseModel):
    query: str
    catalogs: List[Dict[str, Any]]


class UserProfile(BaseModel):
    sys_id: str
    name: str
    email: str
    department: Optional[str] = None
    department_id: Optional[str] = None
    manager: Optional[str] = None
    manager_id: Optional[str] = None
    location: Optional[str] = None
    title: Optional[str] = None
