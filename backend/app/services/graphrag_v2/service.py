from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.graphrag_service import graphrag_service as legacy_graphrag_service
from app.services.neo4j_query_runtime import neo4j_query_runtime_service

from .cypher_generator import GraphRAGv2CypherGenerator
from .dynamic_retriever import GraphRAGv2DynamicRetriever
from .embedding_service import graphrag_v2_embedding_service
from .executor import GraphRAGv2Executor
from .graph_profile import GraphRAGv2GraphProfileService
from .grounding import GraphRAGv2GroundingResult, GraphRAGv2GroundingService
from .ontology import GraphRAGv2OntologyPlanner
from .query_guard import GraphRAGv2QueryGuard
from .schema_provider import GraphRAGv2SchemaProvider
from .synthesizer import GraphRAGv2Synthesizer
from .types import (
    GraphRAGv2CypherCandidate,
    GraphRAGv2EvidencePayload,
    GraphRAGv2ExecutionResult,
    GraphRAGv2OntologyProfile,
    GraphRAGv2QueryResult,
    GraphRAGv2SchemaSnapshot,
    GraphRAGv2TimeWindow,
)

logger = logging.getLogger(__name__)

_graphrag_v2_debug_logger: Optional[logging.Logger] = None
_ONTOLOGY_ENABLED_FAMILIES = {
    "similar_entities_by_shared_problem",
    "entity_issue_history",
    "recurring_problems_for_entity",
    "recommendation_from_problem_evidence",
    "related_events_by_category_subcategory",
    "triggered_requests_for_entity_or_event",
}


def _get_graphrag_v2_debug_logger() -> Optional[logging.Logger]:
    global _graphrag_v2_debug_logger
    if not settings.graphrag_debug_log_enabled:
        return None
    if _graphrag_v2_debug_logger is not None:
        return _graphrag_v2_debug_logger

    try:
        log_path = Path(settings.graphrag_debug_log_path).expanduser()
        if not log_path.is_absolute():
            project_root = Path(__file__).resolve().parents[4]
            log_path = project_root / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger_instance = logging.getLogger("app.graphrag.v2.debug")
        logger_instance.setLevel(logging.INFO)
        logger_instance.propagate = False
        if not logger_instance.handlers:
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger_instance.addHandler(handler)
        _graphrag_v2_debug_logger = logger_instance
        return _graphrag_v2_debug_logger
    except Exception:
        logger.exception("[GRAPHRAG_V2] Failed to initialize debug logger")
        return None


