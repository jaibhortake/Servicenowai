from __future__ import annotations

import re
import time
from typing import Dict, List, Tuple

from app.services.neo4j_query_runtime import neo4j_query_runtime_service

from .types import (
    GraphRAGv2NodeType,
    GraphRAGv2PropertyProfile,
    GraphRAGv2SchemaSnapshot,
    GraphRAGv2SemanticProfile,
    GraphRAGv2VirtualEdge,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")
_SEARCH_FIELD_WEIGHTS = {
    "name": 90,
    "display_name": 90,
    "title": 85,
    "subcategory": 82,
    "category": 75,
    "description": 72,
    "short_description": 72,
    "content": 65,
    "question": 60,
    "hypothesis": 60,
    "observed_value": 55,
    "status": 45,
    "state": 45,
}
_ENUM_FIELD_HINTS = {
    "status",
    "state",
    "severity",
    "category",
    "subcategory",
    "type",
    "resolution_type",
    "role",
    "event_type",
}
_TARGET_ID_FIELDS = {"sys_id", "id", "key", "number", "name"}
_FOREIGN_KEY_HINTS = ("_id", "_sys_id", "session", "cause_id", "component_id", "resolution_id", "root_cause_id")


class GraphRAGv2GraphProfileService:
    def __init__(self, ttl_seconds: int = 300, sample_limit: int = 40) -> None:
        self.ttl_seconds = max(30, int(ttl_seconds or 300))
        self.sample_limit = max(10, min(int(sample_limit or 40), 100))
        self._cached_profile: GraphRAGv2SemanticProfile | None = None
        self._cached_at_epoch: float = 0.0

    def get_profile(self, snapshot: GraphRAGv2SchemaSnapshot) -> GraphRAGv2SemanticProfile:
        now_epoch = time.time()
        if (
            self._cached_profile is not None
            and self._cached_profile.snapshot_id == snapshot.snapshot_id
            and (now_epoch - self._cached_at_epoch) < self.ttl_seconds
        ):
            return self._cached_profile

        profile = self._build_profile(snapshot)
        self._cached_profile = profile
        self._cached_at_epoch = now_epoch
        return profile

    def _build_profile(self, snapshot: GraphRAGv2SchemaSnapshot) -> GraphRAGv2SemanticProfile:
        property_profiles: List[GraphRAGv2PropertyProfile] = []
        enum_values: Dict[str, List[str]] = {}
        searchable_fields: Dict[str, List[str]] = {}

        for node in snapshot.node_types:
            ranked_fields: List[Tuple[int, str]] = []
            samples_by_property = self._sample_label_property_values(node.label)
            for property_name in node.properties:
                samples = samples_by_property.get(property_name, [])
                if not samples and not samples_by_property:
                    samples = self._sample_property_values(node.label, property_name)
                property_type = self._infer_property_type(property_name, samples)
                searchable_score = self._searchable_score(property_name, property_type)
                property_profiles.append(
                    GraphRAGv2PropertyProfile(
                        label=node.label,
                        property_name=property_name,
                        property_type=property_type,
                        sample_values=samples,
                        searchable_score=searchable_score,
                    )
                )
                if property_type == "enum":
                    enum_values[f"{node.label}.{property_name}"] = samples[:30]
                if searchable_score > 0:
                    ranked_fields.append((searchable_score, property_name))
            searchable_fields[node.label] = [
                field_name
                for _, field_name in sorted(ranked_fields, key=lambda item: (-item[0], item[1]))
            ][:8]

        virtual_edges = self._discover_virtual_edges(snapshot.node_types, property_profiles)
        return GraphRAGv2SemanticProfile(
            snapshot_id=snapshot.snapshot_id,
            property_profiles=property_profiles,
            searchable_fields_by_label=searchable_fields,
            enum_values_by_label_property=enum_values,
            virtual_edges=virtual_edges,
        )

    def _sample_label_property_values(self, label: str) -> Dict[str, List[str]]:
        if not self._safe_identifier(label):
            return {}
        sample_slice_limit = self.sample_limit
        query = (
            f"MATCH (n:{label}) "
            "WITH n LIMIT $limit "
            "UNWIND keys(n) AS property_name "
            f"WITH property_name, collect(DISTINCT toString(n[property_name]))[0..{sample_slice_limit}] AS values "
            "RETURN property_name, values "
            "ORDER BY property_name"
        )
        result = neo4j_query_runtime_service.execute_readonly_query(
            query,
            {"limit": self.sample_limit},
            limit_cap=max(self.sample_limit, 100),
            timeout_seconds=4.0,
        )
        if not result.get("ok"):
            return {}
        samples_by_property: Dict[str, List[str]] = {}
        for row in result.get("rows", []) or []:
            property_name = str(row.get("property_name") or "").strip()
            if not self._safe_identifier(property_name):
                continue
            values = []
            seen = set()
            for raw_value in row.get("values", []) or []:
                value = str(raw_value or "").strip()
                lowered = value.lower()
                if not value or lowered in seen:
                    continue
                seen.add(lowered)
                values.append(value)
            samples_by_property[property_name] = values
        return samples_by_property

    def _sample_property_values(self, label: str, property_name: str) -> List[str]:
        if not self._safe_identifier(label) or not self._safe_identifier(property_name):
            return []
        query = (
            f"MATCH (n:{label}) "
            f"WHERE n.{property_name} IS NOT NULL "
            f"RETURN DISTINCT toString(n.{property_name}) AS value "
            "LIMIT $limit"
        )
        result = neo4j_query_runtime_service.execute_readonly_query(
            query,
            {"limit": self.sample_limit},
            limit_cap=self.sample_limit,
            timeout_seconds=4.0,
        )
        if not result.get("ok"):
            return []
        values = []
        seen = set()
        for row in result.get("rows", []) or []:
            value = str(row.get("value") or "").strip()
            lowered = value.lower()
            if not value or lowered in seen:
                continue
            seen.add(lowered)
            values.append(value)
        return values

    def _discover_virtual_edges(
        self,
        node_types: List[GraphRAGv2NodeType],
        property_profiles: List[GraphRAGv2PropertyProfile],
    ) -> List[GraphRAGv2VirtualEdge]:
        by_label_property = {
            (profile.label, profile.property_name): profile
            for profile in property_profiles
        }
        node_by_label = {node.label: node for node in node_types}
        edges: List[GraphRAGv2VirtualEdge] = []
        seen = set()

        for source in property_profiles:
            if not self._foreign_key_like(source.property_name) or not source.sample_values:
                continue
            source_values = {value.strip().lower() for value in source.sample_values if value.strip()}
            for target_label, target_node in node_by_label.items():
                if target_label == source.label:
                    continue
                for target_property in target_node.properties:
                    if not self._target_id_like(target_property):
                        continue
                    target = by_label_property.get((target_label, target_property))
                    if target is None or not target.sample_values:
                        continue
                    target_values = {value.strip().lower() for value in target.sample_values if value.strip()}
                    overlap = source_values & target_values
                    if not overlap:
                        continue
                    confidence = min(1.0, len(overlap) / max(1, min(len(source_values), len(target_values))))
                    edge_key = (source.label, source.property_name, target_label, target_property)
                    if edge_key in seen:
                        continue
                    seen.add(edge_key)
                    edges.append(
                        GraphRAGv2VirtualEdge(
                            from_label=source.label,
                            from_property=source.property_name,
                            to_label=target_label,
                            to_property=target_property,
                            edge_type=f"VIRTUAL_{source.property_name.upper()}",
                            confidence=round(confidence, 3),
                            sample_overlap=len(overlap),
                        )
                    )
        return sorted(
            edges,
            key=lambda edge: (-edge.confidence, -edge.sample_overlap, edge.from_label, edge.from_property),
        )[:80]

    @staticmethod
    def _infer_property_type(property_name: str, sample_values: List[str]) -> str:
        normalized_name = str(property_name or "").strip().lower()
        samples = [str(value or "").strip() for value in sample_values if str(value or "").strip()]
        lowered_samples = [value.lower() for value in samples]
        if normalized_name == "sys_id" or normalized_name.endswith("_id") or normalized_name in {"id", "key"}:
            return "id_like"
        if any(token in normalized_name for token in ("time", "date", "created", "updated", "opened", "started", "completed", "timestamp")):
            return "datetime_like"
        if samples and all(value in {"true", "false", "0", "1", "yes", "no"} for value in lowered_samples):
            return "boolean_like"
        if samples and all(_NUMERIC_RE.fullmatch(value) for value in samples):
            return "numeric_like_string"
        if samples and all(_DATETIME_RE.match(value) for value in samples[:10]):
            return "datetime_like"
        if normalized_name in _ENUM_FIELD_HINTS or (samples and len(samples) <= 20 and all(len(value) <= 64 for value in samples)):
            return "enum"
        if samples:
            return "text"
        return "unknown"

    @staticmethod
    def _searchable_score(property_name: str, property_type: str) -> int:
        normalized = str(property_name or "").strip().lower()
        if normalized in _SEARCH_FIELD_WEIGHTS:
            return _SEARCH_FIELD_WEIGHTS[normalized]
        for token, score in _SEARCH_FIELD_WEIGHTS.items():
            if token in normalized:
                return max(10, score - 15)
        if property_type in {"text", "enum"}:
            return 20
        return 0

    @staticmethod
    def _foreign_key_like(property_name: str) -> bool:
        normalized = str(property_name or "").strip().lower()
        if normalized in {"sys_id", "id", "key", "number", "name"}:
            return False
        return any(normalized == hint or normalized.endswith(hint) for hint in _FOREIGN_KEY_HINTS)

    @staticmethod
    def _target_id_like(property_name: str) -> bool:
        normalized = str(property_name or "").strip().lower()
        return normalized in _TARGET_ID_FIELDS or normalized.endswith("_id")

    @staticmethod
    def _safe_identifier(value: str) -> bool:
        return bool(_IDENTIFIER_RE.fullmatch(str(value or "").strip()))
