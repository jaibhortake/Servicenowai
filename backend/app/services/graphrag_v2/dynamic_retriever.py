from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .executor import GraphRAGv2Executor
from .generic_planner import GraphRAGv2GenericPlanner
from .query_guard import GraphRAGv2QueryGuard
from .types import (
    GraphRAGv2CypherCandidate,
    GraphRAGv2ExecutionResult,
    GraphRAGv2RelationshipType,
    GraphRAGv2SchemaSnapshot,
    GraphRAGv2SemanticProfile,
    GraphRAGv2TimeWindow,
    GraphRAGv2VirtualEdge,
)


_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "need", "want",
    "what", "are", "were", "was", "all", "any", "show", "give", "tell", "about",
    "between", "through", "during", "based", "linked", "common", "potential",
    "diagnosis", "sessions", "session", "issue", "issues", "problem", "problems",
    "water", "product", "products", "model", "models",
}
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True)
class _GraphRoles:
    session: str = ""
    product: str = ""
    issue: str = ""
    cause: str = ""
    resolution: str = ""
    condition: str = ""
    component: str = ""
    hypothesis: str = ""
    observation: str = ""
    question: str = ""


class GraphRAGv2DynamicRetriever:
    def __init__(self, query_guard: GraphRAGv2QueryGuard, executor: GraphRAGv2Executor) -> None:
        self.query_guard = query_guard
        self.executor = executor
        self.generic_planner = GraphRAGv2GenericPlanner()

    async def run(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        desired_shape: str,
        requested_shapes: List[str],
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> Optional[Dict[str, object]]:
        roles = self._infer_roles(snapshot, profile)
        generic_candidates = self.generic_planner.build_candidates(
            question=question,
            snapshot=snapshot,
            profile=profile,
            desired_shape=desired_shape,
            time_window=time_window or self.resolve_absolute_time_window(question),
        )
        domain_candidates = self._build_candidates(
            question=question,
            snapshot=snapshot,
            profile=profile,
            roles=roles,
            desired_shape=desired_shape,
            time_window=time_window or self.resolve_absolute_time_window(question),
        )
        candidates = self._dedupe_candidates(generic_candidates + domain_candidates)
        if not candidates:
            return None

        validations = self.query_guard.validate_candidates(candidates)
        valid_ids = {validation.candidate_id for validation in validations if validation.valid}
        valid_candidates = [candidate for candidate in candidates if candidate.candidate_id in valid_ids]
        if not valid_candidates:
            return None

        execution_results: List[GraphRAGv2ExecutionResult] = []
        for candidate in valid_candidates:
            execution_results.append(await self.executor.execute_candidate(candidate))

        strong_executions = [
            execution
            for execution in execution_results
            if execution.ok and execution.row_count > 0 and not self._zero_count_execution(execution)
        ]
        if not strong_executions:
            return None

        selected_executions = self._select_strong_executions(question, strong_executions)
        chosen = selected_executions[0]
        return {
            "route_name": "dynamic_profile_v1",
            "query_family": str(chosen.candidate_id).split("__", 1)[0],
            "required_evidence_fields": self._required_fields_for_question(question),
            "generated_candidates": candidates,
            "validations": validations,
            "execution_results": execution_results,
            "selected_executions": selected_executions,
            "chosen_execution": chosen,
            "execution_outcome": "grounded_success",
            "failure_reason": "",
            "repair_attempted": len(strong_executions) < len(valid_candidates),
            "repair_successful": bool(strong_executions),
            "timings": {},
        }

    def _build_candidates(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        roles: _GraphRoles,
        desired_shape: str,
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> List[GraphRAGv2CypherCandidate]:
        query_lower = question.lower()
        candidates: List[GraphRAGv2CypherCandidate] = []
        if self._looks_like_replacement_cycle_risk(query_lower):
            candidate = self._replacement_cycle_candidate(snapshot, profile, roles, question)
            if candidate:
                candidates.append(candidate)
            return candidates
        if self._looks_like_product_time_summary(query_lower, roles, question, time_window):
            candidates.extend(self._product_time_summary_candidates(snapshot, profile, roles, question, time_window))
            return candidates
        if self._looks_like_product_issue_ranking(query_lower):
            candidate = self._product_issue_ranking_candidate(snapshot, profile, roles)
            if candidate:
                candidates.append(candidate)
            return candidates
        if self._looks_like_in_progress_summary(query_lower):
            candidate = self._in_progress_summary_candidate(snapshot, profile, roles)
            if candidate:
                candidates.append(candidate)
            return candidates
        if self._looks_like_issue_cause_lookup(query_lower):
            candidate = self._issue_cause_candidate(snapshot, profile, roles, question, desired_shape)
            if candidate:
                candidates.append(candidate)
            return candidates
        return candidates

    def _issue_cause_candidate(
        self,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        roles: _GraphRoles,
        question: str,
        desired_shape: str,
    ) -> Optional[GraphRAGv2CypherCandidate]:
        if not roles.issue or not roles.cause or not self._has_rel(snapshot, roles.issue, "INDICATES", roles.cause):
            return None
        condition_clause = ""
        condition_return = "[] AS diagnostic_checks"
        if roles.condition and self._has_rel(snapshot, roles.cause, "REQUIRES_CHECK", roles.condition):
            condition_clause = f"OPTIONAL MATCH (cause)-[:REQUIRES_CHECK]->(check:{roles.condition})"
            condition_return = "collect(DISTINCT coalesce(check.key, check.name)) AS diagnostic_checks"
        resolution_clause = ""
        resolution_return = "[] AS resolutions, [] AS resolution_types"
        if roles.resolution:
            if self._has_rel(snapshot, roles.cause, "RESOLVED_BY", roles.resolution):
                resolution_clause = f"OPTIONAL MATCH (cause)-[:RESOLVED_BY]->(resolution:{roles.resolution})"
            else:
                resolution_clause = self._virtual_match(
                    profile,
                    known_var="cause",
                    known_label=roles.cause,
                    new_var="resolution",
                    new_label=roles.resolution,
                    preferred_properties=("cause_id", "root_cause_id"),
                    optional=True,
                )
            if resolution_clause:
                resolution_return = (
                    "collect(DISTINCT resolution.name) AS resolutions, "
                    "collect(DISTINCT resolution.resolution_type) AS resolution_types"
            )
        component_clause = ""
        component_return = "'' AS component"
        if roles.component:
            component_clause = self._virtual_match(
                profile,
                known_var="cause",
                known_label=roles.cause,
                new_var="component",
                new_label=roles.component,
                preferred_properties=("component_id",),
                optional=True,
            )
            if component_clause:
                component_return = "coalesce(component.name, '') AS component"

        cypher = f"""
MATCH (issue:{roles.issue})-[:INDICATES]->(cause:{roles.cause})
WHERE ANY(term IN $subject_terms WHERE
    toLower(coalesce(issue.name, '')) CONTAINS term OR
    toLower(coalesce(issue.subcategory, '')) CONTAINS term OR
    toLower(coalesce(issue.description, '')) CONTAINS term
)
{condition_clause}
{resolution_clause}
{component_clause}
RETURN issue.name AS issue,
       issue.subcategory AS subcategory,
       issue.description AS issue_description,
       cause.name AS potential_cause,
       cause.severity AS severity,
       {component_return},
       {condition_return},
       {resolution_return}
ORDER BY CASE severity WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 1 ELSE 0 END DESC,
         potential_cause ASC
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id="issue_cause_lookup__primary",
            query_shape=desired_shape if desired_shape in {"lookup", "list"} else "lookup",
            purpose="Resolve issue terms to potential causes and remediation evidence",
            cypher_query=cypher,
            parameters={"subject_terms": self.issue_terms(question), "limit": 20},
        )

    def _product_issue_ranking_candidate(
        self,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        roles: _GraphRoles,
    ) -> Optional[GraphRAGv2CypherCandidate]:
        if not (roles.session and roles.product and self._has_rel(snapshot, roles.session, "INVOLVES_PRODUCT", roles.product)):
            return None
        issue_clause = ""
        session_issues_expr = "[] AS session_issues"
        if roles.issue and self._has_rel(snapshot, roles.session, "HAS_ISSUE", roles.issue):
            issue_clause = f"OPTIONAL MATCH (session)-[:HAS_ISSUE]->(issue:{roles.issue})"
            session_issues_expr = "collect(DISTINCT coalesce(issue.subcategory, issue.name)) AS session_issues"
        hypothesis_clause = ""
        session_hypotheses_expr = "[] AS session_hypotheses"
        session_highest_score_expr = "0.0 AS session_highest_score"
        if roles.hypothesis:
            hypothesis_clause = self._virtual_match(
                profile,
                known_var="session",
                known_label=roles.session,
                new_var="hypothesis",
                new_label=roles.hypothesis,
                preferred_properties=("session",),
                optional=True,
            )
            if hypothesis_clause:
                session_hypotheses_expr = "collect(DISTINCT hypothesis.hypothesis) AS session_hypotheses"
                session_highest_score_expr = "max(toFloat(coalesce(hypothesis.score, '0'))) AS session_highest_score"
        cypher = f"""
MATCH (session:{roles.session})-[:INVOLVES_PRODUCT]->(product:{roles.product})
{issue_clause}
{hypothesis_clause}
WITH product,
     session,
     {session_issues_expr},
     {session_hypotheses_expr},
     {session_highest_score_expr}
WITH product,
     collect(DISTINCT session) AS sessions,
     collect(DISTINCT session_issues) AS issue_groups,
     collect(DISTINCT session_hypotheses) AS hypothesis_groups,
     max(session_highest_score) AS highest_hypothesis_score,
     max(toString(coalesce(session.started_at, ''))) AS most_recent_started_at
WITH product, sessions, issue_groups, hypothesis_groups, highest_hypothesis_score, most_recent_started_at,
     [s IN sessions WHERE toString(coalesce(s.started_at, '')) = most_recent_started_at][0] AS most_recent_session
RETURN coalesce(product.model, product.name) AS product_model,
       product.name AS product_name,
       size(sessions) AS issue_count,
       size([s IN sessions WHERE toLower(coalesce(s.status, '')) = 'completed']) AS completed_count,
       size([s IN sessions WHERE toLower(coalesce(s.status, '')) = 'in progress']) AS in_progress_count,
       most_recent_session.incident_number AS most_recent_incident,
       most_recent_started_at,
       reduce(flat = [], group IN issue_groups | flat + group)[0..20] AS issue_subcategories,
       reduce(flat = [], group IN hypothesis_groups | flat + group)[0..20] AS common_hypotheses,
       highest_hypothesis_score
ORDER BY issue_count DESC, product_model ASC
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id="product_issue_ranking__primary",
            query_shape="rank",
            purpose="Rank products by diagnosis session issue frequency with status and hypothesis evidence",
            cypher_query=cypher,
            parameters={"limit": 20},
        )

    def _product_time_summary_candidates(
        self,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        roles: _GraphRoles,
        question: str,
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> List[GraphRAGv2CypherCandidate]:
        if not time_window or not roles.session or not roles.product:
            return []
        if not self._has_rel(snapshot, roles.session, "INVOLVES_PRODUCT", roles.product):
            return []
        product_terms = self.product_terms(question)
        if not product_terms:
            return []
        candidates: List[GraphRAGv2CypherCandidate] = []
        if roles.issue and self._has_rel(snapshot, roles.session, "HAS_ISSUE", roles.issue):
            candidates.append(
                GraphRAGv2CypherCandidate(
                    candidate_id="product_time_summary__issue_counts",
                    query_shape="aggregate",
                    purpose="Issue counts by subcategory for the resolved product and time window",
                    cypher_query=f"""
MATCH (session:{roles.session})-[:INVOLVES_PRODUCT]->(product:{roles.product})
MATCH (session)-[:HAS_ISSUE]->(issue:{roles.issue})
WHERE ANY(term IN $item_terms WHERE
    toLower(coalesce(product.name, '')) CONTAINS term OR
    toLower(coalesce(product.model, '')) CONTAINS term
)
  AND toString(coalesce(session.started_at, '')) >= $opened_start
  AND toString(coalesce(session.started_at, '')) < $opened_end
RETURN coalesce(issue.subcategory, issue.name) AS subcategory,
       count(DISTINCT session) AS issue_count
ORDER BY issue_count DESC, subcategory ASC
LIMIT $limit
""".strip(),
                    parameters={
                        "item_terms": product_terms,
                        "opened_start": time_window.start,
                        "opened_end": time_window.end,
                        "limit": 20,
                    },
                )
            )
        hypothesis_match = ""
        if roles.hypothesis:
            hypothesis_match = self._virtual_match(
                profile,
                known_var="session",
                known_label=roles.session,
                new_var="hypothesis",
                new_label=roles.hypothesis,
                preferred_properties=("session",),
                optional=False,
            )
        if hypothesis_match:
            candidates.append(
                GraphRAGv2CypherCandidate(
                    candidate_id="product_time_summary__hypotheses",
                    query_shape="rank",
                    purpose="Common hypotheses and highest scores for the resolved product and time window",
                    cypher_query=f"""
MATCH (session:{roles.session})-[:INVOLVES_PRODUCT]->(product:{roles.product})
{hypothesis_match}
WITH session, product, hypothesis
WHERE ANY(term IN $item_terms WHERE
    toLower(coalesce(product.name, '')) CONTAINS term OR
    toLower(coalesce(product.model, '')) CONTAINS term
  )
  AND toString(coalesce(session.started_at, '')) >= $opened_start
  AND toString(coalesce(session.started_at, '')) < $opened_end
RETURN hypothesis.hypothesis AS hypothesis,
       count(DISTINCT session) AS session_count,
       max(toFloat(coalesce(hypothesis.score, '0'))) AS highest_score,
       collect(DISTINCT session.incident_number)[0..5] AS sample_incidents
ORDER BY session_count DESC, highest_score DESC, hypothesis ASC
LIMIT $limit
""".strip(),
                    parameters={
                        "item_terms": product_terms,
                        "opened_start": time_window.start,
                        "opened_end": time_window.end,
                        "limit": 20,
                    },
                )
            )
        candidates.append(
            GraphRAGv2CypherCandidate(
                candidate_id="product_time_summary__in_progress",
                query_shape="list",
                purpose="In-progress incidents for the resolved product and time window",
                cypher_query=f"""
MATCH (session:{roles.session})-[:INVOLVES_PRODUCT]->(product:{roles.product})
WHERE ANY(term IN $item_terms WHERE
    toLower(coalesce(product.name, '')) CONTAINS term OR
    toLower(coalesce(product.model, '')) CONTAINS term
)
  AND ANY(state IN $state_terms WHERE toLower(coalesce(session.status, '')) CONTAINS state)
  AND toString(coalesce(session.started_at, '')) >= $opened_start
  AND toString(coalesce(session.started_at, '')) < $opened_end
{f"OPTIONAL MATCH (session)-[:HAS_ISSUE]->(issue:{roles.issue})" if roles.issue and self._has_rel(snapshot, roles.session, "HAS_ISSUE", roles.issue) else ""}
RETURN session.incident_number AS incident,
       session.sys_id AS session_id,
       session.started_at AS started_at,
       session.status AS status,
       {("coalesce(issue.subcategory, issue.name, '') AS issue_subcategory" if roles.issue and self._has_rel(snapshot, roles.session, "HAS_ISSUE", roles.issue) else "'' AS issue_subcategory")}
ORDER BY started_at DESC
LIMIT $limit
""".strip(),
                parameters={
                    "item_terms": product_terms,
                    "state_terms": ["in progress"],
                    "opened_start": time_window.start,
                    "opened_end": time_window.end,
                    "limit": 20,
                },
            )
        )
        return candidates

    def _replacement_cycle_candidate(
        self,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        roles: _GraphRoles,
        question: str,
    ) -> Optional[GraphRAGv2CypherCandidate]:
        if not (roles.session and roles.product and roles.issue):
            return None
        if not self._has_rel(snapshot, roles.session, "INVOLVES_PRODUCT", roles.product):
            return None
        if not self._has_rel(snapshot, roles.session, "HAS_ISSUE", roles.issue):
            return None
        cause_clause = ""
        if roles.cause and self._has_rel(snapshot, roles.issue, "INDICATES", roles.cause):
            cause_clause = f"OPTIONAL MATCH (issue)-[:INDICATES]->(cause:{roles.cause})"
        resolution_clause = ""
        resolution_return = "[] AS resolution_types, [] AS resolutions"
        if cause_clause and roles.resolution:
            if self._has_rel(snapshot, roles.cause, "RESOLVED_BY", roles.resolution):
                resolution_clause = f"OPTIONAL MATCH (cause)-[:RESOLVED_BY]->(resolution:{roles.resolution})"
            else:
                resolution_clause = self._virtual_match(
                    profile,
                    known_var="cause",
                    known_label=roles.cause,
                    new_var="resolution",
                    new_label=roles.resolution,
                    preferred_properties=("cause_id", "root_cause_id"),
                    optional=True,
                )
            if resolution_clause:
                resolution_return = (
                    "collect(DISTINCT resolution.resolution_type) AS resolution_types, "
                    "collect(DISTINCT resolution.name) AS resolutions"
            )
        component_clause = ""
        component_return = "[] AS linked_components"
        if cause_clause and roles.component:
            component_clause = self._virtual_match(
                profile,
                known_var="cause",
                known_label=roles.cause,
                new_var="component",
                new_label=roles.component,
                preferred_properties=("component_id",),
                optional=True,
            )
            if component_clause:
                component_return = "collect(DISTINCT component.name) AS linked_components"
        cypher = f"""
MATCH (session:{roles.session})-[:INVOLVES_PRODUCT]->(product:{roles.product})
MATCH (session)-[:HAS_ISSUE]->(issue:{roles.issue})
WHERE toInteger(coalesce(product.replacement_cycle_months, '999999')) <= $threshold
  AND ANY(term IN $subject_terms WHERE
    toLower(coalesce(issue.subcategory, '')) CONTAINS term OR
    toLower(coalesce(issue.name, '')) CONTAINS term OR
    toLower(coalesce(issue.description, '')) CONTAINS term
  )
{cause_clause}
{resolution_clause}
{component_clause}
RETURN coalesce(product.model, product.name) AS product_model,
       product.name AS product_name,
       product.category AS product_category,
       product.replacement_cycle_months AS replacement_cycle_months,
       coalesce(issue.subcategory, issue.name) AS issue_subcategory,
       count(DISTINCT session) AS issue_count,
       {component_return},
       {resolution_return}
ORDER BY issue_count DESC, product_model ASC, issue_subcategory ASC
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id="replacement_cycle_risk__primary",
            query_shape="rank",
            purpose="Rank short replacement-cycle products by selected issue sessions",
            cypher_query=cypher,
            parameters={"subject_terms": self.issue_terms(question), "threshold": 6, "limit": 20},
        )

    def _in_progress_summary_candidate(
        self,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        roles: _GraphRoles,
    ) -> Optional[GraphRAGv2CypherCandidate]:
        if not roles.session:
            return None
        product_clause = (
            f"OPTIONAL MATCH (session)-[:INVOLVES_PRODUCT]->(product:{roles.product})"
            if roles.product and self._has_rel(snapshot, roles.session, "INVOLVES_PRODUCT", roles.product)
            else ""
        )
        product_return = (
            "coalesce(product.name, '') AS product,\n       coalesce(product.model, '') AS model"
            if product_clause
            else "'' AS product,\n       '' AS model"
        )
        issue_clause = (
            f"OPTIONAL MATCH (session)-[:HAS_ISSUE]->(issue:{roles.issue})"
            if roles.issue and self._has_rel(snapshot, roles.session, "HAS_ISSUE", roles.issue)
            else ""
        )
        issue_return = (
            "coalesce(issue.subcategory, issue.name, '') AS issue_subcategory"
            if issue_clause
            else "'' AS issue_subcategory"
        )
        observation_clause = ""
        observation_return = "[] AS latest_observations"
        if roles.observation:
            observation_clause = self._virtual_match(
                profile,
                known_var="session",
                known_label=roles.session,
                new_var="observation",
                new_label=roles.observation,
                preferred_properties=("session",),
                optional=True,
            )
            if observation_clause:
                observation_return = "collect(DISTINCT observation {.condition_key, .observed_value, .captured_at})[0..5] AS latest_observations"
        question_clause = ""
        question_return = "[] AS unanswered_questions"
        if roles.question:
            question_clause = self._virtual_match(
                profile,
                known_var="session",
                known_label=roles.session,
                new_var="llm_question",
                new_label=roles.question,
                preferred_properties=("session",),
                optional=True,
            )
            if question_clause:
                question_clause += "\n  AND (llm_question.answer_received IS NULL OR toLower(toString(llm_question.answer_received)) IN ['', 'false', 'no'])"
                question_return = "collect(DISTINCT llm_question {.question, .condition_key, .question_rank})[0..5] AS unanswered_questions"
        hypothesis_clause = ""
        hypothesis_return = "[] AS top_hypotheses"
        if roles.hypothesis:
            hypothesis_clause = self._virtual_match(
                profile,
                known_var="session",
                known_label=roles.session,
                new_var="hypothesis",
                new_label=roles.hypothesis,
                preferred_properties=("session",),
                optional=True,
            )
            if hypothesis_clause:
                hypothesis_return = "collect(DISTINCT hypothesis {.hypothesis, .score})[0..5] AS top_hypotheses"
        resolution_clause = ""
        resolution_return = "[] AS next_resolution_paths"
        if roles.issue and roles.cause and roles.resolution and self._has_rel(snapshot, roles.issue, "INDICATES", roles.cause):
            cause_clause = f"OPTIONAL MATCH (issue)-[:INDICATES]->(cause:{roles.cause})"
            if self._has_rel(snapshot, roles.cause, "RESOLVED_BY", roles.resolution):
                resolution_join = f"OPTIONAL MATCH (cause)-[:RESOLVED_BY]->(resolution:{roles.resolution})"
            else:
                resolution_join = self._virtual_match(
                    profile,
                    known_var="cause",
                    known_label=roles.cause,
                    new_var="resolution",
                    new_label=roles.resolution,
                    preferred_properties=("cause_id", "root_cause_id"),
                    optional=True,
                )
            if resolution_join:
                resolution_clause = f"""
OPTIONAL MATCH (issue)-[:INDICATES]->(cause:{roles.cause})
{resolution_join}
""".strip()
                resolution_return = (
                    "collect(DISTINCT {root_cause: cause.name, severity: cause.severity, "
                    "resolution: resolution.name, resolution_type: resolution.resolution_type})[0..5] AS next_resolution_paths"
                )
        cypher = f"""
MATCH (session:{roles.session})
WHERE ANY(state IN $state_terms WHERE toLower(coalesce(session.status, '')) CONTAINS state)
{product_clause}
{issue_clause}
{observation_clause}
{question_clause}
{hypothesis_clause}
{resolution_clause}
RETURN session.incident_number AS incident,
       session.sys_id AS session_id,
       session.started_at AS started_at,
       session.status AS status,
       {product_return},
       {issue_return},
       {observation_return},
       {question_return},
       {hypothesis_return},
       {resolution_return}
ORDER BY started_at DESC
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id="in_progress_summary__primary",
            query_shape="list",
            purpose="Summarize in-progress sessions with product, issue, observations, questions, hypotheses, and resolutions",
            cypher_query=cypher,
            parameters={"state_terms": ["in progress"], "limit": 20},
        )

    def _infer_roles(self, snapshot: GraphRAGv2SchemaSnapshot, profile: GraphRAGv2SemanticProfile) -> _GraphRoles:
        labels = {node.label for node in snapshot.node_types}
        props_by_label = {node.label: {prop.lower() for prop in node.properties} for node in snapshot.node_types}
        return _GraphRoles(
            session=self._find_label(labels, props_by_label, "DiagnosisSession", {"incident_number", "started_at", "status"}),
            product=self._find_label(labels, props_by_label, "Product", {"model", "replacement_cycle_months"}),
            issue=self._find_label(labels, props_by_label, "IssueTaxonomy", {"subcategory", "description"}),
            cause=self._find_label(labels, props_by_label, "RootCause", {"severity", "component_id"}),
            resolution=self._find_label(labels, props_by_label, "Resolution", {"resolution_type", "steps"}),
            condition=self._find_label(labels, props_by_label, "ConditionDefinition", {"condition_type", "observation_source"}),
            component=self._find_label(labels, props_by_label, "Component", {"sensitivity", "type", "name"}),
            hypothesis=self._find_label(labels, props_by_label, "HypothesisScore", {"hypothesis", "score", "session"}),
            observation=self._find_label(labels, props_by_label, "Observation", {"observed_value", "condition_key", "session"}),
            question=self._find_label(labels, props_by_label, "LLMQuestion", {"question", "answer_received", "session"}),
        )

    @staticmethod
    def _find_label(labels: set[str], props_by_label: Dict[str, set[str]], preferred: str, required_props: set[str]) -> str:
        if preferred in labels:
            return preferred
        best_label = ""
        best_score = 0
        for label, props in props_by_label.items():
            score = len(props & required_props)
            if score > best_score:
                best_label = label
                best_score = score
        return best_label if best_score >= max(1, min(2, len(required_props))) else ""

    @staticmethod
    def _has_rel(snapshot: GraphRAGv2SchemaSnapshot, from_label: str, rel_type: str, to_label: str) -> bool:
        return any(
            rel.from_label == from_label and rel.rel_type == rel_type and rel.to_label == to_label
            for rel in snapshot.relationships
        )

    def _virtual_match(
        self,
        profile: GraphRAGv2SemanticProfile,
        *,
        known_var: str,
        known_label: str,
        new_var: str,
        new_label: str,
        preferred_properties: tuple[str, ...] = (),
        optional: bool = True,
    ) -> str:
        edge = self._best_virtual_edge(profile, known_label, new_label, preferred_properties)
        if edge is None:
            return ""
        clause = "OPTIONAL MATCH" if optional else "MATCH"
        if edge.from_label == known_label and edge.to_label == new_label:
            condition = f"{known_var}.{edge.from_property} = {new_var}.{edge.to_property}"
        else:
            condition = f"{new_var}.{edge.from_property} = {known_var}.{edge.to_property}"
        return f"{clause} ({new_var}:{new_label}) WHERE {condition}"

    @staticmethod
    def _best_virtual_edge(
        profile: GraphRAGv2SemanticProfile,
        label_a: str,
        label_b: str,
        preferred_properties: tuple[str, ...] = (),
    ) -> Optional[GraphRAGv2VirtualEdge]:
        preferred = {property_name.lower() for property_name in preferred_properties}
        candidates = [
            edge
            for edge in profile.virtual_edges
            if (
                (edge.from_label == label_a and edge.to_label == label_b)
                or (edge.from_label == label_b and edge.to_label == label_a)
            )
        ]
        if not candidates:
            return None

        def score(edge: GraphRAGv2VirtualEdge) -> tuple[float, int, int, str]:
            property_bonus = 0
            if edge.from_property.lower() in preferred:
                property_bonus += 2
            if edge.to_property.lower() in {"sys_id", "id", "key", "number"}:
                property_bonus += 1
            return (
                float(edge.confidence or 0.0),
                int(property_bonus),
                int(edge.sample_overlap or 0),
                edge.from_property,
            )

        return sorted(candidates, key=score, reverse=True)[0]

    def _select_strong_executions(
        self,
        question: str,
        executions: List[GraphRAGv2ExecutionResult],
    ) -> List[GraphRAGv2ExecutionResult]:
        ranked = sorted(
            executions,
            key=lambda execution: self._execution_score(question, execution),
            reverse=True,
        )
        chosen_family = str(ranked[0].candidate_id).split("__", 1)[0]
        selected = [
            execution
            for execution in ranked
            if str(execution.candidate_id).split("__", 1)[0] == chosen_family
        ][:4]
        return selected or ranked[:1]

    def _execution_score(self, question: str, execution: GraphRAGv2ExecutionResult) -> float:
        query_lower = str(question or "").lower()
        candidate_id = str(execution.candidate_id or "")
        score = float(min(int(execution.row_count or 0), 20))
        if not candidate_id.startswith("generic_"):
            score += 12.0
        if execution.query_shape in {"rank", "aggregate", "count"} and any(
            token in query_lower for token in ("rank", "most", "count", "counts", "frequency", "group")
        ):
            score += 8.0
        if execution.query_shape in {"lookup", "list"} and any(
            token in query_lower for token in ("what", "which", "show", "list", "cause", "resolution")
        ):
            score += 4.0
        has_numeric_threshold = bool(re.search(r"\b\d{1,9}\b", query_lower)) and any(
            token in query_lower
            for token in ("less", "below", "under", "at most", "or less", "greater", "above", "over", "more", ">=", "<=")
        )
        if has_numeric_threshold and candidate_id.startswith("generic_numeric_threshold"):
            score += 28.0
        if has_numeric_threshold and candidate_id.startswith("generic_filtered_list"):
            score -= 10.0
        field_names = {
            str(key).lower()
            for row in (execution.rows or [])[:5]
            for key in row.keys()
        }
        field_text = " ".join(field_names)
        requested_field_tokens = (
            ("incident", ("incident", "incident_number")),
            ("hypothesis", ("hypothesis", "score")),
            ("score", ("score", "highest_score")),
            ("resolution", ("resolution", "resolution_type")),
            ("observation", ("observation", "latest_observations")),
            ("question", ("question", "unanswered_questions")),
            ("component", ("component", "linked_components")),
            ("status", ("status", "state")),
            ("category", ("category", "subcategory")),
        )
        for query_token, possible_fields in requested_field_tokens:
            if query_token in query_lower and any(field in field_text for field in possible_fields):
                score += 6.0
        if any("count" in field for field in field_names):
            score += 4.0
        if any(field in field_names for field in {"anchor", "related", "group_node", "sample_related", "node"}):
            score += 2.0
        if "cause" in query_lower and any("cause" in field for field in field_names):
            score += 8.0
        return score

    @staticmethod
    def _dedupe_candidates(candidates: List[GraphRAGv2CypherCandidate]) -> List[GraphRAGv2CypherCandidate]:
        result: List[GraphRAGv2CypherCandidate] = []
        seen = set()
        for candidate in candidates:
            if candidate.candidate_id in seen:
                continue
            seen.add(candidate.candidate_id)
            result.append(candidate)
        return result

    @staticmethod
    def _zero_count_execution(execution: GraphRAGv2ExecutionResult) -> bool:
        if execution.row_count != 1 or not execution.rows:
            return False
        row = execution.rows[0]
        count_values = [
            value for key, value in row.items()
            if isinstance(value, (int, float)) and "count" in str(key).lower()
        ]
        return bool(count_values) and all(int(value or 0) == 0 for value in count_values)

    def issue_terms(self, question: str) -> List[str]:
        terms = self._question_terms(question)
        query_lower = question.lower()
        if "taste" in query_lower or "odor" in query_lower:
            terms.extend(["taste", "odor", "chlorine", "metallic", "cloudy", "taste/odor"])
        if "wrong" in query_lower and "cartridge" in query_lower:
            terms.extend(["wrong cartridge delivered", "wrong cartridge", "cartridge delivered"])
        if "low flow" in query_lower:
            terms.append("low flow")
        if "no water" in query_lower:
            terms.append("no water")
        return self._dedupe(terms)

    def product_terms(self, question: str) -> List[str]:
        terms: List[str] = []
        for match in re.finditer(r"\b(?:e\s+)?[a-z]{0,3}-?\d{2,4}[a-z]?\b", question, flags=re.IGNORECASE):
            raw = match.group(0).strip()
            if raw:
                terms.append(raw)
                terms.append(raw.replace("-", ""))
                if not raw.lower().startswith("e "):
                    terms.append(f"e {raw}")
        claris = re.search(r"\bclaris\b", question, flags=re.IGNORECASE)
        if claris:
            terms.extend(["claris", "e claris ultra"])
        return self._dedupe([term.lower() for term in terms])

    def _question_terms(self, question: str) -> List[str]:
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", question.lower())
            if len(token) >= 3 and token not in _STOP_WORDS
        ]
        return self._dedupe(tokens)

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for value in values:
            normalized = str(value or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            result.append(normalized)
            seen.add(normalized)
        return result

    @staticmethod
    def _required_fields_for_question(question: str) -> List[str]:
        query_lower = question.lower()
        fields: List[str] = []
        for token, field in (
            ("component", "component"),
            ("incident", "incident"),
            ("hypothesis", "hypothesis"),
            ("score", "score"),
            ("resolution", "resolution"),
            ("observation", "latest_observations"),
            ("question", "unanswered_questions"),
        ):
            if token in query_lower:
                fields.append(field)
        return fields

    @staticmethod
    def _looks_like_issue_cause_lookup(query_lower: str) -> bool:
        return any(term in query_lower for term in ("cause", "causes", "root cause", "resolution", "diagnostic check", "potential"))

    @staticmethod
    def _looks_like_product_issue_ranking(query_lower: str) -> bool:
        return "product" in query_lower and any(term in query_lower for term in ("rank", "most", "frequency")) and "issue" in query_lower

    def _looks_like_product_time_summary(
        self,
        query_lower: str,
        roles: _GraphRoles,
        question: str,
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> bool:
        return bool(
            roles.product
            and (time_window is not None or "between" in query_lower)
            and self.product_terms(question)
            and any(term in query_lower for term in ("issue count", "hypothesis", "in-progress", "in progress"))
        )

    @staticmethod
    def _looks_like_replacement_cycle_risk(query_lower: str) -> bool:
        return "replacement cycle" in query_lower and any(term in query_lower for term in ("leak", "scal", "wrong"))

    @staticmethod
    def _looks_like_in_progress_summary(query_lower: str) -> bool:
        return ("in-progress" in query_lower or "in progress" in query_lower or "open " in query_lower) and (
            "observation" in query_lower or "hypothesis" in query_lower or "diagnosis" in query_lower
        )

    @staticmethod
    def resolve_absolute_time_window(question: str) -> Optional[GraphRAGv2TimeWindow]:
        matches = list(
            re.finditer(
                r"\b("
                + "|".join(_MONTHS.keys())
                + r")\s+(\d{1,2}),\s*(\d{4})",
                question,
                flags=re.IGNORECASE,
            )
        )
        if len(matches) < 2:
            return None

        def parse(match: re.Match[str]) -> tuple[int, int, int]:
            return (
                int(match.group(3)),
                _MONTHS[match.group(1).lower()],
                int(match.group(2)),
            )

        start_year, start_month, start_day = parse(matches[0])
        end_year, end_month, end_day = parse(matches[1])
        start = f"{start_year:04d}-{start_month:02d}-{start_day:02d} 00:00:00"
        # User-facing date ranges are typically inclusive; use the next day as exclusive upper bound.
        from datetime import date, timedelta
        end_date = date(end_year, end_month, end_day) + timedelta(days=1)
        end = f"{end_date.year:04d}-{end_date.month:02d}-{end_date.day:02d} 00:00:00"
        return GraphRAGv2TimeWindow(
            label=f"{matches[0].group(0)} to {matches[1].group(0)}",
            start=start,
            end=end,
        )
