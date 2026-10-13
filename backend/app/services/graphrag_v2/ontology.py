from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .types import (
    GraphRAGv2OntologyProfile,
    GraphRAGv2RelationshipType,
    GraphRAGv2SchemaSnapshot,
)

_ENTITY_PRIMARY_HINTS = {"name", "location", "vendor", "manufacturer", "model", "model_number", "asset_tag"}
_ENTITY_SECONDARY_HINTS = {"sys_id", "serial_number", "hostname", "ip_address"}
_EVENT_PRIMARY_HINTS = {"opened_at", "created_at", "updated_at", "timestamp", "date", "short_description"}
_EVENT_SECONDARY_HINTS = {"state", "priority", "severity", "description", "resolution_notes", "close_notes"}
_PROBLEM_PRIMARY_HINTS = {"workaround", "fix_notes", "cause_notes"}
_PROBLEM_SECONDARY_HINTS = {"resolution_notes", "known_error", "root_cause"}
_REQUEST_PRIMARY_HINTS = {"number", "quantity"}
_REQUEST_SECONDARY_HINTS = {"state", "description", "resolution_notes", "requested_for", "requested_by"}
_RECOMMENDATION_FIELDS = ("workaround", "resolution_notes", "fix_notes", "cause_notes")
_INTENT_SEARCH_FIELDS: Dict[str, Dict[str, List[str]]] = {
    "recommendation_from_problem_evidence": {
        "Incident": ["short_description", "description", "resolution_notes", "close_notes"],
        "Problem": [
            "short_description",
            "description",
            "cause_notes",
            "workaround",
            "resolution_notes",
            "fix_notes",
        ],
        "ServiceRequest": ["short_description", "description", "resolution_notes"],
    },
}
_OPERATIONAL_ALIAS_HINTS = (
    "battery backup unit may appear as BBU",
    "degradation may appear as degraded, failed, or failure",
)


