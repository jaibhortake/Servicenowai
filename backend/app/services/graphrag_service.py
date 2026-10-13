import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services import catalog_lexical_index, query_expansion
from app.services.ai_service import get_async_client
from app.services.graphrag_modules import GraphModulePlanner, GraphModuleQueryPlan
from app.services.neo4j_catalog_graph import neo4j_catalog_graph_service
from app.services.neo4j_query_runtime import neo4j_query_runtime_service
from app.services.neo4j_schema_graph import neo4j_schema_graph_service
from app.services.neo4j_ticket_graph import neo4j_ticket_graph_service
from app.services.semantic_query_mapper import parse_semantic_intent, semantic_search
from app.services import servicenow as snow, session_service

logger = logging.getLogger(__name__)

NO_CONTEXT_ANSWER = "I couldn't find matching catalog, schema, or ticket entities in the graph for that request."
INSUFFICIENT_CONTEXT_ANSWER = "I found related graph data, but it is not sufficient to answer that request confidently."
GRAPH_RAG_QUERY_TIMEOUT_SECONDS = 35.0
GRAPH_RAG_LLM_TIMEOUT_SECONDS = 12.0
GRAPH_RAG_NEO4J_TIMEOUT_SECONDS = 8.0
GRAPH_RAG_RESOLVER_TIMEOUT_SECONDS = 6.0
GRAPH_RAG_TIMEOUT_ANSWER = (
    "The graph query timed out before I could finish a grounded answer. "
    "Try narrowing the question, or retry after the graph service has caught up."
)

ENTITY_EXTRACTION_PROMPT = """Extract ServiceNow graph retrieval terms from the user query.

Return JSON with these keys only:
- catalog_terms: catalog item names or request names
- table_terms: ServiceNow table names
- field_terms: variable names, field labels, or column names
- concept_terms: broader IT concepts useful for graph lookup

Guidelines:
- Keep each list short and concrete.
- Prefer exact nouns and short noun phrases.
- Infer likely ServiceNow concepts even when the user uses informal wording.
- Normalize intent into graph-friendly terms. For example:
  - "remote work" may imply vpn or remote access
  - "new laptop" may imply laptop, macbook, notebook, or hardware request
  - "employee setup" may imply onboarding hardware or access requests
- If a category is not present, return an empty list.

User query: "{query}"
"""

INTENT_PLAN_PROMPT = """You are planning a safe ServiceNow graph retrieval strategy.

Return JSON with these keys only:
- domain: "catalog", "schema", or "ticket"
- intent_family: one of "lookup", "explain", "list", "compare", "rank", "filter", "count", "aggregate"
- entity_scope: one of "item", "variable", "choice", "table", "column", "ticket", "request_item", "catalog_wide", "schema_wide", "ticket_wide"
- answer_style: one of "concise", "enumerated_list", "comparison_summary"
- named_entities: exact product, catalog, or table names if present
- category_terms: category-like terms such as laptop, software, vpn, access, hardware
- catalog_terms
- table_terms
- field_terms
- concept_terms
- sort_field: null or one of "price", "name", "category", "field_count", "column_count", "request_count"
- sort_direction: "asc" or "desc"
- aggregation: null or one of "min", "max", "count", "distinct", "grouped_count"
- limit: integer from 1 to 10

Important:
- Plan from user intent, not only exact keywords.
- The graph may use different wording than the user, so include semantic equivalents.
- Use "rank" when the user asks for cheapest, most expensive, top, lowest, highest, best match by an explicit sortable field.
- Use sort_field "request_count" when the user asks for most requested, least requested, most popular, most used, or frequently requested catalog items.
- Use "count" when the user asks how many items or tables match.
- Questions about requests or tickets that were made, submitted, created, raised, opened, or logged should use domain "ticket" and count or list matching request records when the user is asking about request history rather than catalog definitions.
- Use "compare" when the user asks to compare named items or alternatives.
- Use "list" when the user asks for matching items or tables.
- Use "lookup" or "explain" for item details, fields, variables, options, or schema explanations.
- Use entity_scope "variable" for form fields and required fields.
- Use entity_scope "choice" for options or allowed values.
- Use entity_scope "column" for schema column requests.
- Use domain "ticket" when the user asks about tickets, requests, change requests, service requests, or ticket numbers.
- Use entity_scope "ticket" for specific ticket details and "ticket_wide" for counts, lists, or rankings over a user's tickets.
- Prefer domain "schema" only when the user is asking about tables, columns, or data structures.

User query: "{query}"
"""

ANSWER_PROMPT = """You are a GraphRAG expert for ServiceNow.
Use only the supplied graph retrieval result to answer the user's question.
Do not invent catalog items, fields, choices, tables, or columns.
If the retrieval result is insufficient, say so clearly.

Retrieval result:
{result}

User query: "{query}"
"""

CYPHER_BLUEPRINT_PROMPT = """You are composing a safe read-only Cypher retrieval blueprint for an existing ServiceNow graph.

Return JSON with these keys only:
- domain: "catalog", "schema", or "ticket"
- goal: one of "lookup", "explain", "list", "compare", "rank", "filter", "count", "aggregate"
- primary_label: one of "CatalogItem", "CatalogVariable", "CatalogChoice", "Table", "Column", "Ticket", "RequestItem"
- cypher_query: a read-only Cypher query using placeholders like $subject_terms, $field_terms, $table_terms, $state_terms, $ticket_numbers, $limit
- relationship_chain: list of relationships used in the MATCH path
- subject_terms
- field_terms
- table_terms
- state_terms
- ticket_numbers
- sort_field: null or one of "price", "name", "category", "field_count", "column_count", "opened_at", "request_count"
- sort_direction: "asc" or "desc"
- requires_ticket_history: boolean

Rules:
- Use MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT only.
- Do not use CREATE, MERGE, DELETE, SET, CALL, APOC, or any write clause.
- Compose the query from user intent, not just exact keywords.
- If the user is asking about request history or tickets created/made/submitted, use domain "ticket".
- If the user is asking about catalog definitions, fields, choices, pricing, or item comparison, use domain "catalog".
- If the user is asking about tables or columns, use domain "schema".
- Keep placeholders generic so the retrieval layer can map them semantically to the existing graph.

User query: "{query}"
"""

GRAPH_QUERY_PROGRAM_PROMPT = """You are compiling a safe read-only Cypher query for a ServiceNow knowledge graph.

Graph schema:
{schema}

Structured hints:
{hints}

User query:
{query}

Return JSON with these keys only:
- intent_summary
- answer_style: one of "concise", "enumerated_list", "comparison_summary"
- cypher_query
- expected_columns
- subject_terms
- field_terms
- table_terms
- state_terms
- ticket_numbers
- limit

Rules:
- Use only read-only Cypher clauses: MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT.
- Never use CREATE, MERGE, DELETE, SET, REMOVE, CALL, APOC, FOREACH, LOAD CSV, DROP, ALTER, or semicolons.
- Use only these placeholders: $subject_terms, $field_terms, $table_terms, $state_terms, $ticket_numbers, $limit.
- Always alias returned values with simple names like name, number, description, state, count, recommendation, table_name.
- Prefer the smallest query that directly answers the user question.
- Support multi-hop reasoning when needed through the known relationships in the schema.
- For co-occurrence or recommendation questions, use shared User -> Ticket -> RequestItem -> CatalogItem traversals.
- If the user asks for names only, return only names.
- If the user asks for numbers with descriptions, return both.
- If the question asks "who", return people names and the related item or request context.
"""

GRAPH_QUERY_ANSWER_PROMPT = """You are answering a user question from rows returned by a validated read-only Cypher query over a ServiceNow graph.

Use only the supplied rows.
Do not invent entities, relationships, counts, or recommendations.
If the rows are empty or insufficient, say so clearly.
Respect the user's formatting intent:
- if they asked for names only, return names only
- if they asked for request numbers with descriptions, include both
- if they asked for a list, present a compact list
- if they asked for a recommendation-like relation, explain it as an observed graph pattern, not a certainty

Query summary:
{summary}

Rows:
{rows}

User query:
{query}
"""

TICKET_QUERY_BREAKDOWN_PROMPT = """Decompose the user query into a deterministic ServiceNow ticket query plan.

Return JSON with these keys only:
- domain: "ticket"
- answer_intent: "count" or "list"
- requester_terms: requester/user names only
- base_ticket_type: "SR", "CH", or null
- metrics: ordered metric definitions

Each metric must have:
- label: user-facing label such as "total requests", "hardware requests", or "closed change requests"
- parent_metric: previous metric label when the user says "of them", "those", or "of those"; otherwise null
- inherit_parent_filters: true only when the metric is explicitly scoped by "of them", "those", or "of those"
- ticket_type: "SR", "CH", or null
- requester_terms: requester/user names only when the metric overrides the base requester; otherwise []
- catalog_item_terms: catalog item or requested item words such as "standard laptop"
- category_terms: broader item/category words such as "hardware" or "software"
- state_terms: state words such as "closed", "open", or "pending"
- ticket_numbers: request/ticket identifiers only

Rules:
- Keep people/requesters separate from item/category terms.
- Do not put requester names into catalog_item_terms or category_terms.
- Do not apply subset filters like "hardware", "standard laptop", or "closed" to a "total" metric.
- Plain "requests" normally means service requests and should use base_ticket_type "SR".
- "change requests" means base_ticket_type "CH".
- If the user asks for request numbers or ticket numbers, use answer_intent "list".
- For "how many total requests have lucius bagnoli made and how many those are hardware and how many of them are standard laptop",
  return metrics for "total requests", "hardware requests", and "standard laptop requests"; the standard laptop metric should inherit from the hardware metric.

User query:
{query}
"""

TICKET_OUTPUT_PLAN_PROMPT = """Decompose the user query into output sections for an exact ServiceNow ticket answer.

Return JSON with these keys only:
- sections: ordered output sections

Each section must have:
- section_type: "count_metric" or "list_scope"
- metric_label: the metric label this section should render from, such as "total requests" or "hardware requests"
- source_metric: for list_scope only, the metric label whose matching requests should be listed; otherwise null
- include_descriptions: true when the user asked for descriptions/details, otherwise false

Rules:
- Preserve the output order the user asked for.
- If the user asks only for counts, return only count_metric sections.
- If the user asks only for matching requests/request numbers/ticket numbers, return only a list_scope section.
- If the user asks for both counts and matching requests, return both.
- Pronoun follow-ups like "they", "them", "those" should point the list_scope to the most recent subset metric when present, otherwise the most recent metric.
- Do not invent new metric labels; use labels that align with the exact metric plan.

User query:
{query}
"""

TERM_STOP_WORDS = {
    "what", "which", "show", "tell", "give", "need", "want", "with", "from", "this", "that",
    "about", "into", "onto", "form", "request", "item", "catalog", "service", "please",
    "required", "mandatory", "field", "fields", "column", "columns", "table", "tables",
    "option", "options", "choice", "choices", "data", "access", "type", "types",
    "are", "the", "for", "and", "but", "not", "you", "your", "our", "their", "them",
    "to", "too", "from", "into", "onto", "over", "under", "between", "about", "after",
    "before", "me", "my", "mine", "we", "us", "on", "off", "in", "out", "of", "at",
    "by", "as", "is", "it", "its", "a", "an",
}

TICKET_STATE_TERMS = {
    "open",
    "opened",
    "pending",
    "resolved",
    "closed",
    "close",
    "complete",
    "completed",
    "progress",
    "new",
    "cancelled",
    "canceled",
}

TICKET_STATE_CANONICAL = {
    "opened": "open",
    "close": "closed",
    "complete": "closed",
    "completed": "closed",
    "cancelled": "closed",
    "canceled": "closed",
}

TICKET_ITEM_HINT_TERMS = {
    "access",
    "adobe",
    "apple",
    "application",
    "business",
    "computer",
    "desktop",
    "email",
    "hardware",
    "ios",
    "ipad",
    "iphone",
    "keyboard",
    "laptop",
    "macbook",
    "mobile",
    "monitor",
    "mouse",
    "network",
    "notebook",
    "phone",
    "printer",
    "software",
    "standard",
    "tablet",
    "vpn",
    "windows",
}

BROAD_TICKET_CATEGORY_TERMS = {
    "access",
    "computer",
    "desktop",
    "hardware",
    "laptop",
    "macbook",
    "mobile",
    "network",
    "notebook",
    "phone",
    "printer",
    "software",
    "tablet",
    "vpn",
}


class GraphRAGEntities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_terms: List[str] = Field(default_factory=list)
    table_terms: List[str] = Field(default_factory=list)
    field_terms: List[str] = Field(default_factory=list)
    concept_terms: List[str] = Field(default_factory=list)


class GraphRAGRetrievalPlan(GraphRAGEntities):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["catalog", "schema", "ticket"] = "catalog"
    intent_family: Literal["lookup", "explain", "list", "compare", "rank", "filter", "count", "aggregate"] = "lookup"
    entity_scope: Literal["item", "variable", "choice", "table", "column", "ticket", "request_item", "catalog_wide", "schema_wide", "ticket_wide"] = "item"
    answer_style: Literal["concise", "enumerated_list", "comparison_summary"] = "concise"
    named_entities: List[str] = Field(default_factory=list)
    category_terms: List[str] = Field(default_factory=list)
    sort_field: Optional[Literal["price", "name", "category", "field_count", "column_count", "opened_at", "request_count"]] = None
    sort_direction: Literal["asc", "desc"] = "asc"
    aggregation: Optional[Literal["min", "max", "count", "distinct", "grouped_count"]] = None
    limit: int = 5


class GraphRAGCypherBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["catalog", "schema", "ticket"] = "catalog"
    goal: Literal["lookup", "explain", "list", "compare", "rank", "filter", "count", "aggregate"] = "lookup"
    primary_label: Literal["CatalogItem", "CatalogVariable", "CatalogChoice", "Table", "Column", "Ticket", "RequestItem"] = "CatalogItem"
    cypher_query: str = ""
    relationship_chain: List[str] = Field(default_factory=list)
    subject_terms: List[str] = Field(default_factory=list)
    field_terms: List[str] = Field(default_factory=list)
    table_terms: List[str] = Field(default_factory=list)
    state_terms: List[str] = Field(default_factory=list)
    ticket_numbers: List[str] = Field(default_factory=list)
    sort_field: Optional[Literal["price", "name", "category", "field_count", "column_count", "opened_at", "request_count"]] = None
    sort_direction: Literal["asc", "desc"] = "asc"
    requires_ticket_history: bool = False


