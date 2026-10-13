from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class GraphRAGv2NodeType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    count: int = 0
    properties: List[str] = Field(default_factory=list)


class GraphRAGv2RelationshipType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_label: str
    rel_type: str
    to_label: str


class GraphRAGv2TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    start: str = ""
    end: str = ""


class GraphRAGv2PropertyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    property_name: str
    property_type: Literal[
        "text",
        "enum",
        "numeric_like_string",
        "datetime_like",
        "boolean_like",
        "id_like",
        "unknown",
    ] = "unknown"
    sample_values: List[str] = Field(default_factory=list)
    searchable_score: int = 0


class GraphRAGv2VirtualEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_label: str
    from_property: str
    to_label: str
    to_property: str
    edge_type: str
    confidence: float = 0.0
    sample_overlap: int = 0


class GraphRAGv2SemanticProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    property_profiles: List[GraphRAGv2PropertyProfile] = Field(default_factory=list)
    searchable_fields_by_label: Dict[str, List[str]] = Field(default_factory=dict)
    enum_values_by_label_property: Dict[str, List[str]] = Field(default_factory=dict)
    virtual_edges: List[GraphRAGv2VirtualEdge] = Field(default_factory=list)


class GraphRAGv2ResolvedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    property_name: str
    value: str
    score: float = 0.0


class GraphRAGv2GraphPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_type: Literal["anchor", "filter", "traverse", "virtual_traverse", "aggregate", "return"]
    description: str = ""


class GraphRAGv2GraphQueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    steps: List[GraphRAGv2GraphPlanStep] = Field(default_factory=list)
    resolved_entities: List[GraphRAGv2ResolvedEntity] = Field(default_factory=list)


class GraphRAGv2SchemaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    generated_at: str
    summary_text: str
    graph_profile: Literal["servicenow_like", "generic_business", "unknown_generic"] = "unknown_generic"
    node_types: List[GraphRAGv2NodeType] = Field(default_factory=list)
    relationships: List[GraphRAGv2RelationshipType] = Field(default_factory=list)
    allowed_labels: List[str] = Field(default_factory=list)
    allowed_relationships: List[str] = Field(default_factory=list)
    time_field_candidates: List[str] = Field(default_factory=list)
    service_now_hints_enabled: bool = False
    module_hints: List[str] = Field(default_factory=list)


class GraphRAGv2OntologyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_labels: List[str] = Field(default_factory=list)
    event_labels: List[str] = Field(default_factory=list)
    problem_labels: List[str] = Field(default_factory=list)
    request_labels: List[str] = Field(default_factory=list)
    search_fields_by_label: Dict[str, List[str]] = Field(default_factory=dict)
    time_fields: List[str] = Field(default_factory=list)
    recommendation_fields: List[str] = Field(default_factory=list)
    role_paths: Dict[str, List[str]] = Field(default_factory=dict)


class GraphRAGv2CypherCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    query_shape: Literal["count", "list", "compare", "cohort", "rank", "aggregate", "lookup"] = "lookup"
    purpose: str = ""
    cypher_query: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class GraphRAGv2ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    valid: bool
    reason: str = ""


class GraphRAGv2ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    query_shape: Literal["count", "list", "compare", "cohort", "rank", "aggregate", "lookup"] = "lookup"
    purpose: str = ""
    cypher_query: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    ok: bool = False
    error: str = ""
    duration_ms: float = 0.0


class GraphRAGv2EvidencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_snapshot_id: str
    graph_profile: str = "unknown_generic"
    allowed_labels: List[str] = Field(default_factory=list)
    allowed_relationships: List[str] = Field(default_factory=list)
    time_field_candidates: List[str] = Field(default_factory=list)
    service_now_hints_enabled: bool = False
    ontology_profile: Optional[GraphRAGv2OntologyProfile] = None
    query_family: str = ""
    required_evidence_fields: List[str] = Field(default_factory=list)
    execution_outcome: str = ""
    selected_route: str = ""
    selection_reason: str = ""
    repair_attempted: bool = False
    repair_successful: bool = False
    baseline_route_result: Dict[str, Any] = Field(default_factory=dict)
    ontology_route_result: Dict[str, Any] = Field(default_factory=dict)
    generated_candidates: List[GraphRAGv2CypherCandidate] = Field(default_factory=list)
    validation_status: List[GraphRAGv2ValidationResult] = Field(default_factory=list)
    chosen_candidate: Optional[str] = None
    executed_cypher: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    executed_sections: List[GraphRAGv2ExecutionResult] = Field(default_factory=list)
    timings: Dict[str, float] = Field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str = ""


class GraphRAGv2QueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    evidence: GraphRAGv2EvidencePayload
    fallback_result: Optional[Dict[str, Any]] = None
