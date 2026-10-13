from __future__ import annotations

from typing import Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


class GraphModuleResolverSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["user", "catalog_item", "request_item", "service_request", "change_request", "ticket_state", "time_window"]
    resolver_fields: List[str] = Field(default_factory=list)
    resolver_mode: Literal["lexical", "lexical_plus_embedding"] = "lexical"


class GraphModuleAdapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_name: Literal["users", "catalog", "service_requests", "request_items", "change_requests"]
    entity_types: List[str] = Field(default_factory=list)
    relationship_paths: List[str] = Field(default_factory=list)
    resolver_specs: List[GraphModuleResolverSpec] = Field(default_factory=list)
    query_families: List[str] = Field(default_factory=list)
    output_section_types: List[Literal["count_metric", "list_scope"]] = Field(default_factory=list)
    template_keys: List[str] = Field(default_factory=list)
    evidence_shape: Literal["row_metrics", "ticket_rows", "requester_rows", "item_rows", "subgraph_rows"] = "row_metrics"

    def as_prompt_payload(self) -> Dict[str, object]:
        return {
            "module_name": self.module_name,
            "entity_types": list(self.entity_types),
            "relationship_paths": list(self.relationship_paths),
            "resolver_specs": [spec.model_dump() for spec in self.resolver_specs],
            "query_families": list(self.query_families),
            "output_section_types": list(self.output_section_types),
            "template_keys": list(self.template_keys),
            "evidence_shape": self.evidence_shape,
        }
