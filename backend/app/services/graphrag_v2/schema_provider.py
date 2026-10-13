from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import List

from app.config import settings
from app.services.neo4j_query_runtime import neo4j_query_runtime_service

from .types import (
    GraphRAGv2NodeType,
    GraphRAGv2RelationshipType,
    GraphRAGv2SchemaSnapshot,
)

logger = logging.getLogger(__name__)


class GraphRAGv2SchemaProvider:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = max(30, int(ttl_seconds or 300))
        self._cached_snapshot: GraphRAGv2SchemaSnapshot | None = None
        self._cached_at_epoch: float = 0.0

    def get_snapshot(self) -> GraphRAGv2SchemaSnapshot:
        now_epoch = datetime.now(timezone.utc).timestamp()
        if self._cached_snapshot and (now_epoch - self._cached_at_epoch) < self.ttl_seconds:
            return self._cached_snapshot

        snapshot = self._build_snapshot()
        self._cached_snapshot = snapshot
        self._cached_at_epoch = now_epoch
        return snapshot

    def _build_snapshot(self) -> GraphRAGv2SchemaSnapshot:
        if not neo4j_query_runtime_service.is_available():
            now_iso = datetime.now(timezone.utc).isoformat()
            fallback_text = self._fallback_schema_text()
            graph_profile = "unknown_generic"
            service_now_hints_enabled = self._service_now_hints_enabled(graph_profile)
            return GraphRAGv2SchemaSnapshot(
                snapshot_id=self._snapshot_id(fallback_text),
                generated_at=now_iso,
                summary_text=fallback_text,
                graph_profile=graph_profile,
                node_types=[],
                relationships=[],
                allowed_labels=[],
                allowed_relationships=[],
                time_field_candidates=[],
                service_now_hints_enabled=service_now_hints_enabled,
                module_hints=self._module_hints([], [], graph_profile, service_now_hints_enabled, []),
            )

        node_rows = self._run_schema_query(
            """
MATCH (n)
UNWIND labels(n) AS label
WITH label, count(*) AS cnt, collect(DISTINCT keys(n))[0..20] AS key_sets
WITH label, cnt, reduce(all_keys = [], key_list IN key_sets |
    all_keys + [key IN key_list WHERE NOT key IN all_keys]
) AS props
RETURN label AS label, cnt AS cnt, props[0..12] AS props
ORDER BY cnt DESC, label ASC
""".strip(),
            limit_cap=200,
        )
        rel_rows = self._run_schema_query(
            """
MATCH (a)-[r]->(b)
UNWIND labels(a) AS from_label
UNWIND labels(b) AS to_label
RETURN DISTINCT from_label AS from_label, type(r) AS rel_type, to_label AS to_label
ORDER BY from_label ASC, rel_type ASC, to_label ASC
LIMIT 120
""".strip(),
            limit_cap=120,
        )

        node_types = [
            GraphRAGv2NodeType(
                label=str(row.get("label") or ""),
                count=int(row.get("cnt") or 0),
                properties=[str(prop) for prop in (row.get("props") or []) if str(prop or "").strip()],
            )
            for row in node_rows
            if str(row.get("label") or "").strip()
        ]
        relationships = [
            GraphRAGv2RelationshipType(
                from_label=str(row.get("from_label") or ""),
                rel_type=str(row.get("rel_type") or ""),
                to_label=str(row.get("to_label") or ""),
            )
            for row in rel_rows
            if str(row.get("from_label") or "").strip()
            and str(row.get("rel_type") or "").strip()
            and str(row.get("to_label") or "").strip()
        ]
        allowed_labels = sorted({node.label for node in node_types if node.label})
        allowed_relationships = sorted({rel.rel_type for rel in relationships if rel.rel_type})
        time_field_candidates = self._time_field_candidates(node_types)
        graph_profile = self._graph_profile(node_types, relationships)
        service_now_hints_enabled = self._service_now_hints_enabled(graph_profile)
        module_hints = self._module_hints(
            node_types,
            relationships,
            graph_profile,
            service_now_hints_enabled,
            time_field_candidates,
        )
        summary_text = self._render_summary(
            node_types,
            relationships,
            graph_profile,
            module_hints,
            time_field_candidates,
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        return GraphRAGv2SchemaSnapshot(
            snapshot_id=self._snapshot_id(summary_text),
            generated_at=now_iso,
            summary_text=summary_text,
            graph_profile=graph_profile,
            node_types=node_types,
            relationships=relationships,
            allowed_labels=allowed_labels,
            allowed_relationships=allowed_relationships,
            time_field_candidates=time_field_candidates,
            service_now_hints_enabled=service_now_hints_enabled,
            module_hints=module_hints,
        )

    def _run_schema_query(self, cypher_query: str, limit_cap: int) -> List[dict]:
        execution = neo4j_query_runtime_service.execute_readonly_query(
            cypher_query,
            {"limit": limit_cap},
            limit_cap,
            timeout_seconds=6.0,
        )
        if execution.get("error"):
            logger.warning("[GRAPHRAG_V2] Schema query failed: %s", execution["error"])
            return []
        return list(execution.get("rows", []) or [])

    def _render_summary(
        self,
        node_types: List[GraphRAGv2NodeType],
        relationships: List[GraphRAGv2RelationshipType],
        graph_profile: str,
        module_hints: List[str],
        time_field_candidates: List[str],
    ) -> str:
        lines = ["LIVE GRAPH SNAPSHOT"]
        lines.append(f"Graph profile: {graph_profile}")
        lines.append("Node types:")
        for node in node_types:
            props = ", ".join(node.properties[:12])
            lines.append(f"- {node.label} ({node.count}): {props}")
        lines.append("Relationships:")
        for rel in relationships[:80]:
            lines.append(f"- ({rel.from_label})-[:{rel.rel_type}]->({rel.to_label})")
        if time_field_candidates:
            lines.append("Time-like properties:")
            lines.extend(f"- {field_name}" for field_name in time_field_candidates)
        lines.append("Module hints:")
        lines.extend(f"- {hint}" for hint in module_hints)
        return "\n".join(lines)

    def _module_hints(
        self,
        node_types: List[GraphRAGv2NodeType],
        relationships: List[GraphRAGv2RelationshipType],
        graph_profile: str,
        service_now_hints_enabled: bool,
        time_field_candidates: List[str],
    ) -> List[str]:
        relationship_set = {
            (rel.from_label, rel.rel_type, rel.to_label)
            for rel in relationships
        }
        labels = {node.label for node in node_types}
        hints: List[str] = []
        hints.append("Use only labels, relationships, and property names explicitly present in this snapshot.")
        hints.append("Prefer traversals that follow observed relationships instead of inventing hidden joins.")
        if time_field_candidates:
            hints.append(f"Candidate time fields: {', '.join(time_field_candidates[:6])}.")

        if service_now_hints_enabled and "User" in labels and "Ticket" in labels:
            hints.append("Users request tickets through (:User)-[:REQUESTED]->(:Ticket).")
            hints.append("Requester identity may also appear on Ticket properties such as requested_for_name, requested_by_name, requested_for_sys_id, and requested_by_sys_id.")
        if service_now_hints_enabled and ("Ticket", "HAS_ITEM", "RequestItem") in relationship_set:
            hints.append("Service request evidence often flows through Ticket -> HAS_ITEM -> RequestItem.")
        if service_now_hints_enabled and ("RequestItem", "FULFILLS_CATALOG_ITEM", "CatalogItem") in relationship_set:
            hints.append("Requested products connect through RequestItem -> FULFILLS_CATALOG_ITEM -> CatalogItem.")
            hints.append("Catalog item matching can require case-insensitive text matching across CatalogItem.name, CatalogItem.short_description, RequestItem.cat_item_name, and RequestItem descriptions.")
        if service_now_hints_enabled and ("ServiceRequest" in labels or "ChangeRequest" in labels):
            hints.append("Ticket subtypes distinguish service requests and change requests.")
        if graph_profile != "servicenow_like":
            hints.append("This graph is not being treated as ServiceNow-specific; rely on the live graph structure over domain assumptions.")
        return hints

    def _graph_profile(
        self,
        node_types: List[GraphRAGv2NodeType],
        relationships: List[GraphRAGv2RelationshipType],
    ) -> str:
        labels = {node.label for node in node_types}
        relationship_set = {
            (rel.from_label, rel.rel_type, rel.to_label)
            for rel in relationships
        }
        if {
            "User",
            "Ticket",
            "RequestItem",
            "CatalogItem",
        }.issubset(labels) or (
            ("User", "REQUESTED", "Ticket") in relationship_set
            and ("Ticket", "HAS_ITEM", "RequestItem") in relationship_set
            and ("RequestItem", "FULFILLS_CATALOG_ITEM", "CatalogItem") in relationship_set
        ):
            return "servicenow_like"
        if labels or relationship_set:
            return "generic_business"
        return "unknown_generic"

    def _service_now_hints_enabled(self, graph_profile: str) -> bool:
        mode = str(getattr(settings, "graphrag_domain_hint_mode", "auto") or "auto").strip().lower()
        if mode == "generic_only":
            return False
        if mode == "servicenow_only":
            return True
        return graph_profile == "servicenow_like"

    def _time_field_candidates(self, node_types: List[GraphRAGv2NodeType]) -> List[str]:
        candidates = {
            property_name
            for node in node_types
            for property_name in node.properties
            if any(token in property_name.lower() for token in ("opened_at", "created_at", "updated_at", "modified_at", "timestamp", "date", "time"))
        }
        return sorted(candidates)

    def _snapshot_id(self, summary_text: str) -> str:
        return hashlib.sha256(summary_text.encode("utf-8")).hexdigest()[:16]

    def _fallback_schema_text(self) -> str:
        return "\n".join(
            [
                "LIVE GRAPH SNAPSHOT",
                "Node types and relationships unavailable from live Neo4j.",
                "Fallback graph schema:",
                neo4j_query_runtime_service.schema_overview(),
            ]
        )
