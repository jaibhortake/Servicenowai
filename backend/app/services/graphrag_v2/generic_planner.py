from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .types import (
    GraphRAGv2CypherCandidate,
    GraphRAGv2NodeType,
    GraphRAGv2PropertyProfile,
    GraphRAGv2RelationshipType,
    GraphRAGv2SchemaSnapshot,
    GraphRAGv2SemanticProfile,
    GraphRAGv2TimeWindow,
    GraphRAGv2VirtualEdge,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_WORD_RE = re.compile(r"[a-z0-9]+")
_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")
_GENERIC_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "need", "want",
    "what", "which", "who", "where", "when", "why", "how", "are", "were", "was",
    "all", "any", "show", "give", "tell", "about", "between", "through", "during",
    "based", "linked", "common", "potential", "summary", "summarize", "include",
    "includes", "including", "latest", "top", "best", "next", "path", "paths",
    "count", "counts", "number", "total", "rank", "most", "least", "group", "by",
    "list", "find", "search", "match", "matches", "details", "detail", "rows",
    "items", "item", "entities", "entity", "nodes", "node", "graph",
}
_STATUS_WORDS = {
    "open": ["open", "opened", "active", "pending", "in progress", "in-progress"],
    "in progress": ["in progress", "in-progress", "progress", "active", "pending", "open"],
    "closed": ["closed", "complete", "completed", "resolved", "done"],
    "completed": ["completed", "complete", "closed", "resolved", "done"],
}
_DISPLAY_FIELD_HINTS = (
    "sys_id",
    "id",
    "number",
    "key",
    "name",
    "title",
    "display_name",
    "model",
    "category",
    "subcategory",
    "status",
    "state",
    "severity",
    "description",
    "short_description",
    "started_at",
    "opened_at",
    "created_at",
    "updated_at",
)
_TIME_FIELD_HINTS = ("time", "date", "opened", "started", "created", "updated", "completed", "timestamp")
_STATE_FIELD_HINTS = ("status", "state", "stage", "phase")


@dataclass(frozen=True)
class _ResolvedValue:
    label: str
    property_name: str
    value: str
    score: float
    match_terms: Tuple[str, ...]


@dataclass(frozen=True)
class _LabelMatch:
    label: str
    score: float
    reason: str = ""