class GraphRAGv2OntologyPlanner:
    def infer_profile(self, snapshot: GraphRAGv2SchemaSnapshot) -> GraphRAGv2OntologyProfile:
        entity_labels: List[str] = []
        event_labels: List[str] = []
        problem_labels: List[str] = []
        request_labels: List[str] = []
        search_fields_by_label: Dict[str, List[str]] = {}
        time_fields = list(snapshot.time_field_candidates)

        for node in snapshot.node_types:
            props = {prop.lower() for prop in node.properties}
            search_fields_by_label[node.label] = list(node.properties)
            if self._looks_like_entity(props):
                entity_labels.append(node.label)
            if self._looks_like_event(props):
                event_labels.append(node.label)
            if self._looks_like_problem(props):
                problem_labels.append(node.label)
            if self._looks_like_request(props):
                request_labels.append(node.label)

        role_paths = self._infer_role_paths(snapshot.relationships, entity_labels, event_labels, problem_labels, request_labels)
        recommendation_fields = sorted(
            {
                field_name
                for node in snapshot.node_types
                for field_name in node.properties
                if field_name in _RECOMMENDATION_FIELDS
            }
        )

        return GraphRAGv2OntologyProfile(
            entity_labels=sorted(set(entity_labels)),
            event_labels=sorted(set(event_labels)),
            problem_labels=sorted(set(problem_labels)),
            request_labels=sorted(set(request_labels)),
            search_fields_by_label=search_fields_by_label,
            time_fields=time_fields,
            recommendation_fields=recommendation_fields,
            role_paths=role_paths,
        )

    def detect_query_family(
        self,
        question: str,
        ontology_profile: GraphRAGv2OntologyProfile,
    ) -> Tuple[str, List[str], str]:
        query_lower = str(question or "").strip().lower()
        if not query_lower:
            return "", [], ""

        recommendation_terms = ("triage", "troubleshooting", "recommendation", "recommend", "next step")
        if any(term in query_lower for term in recommendation_terms):
            required = [field for field in _RECOMMENDATION_FIELDS if field in ontology_profile.recommendation_fields]
            return (
                "recommendation_from_problem_evidence",
                required or ["workaround", "resolution_notes"],
                "question requests remediation or troubleshooting guidance from graph evidence",
            )

        if ("same issue" in query_lower or "same problem" in query_lower or "similar issue" in query_lower) and (
            "other" in query_lower or "which" in query_lower
        ):
            return (
                "similar_entities_by_shared_problem",
                ["name", "short_description"],
                "question compares entities through shared issue/problem evidence",
            )

        if "other issues" in query_lower or re.search(r"\bissues?\b.*\bhas had\b", query_lower):
            return (
                "entity_issue_history",
                ["short_description", "opened_at"],
                "question asks for issue history for the same entity",
            )

        if "recurring problem" in query_lower or "recurring problems" in query_lower:
            required = [field for field in _RECOMMENDATION_FIELDS if field in ontology_profile.recommendation_fields]
            return (
                "recurring_problems_for_entity",
                ["number", "short_description", *required],
                "question asks for repeated problems and their remediation evidence",
            )

        if "same category and subcategory" in query_lower:
            return (
                "related_events_by_category_subcategory",
                ["category", "subcategory", "short_description"],
                "question compares events by category and subcategory",
            )

        if ("service request" in query_lower or "service requests" in query_lower or "requests were raised" in query_lower) and (
            "around the same time" in query_lower or "trigger" in query_lower or "related" in query_lower
        ):
            return (
                "triggered_requests_for_entity_or_event",
                ["number", "state", "short_description"],
                "question asks for request linkage or triggered request evidence",
            )

        return "", [], ""

    def build_planning_hints(
        self,
        family: str,
        ontology_profile: GraphRAGv2OntologyProfile,
        required_evidence_fields: List[str],
    ) -> List[str]:
        if not family:
            return []

        hints = [
            f"Detected ontology family: {family}.",
            "Use ontology role paths inferred from the live graph rather than inventing new traversals.",
        ]
        if ontology_profile.entity_labels:
            hints.append(f"Entity labels inferred from the graph: {', '.join(ontology_profile.entity_labels[:6])}.")
        if ontology_profile.event_labels:
            hints.append(f"Event labels inferred from the graph: {', '.join(ontology_profile.event_labels[:6])}.")
        if ontology_profile.problem_labels:
            hints.append(f"Problem labels inferred from the graph: {', '.join(ontology_profile.problem_labels[:6])}.")
        if ontology_profile.request_labels:
            hints.append(f"Request labels inferred from the graph: {', '.join(ontology_profile.request_labels[:6])}.")
        if ontology_profile.role_paths:
            for role_name, rels in sorted(ontology_profile.role_paths.items()):
                hints.append(f"Observed role path {role_name}: {' -> '.join(rels)}.")
        if required_evidence_fields:
            hints.append(
                "Required evidence fields for this family: "
                + ", ".join(required_evidence_fields)
                + "."
            )

        family_specific = {
            "similar_entities_by_shared_problem": [
                "Prefer matching entities through shared problem/cause nodes before falling back to issue text similarity.",
            ],
            "entity_issue_history": [
                "Return the same entity's other event records inside the requested time window.",
            ],
            "recurring_problems_for_entity": [
                "Return recurring problem identity plus recurrence count and the most recent remediation evidence when available.",
            ],
            "recommendation_from_problem_evidence": [
                "Return remediation fields such as workaround, fix notes, cause notes, or resolution notes, not just problem titles.",
            ],
            "related_events_by_category_subcategory": [
                "Anchor category and subcategory on the same event label and return matching peer events in the requested window.",
            ],
            "triggered_requests_for_entity_or_event": [
                "Use trigger/request linkage edges when present and include request identifiers in the result rows.",
            ],
        }
        hints.extend(family_specific.get(family, []))

        search_fields = self.intent_search_fields(family, ontology_profile)
        if search_fields:
            formatted_groups = [
                f"{label}: {', '.join(fields)}"
                for label, fields in sorted(search_fields.items())
                if fields
            ]
            hints.append(
                "Intent search fields for this family: "
                + "; ".join(formatted_groups)
                + "."
            )
            hints.append(
                "Search across all intent search fields using case-insensitive CONTAINS; do not choose only one description field."
            )
            hints.append(
                "Do not require the full user phrase to appear in one field; prefer matching multiple meaningful terms across any mapped fields."
            )
            hints.append(
                "For triage guidance, return remediation fields from linked Problem nodes even when the matched text is on an Incident or ServiceRequest."
            )
            hints.append("Operational alias hints: " + "; ".join(_OPERATIONAL_ALIAS_HINTS) + ".")
        return hints

    def intent_search_fields(
        self,
        family: str,
        ontology_profile: GraphRAGv2OntologyProfile,
    ) -> Dict[str, List[str]]:
        configured = _INTENT_SEARCH_FIELDS.get(str(family or "").strip())
        if not configured:
            return {}

        available_by_label = {
            label: set(fields)
            for label, fields in dict(ontology_profile.search_fields_by_label or {}).items()
        }
        resolved: Dict[str, List[str]] = {}
        for label, desired_fields in configured.items():
            available = available_by_label.get(label)
            if not available:
                continue
            fields = [field for field in desired_fields if field in available]
            if fields:
                resolved[label] = fields
        return resolved

    def _infer_role_paths(
        self,
        relationships: List[GraphRAGv2RelationshipType],
        entity_labels: List[str],
        event_labels: List[str],
        problem_labels: List[str],
        request_labels: List[str],
    ) -> Dict[str, List[str]]:
        entity_set = set(entity_labels)
        event_set = set(event_labels)
        problem_set = set(problem_labels)
        request_set = set(request_labels)
        paths: Dict[str, List[str]] = {}

        for rel in relationships:
            if rel.from_label in entity_set and rel.to_label in event_set:
                paths.setdefault("entity_to_event", []).append(rel.rel_type)
            if rel.from_label in event_set and rel.to_label in problem_set:
                paths.setdefault("event_to_problem", []).append(rel.rel_type)
            if rel.from_label in event_set and rel.to_label in request_set:
                paths.setdefault("event_to_request", []).append(rel.rel_type)
            if rel.from_label in request_set and rel.to_label in entity_set:
                paths.setdefault("request_to_entity", []).append(rel.rel_type)

        return {
            key: sorted(set(value))
            for key, value in paths.items()
            if value
        }

    def _looks_like_entity(self, props: set[str]) -> bool:
        primary_matches = len(props & _ENTITY_PRIMARY_HINTS)
        secondary_matches = len(props & _ENTITY_SECONDARY_HINTS)
        return primary_matches >= 2 or (primary_matches >= 1 and secondary_matches >= 1)

    def _looks_like_event(self, props: set[str]) -> bool:
        primary_matches = len(props & _EVENT_PRIMARY_HINTS)
        secondary_matches = len(props & _EVENT_SECONDARY_HINTS)
        return primary_matches >= 2 or (primary_matches >= 1 and secondary_matches >= 2)

    def _looks_like_problem(self, props: set[str]) -> bool:
        primary_matches = len(props & _PROBLEM_PRIMARY_HINTS)
        secondary_matches = len(props & _PROBLEM_SECONDARY_HINTS)
        return primary_matches >= 1 or (secondary_matches >= 2)

    def _looks_like_request(self, props: set[str]) -> bool:
        primary_matches = len(props & _REQUEST_PRIMARY_HINTS)
        secondary_matches = len(props & _REQUEST_SECONDARY_HINTS)
        return primary_matches >= 1 and secondary_matches >= 1