class GraphRAGCypherProgram(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_summary: str = ""
    answer_style: Literal["concise", "enumerated_list", "comparison_summary"] = "concise"
    cypher_query: str = ""
    expected_columns: List[str] = Field(default_factory=list)
    subject_terms: List[str] = Field(default_factory=list)
    field_terms: List[str] = Field(default_factory=list)
    table_terms: List[str] = Field(default_factory=list)
    state_terms: List[str] = Field(default_factory=list)
    ticket_numbers: List[str] = Field(default_factory=list)
    requester_terms: List[str] = Field(default_factory=list)
    segment_one_terms: List[str] = Field(default_factory=list)
    segment_two_terms: List[str] = Field(default_factory=list)
    segment_one_label: Optional[str] = None
    segment_two_label: Optional[str] = None
    limit: int = 15


class GraphRAGMetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = ""
    parent_metric: Optional[str] = None
    inherit_parent_filters: bool = False
    ticket_type: Optional[Literal["SR", "CH"]] = None
    requester_terms: List[str] = Field(default_factory=list)
    catalog_item_terms: List[str] = Field(default_factory=list)
    category_terms: List[str] = Field(default_factory=list)
    state_terms: List[str] = Field(default_factory=list)
    ticket_numbers: List[str] = Field(default_factory=list)


class GraphRAGQueryBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["ticket"] = "ticket"
    answer_intent: Literal["count", "list"] = "count"
    requester_terms: List[str] = Field(default_factory=list)
    base_ticket_type: Optional[Literal["SR", "CH"]] = None
    time_window_label: str = ""
    opened_start: str = ""
    opened_end: str = ""
    metrics: List[GraphRAGMetricSpec] = Field(default_factory=list)


class GraphRAGOutputSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_type: Literal["count_metric", "list_scope"] = "count_metric"
    metric_label: str = ""
    source_metric: Optional[str] = None
    include_descriptions: bool = False


class GraphRAGTicketOutputPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: List[GraphRAGOutputSection] = Field(default_factory=list)


class GraphRAGRequesterSetQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["ticket"] = "ticket"
    answer_intent: Literal["list", "count"] = "list"
    operation: Literal["intersection", "union", "left_difference", "right_difference", "symmetric_difference"] = "intersection"
    requester_one: str = ""
    requester_two: str = ""
    item_phrase: str = ""
    item_terms: List[str] = Field(default_factory=list)
    category_terms: List[str] = Field(default_factory=list)
    ticket_type: Optional[Literal["SR", "CH"]] = "SR"


class GraphRAGItemRequesterQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["ticket"] = "ticket"
    answer_intent: Literal["list", "count"] = "list"
    item_phrase: str = ""
    item_terms: List[str] = Field(default_factory=list)
    category_terms: List[str] = Field(default_factory=list)
    ticket_type: Optional[Literal["SR", "CH"]] = "SR"


class GraphRAGItemCohortExpansionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["ticket"] = "ticket"
    answer_intent: Literal["list"] = "list"
    seed_item_phrase: str = ""
    seed_item_terms: List[str] = Field(default_factory=list)
    seed_category_terms: List[str] = Field(default_factory=list)
    ticket_type: Optional[Literal["SR", "CH"]] = "SR"


@dataclass
class GraphRAGTimeWindow:
    label: str = ""
    start: str = ""
    end: str = ""


@dataclass
class GraphRAGExactBusinessQuery:
    family: str
    measure: str
    requester_terms: List[str] = field(default_factory=list)
    item_terms: List[str] = field(default_factory=list)
    category_terms: List[str] = field(default_factory=list)
    state_terms: List[str] = field(default_factory=list)
    ticket_type: Optional[str] = "SR"
    time_window: Optional[GraphRAGTimeWindow] = None
    threshold: int = 0
    limit: int = 25


class GraphRAGRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["exact_graph_query", "subgraph_reasoning_query", "exploratory_hybrid_query"] = "exploratory_hybrid_query"
    reason: str = ""
    retrieval_mode: Literal["fixed_cypher_template", "bounded_subgraph", "graph_plus_semantic"] = "graph_plus_semantic"


_graphrag_debug_logger: Optional[logging.Logger] = None


def _get_graphrag_debug_logger() -> Optional[logging.Logger]:
    global _graphrag_debug_logger
    if not settings.graphrag_debug_log_enabled:
        return None
    if _graphrag_debug_logger is not None:
        return _graphrag_debug_logger

    try:
        log_path = Path(settings.graphrag_debug_log_path).expanduser()
        if not log_path.is_absolute():
            project_root = Path(__file__).resolve().parents[3]
            log_path = project_root / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger_instance = logging.getLogger("app.graphrag.debug")
        logger_instance.setLevel(logging.INFO)
        logger_instance.propagate = False
        if not logger_instance.handlers:
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger_instance.addHandler(handler)
        _graphrag_debug_logger = logger_instance
        return _graphrag_debug_logger
    except Exception:
        logger.exception("[GRAPHRAG] Failed to initialize debug log handler")
        return None


class GraphRAGService:
    def __init__(self) -> None:
        self.module_planner = GraphModulePlanner()

    async def query(self, user_query: str, user_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        logger.info("[GRAPHRAG:%s] Query started: %s", request_id, user_query)

        try:
            result = await asyncio.wait_for(
                self._query_impl(user_query, user_context or {}, request_id),
                timeout=GRAPH_RAG_QUERY_TIMEOUT_SECONDS,
            )
            self._write_graphrag_debug_event(
                request_id=request_id,
                user_query=user_query,
                started=started,
                result=result,
            )
            logger.info(
                "[GRAPHRAG:%s] Query finished in %.2fs",
                request_id,
                time.perf_counter() - started,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "[GRAPHRAG:%s] Query timed out after %.2fs",
                request_id,
                GRAPH_RAG_QUERY_TIMEOUT_SECONDS,
            )
            result = {
                "answer": GRAPH_RAG_TIMEOUT_ANSWER,
                "plan": {},
                "cypher": {"mode": "timeout", "request_id": request_id},
                "entities": {},
                "candidate_count": 0,
                "context": [],
                "context_count": 0,
                "error": "timeout",
            }
            self._write_graphrag_debug_event(
                request_id=request_id,
                user_query=user_query,
                started=started,
                result=result,
            )
            return result

    def _write_graphrag_debug_event(
        self,
        *,
        request_id: str,
        user_query: str,
        started: float,
        result: Dict[str, Any],
    ) -> None:
        debug_logger = _get_graphrag_debug_logger()
        if debug_logger is None:
            return

        try:
            payload = {
                "request_id": request_id,
                "query": user_query,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "route": result.get("route", ""),
                "plan": result.get("plan", {}),
                "entities": result.get("entities", {}),
                "cypher": result.get("cypher", {}),
                "candidate_count": result.get("candidate_count", 0),
                "context_count": result.get("context_count", 0),
                "answer": result.get("answer", ""),
                "normalized_result": result.get("normalized_result", {}),
                "error": result.get("error", ""),
            }
            debug_logger.info(json.dumps(payload, ensure_ascii=True))
        except Exception:
            logger.exception("[GRAPHRAG] Failed to write debug log event")

    async def _query_impl(
        self,
        user_query: str,
        user_context: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        route_decision = self._classify_graphrag_route(user_query)
        logger.info(
            "[GRAPHRAG:%s] Route selected: %s (%s)",
            request_id,
            route_decision.route,
            route_decision.reason or "no reason",
        )

        if route_decision.route == "exact_graph_query":
            exact_result = await self._execute_exact_graph_route(user_query)
            if exact_result.get("handled"):
                return self._package_route_result(
                    user_query=user_query,
                    route_decision=route_decision,
                    plan=exact_result.get("plan"),
                    cypher=exact_result.get("cypher", {}),
                    entities=exact_result.get("entities", {}),
                    context=exact_result.get("context", []),
                    candidate_count=exact_result.get("candidate_count", 0),
                    answer=exact_result.get("answer") or INSUFFICIENT_CONTEXT_ANSWER,
                    time_window=exact_result.get("time_window"),
                )
            logger.warning("[GRAPHRAG:%s] Exact graph route fell through to exploratory fallback", request_id)

        if route_decision.route == "subgraph_reasoning_query":
            subgraph_result = await self._execute_subgraph_reasoning_route(user_query)
            if subgraph_result.get("handled"):
                return self._package_route_result(
                    user_query=user_query,
                    route_decision=route_decision,
                    plan=subgraph_result.get("plan"),
                    cypher=subgraph_result.get("cypher", {}),
                    entities=subgraph_result.get("entities", {}),
                    context=subgraph_result.get("context", []),
                    candidate_count=subgraph_result.get("candidate_count", 0),
                    answer=subgraph_result.get("answer") or INSUFFICIENT_CONTEXT_ANSWER,
                    time_window=subgraph_result.get("time_window"),
                )
            logger.warning("[GRAPHRAG:%s] Subgraph route fell through to exploratory fallback", request_id)

        return await self._execute_exploratory_hybrid_route(
            user_query=user_query,
            user_context=user_context,
            request_id=request_id,
        )

    def _classify_graphrag_route(self, query: str) -> GraphRAGRouteDecision:
        exact_business_query = self._parse_exact_business_query(query)
        if exact_business_query:
            module_plan = self._build_module_plan_for_exact_business(exact_business_query)
            return GraphRAGRouteDecision(
                route="exact_graph_query",
                reason=(
                    f"deterministic business query family: {exact_business_query.family}"
                    + self._module_reason_suffix(module_plan)
                ),
                retrieval_mode="fixed_cypher_template",
            )

        cohort_query = self._parse_item_cohort_expansion_query(query)
        if cohort_query:
            module_plan = self.module_planner.validate_plan(
                self.module_planner.build_item_cohort_plan(cohort_query)
            )
            return GraphRAGRouteDecision(
                route="subgraph_reasoning_query",
                reason="cohort follow-up intent over shared requesters and items" + self._module_reason_suffix(module_plan),
                retrieval_mode="bounded_subgraph",
            )

        requester_query = self._parse_item_requester_query(query)
        if requester_query:
            module_plan = self.module_planner.validate_plan(
                self.module_planner.build_item_requester_plan(requester_query)
            )
            return GraphRAGRouteDecision(
                route="subgraph_reasoning_query",
                reason="item-to-requester relationship query" + self._module_reason_suffix(module_plan),
                retrieval_mode="bounded_subgraph",
            )

        requester_set_query = self._parse_requester_item_set_query(query)
        if requester_set_query:
            module_plan = self.module_planner.validate_plan(
                self.module_planner.build_requester_set_plan(requester_set_query)
            )
            return GraphRAGRouteDecision(
                route="subgraph_reasoning_query",
                reason="multi-requester set comparison over requested items" + self._module_reason_suffix(module_plan),
                retrieval_mode="bounded_subgraph",
            )

        heuristic_plan = self._heuristic_ticket_plan(query)
        if self._should_use_structured_ticket_query(query, heuristic_plan):
            breakdown = self._normalize_ticket_query_breakdown(
                query,
                heuristic_plan,
                self._heuristic_ticket_query_breakdown(query, heuristic_plan),
            )
            output_plan = self._heuristic_ticket_output_plan(query, breakdown)
            module_plan: Optional[GraphModuleQueryPlan] = None
            if breakdown.metrics and output_plan.sections:
                module_plan = self.module_planner.validate_plan(
                    self.module_planner.build_structured_ticket_plan(
                        breakdown=breakdown,
                        output_plan=output_plan,
                    )
                )
            return GraphRAGRouteDecision(
                route="exact_graph_query",
                reason="structured ticket count or list query" + self._module_reason_suffix(module_plan),
                retrieval_mode="fixed_cypher_template",
            )

        return GraphRAGRouteDecision(
            route="exploratory_hybrid_query",
            reason="generic graph retrieval with semantic assistance",
            retrieval_mode="graph_plus_semantic",
        )

    def _heuristic_ticket_plan(self, query: str) -> GraphRAGRetrievalPlan:
        plan = self._heuristic_plan(query, GraphRAGEntities())
        if plan.domain != "ticket":
            plan = self._normalize_plan(
                GraphRAGRetrievalPlan(
                    domain="ticket",
                    intent_family=plan.intent_family,
                    entity_scope="ticket_wide",
                    answer_style=plan.answer_style,
                    named_entities=plan.named_entities,
                    category_terms=plan.category_terms,
                    catalog_terms=plan.catalog_terms,
                    table_terms=plan.table_terms,
                    field_terms=plan.field_terms,
                    concept_terms=plan.concept_terms,
                    sort_field=plan.sort_field,
                    sort_direction=plan.sort_direction,
                    aggregation=plan.aggregation,
                    limit=plan.limit,
                )
            )
        return plan

    async def _execute_exact_graph_route(self, query: str) -> Dict[str, Any]:
        heuristic_plan = self._heuristic_ticket_plan(query)
        exact_business_query = self._parse_exact_business_query(query)

        if exact_business_query and exact_business_query.family in {"grouped_requester_summary", "threshold_requester_aggregate"}:
            exact_business_execution = await self._execute_exact_business_query(query)
            if exact_business_execution.get("handled"):
                module_plan = self._build_module_plan_for_exact_business(exact_business_query)
                return {
                    "handled": True,
                    "answer": exact_business_execution.get("answer"),
                    "plan": self._compose_route_plan_payload(
                        retrieval_plan=self._exact_business_plan_payload(heuristic_plan, exact_business_query),
                        module_plan=module_plan,
                    ),
                    "cypher": exact_business_execution.get("cypher", {}),
                    "entities": exact_business_execution.get("entities", {}),
                    "context": exact_business_execution.get("context", []),
                    "candidate_count": exact_business_execution.get("candidate_count", 0),
                    "time_window": self._time_window_payload(exact_business_query.time_window),
                }

        structured_execution = await self._execute_structured_ticket_query(
            query,
            heuristic_plan,
            use_llm_breakdown=False,
        )
        if structured_execution.get("handled"):
            return {
                "handled": True,
                "answer": structured_execution.get("answer"),
                "plan": self._compose_route_plan_payload(
                    retrieval_plan=heuristic_plan.model_dump(),
                    module_plan=structured_execution.get("module_plan"),
                ),
                "cypher": structured_execution.get("cypher", {}),
                "entities": structured_execution.get("cypher", {}).get("resolved_entities", {}),
                "context": structured_execution.get("context", []),
                "candidate_count": structured_execution.get("candidate_count", 0),
                "time_window": self._time_window_payload(self._parse_time_window(query.lower())),
            }

        if exact_business_query:
            exact_business_execution = await self._execute_exact_business_query(query)
            if exact_business_execution.get("handled"):
                module_plan = self._build_module_plan_for_exact_business(exact_business_query)
                return {
                    "handled": True,
                    "answer": exact_business_execution.get("answer"),
                    "plan": self._compose_route_plan_payload(
                        retrieval_plan=self._exact_business_plan_payload(heuristic_plan, exact_business_query),
                        module_plan=module_plan,
                    ),
                    "cypher": exact_business_execution.get("cypher", {}),
                    "entities": exact_business_execution.get("entities", {}),
                    "context": exact_business_execution.get("context", []),
                    "candidate_count": exact_business_execution.get("candidate_count", 0),
                    "time_window": self._time_window_payload(exact_business_query.time_window),
                }

        return {"handled": False}

    async def _execute_subgraph_reasoning_route(self, query: str) -> Dict[str, Any]:
        plan = self._heuristic_ticket_plan(query)
        executor_specs = (
            (
                self._execute_item_cohort_expansion_query,
                "list",
                self._parse_item_cohort_expansion_query,
                lambda parsed: self.module_planner.validate_plan(self.module_planner.build_item_cohort_plan(parsed)),
            ),
            (
                self._execute_item_requester_query,
                "list",
                self._parse_item_requester_query,
                lambda parsed: self.module_planner.validate_plan(self.module_planner.build_item_requester_plan(parsed)),
            ),
            (
                self._execute_requester_item_set_query,
                "filter",
                self._parse_requester_item_set_query,
                lambda parsed: self.module_planner.validate_plan(self.module_planner.build_requester_set_plan(parsed)),
            ),
        )
        for executor, intent_family, parser, module_builder in executor_specs:
            parsed_query = parser(query)
            if not parsed_query:
                continue
            execution = await executor(query)
            if execution.get("handled"):
                route_plan = {
                    "domain": "ticket",
                    "intent_family": intent_family,
                    "entity_scope": "ticket_wide",
                    "answer_style": "enumerated_list",
                    **plan.model_dump(),
                }
                route_plan["intent_family"] = intent_family
                route_plan["entity_scope"] = "ticket_wide"
                route_plan["answer_style"] = "enumerated_list"
                module_plan = module_builder(parsed_query)
                return {
                    "handled": True,
                    "answer": execution.get("answer"),
                    "plan": self._compose_route_plan_payload(
                        retrieval_plan=route_plan,
                        module_plan=module_plan,
                    ),
                    "cypher": execution.get("cypher", {}),
                    "entities": execution.get("entities", {}),
                    "context": execution.get("context", []),
                    "candidate_count": execution.get("candidate_count", 0),
                    "time_window": self._time_window_payload(self._parse_time_window(query.lower())),
                }
        return {"handled": False}

    def _compose_route_plan_payload(
        self,
        *,
        retrieval_plan: Optional[Dict[str, Any]] = None,
        module_plan: Optional[GraphModuleQueryPlan | Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if retrieval_plan:
            payload["retrieval_plan"] = retrieval_plan
        validated = self._module_plan_payload(module_plan)
        if validated:
            payload["module_adapter_plan"] = validated
        return payload or {}

    def _module_plan_payload(
        self,
        module_plan: Optional[GraphModuleQueryPlan | Dict[str, Any]],
    ) -> Dict[str, Any]:
        if module_plan is None:
            return {}
        try:
            if isinstance(module_plan, GraphModuleQueryPlan):
                validated = self.module_planner.validate_plan(module_plan)
            else:
                validated = self.module_planner.validate_plan(GraphModuleQueryPlan(**module_plan))
            return validated.model_dump()
        except Exception:
            logger.exception("[GRAPHRAG] Failed to validate module adapter plan payload")
            return {}

    def _module_reason_suffix(self, module_plan: Optional[GraphModuleQueryPlan | Dict[str, Any]]) -> str:
        payload = self._module_plan_payload(module_plan)
        module_path = payload.get("module_path", []) if isinstance(payload, dict) else []
        if not module_path:
            return ""
        return f" via modules: {' -> '.join(str(module) for module in module_path)}"

    def _build_module_plan_for_exact_business(
        self,
        exact_business_query: GraphRAGExactBusinessQuery,
    ) -> GraphModuleQueryPlan:
        return self.module_planner.validate_plan(
            self.module_planner.build_exact_business_plan(
                exact_business_query=exact_business_query,
                time_window=self._time_window_payload(exact_business_query.time_window),
            )
        )

    async def _execute_exploratory_hybrid_route(
        self,
        user_query: str,
        user_context: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        plan = await self._build_retrieval_plan(user_query)
        logger.debug("[GRAPHRAG:%s] Retrieval plan: %s", request_id, plan.model_dump())
        plan = await self._expand_plan_with_semantics(user_query, plan)

        compiled_program = await self._compile_graph_query_program(user_query, plan)
        compiled_execution = await self._execute_compiled_graph_query(user_query, plan, compiled_program)
        if compiled_execution.get("rows"):
            answer = await self._generate_compiled_graph_answer(
                user_query,
                plan,
                compiled_program,
                compiled_execution,
            )
            return self._package_route_result(
                user_query=user_query,
                route_decision=GraphRAGRouteDecision(
                    route="exploratory_hybrid_query",
                    reason="compiled graph program over exploratory plan",
                    retrieval_mode="graph_plus_semantic",
                ),
                plan=plan.model_dump(),
                cypher={
                    "mode": "compiled_query",
                    "program": compiled_program.model_dump(),
                    "row_count": len(compiled_execution.get("rows", [])),
                },
                entities=self._entities_payload(plan),
                context=[{"type": "graph_row", "row": row} for row in compiled_execution.get("rows", [])],
                candidate_count=len(compiled_execution.get("rows", [])),
                answer=answer,
                time_window=self._time_window_payload(self._parse_time_window(user_query.lower())),
            )

        cypher_blueprint = await self._build_cypher_blueprint(user_query, plan)
        plan = self._merge_plan_with_cypher_blueprint(plan, cypher_blueprint)
        cypher_context = self._build_cypher_context(user_query, plan, cypher_blueprint)
        execution = await self._execute_plan(user_query, plan, user_context or {})

        if not execution.get("context"):
            answer = execution.get("answer") or NO_CONTEXT_ANSWER
            return self._package_route_result(
                user_query=user_query,
                route_decision=GraphRAGRouteDecision(
                    route="exploratory_hybrid_query",
                    reason="plan execution returned no grounded context",
                    retrieval_mode="graph_plus_semantic",
                ),
                plan=plan.model_dump(),
                cypher=cypher_context,
                entities=self._entities_payload(plan),
                context=[],
                candidate_count=execution.get("candidate_count", 0),
                answer=answer,
                time_window=self._time_window_payload(self._parse_time_window(user_query.lower())),
            )

        answer = await self._generate_answer(user_query, plan, execution)
        return self._package_route_result(
            user_query=user_query,
            route_decision=GraphRAGRouteDecision(
                route="exploratory_hybrid_query",
                reason="plan execution with graph and semantic context",
                retrieval_mode="graph_plus_semantic",
            ),
            plan=plan.model_dump(),
            cypher=cypher_context,
            entities=self._entities_payload(plan),
            context=execution.get("context", []),
            candidate_count=execution.get("candidate_count", 0),
            answer=answer,
            time_window=self._time_window_payload(self._parse_time_window(user_query.lower())),
        )

    def _exact_business_plan_payload(
        self,
        plan: GraphRAGRetrievalPlan,
        exact_business_query: GraphRAGExactBusinessQuery,
    ) -> Dict[str, Any]:
        payload = plan.model_dump()
        payload.update(
            {
                "domain": "ticket",
                "intent_family": exact_business_query.family,
                "entity_scope": "ticket_wide",
                "answer_style": "enumerated_list",
            }
        )
        return payload

    def _time_window_payload(self, time_window: Optional[GraphRAGTimeWindow]) -> Dict[str, str]:
        if not time_window:
            return {}
        return {
            "label": time_window.label,
            "start": time_window.start,
            "end": time_window.end,
        }

    def _package_route_result(
        self,
        *,
        user_query: str,
        route_decision: GraphRAGRouteDecision,
        plan: Optional[Dict[str, Any]],
        cypher: Dict[str, Any],
        entities: Dict[str, Any],
        context: List[Dict[str, Any]],
        candidate_count: int,
        answer: str,
        time_window: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        normalized_result = {
            "route": route_decision.route,
            "route_reason": route_decision.reason,
            "resolved_entities": entities,
            "time_window": time_window or {},
            "retrieval_mode": route_decision.retrieval_mode,
            "query": user_query,
            "cypher_or_traversal": cypher,
            "evidence": context,
            "answer": answer,
        }
        return {
            "answer": answer,
            "plan": plan or {},
            "cypher": cypher,
            "entities": entities,
            "candidate_count": candidate_count,
            "context": context,
            "context_count": len(context),
            "route": route_decision.route,
            "normalized_result": normalized_result,
        }

    async def _execute_readonly_query_with_timeout(
        self,
        cypher_query: str,
        parameters: Dict[str, Any],
        limit_cap: int,
        stage: str,
    ) -> Dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    neo4j_query_runtime_service.execute_readonly_query,
                    cypher_query,
                    parameters,
                    limit_cap,
                    GRAPH_RAG_NEO4J_TIMEOUT_SECONDS,
                ),
                timeout=GRAPH_RAG_NEO4J_TIMEOUT_SECONDS + 1.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[GRAPHRAG] Neo4j stage timed out: %s", stage)
            return {"ok": False, "rows": [], "error": f"{stage} timed out"}

    async def _execute_exact_business_query(self, query: str) -> Dict[str, Any]:
        business_query = self._parse_exact_business_query(query)
        if not business_query or not neo4j_query_runtime_service.is_available():
            return {"handled": False}

        requester_candidates: List[Dict[str, Any]] = []
        requester_sys_ids: List[str] = []
        if business_query.requester_terms:
            for phrase in business_query.requester_terms:
                try:
                    candidates = self.resolve_ticket_requester(phrase, limit=8)
                except Exception:
                    logger.exception("[GRAPHRAG] Exact business requester resolver failed for phrase: %s", phrase)
                    candidates = []
                if not candidates:
                    try:
                        candidates = self._fuzzy_requester_candidates(phrase, limit=8)
                    except Exception:
                        logger.exception("[GRAPHRAG] Exact business fuzzy requester resolver failed for phrase: %s", phrase)
                        candidates = []
                requester_candidates.extend(candidates)
            requester_candidates = self._merge_resolver_candidates(requester_candidates)
            selected = self._select_single_resolver_candidate(requester_candidates)
            if selected.get("status") == "ambiguous":
                return {
                    "handled": True,
                    "answer": self._render_ambiguous_resolver_answer(
                        "requester",
                        ", ".join(business_query.requester_terms),
                        selected.get("candidates", []),
                    ),
                    "candidate_count": 0,
                    "context": [],
                    "entities": {"requesters": requester_candidates},
                    "cypher": {"mode": "exact_business_query", "status": "ambiguous_requester"},
                    "family": business_query.family,
                }
            requester_sys_ids = self._confident_candidate_values(requester_candidates, "sys_id")

        parameters = self._exact_business_parameters(
            business_query,
            requester_candidates=requester_candidates,
            requester_sys_ids=requester_sys_ids,
        )
        cypher_query = self._exact_business_cypher(business_query.family, business_query.measure)
        if not cypher_query:
            return {"handled": False}

        validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
        if validation_error:
            logger.warning("[GRAPHRAG] Exact business query rejected: %s", validation_error)
            return {"handled": False}

        execution = await self._execute_readonly_query_with_timeout(
            cypher_query,
            parameters,
            100,
            f"exact_business:{business_query.family}",
        )
        if execution.get("error"):
            logger.warning("[GRAPHRAG] Exact business query failed: %s", execution["error"])
            return {"handled": False}

        rows = execution.get("rows", [])
        return {
            "handled": True,
            "answer": self._render_exact_business_answer(business_query, rows),
            "candidate_count": len(rows),
            "context": [{"type": "graph_row", "row": row} for row in rows],
            "entities": {"requesters": requester_candidates},
            "cypher": {
                "mode": "exact_business_query",
                "family": business_query.family,
                "measure": business_query.measure,
                "parameters": parameters,
                "row_count": len(rows),
            },
            "family": business_query.family,
        }

    def _parse_exact_business_query(self, query: str) -> Optional[GraphRAGExactBusinessQuery]:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        if not query_lower:
            return None
        if any(token in query_lower for token in ("field", "fields", "variable", "variables", "option", "choice", "schema", "table", "column")):
            return None

        has_activity = any(
            token in query_lower
            for token in ("request", "requests", "ticket", "tickets", "order", "orders", "ordered", "bought", "buy", "purchased", "purchase", "got", "products", "items", "devices")
        )
        if not has_activity:
            return None

        if re.search(r"\b(?:which|what)\s+(?:users?|people|requesters?|employees)\b", query_lower) and not self._extract_threshold(query_lower):
            return None

        time_window = self._parse_time_window(query_lower)
        ticket_type = self._resolve_ticket_type_from_text(query_lower)
        requester_terms = self._extract_business_requester_terms(query_lower)
        state_terms = self._extract_business_state_terms(query_lower)
        filter_terms = self._extract_business_filter_terms(query_lower, requester_terms)

        threshold = self._extract_threshold(query_lower)
        if threshold and re.search(r"\b(?:users?|people|requesters?|employees)\b", query_lower):
            return GraphRAGExactBusinessQuery(
                family="threshold_requester_aggregate",
                measure="ticket_count",
                item_terms=filter_terms["item_terms"],
                category_terms=filter_terms["category_terms"],
                ticket_type=ticket_type,
                time_window=time_window,
                threshold=threshold,
                limit=50,
            )

        if self._is_grouped_requester_query(query_lower):
            segments = self._extract_grouped_summary_segments(query_lower)
            return GraphRAGExactBusinessQuery(
                family="grouped_requester_summary",
                measure="ticket_count",
                category_terms=segments,
                ticket_type=ticket_type,
                time_window=time_window,
                limit=25,
            )

        wants_list = bool(
            re.search(r"\b(?:what|which|show|list|give me)\b", query_lower)
            and re.search(r"\b(?:orders?|requests?|tickets?)\b", query_lower)
            and not re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", query_lower)
        )
        wants_count = bool(re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", query_lower))
        wants_distinct_products = bool(
            re.search(r"\b(?:products?|catalog items?|items?|devices?)\b", query_lower)
            and not re.search(r"\b(?:request numbers?|ticket numbers?|numbers for)\b", query_lower)
        )

        if wants_list:
            return GraphRAGExactBusinessQuery(
                family="requester_request_list",
                measure="ticket_list",
                requester_terms=requester_terms,
                item_terms=filter_terms["item_terms"],
                category_terms=filter_terms["category_terms"],
                state_terms=state_terms,
                ticket_type=ticket_type,
                time_window=time_window,
                limit=50 if time_window else 25,
            )

        if wants_count:
            measure = "distinct_catalog_item_count" if wants_distinct_products else "ticket_count"
            return GraphRAGExactBusinessQuery(
                family="requester_measure",
                measure=measure,
                requester_terms=requester_terms,
                item_terms=filter_terms["item_terms"],
                category_terms=filter_terms["category_terms"],
                state_terms=state_terms,
                ticket_type=ticket_type,
                time_window=time_window,
                limit=1,
            )

        return None

    def _exact_business_parameters(
        self,
        business_query: GraphRAGExactBusinessQuery,
        requester_candidates: List[Dict[str, Any]],
        requester_sys_ids: List[str],
    ) -> Dict[str, Any]:
        time_window = business_query.time_window or GraphRAGTimeWindow()
        category_terms = self._dedupe_terms(
            business_query.category_terms + self._expand_alias_terms(business_query.category_terms)
        )
        item_terms = self._dedupe_terms(
            business_query.item_terms + self._expand_alias_terms(business_query.item_terms)
        )
        return {
            "ticket_type": business_query.ticket_type,
            "requester_terms": self._dedupe_terms(business_query.requester_terms),
            "requester_sys_ids": requester_sys_ids,
            "item_terms": item_terms,
            "category_terms": category_terms,
            "state_terms": self._dedupe_terms(business_query.state_terms),
            "opened_start": time_window.start,
            "opened_end": time_window.end,
            "threshold": business_query.threshold,
            "segment_one_terms": category_terms[:1],
            "segment_two_terms": category_terms[1:2],
            "limit": business_query.limit,
        }

    def _exact_business_cypher(self, family: str, measure: str) -> str:
        if family == "requester_measure" and measure == "distinct_catalog_item_count":
            return self._exact_distinct_catalog_count_cypher()
        if family == "requester_measure":
            return self._exact_ticket_count_cypher()
        if family == "requester_request_list":
            return self._exact_ticket_list_cypher()
        if family == "grouped_requester_summary":
            return self._exact_grouped_requester_summary_cypher()
        if family == "threshold_requester_aggregate":
            return self._exact_threshold_requester_cypher()
        return ""

    def _opened_at_value_expr(self, node_name: str = "ticket") -> str:
        return f"toString(coalesce({node_name}.opened_at, ''))"

    def _exact_match_where_clause(self) -> str:
        return """
WHERE ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
  AND ($opened_start = '' OR toString(coalesce(ticket.opened_at, '')) >= $opened_start)
  AND ($opened_end = '' OR toString(coalesce(ticket.opened_at, '')) < $opened_end)
  AND (
        (size($requester_sys_ids) = 0 AND size($requester_terms) = 0) OR
        (size($requester_sys_ids) > 0 AND (
            coalesce(requester.sys_id, '') IN $requester_sys_ids OR
            coalesce(ticket.requested_for_sys_id, '') IN $requester_sys_ids OR
            coalesce(ticket.requested_by_sys_id, '') IN $requester_sys_ids
        )) OR
        (size($requester_sys_ids) = 0 AND ANY(term IN $requester_terms WHERE
            toLower(coalesce(requester.name, '')) CONTAINS term OR
            toLower(coalesce(ticket.requested_for_name, '')) CONTAINS term OR
            toLower(coalesce(ticket.requested_by_name, '')) CONTAINS term
        ))
      )
  AND (size($state_terms) = 0 OR ANY(term IN $state_terms WHERE
        toLower(coalesce(ticket.state_display, '')) CONTAINS term OR
        toLower(coalesce(ticket.state, '')) CONTAINS term
      ))
  AND (
        size($item_terms) + size($category_terms) = 0 OR
        ANY(term IN ($item_terms + $category_terms) WHERE
            toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
            toLower(coalesce(item.short_description, '')) CONTAINS term OR
            toLower(coalesce(item.description, '')) CONTAINS term OR
            toLower(coalesce(catalog.name, '')) CONTAINS term OR
            toLower(coalesce(catalog.display_name, '')) CONTAINS term OR
            toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
            toLower(coalesce(catalog.description, '')) CONTAINS term OR
            toLower(coalesce(catalog.category, '')) CONTAINS term OR
            toLower(coalesce(catalog.category_name, '')) CONTAINS term OR
            toLower(coalesce(catalog.search_text, '')) CONTAINS term
        )
      )
""".strip()

    def _exact_ticket_count_cypher(self) -> str:
        return f"""
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
{self._exact_match_where_clause()}
RETURN count(DISTINCT ticket) AS count
""".strip()

    def _exact_distinct_catalog_count_cypher(self) -> str:
        return f"""
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
{self._exact_match_where_clause()}
WITH coalesce(catalog.sys_id, item.cat_item_sys_id, item.cat_item_name, '') AS product_key
WHERE product_key <> ''
RETURN count(DISTINCT product_key) AS count
""".strip()

    def _exact_ticket_list_cypher(self) -> str:
        return f"""
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
{self._exact_match_where_clause()}
RETURN DISTINCT ticket.number AS number,
       coalesce(catalog.name, item.cat_item_name, item.short_description, ticket.short_description, '') AS item_name,
       coalesce(ticket.short_description, item.short_description, '') AS description,
       coalesce(ticket.state_display, ticket.state, '') AS state,
       toString(coalesce(ticket.opened_at, '')) AS opened_at
ORDER BY opened_at DESC, number ASC
LIMIT $limit
""".strip()

    def _exact_grouped_requester_summary_cypher(self) -> str:
        return """
MATCH (requester:User)-[:REQUESTED]->(ticket:Ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
WITH requester, ticket, item, catalog,
     toLower(coalesce(item.cat_item_name, '') + ' ' +
             coalesce(item.short_description, '') + ' ' +
             coalesce(item.description, '') + ' ' +
             coalesce(catalog.name, '') + ' ' +
             coalesce(catalog.display_name, '') + ' ' +
             coalesce(catalog.short_description, '') + ' ' +
             coalesce(catalog.description, '') + ' ' +
             coalesce(catalog.category, '') + ' ' +
             coalesce(catalog.category_name, '') + ' ' +
             coalesce(catalog.search_text, '')) AS item_text
WHERE ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
  AND ($opened_start = '' OR toString(coalesce(ticket.opened_at, '')) >= $opened_start)
  AND ($opened_end = '' OR toString(coalesce(ticket.opened_at, '')) < $opened_end)
  AND coalesce(requester.name, '') <> ''
RETURN requester.name AS requester,
       count(DISTINCT ticket) AS total_requests,
       count(DISTINCT CASE WHEN size($segment_one_terms) > 0 AND ANY(term IN $segment_one_terms WHERE item_text CONTAINS term) THEN ticket END) AS segment_one_count,
       count(DISTINCT CASE WHEN size($segment_two_terms) > 0 AND ANY(term IN $segment_two_terms WHERE item_text CONTAINS term) THEN ticket END) AS segment_two_count
ORDER BY total_requests DESC, requester ASC
LIMIT $limit
""".strip()

    def _exact_threshold_requester_cypher(self) -> str:
        return f"""
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
{self._exact_match_where_clause()}
WITH coalesce(requester.name, ticket.requested_for_name, ticket.requested_by_name, '') AS requester,
     count(DISTINCT ticket) AS matching_count
WHERE requester <> '' AND matching_count > $threshold
RETURN requester, matching_count
ORDER BY matching_count DESC, requester ASC
LIMIT $limit
""".strip()

    def _render_exact_business_answer(self, business_query: GraphRAGExactBusinessQuery, rows: List[Dict[str, Any]]) -> str:
        if business_query.family == "requester_measure":
            count = int((rows[0] if rows else {}).get("count") or 0)
            requester = self._display_query_subject(business_query.requester_terms)
            if business_query.measure == "distinct_catalog_item_count":
                label = f"Distinct products ordered by {requester}" if requester else "Distinct products ordered"
            else:
                label = f"Total requests for {requester}" if requester else "Total requests"
            if business_query.time_window and business_query.time_window.label:
                label = f"{label} {business_query.time_window.label}"
            return f"{label}: {count}"

        if business_query.family == "requester_request_list":
            subject = self._display_query_subject(business_query.requester_terms)
            label = f"Orders for {subject}" if subject else "Matching orders"
            if business_query.time_window and business_query.time_window.label:
                label = f"{label} {business_query.time_window.label}"
            if not rows:
                return f"{label}: no matching graph records found."
            lines = [f"{label}:"]
            for row in rows:
                number = row.get("number") or "Unknown"
                item_name = re.sub(r"\s+", " ", str(row.get("item_name") or row.get("description") or "No item name")).strip()
                opened_at = re.sub(r"\s+", " ", str(row.get("opened_at") or "")).strip()
                suffix = f" ({opened_at})" if opened_at else ""
                lines.append(f"- {number}: {item_name}{suffix}")
            return "\n".join(lines)

        if business_query.family == "grouped_requester_summary":
            if not rows:
                return NO_CONTEXT_ANSWER
            segment_labels = business_query.category_terms[:2]
            lines = ["Requester request summary:"]
            for row in rows:
                lines.append(
                    f"- {row.get('requester')}: total={int(row.get('total_requests') or 0)}, "
                    f"{segment_labels[0] if segment_labels else 'segment 1'}={int(row.get('segment_one_count') or 0)}, "
                    f"{segment_labels[1] if len(segment_labels) > 1 else 'segment 2'}={int(row.get('segment_two_count') or 0)}"
                )
            return "\n".join(lines)

        if business_query.family == "threshold_requester_aggregate":
            if not rows:
                return "No requesters matched that threshold in the graph."
            label = " ".join(business_query.category_terms or business_query.item_terms or ["matching"])
            lines = [f"Requesters with more than {business_query.threshold} {label} requests:"]
            for row in rows:
                lines.append(f"- {row.get('requester')}: {int(row.get('matching_count') or 0)}")
            return "\n".join(lines)

        return NO_CONTEXT_ANSWER

    def _display_query_subject(self, terms: List[str]) -> str:
        if not terms:
            return ""
        return " ".join(part.capitalize() for part in terms[0].split())

    def _resolve_ticket_type_from_text(self, query_lower: str) -> Optional[str]:
        if "change request" in query_lower or "change requests" in query_lower:
            return "CH"
        if any(token in query_lower for token in ("request", "requests", "order", "orders", "ordered", "bought", "purchased", "products", "items", "devices")):
            return "SR"
        return None

    def _extract_business_requester_terms(self, query_lower: str) -> List[str]:
        patterns = (
            r"\b(?:orders?|requests?|tickets?|products?|items?|devices?)\s+did\s+(.+?)\s+(?:make|order|buy|purchase|request|get|got)\b",
            r"\b(?:orders?|requests?|tickets?|products?|items?|devices?)\s+(?:has|have)\s+(.+?)\s+(?:made|ordered|bought|purchased|requested|got)\b",
            r"\bdid\s+(.+?)\s+(?:make|order|buy|purchase|request|get)\b",
            r"\b(?:for|by|from)\s+([a-z][a-z\s.'-]{1,100}?)(?=\s+(?:that|which|where|when|during|last|this|today|yesterday|are|is|were|was)\b|[?.!,]|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if not match:
                continue
            candidate = re.sub(r"\s+", " ", match.group(1)).strip(" ?!.,")
            candidate = re.sub(r"\b(?:the|user|requester|requestor)\b", " ", candidate)
            candidate = re.sub(r"\s+", " ", candidate).strip()
            if candidate and not self._looks_like_ticket_filter_tail(candidate):
                return self._dedupe_terms([candidate])
            split = self._split_requester_and_filter_tail(candidate)
            if split.get("requester"):
                return self._dedupe_terms([split["requester"]])
        return self._extract_requester_terms(query_lower)

    def _extract_business_state_terms(self, query_lower: str) -> List[str]:
        states = []
        for token in self._extract_ordered_terms(query_lower):
            if token in TICKET_STATE_TERMS:
                states.append(self._canonical_ticket_state(token))
        if "active" in query_lower:
            states.append("active")
        return self._dedupe_terms(states)

    def _extract_business_filter_terms(self, query_lower: str, requester_terms: List[str]) -> Dict[str, List[str]]:
        generic = self._business_generic_terms()
        for requester in requester_terms:
            generic.update(self._extract_ordered_terms(requester))
        generic.update(self._time_window_generic_terms(query_lower))

        tokens = [
            self._singularize_term(token)
            for token in self._extract_ordered_terms(query_lower)
            if token not in generic
        ]
        tokens = [token for token in tokens if token not in generic and len(token) >= 2]
        if "mobile" in tokens and "device" in tokens:
            tokens = [token for token in tokens if token != "device"]

        category_terms = [
            token
            for token in tokens
            if token in BROAD_TICKET_CATEGORY_TERMS or token in {"device"}
        ]
        item_tokens = [
            token
            for token in tokens
            if token not in category_terms and (token in TICKET_ITEM_HINT_TERMS or not token.isdigit())
        ]
        item_phrase = " ".join(item_tokens).strip()
        item_terms = [item_phrase] if len(item_tokens) > 1 else item_tokens
        return {
            "item_terms": self._dedupe_terms(item_terms),
            "category_terms": self._dedupe_terms(category_terms),
        }

    def _time_window_generic_terms(self, query_lower: str) -> set[str]:
        time_window = self._parse_time_window(query_lower)
        if not time_window or not time_window.label:
            return set()
        return set(self._extract_ordered_terms(time_window.label))

    def _business_generic_terms(self) -> set[str]:
        return {
            "how", "many", "what", "which", "show", "list", "give", "me", "the",
            "request", "requests", "ticket", "tickets", "order", "orders", "ordered",
            "buy", "bought", "purchase", "purchased", "requesting", "requested", "got",
            "make", "made", "did", "has", "have", "from", "for", "by", "that", "are",
            "is", "was", "were", "with", "and", "or", "of", "to", "in", "on", "during",
            "product", "products", "item", "items", "catalog", "count", "number",
            "numbers", "users", "user", "requesters", "requester", "people", "employees",
            "more", "than", "one", "two", "three", "at", "least", "each", "per",
        }

    def _is_grouped_requester_query(self, query_lower: str) -> bool:
        return bool(re.search(r"\b(?:for each|each|per)\s+(?:requester|requestor|user|employee)\b", query_lower))

    def _extract_grouped_summary_segments(self, query_lower: str) -> List[str]:
        candidates = []
        for token in self._extract_ordered_terms(query_lower):
            singular = self._singularize_term(token)
            if singular in BROAD_TICKET_CATEGORY_TERMS and singular not in candidates:
                candidates.append(singular)
        return candidates[:2]

    def _extract_threshold(self, query_lower: str) -> int:
        if "more than one" in query_lower:
            return 1
        if "at least two" in query_lower:
            return 1
        match = re.search(r"\bmore\s+than\s+(\d+)\b", query_lower)
        if match:
            return int(match.group(1))
        match = re.search(r"\bat\s+least\s+(\d+)\b", query_lower)
        if match:
            return max(0, int(match.group(1)) - 1)
        return 0

    def _parse_time_window(self, query_lower: str) -> Optional[GraphRAGTimeWindow]:
        now = datetime.now()
        today = datetime(now.year, now.month, now.day)

        if "today" in query_lower:
            return GraphRAGTimeWindow("today", self._format_window_start(today), self._format_window_start(today + timedelta(days=1)))
        if "yesterday" in query_lower:
            start = today - timedelta(days=1)
            return GraphRAGTimeWindow("yesterday", self._format_window_start(start), self._format_window_start(today))
        if "this week" in query_lower:
            start = today - timedelta(days=today.weekday())
            return GraphRAGTimeWindow("this week", self._format_window_start(start), self._format_window_start(start + timedelta(days=7)))
        if "last week" in query_lower:
            this_week_start = today - timedelta(days=today.weekday())
            start = this_week_start - timedelta(days=7)
            return GraphRAGTimeWindow("last week", self._format_window_start(start), self._format_window_start(this_week_start))
        if "this month" in query_lower:
            start = datetime(today.year, today.month, 1)
            end = datetime(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
            return GraphRAGTimeWindow("this month", self._format_window_start(start), self._format_window_start(end))
        if "last month" in query_lower:
            this_month_start = datetime(today.year, today.month, 1)
            previous_month_end = this_month_start
            previous_month_start = datetime(
                previous_month_end.year - (1 if previous_month_end.month == 1 else 0),
                12 if previous_month_end.month == 1 else previous_month_end.month - 1,
                1,
            )
            return GraphRAGTimeWindow("last month", self._format_window_start(previous_month_start), self._format_window_start(previous_month_end))
        return None

    def _format_window_start(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def _fuzzy_requester_candidates(self, phrase: str, limit: int = 8) -> List[Dict[str, Any]]:
        if not phrase or not neo4j_query_runtime_service.is_available():
            return []
        cypher_query = """
MATCH (ticket:Ticket)
OPTIONAL MATCH (user:User)-[:REQUESTED]->(ticket)
WITH DISTINCT
     coalesce(user.sys_id, ticket.requested_for_sys_id, ticket.requested_by_sys_id, '') AS sys_id,
     coalesce(user.name, ticket.requested_for_name, ticket.requested_by_name, '') AS name
WHERE name <> ''
RETURN sys_id AS sys_id,
       null AS request_item_sys_id,
       name AS name,
       null AS category,
       0 AS match_score,
       'fuzzy' AS match_type
LIMIT 500
""".strip()
        validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
        if validation_error:
            return []
        execution = neo4j_query_runtime_service.execute_readonly_query(
            cypher_query,
            {"limit": 500},
            500,
            GRAPH_RAG_NEO4J_TIMEOUT_SECONDS,
        )
        rows = execution.get("rows", []) if not execution.get("error") else []
        query_name = self._clean_ticket_metric_label(phrase)
        candidates: List[Dict[str, Any]] = []
        for row in rows:
            name = self._clean_ticket_metric_label(str(row.get("name") or ""))
            if not name:
                continue
            score = SequenceMatcher(None, query_name, name).ratio() * 100
            query_tokens = query_name.split()
            name_tokens = name.split()
            if query_tokens and name_tokens:
                token_score = max(
                    SequenceMatcher(None, query_token, name_token).ratio() * 100
                    for query_token in query_tokens
                    for name_token in name_tokens
                )
                score = max(score, token_score)
            if score >= 78:
                candidate = dict(row)
                candidate["match_score"] = score
                candidate["match_type"] = "fuzzy"
                candidates.append(candidate)
        return self._normalize_resolver_candidates(candidates, source="requester")[: max(1, limit)]

    async def _execute_item_cohort_expansion_query(self, query: str) -> Dict[str, Any]:
        cohort_query = self._parse_item_cohort_expansion_query(query)
        if not cohort_query:
            return {"handled": False}
        if not neo4j_query_runtime_service.is_available():
            return {"handled": False}

        item_candidates: List[Dict[str, Any]] = []
        for term in cohort_query.seed_item_terms:
            try:
                candidates = await asyncio.wait_for(
                    asyncio.to_thread(self.resolve_ticket_item, term),
                    timeout=GRAPH_RAG_RESOLVER_TIMEOUT_SECONDS,
                )
                item_candidates.extend(candidates)
            except asyncio.TimeoutError:
                logger.warning("[GRAPHRAG] Cohort item resolver timed out for term: %s", term)

        item_candidates = self._merge_resolver_candidates(item_candidates)
        selected_item = self._select_single_resolver_candidate(item_candidates)
        if selected_item.get("status") == "ambiguous":
            return {
                "handled": True,
                "answer": self._render_ambiguous_resolver_answer(
                    "catalog item",
                    cohort_query.seed_item_phrase,
                    selected_item.get("candidates", []),
                ),
                "candidate_count": 0,
                "context": [],
                "entities": {"items": selected_item.get("candidates", [])},
                "cypher": {"mode": "item_cohort_expansion_query", "error": "ambiguous_item"},
            }

        catalog_item_sys_ids = self._confident_candidate_values(item_candidates, "sys_id")
        request_item_sys_ids = self._confident_candidate_values(item_candidates, "request_item_sys_id")
        cypher_query = self._item_cohort_expansion_cypher()
        parameters = self._item_cohort_expansion_parameters(
            cohort_query,
            catalog_item_sys_ids,
            request_item_sys_ids,
        )
        validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
        if validation_error:
            logger.warning("[GRAPHRAG] Item cohort expansion query rejected: %s", validation_error)
            return {"handled": False}

        execution = await self._execute_readonly_query_with_timeout(
            cypher_query,
            parameters,
            100,
            "item_cohort_expansion_query",
        )
        if execution.get("error"):
            logger.warning("[GRAPHRAG] Item cohort expansion query failed: %s", execution["error"])
            return {"handled": False}

        rows = execution.get("rows", [])
        answer = self._render_item_cohort_expansion_answer(cohort_query, rows)
        return {
            "handled": True,
            "answer": answer,
            "candidate_count": len(rows),
            "context": [{"type": "graph_row", "row": row} for row in rows],
            "entities": {"items": item_candidates},
            "cypher": {
                "mode": "item_cohort_expansion_query",
                "item_cohort_expansion_query": cohort_query.model_dump(),
                "parameters": parameters,
                "row_count": len(rows),
            },
        }

    def _parse_item_cohort_expansion_query(self, query: str) -> Optional[GraphRAGItemCohortExpansionQuery]:
        if not self._should_use_item_cohort_expansion_query(query):
            return None

        seed_phrase = self._extract_item_cohort_seed_phrase(query)
        if not seed_phrase:
            return None

        inferred = self._infer_ticket_metric_filters(seed_phrase)
        seed_item_terms = inferred["catalog_item_terms"]
        seed_category_terms = inferred["category_terms"]
        if not seed_item_terms and not seed_category_terms:
            seed_item_terms = [seed_phrase]

        return GraphRAGItemCohortExpansionQuery(
            seed_item_phrase=seed_phrase,
            seed_item_terms=self._dedupe_terms(seed_item_terms),
            seed_category_terms=self._dedupe_terms(seed_category_terms),
            ticket_type="SR",
        )

    def _should_use_item_cohort_expansion_query(self, query: str) -> bool:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        asks_for_cohort = bool(
            re.search(
                r"\b(?:users?|people|requesters?|employees)\s+who\s+(?:bought|buy|purchased|purchase|requested|request|ordered|order|got|get)\b",
                query_lower,
            )
        )
        asks_followup_first = bool(
            re.search(
                r"\bwhat\s+(?:else|other(?:\s+items?)?)\s+did\s+(?:the\s+)?(?:users?|people|requesters?|employees)\s+who\b",
                query_lower,
            )
        )
        has_followup = any(
            marker in query_lower
            for marker in (
                "what else",
                "what other",
                "else did",
                "also bought",
                "also requested",
                "besides",
                "other than",
            )
        )
        return has_followup and (asks_for_cohort or asks_followup_first)

    def _extract_item_cohort_seed_phrase(self, query: str) -> str:
        query_lower = re.sub(r"\s+", " ", query.strip().lower()).strip(" ?!.,")
        patterns = (
            r"\b(?:the\s+)?(?:users?|people|requesters?|employees)\s+who\s+(?:bought|buy|purchased|purchase|requested|request|ordered|order|got|get)\s+(.+?)\s+(?:what\s+else|what\s+other|else\s+did|also\s+(?:bought|buy|requested|request|purchased|purchase|ordered|order)|besides|other\s+than)\b",
            r"\bwhat\s+(?:else|other(?:\s+items?)?)\s+did\s+(?:the\s+)?(?:users?|people|requesters?|employees)\s+who\s+(?:bought|buy|purchased|purchase|requested|request|ordered|order|got|get)\s+(.+?)\s+(?:buy|request|purchase|order|get)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if match:
                return self._clean_item_cohort_seed_phrase(match.group(1))
        return ""

    def _clean_item_cohort_seed_phrase(self, value: str) -> str:
        cleaned = self._strip_cohort_followup_tail(value)
        cleaned = re.sub(r"^(?:a|an|the)\s+", "", cleaned)
        cleaned = re.sub(r"\b(?:catalog\s+items?|items?|requests?|tickets?)\b", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _strip_cohort_followup_tail(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip().lower()).strip(" ?!.,")
        marker = re.search(
            r"\b(?:what\s+else|what\s+other|else\s+did|also\s+(?:bought|buy|requested|request|purchased|purchase|ordered|order)|besides|other\s+than)\b",
            cleaned,
        )
        if marker:
            cleaned = cleaned[: marker.start()]
        return cleaned.strip(" ?!.,")

    def _item_cohort_expansion_parameters(
        self,
        cohort_query: GraphRAGItemCohortExpansionQuery,
        catalog_item_sys_ids: List[str],
        request_item_sys_ids: List[str],
    ) -> Dict[str, Any]:
        return {
            "ticket_type": cohort_query.ticket_type,
            "catalog_item_sys_ids": catalog_item_sys_ids,
            "request_item_sys_ids": request_item_sys_ids,
            "item_terms": self._dedupe_terms(cohort_query.seed_item_terms),
            "category_terms": self._dedupe_terms(cohort_query.seed_category_terms),
            "limit": 100,
        }

    def _item_cohort_expansion_cypher(self) -> str:
        return """
MATCH (seed_ticket:Ticket)
OPTIONAL MATCH (seed_requester:User)-[:REQUESTED]->(seed_ticket)
OPTIONAL MATCH (seed_ticket)-[:HAS_ITEM]->(seed_item:RequestItem)
OPTIONAL MATCH (seed_item)-[:FULFILLS_CATALOG_ITEM]->(seed_catalog:CatalogItem)
WITH seed_ticket, seed_requester, seed_item, seed_catalog,
     coalesce(seed_requester.name, seed_ticket.requested_for_name, seed_ticket.requested_by_name, '') AS cohort_name,
     coalesce(seed_requester.sys_id, seed_ticket.requested_for_sys_id, seed_ticket.requested_by_sys_id, '') AS cohort_sys_id,
     coalesce(seed_catalog.sys_id, seed_item.cat_item_sys_id, seed_item.sys_id, '') AS seed_item_key
WHERE cohort_name <> ''
  AND seed_item_key <> ''
  AND ($ticket_type IS NULL OR seed_ticket.ticket_type = $ticket_type)
  AND (
        (size($catalog_item_sys_ids) = 0 AND size($item_terms) + size($category_terms) = 0) OR
        (size($catalog_item_sys_ids) > 0 AND (
            coalesce(seed_catalog.sys_id, '') IN $catalog_item_sys_ids OR
            coalesce(seed_item.sys_id, '') IN $request_item_sys_ids
        )) OR
        (size($catalog_item_sys_ids) = 0 AND ANY(term IN ($item_terms + $category_terms) WHERE
            toLower(coalesce(seed_ticket.short_description, '')) CONTAINS term OR
            toLower(coalesce(seed_ticket.description, '')) CONTAINS term OR
            toLower(coalesce(seed_item.short_description, '')) CONTAINS term OR
            toLower(coalesce(seed_item.description, '')) CONTAINS term OR
            toLower(coalesce(seed_item.cat_item_name, '')) CONTAINS term OR
            toLower(coalesce(seed_catalog.name, '')) CONTAINS term OR
            toLower(coalesce(seed_catalog.short_description, '')) CONTAINS term OR
            toLower(coalesce(seed_catalog.description, '')) CONTAINS term OR
            toLower(coalesce(seed_catalog.category, '')) CONTAINS term))
      )
WITH DISTINCT cohort_name, cohort_sys_id, collect(DISTINCT seed_item_key) AS seed_item_keys
MATCH (other_ticket:Ticket)
OPTIONAL MATCH (other_requester:User)-[:REQUESTED]->(other_ticket)
OPTIONAL MATCH (other_ticket)-[:HAS_ITEM]->(other_item:RequestItem)
OPTIONAL MATCH (other_item)-[:FULFILLS_CATALOG_ITEM]->(other_catalog:CatalogItem)
WITH cohort_name, cohort_sys_id, seed_item_keys, other_ticket, other_requester, other_item, other_catalog,
     coalesce(other_requester.name, other_ticket.requested_for_name, other_ticket.requested_by_name, '') AS other_requester_name,
     coalesce(other_requester.sys_id, other_ticket.requested_for_sys_id, other_ticket.requested_by_sys_id, '') AS other_requester_sys_id,
     coalesce(other_catalog.sys_id, other_item.cat_item_sys_id, other_item.sys_id, '') AS other_item_key,
     coalesce(other_catalog.name, other_item.cat_item_name, other_item.short_description, other_ticket.short_description, '') AS other_item_name,
     coalesce(other_catalog.category, '') AS other_item_category
WHERE other_item_key <> ''
  AND other_item_name <> ''
  AND ($ticket_type IS NULL OR other_ticket.ticket_type = $ticket_type)
  AND (
        (cohort_sys_id <> '' AND other_requester_sys_id = cohort_sys_id) OR
        (cohort_sys_id = '' AND toLower(other_requester_name) = toLower(cohort_name))
      )
  AND NOT other_item_key IN seed_item_keys
  AND NOT ANY(term IN $item_terms WHERE
        toLower(coalesce(other_ticket.short_description, '')) CONTAINS term OR
        toLower(coalesce(other_ticket.description, '')) CONTAINS term OR
        toLower(coalesce(other_item.short_description, '')) CONTAINS term OR
        toLower(coalesce(other_item.description, '')) CONTAINS term OR
        toLower(coalesce(other_item.cat_item_name, '')) CONTAINS term OR
        toLower(coalesce(other_catalog.name, '')) CONTAINS term)
WITH other_item_name, other_item_category, cohort_name, count(DISTINCT other_ticket) AS requester_request_count,
     collect(DISTINCT other_ticket.number) AS ticket_numbers
WITH other_item_name, other_item_category,
     collect(DISTINCT cohort_name) AS requesters,
     sum(requester_request_count) AS request_count,
     collect(ticket_numbers) AS ticket_number_groups
RETURN other_item_name AS name,
       other_item_category AS category,
       requesters AS requesters,
       request_count AS request_count,
       ticket_number_groups AS ticket_numbers
ORDER BY toLower(name) ASC
LIMIT $limit
""".strip()

    def _render_item_cohort_expansion_answer(
        self,
        cohort_query: GraphRAGItemCohortExpansionQuery,
        rows: List[Dict[str, Any]],
    ) -> str:
        seed_label = cohort_query.seed_item_phrase or "that item"
        if not rows:
            return f"No other requested items were found for users who requested {seed_label}."

        lines = [f"Other items requested by users who requested {seed_label}:"]
        seen = set()
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            requesters = [
                str(requester).strip()
                for requester in (row.get("requesters") or [])
                if str(requester or "").strip()
            ]
            requester_text = f" ({', '.join(requesters[:5])})" if requesters else ""
            lines.append(f"- {name}{requester_text}")
        return "\n".join(lines) if len(lines) > 1 else f"No other requested items were found for users who requested {seed_label}."

    async def _execute_item_requester_query(self, query: str) -> Dict[str, Any]:
        requester_query = self._parse_item_requester_query(query)
        if not requester_query:
            return {"handled": False}
        if not neo4j_query_runtime_service.is_available():
            return {"handled": False}

        item_candidates: List[Dict[str, Any]] = []
        for term in requester_query.item_terms:
            try:
                candidates = await asyncio.wait_for(
                    asyncio.to_thread(self.resolve_ticket_item, term),
                    timeout=GRAPH_RAG_RESOLVER_TIMEOUT_SECONDS,
                )
                item_candidates.extend(candidates)
            except asyncio.TimeoutError:
                logger.warning("[GRAPHRAG] Item requester resolver timed out for term: %s", term)

        item_candidates = self._merge_resolver_candidates(item_candidates)
        selected_item = self._select_single_resolver_candidate(item_candidates)
        if selected_item.get("status") == "ambiguous":
            return {
                "handled": True,
                "answer": self._render_ambiguous_resolver_answer(
                    "catalog item",
                    requester_query.item_phrase,
                    selected_item.get("candidates", []),
                ),
                "candidate_count": 0,
                "context": [],
                "entities": {"items": selected_item.get("candidates", [])},
                "cypher": {"mode": "item_requester_query", "error": "ambiguous_item"},
            }

        catalog_item_sys_ids = self._confident_candidate_values(item_candidates, "sys_id")
        request_item_sys_ids = self._confident_candidate_values(item_candidates, "request_item_sys_id")
        cypher_query = self._item_requester_cypher()
        parameters = self._item_requester_parameters(
            requester_query,
            catalog_item_sys_ids,
            request_item_sys_ids,
        )
        validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
        if validation_error:
            logger.warning("[GRAPHRAG] Item requester query rejected: %s", validation_error)
            return {"handled": False}

        execution = await self._execute_readonly_query_with_timeout(
            cypher_query,
            parameters,
            100,
            "item_requester_query",
        )
        if execution.get("error"):
            logger.warning("[GRAPHRAG] Item requester query failed: %s", execution["error"])
            return {"handled": False}

        rows = execution.get("rows", [])
        answer = self._render_item_requester_answer(requester_query, rows)
        return {
            "handled": True,
            "answer": answer,
            "candidate_count": len(rows),
            "context": [{"type": "graph_row", "row": row} for row in rows],
            "entities": {"items": item_candidates},
            "cypher": {
                "mode": "item_requester_query",
                "item_requester_query": requester_query.model_dump(),
                "parameters": parameters,
                "row_count": len(rows),
            },
        }

    def _parse_item_requester_query(self, query: str) -> Optional[GraphRAGItemRequesterQuery]:
        if not self._should_use_item_requester_query(query):
            return None

        item_phrase = self._extract_item_requester_phrase(query)
        if not item_phrase:
            return None

        inferred = self._infer_ticket_metric_filters(item_phrase)
        item_terms = inferred["catalog_item_terms"]
        category_terms = inferred["category_terms"]
        if not item_terms and not category_terms:
            item_terms = [item_phrase]

        return GraphRAGItemRequesterQuery(
            answer_intent="count" if self._item_requester_query_wants_count(query) else "list",
            item_phrase=item_phrase,
            item_terms=self._dedupe_terms(item_terms),
            category_terms=self._dedupe_terms(category_terms),
            ticket_type="SR",
        )

    def _should_use_item_requester_query(self, query: str) -> bool:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        asks_for_users = bool(
            re.search(r"\b(?:who|which\s+users?|what\s+users?|list\s+users?|show\s+users?|users?\s+who|people\s+who|requesters?\s+who)\b", query_lower)
        )
        asks_for_user_count = bool(re.search(r"\bhow\s+many\s+(?:users?|people|requesters?|employees)\b", query_lower))
        has_activity = any(
            term in query_lower
            for term in ("bought", "buy", "purchased", "purchase", "requested", "request", "ordered", "order", "got", "get")
        )
        return (asks_for_users or asks_for_user_count) and has_activity

    def _item_requester_query_wants_count(self, query: str) -> bool:
        return bool(re.search(r"\bhow\s+many\s+(?:users?|people|requesters?|employees)\b", query.lower()))

    def _extract_item_requester_phrase(self, query: str) -> str:
        query_lower = re.sub(r"\s+", " ", query.strip().lower()).strip(" ?!.,")
        patterns = (
            r"\b(?:which|what)\s+(?:users?|people|requesters?|employees)\s+(?:bought|buy|purchased|purchase|requested|request|ordered|order|got|get)\s+(.+)$",
            r"\bwho\s+(?:bought|buy|purchased|purchase|requested|request|ordered|order|got|get)\s+(.+)$",
            r"\b(?:users?|people|requesters?|employees)\s+who\s+(?:bought|buy|purchased|purchase|requested|request|ordered|order|got|get)\s+(.+)$",
            r"\b(?:show|list|give\s+me)\s+(?:the\s+)?(?:users?|people|requesters?|employees)\s+(?:who\s+)?(?:bought|buy|purchased|purchase|requested|request|ordered|order|got|get)\s+(.+)$",
            r"\bhow\s+many\s+(?:users?|people|requesters?|employees)\s+(?:bought|buy|purchased|purchase|requested|request|ordered|order|got|get)\s+(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if match:
                return self._clean_item_requester_phrase(match.group(1))
        return ""

    def _clean_item_requester_phrase(self, value: str) -> str:
        cleaned = self._strip_cohort_followup_tail(value)
        cleaned = re.sub(r"^(?:a|an|the)\s+", "", cleaned)
        cleaned = re.sub(r"\b(?:catalog\s+items?|items?|requests?|tickets?)\b", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _item_requester_parameters(
        self,
        requester_query: GraphRAGItemRequesterQuery,
        catalog_item_sys_ids: List[str],
        request_item_sys_ids: List[str],
    ) -> Dict[str, Any]:
        return {
            "ticket_type": requester_query.ticket_type,
            "catalog_item_sys_ids": catalog_item_sys_ids,
            "request_item_sys_ids": request_item_sys_ids,
            "item_terms": self._dedupe_terms(requester_query.item_terms),
            "category_terms": self._dedupe_terms(requester_query.category_terms),
            "limit": 100,
        }

    def _item_requester_cypher(self) -> str:
        return """
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
WITH ticket, requester, item, catalog,
     coalesce(requester.name, ticket.requested_for_name, ticket.requested_by_name, '') AS requester_name,
     coalesce(requester.sys_id, ticket.requested_for_sys_id, ticket.requested_by_sys_id, '') AS requester_sys_id
WHERE requester_name <> ''
  AND ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
  AND (
        (size($catalog_item_sys_ids) = 0 AND size($item_terms) + size($category_terms) = 0) OR
        (size($catalog_item_sys_ids) > 0 AND (
            coalesce(catalog.sys_id, '') IN $catalog_item_sys_ids OR
            coalesce(item.sys_id, '') IN $request_item_sys_ids
        )) OR
        (size($catalog_item_sys_ids) = 0 AND ANY(term IN ($item_terms + $category_terms) WHERE
            toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
            toLower(coalesce(ticket.description, '')) CONTAINS term OR
            toLower(coalesce(item.short_description, '')) CONTAINS term OR
            toLower(coalesce(item.description, '')) CONTAINS term OR
            toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
            toLower(coalesce(catalog.name, '')) CONTAINS term OR
            toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
            toLower(coalesce(catalog.description, '')) CONTAINS term OR
            toLower(coalesce(catalog.category, '')) CONTAINS term))
      )
RETURN requester_name AS name,
       requester_sys_id AS sys_id,
       count(DISTINCT ticket) AS request_count,
       collect(DISTINCT ticket.number) AS ticket_numbers
ORDER BY toLower(name) ASC
LIMIT $limit
""".strip()

    def _render_item_requester_answer(self, requester_query: GraphRAGItemRequesterQuery, rows: List[Dict[str, Any]]) -> str:
        item_label = requester_query.item_phrase or "that item"
        if requester_query.answer_intent == "count":
            return f"Users who requested {item_label}: {len(rows)}"
        if not rows:
            return f"No users were found in the graph for {item_label}."

        lines = [f"Users who requested {item_label}:"]
        seen = set()
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            lines.append(f"- {name}")
        return "\n".join(lines) if len(lines) > 1 else f"No users were found in the graph for {item_label}."

    async def _execute_requester_item_set_query(self, query: str) -> Dict[str, Any]:
        set_query = self._parse_requester_item_set_query(query)
        if not set_query:
            return {"handled": False}
        if not neo4j_query_runtime_service.is_available():
            return {"handled": False}

        requester_one = await self._resolve_requester_for_exact_query(set_query.requester_one)
        requester_two = await self._resolve_requester_for_exact_query(set_query.requester_two)
        for phrase, resolution in (
            (set_query.requester_one, requester_one),
            (set_query.requester_two, requester_two),
        ):
            if resolution.get("status") == "ambiguous":
                return {
                    "handled": True,
                    "answer": self._render_ambiguous_resolver_answer("requester", phrase, resolution.get("candidates", [])),
                    "candidate_count": 0,
                    "context": [],
                    "entities": {"requesters": {phrase: resolution.get("candidates", [])}},
                    "cypher": {"mode": "requester_item_set_query", "error": "ambiguous_requester"},
                }

        item_candidates: List[Dict[str, Any]] = []
        for term in set_query.item_terms:
            try:
                candidates = await asyncio.wait_for(
                    asyncio.to_thread(self.resolve_ticket_item, term),
                    timeout=GRAPH_RAG_RESOLVER_TIMEOUT_SECONDS,
                )
                item_candidates.extend(candidates)
            except asyncio.TimeoutError:
                logger.warning("[GRAPHRAG] Item resolver timed out for set query term: %s", term)

        item_candidates = self._merge_resolver_candidates(item_candidates)
        catalog_item_sys_ids = self._confident_candidate_values(item_candidates, "sys_id")
        request_item_sys_ids = self._confident_candidate_values(item_candidates, "request_item_sys_id")
        cypher_query = self._requester_item_set_cypher()
        parameters = self._requester_item_set_parameters(
            set_query,
            requester_one,
            requester_two,
            catalog_item_sys_ids,
            request_item_sys_ids,
        )
        validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
        if validation_error:
            logger.warning("[GRAPHRAG] Requester item set query rejected: %s", validation_error)
            return {"handled": False}

        execution = await self._execute_readonly_query_with_timeout(
            cypher_query,
            parameters,
            100,
            "requester_item_set_query",
        )
        if execution.get("error"):
            logger.warning("[GRAPHRAG] Requester item set query failed: %s", execution["error"])
            return {"handled": False}

        rows = execution.get("rows", [])
        answer = self._render_requester_item_set_answer(set_query, rows)
        return {
            "handled": True,
            "answer": answer,
            "candidate_count": len(rows),
            "context": [{"type": "graph_row", "row": row} for row in rows],
            "entities": {
                "requesters": {
                    set_query.requester_one: requester_one,
                    set_query.requester_two: requester_two,
                },
                "items": item_candidates,
            },
            "cypher": {
                "mode": "requester_item_set_query",
                "set_query": set_query.model_dump(),
                "parameters": parameters,
                "row_count": len(rows),
            },
        }

    def _parse_requester_item_set_query(self, query: str) -> Optional[GraphRAGRequesterSetQuery]:
        if not self._should_use_requester_item_set_query(query):
            return None

        requester_pair = self._extract_set_query_requesters(query)
        if not requester_pair:
            return None

        item_phrase = self._extract_set_query_item_phrase(query)
        inferred = self._infer_ticket_metric_filters(item_phrase)
        item_terms = inferred["catalog_item_terms"]
        category_terms = inferred["category_terms"]
        if not item_terms and not category_terms and item_phrase:
            item_terms = [item_phrase]

        return GraphRAGRequesterSetQuery(
            operation=self._set_query_operation(query),
            requester_one=requester_pair[0],
            requester_two=requester_pair[1],
            item_phrase=item_phrase,
            item_terms=self._dedupe_terms(item_terms),
            category_terms=self._dedupe_terms(category_terms),
            ticket_type="SR",
        )

    def _should_use_requester_item_set_query(self, query: str) -> bool:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        if " and " not in query_lower and " but " not in query_lower:
            return False
        has_activity = any(
            term in query_lower
            for term in ("bought", "buy", "purchased", "purchase", "requested", "request", "ordered", "order", "got")
        )
        has_set_language = any(
            term in query_lower
            for term in ("both", "either", "but not", "did not", "not bought", "not requested", "not purchased")
        )
        normalized_tokens = {self._singularize_term(token) for token in self._extract_ordered_terms(query)}
        has_item_language = bool(normalized_tokens & (TICKET_ITEM_HINT_TERMS | BROAD_TICKET_CATEGORY_TERMS | {"item"}))
        return has_activity and has_set_language and has_item_language

    def _extract_set_query_requesters(self, query: str) -> Optional[List[str]]:
        query_lower = re.sub(r"\s+", " ", query.strip().lower()).strip(" ?!.,")
        patterns = (
            r"\bdid\s+(.+?)\s+(?:buy|purchase|request|order|get)\b.*?\bbut\s+(.+?)\s+did\s+not\b",
            r"\bdid\s+(.+?)\s+and\s+(.+?)\s+both\s+(?:buy|purchase|request|order|get)\b",
            r"\bby\s+both\s+(.+?)\s+and\s+(.+?)(?=[?.!,]|$)",
            r"\bboth\s+(.+?)\s+and\s+(.+?)(?=[?.!,]|$)",
            r"\b(?:by|for|from)\s+(.+?)\s+and\s+(.+?)(?=[?.!,]|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if not match:
                continue
            first = self._clean_set_query_requester(match.group(1))
            second = self._clean_set_query_requester(match.group(2))
            if first and second and first != second:
                return [first, second]
        return None

    def _clean_set_query_requester(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip().lower()).strip(" ?!.,")
        cleaned = re.sub(r"^(?:both|either|user|requester|requestor)\s+", "", cleaned)
        cleaned = re.sub(r"\s+(?:both|either)$", "", cleaned)
        return cleaned.strip()

    def _extract_set_query_item_phrase(self, query: str) -> str:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        match = re.search(r"\b(?:which|what|show|list)\s+(.+?)\s+(?:were|was|are|is|did|do|does)\b", query_lower)
        if match:
            phrase = self._clean_set_query_item_phrase(match.group(1))
            if phrase:
                return phrase

        tokens = []
        for token in self._extract_ordered_terms(query):
            singular = self._singularize_term(token)
            if singular in TICKET_ITEM_HINT_TERMS or singular in BROAD_TICKET_CATEGORY_TERMS:
                tokens.append(singular)
        return " ".join(self._dedupe_terms(tokens[:3]))

    def _clean_set_query_item_phrase(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip().lower()).strip(" ?!.,")
        cleaned = re.sub(r"\b(?:items?|things?|records?)\b", " ", cleaned)
        tokens = [self._singularize_term(token) for token in self._extract_ordered_terms(cleaned)]
        return " ".join(tokens).strip()

    def _set_query_operation(self, query: str) -> str:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        if "but not" in query_lower or re.search(r"\bdid\s+.+?\s+not\b", query_lower):
            return "left_difference"
        if re.search(r"\bnot\s+(?:\w+\s+){0,3}by\s+both\b", query_lower) or "not bought by both" in query_lower:
            return "symmetric_difference"
        if "either" in query_lower:
            return "union"
        if "both" in query_lower:
            return "intersection"
        return "intersection"

    async def _resolve_requester_for_exact_query(self, phrase: str) -> Dict[str, Any]:
        try:
            candidates = await asyncio.wait_for(
                asyncio.to_thread(self.resolve_ticket_requester, phrase),
                timeout=GRAPH_RAG_RESOLVER_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("[GRAPHRAG] Requester resolver timed out for phrase: %s", phrase)
            return {"status": "fallback", "terms": [phrase], "candidates": []}

        selected = self._select_single_resolver_candidate(candidates)
        if selected.get("status") == "resolved":
            return selected
        if selected.get("status") == "ambiguous":
            return selected
        return {"status": "fallback", "terms": [phrase], "candidates": candidates}

    def _select_single_resolver_candidate(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        confident = [candidate for candidate in candidates if self._is_confident_resolver_candidate(candidate)]
        if not confident:
            return {"status": "fallback", "candidates": candidates}

        confident = self._merge_resolver_candidates(confident)
        top = confident[0]
        top_score = float(top.get("match_score") or 0)
        top_identity = self._resolver_candidate_identity(top)
        close_conflicts = [
            candidate
            for candidate in confident[1:]
            if float(candidate.get("match_score") or 0) >= top_score - 5
            and self._resolver_candidate_identity(candidate) != top_identity
        ]
        if close_conflicts:
            return {"status": "ambiguous", "candidates": [top] + close_conflicts[:4]}
        return {"status": "resolved", "candidate": top, "candidates": confident}

    def _resolver_candidate_identity(self, candidate: Dict[str, Any]) -> str:
        return (
            str(candidate.get("sys_id") or "").strip().lower()
            or str(candidate.get("request_item_sys_id") or "").strip().lower()
            or str(candidate.get("name") or "").strip().lower()
        )

    def _requester_item_set_parameters(
        self,
        set_query: GraphRAGRequesterSetQuery,
        requester_one: Dict[str, Any],
        requester_two: Dict[str, Any],
        catalog_item_sys_ids: List[str],
        request_item_sys_ids: List[str],
    ) -> Dict[str, Any]:
        requester_one_candidate = requester_one.get("candidate") or {}
        requester_two_candidate = requester_two.get("candidate") or {}
        return {
            "ticket_type": set_query.ticket_type,
            "requester_one_terms": self._dedupe_terms(requester_one.get("terms", []) or [set_query.requester_one]),
            "requester_two_terms": self._dedupe_terms(requester_two.get("terms", []) or [set_query.requester_two]),
            "requester_one_sys_ids": self._dedupe_terms([requester_one_candidate.get("sys_id", "")]),
            "requester_two_sys_ids": self._dedupe_terms([requester_two_candidate.get("sys_id", "")]),
            "catalog_item_sys_ids": catalog_item_sys_ids,
            "request_item_sys_ids": request_item_sys_ids,
            "item_terms": self._dedupe_terms(set_query.item_terms),
            "category_terms": self._dedupe_terms(set_query.category_terms),
            "set_operation": set_query.operation,
            "limit": 100,
        }

    def _requester_item_set_cypher(self) -> str:
        return """
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
WITH ticket, requester, item, catalog,
     coalesce(catalog.sys_id, item.cat_item_sys_id, item.sys_id, '') AS item_key,
     coalesce(catalog.name, item.cat_item_name, item.short_description, ticket.short_description, '') AS item_name,
     coalesce(catalog.category, '') AS item_category
WITH ticket, requester, item, catalog, item_key, item_name, item_category,
     (
        (size($requester_one_sys_ids) > 0 AND (
            coalesce(requester.sys_id, '') IN $requester_one_sys_ids OR
            coalesce(ticket.requested_for_sys_id, '') IN $requester_one_sys_ids OR
            coalesce(ticket.requested_by_sys_id, '') IN $requester_one_sys_ids
        )) OR
        (size($requester_one_sys_ids) = 0 AND ANY(term IN $requester_one_terms WHERE
            toLower(coalesce(ticket.requested_for_name, '')) CONTAINS term OR
            toLower(coalesce(ticket.requested_by_name, '')) CONTAINS term OR
            toLower(coalesce(requester.name, '')) CONTAINS term))
     ) AS matches_requester_one,
     (
        (size($requester_two_sys_ids) > 0 AND (
            coalesce(requester.sys_id, '') IN $requester_two_sys_ids OR
            coalesce(ticket.requested_for_sys_id, '') IN $requester_two_sys_ids OR
            coalesce(ticket.requested_by_sys_id, '') IN $requester_two_sys_ids
        )) OR
        (size($requester_two_sys_ids) = 0 AND ANY(term IN $requester_two_terms WHERE
            toLower(coalesce(ticket.requested_for_name, '')) CONTAINS term OR
            toLower(coalesce(ticket.requested_by_name, '')) CONTAINS term OR
            toLower(coalesce(requester.name, '')) CONTAINS term))
     ) AS matches_requester_two
WHERE item_key <> ''
  AND item_name <> ''
  AND ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
  AND (matches_requester_one OR matches_requester_two)
  AND (
        (size($catalog_item_sys_ids) = 0 AND size($item_terms) + size($category_terms) = 0) OR
        (size($catalog_item_sys_ids) > 0 AND (
            coalesce(catalog.sys_id, '') IN $catalog_item_sys_ids OR
            coalesce(item.sys_id, '') IN $request_item_sys_ids
        )) OR
        (size($catalog_item_sys_ids) = 0 AND ANY(term IN ($item_terms + $category_terms) WHERE
            toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
            toLower(coalesce(ticket.description, '')) CONTAINS term OR
            toLower(coalesce(item.short_description, '')) CONTAINS term OR
            toLower(coalesce(item.description, '')) CONTAINS term OR
            toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
            toLower(coalesce(catalog.name, '')) CONTAINS term OR
            toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
            toLower(coalesce(catalog.description, '')) CONTAINS term OR
            toLower(coalesce(catalog.category, '')) CONTAINS term))
      )
WITH item_key, item_name, item_category,
     count(DISTINCT CASE WHEN matches_requester_one THEN ticket END) AS requester_one_count,
     count(DISTINCT CASE WHEN matches_requester_two THEN ticket END) AS requester_two_count,
     collect(DISTINCT CASE WHEN matches_requester_one THEN ticket.number END) AS requester_one_numbers_raw,
     collect(DISTINCT CASE WHEN matches_requester_two THEN ticket.number END) AS requester_two_numbers_raw
WITH item_key, item_name, item_category, requester_one_count, requester_two_count,
     [number IN requester_one_numbers_raw WHERE number IS NOT NULL] AS requester_one_ticket_numbers,
     [number IN requester_two_numbers_raw WHERE number IS NOT NULL] AS requester_two_ticket_numbers
WHERE ($set_operation = 'intersection' AND requester_one_count > 0 AND requester_two_count > 0)
   OR ($set_operation = 'union' AND (requester_one_count > 0 OR requester_two_count > 0))
   OR ($set_operation = 'left_difference' AND requester_one_count > 0 AND requester_two_count = 0)
   OR ($set_operation = 'right_difference' AND requester_two_count > 0 AND requester_one_count = 0)
   OR ($set_operation = 'symmetric_difference' AND (
        (requester_one_count > 0 AND requester_two_count = 0) OR
        (requester_two_count > 0 AND requester_one_count = 0)
      ))
RETURN item_name AS name,
       item_category AS category,
       requester_one_count AS requester_one_count,
       requester_two_count AS requester_two_count,
       requester_one_ticket_numbers AS requester_one_ticket_numbers,
       requester_two_ticket_numbers AS requester_two_ticket_numbers
ORDER BY toLower(name) ASC
LIMIT $limit
""".strip()

    def _render_requester_item_set_answer(self, set_query: GraphRAGRequesterSetQuery, rows: List[Dict[str, Any]]) -> str:
        relation = self._set_operation_answer_label(set_query)
        item_label = set_query.item_phrase or "items"
        if not rows:
            return f"No matching {item_label} records were found in the graph for items {relation}."

        lines = [f"{self._capitalize_first(item_label)} {relation}:"]
        seen = set()
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            lines.append(f"- {name}")
        return "\n".join(lines) if len(lines) > 1 else NO_CONTEXT_ANSWER

    def _set_operation_answer_label(self, set_query: GraphRAGRequesterSetQuery) -> str:
        first = set_query.requester_one
        second = set_query.requester_two
        if set_query.operation == "intersection":
            return f"requested by both {first} and {second}"
        if set_query.operation == "union":
            return f"requested by either {first} or {second}"
        if set_query.operation == "left_difference":
            return f"requested by {first} but not {second}"
        if set_query.operation == "right_difference":
            return f"requested by {second} but not {first}"
        return f"requested by exactly one of {first} or {second}"

    def _render_ambiguous_resolver_answer(self, entity_type: str, phrase: str, candidates: List[Dict[str, Any]]) -> str:
        names = []
        for candidate in candidates[:5]:
            name = str(candidate.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
        if not names:
            return f"I could not confidently resolve the {entity_type} '{phrase}' in the graph."
        return (
            f"I found multiple possible {entity_type} matches for '{phrase}': "
            f"{', '.join(names)}. Please ask again with the full name."
        )

    def _capitalize_first(self, value: str) -> str:
        cleaned = str(value or "").strip()
        return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned

    async def _compile_graph_query_program(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
    ) -> GraphRAGCypherProgram:
        hints = {
            "domain": plan.domain,
            "intent_family": plan.intent_family,
            "entity_scope": plan.entity_scope,
            "answer_style": plan.answer_style,
            "named_entities": plan.named_entities,
            "category_terms": plan.category_terms,
            "catalog_terms": plan.catalog_terms,
            "table_terms": plan.table_terms,
            "field_terms": plan.field_terms,
            "concept_terms": plan.concept_terms,
            "sort_field": plan.sort_field,
            "sort_direction": plan.sort_direction,
            "aggregation": plan.aggregation,
            "limit": plan.limit,
        }
        fallback = self._default_graph_query_program(query, plan)

        if self._should_use_deterministic_ticket_count_program(query, plan):
            return fallback

        if not neo4j_query_runtime_service.is_available():
            return fallback

        try:
            async_client = get_async_client()
            completion = await asyncio.wait_for(
                async_client.beta.chat.completions.parse(
                    model=settings.openai_chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You compile safe read-only Cypher queries for a known ServiceNow graph. Return JSON only.",
                        },
                        {
                            "role": "user",
                            "content": GRAPH_QUERY_PROGRAM_PROMPT.format(
                                schema=neo4j_query_runtime_service.schema_overview(),
                                hints=json.dumps(hints, ensure_ascii=True, indent=2),
                                query=query,
                            ),
                        },
                    ],
                    temperature=0.1,
                    response_format=GraphRAGCypherProgram,
                ),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("OpenAI did not return a parsed graph query program")
            normalized = self._normalize_graph_query_program(parsed, fallback=fallback)
            validation_error = neo4j_query_runtime_service.validate_readonly_query(normalized.cypher_query)
            if validation_error:
                logger.warning("[GRAPHRAG] Compiled graph query rejected: %s", validation_error)
                return fallback
            return normalized
        except Exception:
            logger.exception("[GRAPHRAG] Graph query compilation failed, using fallback program")
            return fallback

    async def _execute_compiled_graph_query(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        program: GraphRAGCypherProgram,
    ) -> Dict[str, Any]:
        if not neo4j_query_runtime_service.is_available() or not program.cypher_query:
            return {"ok": False, "rows": [], "error": "runtime unavailable"}

        parameters = self._build_graph_query_parameters(query, plan, program)
        execution = await self._execute_readonly_query_with_timeout(
            program.cypher_query,
            parameters,
            50,
            "compiled_graph_query",
        )
        if execution.get("error"):
            logger.warning("[GRAPHRAG] Compiled graph query execution failed: %s", execution["error"])
        return execution

    async def _generate_compiled_graph_answer(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        program: GraphRAGCypherProgram,
        execution: Dict[str, Any],
    ) -> str:
        rows = execution.get("rows", [])
        if not rows:
            return NO_CONTEXT_ANSWER

        deterministic = self._render_compiled_rows_deterministically(query, program, rows)
        if deterministic:
            return deterministic

        try:
            async_client = get_async_client()
            completion = await asyncio.wait_for(
                async_client.chat.completions.create(
                    model=settings.openai_chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You answer only from grounded graph query rows.",
                        },
                        {
                            "role": "user",
                            "content": GRAPH_QUERY_ANSWER_PROMPT.format(
                                summary=json.dumps(
                                    {
                                        "intent_summary": program.intent_summary,
                                        "answer_style": program.answer_style,
                                        "expected_columns": program.expected_columns,
                                        "plan": {
                                            "domain": plan.domain,
                                            "intent_family": plan.intent_family,
                                            "entity_scope": plan.entity_scope,
                                        },
                                    },
                                    ensure_ascii=True,
                                    indent=2,
                                ),
                                rows=json.dumps(rows[:25], ensure_ascii=True, indent=2),
                                query=query,
                            ),
                        },
                    ],
                    temperature=0.1,
                    max_tokens=500,
                ),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            answer = (completion.choices[0].message.content or "").strip()
            if answer:
                return answer
        except Exception:
            logger.exception("[GRAPHRAG] Compiled graph answer generation failed")

        return self._render_compiled_rows_fallback(rows)

    async def _execute_structured_ticket_query(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        use_llm_breakdown: bool = True,
    ) -> Dict[str, Any]:
        if not self._should_use_structured_ticket_query(query, plan):
            return {"handled": False}
        if not neo4j_query_runtime_service.is_available():
            return {"handled": False}

        if use_llm_breakdown:
            breakdown = await self._build_ticket_query_breakdown(query, plan)
        else:
            breakdown = self._heuristic_ticket_query_breakdown(query, plan)
        breakdown = self._normalize_ticket_query_breakdown(query, plan, breakdown)
        if not breakdown.metrics:
            return {"handled": False}
        output_plan = await self._build_ticket_output_plan(query, breakdown)
        if not output_plan.sections:
            return {"handled": False}
        module_plan = self.module_planner.validate_plan(
            self.module_planner.build_structured_ticket_plan(
                breakdown=breakdown,
                output_plan=output_plan,
            )
        )
        resolution_context = await self._resolve_ticket_entities_for_breakdown(breakdown)
        resolved_metric_filters: Dict[str, Dict[str, Any]] = {}
        for metric in breakdown.metrics[:6]:
            filters = self._resolve_ticket_metric_filters(
                metric,
                breakdown,
                resolved_metric_filters,
                resolution_context,
            )
            resolved_metric_filters[self._metric_key(metric.label)] = filters
        query_entries: List[Dict[str, Any]] = []
        section_results: List[Dict[str, Any]] = []
        cached_counts: Dict[str, Dict[str, Any]] = {}
        cached_lists: Dict[str, Dict[str, Any]] = {}

        for section in output_plan.sections:
            metric_label = self._clean_ticket_metric_label(section.metric_label or section.source_metric or "")
            if not metric_label and breakdown.metrics:
                metric_label = self._metric_key(breakdown.metrics[0].label)
            filters = resolved_metric_filters.get(metric_label, {})
            if not filters:
                continue

            if section.section_type == "count_metric":
                cached = cached_counts.get(metric_label)
                if cached is None:
                    cypher_query = self.build_ticket_count_cypher()
                    parameters = self._ticket_metric_parameters(filters, limit=1)
                    validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
                    if validation_error:
                        logger.warning("[GRAPHRAG] Structured ticket count query rejected: %s", validation_error)
                        return {"handled": False}

                    execution = await self._execute_readonly_query_with_timeout(
                        cypher_query,
                        parameters,
                        5,
                        f"structured_ticket_count:{metric_label}",
                    )
                    if execution.get("error"):
                        logger.warning("[GRAPHRAG] Structured ticket count query failed: %s", execution["error"])
                        return {"handled": False}
                    rows = execution.get("rows", [])
                    cached = {
                        "label": self._metric_display_label_from_key(metric_label),
                        "count": int((rows[0] if rows else {}).get("count") or 0),
                        "filters": filters,
                    }
                    cached_counts[metric_label] = cached
                    query_entries.append(
                        {
                            "section_type": "count_metric",
                            "label": cached["label"],
                            "cypher_query": cypher_query,
                            "parameters": parameters,
                            "row_count": len(rows),
                        }
                    )
                section_results.append({"section_type": "count_metric", **cached})
                continue

            if section.section_type == "list_scope":
                source_metric = self._clean_ticket_metric_label(section.source_metric or section.metric_label or metric_label)
                source_filters = resolved_metric_filters.get(source_metric, filters)
                cached = cached_lists.get(source_metric)
                if cached is None:
                    cypher_query = self.build_ticket_list_cypher()
                    parameters = self._ticket_metric_parameters(
                        source_filters,
                        limit=self._ticket_result_limit(query, plan),
                    )
                    validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
                    if validation_error:
                        logger.warning("[GRAPHRAG] Structured ticket list query rejected: %s", validation_error)
                        return {"handled": False}

                    execution = await self._execute_readonly_query_with_timeout(
                        cypher_query,
                        parameters,
                        100,
                        f"structured_ticket_list:{source_metric}",
                    )
                    if execution.get("error"):
                        logger.warning("[GRAPHRAG] Structured ticket list query failed: %s", execution["error"])
                        return {"handled": False}
                    rows = execution.get("rows", [])
                    cached = {
                        "label": self._metric_display_label_from_key(source_metric),
                        "rows": rows,
                        "filters": source_filters,
                        "include_descriptions": section.include_descriptions,
                    }
                    cached_lists[source_metric] = cached
                    query_entries.append(
                        {
                            "section_type": "list_scope",
                            "label": cached["label"],
                            "cypher_query": cypher_query,
                            "parameters": parameters,
                            "row_count": len(rows),
                        }
                    )
                section_results.append({"section_type": "list_scope", **cached})

        answer = self._render_structured_ticket_output_answer(query, section_results)
        context_rows: List[Dict[str, Any]] = []
        for section_result in section_results:
            if section_result.get("section_type") == "count_metric":
                context_rows.append({"type": "graph_metric", "row": section_result})
            else:
                context_rows.append(
                    {
                        "type": "graph_list_section",
                        "label": section_result.get("label"),
                        "rows": section_result.get("rows", []),
                    }
                )
        return {
            "handled": True,
            "answer": answer,
            "candidate_count": self._structured_section_candidate_count(section_results),
            "context": context_rows,
            "cypher": {
                "mode": "structured_ticket_query",
                "answer_intent": breakdown.answer_intent,
                "module_plan": module_plan.model_dump(),
                "breakdown": breakdown.model_dump(),
                "output_plan": output_plan.model_dump(),
                "resolved_entities": resolution_context,
                "queries": query_entries,
                "row_count": len(query_entries),
            },
            "module_plan": module_plan.model_dump(),
        }

    async def _build_ticket_output_plan(
        self,
        query: str,
        breakdown: GraphRAGQueryBreakdown,
    ) -> GraphRAGTicketOutputPlan:
        heuristic = self._heuristic_ticket_output_plan(query, breakdown)
        if not breakdown.metrics:
            return heuristic

        try:
            async_client = get_async_client()
            completion = await asyncio.wait_for(
                async_client.beta.chat.completions.parse(
                    model=settings.openai_chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You map exact ServiceNow ticket questions into output sections. Return JSON only.",
                        },
                        {
                            "role": "user",
                            "content": TICKET_OUTPUT_PLAN_PROMPT.format(query=query),
                        },
                    ],
                    temperature=0,
                    response_format=GraphRAGTicketOutputPlan,
                ),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("OpenAI did not return a parsed ticket output plan")
            return self._merge_ticket_output_plans(parsed, heuristic, breakdown)
        except Exception:
            logger.exception("[GRAPHRAG] Ticket output planning failed, using deterministic heuristic")
            return heuristic

    def _heuristic_ticket_output_plan(
        self,
        query: str,
        breakdown: GraphRAGQueryBreakdown,
    ) -> GraphRAGTicketOutputPlan:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        metrics = breakdown.metrics[:6]
        if not metrics:
            return GraphRAGTicketOutputPlan()

        include_descriptions = self._query_requests_ticket_descriptions(query_lower)
        wants_list = breakdown.answer_intent == "list" or self._query_has_list_followup(query_lower)
        sections: List[GraphRAGOutputSection] = []
        list_marker_index = self._ticket_list_marker_index(query_lower)
        count_marker_index = query_lower.find("how many")
        list_first = (
            wants_list
            and list_marker_index >= 0
            and (count_marker_index < 0 or list_marker_index < count_marker_index)
        )

        def append_list_section() -> None:
            if breakdown.answer_intent == "list":
                source_metric = metrics[0].label
            elif len(metrics) > 1:
                source_metric = metrics[-1].label
            else:
                source_metric = metrics[0].label
            sections.append(
                GraphRAGOutputSection(
                    section_type="list_scope",
                    metric_label=source_metric,
                    source_metric=source_metric,
                    include_descriptions=include_descriptions,
                )
            )

        if wants_list and list_first:
            append_list_section()

        if breakdown.answer_intent != "list":
            for metric in metrics:
                sections.append(
                    GraphRAGOutputSection(
                        section_type="count_metric",
                        metric_label=metric.label,
                    )
                )

        if wants_list and not list_first:
            append_list_section()

        return GraphRAGTicketOutputPlan(sections=sections)

    def _merge_ticket_output_plans(
        self,
        parsed: GraphRAGTicketOutputPlan,
        heuristic: GraphRAGTicketOutputPlan,
        breakdown: GraphRAGQueryBreakdown,
    ) -> GraphRAGTicketOutputPlan:
        allowed_labels = {
            self._metric_key(metric.label): metric.label
            for metric in breakdown.metrics
        }
        sections: List[GraphRAGOutputSection] = []
        for section in parsed.sections:
            metric_key = self._metric_key(section.metric_label or section.source_metric or "")
            if not metric_key or metric_key not in allowed_labels:
                continue
            source_metric = self._metric_key(section.source_metric or section.metric_label or "")
            if source_metric and source_metric not in allowed_labels:
                source_metric = metric_key
            sections.append(
                GraphRAGOutputSection(
                    section_type=section.section_type,
                    metric_label=allowed_labels[metric_key],
                    source_metric=allowed_labels.get(source_metric) if source_metric else None,
                    include_descriptions=bool(section.include_descriptions),
                )
            )

        if not sections:
            return heuristic

        if heuristic.sections:
            wants_list = any(section.section_type == "list_scope" for section in heuristic.sections)
            has_list = any(section.section_type == "list_scope" for section in sections)
            if wants_list and not has_list:
                return heuristic

        return GraphRAGTicketOutputPlan(sections=sections)

    def _query_has_list_followup(self, query_lower: str) -> bool:
        return bool(
            re.search(r"\b(?:what|which)\s+requests?\s+(?:were|are|was|is)\s+(?:they|them|those)\b", query_lower)
            or re.search(r"\b(?:show|list)\s+(?:the\s+)?(?:requests?|tickets?|request numbers?|ticket numbers?)\b", query_lower)
            or re.search(r"\b(?:show|list)\s+(?:them|those)\b", query_lower)
        )

    def _ticket_list_marker_index(self, query_lower: str) -> int:
        patterns = (
            r"\b(?:what|which)\s+requests?\s+(?:were|are|was|is)\s+(?:they|them|those)\b",
            r"\b(?:show|list)\s+(?:the\s+)?(?:requests?|tickets?|request numbers?|ticket numbers?)\b",
            r"\b(?:show|list)\s+(?:them|those)\b",
            r"\bwhat\s+requests?\s+did\b",
            r"\bwhich\s+requests?\s+did\b",
        )
        indices = [
            match.start()
            for pattern in patterns
            if (match := re.search(pattern, query_lower))
        ]
        return min(indices) if indices else -1

    def _query_requests_ticket_descriptions(self, query_lower: str) -> bool:
        return bool(
            re.search(r"\b(?:what|which)\s+requests?\s+(?:were|are|was|is)\b", query_lower)
            or re.search(r"\bdescriptions?\b", query_lower)
            or re.search(r"\bdetails?\b", query_lower)
        )

    def _structured_section_candidate_count(self, section_results: List[Dict[str, Any]]) -> int:
        for section_result in section_results:
            if section_result.get("section_type") == "count_metric":
                return int(section_result.get("count") or 0)
        for section_result in section_results:
            if section_result.get("section_type") == "list_scope":
                return len(section_result.get("rows", []))
        return 0

    def _metric_display_label_from_key(self, metric_key: str) -> str:
        cleaned = self._clean_ticket_metric_label(metric_key)
        return cleaned or "matching requests"

    def _render_structured_ticket_output_answer(
        self,
        query: str,
        section_results: List[Dict[str, Any]],
    ) -> str:
        if not section_results:
            return NO_CONTEXT_ANSWER

        lines: List[str] = []
        for section_result in section_results:
            section_type = section_result.get("section_type")
            if section_type == "count_metric":
                label = self._format_metric_label(str(section_result.get("label") or "matching requests"))
                lines.append(f"{label}: {int(section_result.get('count') or 0)}")
                continue

            if section_type == "list_scope":
                rows = list(section_result.get("rows", []) or [])
                if not rows:
                    lines.append(
                        f"{self._format_metric_label(str(section_result.get('label') or 'matching requests'))}: no matching requests found."
                    )
                    continue
                lines.extend(
                    self._render_structured_ticket_list_lines(
                        rows,
                        label=str(section_result.get("label") or "matching requests"),
                        include_descriptions=bool(section_result.get("include_descriptions")),
                    )
                )
        return "\n".join(lines) if lines else NO_CONTEXT_ANSWER

    def _render_structured_ticket_list_lines(
        self,
        rows: List[Dict[str, Any]],
        *,
        label: str,
        include_descriptions: bool,
    ) -> List[str]:
        heading = f"{self._format_metric_label(label)}:"
        lines = [heading]
        for row in rows:
            number = row.get("number")
            if not number:
                continue
            if include_descriptions:
                description = re.sub(r"\s+", " ", str(row.get("description") or "No description available.")).strip()
                lines.append(f"- {number}: {description}")
            else:
                lines.append(f"- {number}")
        return lines if len(lines) > 1 else [f"{heading} no matching requests found."]

    async def _build_ticket_query_breakdown(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
    ) -> GraphRAGQueryBreakdown:
        heuristic = self._heuristic_ticket_query_breakdown(query, plan)

        try:
            async_client = get_async_client()
            completion = await asyncio.wait_for(
                async_client.beta.chat.completions.parse(
                    model=settings.openai_chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You decompose ServiceNow ticket questions into stable structured metric specs. Return JSON only.",
                        },
                        {
                            "role": "user",
                            "content": TICKET_QUERY_BREAKDOWN_PROMPT.format(query=query),
                        },
                    ],
                    temperature=0,
                    response_format=GraphRAGQueryBreakdown,
                ),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("OpenAI did not return a parsed ticket query breakdown")
            return self._merge_ticket_query_breakdowns(parsed, heuristic)
        except Exception:
            logger.exception("[GRAPHRAG] Ticket query decomposition failed, using deterministic heuristic")
            return heuristic

    def _merge_ticket_query_breakdowns(
        self,
        parsed: GraphRAGQueryBreakdown,
        heuristic: GraphRAGQueryBreakdown,
    ) -> GraphRAGQueryBreakdown:
        answer_intent = parsed.answer_intent
        if heuristic.answer_intent == "list":
            answer_intent = "list"

        metrics = parsed.metrics
        if heuristic.answer_intent == "list" or len(heuristic.metrics) > 1:
            metrics = heuristic.metrics
        elif heuristic.metrics and metrics:
            heuristic_has_filters = bool(
                heuristic.metrics[0].catalog_item_terms
                or heuristic.metrics[0].category_terms
                or heuristic.metrics[0].state_terms
            )
            parsed_has_filters = bool(
                metrics[0].catalog_item_terms
                or metrics[0].category_terms
                or metrics[0].state_terms
            )
            if heuristic_has_filters and not parsed_has_filters:
                metrics = heuristic.metrics
        elif not metrics or len(heuristic.metrics) > len(metrics):
            metrics = heuristic.metrics

        return GraphRAGQueryBreakdown(
            domain="ticket",
            answer_intent=answer_intent,
            requester_terms=heuristic.requester_terms or parsed.requester_terms,
            base_ticket_type=heuristic.base_ticket_type or parsed.base_ticket_type,
            time_window_label=heuristic.time_window_label or parsed.time_window_label,
            opened_start=heuristic.opened_start or parsed.opened_start,
            opened_end=heuristic.opened_end or parsed.opened_end,
            metrics=metrics,
        )

    def _normalize_ticket_query_breakdown(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        breakdown: GraphRAGQueryBreakdown,
    ) -> GraphRAGQueryBreakdown:
        split_requesters = []
        extra_filter_terms = []
        for requester in breakdown.requester_terms:
            split = self._split_requester_and_filter_tail(requester)
            if split.get("requester"):
                split_requesters.append(split["requester"])
            if split.get("filter_tail"):
                extra_filter_terms.append(split["filter_tail"])

        requester_terms = self._dedupe_terms(split_requesters or self._extract_requester_terms(query))
        base_ticket_type = breakdown.base_ticket_type or self._resolve_ticket_type_filter(plan, query)
        time_window = self._parse_time_window(re.sub(r"\s+", " ", query.strip().lower()))

        raw_metrics = list(breakdown.metrics)
        if not raw_metrics:
            raw_metrics = [
                GraphRAGMetricSpec(
                    label="matching requests" if breakdown.answer_intent == "list" else self._default_ticket_metric_label(base_ticket_type),
                )
            ]

        normalized_metrics: List[GraphRAGMetricSpec] = []
        multiple_metrics = len(raw_metrics) > 1
        for index, metric in enumerate(raw_metrics[:6]):
            label = self._clean_ticket_metric_label(metric.label)
            if not label:
                label = "matching requests" if breakdown.answer_intent == "list" else self._default_ticket_metric_label(base_ticket_type)

            inferred = self._infer_ticket_metric_filters(label)
            catalog_item_terms = self._dedupe_terms(metric.catalog_item_terms + inferred["catalog_item_terms"])
            category_terms = self._dedupe_terms(metric.category_terms + inferred["category_terms"])
            split_specific = self._split_specific_item_terms(category_terms)
            catalog_item_terms = self._dedupe_terms(catalog_item_terms + split_specific["catalog_item_terms"])
            category_terms = split_specific["category_terms"]
            state_terms = self._dedupe_terms(
                [self._canonical_ticket_state(term) for term in metric.state_terms + inferred["state_terms"]]
            )

            if index == 0 and multiple_metrics and self._metric_is_total(label):
                catalog_item_terms = []
                category_terms = []
                state_terms = []

            if extra_filter_terms and not (catalog_item_terms or category_terms or state_terms) and not self._metric_is_total(label):
                extra = self._infer_ticket_metric_filters(" ".join(extra_filter_terms))
                catalog_item_terms = self._dedupe_terms(catalog_item_terms + extra["catalog_item_terms"])
                category_terms = self._dedupe_terms(category_terms + extra["category_terms"])
                state_terms = self._dedupe_terms(state_terms + extra["state_terms"])

            normalized_metrics.append(
                GraphRAGMetricSpec(
                    label=label,
                    parent_metric=self._clean_ticket_metric_label(metric.parent_metric or "") or None,
                    inherit_parent_filters=bool(metric.inherit_parent_filters),
                    ticket_type=metric.ticket_type or base_ticket_type,  # type: ignore[arg-type]
                    requester_terms=self._dedupe_terms(metric.requester_terms or requester_terms),
                    catalog_item_terms=catalog_item_terms,
                    category_terms=category_terms,
                    state_terms=state_terms,
                    ticket_numbers=self._dedupe_terms(metric.ticket_numbers),
                )
            )

        return GraphRAGQueryBreakdown(
            domain="ticket",
            answer_intent=breakdown.answer_intent,
            requester_terms=requester_terms,
            base_ticket_type=base_ticket_type,  # type: ignore[arg-type]
            time_window_label=(time_window.label if time_window else ""),
            opened_start=(time_window.start if time_window else ""),
            opened_end=(time_window.end if time_window else ""),
            metrics=normalized_metrics,
        )

    def _heuristic_ticket_query_breakdown(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
    ) -> GraphRAGQueryBreakdown:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        requester_split = self._extract_ticket_requester_tail(query)
        requester_terms = self._dedupe_terms([requester_split.get("requester", "")])
        tail_filter = requester_split.get("filter_tail", "")
        ticket_type = self._resolve_ticket_type_filter(plan, query)
        answer_intent: Literal["count", "list"] = "list" if self._is_ticket_identifier_query(query_lower) else "count"
        time_window = self._parse_time_window(query_lower)

        if answer_intent == "list":
            label = "matching requests"
            inferred = self._infer_ticket_metric_filters(tail_filter)
            metric = GraphRAGMetricSpec(
                label=label,
                ticket_type=ticket_type,  # type: ignore[arg-type]
                requester_terms=requester_terms,
                catalog_item_terms=inferred["catalog_item_terms"],
                category_terms=inferred["category_terms"],
                state_terms=inferred["state_terms"],
            )
            return GraphRAGQueryBreakdown(
                domain="ticket",
                answer_intent="list",
                requester_terms=requester_terms,
                base_ticket_type=ticket_type,  # type: ignore[arg-type]
                time_window_label=(time_window.label if time_window else ""),
                opened_start=(time_window.start if time_window else ""),
                opened_end=(time_window.end if time_window else ""),
                metrics=[metric],
            )

        segment_specs = self._extract_ticket_metric_segments(query)
        metrics: List[GraphRAGMetricSpec] = []
        if segment_specs:
            total_label = self._default_ticket_metric_label(ticket_type)
            metrics.append(
                GraphRAGMetricSpec(
                    label=total_label,
                    ticket_type=ticket_type,  # type: ignore[arg-type]
                    requester_terms=requester_terms,
                )
            )
            previous_label = total_label
            for spec in segment_specs:
                inferred = self._infer_ticket_metric_filters(spec["label"])
                label = self._ticket_metric_display_label(spec["label"], ticket_type)
                inherit = bool(spec.get("inherit_parent_filters"))
                metrics.append(
                    GraphRAGMetricSpec(
                        label=label,
                        parent_metric=previous_label if inherit else None,
                        inherit_parent_filters=inherit,
                        ticket_type=ticket_type,  # type: ignore[arg-type]
                        requester_terms=requester_terms,
                        catalog_item_terms=inferred["catalog_item_terms"],
                        category_terms=inferred["category_terms"],
                        state_terms=inferred["state_terms"],
                    )
                )
                previous_label = label
        else:
            direct_label = self._extract_direct_ticket_filter_label(query, tail_filter)
            if direct_label:
                inferred = self._infer_ticket_metric_filters(direct_label)
                label = self._ticket_metric_display_label(direct_label, ticket_type)
                metrics.append(
                    GraphRAGMetricSpec(
                        label=label,
                        ticket_type=ticket_type,  # type: ignore[arg-type]
                        requester_terms=requester_terms,
                        catalog_item_terms=inferred["catalog_item_terms"],
                        category_terms=inferred["category_terms"],
                        state_terms=inferred["state_terms"],
                    )
                )
            else:
                state_terms = self._resolve_ticket_state_filters(query, plan)
                metrics.append(
                    GraphRAGMetricSpec(
                        label=self._default_ticket_metric_label(ticket_type, state_terms=state_terms),
                        ticket_type=ticket_type,  # type: ignore[arg-type]
                        requester_terms=requester_terms,
                        state_terms=state_terms,
                    )
                )

        return GraphRAGQueryBreakdown(
            domain="ticket",
            answer_intent="count",
            requester_terms=requester_terms,
            base_ticket_type=ticket_type,  # type: ignore[arg-type]
            time_window_label=(time_window.label if time_window else ""),
            opened_start=(time_window.start if time_window else ""),
            opened_end=(time_window.end if time_window else ""),
            metrics=metrics,
        )

    def _should_use_structured_ticket_query(self, query: str, plan: GraphRAGRetrievalPlan) -> bool:
        if plan.domain != "ticket":
            return False
        query_lower = query.lower()
        has_ticket_noun = any(
            phrase in query_lower
            for phrase in (
                "request",
                "requests",
                "ticket",
                "tickets",
                "service request",
                "service requests",
                "change request",
                "change requests",
            )
        )
        has_structured_intent = (
            any(token in query_lower for token in ("how many", "count", "number of"))
            or self._is_ticket_identifier_query(query_lower)
        )
        return has_ticket_noun and has_structured_intent

    def _resolve_ticket_metric_filters(
        self,
        metric: GraphRAGMetricSpec,
        breakdown: GraphRAGQueryBreakdown,
        resolved_metric_filters: Dict[str, Dict[str, Any]],
        resolution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        parent_filters = {}
        if metric.inherit_parent_filters and metric.parent_metric:
            parent_filters = resolved_metric_filters.get(self._metric_key(metric.parent_metric), {})

        resolution_context = resolution_context or {}
        requester_terms = self._dedupe_terms(metric.requester_terms or breakdown.requester_terms)
        catalog_item_terms = self._dedupe_terms(metric.catalog_item_terms)
        category_terms = self._dedupe_terms(metric.category_terms)
        requester_candidates = self._resolved_candidates_for_terms(
            requester_terms,
            resolution_context.get("requesters", {}),
        )
        item_candidates = self._resolved_candidates_for_terms(
            catalog_item_terms,
            resolution_context.get("items", {}),
        )
        requester_sys_ids = self._confident_candidate_values(requester_candidates, "sys_id")
        catalog_item_sys_ids = self._confident_candidate_values(item_candidates, "sys_id")
        request_item_sys_ids = self._confident_candidate_values(item_candidates, "request_item_sys_id")

        return {
            "ticket_type": metric.ticket_type or breakdown.base_ticket_type,
            "opened_start": breakdown.opened_start,
            "opened_end": breakdown.opened_end,
            "requester_terms": requester_terms,
            "requester_sys_ids": requester_sys_ids,
            "ticket_numbers": self._dedupe_terms(metric.ticket_numbers),
            "state_terms": self._dedupe_terms(metric.state_terms),
            "parent_catalog_item_sys_ids": self._dedupe_terms(parent_filters.get("catalog_item_sys_ids", [])),
            "parent_request_item_sys_ids": self._dedupe_terms(parent_filters.get("request_item_sys_ids", [])),
            "parent_item_terms": self._dedupe_terms(parent_filters.get("catalog_item_terms", [])),
            "parent_category_terms": self._dedupe_terms(parent_filters.get("category_terms", [])),
            "catalog_item_sys_ids": catalog_item_sys_ids,
            "request_item_sys_ids": request_item_sys_ids,
            "catalog_item_terms": catalog_item_terms,
            "category_terms": category_terms,
            "requester_candidates": requester_candidates,
            "item_candidates": item_candidates,
        }

    def _ticket_metric_parameters(self, filters: Dict[str, Any], limit: int) -> Dict[str, Any]:
        return {
            "ticket_type": filters.get("ticket_type"),
            "opened_start": str(filters.get("opened_start") or "").strip(),
            "opened_end": str(filters.get("opened_end") or "").strip(),
            "requester_terms": self._dedupe_terms(filters.get("requester_terms", [])),
            "requester_sys_ids": self._dedupe_terms(filters.get("requester_sys_ids", [])),
            "ticket_numbers": self._dedupe_terms(filters.get("ticket_numbers", [])),
            "state_terms": self._dedupe_terms(filters.get("state_terms", [])),
            "parent_catalog_item_sys_ids": self._dedupe_terms(filters.get("parent_catalog_item_sys_ids", [])),
            "parent_request_item_sys_ids": self._dedupe_terms(filters.get("parent_request_item_sys_ids", [])),
            "parent_item_terms": self._dedupe_terms(filters.get("parent_item_terms", [])),
            "parent_category_terms": self._dedupe_terms(filters.get("parent_category_terms", [])),
            "catalog_item_sys_ids": self._dedupe_terms(filters.get("catalog_item_sys_ids", [])),
            "request_item_sys_ids": self._dedupe_terms(filters.get("request_item_sys_ids", [])),
            "item_terms": self._dedupe_terms(filters.get("catalog_item_terms", [])),
            "category_terms": self._dedupe_terms(filters.get("category_terms", [])),
            "limit": max(1, min(int(limit or 15), 100)),
        }

    async def _resolve_ticket_entities_for_breakdown(self, breakdown: GraphRAGQueryBreakdown) -> Dict[str, Any]:
        requester_terms = self._dedupe_terms(
            breakdown.requester_terms +
            [
                term
                for metric in breakdown.metrics
                for term in metric.requester_terms
            ]
        )
        item_terms = self._dedupe_terms(
            [
                term
                for metric in breakdown.metrics
                for term in metric.catalog_item_terms
            ]
        )

        requesters: Dict[str, List[Dict[str, Any]]] = {}
        items: Dict[str, List[Dict[str, Any]]] = {}

        for phrase in requester_terms:
            try:
                requesters[phrase] = await asyncio.wait_for(
                    asyncio.to_thread(self.resolve_ticket_requester, phrase),
                    timeout=GRAPH_RAG_RESOLVER_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("[GRAPHRAG] Requester resolver timed out for phrase: %s", phrase)
                requesters[phrase] = []
        for phrase in item_terms:
            try:
                items[phrase] = await asyncio.wait_for(
                    asyncio.to_thread(self.resolve_ticket_item, phrase),
                    timeout=GRAPH_RAG_RESOLVER_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("[GRAPHRAG] Item resolver timed out for phrase: %s", phrase)
                items[phrase] = []

        return {"requesters": requesters, "items": items}

    def resolve_ticket_requester(self, phrase: str, limit: int = 5) -> List[Dict[str, Any]]:
        terms = self._resolver_terms(phrase)
        if not terms or not neo4j_query_runtime_service.is_available():
            return []

        cypher_query = self._ticket_requester_resolver_cypher()
        validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
        if validation_error:
            logger.warning("[GRAPHRAG] Ticket requester resolver rejected: %s", validation_error)
            return []

        execution = neo4j_query_runtime_service.execute_readonly_query(
            cypher_query,
            {"requester_terms": terms, "limit": max(1, min(limit, 20))},
            20,
            GRAPH_RAG_NEO4J_TIMEOUT_SECONDS,
        )
        if execution.get("error"):
            logger.warning("[GRAPHRAG] Ticket requester resolver failed: %s", execution["error"])
            return []
        return self._normalize_resolver_candidates(execution.get("rows", []), source="requester")

    def resolve_ticket_item(self, phrase: str, limit: int = 5) -> List[Dict[str, Any]]:
        terms = self._resolver_terms(phrase)
        if not terms or not neo4j_query_runtime_service.is_available():
            return []

        candidates: List[Dict[str, Any]] = []
        for cypher_query in (self._ticket_catalog_item_resolver_cypher(), self._ticket_request_item_resolver_cypher()):
            validation_error = neo4j_query_runtime_service.validate_readonly_query(cypher_query)
            if validation_error:
                logger.warning("[GRAPHRAG] Ticket item resolver rejected: %s", validation_error)
                continue
            execution = neo4j_query_runtime_service.execute_readonly_query(
                cypher_query,
                {
                    "item_terms": terms,
                    "category_terms": [],
                    "limit": max(1, min(limit, 20)),
                },
                20,
                GRAPH_RAG_NEO4J_TIMEOUT_SECONDS,
            )
            if execution.get("error"):
                logger.warning("[GRAPHRAG] Ticket item resolver failed: %s", execution["error"])
                continue
            candidates.extend(self._normalize_resolver_candidates(execution.get("rows", []), source="item"))

        return self._merge_resolver_candidates(candidates)[: max(1, min(limit, 20))]

    def _ticket_requester_resolver_cypher(self) -> str:
        return """
MATCH (ticket:Ticket)
OPTIONAL MATCH (user:User)-[:REQUESTED]->(ticket)
WITH DISTINCT
     coalesce(user.sys_id, ticket.requested_for_sys_id, ticket.requested_by_sys_id, '') AS sys_id,
     coalesce(user.name, ticket.requested_for_name, ticket.requested_by_name, '') AS name
WITH sys_id, name, toLower(coalesce(name, '')) AS normalized_name
WHERE normalized_name <> '' AND size($requester_terms) > 0
WITH sys_id, name, normalized_name,
     reduce(score = 0, term IN $requester_terms |
        score +
        CASE WHEN normalized_name = term THEN 100 ELSE 0 END +
        CASE WHEN normalized_name <> term AND (normalized_name CONTAINS term OR term CONTAINS normalized_name) THEN 80 ELSE 0 END +
        CASE WHEN ANY(token IN split(term, ' ') WHERE size(token) >= 3 AND normalized_name CONTAINS token) THEN 10 ELSE 0 END
     ) AS match_score
WHERE match_score > 0
RETURN sys_id AS sys_id,
       null AS request_item_sys_id,
       name AS name,
       null AS category,
       match_score AS match_score,
       CASE
           WHEN ANY(term IN $requester_terms WHERE normalized_name = term) THEN 'exact'
           WHEN ANY(term IN $requester_terms WHERE normalized_name CONTAINS term OR term CONTAINS normalized_name) THEN 'phrase'
           ELSE 'token'
       END AS match_type
ORDER BY match_score DESC, name ASC
LIMIT $limit
""".strip()

    def _ticket_catalog_item_resolver_cypher(self) -> str:
        return """
MATCH (catalog:CatalogItem)
WITH catalog,
     toLower(coalesce(catalog.name, '')) AS normalized_name,
     toLower(coalesce(catalog.category, '')) AS normalized_category,
     toLower(coalesce(catalog.short_description, '')) AS normalized_short_description,
     toLower(coalesce(catalog.description, '')) AS normalized_description,
     ($item_terms + $category_terms) AS terms
WHERE size(terms) > 0
WITH catalog, normalized_name, normalized_category, normalized_short_description, normalized_description, terms,
     reduce(score = 0, term IN terms |
        score +
        CASE WHEN normalized_name = term THEN 100 ELSE 0 END +
        CASE WHEN normalized_name <> term AND (normalized_name CONTAINS term OR term CONTAINS normalized_name) THEN 80 ELSE 0 END +
        CASE WHEN normalized_category = term THEN 70 ELSE 0 END +
        CASE WHEN normalized_short_description CONTAINS term THEN 35 ELSE 0 END +
        CASE WHEN normalized_description CONTAINS term THEN 20 ELSE 0 END +
        CASE WHEN ANY(token IN split(term, ' ') WHERE size(token) >= 3 AND (normalized_name CONTAINS token OR normalized_category CONTAINS token)) THEN 10 ELSE 0 END
     ) AS match_score
WHERE match_score > 0
RETURN catalog.sys_id AS sys_id,
       null AS request_item_sys_id,
       catalog.name AS name,
       catalog.category AS category,
       match_score AS match_score,
       CASE
           WHEN ANY(term IN terms WHERE normalized_name = term) THEN 'exact'
           WHEN ANY(term IN terms WHERE normalized_name CONTAINS term OR term CONTAINS normalized_name) THEN 'phrase'
           WHEN ANY(term IN terms WHERE normalized_category = term) THEN 'category'
           ELSE 'token'
       END AS match_type
ORDER BY match_score DESC, name ASC
LIMIT $limit
""".strip()

    def _ticket_request_item_resolver_cypher(self) -> str:
        return """
MATCH (item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
WITH item, catalog,
     toLower(coalesce(item.cat_item_name, catalog.name, '')) AS normalized_name,
     toLower(coalesce(catalog.category, '')) AS normalized_category,
     toLower(coalesce(item.short_description, '')) AS normalized_short_description,
     toLower(coalesce(item.description, '')) AS normalized_description,
     ($item_terms + $category_terms) AS terms
WHERE size(terms) > 0
WITH item, catalog, normalized_name, normalized_category, normalized_short_description, normalized_description, terms,
     reduce(score = 0, term IN terms |
        score +
        CASE WHEN normalized_name = term THEN 100 ELSE 0 END +
        CASE WHEN normalized_name <> term AND (normalized_name CONTAINS term OR term CONTAINS normalized_name) THEN 80 ELSE 0 END +
        CASE WHEN normalized_category = term THEN 70 ELSE 0 END +
        CASE WHEN normalized_short_description CONTAINS term THEN 35 ELSE 0 END +
        CASE WHEN normalized_description CONTAINS term THEN 20 ELSE 0 END +
        CASE WHEN ANY(token IN split(term, ' ') WHERE size(token) >= 3 AND (normalized_name CONTAINS token OR normalized_category CONTAINS token)) THEN 10 ELSE 0 END
     ) AS match_score
WHERE match_score > 0
RETURN coalesce(catalog.sys_id, item.cat_item_sys_id, '') AS sys_id,
       item.sys_id AS request_item_sys_id,
       coalesce(catalog.name, item.cat_item_name, item.short_description) AS name,
       catalog.category AS category,
       match_score AS match_score,
       CASE
           WHEN ANY(term IN terms WHERE normalized_name = term) THEN 'exact'
           WHEN ANY(term IN terms WHERE normalized_name CONTAINS term OR term CONTAINS normalized_name) THEN 'phrase'
           WHEN ANY(term IN terms WHERE normalized_category = term) THEN 'category'
           ELSE 'token'
       END AS match_type
ORDER BY match_score DESC, name ASC
LIMIT $limit
""".strip()

    def _resolver_terms(self, phrase: str) -> List[str]:
        cleaned = self._clean_ticket_metric_label(phrase)
        if not cleaned:
            return []
        tokens = self._extract_ordered_terms(cleaned)
        return self._dedupe_terms([cleaned] + tokens + self._expand_alias_terms([cleaned] + tokens))

    def _normalize_resolver_candidates(self, rows: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            try:
                match_score = float(row.get("match_score") or 0)
            except (TypeError, ValueError):
                match_score = 0.0
            candidates.append(
                {
                    "sys_id": str(row.get("sys_id") or "").strip(),
                    "request_item_sys_id": str(row.get("request_item_sys_id") or "").strip(),
                    "name": name,
                    "category": str(row.get("category") or "").strip(),
                    "match_score": match_score,
                    "match_type": str(row.get("match_type") or "token").strip().lower(),
                    "source": source,
                }
            )
        candidates.sort(key=lambda item: (float(item.get("match_score") or 0), item.get("name") or ""), reverse=True)
        return candidates

    def _merge_resolver_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for candidate in candidates:
            key = "|".join(
                [
                    str(candidate.get("sys_id") or ""),
                    str(candidate.get("request_item_sys_id") or ""),
                    str(candidate.get("name") or "").lower(),
                ]
            )
            current = merged.get(key)
            if not current or float(candidate.get("match_score") or 0) > float(current.get("match_score") or 0):
                merged[key] = candidate
        return sorted(
            merged.values(),
            key=lambda item: (float(item.get("match_score") or 0), item.get("name") or ""),
            reverse=True,
        )

    def _resolved_candidates_for_terms(
        self,
        terms: List[str],
        grouped_candidates: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for term in terms:
            candidates.extend(grouped_candidates.get(term, []))
        return self._merge_resolver_candidates(candidates)

    def _confident_candidate_values(self, candidates: List[Dict[str, Any]], field_name: str) -> List[str]:
        values = []
        selected = self._select_single_resolver_candidate(candidates)
        if selected.get("status") != "resolved":
            return []
        top_identity = self._resolver_candidate_identity(selected.get("candidate") or {})
        for candidate in candidates:
            if self._resolver_candidate_identity(candidate) != top_identity:
                continue
            value = str(candidate.get(field_name) or "").strip().lower()
            if value:
                values.append(value)
        return self._dedupe_terms(values)

    def _is_confident_resolver_candidate(self, candidate: Dict[str, Any]) -> bool:
        match_type = str(candidate.get("match_type") or "").lower()
        try:
            match_score = float(candidate.get("match_score") or 0)
        except (TypeError, ValueError):
            match_score = 0.0
        return match_type in {"exact", "phrase"} or match_score >= 75.0

    def build_ticket_count_cypher(self) -> str:
        return self._ticket_metric_count_cypher()

    def build_ticket_list_cypher(self) -> str:
        return self._ticket_metric_list_cypher()

    def _ticket_metric_count_cypher(self) -> str:
        return """
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
WITH ticket, requester, item, catalog
WHERE ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
  AND ($opened_start = '' OR toString(coalesce(ticket.opened_at, '')) >= $opened_start)
  AND ($opened_end = '' OR toString(coalesce(ticket.opened_at, '')) < $opened_end)
  AND (
        (size($requester_sys_ids) = 0 AND size($requester_terms) = 0) OR
        (size($requester_sys_ids) > 0 AND (
            coalesce(requester.sys_id, '') IN $requester_sys_ids OR
            coalesce(ticket.requested_for_sys_id, '') IN $requester_sys_ids OR
            coalesce(ticket.requested_by_sys_id, '') IN $requester_sys_ids
        )) OR
        (size($requester_sys_ids) = 0 AND ANY(term IN $requester_terms WHERE
            (toLower(coalesce(ticket.requested_for_name, '')) CONTAINS term OR
            toLower(coalesce(ticket.requested_by_name, '')) CONTAINS term OR
            toLower(coalesce(requester.name, '')) CONTAINS term)))
      )
  AND (size($ticket_numbers) = 0 OR ANY(term IN $ticket_numbers WHERE
        (toLower(coalesce(ticket.number, '')) CONTAINS term OR
        toLower(coalesce(item.number, '')) CONTAINS term)))
  AND (size($state_terms) = 0 OR ANY(term IN $state_terms WHERE
        (toLower(coalesce(ticket.state_display, '')) CONTAINS term OR
        toLower(coalesce(ticket.state, '')) CONTAINS term)))
  AND (
        (size($parent_catalog_item_sys_ids) = 0 AND size($parent_item_terms) + size($parent_category_terms) = 0) OR
        (size($parent_catalog_item_sys_ids) > 0 AND (
            coalesce(catalog.sys_id, '') IN $parent_catalog_item_sys_ids OR
            coalesce(item.sys_id, '') IN $parent_request_item_sys_ids
        )) OR
        (size($parent_catalog_item_sys_ids) = 0 AND ANY(term IN ($parent_item_terms + $parent_category_terms) WHERE
            (toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
            toLower(coalesce(ticket.description, '')) CONTAINS term OR
            toLower(coalesce(item.short_description, '')) CONTAINS term OR
            toLower(coalesce(item.description, '')) CONTAINS term OR
            toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
            toLower(coalesce(catalog.name, '')) CONTAINS term OR
            toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
            toLower(coalesce(catalog.description, '')) CONTAINS term OR
            toLower(coalesce(catalog.category, '')) CONTAINS term)))
      )
  AND (
        (size($catalog_item_sys_ids) = 0 AND size($item_terms) + size($category_terms) = 0) OR
        (size($catalog_item_sys_ids) > 0 AND (
            coalesce(catalog.sys_id, '') IN $catalog_item_sys_ids OR
            coalesce(item.sys_id, '') IN $request_item_sys_ids
        )) OR
        (size($catalog_item_sys_ids) = 0 AND ANY(term IN ($item_terms + $category_terms) WHERE
            (toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
            toLower(coalesce(ticket.description, '')) CONTAINS term OR
            toLower(coalesce(item.short_description, '')) CONTAINS term OR
            toLower(coalesce(item.description, '')) CONTAINS term OR
            toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
            toLower(coalesce(catalog.name, '')) CONTAINS term OR
            toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
            toLower(coalesce(catalog.description, '')) CONTAINS term OR
            toLower(coalesce(catalog.category, '')) CONTAINS term)))
      )
RETURN count(DISTINCT ticket) AS count
""".strip()

    def _ticket_metric_list_cypher(self) -> str:
        return """
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
WITH ticket, requester, item, catalog
WHERE ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
  AND ($opened_start = '' OR toString(coalesce(ticket.opened_at, '')) >= $opened_start)
  AND ($opened_end = '' OR toString(coalesce(ticket.opened_at, '')) < $opened_end)
  AND (
        (size($requester_sys_ids) = 0 AND size($requester_terms) = 0) OR
        (size($requester_sys_ids) > 0 AND (
            coalesce(requester.sys_id, '') IN $requester_sys_ids OR
            coalesce(ticket.requested_for_sys_id, '') IN $requester_sys_ids OR
            coalesce(ticket.requested_by_sys_id, '') IN $requester_sys_ids
        )) OR
        (size($requester_sys_ids) = 0 AND ANY(term IN $requester_terms WHERE
            (toLower(coalesce(ticket.requested_for_name, '')) CONTAINS term OR
            toLower(coalesce(ticket.requested_by_name, '')) CONTAINS term OR
            toLower(coalesce(requester.name, '')) CONTAINS term)))
      )
  AND (size($ticket_numbers) = 0 OR ANY(term IN $ticket_numbers WHERE
        (toLower(coalesce(ticket.number, '')) CONTAINS term OR
        toLower(coalesce(item.number, '')) CONTAINS term)))
  AND (size($state_terms) = 0 OR ANY(term IN $state_terms WHERE
        (toLower(coalesce(ticket.state_display, '')) CONTAINS term OR
        toLower(coalesce(ticket.state, '')) CONTAINS term)))
  AND (
        (size($parent_catalog_item_sys_ids) = 0 AND size($parent_item_terms) + size($parent_category_terms) = 0) OR
        (size($parent_catalog_item_sys_ids) > 0 AND (
            coalesce(catalog.sys_id, '') IN $parent_catalog_item_sys_ids OR
            coalesce(item.sys_id, '') IN $parent_request_item_sys_ids
        )) OR
        (size($parent_catalog_item_sys_ids) = 0 AND ANY(term IN ($parent_item_terms + $parent_category_terms) WHERE
            (toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
            toLower(coalesce(ticket.description, '')) CONTAINS term OR
            toLower(coalesce(item.short_description, '')) CONTAINS term OR
            toLower(coalesce(item.description, '')) CONTAINS term OR
            toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
            toLower(coalesce(catalog.name, '')) CONTAINS term OR
            toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
            toLower(coalesce(catalog.description, '')) CONTAINS term OR
            toLower(coalesce(catalog.category, '')) CONTAINS term)))
      )
  AND (
        (size($catalog_item_sys_ids) = 0 AND size($item_terms) + size($category_terms) = 0) OR
        (size($catalog_item_sys_ids) > 0 AND (
            coalesce(catalog.sys_id, '') IN $catalog_item_sys_ids OR
            coalesce(item.sys_id, '') IN $request_item_sys_ids
        )) OR
        (size($catalog_item_sys_ids) = 0 AND ANY(term IN ($item_terms + $category_terms) WHERE
            (toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
            toLower(coalesce(ticket.description, '')) CONTAINS term OR
            toLower(coalesce(item.short_description, '')) CONTAINS term OR
            toLower(coalesce(item.description, '')) CONTAINS term OR
            toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
            toLower(coalesce(catalog.name, '')) CONTAINS term OR
            toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
            toLower(coalesce(catalog.description, '')) CONTAINS term OR
            toLower(coalesce(catalog.category, '')) CONTAINS term)))
      )
RETURN DISTINCT ticket.number AS number,
       coalesce(ticket.short_description, item.short_description, catalog.name, '') AS description,
       ticket.state_display AS state,
       toString(coalesce(ticket.opened_at, '')) AS opened_at
ORDER BY opened_at DESC, number ASC
LIMIT $limit
""".strip()

    def _render_structured_ticket_count_answer(self, metric_results: List[Dict[str, Any]]) -> str:
        if not metric_results:
            return NO_CONTEXT_ANSWER
        lines = []
        for result in metric_results:
            label = self._format_metric_label(str(result.get("label") or "matching requests"))
            lines.append(f"{label}: {int(result.get('count') or 0)}")
        return "\n".join(lines)

    def _render_structured_ticket_list_answer(self, query: str, rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return NO_CONTEXT_ANSWER
        wants_descriptions = self._ticket_identifier_wants_descriptions(query.lower())
        lines = []
        for row in rows:
            number = row.get("number")
            if not number:
                continue
            if wants_descriptions:
                lines.append(f"- {number}: {row.get('description') or 'No description available.'}")
            else:
                lines.append(f"- {number}")
        return "\n".join(lines) if lines else NO_CONTEXT_ANSWER

    def _extract_ticket_requester_tail(self, query: str) -> Dict[str, str]:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        patterns = (
            r"\b(?:change\s+requests?|service\s+requests?|requests?|tickets?)\s+(?:have|has)\s+(.+?)\s+(?:made|submitted|created|raised|opened|logged|requested)\b",
            r"\b(?:change\s+requests?|service\s+requests?|requests?|tickets?)\s+did\s+(.+?)\s+(?:make|submit|create|raise|open|log|request)\b",
            r"\b(?:for|by|from)\s+([a-z][a-z\s.'-]{1,100}?)(?=\s+(?:(?:that|those)\s+)?(?:are|is|were|was)\b|\s+(?:and\s+how\s+many|with|where|when|during)\b|[?.!,]|$)",
            r"\b(?:for|by|from)\s+([a-z][a-z\s.'-]{1,100})$",
        )
        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if not match:
                continue
            candidate = re.sub(r"\s+", " ", match.group(1)).strip(" ?!.,")
            candidate = re.sub(r"^(?:user|requester|requestor)\s+", "", candidate).strip()
            split = self._split_requester_and_filter_tail(candidate)
            if split.get("requester") or split.get("filter_tail"):
                return split
        return {"requester": "", "filter_tail": ""}

    def _split_requester_and_filter_tail(self, phrase: str) -> Dict[str, str]:
        cleaned = re.sub(r"\s+", " ", str(phrase or "").strip().lower()).strip(" ?!.,")
        if not cleaned:
            return {"requester": "", "filter_tail": ""}

        tokens = cleaned.split()
        if len(tokens) <= 2 and self._looks_like_ticket_filter_tail(cleaned):
            return {"requester": "", "filter_tail": cleaned}

        for index in range(2, len(tokens)):
            requester = " ".join(tokens[:index]).strip()
            tail = " ".join(tokens[index:]).strip()
            if requester and tail and self._looks_like_ticket_filter_tail(tail):
                return {"requester": requester, "filter_tail": tail}

        return {"requester": cleaned, "filter_tail": ""}

    def _looks_like_ticket_filter_tail(self, phrase: str) -> bool:
        terms = self._extract_ordered_terms(phrase)
        if not terms:
            return False
        return any(term in TICKET_ITEM_HINT_TERMS or term in TICKET_STATE_TERMS for term in terms)

    def _extract_ticket_metric_segments(self, query: str) -> List[Dict[str, Any]]:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        matches = re.finditer(
            r"\band\s+how\s+many\s+(.+?)(?=\s+\band\s+how\s+many\b|[?.!,]|$)",
            query_lower,
        )
        segments: List[Dict[str, Any]] = []
        for match in matches:
            body = re.sub(r"\s+", " ", match.group(1)).strip(" ?!.,")
            inherit = bool(re.search(r"\b(?:of\s+)?(?:those|them)\b", body))
            label = self._strip_ticket_list_followup_tail(body)
            label = re.sub(r"^(?:of\s+)?(?:those|them)\s+", "", label).strip()
            label = re.sub(r"^(?:are|were|was|is|have\s+been|had\s+been)\s+", "", label).strip()
            label = self._metric_filter_fragment(label)
            if label:
                segments.append({"label": label, "inherit_parent_filters": inherit})
        return segments[:5]

    def _extract_direct_ticket_filter_label(self, query: str, tail_filter: str) -> str:
        if tail_filter:
            return self._metric_filter_fragment(self._strip_ticket_list_followup_tail(tail_filter))

        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        direct_patterns = (
            r"\b(?:are|were|is|was)\s+(.+?)(?=\s+\band\s+how\s+many\b|[?.!,]|$)",
            r"\bmatching\s+(.+?)(?=[?.!,]|$)",
        )
        for pattern in direct_patterns:
            match = re.search(pattern, query_lower)
            if not match:
                continue
            label = self._metric_filter_fragment(self._strip_ticket_list_followup_tail(match.group(1)))
            if label:
                return label
        return ""

    def _strip_ticket_list_followup_tail(self, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip().lower()).strip(" ?!.,")
        marker = re.search(
            (
                r"\s+\band\s+(?:what|which)\s+requests?\s+(?:were|are|was|is)\s+(?:they|them|those)\b"
                r"|\s+\band\s+(?:show|list)\s+(?:the\s+)?(?:requests?|tickets?|request numbers?|ticket numbers?)\b"
                r"|\s+\band\s+(?:show|list)\s+(?:them|those)\b"
                r"|^(?:what|which)\s+requests?\s+(?:were|are|was|is)\s+(?:they|them|those)\b"
                r"|^(?:show|list)\s+(?:the\s+)?(?:requests?|tickets?|request numbers?|ticket numbers?)\b"
                r"|^(?:show|list)\s+(?:them|those)\b"
            ),
            cleaned,
        )
        if marker:
            cleaned = cleaned[:marker.start()].strip()
        return cleaned

    def _infer_ticket_metric_filters(self, label: str) -> Dict[str, List[str]]:
        fragment = self._metric_filter_fragment(label)
        if not fragment:
            return {"catalog_item_terms": [], "category_terms": [], "state_terms": []}

        raw_tokens = self._extract_ordered_terms(fragment)
        tokens = [self._singularize_term(token) for token in raw_tokens]
        state_terms = [
            self._canonical_ticket_state(token)
            for token in tokens
            if token in TICKET_STATE_TERMS
        ]
        non_state_tokens = [token for token in tokens if token not in TICKET_STATE_TERMS]
        if state_terms and not non_state_tokens:
            return {
                "catalog_item_terms": [],
                "category_terms": [],
                "state_terms": self._dedupe_terms(state_terms),
            }

        if len(tokens) == 1 and tokens[0] != fragment:
            fragment = tokens[0]

        terms = [fragment]
        if len(tokens) <= 1:
            terms.extend(tokens)
            terms.extend(self._expand_alias_terms(tokens or [fragment]))
        terms = self._dedupe_terms(terms)

        if self._is_broad_ticket_category_fragment(fragment, tokens):
            return {"catalog_item_terms": [], "category_terms": terms, "state_terms": []}
        return {"catalog_item_terms": terms, "category_terms": [], "state_terms": []}

    def _split_specific_item_terms(self, category_terms: List[str]) -> Dict[str, List[str]]:
        catalog_item_terms: List[str] = []
        broad_category_terms: List[str] = []
        for term in category_terms:
            tokens = self._extract_ordered_terms(term)
            if self._is_specific_ticket_item_fragment(term, tokens):
                catalog_item_terms.append(term)
            else:
                broad_category_terms.append(term)
        return {
            "catalog_item_terms": self._dedupe_terms(catalog_item_terms),
            "category_terms": self._dedupe_terms(broad_category_terms),
        }

    def _is_specific_ticket_item_fragment(self, fragment: str, tokens: Optional[List[str]] = None) -> bool:
        tokens = tokens if tokens is not None else self._extract_ordered_terms(fragment)
        if len(tokens) <= 1:
            return False
        return not all(token in BROAD_TICKET_CATEGORY_TERMS for token in tokens)

    def _is_broad_ticket_category_fragment(self, fragment: str, tokens: Optional[List[str]] = None) -> bool:
        tokens = tokens if tokens is not None else self._extract_ordered_terms(fragment)
        if len(tokens) != 1:
            return False
        return tokens[0] in BROAD_TICKET_CATEGORY_TERMS

    def _metric_filter_fragment(self, label: str) -> str:
        cleaned = self._clean_ticket_metric_label(label)
        if not cleaned:
            return ""
        cleaned = re.sub(r"\b(?:total|matching|match|count|counts|number|numbers)\b", " ", cleaned)
        cleaned = re.sub(r"\b(?:service|services|change|changes|request|requests|ticket|tickets)\b", " ", cleaned)
        cleaned = re.sub(r"\b(?:made|submitted|created|raised|logged|requested|have|has|did|make)\b", " ", cleaned)
        cleaned = re.sub(r"\b(?:of|those|them|that|these|are|were|was|is|the|a|an)\b", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _clean_ticket_metric_label(self, label: str) -> str:
        return re.sub(r"\s+", " ", str(label or "").strip().lower()).strip(" ?!.,")

    def _canonical_ticket_state(self, state: str) -> str:
        normalized = self._clean_ticket_metric_label(state)
        return TICKET_STATE_CANONICAL.get(normalized, normalized)

    def _metric_is_total(self, label: str) -> bool:
        cleaned = self._clean_ticket_metric_label(label)
        if "matching" in cleaned:
            return False
        return not self._metric_filter_fragment(cleaned) or "total" in cleaned

    def _metric_key(self, label: str) -> str:
        return self._clean_ticket_metric_label(label)

    def _default_ticket_metric_label(
        self,
        ticket_type: Optional[str],
        state_terms: Optional[List[str]] = None,
    ) -> str:
        state_prefix = " ".join(self._dedupe_terms(state_terms or []))
        noun = "change requests" if ticket_type == "CH" else "requests"
        if state_prefix:
            return f"{state_prefix} {noun}"
        return f"total {noun}"

    def _ticket_metric_display_label(self, fragment: str, ticket_type: Optional[str]) -> str:
        cleaned = self._metric_filter_fragment(fragment)
        noun = "change requests" if ticket_type == "CH" else "requests"
        return f"{cleaned} {noun}" if cleaned else self._default_ticket_metric_label(ticket_type)

    def _format_metric_label(self, label: str) -> str:
        cleaned = self._clean_ticket_metric_label(label)
        if not cleaned:
            cleaned = "matching requests"
        return cleaned[:1].upper() + cleaned[1:]

    async def _build_retrieval_plan(self, query: str) -> GraphRAGRetrievalPlan:
        entities = await self._extract_entities(query)
        heuristic = self._heuristic_plan(query, entities)

        try:
            async_client = get_async_client()
            completion = await asyncio.wait_for(
                async_client.beta.chat.completions.parse(
                    model=settings.openai_chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You create safe ServiceNow graph retrieval plans. Return JSON only.",
                        },
                        {
                            "role": "user",
                            "content": INTENT_PLAN_PROMPT.format(query=query),
                        },
                    ],
                    temperature=0.1,
                    response_format=GraphRAGRetrievalPlan,
                ),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("OpenAI did not return a parsed GraphRAG retrieval plan")
            return self._merge_plan_with_heuristic(parsed, heuristic)
        except Exception:
            logger.exception("[GRAPHRAG] Retrieval planning failed, using heuristic plan")
            return self._normalize_plan(heuristic)

    async def _extract_entities(self, query: str) -> GraphRAGEntities:
        try:
            async_client = get_async_client()
            completion = await asyncio.wait_for(
                async_client.beta.chat.completions.parse(
                    model=settings.openai_chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You extract structured ServiceNow graph retrieval terms. Return JSON only.",
                        },
                        {
                            "role": "user",
                            "content": ENTITY_EXTRACTION_PROMPT.format(query=query),
                        },
                    ],
                    temperature=0.1,
                    response_format=GraphRAGEntities,
                ),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("OpenAI did not return parsed GraphRAG entities")
            return self._normalize_entities(parsed)
        except Exception:
            logger.exception("[GRAPHRAG] Entity extraction failed")
            return GraphRAGEntities()

    def _merge_plan_with_heuristic(
        self,
        parsed: GraphRAGRetrievalPlan,
        heuristic: GraphRAGRetrievalPlan,
    ) -> GraphRAGRetrievalPlan:
        domain = parsed.domain
        if heuristic.domain != "catalog" and parsed.domain == "catalog":
            domain = heuristic.domain

        intent_family = parsed.intent_family
        if heuristic.intent_family != "lookup" and parsed.intent_family == "lookup":
            intent_family = heuristic.intent_family

        entity_scope = parsed.entity_scope
        if heuristic.entity_scope != "item" and parsed.entity_scope == "item":
            entity_scope = heuristic.entity_scope

        answer_style = parsed.answer_style
        if heuristic.answer_style != "concise" and parsed.answer_style == "concise":
            answer_style = heuristic.answer_style

        merged = GraphRAGRetrievalPlan(
            domain=domain,
            intent_family=intent_family,
            entity_scope=entity_scope,
            answer_style=answer_style,
            named_entities=parsed.named_entities or heuristic.named_entities,
            category_terms=parsed.category_terms or heuristic.category_terms,
            catalog_terms=parsed.catalog_terms or heuristic.catalog_terms,
            table_terms=parsed.table_terms or heuristic.table_terms,
            field_terms=parsed.field_terms or heuristic.field_terms,
            concept_terms=parsed.concept_terms or heuristic.concept_terms,
            sort_field=parsed.sort_field or heuristic.sort_field,
            sort_direction=parsed.sort_direction or heuristic.sort_direction,
            aggregation=parsed.aggregation or heuristic.aggregation,
            limit=parsed.limit or heuristic.limit,
        )
        return self._normalize_plan(merged)

    async def _expand_plan_with_semantics(self, query: str, plan: GraphRAGRetrievalPlan) -> GraphRAGRetrievalPlan:
        intent = parse_semantic_intent(query)

        catalog_terms = list(plan.catalog_terms)
        table_terms = list(plan.table_terms)
        field_terms = list(plan.field_terms)
        concept_terms = list(plan.concept_terms)
        category_terms = list(plan.category_terms)
        named_entities = list(plan.named_entities)

        primary_intent = intent.get("primary_intent")
        if primary_intent and primary_intent != "general_request":
            concept_terms.append(primary_intent)
        category_terms.extend(intent.get("categories", []))
        concept_terms.extend(intent.get("keywords", []))

        if neo4j_catalog_graph_service.is_available() and plan.domain == "catalog":
            graph_hints = neo4j_catalog_graph_service.suggest_search_keywords(
                query,
                catalog_terms + field_terms + category_terms + concept_terms,
                limit=6,
            )
            concept_terms.extend(graph_hints)

        try:
            expansion_terms = await asyncio.wait_for(
                query_expansion.expand_query(query),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            concept_terms.extend(expansion_terms)
        except Exception:
            logger.exception("[GRAPHRAG] Query expansion failed")

        try:
            semantic_results, _ = await asyncio.wait_for(
                asyncio.to_thread(semantic_search, query, graph_service=neo4j_catalog_graph_service),
                timeout=GRAPH_RAG_RESOLVER_TIMEOUT_SECONDS,
            )
            for item in semantic_results[:5]:
                matched_terms = item.get("matched_terms") or []
                if isinstance(matched_terms, list):
                    concept_terms.extend(str(term) for term in matched_terms)
                category = item.get("category")
                if category:
                    category_terms.append(str(category))
                name = str(item.get("name") or "").strip().lower()
                if name:
                    named_entities.append(name)
                    if not catalog_terms:
                        catalog_terms.extend(self._extract_ordered_terms(name))
        except Exception:
            logger.exception("[GRAPHRAG] Semantic expansion failed")

        concept_terms.extend(self._expand_alias_terms(catalog_terms + category_terms + field_terms + concept_terms))

        return self._normalize_plan(
            GraphRAGRetrievalPlan(
                domain=plan.domain,
                intent_family=plan.intent_family,
                entity_scope=plan.entity_scope,
                answer_style=plan.answer_style,
                named_entities=named_entities,
                category_terms=category_terms,
                catalog_terms=catalog_terms,
                table_terms=table_terms,
                field_terms=field_terms,
                concept_terms=concept_terms,
                sort_field=plan.sort_field,
                sort_direction=plan.sort_direction,
                aggregation=plan.aggregation,
                limit=plan.limit,
            )
        )

    async def _build_cypher_blueprint(self, query: str, plan: GraphRAGRetrievalPlan) -> GraphRAGCypherBlueprint:
        default_blueprint = self._default_cypher_blueprint(query, plan)

        try:
            async_client = get_async_client()
            completion = await asyncio.wait_for(
                async_client.beta.chat.completions.parse(
                    model=settings.openai_chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You compose safe read-only Cypher blueprints for an existing ServiceNow graph. Return JSON only.",
                        },
                        {
                            "role": "user",
                            "content": CYPHER_BLUEPRINT_PROMPT.format(query=query),
                        },
                    ],
                    temperature=0.1,
                    response_format=GraphRAGCypherBlueprint,
                ),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise RuntimeError("OpenAI did not return a parsed Cypher blueprint")
            return self._normalize_cypher_blueprint(parsed, fallback=default_blueprint)
        except Exception:
            logger.exception("[GRAPHRAG] Cypher blueprint planning failed, using deterministic blueprint")
            return default_blueprint

    def _default_cypher_blueprint(self, query: str, plan: GraphRAGRetrievalPlan) -> GraphRAGCypherBlueprint:
        subject_terms = self._dedupe_terms(
            plan.named_entities + plan.category_terms + plan.catalog_terms + plan.concept_terms
        )
        state_terms = self._resolve_ticket_state_filters(query, plan)
        ticket_numbers = [
            term for term in self._dedupe_terms(plan.named_entities + self._extract_ordered_terms(query))
            if re.fullmatch(r"(req|chg|ritm)\d+", term)
        ]

        if plan.domain == "ticket":
            return GraphRAGCypherBlueprint(
                domain="ticket",
                goal=plan.intent_family,
                primary_label="Ticket" if plan.entity_scope != "request_item" else "RequestItem",
                cypher_query="""
MATCH (ticket:Ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
WITH ticket, item, catalog
WHERE ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
  AND (size($state_terms) = 0 OR ANY(term IN $state_terms WHERE (toLower(coalesce(ticket.state_display, '')) CONTAINS term OR toLower(coalesce(ticket.state, '')) CONTAINS term)))
  AND (size($ticket_numbers) = 0 OR ANY(term IN $ticket_numbers WHERE (toLower(coalesce(ticket.number, '')) CONTAINS term OR toLower(coalesce(item.number, '')) CONTAINS term)))
  AND (size($subject_terms) = 0 OR ANY(term IN $subject_terms WHERE
        (toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
        toLower(coalesce(ticket.description, '')) CONTAINS term OR
        toLower(coalesce(item.short_description, '')) CONTAINS term OR
        toLower(coalesce(item.description, '')) CONTAINS term OR
        toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
        toLower(coalesce(catalog.name, '')) CONTAINS term)))
RETURN ticket, item, catalog
ORDER BY coalesce(ticket.opened_at, '') DESC
LIMIT $limit
""".strip(),
                relationship_chain=["HAS_ITEM", "FULFILLS_CATALOG_ITEM"],
                subject_terms=subject_terms,
                field_terms=list(plan.field_terms),
                table_terms=[],
                state_terms=state_terms,
                ticket_numbers=ticket_numbers,
                sort_field=plan.sort_field,
                sort_direction=plan.sort_direction,
                requires_ticket_history=self._is_ticket_activity_query(query.lower()),
            )

        if plan.domain == "schema":
            return GraphRAGCypherBlueprint(
                domain="schema",
                goal=plan.intent_family,
                primary_label="Column" if plan.entity_scope == "column" else "Table",
                cypher_query="""
MATCH (table:Table)
OPTIONAL MATCH (table)-[:HAS_COLUMN]->(column:Column)
WITH table, column
WHERE (size($table_terms) = 0 OR ANY(term IN $table_terms WHERE
        (toLower(coalesce(table.name, '')) CONTAINS term OR
        toLower(coalesce(table.label, '')) CONTAINS term)))
  AND (size($field_terms) = 0 OR ANY(term IN $field_terms WHERE
        (toLower(coalesce(column.name, '')) CONTAINS term OR
        toLower(coalesce(column.label, '')) CONTAINS term)))
RETURN table, column
ORDER BY coalesce(table.label, table.name) ASC
LIMIT $limit
""".strip(),
                relationship_chain=["HAS_COLUMN"],
                subject_terms=[],
                field_terms=list(plan.field_terms),
                table_terms=self._dedupe_terms(plan.table_terms + plan.named_entities),
                state_terms=[],
                ticket_numbers=[],
                sort_field=plan.sort_field,
                sort_direction=plan.sort_direction,
                requires_ticket_history=False,
            )

        primary_label = "CatalogChoice" if plan.entity_scope == "choice" else "CatalogVariable" if plan.entity_scope == "variable" else "CatalogItem"
        return GraphRAGCypherBlueprint(
            domain="catalog",
            goal=plan.intent_family,
            primary_label=primary_label,
            cypher_query="""
MATCH (item:CatalogItem)
OPTIONAL MATCH (item)-[:USES_SCHEMA]->(schema:FilterSchema)
OPTIONAL MATCH (schema)-[:HAS_FIELD]->(variable:CatalogVariable)
OPTIONAL MATCH (variable)-[:HAS_CHOICE]->(choice:CatalogChoice)
WITH item, schema, variable, choice
WHERE (size($subject_terms) = 0 OR ANY(term IN $subject_terms WHERE
        (toLower(coalesce(item.name, '')) CONTAINS term OR
        toLower(coalesce(item.short_description, '')) CONTAINS term OR
        toLower(coalesce(item.description, '')) CONTAINS term OR
        toLower(coalesce(item.category, '')) CONTAINS term)))
  AND (size($field_terms) = 0 OR ANY(term IN $field_terms WHERE
        (toLower(coalesce(variable.name, '')) CONTAINS term OR
        toLower(coalesce(variable.label, '')) CONTAINS term OR
        toLower(coalesce(choice.label, '')) CONTAINS term OR
        toLower(coalesce(choice.value, '')) CONTAINS term)))
RETURN item, schema, variable, choice
ORDER BY coalesce(item.name, '') ASC
LIMIT $limit
""".strip(),
            relationship_chain=["USES_SCHEMA", "HAS_FIELD", "HAS_CHOICE"],
            subject_terms=subject_terms,
            field_terms=list(plan.field_terms),
            table_terms=[],
            state_terms=[],
            ticket_numbers=[],
            sort_field=plan.sort_field,
            sort_direction=plan.sort_direction,
            requires_ticket_history=False,
        )

    def _normalize_cypher_blueprint(
        self,
        blueprint: GraphRAGCypherBlueprint,
        fallback: GraphRAGCypherBlueprint,
    ) -> GraphRAGCypherBlueprint:
        cypher_query = str(blueprint.cypher_query or "").strip()
        if not cypher_query:
            cypher_query = fallback.cypher_query
        lowered_query = cypher_query.lower()
        if any(keyword in lowered_query for keyword in (" create ", " merge ", " delete ", " set ", " call ", " apoc.", " remove ")):
            cypher_query = fallback.cypher_query

        domain = blueprint.domain
        if fallback.domain != "catalog" and blueprint.domain == "catalog":
            domain = fallback.domain

        goal = blueprint.goal
        if fallback.goal != "lookup" and blueprint.goal == "lookup":
            goal = fallback.goal

        primary_label = blueprint.primary_label
        if fallback.primary_label != "CatalogItem" and blueprint.primary_label == "CatalogItem":
            primary_label = fallback.primary_label

        return GraphRAGCypherBlueprint(
            domain=domain,
            goal=goal,
            primary_label=primary_label,
            cypher_query=cypher_query,
            relationship_chain=self._dedupe_terms(blueprint.relationship_chain or fallback.relationship_chain),
            subject_terms=self._dedupe_terms(blueprint.subject_terms or fallback.subject_terms),
            field_terms=self._dedupe_terms(blueprint.field_terms or fallback.field_terms),
            table_terms=self._dedupe_terms(blueprint.table_terms or fallback.table_terms),
            state_terms=self._dedupe_terms(blueprint.state_terms or fallback.state_terms),
            ticket_numbers=self._dedupe_terms(blueprint.ticket_numbers or fallback.ticket_numbers),
            sort_field=blueprint.sort_field or fallback.sort_field,
            sort_direction=blueprint.sort_direction or fallback.sort_direction,
            requires_ticket_history=bool(blueprint.requires_ticket_history or fallback.requires_ticket_history),
        )

    def _merge_plan_with_cypher_blueprint(
        self,
        plan: GraphRAGRetrievalPlan,
        blueprint: GraphRAGCypherBlueprint,
    ) -> GraphRAGRetrievalPlan:
        domain = blueprint.domain or plan.domain
        intent_family = blueprint.goal or plan.intent_family
        entity_scope = plan.entity_scope

        if domain == "ticket":
            entity_scope = "ticket_wide" if intent_family in {"count", "list", "rank", "filter", "aggregate"} else "ticket"
        elif domain == "schema":
            entity_scope = "column" if blueprint.primary_label == "Column" else "schema_wide" if intent_family in {"count", "list"} else "table"
        elif domain == "catalog":
            if blueprint.primary_label == "CatalogChoice":
                entity_scope = "choice"
            elif blueprint.primary_label == "CatalogVariable":
                entity_scope = "variable"
            else:
                entity_scope = "catalog_wide" if intent_family in {"count", "list", "rank", "filter", "aggregate"} else "item"

        named_entities = list(plan.named_entities)
        category_terms = list(plan.category_terms)
        catalog_terms = list(plan.catalog_terms)
        table_terms = list(plan.table_terms)
        field_terms = list(plan.field_terms)
        concept_terms = list(plan.concept_terms)

        if domain == "ticket":
            named_entities.extend(blueprint.ticket_numbers)
            concept_terms.extend(blueprint.subject_terms)
            category_terms.extend(blueprint.state_terms)
        elif domain == "schema":
            table_terms.extend(blueprint.table_terms)
            field_terms.extend(blueprint.field_terms)
            concept_terms.extend(blueprint.subject_terms)
        else:
            catalog_terms.extend(blueprint.subject_terms)
            field_terms.extend(blueprint.field_terms)
            concept_terms.extend(blueprint.subject_terms)

        return self._normalize_plan(
            GraphRAGRetrievalPlan(
                domain=domain,
                intent_family=intent_family,
                entity_scope=entity_scope,
                answer_style=plan.answer_style,
                named_entities=named_entities,
                category_terms=category_terms,
                catalog_terms=catalog_terms,
                table_terms=table_terms,
                field_terms=field_terms,
                concept_terms=concept_terms,
                sort_field=blueprint.sort_field or plan.sort_field,
                sort_direction=blueprint.sort_direction or plan.sort_direction,
                aggregation=plan.aggregation,
                limit=plan.limit,
            )
        )

    def _build_cypher_context(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        blueprint: GraphRAGCypherBlueprint,
    ) -> Dict[str, Any]:
        mapped_variables = {
            "subject_terms": self._dedupe_terms(
                blueprint.subject_terms +
                plan.named_entities +
                plan.category_terms +
                plan.catalog_terms +
                plan.concept_terms +
                self._expand_alias_terms(blueprint.subject_terms + plan.category_terms + plan.catalog_terms + plan.concept_terms)
            ),
            "field_terms": self._dedupe_terms(blueprint.field_terms + plan.field_terms),
            "table_terms": self._dedupe_terms(blueprint.table_terms + plan.table_terms),
            "state_terms": self._dedupe_terms(blueprint.state_terms + self._resolve_ticket_state_filters(query, plan)),
            "ticket_numbers": self._dedupe_terms(
                blueprint.ticket_numbers +
                [term for term in plan.named_entities if re.fullmatch(r"(req|chg|ritm)\d+", term)]
            ),
            "limit": [str(plan.limit)],
        }
        return {
            "domain": blueprint.domain,
            "goal": blueprint.goal,
            "primary_label": blueprint.primary_label,
            "template": blueprint.cypher_query,
            "mapped_variables": mapped_variables,
            "rendered_query": self._render_cypher_query(blueprint.cypher_query, mapped_variables),
            "relationship_chain": blueprint.relationship_chain,
        }

    def _render_cypher_query(self, template: str, mapped_variables: Dict[str, List[str]]) -> str:
        rendered = str(template or "")
        for key, values in mapped_variables.items():
            if key == "limit":
                rendered = rendered.replace(f"${key}", values[0] if values else "5")
            else:
                rendered = rendered.replace(f"${key}", json.dumps(values, ensure_ascii=True))
        rendered = rendered.replace("$ticket_type", "null")
        return rendered

    def _default_graph_query_program(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
    ) -> GraphRAGCypherProgram:
        if self._should_use_deterministic_ticket_count_program(query, plan):
            return self._build_ticket_count_graph_query_program(query, plan)

        blueprint = self._default_cypher_blueprint(query, plan)
        return GraphRAGCypherProgram(
            intent_summary=f"{plan.domain}:{plan.intent_family}",
            answer_style=plan.answer_style,
            cypher_query=blueprint.cypher_query,
            expected_columns=[],
            subject_terms=self._dedupe_terms(blueprint.subject_terms),
            field_terms=self._dedupe_terms(blueprint.field_terms),
            table_terms=self._dedupe_terms(blueprint.table_terms),
            state_terms=self._dedupe_terms(blueprint.state_terms),
            ticket_numbers=self._dedupe_terms(blueprint.ticket_numbers),
            requester_terms=[],
            segment_one_terms=[],
            segment_two_terms=[],
            segment_one_label=None,
            segment_two_label=None,
            limit=max(1, min(plan.limit or 5, 25)),
        )

    def _normalize_graph_query_program(
        self,
        program: GraphRAGCypherProgram,
        fallback: GraphRAGCypherProgram,
    ) -> GraphRAGCypherProgram:
        return GraphRAGCypherProgram(
            intent_summary=str(program.intent_summary or fallback.intent_summary),
            answer_style=program.answer_style or fallback.answer_style,
            cypher_query=str(program.cypher_query or fallback.cypher_query).strip(),
            expected_columns=self._dedupe_terms(program.expected_columns or fallback.expected_columns),
            subject_terms=self._dedupe_terms(program.subject_terms or fallback.subject_terms),
            field_terms=self._dedupe_terms(program.field_terms or fallback.field_terms),
            table_terms=self._dedupe_terms(program.table_terms or fallback.table_terms),
            state_terms=self._dedupe_terms(program.state_terms or fallback.state_terms),
            ticket_numbers=self._dedupe_terms(program.ticket_numbers or fallback.ticket_numbers),
            requester_terms=self._dedupe_terms(program.requester_terms or fallback.requester_terms),
            segment_one_terms=self._dedupe_terms(program.segment_one_terms or fallback.segment_one_terms),
            segment_two_terms=self._dedupe_terms(program.segment_two_terms or fallback.segment_two_terms),
            segment_one_label=str(program.segment_one_label or fallback.segment_one_label or "").strip() or None,
            segment_two_label=str(program.segment_two_label or fallback.segment_two_label or "").strip() or None,
            limit=max(1, min(int(program.limit or fallback.limit or 15), 50)),
        )

    def _build_graph_query_parameters(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        program: GraphRAGCypherProgram,
    ) -> Dict[str, Any]:
        if self._should_use_deterministic_ticket_count_program(query, plan):
            raw_subject_terms = self._dedupe_terms(program.subject_terms)
        else:
            raw_subject_terms = self._dedupe_terms(
                program.subject_terms
                + plan.named_entities
                + self._extract_phrase_candidates(query)[:6]
                + self._extract_ordered_terms(query)[:10]
            )
        state_terms = self._dedupe_terms(program.state_terms + self._resolve_ticket_state_filters(query, plan))
        ticket_numbers = self._dedupe_terms(
            program.ticket_numbers + [term for term in self._extract_ordered_terms(query) if re.fullmatch(r"(req|chg|ritm)\d+", term)]
        )

        if plan.domain == "ticket" and self._should_use_deterministic_ticket_count_program(query, plan):
            subject_terms = raw_subject_terms
        elif plan.domain == "ticket":
            subject_terms = self._build_specific_ticket_terms(query, plan, state_terms) or raw_subject_terms
        else:
            subject_terms = raw_subject_terms

        return {
            "subject_terms": subject_terms,
            "field_terms": self._dedupe_terms(program.field_terms + plan.field_terms),
            "table_terms": self._dedupe_terms(program.table_terms + plan.table_terms),
            "state_terms": state_terms,
            "ticket_numbers": ticket_numbers,
            "ticket_type": self._resolve_ticket_type_filter(plan, query),
            "requester_terms": self._dedupe_terms(program.requester_terms),
            "segment_one_terms": self._dedupe_terms(program.segment_one_terms),
            "segment_two_terms": self._dedupe_terms(program.segment_two_terms),
            "limit": max(1, min(int(program.limit or plan.limit or 15), 50)),
        }

    def _render_compiled_rows_deterministically(
        self,
        query: str,
        program: GraphRAGCypherProgram,
        rows: List[Dict[str, Any]],
    ) -> Optional[str]:
        if not rows:
            return None

        first_row = rows[0]
        columns = list(first_row.keys())
        query_lower = query.lower()
        wants_list = any(token in query_lower for token in ("list", "show", "what are", "which", "names", "numbers"))
        wants_names_only = "name" in query_lower and "description" not in query_lower and "number" not in query_lower

        if "total_count" in columns:
            lines = [f"Total requests: {int(first_row.get('total_count') or 0)}"]
            if "segment_one_count" in columns and program.segment_one_label:
                lines.append(f"{program.segment_one_label}: {int(first_row.get('segment_one_count') or 0)}")
            if "segment_two_count" in columns and program.segment_two_label:
                lines.append(f"{program.segment_two_label}: {int(first_row.get('segment_two_count') or 0)}")
            return "\n".join(lines)

        if len(columns) == 1 and wants_list:
            column = columns[0]
            values = [row.get(column) for row in rows if row.get(column) not in (None, "")]
            if values:
                return "\n".join(f"- {value}" for value in values)

        if wants_names_only:
            name_column = next((column for column in columns if column.endswith("name") or column == "name"), None)
            if name_column:
                values = [row.get(name_column) for row in rows if row.get(name_column)]
                if values:
                    return "\n".join(f"- {value}" for value in values)

        if "number" in columns and "description" in columns:
            return "\n".join(
                f"- {row.get('number')}: {row.get('description') or 'No description available.'}"
                for row in rows
                if row.get("number")
            )

        return None

    def _render_compiled_rows_fallback(self, rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return NO_CONTEXT_ANSWER

        first_row = rows[0]
        columns = list(first_row.keys())
        if len(columns) == 1:
            column = columns[0]
            return "\n".join(
                f"- {row.get(column)}"
                for row in rows
                if row.get(column) not in (None, "")
            )

        lines = []
        for row in rows[:20]:
            parts = [f"{key}={value}" for key, value in row.items() if value not in (None, "")]
            if parts:
                lines.append("- " + ", ".join(parts))
        return "\n".join(lines) if lines else INSUFFICIENT_CONTEXT_ANSWER

    def _heuristic_plan(self, query: str, entities: GraphRAGEntities) -> GraphRAGRetrievalPlan:
        query_lower = query.lower()
        ordered_terms = self._extract_ordered_terms(query)
        phrases = self._extract_phrase_candidates(query)

        domain: Literal["catalog", "schema", "ticket"] = "catalog"
        intent_family: GraphRAGRetrievalPlan.model_fields["intent_family"].annotation = "lookup"
        entity_scope: GraphRAGRetrievalPlan.model_fields["entity_scope"].annotation = "item"
        answer_style: GraphRAGRetrievalPlan.model_fields["answer_style"].annotation = "concise"
        sort_field: Optional[str] = None
        sort_direction: Literal["asc", "desc"] = "asc"
        aggregation: Optional[str] = None
        limit = 5

        if self._is_ticket_activity_query(query_lower):
            domain = "ticket"
            entity_scope = "ticket"
        elif any(token in query_lower for token in ("ticket", "tickets", "change request", "service request", "change requests", "service requests")):
            domain = "ticket"
            entity_scope = "ticket"
        elif re.search(r"\b(req|chg|ritm)\d+\b", query_lower):
            domain = "ticket"
            entity_scope = "ticket"
        elif any(token in query_lower for token in ("schema", "table", "tables", "column", "columns")):
            domain = "schema"
            entity_scope = "column" if any(token in query_lower for token in ("column", "columns")) else "table"

        if any(token in query_lower for token in ("compare", "difference", "versus", " vs ", "better than")):
            intent_family = "compare"
            answer_style = "comparison_summary"
            limit = 3
        elif any(token in query_lower for token in ("how many", "count", "number of")):
            intent_family = "count"
            aggregation = "count"
        elif self._is_ticket_identifier_query(query_lower):
            intent_family = "list"
            answer_style = "enumerated_list"
            limit = 100
        elif any(token in query_lower for token in ("list", "show", "which", "available")):
            intent_family = "list"
            answer_style = "enumerated_list"
        elif any(phrase in query_lower for phrase in ("most requested", "least requested", "most popular", "least popular", "most used", "least used", "frequently requested")):
            intent_family = "rank"
            answer_style = "concise"
            sort_field = "request_count"
            sort_direction = "asc" if any(phrase in query_lower for phrase in ("least requested", "least popular", "least used")) else "desc"
            aggregation = "min" if sort_direction == "asc" else "max"
        elif any(token in query_lower for token in ("cheapest", "lowest", "least expensive", "most expensive", "highest price", "top", "latest", "recent", "newest")):
            intent_family = "rank"
            answer_style = "concise"
            if domain == "ticket" and any(token in query_lower for token in ("latest", "recent", "newest")):
                sort_field = "opened_at"
                sort_direction = "desc"
                aggregation = "max"
            else:
                sort_field = "price"
                sort_direction = "asc" if any(token in query_lower for token in ("cheapest", "lowest", "least")) else "desc"
                aggregation = "min" if sort_direction == "asc" else "max"
        elif any(token in query_lower for token in ("required", "mandatory", "field", "fields", "variable", "variables")):
            intent_family = "lookup"
            entity_scope = "variable" if domain == "catalog" else entity_scope
        elif any(token in query_lower for token in ("option", "options", "choice", "choices")):
            intent_family = "lookup"
            entity_scope = "choice" if domain == "catalog" else entity_scope
        elif any(token in query_lower for token in ("tell me about", "explain", "describe")):
            intent_family = "explain"

        catalog_terms = list(entities.catalog_terms)
        table_terms = list(entities.table_terms)
        field_terms = list(entities.field_terms)
        concept_terms = list(entities.concept_terms)
        named_entities = entities.catalog_terms + entities.table_terms
        category_terms: List[str] = []

        if domain == "catalog" and not catalog_terms:
            catalog_terms.extend(phrases[:4])
            category_terms.extend(ordered_terms[:6])
        if domain == "schema" and not table_terms:
            table_terms.extend(phrases[:4] or ordered_terms[:4])
        if domain == "ticket":
            named_entities.extend([token for token in ordered_terms if re.fullmatch(r"(req|chg|ritm)\d+", token)])
            category_terms.extend([token for token in ordered_terms if token in {"open", "closed", "pending", "resolved", "change", "service", "vpn", "laptop", "macbook"}])
        if entity_scope in {"variable", "choice", "column"}:
            field_terms.extend(ordered_terms[:8])

        concept_terms.extend(ordered_terms[:10])

        if domain == "catalog" and any(token in ordered_terms for token in ("software", "hardware", "laptop", "vpn", "access", "macbook", "phone", "mobile")):
            category_terms.extend([token for token in ordered_terms if token in {"software", "hardware", "laptop", "vpn", "access", "macbook", "phone", "mobile"}])

        if domain == "schema" and intent_family in {"list", "count"} and entity_scope == "table":
            entity_scope = "schema_wide"
        if domain == "catalog" and intent_family in {"list", "count", "rank", "filter"} and entity_scope == "item":
            entity_scope = "catalog_wide"
        if domain == "ticket" and intent_family in {"list", "count", "rank", "filter"}:
            entity_scope = "ticket_wide"

        return self._normalize_plan(
            GraphRAGRetrievalPlan(
                domain=domain,
                intent_family=intent_family,
                entity_scope=entity_scope,
                answer_style=answer_style,
                named_entities=named_entities,
                category_terms=category_terms,
                catalog_terms=catalog_terms,
                table_terms=table_terms,
                field_terms=field_terms,
                concept_terms=concept_terms,
                sort_field=sort_field,  # type: ignore[arg-type]
                sort_direction=sort_direction,
                aggregation=aggregation,  # type: ignore[arg-type]
                limit=limit,
            )
        )

    async def _execute_plan(self, query: str, plan: GraphRAGRetrievalPlan, user_context: Dict[str, Any]) -> Dict[str, Any]:
        if plan.domain == "ticket":
            return await self._execute_ticket_plan(query, plan, user_context)
        if plan.domain == "schema":
            return self._execute_schema_plan(query, plan)
        return await self._execute_catalog_plan(query, plan)

    async def _execute_catalog_plan(self, query: str, plan: GraphRAGRetrievalPlan) -> Dict[str, Any]:
        candidates = await self._search_catalog_candidates(query, plan)
        if plan.sort_field == "request_count":
            candidates = self._attach_catalog_request_counts(query, plan, candidates)

        if not candidates and plan.intent_family in {"list", "count", "rank", "aggregate"}:
            candidates = self._fallback_catalog_wide_candidates(query, plan)
            if plan.sort_field == "request_count":
                candidates = self._attach_catalog_request_counts(query, plan, candidates)

        if not candidates:
            return {"answer": NO_CONTEXT_ANSWER, "context": [], "candidate_count": 0}

        candidate_count = len(candidates)
        if plan.entity_scope in {"variable", "choice"} or plan.intent_family in {"lookup", "explain", "compare"}:
            context = self._materialize_catalog_contexts(candidates, limit=max(plan.limit, 3))
        else:
            context = [self._normalize_catalog_record(candidate) for candidate in candidates[: max(plan.limit, 5)]]

        summary = self._build_catalog_summary(query, plan, candidates, context)
        if plan.intent_family == "count":
            search_terms = self._build_catalog_search_terms(query, plan)
            summary["count"] = neo4j_catalog_graph_service.count_catalog_items(search_terms)
        answer = self._maybe_answer_deterministically(plan, summary)
        return {
            "answer": answer,
            "candidate_count": candidate_count,
            "context": context,
            "summary": summary,
        }

    def _execute_schema_plan(self, query: str, plan: GraphRAGRetrievalPlan) -> Dict[str, Any]:
        if not neo4j_schema_graph_service.is_available():
            return {"answer": NO_CONTEXT_ANSWER, "context": [], "candidate_count": 0}

        terms = self._dedupe_terms(plan.table_terms + plan.field_terms + plan.concept_terms + plan.named_entities)
        hits = neo4j_schema_graph_service.search_tables(terms) if terms else []
        if not hits:
            return {"answer": NO_CONTEXT_ANSWER, "context": [], "candidate_count": 0}

        candidate_count = len(hits)
        detailed_context = []
        for hit in hits[: max(plan.limit, 3)]:
            identifier = str(hit.get("sys_id") or hit.get("name") or "").strip()
            details = neo4j_schema_graph_service.get_table_details(identifier)
            if details:
                detailed_context.append(self._normalize_schema_context(details))

        if not detailed_context:
            return {"answer": NO_CONTEXT_ANSWER, "context": [], "candidate_count": candidate_count}

        summary = self._build_schema_summary(query, plan, hits, detailed_context)
        if plan.intent_family == "count":
            summary["count"] = neo4j_schema_graph_service.count_tables(terms)
        answer = self._maybe_answer_deterministically(plan, summary)
        return {
            "answer": answer,
            "candidate_count": candidate_count,
            "context": detailed_context,
            "summary": summary,
        }

    async def _execute_ticket_plan(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        user_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not neo4j_ticket_graph_service.is_available():
            return {"answer": NO_CONTEXT_ANSWER, "context": [], "candidate_count": 0}

        # Temporary admin-style visibility for the GraphRAG query panel:
        # ticket questions should search across the full ingested ticket graph.
        resolved_user_sys_id = None

        ticket_type = self._resolve_ticket_type_filter(plan, query)
        state_filters = self._resolve_ticket_state_filters(query, plan)
        use_semantic_filter = self._ticket_query_needs_semantic_filter(query, plan, state_filters)

        if plan.intent_family == "count":
            result_limit = self._ticket_result_limit(query, plan)
            if use_semantic_filter:
                matched_items = await self._search_ticket_candidates(
                    query,
                    plan,
                    resolved_user_sys_id,
                    ticket_type=ticket_type,
                    state_filters=state_filters,
                )
                count = len(matched_items)
                items = matched_items[:result_limit]
            else:
                count = neo4j_ticket_graph_service.count_tickets_for_user(
                    resolved_user_sys_id,
                    ticket_type=ticket_type,
                    states=state_filters,
                )
                items = neo4j_ticket_graph_service.list_tickets_for_user(
                    resolved_user_sys_id,
                    ticket_type=ticket_type,
                    states=state_filters,
                    limit=result_limit,
                )
            context = [self._normalize_ticket_record(item) for item in items]
            summary = {
                "domain": "ticket",
                "intent_family": plan.intent_family,
                "entity_scope": plan.entity_scope,
                "candidate_count": len(items),
                "count": count,
                "tickets": context,
                "include_identifiers": self._is_ticket_identifier_query(query.lower()),
                "include_ticket_descriptions": self._ticket_identifier_wants_descriptions(query.lower()),
            }
            answer = self._maybe_answer_deterministically(plan, summary)
            return {
                "answer": answer,
                "candidate_count": count,
                "context": context,
                "summary": summary,
            }

        candidates = await self._search_ticket_candidates(
            query,
            plan,
            resolved_user_sys_id,
            ticket_type=ticket_type,
            state_filters=state_filters,
        )
        if not candidates and plan.intent_family in {"list", "rank", "filter"}:
            candidates = neo4j_ticket_graph_service.list_tickets_for_user(
                resolved_user_sys_id,
                ticket_type=ticket_type,
                states=state_filters,
                limit=self._ticket_result_limit(query, plan),
            )

        if not candidates:
            return {"answer": NO_CONTEXT_ANSWER, "context": [], "candidate_count": 0}

        normalized_candidates = [self._normalize_ticket_record(candidate) for candidate in candidates]
        context = self._materialize_ticket_contexts(
            normalized_candidates,
            resolved_user_sys_id,
            ticket_type=ticket_type,
            limit=max(plan.limit, 3),
        )
        summary = self._build_ticket_summary(query, plan, normalized_candidates, context)
        answer = self._maybe_answer_deterministically(plan, summary)
        return {
            "answer": answer,
            "candidate_count": len(normalized_candidates),
            "context": context,
            "summary": summary,
        }

    async def _resolve_user_sys_id(self, user_context: Dict[str, Any]) -> Optional[str]:
        direct = str(user_context.get("user_sys_id") or "").strip()
        if direct:
            return direct

        session_id = str(user_context.get("session_id") or "").strip()
        if session_id:
            session = session_service.get_session(session_id)
            if session and session.user_id:
                return str(session.user_id).strip()
            if session and session.user_email:
                user = await asyncio.to_thread(snow.get_user_by_email, session.user_email)
                if user:
                    return str(user.get("sys_id") or "").strip() or None

        email = str(user_context.get("user_email") or "").strip()
        if email:
            user = await asyncio.to_thread(snow.get_user_by_email, email)
            if user:
                return str(user.get("sys_id") or "").strip() or None

        return None

    async def _search_ticket_candidates(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        user_sys_id: Optional[str],
        ticket_type: Optional[str],
        state_filters: List[str],
    ) -> List[Dict[str, Any]]:
        search_terms = self._build_ticket_search_terms(query, plan, state_filters)
        result_limit = self._ticket_result_limit(query, plan)
        candidates = neo4j_ticket_graph_service.search_tickets(
            query,
            keywords=search_terms,
            user_sys_id=user_sys_id,
            limit=result_limit,
        )

        filtered = []
        for candidate in candidates:
            candidate_type = str(candidate.get("ticket_type") or "").upper()
            candidate_state = f"{candidate.get('state_display') or ''} {candidate.get('state') or ''}".lower()
            if ticket_type and candidate_type != ticket_type:
                continue
            if state_filters and not any(state in candidate_state for state in state_filters):
                continue
            filtered.append(candidate)
        anchor_terms = self._extract_ticket_anchor_terms(query, state_filters)
        if anchor_terms:
            anchored = [
                candidate for candidate in filtered
                if self._ticket_candidate_matches_anchor(candidate, anchor_terms)
            ]
            if anchored:
                return anchored
        return filtered

    def _materialize_ticket_contexts(
        self,
        candidates: List[Dict[str, Any]],
        user_sys_id: Optional[str],
        ticket_type: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        context: List[Dict[str, Any]] = []
        seen = set()
        for candidate in candidates[: max(limit, 4)]:
            sys_id = str(candidate.get("sys_id") or "").strip()
            if not sys_id or sys_id in seen:
                continue
            raw = neo4j_ticket_graph_service.get_ticket_context(sys_id, ticket_type=ticket_type, user_sys_id=user_sys_id)
            if raw:
                context.append(self._normalize_ticket_context(raw))
            else:
                context.append({"type": "ticket", "ticket": candidate})
            seen.add(sys_id)
        return context

    def _build_ticket_summary(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        candidates: List[Dict[str, Any]],
        context: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        include_identifiers = self._is_ticket_identifier_query(query.lower())
        include_ticket_descriptions = self._ticket_identifier_wants_descriptions(query.lower())
        ticket_result_limit = self._ticket_result_limit(query, plan)
        summary: Dict[str, Any] = {
            "domain": "ticket",
            "intent_family": plan.intent_family,
            "entity_scope": plan.entity_scope,
            "candidate_count": len(candidates),
            "tickets": candidates[:ticket_result_limit] if include_identifiers else candidates[: max(plan.limit, 10)],
            "include_identifiers": include_identifiers,
            "include_ticket_descriptions": include_ticket_descriptions,
        }

        if plan.intent_family == "list":
            summary["listed_tickets"] = candidates[:ticket_result_limit]
        elif plan.intent_family == "rank":
            ranked = self._rank_ticket_records(candidates, plan)
            summary["ranked_tickets"] = ranked[: plan.limit]
            if ranked:
                summary["winner"] = ranked[0]
        elif plan.intent_family == "compare":
            summary["comparisons"] = context[: max(2, min(plan.limit, 3))]
        else:
            summary["top_match"] = (context or [{"ticket": candidates[0]}])[0] if candidates else None

        return summary

    def _resolve_ticket_type_filter(self, plan: GraphRAGRetrievalPlan, query: str) -> Optional[str]:
        lowered = query.lower()
        terms = set(plan.named_entities + plan.category_terms + plan.concept_terms + self._extract_ordered_terms(query))
        if "change request" in lowered or "change requests" in lowered or "change" in terms:
            return "CH"
        if (
            "service request" in lowered
            or "service requests" in lowered
            or re.search(r"\brequests?\b", lowered)
            or "request" in terms
        ):
            return "SR"
        return None

    def _resolve_ticket_state_filters(self, query: str, plan: GraphRAGRetrievalPlan) -> List[str]:
        lowered = query.lower()
        states = []
        for state in ("open", "pending", "resolved", "closed", "progress", "new"):
            if state in lowered:
                states.append(state)
        return states

    def _is_ticket_activity_query(self, query_lower: str) -> bool:
        if any(token in query_lower for token in ("field", "fields", "variable", "variables", "option", "options", "choice", "choices", "form", "schema", "table", "column")):
            return False

        has_ticket_noun = any(
            phrase in query_lower
            for phrase in (
                "request",
                "requests",
                "ticket",
                "tickets",
                "service request",
                "service requests",
                "change request",
                "change requests",
            )
        )
        if not has_ticket_noun:
            return False

        has_activity_verb = any(
            phrase in query_lower
            for phrase in (
                "were made",
                "was made",
                "made",
                "submitted",
                "created",
                "raised",
                "opened",
                "logged",
                "requested",
            )
        )

        has_count_or_listing_intent = any(
            phrase in query_lower
            for phrase in ("how many", "count", "number of", "list", "show", "which")
        )

        return has_activity_verb or has_count_or_listing_intent or self._is_ticket_identifier_query(query_lower)

    def _is_ticket_identifier_query(self, query_lower: str) -> bool:
        has_ticket_noun = any(
            phrase in query_lower
            for phrase in (
                "request",
                "requests",
                "ticket",
                "tickets",
                "service request",
                "service requests",
                "change request",
                "change requests",
            )
        )
        if not has_ticket_noun:
            return False

        has_identifier_term = any(
            phrase in query_lower
            for phrase in (
                "request number",
                "request numbers",
                "ticket number",
                "ticket numbers",
                "request id",
                "request ids",
                "ticket id",
                "ticket ids",
                "numbers for",
                "ids for",
            )
        )
        return has_identifier_term

    def _ticket_identifier_wants_descriptions(self, query_lower: str) -> bool:
        return self._is_ticket_identifier_query(query_lower) and any(
            phrase in query_lower
            for phrase in (
                "description",
                "descriptions",
                "short description",
                "short descriptions",
                "along with",
                "with description",
                "with descriptions",
                "and description",
                "and descriptions",
                "details",
            )
        )

    def _ticket_query_needs_semantic_filter(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        state_filters: List[str],
    ) -> bool:
        return len(self._build_specific_ticket_terms(query, plan, state_filters)) > 0

    def _ticket_result_limit(self, query: str, plan: GraphRAGRetrievalPlan) -> int:
        query_lower = query.lower()
        if self._is_ticket_identifier_query(query_lower):
            return 100
        return max(plan.limit, 12)

    def _build_ticket_search_terms(self, query: str, plan: GraphRAGRetrievalPlan, state_filters: List[str]) -> List[str]:
        specific_terms = self._build_specific_ticket_terms(query, plan, state_filters)
        if specific_terms:
            return specific_terms

        terms = []
        terms.extend(plan.named_entities)
        terms.extend(plan.category_terms)
        terms.extend(plan.concept_terms)
        terms.extend(state_filters)
        return self._dedupe_terms(terms)

    def _build_specific_ticket_terms(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        state_filters: List[str],
        excluded_terms: Optional[set[str]] = None,
    ) -> List[str]:
        generic_terms = self._ticket_generic_terms(state_filters)
        generic_terms.update(excluded_terms or set())
        anchor_terms = self._extract_ticket_anchor_terms(query, state_filters)
        anchor_phrases = [
            phrase
            for phrase in self._extract_phrase_candidates(query)[:6]
            if phrase and all(token not in generic_terms for token in phrase.split())
        ]
        if anchor_terms:
            anchored_named_entities = [
                term
                for term in self._dedupe_terms(plan.named_entities + plan.catalog_terms)
                if any(anchor in term for anchor in anchor_terms)
            ]
            return self._dedupe_terms(anchor_phrases + anchor_terms + anchored_named_entities)

        specific_tokens = [
            term
            for term in self._dedupe_terms(
                plan.named_entities
                + plan.category_terms
                + plan.catalog_terms
                + plan.concept_terms
                + self._extract_ordered_terms(query)
            )
            if term not in generic_terms
        ]
        specific_phrases = [
            phrase
            for phrase in self._extract_phrase_candidates(query)[:6]
            if any(token not in generic_terms for token in phrase.split())
        ]
        return self._dedupe_terms(specific_phrases + specific_tokens)

    def _should_use_deterministic_ticket_count_program(self, query: str, plan: GraphRAGRetrievalPlan) -> bool:
        query_lower = query.lower()
        return (
            plan.domain == "ticket"
            and any(token in query_lower for token in ("how many", "count", "number of"))
        )

    def _build_ticket_count_graph_query_program(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
    ) -> GraphRAGCypherProgram:
        state_terms = self._resolve_ticket_state_filters(query, plan)
        requester_terms = self._extract_requester_terms(query)
        segment_labels = self._extract_ticket_count_segments(query)
        segment_one_label = segment_labels[0] if segment_labels else None
        segment_two_label = segment_labels[1] if len(segment_labels) > 1 else None
        segment_one_terms = self._build_ticket_segment_terms(segment_one_label) if segment_one_label else []
        segment_two_terms = self._build_ticket_segment_terms(segment_two_label) if segment_two_label else []
        ticket_numbers = [
            term for term in self._dedupe_terms(plan.named_entities + self._extract_ordered_terms(query))
            if re.fullmatch(r"(req|chg|ritm)\d+", term)
        ]

        excluded_terms = set()
        for phrase in requester_terms + [label for label in (segment_one_label, segment_two_label) if label]:
            excluded_terms.update(self._extract_ordered_terms(phrase))

        subject_terms = self._build_ticket_count_subject_terms(
            query,
            state_terms,
            excluded_terms,
        )

        cypher_query = """
MATCH (ticket:Ticket)
OPTIONAL MATCH (requester:User)-[:REQUESTED]->(ticket)
OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
WITH ticket, requester, item, catalog
WHERE ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
  AND (size($state_terms) = 0 OR ANY(term IN $state_terms WHERE
        (toLower(coalesce(ticket.state_display, '')) CONTAINS term OR
        toLower(coalesce(ticket.state, '')) CONTAINS term)))
  AND (size($ticket_numbers) = 0 OR ANY(term IN $ticket_numbers WHERE
        (toLower(coalesce(ticket.number, '')) CONTAINS term OR
        toLower(coalesce(item.number, '')) CONTAINS term)))
  AND (size($requester_terms) = 0 OR ANY(term IN $requester_terms WHERE
        (toLower(coalesce(ticket.requested_for_name, '')) CONTAINS term OR
        toLower(coalesce(ticket.requested_by_name, '')) CONTAINS term OR
        toLower(coalesce(requester.name, '')) CONTAINS term)))
WITH DISTINCT ticket, item, catalog,
     CASE
         WHEN size($subject_terms) = 0 THEN true
         ELSE ANY(term IN $subject_terms WHERE
             (toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
             toLower(coalesce(ticket.description, '')) CONTAINS term OR
             toLower(coalesce(item.short_description, '')) CONTAINS term OR
             toLower(coalesce(item.description, '')) CONTAINS term OR
             toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
             toLower(coalesce(catalog.name, '')) CONTAINS term OR
             toLower(coalesce(catalog.category, '')) CONTAINS term)
         )
     END AS matches_subject,
     CASE
         WHEN size($segment_one_terms) = 0 THEN false
         ELSE ANY(term IN $segment_one_terms WHERE
             (toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
             toLower(coalesce(ticket.description, '')) CONTAINS term OR
             toLower(coalesce(item.short_description, '')) CONTAINS term OR
             toLower(coalesce(item.description, '')) CONTAINS term OR
             toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
             toLower(coalesce(catalog.name, '')) CONTAINS term OR
             toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
             toLower(coalesce(catalog.description, '')) CONTAINS term OR
             toLower(coalesce(catalog.category, '')) CONTAINS term)
         )
     END AS matches_segment_one,
     CASE
         WHEN size($segment_two_terms) = 0 THEN false
         ELSE ANY(term IN $segment_two_terms WHERE
             (toLower(coalesce(ticket.short_description, '')) CONTAINS term OR
             toLower(coalesce(ticket.description, '')) CONTAINS term OR
             toLower(coalesce(item.short_description, '')) CONTAINS term OR
             toLower(coalesce(item.description, '')) CONTAINS term OR
             toLower(coalesce(item.cat_item_name, '')) CONTAINS term OR
             toLower(coalesce(catalog.name, '')) CONTAINS term OR
             toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
             toLower(coalesce(catalog.description, '')) CONTAINS term OR
             toLower(coalesce(catalog.category, '')) CONTAINS term)
         )
     END AS matches_segment_two
WHERE matches_subject
RETURN count(DISTINCT ticket) AS total_count,
       count(DISTINCT CASE WHEN matches_segment_one THEN ticket END) AS segment_one_count,
       count(DISTINCT CASE WHEN (size($segment_one_terms) = 0 OR matches_segment_one) AND matches_segment_two THEN ticket END) AS segment_two_count
""".strip()

        expected_columns = ["total_count"]
        if segment_one_label:
            expected_columns.append("segment_one_count")
        if segment_two_label:
            expected_columns.append("segment_two_count")

        return GraphRAGCypherProgram(
            intent_summary="ticket:count_segments" if segment_labels else "ticket:count",
            answer_style="concise",
            cypher_query=cypher_query,
            expected_columns=expected_columns,
            subject_terms=subject_terms,
            field_terms=[],
            table_terms=[],
            state_terms=self._dedupe_terms(state_terms),
            ticket_numbers=self._dedupe_terms(ticket_numbers),
            requester_terms=self._dedupe_terms(requester_terms),
            segment_one_terms=self._dedupe_terms(segment_one_terms),
            segment_two_terms=self._dedupe_terms(segment_two_terms),
            segment_one_label=segment_one_label,
            segment_two_label=segment_two_label,
            limit=1,
        )

    def _extract_requester_terms(self, query: str) -> List[str]:
        requester = self._extract_ticket_requester_tail(query).get("requester", "")
        return [requester] if requester else []

    def _extract_ticket_count_segments(self, query: str) -> List[str]:
        query_lower = re.sub(r"\s+", " ", query.strip().lower())
        matches = re.finditer(
            r"\band\s+how\s+many(?:\s+(?:of\s+)?(?:those|them))?\s*(?:are|were|was|have\s+been|had\s+been)?\s*(.+?)(?=\s+\band\s+how\s+many\b|[?.!,]|$)",
            query_lower,
        )
        segments: List[str] = []
        for match in matches:
            label = re.sub(r"\s+", " ", match.group(1)).strip(" ?!.,")
            label = re.sub(r"^(?:of\s+)?(?:those|them)\s+", "", label).strip()
            label = re.sub(r"^(?:are|were|was|have\s+been|had\s+been)\s+", "", label).strip()
            if label:
                segments.append(label)
        return segments[:2]

    def _build_ticket_segment_terms(self, label: Optional[str]) -> List[str]:
        if not label:
            return []
        tokens = self._extract_ordered_terms(label)
        terms = [label]
        if len(tokens) <= 1:
            terms.extend(tokens)
            terms.extend(self._expand_alias_terms(tokens or [label]))
        return self._dedupe_terms(terms)

    def _build_ticket_count_subject_terms(
        self,
        query: str,
        state_filters: List[str],
        excluded_terms: set[str],
    ) -> List[str]:
        generic_terms = self._ticket_generic_terms(state_filters)
        generic_terms.update(excluded_terms)
        generic_terms.update({"how", "many", "total", "those", "them", "have", "has", "made"})

        phrases = [
            phrase
            for phrase in self._extract_phrase_candidates(query)[:6]
            if phrase and all(token not in generic_terms for token in phrase.split())
        ]
        tokens = [
            token
            for token in self._extract_ordered_terms(query)
            if token not in generic_terms
        ]
        return self._dedupe_terms(phrases + tokens)

    def _extract_ticket_anchor_terms(self, query: str, state_filters: List[str]) -> List[str]:
        generic_terms = self._ticket_generic_terms(state_filters)
        return [
            term
            for term in self._extract_ordered_terms(query)
            if term not in generic_terms
        ]

    def _ticket_candidate_matches_anchor(self, candidate: Dict[str, Any], anchor_terms: List[str]) -> bool:
        candidate_text = " ".join(
            str(value or "").lower()
            for value in (
                candidate.get("number"),
                candidate.get("short_description"),
                candidate.get("description"),
                candidate.get("request_item_number"),
                candidate.get("request_item_short_description"),
                candidate.get("catalog_item_name"),
            )
        )
        return any(term in candidate_text for term in anchor_terms)

    def _ticket_generic_terms(self, state_filters: List[str]) -> set[str]:
        generic_terms = {
            "request", "requests", "ticket", "tickets", "service", "services", "change",
            "made", "submitted", "created", "raised", "opened", "logged", "requested",
            "count", "number", "numbers", "many", "show", "list", "which", "what",
            "share", "give", "tell", "find", "fetch", "along", "with", "their", "there",
            "description", "descriptions", "short", "details", "detail",
            "user", "users", "everyone", "everybody", "all",
            "requester", "requesters", "ticketing", "history", "records",
            "for", "the", "and", "ids", "id",
        }
        generic_terms.update(state_filters)
        return generic_terms

    async def _search_catalog_candidates(self, query: str, plan: GraphRAGRetrievalPlan) -> List[Dict[str, Any]]:
        search_terms = self._build_catalog_search_terms(query, plan)
        lexical_results: List[Dict[str, Any]] = []
        graph_results: List[Dict[str, Any]] = []
        semantic_results: List[Dict[str, Any]] = []

        try:
            lexical_results = catalog_lexical_index.search_index(query, extra_tokens=search_terms)
        except Exception:
            logger.exception("[GRAPHRAG] Lexical catalog search failed")

        try:
            graph_results = neo4j_catalog_graph_service.search_catalog_semantic(query, search_terms)
        except Exception:
            logger.exception("[GRAPHRAG] Neo4j catalog search failed")

        should_use_semantic = (
            len(lexical_results) < 3 or
            not lexical_results or
            float(lexical_results[0].get("score") or 0.0) < 0.55
        )
        if should_use_semantic:
            try:
                semantic_results, _ = await asyncio.to_thread(
                    semantic_search,
                    query,
                    graph_service=neo4j_catalog_graph_service,
                )
            except Exception:
                logger.exception("[GRAPHRAG] Semantic catalog fallback search failed")

        merged = self._merge_catalog_candidates(query, plan, lexical_results, graph_results, semantic_results)
        logger.info(
            "[GRAPHRAG] Catalog candidates: lexical=%d graph=%d semantic=%d merged=%d",
            len(lexical_results),
            len(graph_results),
            len(semantic_results),
            len(merged),
        )
        return merged

    def _fallback_catalog_wide_candidates(self, query: str, plan: GraphRAGRetrievalPlan) -> List[Dict[str, Any]]:
        if plan.sort_field == "request_count" and neo4j_ticket_graph_service.is_available():
            ranked_items = neo4j_ticket_graph_service.rank_catalog_items_by_requests(
                keywords=self._build_catalog_search_terms(query, plan),
                limit=200,
            )
            if ranked_items:
                return [self._normalize_catalog_record(item) for item in ranked_items]

        if not neo4j_catalog_graph_service.is_available():
            return []
        priced_only = plan.sort_field == "price" or plan.aggregation in {"min", "max"}
        items = neo4j_catalog_graph_service.list_catalog_items(limit=200, priced_only=priced_only)
        return [self._normalize_catalog_record(item) for item in items]

    def _attach_catalog_request_counts(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not neo4j_ticket_graph_service.is_available():
            return candidates

        catalog_sys_ids = [
            str(candidate.get("sys_id") or "").strip()
            for candidate in candidates
            if str(candidate.get("sys_id") or "").strip()
        ]
        ranked_items = neo4j_ticket_graph_service.rank_catalog_items_by_requests(
            keywords=self._build_catalog_search_terms(query, plan),
            catalog_sys_ids=catalog_sys_ids or None,
            limit=max(len(catalog_sys_ids), plan.limit, 20),
        )

        if not ranked_items and not candidates:
            return []
        if not ranked_items:
            return [self._normalize_catalog_record(candidate) for candidate in candidates]

        request_counts = {
            str(item.get("sys_id") or "").strip(): item
            for item in ranked_items
            if str(item.get("sys_id") or "").strip()
        }

        enriched: List[Dict[str, Any]] = []
        seen = set()
        for candidate in candidates:
            sys_id = str(candidate.get("sys_id") or "").strip()
            normalized = self._normalize_catalog_record(candidate)
            request_item = request_counts.get(sys_id)
            if request_item:
                normalized["request_count"] = int(request_item.get("request_count") or 0)
                normalized["request_count_display"] = f"{normalized['request_count']} requests"
            enriched.append(normalized)
            if sys_id:
                seen.add(sys_id)

        for item in ranked_items:
            sys_id = str(item.get("sys_id") or "").strip()
            if sys_id in seen:
                continue
            normalized = self._normalize_catalog_record(item)
            normalized["request_count"] = int(item.get("request_count") or 0)
            normalized["request_count_display"] = f"{normalized['request_count']} requests"
            enriched.append(normalized)

        return enriched

    def _materialize_catalog_contexts(self, candidates: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        context: List[Dict[str, Any]] = []
        seen = set()
        for candidate in candidates[: max(1, limit)]:
            sys_id = str(candidate.get("sys_id") or "").strip()
            if not sys_id or sys_id in seen:
                continue
            raw_context = neo4j_catalog_graph_service.get_catalog_item_context(sys_id)
            if raw_context:
                context.append(self._normalize_catalog_context(raw_context))
                seen.add(sys_id)
                continue
            normalized = self._normalize_catalog_record(candidate)
            context.append(normalized)
            seen.add(sys_id)
        return context

    def _build_catalog_summary(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        candidates: List[Dict[str, Any]],
        context: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        normalized_candidates = [self._normalize_catalog_record(candidate) for candidate in candidates]
        summary: Dict[str, Any] = {
            "domain": "catalog",
            "intent_family": plan.intent_family,
            "entity_scope": plan.entity_scope,
            "candidate_count": len(normalized_candidates),
            "items": normalized_candidates[: max(plan.limit, 10)],
        }

        if plan.intent_family == "count":
            summary["count"] = len(normalized_candidates)
        elif plan.intent_family == "list":
            summary["listed_items"] = normalized_candidates[: plan.limit]
        elif plan.intent_family == "rank":
            ranked = self._rank_catalog_records(normalized_candidates, plan)
            summary["ranked_items"] = ranked[: plan.limit]
            if ranked:
                summary["winner"] = ranked[0]
            else:
                summary["insufficient_reason"] = "No matching catalog items had a usable value for the requested ranking field."
        elif plan.intent_family == "compare":
            comparisons = self._select_compare_items(context or normalized_candidates, plan.limit)
            summary["comparisons"] = comparisons
        elif plan.entity_scope == "choice":
            summary["choice_sets"] = self._extract_choice_sets(context)
        elif plan.entity_scope == "variable":
            summary["variable_sets"] = self._extract_variable_sets(context)
        else:
            summary["top_match"] = (context or normalized_candidates[:1])[0] if (context or normalized_candidates) else None

        return summary

    def _build_schema_summary(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        hits: List[Dict[str, Any]],
        context: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "domain": "schema",
            "intent_family": plan.intent_family,
            "entity_scope": plan.entity_scope,
            "candidate_count": len(hits),
            "tables": context[: max(plan.limit, 10)],
        }
        if plan.intent_family == "count":
            summary["count"] = len(hits)
        elif plan.intent_family == "list":
            summary["listed_tables"] = context[: plan.limit]
        elif plan.intent_family == "compare":
            summary["comparisons"] = context[: max(2, min(plan.limit, 3))]
        else:
            summary["top_match"] = context[0] if context else None
        return summary

    def _maybe_answer_deterministically(self, plan: GraphRAGRetrievalPlan, summary: Dict[str, Any]) -> Optional[str]:
        if summary.get("candidate_count", 0) == 0:
            return NO_CONTEXT_ANSWER

        if plan.domain == "catalog":
            if plan.intent_family == "count":
                return f"I found {summary.get('count', 0)} matching catalog items in the graph."
            if plan.intent_family == "rank":
                winner = summary.get("winner")
                if not winner:
                    return summary.get("insufficient_reason") or INSUFFICIENT_CONTEXT_ANSWER
                if plan.sort_field == "request_count":
                    count = winner.get("request_count")
                    if count is None:
                        return summary.get("insufficient_reason") or INSUFFICIENT_CONTEXT_ANSWER
                    qualifier = "least requested" if plan.sort_direction == "asc" else "most requested"
                    return f"The {qualifier} matching catalog item is {winner.get('name')} with {count} requests in the graph."
                field = plan.sort_field or "requested field"
                value = winner.get(field)
                display = winner.get(f"{field}_display") or value
                return f"The best match for that ranking is {winner.get('name')} with {field} {display}."
            if plan.entity_scope == "choice" and summary.get("choice_sets"):
                first = summary["choice_sets"][0]
                item_name = first.get("item_name") or "Catalog item"
                lines = []
                for variable in first.get("variables", [])[:8]:
                    choices = ", ".join(variable.get("choices", []))
                    if choices:
                        lines.append(f"{variable.get('label')}: {choices}")
                if lines:
                    return f"{item_name}\n" + "\n".join(lines)
            if plan.entity_scope == "variable" and summary.get("variable_sets"):
                first = summary["variable_sets"][0]
                item_name = first.get("item_name") or "Catalog item"
                required = [entry["label"] for entry in first.get("variables", []) if entry.get("mandatory")]
                if required:
                    return f"{item_name}: required fields are {', '.join(required)}."
        elif plan.domain == "schema":
            if plan.intent_family == "count":
                return f"I found {summary.get('count', 0)} matching tables in the schema graph."
            if plan.entity_scope == "column" and summary.get("top_match"):
                top = summary["top_match"]
                table = top.get("table") or {}
                columns = top.get("columns") or []
                names = [column.get("label") or column.get("name") for column in columns[:25]]
                return f"{table.get('label') or table.get('name')}: {', '.join(names)}"
        elif plan.domain == "ticket":
            if plan.intent_family == "count":
                ticket_type_label = self._ticket_type_label_from_summary(summary)
                prefix = f"matching {ticket_type_label} " if ticket_type_label else "matching "
                if summary.get("include_identifiers"):
                    tickets = summary.get("tickets") or []
                    if tickets:
                        if summary.get("include_ticket_descriptions"):
                            lines = [
                                f"- {ticket.get('number')}: {ticket.get('short_description') or 'No description available.'}"
                                for ticket in tickets
                                if ticket.get("number")
                            ]
                        else:
                            lines = [f"- {ticket.get('number')}" for ticket in tickets if ticket.get("number")]
                    else:
                        lines = []
                    if lines:
                        return (
                            f"I found {summary.get('count', 0)} {prefix}tickets in the graph.\n"
                            + "\n".join(lines)
                        )
                return f"I found {summary.get('count', 0)} {prefix}tickets in the graph."
            if plan.intent_family == "rank":
                winner = summary.get("winner")
                if not winner:
                    return INSUFFICIENT_CONTEXT_ANSWER
                return f"The most relevant ticket is {winner.get('number')}: {winner.get('short_description') or 'No description available.'}"
            if plan.intent_family == "list" and summary.get("listed_tickets"):
                tickets = summary["listed_tickets"]
                if summary.get("include_identifiers"):
                    if summary.get("include_ticket_descriptions"):
                        return "\n".join(
                            f"- {ticket.get('number')}: {ticket.get('short_description') or 'No description available.'}"
                            for ticket in tickets
                            if ticket.get("number")
                        )
                    return "\n".join(
                        f"- {ticket.get('number')}"
                        for ticket in tickets
                        if ticket.get("number")
                    )
                return "\n".join(
                    f"- {ticket.get('number')}: {ticket.get('short_description') or 'No description available.'}"
                    for ticket in tickets
                )
            if summary.get("top_match"):
                top = summary["top_match"]
                ticket = top.get("ticket") if top.get("type") == "ticket" else top
                if ticket:
                    return f"{ticket.get('number')}: {ticket.get('short_description') or 'No description available.'}"

        return None

    async def _generate_answer(self, query: str, plan: GraphRAGRetrievalPlan, execution: Dict[str, Any]) -> str:
        if execution.get("answer"):
            return execution["answer"]

        try:
            async_client = get_async_client()
            response = await asyncio.wait_for(
                async_client.chat.completions.create(
                    model=settings.openai_chat_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You answer ServiceNow questions using only supplied graph retrieval results.",
                        },
                        {
                            "role": "user",
                            "content": ANSWER_PROMPT.format(
                                query=query,
                                result=json.dumps(
                                    {
                                        "plan": plan.model_dump(),
                                        "summary": execution.get("summary", {}),
                                        "context": execution.get("context", []),
                                    },
                                    indent=2,
                                    ensure_ascii=True,
                                ),
                            ),
                        },
                    ],
                    temperature=0.1,
                    max_tokens=700,
                ),
                timeout=GRAPH_RAG_LLM_TIMEOUT_SECONDS,
            )
            answer = (response.choices[0].message.content or "").strip()
            if answer:
                return answer
        except Exception:
            logger.exception("[GRAPHRAG] Answer generation failed, using deterministic fallback")

        return self._generate_answer_from_result(plan, execution)

    def _generate_answer_from_result(self, plan: GraphRAGRetrievalPlan, execution: Dict[str, Any]) -> str:
        summary = execution.get("summary", {})
        context = execution.get("context", [])

        if execution.get("answer"):
            return execution["answer"]

        if plan.domain == "catalog":
            if plan.intent_family == "list":
                items = summary.get("listed_items") or summary.get("items") or []
                if not items:
                    return NO_CONTEXT_ANSWER
                return "\n".join(f"- {item.get('name')}" for item in items[: plan.limit])
            if plan.intent_family == "compare":
                comparisons = summary.get("comparisons") or context[:2]
                if not comparisons:
                    return INSUFFICIENT_CONTEXT_ANSWER
                parts = []
                for entry in comparisons:
                    item = entry.get("item") if entry.get("type") == "catalog" else entry
                    if not item:
                        continue
                    name = item.get("name") or "Catalog item"
                    desc = item.get("short_description") or item.get("description") or "No description available."
                    price = item.get("price_display") or item.get("price_raw")
                    tail = f" Price: {price}." if price else ""
                    parts.append(f"{name}: {desc}{tail}")
                return "\n\n".join(parts) if parts else INSUFFICIENT_CONTEXT_ANSWER
            top_match = summary.get("top_match")
            if top_match:
                item = top_match.get("item") if top_match.get("type") == "catalog" else top_match
                if item:
                    desc = item.get("short_description") or item.get("description") or "No description available."
                    return f"{item.get('name')}: {desc}"
        elif plan.domain == "schema":
            if plan.intent_family == "list":
                tables = summary.get("listed_tables") or summary.get("tables") or []
                if tables:
                    return "\n".join(
                        f"- {(table.get('table') or {}).get('label') or (table.get('table') or {}).get('name')}"
                        for table in tables[: plan.limit]
                    )
            top_match = summary.get("top_match")
            if top_match:
                table = top_match.get("table") or {}
                return f"{table.get('label') or table.get('name')}: {len(top_match.get('columns') or [])} columns found in the graph."
        elif plan.domain == "ticket":
            if plan.intent_family == "compare":
                comparisons = summary.get("comparisons") or context[:2]
                if not comparisons:
                    return INSUFFICIENT_CONTEXT_ANSWER
                parts = []
                for entry in comparisons:
                    ticket = entry.get("ticket") if entry.get("type") == "ticket" else entry
                    if not ticket:
                        continue
                    parts.append(
                        f"{ticket.get('number')}: {ticket.get('short_description') or 'No description available.'} "
                        f"State: {ticket.get('state_display') or ticket.get('state') or 'unknown'}."
                    )
                return "\n\n".join(parts) if parts else INSUFFICIENT_CONTEXT_ANSWER
            tickets = summary.get("listed_tickets") or summary.get("tickets") or []
            if tickets:
                if summary.get("include_identifiers"):
                    if summary.get("include_ticket_descriptions"):
                        return "\n".join(
                            f"- {ticket.get('number')}: {ticket.get('short_description') or 'No description available.'}"
                            for ticket in tickets
                            if ticket.get("number")
                        )
                    return "\n".join(
                        f"- {ticket.get('number')}"
                        for ticket in tickets
                        if ticket.get("number")
                    )
                return "\n".join(
                    f"- {ticket.get('number')}: {ticket.get('short_description') or 'No description available.'}"
                    for ticket in tickets[: plan.limit]
                )

        return INSUFFICIENT_CONTEXT_ANSWER

    def _normalize_plan(self, plan: GraphRAGRetrievalPlan) -> GraphRAGRetrievalPlan:
        limit = max(1, min(int(plan.limit or 5), 10))
        return GraphRAGRetrievalPlan(
            domain=plan.domain,
            intent_family=plan.intent_family,
            entity_scope=plan.entity_scope,
            answer_style=plan.answer_style,
            named_entities=self._dedupe_terms(plan.named_entities)[:8],
            category_terms=self._dedupe_terms(plan.category_terms)[:10],
            catalog_terms=self._dedupe_terms(plan.catalog_terms)[:8],
            table_terms=self._dedupe_terms(plan.table_terms)[:8],
            field_terms=self._dedupe_terms(plan.field_terms)[:10],
            concept_terms=self._dedupe_terms(plan.concept_terms)[:16],
            sort_field=plan.sort_field,
            sort_direction=plan.sort_direction,
            aggregation=plan.aggregation,
            limit=limit,
        )

    def _normalize_entities(self, entities: GraphRAGEntities) -> GraphRAGEntities:
        return GraphRAGEntities(
            catalog_terms=self._dedupe_terms(entities.catalog_terms)[:8],
            table_terms=self._dedupe_terms(entities.table_terms)[:8],
            field_terms=self._dedupe_terms(entities.field_terms)[:10],
            concept_terms=self._dedupe_terms(entities.concept_terms)[:14],
        )

    def _normalize_catalog_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = payload.get("item") or {}
        schema = payload.get("schema") or {}
        variables = payload.get("variables") or []
        parsed_price = self._parse_price(item.get("price"))

        return {
            "type": "catalog",
            "item": {
                "sys_id": item.get("sys_id"),
                "name": item.get("name"),
                "short_description": item.get("short_description"),
                "description": item.get("description"),
                "category": item.get("category"),
                "price_raw": item.get("price"),
                "price_value": parsed_price,
                "price_display": self._format_price(parsed_price, item.get("price")),
            },
            "schema": {
                "key": schema.get("key") or item.get("form_schema_key"),
                "name": schema.get("name") or item.get("form_schema_name"),
                "field_count": schema.get("field_count") or item.get("form_field_count"),
            },
            "variables": [
                {
                    "sys_id": variable.get("sys_id"),
                    "name": variable.get("name"),
                    "label": variable.get("label"),
                    "type": variable.get("type"),
                    "mandatory": bool(variable.get("mandatory")),
                    "default_value": variable.get("default_value"),
                    "depends_on": variable.get("depends_on"),
                    "inactive_on_value": variable.get("inactive_on_value"),
                    "active_on_value": variable.get("active_on_value"),
                    "choices": [
                        {
                            "label": choice.get("label"),
                            "value": choice.get("value"),
                        }
                        for choice in variable.get("choices", [])
                    ],
                }
                for variable in variables
            ],
        }

    def _normalize_catalog_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = payload.get("item") or payload
        parsed_price = self._parse_price(item.get("price") or item.get("price_raw"))
        field_count = item.get("field_count") or item.get("variable_count")
        request_count = item.get("request_count")
        normalized_request_count = None
        if request_count not in (None, ""):
            try:
                normalized_request_count = int(request_count)
            except (TypeError, ValueError):
                normalized_request_count = None
        return {
            "sys_id": item.get("sys_id"),
            "name": item.get("name"),
            "short_description": item.get("short_description"),
            "description": item.get("description"),
            "category": item.get("category"),
            "price": parsed_price,
            "price_raw": item.get("price") or item.get("price_raw"),
            "price_display": self._format_price(parsed_price, item.get("price") or item.get("price_raw")),
            "field_count": int(field_count) if str(field_count or "").isdigit() else field_count,
            "request_count": normalized_request_count,
            "request_count_display": f"{normalized_request_count} requests" if normalized_request_count is not None else None,
            "score": float(item.get("combined_score") or item.get("score") or 0.0),
        }

    def _normalize_schema_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        table = payload.get("table") or {}
        columns = payload.get("columns") or []

        return {
            "type": "schema",
            "table": {
                "sys_id": table.get("sys_id"),
                "name": table.get("name"),
                "label": table.get("label"),
                "scope": table.get("scope"),
                "provider_class": table.get("provider_class"),
                "column_count": len(columns),
            },
            "columns": [
                {
                    "name": column.get("name"),
                    "label": column.get("label"),
                    "type": column.get("type"),
                    "max_length": column.get("max_length"),
                    "mandatory": bool(column.get("mandatory")),
                    "default_value": column.get("default_value"),
                    "reference": column.get("reference"),
                }
                for column in columns
            ],
        }

    def _normalize_ticket_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ticket = payload.get("ticket") or {}
        request_item = payload.get("request_item") or {}
        catalog_item = payload.get("catalog_item") or {}
        return {
            "type": "ticket",
            "ticket": self._normalize_ticket_record(ticket),
            "request_item": {
                "sys_id": request_item.get("sys_id"),
                "number": request_item.get("number"),
                "short_description": request_item.get("short_description"),
                "description": request_item.get("description"),
                "state": request_item.get("state"),
                "state_display": request_item.get("state_display"),
                "cat_item_sys_id": request_item.get("cat_item_sys_id"),
                "cat_item_name": request_item.get("cat_item_name"),
            } if request_item else None,
            "catalog_item": {
                "sys_id": catalog_item.get("sys_id"),
                "name": catalog_item.get("name"),
            } if catalog_item else None,
        }

    def _normalize_ticket_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "sys_id": payload.get("sys_id"),
            "number": payload.get("number"),
            "ticket_type": payload.get("ticket_type"),
            "short_description": payload.get("short_description"),
            "description": payload.get("description"),
            "state": payload.get("state"),
            "state_display": payload.get("state_display"),
            "opened_at": payload.get("opened_at"),
            "requested_for_sys_id": payload.get("requested_for_sys_id"),
            "requested_for_name": payload.get("requested_for_name"),
            "requested_by_sys_id": payload.get("requested_by_sys_id"),
            "requested_by_name": payload.get("requested_by_name"),
            "assigned_to_sys_id": payload.get("assigned_to_sys_id"),
            "assigned_to_name": payload.get("assigned_to_name"),
            "risk": payload.get("risk"),
            "impact": payload.get("impact"),
            "change_type": payload.get("change_type"),
            "request_item_sys_id": payload.get("request_item_sys_id"),
            "request_item_number": payload.get("request_item_number"),
            "request_item_short_description": payload.get("request_item_short_description"),
            "catalog_item_sys_id": payload.get("catalog_item_sys_id"),
            "catalog_item_name": payload.get("catalog_item_name"),
            "score": float(payload.get("combined_score") or payload.get("score") or 0.0),
        }

    def _entities_payload(self, plan: GraphRAGRetrievalPlan) -> Dict[str, Any]:
        return {
            "named_entities": plan.named_entities,
            "category_terms": plan.category_terms,
            "catalog_terms": plan.catalog_terms,
            "table_terms": plan.table_terms,
            "field_terms": plan.field_terms,
            "concept_terms": plan.concept_terms,
        }

    def _build_catalog_search_terms(self, query: str, plan: GraphRAGRetrievalPlan) -> List[str]:
        terms = []
        terms.extend(plan.named_entities)
        terms.extend(plan.category_terms)
        terms.extend(plan.catalog_terms)
        terms.extend(plan.field_terms)
        terms.extend(plan.concept_terms)
        terms.extend(self._extract_phrase_candidates(query)[:6])
        terms.extend(self._extract_ordered_terms(query)[:10])
        terms.extend(self._expand_alias_terms(terms))
        return self._dedupe_terms(terms)

    def _merge_catalog_candidates(
        self,
        query: str,
        plan: GraphRAGRetrievalPlan,
        lexical_results: List[Dict[str, Any]],
        graph_results: List[Dict[str, Any]],
        semantic_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        combined: Dict[str, Dict[str, Any]] = {}
        max_graph_score = max((float(item.get("score") or 0.0) for item in graph_results), default=0.0)
        query_terms = set(self._extract_ordered_terms(query))
        phrase_terms = set(self._extract_phrase_candidates(query) + plan.catalog_terms + plan.named_entities)

        for rank, item in enumerate(lexical_results):
            sys_id = str(item.get("sys_id") or "").strip()
            if not sys_id:
                continue
            combined.setdefault(sys_id, {"sys_id": sys_id})
            combined[sys_id].update(
                {
                    "name": item.get("name"),
                    "short_description": item.get("short_description"),
                    "lexical_score": float(item.get("score") or 0.0),
                    "lexical_rank": rank,
                }
            )

        for rank, item in enumerate(graph_results):
            sys_id = str(item.get("sys_id") or "").strip()
            if not sys_id:
                continue
            combined.setdefault(sys_id, {"sys_id": sys_id})
            combined[sys_id].update(
                {
                    "name": item.get("name") or combined[sys_id].get("name"),
                    "short_description": item.get("short_description") or combined[sys_id].get("short_description"),
                    "description": item.get("description") or combined[sys_id].get("description"),
                    "category": item.get("category") or combined[sys_id].get("category"),
                    "price": item.get("price") or combined[sys_id].get("price"),
                    "matched_terms": item.get("matched_terms") or combined[sys_id].get("matched_terms"),
                    "graph_score": float(item.get("score") or 0.0),
                    "graph_rank": rank,
                }
            )

        for rank, item in enumerate(semantic_results):
            sys_id = str(item.get("sys_id") or "").strip()
            if not sys_id:
                continue
            combined.setdefault(sys_id, {"sys_id": sys_id})
            combined[sys_id].update(
                {
                    "name": item.get("name") or combined[sys_id].get("name"),
                    "short_description": item.get("short_description") or combined[sys_id].get("short_description"),
                    "description": item.get("description") or combined[sys_id].get("description"),
                    "category": item.get("category") or combined[sys_id].get("category"),
                    "price": item.get("price") or combined[sys_id].get("price"),
                    "semantic_rank": rank,
                    "semantic_score": max(0.0, 50.0 - (rank * 5.0)),
                }
            )

        merged: List[Dict[str, Any]] = []
        for candidate in combined.values():
            name = str(candidate.get("name") or "").lower()
            lexical_score = float(candidate.get("lexical_score") or 0.0) * 100.0
            graph_score = float(candidate.get("graph_score") or 0.0)
            graph_norm = (graph_score / max_graph_score * 100.0) if max_graph_score > 0 else 0.0
            semantic_score = float(candidate.get("semantic_score") or 0.0)
            combined_score = (lexical_score * 0.55) + (graph_norm * 0.30) + (semantic_score * 0.15)

            if any(phrase and phrase in name for phrase in phrase_terms):
                combined_score += 30.0

            name_tokens = set(self._extract_ordered_terms(name))
            token_overlap = len(query_terms & name_tokens)
            combined_score += token_overlap * 4.0

            if plan.entity_scope == "choice" and candidate.get("matched_terms"):
                combined_score += 3.0
            if plan.intent_family == "rank" and candidate.get("price"):
                combined_score += 2.0

            candidate["combined_score"] = combined_score
            merged.append(candidate)

        merged.sort(
            key=lambda item: (
                float(item.get("combined_score") or 0.0),
                float(item.get("lexical_score") or 0.0),
                float(item.get("graph_score") or 0.0),
            ),
            reverse=True,
        )
        return merged[:20]

    def _extract_variable_sets(self, context: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        for entry in context:
            if entry.get("type") != "catalog":
                continue
            item = entry.get("item") or {}
            variables = entry.get("variables") or []
            results.append(
                {
                    "item_name": item.get("name"),
                    "variables": [
                        {
                            "label": variable.get("label") or variable.get("name"),
                            "name": variable.get("name"),
                            "mandatory": bool(variable.get("mandatory")),
                            "type": variable.get("type"),
                        }
                        for variable in variables
                    ],
                }
            )
        return results

    def _extract_choice_sets(self, context: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        for entry in context:
            if entry.get("type") != "catalog":
                continue
            item = entry.get("item") or {}
            variables = entry.get("variables") or []
            results.append(
                {
                    "item_name": item.get("name"),
                    "variables": [
                        {
                            "label": variable.get("label") or variable.get("name"),
                            "choices": [
                                choice.get("label") or choice.get("value")
                                for choice in variable.get("choices", [])
                                if choice.get("label") or choice.get("value")
                            ],
                        }
                        for variable in variables
                        if variable.get("choices")
                    ],
                }
            )
        return results

    def _rank_catalog_records(self, items: List[Dict[str, Any]], plan: GraphRAGRetrievalPlan) -> List[Dict[str, Any]]:
        field = plan.sort_field or "price"
        ranked = [item for item in items if item.get(field) is not None]
        ranked.sort(
            key=lambda item: item.get(field),
            reverse=plan.sort_direction == "desc",
        )
        return ranked

    def _select_compare_items(self, items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        return items[: max(2, min(limit, 4))]

    def _rank_ticket_records(self, items: List[Dict[str, Any]], plan: GraphRAGRetrievalPlan) -> List[Dict[str, Any]]:
        field = plan.sort_field or "opened_at"
        ranked = [item for item in items if item.get(field) is not None]
        ranked.sort(
            key=lambda item: str(item.get(field) or ""),
            reverse=plan.sort_direction == "desc",
        )
        return ranked

    def _ticket_type_label_from_summary(self, summary: Dict[str, Any]) -> Optional[str]:
        tickets = summary.get("tickets") or []
        if not tickets:
            return None
        ticket_types = {str(ticket.get("ticket_type") or "").upper() for ticket in tickets if ticket.get("ticket_type")}
        if len(ticket_types) == 1:
            ticket_type = next(iter(ticket_types))
            return "change request" if ticket_type == "CH" else "service request"
        return None

    def _parse_price(self, value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return None
        normalized = re.sub(r"[^0-9.]+", "", text.replace(",", ""))
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None

    def _format_price(self, parsed_value: Optional[float], raw_value: Any) -> Optional[str]:
        if parsed_value is not None:
            return f"${parsed_value:,.2f}"
        if raw_value in (None, ""):
            return None
        return str(raw_value)

    def _expand_alias_terms(self, values: List[str]) -> List[str]:
        reverse_alias: Dict[str, set[str]] = {}
        for key, synonyms in catalog_lexical_index.ALIAS_EXPANSION.items():
            reverse_alias.setdefault(key, set()).update(synonyms)
            for synonym in synonyms:
                reverse_alias.setdefault(synonym, set()).add(key)
                reverse_alias[synonym].update(s for s in synonyms if s != synonym)

        expanded: List[str] = []
        for value in values:
            normalized = str(value or "").strip().lower()
            if not normalized:
                continue
            for token in [normalized, *normalized.split()]:
                expanded.extend(sorted(reverse_alias.get(token, set())))
        return self._dedupe_terms(expanded)

    def _extract_ordered_terms(self, query: str) -> List[str]:
        tokens = re.findall(r"[a-z0-9_]+", query.lower())
        return [
            token for token in tokens
            if len(token) >= 3 and token not in TERM_STOP_WORDS
        ]

    def _extract_phrase_candidates(self, query: str) -> List[str]:
        tokens = self._extract_ordered_terms(query)
        phrases: List[str] = []
        for size in (4, 3, 2):
            for index in range(0, max(0, len(tokens) - size + 1)):
                phrase = " ".join(tokens[index:index + size]).strip()
                if phrase and phrase not in phrases:
                    phrases.append(phrase)
        return phrases[:10]

    def _singularize_term(self, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized.endswith("ies") and len(normalized) > 4:
            candidate = normalized[:-3] + "y"
            if candidate in TICKET_ITEM_HINT_TERMS or candidate in BROAD_TICKET_CATEGORY_TERMS:
                return candidate
        if normalized.endswith("s") and len(normalized) > 4:
            candidate = normalized[:-1]
            if candidate in TICKET_ITEM_HINT_TERMS or candidate in BROAD_TICKET_CATEGORY_TERMS:
                return candidate
        return normalized

    def _dedupe_terms(self, values: List[str]) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for value in values:
            normalized = str(value or "").strip().lower()
            if not normalized or normalized in TERM_STOP_WORDS or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped


graphrag_service = GraphRAGService()