class GraphRAGv2GenericPlanner:
    """Schema-first Cypher planner that avoids domain-specific labels and relationships."""

    def build_candidates(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        desired_shape: str,
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> List[GraphRAGv2CypherCandidate]:
        if not snapshot.node_types:
            return []

        terms = self.question_terms(question)
        resolved_values = self.resolve_values(question, profile, limit=8)
        label_matches = self.resolve_labels(question, snapshot, profile, resolved_values, limit=8)
        state_terms = self.resolve_state_terms(question, profile)
        candidates: List[GraphRAGv2CypherCandidate] = []

        for anchor in resolved_values[:2]:
            candidate = self._anchor_neighborhood_candidate(
                anchor=anchor,
                profile=profile,
                desired_shape=desired_shape,
            )
            if candidate:
                candidates.append(candidate)

        if self._looks_like_rank_or_count(question):
            candidates.extend(
                self._rank_by_related_candidates(
                    question=question,
                    snapshot=snapshot,
                    profile=profile,
                    label_matches=label_matches,
                    state_terms=state_terms,
                    time_window=time_window,
                )
            )
            group_candidate = self._property_group_count_candidate(
                question=question,
                snapshot=snapshot,
                profile=profile,
                label_matches=label_matches,
                resolved_values=resolved_values,
                state_terms=state_terms,
                time_window=time_window,
            )
            if group_candidate:
                candidates.append(group_candidate)

        numeric_candidate = self._numeric_threshold_candidate(
            question=question,
            snapshot=snapshot,
            profile=profile,
            label_matches=label_matches,
        )
        if numeric_candidate:
            candidates.append(numeric_candidate)

        if state_terms or self._looks_like_list_or_summary(question) or time_window:
            list_labels = label_matches[:3] or self._fallback_event_labels(snapshot, profile)[:2]
            for label_match in list_labels:
                candidate = self._filtered_list_candidate(
                    label=label_match.label,
                    profile=profile,
                    terms=terms if resolved_values else [],
                    state_terms=state_terms,
                    time_window=time_window,
                )
                if candidate:
                    candidates.append(candidate)

        return self._dedupe_candidates(candidates)[:8]

    def resolve_values(
        self,
        question: str,
        profile: GraphRAGv2SemanticProfile,
        *,
        limit: int = 8,
    ) -> List[_ResolvedValue]:
        query_lower = str(question or "").lower()
        query_terms = set(self.question_terms(question))
        resolved: List[_ResolvedValue] = []
        for property_profile in profile.property_profiles:
            if not self._safe_identifier(property_profile.label) or not self._safe_identifier(property_profile.property_name):
                continue
            if property_profile.property_type not in {"text", "enum", "id_like"} and property_profile.searchable_score <= 0:
                continue
            for sample_value in property_profile.sample_values[:30]:
                value = str(sample_value or "").strip()
                if not value:
                    continue
                value_lower = value.lower()
                value_terms = set(self._tokens(value_lower))
                overlap = query_terms & value_terms
                exact_phrase = len(value_lower) >= 3 and value_lower in query_lower
                status_match = self._status_value_matches_query(value_lower, query_lower)
                if not overlap and not exact_phrase and not status_match:
                    continue
                label_terms = set(self._name_terms(property_profile.label))
                property_terms = set(self._name_terms(property_profile.property_name))
                score = len(overlap) * 6.0
                score += min(3.0, float(property_profile.searchable_score or 0) / 35.0)
                if exact_phrase:
                    score += 8.0
                if status_match:
                    score += 6.0
                score += len(query_terms & label_terms) * 1.5
                score += len(query_terms & property_terms) * 1.2
                if property_profile.property_type == "enum":
                    score += 1.0
                match_terms = tuple(sorted(overlap)) or tuple(self._tokens(value_lower)[:4])
                if score >= 2.0 and match_terms:
                    resolved.append(
                        _ResolvedValue(
                            label=property_profile.label,
                            property_name=property_profile.property_name,
                            value=value,
                            score=round(score, 3),
                            match_terms=match_terms,
                        )
                    )
        return sorted(
            resolved,
            key=lambda item: (-item.score, item.label, item.property_name, item.value),
        )[:limit]

    def resolve_labels(
        self,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        resolved_values: Sequence[_ResolvedValue],
        *,
        limit: int = 8,
    ) -> List[_LabelMatch]:
        query_lower = str(question or "").lower()
        query_terms = set(self.question_terms(question))
        anchor_labels = {value.label for value in resolved_values}
        matches: List[_LabelMatch] = []
        for node in snapshot.node_types:
            label = node.label
            label_terms = set(self._name_terms(label))
            score = len(query_terms & label_terms) * 5.0
            normalized_label = " ".join(label_terms)
            if normalized_label and normalized_label in query_lower:
                score += 8.0
            if label in anchor_labels:
                score += 6.0
            searchable_fields = set(profile.searchable_fields_by_label.get(label, []))
            for field_name in searchable_fields:
                score += len(query_terms & set(self._name_terms(field_name))) * 0.7
            if score > 0:
                matches.append(_LabelMatch(label=label, score=round(score, 3), reason="schema/query match"))
        return sorted(matches, key=lambda item: (-item.score, item.label))[:limit]

    def resolve_state_terms(self, question: str, profile: GraphRAGv2SemanticProfile) -> List[str]:
        query_lower = str(question or "").lower().replace("-", " ")
        terms: List[str] = []
        for canonical, variants in _STATUS_WORDS.items():
            if any(variant.replace("-", " ") in query_lower for variant in variants):
                terms.extend(variants)
                terms.append(canonical)
        for key, values in profile.enum_values_by_label_property.items():
            property_name = key.rsplit(".", 1)[-1].lower()
            if not any(hint in property_name for hint in _STATE_FIELD_HINTS):
                continue
            for value in values:
                value_lower = str(value or "").strip().lower()
                if not value_lower:
                    continue
                if self._status_value_matches_query(value_lower, query_lower):
                    terms.append(value_lower)
        return self._dedupe(terms)

    def question_terms(self, question: str) -> List[str]:
        return self._dedupe(
            [
                token
                for token in self._tokens(str(question or "").lower())
                if len(token) >= 3 and token not in _GENERIC_STOP_WORDS
            ]
        )

    def _anchor_neighborhood_candidate(
        self,
        *,
        anchor: _ResolvedValue,
        profile: GraphRAGv2SemanticProfile,
        desired_shape: str,
    ) -> Optional[GraphRAGv2CypherCandidate]:
        search_fields = self._search_fields(anchor.label, profile, preferred=anchor.property_name)
        if not search_fields:
            return None
        search_expr = self._contains_expr("anchor", search_fields)
        virtual_steps = self._virtual_neighbor_steps(anchor.label, profile)
        with_virtual_clauses: List[str] = []
        virtual_lists: List[str] = []
        carried = "anchor, outgoing, incoming"
        for index, edge in enumerate(virtual_steps):
            other_label = edge.to_label if edge.from_label == anchor.label else edge.from_label
            var_name = f"virtual_{index}"
            if edge.from_label == anchor.label:
                condition = f"anchor.{edge.from_property} = {var_name}.{edge.to_property}"
            else:
                condition = f"{var_name}.{edge.from_property} = anchor.{edge.to_property}"
            list_name = f"virtual_neighbors_{index}"
            with_virtual_clauses.append(
                "\n".join(
                    [
                        f"OPTIONAL MATCH ({var_name}:{other_label}) WHERE {condition}",
                        (
                            f"WITH {carried}, "
                            f"[item IN collect(DISTINCT CASE WHEN {var_name} IS NULL THEN null ELSE "
                            f"{{direction: 'virtual', relationship: '{edge.edge_type}', labels: labels({var_name}), node: properties({var_name})}} "
                            f"END) WHERE item IS NOT NULL][0..10] AS {list_name}"
                        ),
                    ]
                )
            )
            carried += f", {list_name}"
            virtual_lists.append(list_name)
        related_expr = "outgoing + incoming"
        if virtual_lists:
            related_expr += " + " + " + ".join(virtual_lists)

        cypher = f"""
MATCH (anchor:{anchor.label})
WHERE ANY(term IN $subject_terms WHERE {search_expr})
WITH anchor LIMIT $limit
OPTIONAL MATCH (anchor)-[out_rel]->(out_node)
WITH anchor,
     [item IN collect(DISTINCT CASE WHEN out_rel IS NULL THEN null ELSE {{direction: 'out', relationship: type(out_rel), labels: labels(out_node), node: properties(out_node)}} END) WHERE item IS NOT NULL][0..20] AS outgoing
OPTIONAL MATCH (anchor)<-[in_rel]-(in_node)
WITH anchor, outgoing,
     [item IN collect(DISTINCT CASE WHEN in_rel IS NULL THEN null ELSE {{direction: 'in', relationship: type(in_rel), labels: labels(in_node), node: properties(in_node)}} END) WHERE item IS NOT NULL][0..20] AS incoming
{chr(10).join(with_virtual_clauses)}
RETURN labels(anchor) AS anchor_labels,
       properties(anchor) AS anchor,
       {related_expr} AS related
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id=f"generic_anchor_neighborhood__{anchor.label}__{anchor.property_name}",
            query_shape=desired_shape if desired_shape in {"lookup", "list"} else "lookup",
            purpose=(
                f"Find graph evidence around {anchor.label}.{anchor.property_name} "
                "using searchable fields and verified virtual joins"
            ),
            cypher_query=cypher,
            parameters={"subject_terms": list(anchor.match_terms), "limit": 8},
        )

    def _filtered_list_candidate(
        self,
        *,
        label: str,
        profile: GraphRAGv2SemanticProfile,
        terms: List[str],
        state_terms: List[str],
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> Optional[GraphRAGv2CypherCandidate]:
        if not self._safe_identifier(label):
            return None
        search_fields = self._search_fields(label, profile)
        state_fields = self._state_fields(label, profile)
        time_field = self._time_field(label, profile)
        where_parts = ["true"]
        params: Dict[str, object] = {"subject_terms": terms[:8], "state_terms": state_terms[:8], "limit": 20}
        if terms and search_fields:
            where_parts.append(f"(size($subject_terms) = 0 OR ANY(term IN $subject_terms WHERE {self._contains_expr('n', search_fields)}))")
        if state_terms:
            if not state_fields:
                return None
            where_parts.append(f"(size($state_terms) = 0 OR ANY(state IN $state_terms WHERE {self._contains_expr('n', state_fields, variable_name='state')}))")
        if time_window and time_field:
            where_parts.append(f"($opened_start = '' OR toString(coalesce(n.{time_field}, '')) >= $opened_start)")
            where_parts.append(f"($opened_end = '' OR toString(coalesce(n.{time_field}, '')) < $opened_end)")
            params["opened_start"] = time_window.start
            params["opened_end"] = time_window.end
        order_clause = f"ORDER BY toString(coalesce(n.{time_field}, '')) DESC" if time_field else ""
        projection = self._node_projection("n", label, profile)
        cypher = f"""
MATCH (n:{label})
WHERE {' AND '.join(where_parts)}
RETURN labels(n) AS labels,
       {projection} AS node
{order_clause}
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id=f"generic_filtered_list__{label}",
            query_shape="list",
            purpose=f"List {label} rows using schema-derived text/status/time filters",
            cypher_query=cypher,
            parameters=params,
        )

    def _rank_by_related_candidates(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        label_matches: Sequence[_LabelMatch],
        state_terms: List[str],
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> List[GraphRAGv2CypherCandidate]:
        candidates: List[GraphRAGv2CypherCandidate] = []
        for label_match in label_matches[:4]:
            related = self._best_related_label(label_match.label, question, snapshot, profile)
            if not related:
                continue
            candidate = self._rank_by_related_candidate(
                group_label=label_match.label,
                item_label=related,
                snapshot=snapshot,
                profile=profile,
                state_terms=state_terms,
                time_window=time_window,
            )
            if candidate:
                candidates.append(candidate)
        return candidates[:3]

    def _rank_by_related_candidate(
        self,
        *,
        group_label: str,
        item_label: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        state_terms: List[str],
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> Optional[GraphRAGv2CypherCandidate]:
        relation_clause = ""
        if self._has_any_real_relationship(snapshot, group_label, item_label):
            relation_clause = f"MATCH (group:{group_label})-[rel]-(item:{item_label})"
        else:
            edge = self._best_virtual_edge(profile, group_label, item_label)
            if edge is None:
                return None
            if edge.from_label == group_label:
                condition = f"group.{edge.from_property} = item.{edge.to_property}"
            else:
                condition = f"item.{edge.from_property} = group.{edge.to_property}"
            relation_clause = f"MATCH (group:{group_label})\nMATCH (item:{item_label})\nWHERE {condition}"

        time_field = self._time_field(item_label, profile)
        state_fields = self._state_fields(item_label, profile)
        where_parts = []
        params: Dict[str, object] = {"state_terms": state_terms[:8], "limit": 20}
        if time_window and time_field:
            where_parts.append(f"toString(coalesce(item.{time_field}, '')) >= $opened_start")
            where_parts.append(f"toString(coalesce(item.{time_field}, '')) < $opened_end")
            params["opened_start"] = time_window.start
            params["opened_end"] = time_window.end
        where_clause = f"\nWHERE {' AND '.join(where_parts)}" if where_parts and "WHERE" not in relation_clause else ""
        state_count_expr = "0 AS state_match_count"
        if state_terms and state_fields:
            state_count_expr = (
                "size([candidate IN related_items WHERE ANY(state IN $state_terms WHERE "
                + self._contains_expr("candidate", state_fields, variable_name="state")
                + ")]) AS state_match_count"
            )
        group_projection = self._node_projection("group", group_label, profile)
        item_projection = self._node_projection("candidate", item_label, profile)
        cypher = f"""
{relation_clause}{where_clause}
WITH group, collect(DISTINCT item) AS related_items
RETURN {group_projection} AS group_node,
       size(related_items) AS related_count,
       {state_count_expr},
       [candidate IN related_items[0..5] | {item_projection}] AS sample_related
ORDER BY related_count DESC
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id=f"generic_rank_by_related__{group_label}__{item_label}",
            query_shape="rank",
            purpose=f"Rank {group_label} nodes by connected {item_label} evidence",
            cypher_query=cypher,
            parameters=params,
        )

    def _property_group_count_candidate(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        label_matches: Sequence[_LabelMatch],
        resolved_values: Sequence[_ResolvedValue],
        state_terms: List[str],
        time_window: Optional[GraphRAGv2TimeWindow],
    ) -> Optional[GraphRAGv2CypherCandidate]:
        label = (label_matches[0].label if label_matches else (resolved_values[0].label if resolved_values else ""))
        if not label:
            return None
        group_property = self._best_group_property(question, label, profile)
        if not group_property:
            return None
        state_fields = self._state_fields(label, profile)
        time_field = self._time_field(label, profile)
        where_parts = [f"n.{group_property} IS NOT NULL"]
        params: Dict[str, object] = {"state_terms": state_terms[:8], "limit": 20}
        if state_terms and state_fields:
            where_parts.append(f"ANY(state IN $state_terms WHERE {self._contains_expr('n', state_fields, variable_name='state')})")
        if time_window and time_field:
            where_parts.append(f"toString(coalesce(n.{time_field}, '')) >= $opened_start")
            where_parts.append(f"toString(coalesce(n.{time_field}, '')) < $opened_end")
            params["opened_start"] = time_window.start
            params["opened_end"] = time_window.end
        cypher = f"""
MATCH (n:{label})
WHERE {' AND '.join(where_parts)}
RETURN toString(n.{group_property}) AS group_value,
       count(DISTINCT n) AS item_count
ORDER BY item_count DESC, group_value ASC
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id=f"generic_group_count__{label}__{group_property}",
            query_shape="aggregate",
            purpose=f"Count {label} rows grouped by {group_property}",
            cypher_query=cypher,
            parameters=params,
        )

    def _numeric_threshold_candidate(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
        label_matches: Sequence[_LabelMatch],
    ) -> Optional[GraphRAGv2CypherCandidate]:
        threshold = self._extract_threshold(question)
        if threshold is None:
            return None
        direction = self._threshold_direction(question)
        query_terms = set(self.question_terms(question))
        best_profile: Optional[GraphRAGv2PropertyProfile] = None
        best_score = 0.0
        labels = {match.label for match in label_matches[:4]}
        for property_profile in profile.property_profiles:
            if property_profile.property_type != "numeric_like_string":
                continue
            prop_terms = set(self._name_terms(property_profile.property_name))
            label_terms = set(self._name_terms(property_profile.label))
            score = len(query_terms & prop_terms) * 4.0 + len(query_terms & label_terms) * 2.0
            if labels and property_profile.label in labels:
                score += 3.0
            if score > best_score:
                best_profile = property_profile
                best_score = score
        if best_profile is None or best_score <= 0:
            return None
        operator = "<=" if direction == "lte" else ">="
        order_direction = "ASC" if direction == "lte" else "DESC"
        projection = self._node_projection("n", best_profile.label, profile)
        cypher = f"""
MATCH (n:{best_profile.label})
WHERE toFloat(coalesce(n.{best_profile.property_name}, '0')) {operator} $threshold
RETURN labels(n) AS labels,
       {projection} AS node,
       toFloat(coalesce(n.{best_profile.property_name}, '0')) AS numeric_value
ORDER BY numeric_value {order_direction}
LIMIT $limit
""".strip()
        return GraphRAGv2CypherCandidate(
            candidate_id=f"generic_numeric_threshold__{best_profile.label}__{best_profile.property_name}",
            query_shape="list",
            purpose=f"Filter {best_profile.label} rows by numeric-like {best_profile.property_name}",
            cypher_query=cypher,
            parameters={"threshold": int(threshold), "limit": 20},
        )

    def _best_related_label(
        self,
        group_label: str,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
    ) -> str:
        query_terms = set(self.question_terms(question))
        node_counts = {node.label: int(node.count or 0) for node in snapshot.node_types}
        related: Dict[str, float] = {}
        for rel in snapshot.relationships:
            other = ""
            if rel.from_label == group_label:
                other = rel.to_label
            elif rel.to_label == group_label:
                other = rel.from_label
            if other:
                related[other] = max(related.get(other, 0.0), 6.0 + len(query_terms & set(self._name_terms(other))) * 4.0)
        for edge in profile.virtual_edges:
            other = ""
            if edge.from_label == group_label:
                other = edge.to_label
            elif edge.to_label == group_label:
                other = edge.from_label
            if other:
                related[other] = max(
                    related.get(other, 0.0),
                    4.0 + float(edge.confidence or 0.0) * 2.0 + len(query_terms & set(self._name_terms(other))) * 3.0,
                )
        for label in list(related):
            if self._time_field(label, profile):
                related[label] += 2.0
            if self._state_fields(label, profile):
                related[label] += 1.0
            related[label] += min(2.0, node_counts.get(label, 0) / 100.0)
        if not related:
            return ""
        return sorted(related.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def _fallback_event_labels(self, snapshot: GraphRAGv2SchemaSnapshot, profile: GraphRAGv2SemanticProfile) -> List[_LabelMatch]:
        labels = []
        for node in snapshot.node_types:
            score = 0.0
            if self._time_field(node.label, profile):
                score += 3.0
            if self._state_fields(node.label, profile):
                score += 2.0
            score += min(2.0, int(node.count or 0) / 100.0)
            if score > 0:
                labels.append(_LabelMatch(label=node.label, score=round(score, 3), reason="fallback event-like label"))
        return sorted(labels, key=lambda item: (-item.score, item.label))

    def _best_group_property(self, question: str, label: str, profile: GraphRAGv2SemanticProfile) -> str:
        query_terms = set(self.question_terms(question))
        best = ""
        best_score = 0.0
        for property_profile in profile.property_profiles:
            if property_profile.label != label:
                continue
            if property_profile.property_type not in {"enum", "text"}:
                continue
            prop_terms = set(self._name_terms(property_profile.property_name))
            score = len(query_terms & prop_terms) * 4.0
            if property_profile.property_name.lower() in {"category", "subcategory", "status", "state", "type", "severity"}:
                score += 2.0
            if score > best_score:
                best = property_profile.property_name
                best_score = score
        return best if best_score > 0 else ""

    def _search_fields(
        self,
        label: str,
        profile: GraphRAGv2SemanticProfile,
        *,
        preferred: str = "",
    ) -> List[str]:
        fields = []
        if preferred and self._safe_identifier(preferred):
            fields.append(preferred)
        fields.extend(profile.searchable_fields_by_label.get(label, [])[:6])
        return self._dedupe([field for field in fields if self._safe_identifier(field)])

    def _state_fields(self, label: str, profile: GraphRAGv2SemanticProfile) -> List[str]:
        fields = []
        for property_profile in profile.property_profiles:
            if property_profile.label != label:
                continue
            name = property_profile.property_name.lower()
            if any(hint in name for hint in _STATE_FIELD_HINTS):
                fields.append(property_profile.property_name)
        return self._dedupe([field for field in fields if self._safe_identifier(field)])

    def _time_field(self, label: str, profile: GraphRAGv2SemanticProfile) -> str:
        best = ""
        for property_profile in profile.property_profiles:
            if property_profile.label != label:
                continue
            name = property_profile.property_name.lower()
            if property_profile.property_type == "datetime_like" or any(hint in name for hint in _TIME_FIELD_HINTS):
                if name.endswith("_at") or name in {"started_at", "opened_at", "created_at", "updated_at"}:
                    return property_profile.property_name
                best = best or property_profile.property_name
        return best

    def _node_projection(self, variable: str, label: str, profile: GraphRAGv2SemanticProfile) -> str:
        label_properties = [
            property_profile.property_name
            for property_profile in profile.property_profiles
            if property_profile.label == label and self._safe_identifier(property_profile.property_name)
        ]
        fields: List[str] = []
        for hint in _DISPLAY_FIELD_HINTS:
            fields.extend([property_name for property_name in label_properties if property_name.lower() == hint])
        fields.extend(profile.searchable_fields_by_label.get(label, [])[:5])
        fields.extend(label_properties[:4])
        fields = self._dedupe(fields)[:10]
        if not fields:
            return f"properties({variable})"
        projection_fields = ", ".join(f".{field}" for field in fields)
        return f"{variable} {{ {projection_fields} }}"

    def _virtual_neighbor_steps(self, label: str, profile: GraphRAGv2SemanticProfile) -> List[GraphRAGv2VirtualEdge]:
        candidates = [
            edge
            for edge in profile.virtual_edges
            if edge.from_label == label or edge.to_label == label
        ]
        return sorted(
            candidates,
            key=lambda edge: (-float(edge.confidence or 0.0), -int(edge.sample_overlap or 0), edge.from_label, edge.to_label),
        )[:2]

    @staticmethod
    def _contains_expr(node_var: str, fields: Sequence[str], *, variable_name: str = "term") -> str:
        return " OR ".join(
            f"toLower(coalesce({node_var}.{field}, '')) CONTAINS {variable_name}"
            for field in fields
            if _IDENTIFIER_RE.fullmatch(field)
        ) or "false"

    @staticmethod
    def _has_any_real_relationship(snapshot: GraphRAGv2SchemaSnapshot, label_a: str, label_b: str) -> bool:
        return any(
            (rel.from_label == label_a and rel.to_label == label_b)
            or (rel.from_label == label_b and rel.to_label == label_a)
            for rel in snapshot.relationships
        )

    @staticmethod
    def _best_virtual_edge(
        profile: GraphRAGv2SemanticProfile,
        label_a: str,
        label_b: str,
    ) -> Optional[GraphRAGv2VirtualEdge]:
        candidates = [
            edge
            for edge in profile.virtual_edges
            if (edge.from_label == label_a and edge.to_label == label_b)
            or (edge.from_label == label_b and edge.to_label == label_a)
        ]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda edge: (-float(edge.confidence or 0.0), -int(edge.sample_overlap or 0), edge.from_property),
        )[0]

    @staticmethod
    def _extract_threshold(question: str) -> Optional[int]:
        matches = re.findall(r"\b(\d{1,9})\b", str(question or ""))
        if not matches:
            return None
        return int(matches[0])

    @staticmethod
    def _threshold_direction(question: str) -> str:
        query_lower = str(question or "").lower()
        if any(term in query_lower for term in ("less", "below", "under", "at most", "or less", "fewer", "<=")):
            return "lte"
        return "gte"

    @staticmethod
    def _looks_like_rank_or_count(question: str) -> bool:
        query_lower = str(question or "").lower()
        return any(term in query_lower for term in ("rank", "most", "least", "count", "counts", "frequency", "group by", "by "))

    @staticmethod
    def _looks_like_list_or_summary(question: str) -> bool:
        query_lower = str(question or "").lower()
        return any(term in query_lower for term in ("show", "list", "summarize", "summary", "which", "what", "find", "details"))

    @staticmethod
    def _status_value_matches_query(value_lower: str, query_lower: str) -> bool:
        normalized_value = value_lower.replace("-", " ")
        normalized_query = query_lower.replace("-", " ")
        if normalized_value and normalized_value in normalized_query:
            return True
        for variants in _STATUS_WORDS.values():
            if normalized_value in {variant.replace("-", " ") for variant in variants}:
                return any(variant.replace("-", " ") in normalized_query for variant in variants)
        return False

    @staticmethod
    def _name_terms(name: str) -> List[str]:
        spaced = _CAMEL_RE.sub(" ", str(name or "")).replace("_", " ").replace("-", " ")
        terms = []
        for token in _WORD_RE.findall(spaced.lower()):
            if len(token) < 2:
                continue
            terms.append(token[:-1] if token.endswith("s") and len(token) > 3 else token)
        return terms

    @staticmethod
    def _tokens(value: str) -> List[str]:
        return _WORD_RE.findall(str(value or "").lower())

    @staticmethod
    def _dedupe(values: Sequence[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for value in values:
            normalized = str(value or "").strip()
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            result.append(normalized)
            seen.add(lowered)
        return result

    @staticmethod
    def _dedupe_candidates(candidates: Sequence[GraphRAGv2CypherCandidate]) -> List[GraphRAGv2CypherCandidate]:
        result: List[GraphRAGv2CypherCandidate] = []
        seen = set()
        for candidate in candidates:
            if candidate.candidate_id in seen:
                continue
            seen.add(candidate.candidate_id)
            result.append(candidate)
        return result

    @staticmethod
    def _safe_identifier(value: str) -> bool:
        return bool(_IDENTIFIER_RE.fullmatch(str(value or "").strip()))
