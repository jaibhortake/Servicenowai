from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .registry import get_graphrag_module_registry


class GraphModuleEntitySlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_name: Literal["requester", "catalog_item", "category", "request_item", "ticket_state", "time_window"]
    slot_value: str = ""
    source_module: str = ""


class GraphModuleOutputSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_type: Literal["count_metric", "list_scope"] = "count_metric"
    label: str = ""
    source_metric: Optional[str] = None


class GraphModuleQueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["exact_graph_query", "subgraph_reasoning_query", "exploratory_hybrid_query"] = "exploratory_hybrid_query"
    module_path: List[str] = Field(default_factory=list)
    query_family: str = ""
    entity_slots: List[GraphModuleEntitySlot] = Field(default_factory=list)
    time_window: Dict[str, str] = Field(default_factory=dict)
    output_sections: List[GraphModuleOutputSection] = Field(default_factory=list)
    template_key: str = ""
    evidence_scope: Literal["row_metrics", "ticket_rows", "requester_rows", "item_rows", "subgraph_rows", "graph_plus_semantic"] = "graph_plus_semantic"


class GraphModulePlanner:
    def __init__(self) -> None:
        self.registry = get_graphrag_module_registry()

    def registry_payload(self) -> Dict[str, Dict[str, object]]:
        return {name: adapter.as_prompt_payload() for name, adapter in self.registry.items()}

    def build_exact_business_plan(
        self,
        *,
        exact_business_query: object,
        time_window: Dict[str, str],
    ) -> GraphModuleQueryPlan:
        family = str(getattr(exact_business_query, "family", "") or "")
        ticket_type = str(getattr(exact_business_query, "ticket_type", "") or "")
        requester_terms = list(getattr(exact_business_query, "requester_terms", []) or [])
        item_terms = list(getattr(exact_business_query, "item_terms", []) or [])
        category_terms = list(getattr(exact_business_query, "category_terms", []) or [])
        state_terms = list(getattr(exact_business_query, "state_terms", []) or [])
        measure = str(getattr(exact_business_query, "measure", "") or "")

        module_path = ["users"]
        if ticket_type == "CH":
            module_path.append("change_requests")
            template_key = "requester_change_count" if family == "requester_measure" else "requester_change_state_breakdown"
        else:
            module_path.append("service_requests")
            if family == "requester_request_list":
                template_key = "requester_measure_list"
            elif family == "grouped_requester_summary":
                template_key = "grouped_requester_summary"
            elif family == "threshold_requester_aggregate":
                template_key = "threshold_requester_aggregate"
            elif measure == "distinct_catalog_item_count":
                template_key = "requester_measure_count"
            else:
                template_key = "requester_measure_count"

        if item_terms or category_terms:
            module_path.extend(["request_items", "catalog"])

        sections = []
        if family == "requester_request_list":
            sections.append(GraphModuleOutputSection(section_type="list_scope", label="matching requests"))
            evidence_scope = "ticket_rows"
        else:
            sections.append(GraphModuleOutputSection(section_type="count_metric", label=family))
            evidence_scope = "row_metrics"

        entity_slots = self._build_entity_slots(
            requester_terms=requester_terms,
            item_terms=item_terms,
            category_terms=category_terms,
            state_terms=state_terms,
            time_window=time_window,
        )
        return GraphModuleQueryPlan(
            route="exact_graph_query",
            module_path=module_path,
            query_family=family,
            entity_slots=entity_slots,
            time_window=time_window,
            output_sections=sections,
            template_key=template_key,
            evidence_scope=evidence_scope,
        )

    def build_structured_ticket_plan(
        self,
        *,
        breakdown: object,
        output_plan: object,
    ) -> GraphModuleQueryPlan:
        base_ticket_type = str(getattr(breakdown, "base_ticket_type", "") or "")
        requester_terms = list(getattr(breakdown, "requester_terms", []) or [])
        metrics = list(getattr(breakdown, "metrics", []) or [])
        time_window = {
            "label": str(getattr(breakdown, "time_window_label", "") or ""),
            "start": str(getattr(breakdown, "opened_start", "") or ""),
            "end": str(getattr(breakdown, "opened_end", "") or ""),
        }

        module_path = ["users", "change_requests" if base_ticket_type == "CH" else "service_requests"]
        template_key = "requester_change_state_breakdown" if base_ticket_type == "CH" else "requester_metric_breakdown"
        if any(getattr(metric, "catalog_item_terms", None) or getattr(metric, "category_terms", None) for metric in metrics):
            module_path.extend(["request_items", "catalog"])

        sections = [
            GraphModuleOutputSection(
                section_type=str(getattr(section, "section_type", "count_metric") or "count_metric"),
                label=str(getattr(section, "metric_label", "") or ""),
                source_metric=getattr(section, "source_metric", None),
            )
            for section in list(getattr(output_plan, "sections", []) or [])
        ]

        entity_slots = self._build_entity_slots(
            requester_terms=requester_terms,
            item_terms=self._collect_metric_terms(metrics, "catalog_item_terms"),
            category_terms=self._collect_metric_terms(metrics, "category_terms"),
            state_terms=self._collect_metric_terms(metrics, "state_terms"),
            time_window=time_window,
        )
        return GraphModuleQueryPlan(
            route="exact_graph_query",
            module_path=module_path,
            query_family="requester_metric_breakdown",
            entity_slots=entity_slots,
            time_window=time_window,
            output_sections=sections,
            template_key=template_key,
            evidence_scope="ticket_rows" if any(section.section_type == "list_scope" for section in sections) else "row_metrics",
        )

    def build_item_requester_plan(self, requester_query: object) -> GraphModuleQueryPlan:
        item_terms = list(getattr(requester_query, "item_terms", []) or [])
        category_terms = list(getattr(requester_query, "category_terms", []) or [])
        return GraphModuleQueryPlan(
            route="subgraph_reasoning_query",
            module_path=["catalog", "request_items", "service_requests", "users"],
            query_family="item_requester_list",
            entity_slots=self._build_entity_slots(
                requester_terms=[],
                item_terms=item_terms,
                category_terms=category_terms,
                state_terms=[],
                time_window={},
            ),
            time_window={},
            output_sections=[GraphModuleOutputSection(section_type="list_scope", label="matching requesters")],
            template_key="item_requester_list",
            evidence_scope="subgraph_rows",
        )

    def build_item_cohort_plan(self, cohort_query: object) -> GraphModuleQueryPlan:
        item_terms = list(getattr(cohort_query, "seed_item_terms", []) or [])
        category_terms = list(getattr(cohort_query, "seed_category_terms", []) or [])
        return GraphModuleQueryPlan(
            route="subgraph_reasoning_query",
            module_path=["catalog", "request_items", "service_requests", "users"],
            query_family="cohort_expansion",
            entity_slots=self._build_entity_slots(
                requester_terms=[],
                item_terms=item_terms,
                category_terms=category_terms,
                state_terms=[],
                time_window={},
            ),
            time_window={},
            output_sections=[GraphModuleOutputSection(section_type="list_scope", label="cohort expansion")],
            template_key="cohort_expansion",
            evidence_scope="subgraph_rows",
        )

    def build_requester_set_plan(self, set_query: object) -> GraphModuleQueryPlan:
        item_terms = list(getattr(set_query, "item_terms", []) or [])
        category_terms = list(getattr(set_query, "category_terms", []) or [])
        requesters = [str(getattr(set_query, "requester_one", "") or ""), str(getattr(set_query, "requester_two", "") or "")]
        entity_slots = self._build_entity_slots(
            requester_terms=[term for term in requesters if term],
            item_terms=item_terms,
            category_terms=category_terms,
            state_terms=[],
            time_window={},
        )
        return GraphModuleQueryPlan(
            route="subgraph_reasoning_query",
            module_path=["users", "service_requests", "request_items", "catalog"],
            query_family="requester_item_set",
            entity_slots=entity_slots,
            time_window={},
            output_sections=[GraphModuleOutputSection(section_type="list_scope", label="set comparison")],
            template_key="shared_item_set",
            evidence_scope="subgraph_rows",
        )

    def validate_plan(self, plan: GraphModuleQueryPlan) -> GraphModuleQueryPlan:
        valid_modules = [module for module in plan.module_path if module in self.registry]
        if not valid_modules:
            valid_modules = ["catalog"]
        allowed_templates = {
            template
            for module in valid_modules
            for template in self.registry[module].template_keys
        }
        template_key = plan.template_key if plan.template_key in allowed_templates else (next(iter(allowed_templates)) if allowed_templates else "")
        evidence_scope = plan.evidence_scope
        return GraphModuleQueryPlan(
            route=plan.route,
            module_path=valid_modules,
            query_family=plan.query_family,
            entity_slots=plan.entity_slots,
            time_window=plan.time_window,
            output_sections=plan.output_sections,
            template_key=template_key,
            evidence_scope=evidence_scope,
        )

    def _build_entity_slots(
        self,
        *,
        requester_terms: List[str],
        item_terms: List[str],
        category_terms: List[str],
        state_terms: List[str],
        time_window: Dict[str, str],
    ) -> List[GraphModuleEntitySlot]:
        slots: List[GraphModuleEntitySlot] = []
        slots.extend(
            GraphModuleEntitySlot(slot_name="requester", slot_value=term, source_module="users")
            for term in requester_terms if term
        )
        slots.extend(
            GraphModuleEntitySlot(slot_name="catalog_item", slot_value=term, source_module="catalog")
            for term in item_terms if term
        )
        slots.extend(
            GraphModuleEntitySlot(slot_name="category", slot_value=term, source_module="catalog")
            for term in category_terms if term
        )
        slots.extend(
            GraphModuleEntitySlot(slot_name="ticket_state", slot_value=term, source_module="service_requests")
            for term in state_terms if term
        )
        if time_window.get("label"):
            slots.append(
                GraphModuleEntitySlot(
                    slot_name="time_window",
                    slot_value=str(time_window.get("label") or ""),
                    source_module="service_requests",
                )
            )
        return slots

    def _collect_metric_terms(self, metrics: List[object], attr_name: str) -> List[str]:
        values: List[str] = []
        for metric in metrics:
            values.extend(list(getattr(metric, attr_name, []) or []))
        deduped: List[str] = []
        seen = set()
        for value in values:
            lowered = str(value).strip().lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(str(value))
        return deduped