class GraphRAGv2Service:
    def __init__(self) -> None:
        self.schema_provider = GraphRAGv2SchemaProvider(ttl_seconds=settings.graphrag_v2_schema_ttl_seconds)
        self.ontology_planner = GraphRAGv2OntologyPlanner()
        self.cypher_generator = GraphRAGv2CypherGenerator()
        self.query_guard = GraphRAGv2QueryGuard()
        self.executor = GraphRAGv2Executor(timeout_seconds=8.0)
        self.synthesizer = GraphRAGv2Synthesizer()
        self.grounding_service = GraphRAGv2GroundingService()
        self.graph_profile_service = GraphRAGv2GraphProfileService(ttl_seconds=settings.graphrag_v2_schema_ttl_seconds)
        self.dynamic_retriever = GraphRAGv2DynamicRetriever(self.query_guard, self.executor)

    async def query(self, question: str, user_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        if not neo4j_query_runtime_service.is_available():
            return await self._fallback(
                question=question,
                user_context=user_context or {},
                request_id=request_id,
                started=started,
                fallback_reason="neo4j query runtime unavailable",
            )

        snapshot = self.schema_provider.get_snapshot()
        ontology_profile = self.ontology_planner.infer_profile(snapshot)
        query_family, required_evidence_fields, family_reason = self.ontology_planner.detect_query_family(
            question,
            ontology_profile,
        )
        desired_shape = self._desired_shape(question)
        requested_shapes = self._requested_output_shapes(question)
        time_window = self._resolve_time_window(question) or self.dynamic_retriever.resolve_absolute_time_window(question)

        try:
            dynamic_started = time.perf_counter()
            semantic_profile = self.graph_profile_service.get_profile(snapshot)
            dynamic_route = await self.dynamic_retriever.run(
                question=question,
                snapshot=snapshot,
                profile=semantic_profile,
                desired_shape=desired_shape,
                requested_shapes=requested_shapes,
                time_window=time_window,
            )
            if dynamic_route and dynamic_route.get("execution_outcome") == "grounded_success":
                synthesize_started = time.perf_counter()
                selected_executions = list(dynamic_route.get("selected_executions", []) or [])
                chosen = dynamic_route.get("chosen_execution")
                try:
                    if len(selected_executions) > 1:
                        answer = await self.synthesizer.synthesize_sections(
                            question=question,
                            execution_results=selected_executions,
                        )
                    else:
                        answer = await self.synthesizer.synthesize(
                            question=question,
                            execution_result=chosen,
                        )
                except Exception:
                    logger.exception("[GRAPHRAG_V2:%s] Dynamic synthesis failed, using deterministic fallback", request_id)
                    if len(selected_executions) > 1:
                        answer = self.synthesizer.fallback_sections_answer(question, selected_executions)
                    else:
                        answer = self.synthesizer.fallback_answer(question, getattr(chosen, "rows", []))

                timings = dict(dynamic_route.get("timings", {}) or {})
                timings["dynamic_profile_route_ms"] = round((time.perf_counter() - dynamic_started) * 1000, 2)
                timings["synthesize_answer_ms"] = round((time.perf_counter() - synthesize_started) * 1000, 2)
                timings["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
                evidence = GraphRAGv2EvidencePayload(
                    schema_snapshot_id=snapshot.snapshot_id,
                    graph_profile=snapshot.graph_profile,
                    allowed_labels=snapshot.allowed_labels,
                    allowed_relationships=snapshot.allowed_relationships,
                    time_field_candidates=snapshot.time_field_candidates,
                    service_now_hints_enabled=snapshot.service_now_hints_enabled,
                    ontology_profile=ontology_profile,
                    query_family=str(dynamic_route.get("query_family") or ""),
                    required_evidence_fields=list(dynamic_route.get("required_evidence_fields", []) or []),
                    execution_outcome=str(dynamic_route.get("execution_outcome") or ""),
                    selected_route="dynamic_profile_v1",
                    selection_reason="dynamic graph profile route produced grounded evidence",
                    repair_attempted=bool(dynamic_route.get("repair_attempted")),
                    repair_successful=bool(dynamic_route.get("repair_successful")),
                    baseline_route_result=self._route_debug_summary(dynamic_route),
                    ontology_route_result={},
                    generated_candidates=list(dynamic_route.get("generated_candidates", []) or []),
                    validation_status=list(dynamic_route.get("validations", []) or []),
                    chosen_candidate=chosen.candidate_id if chosen is not None else None,
                    executed_cypher=chosen.cypher_query if chosen is not None else None,
                    parameters=dict(chosen.parameters) if chosen is not None else {},
                    rows=list(getattr(chosen, "rows", []) or [])[:20],
                    row_count=int(getattr(chosen, "row_count", 0) or 0),
                    executed_sections=selected_executions,
                    timings=timings,
                    fallback_used=False,
                    fallback_reason="",
                )
                result = GraphRAGv2QueryResult(answer=answer, evidence=evidence, fallback_result=None)
                payload = result.model_dump()
                self._write_debug_event(
                    request_id=request_id,
                    question=question,
                    desired_shape=desired_shape,
                    snapshot=snapshot,
                    result=payload,
                )
                return payload
        except Exception:
            logger.exception("[GRAPHRAG_V2:%s] Dynamic profile route failed, falling back to LLM Cypher", request_id)

        vector_hybrid_rows: List[Dict[str, Any]] = []
        vector_planning_hints: List[str] = []
        if self._is_similarity_family(query_family):
            vector_hybrid_rows = graphrag_v2_embedding_service.retrieve_hybrid_similarity_candidates(
                question,
                top_k=max(1, int(settings.graphrag_vector_top_k or 12)),
            )
            if vector_hybrid_rows:
                preview_ids = ", ".join(str(row.get("sys_id") or "") for row in vector_hybrid_rows[:8])
                vector_planning_hints.append(
                    "Hybrid vector candidates are available for similarity matching. "
                    f"Prioritize these sys_ids when they align with graph evidence: {preview_ids}"
                )
        grounding_result = await self._run_grounding_layer(
            question=question,
            snapshot=snapshot,
            ontology_profile=ontology_profile,
            query_family=query_family,
        )
        baseline_route = await self._run_candidate_route(
            question=question,
            snapshot=snapshot,
            desired_shape=desired_shape,
            requested_shapes=requested_shapes,
            time_window=time_window,
            route_name="baseline_v2",
            query_family="",
            planning_hints=vector_planning_hints,
            required_evidence_fields=[],
            vector_hybrid_rows=vector_hybrid_rows,
            grounding_rows=[],
        )
        ontology_route: Dict[str, Any] = {}
        if query_family in _ONTOLOGY_ENABLED_FAMILIES:
            ontology_route = await self._run_candidate_route(
                question=question,
                snapshot=snapshot,
                desired_shape=desired_shape,
                requested_shapes=requested_shapes,
                time_window=time_window,
                route_name="ontology_v2",
                query_family=query_family,
                planning_hints=(
                    self.ontology_planner.build_planning_hints(
                        query_family,
                        ontology_profile,
                        required_evidence_fields,
                    )
                    + vector_planning_hints
                    + list(grounding_result.planning_hints or [])
                ),
                required_evidence_fields=required_evidence_fields,
                vector_hybrid_rows=vector_hybrid_rows,
                grounding_rows=list(grounding_result.evidence_rows or []),
            )

        selected_route_name, selected_route, selection_reason = self._select_route_result(
            baseline_route,
            ontology_route,
            query_family=query_family,
        )

        if selected_route.get("execution_outcome") == "generation_failure":
            fallback_reason = str(selected_route.get("failure_reason") or "no valid candidate produced grounded rows")
            return await self._fallback(
                question=question,
                user_context=user_context or {},
                request_id=request_id,
                started=started,
                fallback_reason=fallback_reason,
                snapshot=snapshot,
                generated_candidates=selected_route.get("generated_candidates", []),
                validations=selected_route.get("validations", []),
                execution_results=selected_route.get("execution_results", []),
                timings={
                    **self._merge_route_timings(baseline_route, ontology_route),
                    "total_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )

        synthesize_started = time.perf_counter()
        if selected_route.get("execution_outcome") == "grounded_empty":
            answer = self._grounded_empty_answer(question)
        else:
            selected_executions = list(selected_route.get("selected_executions", []) or [])
            chosen = selected_route.get("chosen_execution")
            try:
                if len(selected_executions) > 1:
                    answer = await self.synthesizer.synthesize_sections(
                        question=question,
                        execution_results=selected_executions,
                    )
                else:
                    answer = await self.synthesizer.synthesize(
                        question=question,
                        execution_result=chosen,
                    )
            except Exception:
                logger.exception("[GRAPHRAG_V2:%s] Synthesis failed, using deterministic fallback", request_id)
                if len(selected_executions) > 1:
                    answer = self.synthesizer.fallback_sections_answer(question, selected_executions)
                else:
                    answer = self.synthesizer.fallback_answer(question, getattr(chosen, "rows", []))
        merged_timings = self._merge_route_timings(baseline_route, ontology_route)
        merged_timings["synthesize_answer_ms"] = round((time.perf_counter() - synthesize_started) * 1000, 2)
        merged_timings["total_ms"] = round((time.perf_counter() - started) * 1000, 2)

        selected_executions = list(selected_route.get("selected_executions", []) or [])
        chosen = selected_route.get("chosen_execution")
        evidence = GraphRAGv2EvidencePayload(
            schema_snapshot_id=snapshot.snapshot_id,
            graph_profile=snapshot.graph_profile,
            allowed_labels=snapshot.allowed_labels,
            allowed_relationships=snapshot.allowed_relationships,
            time_field_candidates=snapshot.time_field_candidates,
            service_now_hints_enabled=snapshot.service_now_hints_enabled,
            ontology_profile=ontology_profile,
            query_family=query_family,
            required_evidence_fields=required_evidence_fields,
            execution_outcome=str(selected_route.get("execution_outcome") or ""),
            selected_route=selected_route_name,
            selection_reason=selection_reason or family_reason,
            repair_attempted=bool(selected_route.get("repair_attempted")),
            repair_successful=bool(selected_route.get("repair_successful")),
            baseline_route_result=self._route_debug_summary(baseline_route),
            ontology_route_result=self._route_debug_summary(ontology_route),
            generated_candidates=list(selected_route.get("generated_candidates", []) or []),
            validation_status=list(selected_route.get("validations", []) or []),
            chosen_candidate=chosen.candidate_id if chosen is not None else None,
            executed_cypher=chosen.cypher_query if chosen is not None else None,
            parameters=dict(chosen.parameters) if chosen is not None else {},
            rows=list(getattr(chosen, "rows", []) or [])[:20],
            row_count=int(getattr(chosen, "row_count", 0) or 0),
            executed_sections=selected_executions,
            timings=merged_timings,
            fallback_used=False,
            fallback_reason="",
        )
        result = GraphRAGv2QueryResult(
            answer=answer,
            evidence=evidence,
            fallback_result=None,
        )
        payload = result.model_dump()
        self._write_debug_event(
            request_id=request_id,
            question=question,
            desired_shape=desired_shape,
            snapshot=snapshot,
            result=payload,
        )
        return payload

    async def _fallback(
        self,
        *,
        question: str,
        user_context: Dict[str, Any],
        request_id: str,
        started: float,
        fallback_reason: str,
        snapshot: Optional[GraphRAGv2SchemaSnapshot] = None,
        generated_candidates: Optional[List[GraphRAGv2CypherCandidate]] = None,
        validations: Optional[List[Any]] = None,
        execution_results: Optional[List[Any]] = None,
        timings: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        fallback_allowed = self._should_allow_legacy_fallback(snapshot)
        fallback_result: Optional[Dict[str, Any]] = None
        if fallback_allowed:
            fallback_result = await legacy_graphrag_service.query(question, user_context=user_context)
        evidence = GraphRAGv2EvidencePayload(
            schema_snapshot_id=snapshot.snapshot_id if snapshot else "",
            graph_profile=(snapshot.graph_profile if snapshot else "unknown_generic"),
            allowed_labels=(snapshot.allowed_labels if snapshot else []),
            allowed_relationships=(snapshot.allowed_relationships if snapshot else []),
            time_field_candidates=(snapshot.time_field_candidates if snapshot else []),
            service_now_hints_enabled=bool(snapshot.service_now_hints_enabled) if snapshot else False,
            generated_candidates=generated_candidates or [],
            validation_status=list(validations or []),
            chosen_candidate="",
            executed_cypher="",
            parameters={},
            rows=[],
            row_count=0,
            executed_sections=[
                execution if hasattr(execution, "model_dump") else execution
                for execution in (execution_results or [])
            ],
            timings={
                **(timings or {}),
                "total_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            fallback_used=True,
            fallback_reason=fallback_reason,
        )
        payload = GraphRAGv2QueryResult(
            answer=str(
                (fallback_result or {}).get("answer")
                or (
                    "No grounded answer could be produced from the current graph using the live schema."
                    if not fallback_allowed
                    else "No matching data found in the graph for this question."
                )
            ),
            evidence=evidence,
            fallback_result=(
                {
                    "answer": fallback_result.get("answer", ""),
                    "route": fallback_result.get("route", ""),
                    "normalized_result": fallback_result.get("normalized_result", {}),
                    "cypher": fallback_result.get("cypher", {}),
                    "execution_summaries": [
                        execution.model_dump() if hasattr(execution, "model_dump") else execution
                        for execution in (execution_results or [])
                    ],
                }
                if fallback_result is not None
                else None
            ),
        ).model_dump()
        self._write_debug_event(
            request_id=request_id,
            question=question,
            desired_shape=self._desired_shape(question),
            snapshot=snapshot,
            result=payload,
        )
        return payload

    def _desired_shape(self, question: str) -> str:
        query_lower = str(question or "").strip().lower()
        if any(phrase in query_lower for phrase in ("how many", "count", "number of")):
            return "count"
        if any(phrase in query_lower for phrase in ("what else", "shared", "both", "either", "not bought by")):
            return "cohort"
        if any(phrase in query_lower for phrase in ("compare", "difference", "versus", "vs")):
            return "compare"
        if any(phrase in query_lower for phrase in ("top", "most", "least", "highest", "lowest")):
            return "rank"
        if any(phrase in query_lower for phrase in ("which", "show", "list", "what requests", "what orders")):
            return "list"
        return "lookup"

    def _requested_output_shapes(self, question: str) -> List[str]:
        query_lower = str(question or "").strip().lower()
        requested: List[str] = []
        if any(phrase in query_lower for phrase in ("how many", "count", "number of")):
            requested.append("count")
        if any(
            phrase in query_lower
            for phrase in ("which", "show", "list", "give names", "give the names", "what requests", "what orders", "who ")
        ):
            requested.append("list")
        return requested or [self._desired_shape(question)]

    def _rank_candidates(
        self,
        candidates: List[GraphRAGv2CypherCandidate],
        desired_shape: str,
    ) -> List[GraphRAGv2CypherCandidate]:
        def sort_key(candidate: GraphRAGv2CypherCandidate) -> tuple[int, int, str]:
            shape_match = 1 if candidate.query_shape == desired_shape else 0
            aggregation_bonus = 1 if desired_shape == "count" and "count(" in candidate.cypher_query.lower() else 0
            return (-shape_match, -aggregation_bonus, candidate.candidate_id)

        return sorted(candidates, key=sort_key)

    def _prepare_candidates(
        self,
        candidates: List[GraphRAGv2CypherCandidate],
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> List[GraphRAGv2CypherCandidate]:
        prepared: List[GraphRAGv2CypherCandidate] = []
        for candidate in candidates:
            parameters = dict(candidate.parameters)
            if time_window and self._candidate_uses_parameterized_time_window(candidate.cypher_query):
                parameters["opened_start"] = time_window.start
                parameters["opened_end"] = time_window.end
            prepared.append(candidate.model_copy(update={"parameters": parameters}))

        if time_window and (time_window.start or time_window.end):
            parameterized = [
                candidate
                for candidate in prepared
                if self._candidate_uses_parameterized_time_window(candidate.cypher_query)
            ]
            if parameterized:
                return parameterized + [
                    candidate for candidate in prepared if candidate not in parameterized
                ]
        return prepared

    async def _run_candidate_route(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        desired_shape: str,
        requested_shapes: List[str],
        time_window: Optional[GraphRAGv2TimeWindow],
        route_name: str,
        query_family: str,
        planning_hints: List[str],
        required_evidence_fields: List[str],
        vector_hybrid_rows: List[Dict[str, Any]],
        grounding_rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        route: Dict[str, Any] = {
            "route_name": route_name,
            "query_family": query_family,
            "required_evidence_fields": required_evidence_fields,
            "planning_hints": planning_hints,
            "generated_candidates": [],
            "validations": [],
            "execution_results": [],
            "selected_executions": [],
            "chosen_execution": None,
            "execution_outcome": "generation_failure",
            "failure_reason": "",
            "repair_attempted": False,
            "repair_successful": False,
            "timings": {},
        }

        try:
            generate_started = time.perf_counter()
            generated_candidates = await self.cypher_generator.generate_candidates(
                question=question,
                schema_snapshot=snapshot,
                desired_shape=desired_shape,
                time_window=time_window,
                max_candidates=2,
                query_family=query_family,
                planning_hints=planning_hints,
                required_evidence_fields=required_evidence_fields,
            )
            route["timings"]["generate_candidates_ms"] = round((time.perf_counter() - generate_started) * 1000, 2)
        except Exception as exc:
            logger.exception("[GRAPHRAG_V2:%s] Candidate generation failed for route %s", route_name, route_name)
            route["failure_reason"] = f"candidate generation failed: {exc}"
            return route

        generated_candidates = self._rank_candidates(generated_candidates, desired_shape)
        generated_candidates = self._prepare_candidates(generated_candidates, time_window)
        route["generated_candidates"] = generated_candidates

        validations = self.query_guard.validate_candidates(generated_candidates)
        route["validations"] = validations
        valid_candidates = self.query_guard.valid_candidates(generated_candidates, validations)

        repaired_candidate: Optional[GraphRAGv2CypherCandidate] = None
        if not valid_candidates and generated_candidates:
            repaired_candidate = await self._attempt_candidate_repair(
                question=question,
                snapshot=snapshot,
                candidate=generated_candidates[0],
                error_message=str(validations[0].reason if validations else "candidate validation failed"),
                time_window=time_window,
                query_family=query_family,
                planning_hints=planning_hints,
                required_evidence_fields=required_evidence_fields,
            )
            if repaired_candidate is not None:
                route["repair_attempted"] = True
                generated_candidates = [repaired_candidate] + generated_candidates
                validations = self.query_guard.validate_candidates(generated_candidates)
                route["generated_candidates"] = generated_candidates
                route["validations"] = validations
                valid_candidates = self.query_guard.valid_candidates(generated_candidates, validations)
                route["repair_successful"] = bool(valid_candidates and valid_candidates[0].candidate_id == repaired_candidate.candidate_id)

        if not valid_candidates:
            route["failure_reason"] = "all generated candidates failed validation"
            return route

        execute_started = time.perf_counter()
        execution_results = await self.executor.execute_candidates(valid_candidates[:2])
        route["timings"]["execute_candidates_ms"] = round((time.perf_counter() - execute_started) * 1000, 2)
        route["execution_results"] = execution_results

        if self._is_similarity_family(query_family) and vector_hybrid_rows:
            route["execution_results"] = [
                GraphRAGv2ExecutionResult(
                    candidate_id=f"{route_name}_vector_hybrid",
                    query_shape="list",
                    purpose="Hybrid similarity candidates merged from structural, vector, and lexical signals",
                    cypher_query="GRAPH_RAG_VECTOR_HYBRID",
                    parameters={},
                    rows=list(vector_hybrid_rows[:20]),
                    row_count=len(vector_hybrid_rows[:20]),
                    ok=True,
                    error="",
                    duration_ms=0.0,
                ),
                *list(route["execution_results"]),
            ]

        if self._is_grounding_family(query_family) and grounding_rows:
            route["execution_results"] = [
                GraphRAGv2ExecutionResult(
                    candidate_id=f"{route_name}_grounding",
                    query_shape=desired_shape,
                    purpose="LLM grounding candidates expanded into graph evidence",
                    cypher_query="GRAPH_RAG_LLM_GROUNDING",
                    parameters={},
                    rows=list(grounding_rows[:20]),
                    row_count=len(grounding_rows[:20]),
                    ok=True,
                    error="",
                    duration_ms=0.0,
                ),
                *list(route["execution_results"]),
            ]

        if self._should_attempt_execution_repair(execution_results):
            route["repair_attempted"] = True
            repaired_candidate = await self._attempt_candidate_repair(
                question=question,
                snapshot=snapshot,
                candidate=valid_candidates[0],
                error_message=str(execution_results[0].error or "candidate execution failed"),
                time_window=time_window,
                query_family=query_family,
                planning_hints=planning_hints,
                required_evidence_fields=required_evidence_fields,
            )
            if repaired_candidate is not None:
                repaired_candidate = self._prepare_candidates([repaired_candidate], time_window)[0]
                repaired_validation = self.query_guard.validate_candidates([repaired_candidate])
                route["generated_candidates"] = [repaired_candidate] + list(route["generated_candidates"])
                route["validations"] = repaired_validation + list(route["validations"])
                if repaired_validation[0].valid:
                    repaired_execution = await self.executor.execute_candidate(repaired_candidate)
                    route["execution_results"] = [repaired_execution] + list(route["execution_results"])
                    route["repair_successful"] = repaired_execution.ok

        execution_results = list(route["execution_results"])
        selected_executions = self._select_execution_results(execution_results, requested_shapes)
        chosen = selected_executions[0] if selected_executions else self._choose_execution_result(execution_results)
        route["selected_executions"] = selected_executions
        route["chosen_execution"] = chosen
        route["execution_outcome"] = self._execution_outcome(
            execution_results=execution_results,
            selected_executions=selected_executions,
            required_evidence_fields=required_evidence_fields,
        )
        if route["execution_outcome"] == "generation_failure":
            route["failure_reason"] = "no valid candidate produced grounded rows"
        return route

    def _is_similarity_family(self, query_family: str) -> bool:
        return str(query_family or "").strip().lower() == "similar_entities_by_shared_problem"

    def _is_grounding_family(self, query_family: str) -> bool:
        return str(query_family or "").strip().lower() in {
            "recommendation_from_problem_evidence",
            "recurring_problems_for_entity",
            "similar_entities_by_shared_problem",
        }

    async def _run_grounding_layer(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        ontology_profile: GraphRAGv2OntologyProfile,
        query_family: str,
    ) -> GraphRAGv2GroundingResult:
        if not self._is_grounding_family(query_family):
            return GraphRAGv2GroundingResult(enabled=False)
        try:
            return await self.grounding_service.ground(
                question=question,
                snapshot=snapshot,
                ontology_profile=ontology_profile,
                query_family=query_family,
            )
        except Exception:
            logger.exception("[GRAPHRAG_V2] Grounding layer failed")
            return GraphRAGv2GroundingResult(enabled=False, error="grounding layer failed")

    async def _attempt_candidate_repair(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        candidate: GraphRAGv2CypherCandidate,
        error_message: str,
        time_window: Optional[GraphRAGv2TimeWindow],
        query_family: str,
        planning_hints: List[str],
        required_evidence_fields: List[str],
    ) -> Optional[GraphRAGv2CypherCandidate]:
        if not self._should_attempt_repair(error_message):
            return None
        try:
            return await self.cypher_generator.repair_candidate(
                question=question,
                schema_snapshot=snapshot,
                original_candidate=candidate,
                error_message=error_message,
                time_window=time_window,
                query_family=query_family,
                planning_hints=planning_hints,
                required_evidence_fields=required_evidence_fields,
            )
        except Exception:
            logger.exception("[GRAPHRAG_V2] Candidate repair failed")
            return None

    def _should_attempt_execution_repair(self, execution_results: List[Any]) -> bool:
        if not execution_results:
            return False
        first = execution_results[0]
        return bool(
            first is not None
            and not first.ok
            and self._should_attempt_repair(str(first.error or ""))
        )

    def _should_attempt_repair(self, error_message: str) -> bool:
        normalized = str(error_message or "").lower()
        if not normalized:
            return False
        return any(
            token in normalized
            for token in (
                "syntaxerror",
                "syntax error",
                "variable `",
                "not defined",
                "aggregation",
                "invalid input",
                "failed validation",
                "validation failed",
                "unsupported label",
                "unsupported relationship",
            )
        )

    def _execution_outcome(
        self,
        *,
        execution_results: List[Any],
        selected_executions: List[Any],
        required_evidence_fields: List[str],
    ) -> str:
        if selected_executions:
            strong_executions = [
                execution
                for execution in selected_executions
                if not self._is_weak_execution(execution)
            ]
            if not strong_executions:
                if self._has_grounded_empty(execution_results):
                    return "grounded_empty"
                return "generation_failure"
            if required_evidence_fields and not self._has_required_evidence(strong_executions, required_evidence_fields):
                return "generation_failure"
            return "grounded_success"
        if self._has_grounded_empty(execution_results):
            return "grounded_empty"
        return "generation_failure"

    def _has_grounded_empty(self, execution_results: List[Any]) -> bool:
        for execution in execution_results:
            if not execution.ok:
                continue
            if execution.row_count <= 0:
                return True
            if execution.row_count == 1 and execution.rows:
                row = execution.rows[0]
                numeric_count = next(
                    (
                        int(value or 0)
                        for key, value in row.items()
                        if isinstance(value, (int, float)) and "count" in str(key).lower()
                    ),
                    None,
                )
                if numeric_count == 0:
                    return True
        return False

    def _has_required_evidence(self, executions: List[Any], required_evidence_fields: List[str]) -> bool:
        if not required_evidence_fields:
            return True
        for execution in executions:
            for row in execution.rows:
                for field_name in required_evidence_fields:
                    value = row.get(field_name)
                    if value not in (None, "", []):
                        return True
        return False

    def _select_route_result(
        self,
        baseline_route: Dict[str, Any],
        ontology_route: Dict[str, Any],
        *,
        query_family: str,
    ) -> tuple[str, Dict[str, Any], str]:
        if not ontology_route:
            return "baseline_v2", baseline_route, "ontology planner not enabled for this query family"

        baseline_outcome = str(baseline_route.get("execution_outcome") or "")
        ontology_outcome = str(ontology_route.get("execution_outcome") or "")

        if ontology_outcome == "grounded_success" and baseline_outcome != "grounded_success":
            return "ontology_v2", ontology_route, "ontology route produced grounded evidence while baseline did not"
        if baseline_outcome == "grounded_success" and ontology_outcome != "grounded_success":
            return "baseline_v2", baseline_route, "baseline route already produced grounded evidence"
        if baseline_outcome == "grounded_empty" and ontology_outcome == "grounded_success":
            return "ontology_v2", ontology_route, "ontology route improved an otherwise empty baseline result"
        if baseline_outcome == "grounded_success" and ontology_outcome == "grounded_success":
            baseline_score = self._evidence_score(baseline_route, query_family=query_family)
            ontology_score = self._evidence_score(ontology_route, query_family=query_family)
            if ontology_score > baseline_score:
                return "ontology_v2", ontology_route, "ontology route returned richer required evidence"
            return "baseline_v2", baseline_route, "baseline route remains the safer grounded answer"
        if baseline_outcome == "grounded_empty" and ontology_outcome == "grounded_empty":
            return "baseline_v2", baseline_route, "both routes produced a grounded empty result"
        if baseline_outcome == "grounded_empty":
            return "baseline_v2", baseline_route, "baseline route produced a grounded empty result"
        if ontology_outcome == "grounded_empty":
            return "ontology_v2", ontology_route, "ontology route produced a grounded empty result"
        return "baseline_v2", baseline_route, "baseline route kept as the safer fallback path"

    def _evidence_score(self, route_result: Dict[str, Any], *, query_family: str) -> int:
        score = 0
        chosen = route_result.get("chosen_execution")
        if chosen is not None:
            score += int(getattr(chosen, "row_count", 0) or 0)
        required_fields = list(route_result.get("required_evidence_fields", []) or [])
        if required_fields and self._has_required_evidence(list(route_result.get("selected_executions", []) or []), required_fields):
            score += 100
        if query_family == "recommendation_from_problem_evidence":
            chosen_rows = list(getattr(chosen, "rows", []) or []) if chosen is not None else []
            if any(
                row.get(field_name) not in (None, "", [])
                for row in chosen_rows
                for field_name in ("workaround", "resolution_notes", "fix_notes", "cause_notes")
            ):
                score += 200
        return score

    def _grounded_empty_answer(self, question: str) -> str:
        return "No matching graph evidence was found for that question in the current graph."

    def _merge_route_timings(self, baseline_route: Dict[str, Any], ontology_route: Dict[str, Any]) -> Dict[str, float]:
        merged: Dict[str, float] = {}
        for prefix, route in (("baseline", baseline_route), ("ontology", ontology_route)):
            for key, value in dict(route.get("timings", {}) or {}).items():
                merged[f"{prefix}_{key}"] = value
        return merged

    def _route_debug_summary(self, route_result: Dict[str, Any]) -> Dict[str, Any]:
        if not route_result:
            return {}
        chosen = route_result.get("chosen_execution")
        return {
            "route_name": route_result.get("route_name", ""),
            "query_family": route_result.get("query_family", ""),
            "required_evidence_fields": list(route_result.get("required_evidence_fields", []) or []),
            "execution_outcome": route_result.get("execution_outcome", ""),
            "repair_attempted": bool(route_result.get("repair_attempted")),
            "repair_successful": bool(route_result.get("repair_successful")),
            "failure_reason": route_result.get("failure_reason", ""),
            "chosen_candidate": chosen.candidate_id if chosen is not None else "",
            "row_count": int(getattr(chosen, "row_count", 0) or 0),
        }

    def _candidate_uses_parameterized_time_window(self, cypher_query: str) -> bool:
        normalized = str(cypher_query or "")
        return "$opened_start" in normalized and "$opened_end" in normalized

    def _choose_execution_result(self, execution_results: List[Any]) -> Any:
        for execution in execution_results:
            if execution.ok and not self._is_weak_execution(execution):
                return execution
        return execution_results[0] if execution_results else None

    def _select_execution_results(
        self,
        execution_results: List[Any],
        requested_shapes: List[str],
    ) -> List[Any]:
        selected: List[Any] = []
        for shape in requested_shapes:
            for execution in execution_results:
                if execution.query_shape != shape:
                    continue
                if not execution.ok or self._is_weak_execution(execution):
                    continue
                selected.append(execution)
                break
        if selected:
            return selected
        chosen = self._choose_execution_result(execution_results)
        return [chosen] if chosen is not None else []

    def _is_weak_execution(self, execution_result: Any) -> bool:
        if not execution_result or not execution_result.ok:
            return True
        if execution_result.row_count <= 0:
            return True
        if execution_result.row_count == 1:
            row = execution_result.rows[0] if execution_result.rows else {}
            for numeric_key in (
                "count",
                "request_count",
                "catalog_item_count",
                "change_request_count",
                "service_request_count",
                "ticket_count",
            ):
                if numeric_key in row and int(row.get(numeric_key) or 0) == 0:
                    return True
        return False

    def _should_allow_legacy_fallback(self, snapshot: Optional[GraphRAGv2SchemaSnapshot]) -> bool:
        if str(getattr(settings, "graphrag_schema_strict_mode", "dynamic") or "dynamic").strip().lower() == "servicenow_strict":
            return True
        if snapshot is None:
            return False
        mode = str(getattr(settings, "graphrag_domain_hint_mode", "auto") or "auto").strip().lower()
        if mode == "servicenow_only":
            return True
        if mode == "generic_only":
            return False
        return snapshot.graph_profile == "servicenow_like"

    def _resolve_time_window(self, question: str) -> Optional[GraphRAGv2TimeWindow]:
        query_lower = str(question or "").strip().lower()
        if not query_lower:
            return None

        today = datetime.now().date()
        if "this week" in query_lower:
            start = today - timedelta(days=today.weekday())
            return GraphRAGv2TimeWindow(
                label="this week",
                start=self._format_window_start(start),
                end=self._format_window_start(start + timedelta(days=7)),
            )
        if "last week" in query_lower:
            this_week_start = today - timedelta(days=today.weekday())
            start = this_week_start - timedelta(days=7)
            return GraphRAGv2TimeWindow(
                label="last week",
                start=self._format_window_start(start),
                end=self._format_window_start(this_week_start),
            )
        if "this month" in query_lower:
            start = datetime(today.year, today.month, 1)
            end = datetime(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
            return GraphRAGv2TimeWindow(
                label="this month",
                start=self._format_window_start(start),
                end=self._format_window_start(end),
            )
        if "last month" in query_lower:
            this_month_start = datetime(today.year, today.month, 1)
            previous_month_end = this_month_start
            previous_month_start = datetime(
                previous_month_end.year - (1 if previous_month_end.month == 1 else 0),
                12 if previous_month_end.month == 1 else previous_month_end.month - 1,
                1,
            )
            return GraphRAGv2TimeWindow(
                label="last month",
                start=self._format_window_start(previous_month_start),
                end=self._format_window_start(previous_month_end),
            )
        return None

    def _format_window_start(self, value: datetime | Any) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return datetime(value.year, value.month, value.day).strftime("%Y-%m-%d %H:%M:%S")

    def _write_debug_event(
        self,
        *,
        request_id: str,
        question: str,
        desired_shape: str,
        snapshot: Optional[GraphRAGv2SchemaSnapshot],
        result: Dict[str, Any],
    ) -> None:
        debug_logger = _get_graphrag_v2_debug_logger()
        if debug_logger is None:
            return
        try:
            payload = {
                "pipeline": "graphrag_v2",
                "request_id": request_id,
                "query": question,
                "desired_shape": desired_shape,
                "schema_snapshot_id": snapshot.snapshot_id if snapshot else "",
                "graph_profile": snapshot.graph_profile if snapshot else "unknown_generic",
                "allowed_labels": snapshot.allowed_labels if snapshot else [],
                "allowed_relationships": snapshot.allowed_relationships if snapshot else [],
                "time_field_candidates": snapshot.time_field_candidates if snapshot else [],
                "service_now_hints_enabled": bool(snapshot.service_now_hints_enabled) if snapshot else False,
                "validation_context": neo4j_query_runtime_service.validation_context(),
                "result": result,
            }
            debug_logger.info(json.dumps(payload, ensure_ascii=True))
        except Exception:
            logger.exception("[GRAPHRAG_V2] Failed to write debug event")


graphrag_v2_service = GraphRAGv2Service()
