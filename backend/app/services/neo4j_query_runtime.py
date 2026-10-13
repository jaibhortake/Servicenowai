import logging
import re
import time
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase, Query
from neo4j.exceptions import Neo4jError

from app.config import Neo4jConnectionConfig, settings

logger = logging.getLogger(__name__)

_BLOCKED_CYPHER_PATTERNS = [
    r"\bCREATE\b",
    r"\bMERGE\b",
    r"\bDELETE\b",
    r"\bDETACH\b",
    r"\bSET\b",
    r"\bREMOVE\b",
    r"\bDROP\b",
    r"\bALTER\b",
    r"\bRENAME\b",
    r"\bFOREACH\b",
    r"\bCALL\b",
    r"\bLOAD\s+CSV\b",
    r"\bSTART\b",
]

_SERVICENOW_ALLOWED_LABELS = {
    "CatalogItem",
    "FilterSchema",
    "CatalogVariable",
    "CatalogChoice",
    "Table",
    "Column",
    "Ticket",
    "ServiceRequest",
    "ChangeRequest",
    "RequestItem",
    "User",
    "Platform",
}

_SERVICENOW_ALLOWED_RELATIONSHIPS = {
    "USES_SCHEMA",
    "HAS_FIELD",
    "HAS_CHOICE",
    "HAS_COLUMN",
    "REQUESTED",
    "ASSIGNED_TO",
    "HAS_ITEM",
    "FULFILLS_CATALOG_ITEM",
    "HAS_TICKET",
}

_GRAPHRAG_VECTOR_INDEXES = {
    "incident_embedding_idx": "Incident",
    "problem_embedding_idx": "Problem",
    "service_request_embedding_idx": "ServiceRequest",
}

_VECTORRAG_VECTOR_INDEXES = {
    "solution_document_embedding_idx": "SolutionDocument",
}

_GRAPH_SCHEMA_TEXT = """Graph schema:

Catalog domain
- (:CatalogItem {sys_id, name, short_description, description, category, price})
- (:CatalogItem)-[:USES_SCHEMA]->(:FilterSchema {key, name, field_count})
- (:FilterSchema)-[:HAS_FIELD]->(:CatalogVariable {key, name, label, type, mandatory, order})
- (:CatalogVariable)-[:HAS_CHOICE]->(:CatalogChoice {key, label, value, sequence})

Schema domain
- (:Table {sys_id, name, label, scope, provider_class})
- (:Table)-[:HAS_COLUMN]->(:Column {sys_id, name, label, type, mandatory, reference, max_length})

Ticket domain
- (:Ticket {sys_id, number, ticket_type, short_description, description, state, state_display, opened_at, requested_for_name, requested_by_name, assigned_to_name})
- (:Ticket:ServiceRequest) and (:Ticket:ChangeRequest)
- (:User {sys_id, name})-[:REQUESTED]->(:Ticket)
- (:User {sys_id, name})-[:ASSIGNED_TO]->(:Ticket)
- (:Ticket)-[:HAS_ITEM]->(:RequestItem {sys_id, number, short_description, description, cat_item_name})
- (:RequestItem)-[:FULFILLS_CATALOG_ITEM]->(:CatalogItem)

Use only labels, relationships, and properties listed above.
Always return rows with explicit aliases.
Use only read-only Cypher clauses: MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT.
"""

_TIME_FIELD_NAME_PATTERN = re.compile(
    r"(opened|created|updated|modified|timestamp|time|date)(_at)?$",
    flags=re.IGNORECASE,
)


class Neo4jQueryRuntimeService:
    def __init__(self, connection_config: Neo4jConnectionConfig | None = None) -> None:
        self._config = connection_config or settings.graphrag_neo4j_config()
        self._database = self._config.database
        self._driver = None
        self._schema_cache: Optional[Dict[str, Any]] = None
        self._schema_cached_at_epoch: float = 0.0

    def startup(self) -> None:
        if not self._config.enabled:
            logger.info("%s query runtime is disabled", self._config.name)
            return

        if self._driver is not None:
            return

        if not self._config.is_complete:
            logger.warning("%s query runtime settings are incomplete", self._config.name)
            return

        try:
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                connection_timeout=3.0,
                max_transaction_retry_time=2.0,
            )
            self._driver.verify_connectivity()
            logger.info("Connected to %s query runtime", self._config.name)
        except Exception:
            logger.exception("Failed to initialize %s query runtime", self._config.name)
            self.shutdown()

    def shutdown(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
        self._schema_cache = None
        self._schema_cached_at_epoch = 0.0

    def is_available(self) -> bool:
        return self._driver is not None

    def is_write_protected(self) -> bool:
        return self._config.is_write_protected

    def _block_write(self, operation: str) -> bool:
        if not self._config.is_write_protected:
            return False
        logger.warning(
            "Blocked %s on write-protected %s (%s)",
            operation,
            self._config.name,
            self._config.write_protection_reason,
        )
        return True

    def schema_overview(self) -> str:
        if self._schema_strict_mode() == "dynamic":
            metadata = self.live_schema_metadata()
            labels = list(metadata.get("labels", []) or [])
            relationships = list(metadata.get("relationships", []) or [])
            properties_by_label = dict(metadata.get("properties_by_label", {}) or {})
            if labels or relationships:
                lines = ["Graph schema:"]
                lines.append("Node labels")
                for label in labels:
                    props = ", ".join(properties_by_label.get(label, [])[:12])
                    lines.append(f"- (:{label}) {props}".rstrip())
                lines.append("Relationships")
                for rel_type in relationships:
                    lines.append(f"- [:{rel_type}]")
                time_fields = list(metadata.get("time_field_candidates", []) or [])
                if time_fields:
                    lines.append("Time-like properties")
                    for field_name in time_fields:
                        lines.append(f"- {field_name}")
                lines.append("Use only read-only Cypher clauses: MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT.")
                return "\n".join(lines)
        return _GRAPH_SCHEMA_TEXT

    def live_schema_metadata(self, refresh: bool = False, ttl_seconds: int = 300) -> Dict[str, Any]:
        if self._driver is None:
            return {
                "labels": [],
                "relationships": [],
                "properties_by_label": {},
                "time_field_candidates": [],
            }

        now_epoch = time.time()
        if (
            not refresh
            and self._schema_cache is not None
            and (now_epoch - self._schema_cached_at_epoch) < max(30, int(ttl_seconds or 300))
        ):
            return self._schema_cache

        try:
            with self._driver.session(database=self._database) as session:
                label_rows = session.run(
                    """
MATCH (n)
UNWIND labels(n) AS label
WITH label, collect(DISTINCT keys(n))[0..20] AS key_sets
WITH label, reduce(all_keys = [], key_list IN key_sets |
    all_keys + [key IN key_list WHERE NOT key IN all_keys]
) AS props
RETURN label AS label, props[0..20] AS props
ORDER BY label ASC
"""
                )
                rel_rows = session.run(
                    """
MATCH ()-[r]->()
RETURN DISTINCT type(r) AS rel_type
ORDER BY rel_type ASC
"""
                )
                labels: List[str] = []
                properties_by_label: Dict[str, List[str]] = {}
                for record in label_rows:
                    label = str(record.get("label") or "").strip()
                    if not label:
                        continue
                    labels.append(label)
                    properties_by_label[label] = [
                        str(prop).strip()
                        for prop in (record.get("props") or [])
                        if str(prop or "").strip()
                    ]
                relationships = [
                    str(record.get("rel_type") or "").strip()
                    for record in rel_rows
                    if str(record.get("rel_type") or "").strip()
                ]
        except Neo4jError:
            logger.exception("Failed to load live Neo4j schema metadata")
            return {
                "labels": [],
                "relationships": [],
                "properties_by_label": {},
                "time_field_candidates": [],
            }

        time_field_candidates = sorted(
            {
                property_name
                for props in properties_by_label.values()
                for property_name in props
                if _TIME_FIELD_NAME_PATTERN.search(property_name)
            }
        )
        metadata = {
            "labels": sorted(set(labels)),
            "relationships": sorted(set(relationships)),
            "properties_by_label": properties_by_label,
            "time_field_candidates": time_field_candidates,
        }
        self._schema_cache = metadata
        self._schema_cached_at_epoch = now_epoch
        return metadata

    def validation_context(self) -> Dict[str, Any]:
        strict_mode = self._schema_strict_mode()
        if strict_mode == "servicenow_strict":
            return {
                "strict_mode": strict_mode,
                "allowed_labels": sorted(_SERVICENOW_ALLOWED_LABELS),
                "allowed_relationships": sorted(_SERVICENOW_ALLOWED_RELATIONSHIPS),
                "time_field_candidates": ["opened_at"],
            }
        metadata = self.live_schema_metadata()
        return {
            "strict_mode": strict_mode,
            "allowed_labels": list(metadata.get("labels", []) or []),
            "allowed_relationships": list(metadata.get("relationships", []) or []),
            "time_field_candidates": list(metadata.get("time_field_candidates", []) or []),
        }

    def execute_readonly_query(
        self,
        cypher_query: str,
        parameters: Optional[Dict[str, Any]] = None,
        limit_cap: int = 50,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        if self._driver is None:
            return {"ok": False, "error": "Neo4j runtime unavailable", "rows": []}

        validation_error = self.validate_readonly_query(cypher_query)
        if validation_error:
            return {"ok": False, "error": validation_error, "rows": []}

        params = self._normalize_parameters(parameters or {}, limit_cap=limit_cap)
        if params["limit"] <= 0:
            params["limit"] = min(limit_cap, 15)

        try:
            with self._driver.session(database=self._database) as session:
                explain_query = Query(f"EXPLAIN {cypher_query}", timeout=timeout_seconds)
                read_query = Query(cypher_query, timeout=timeout_seconds)
                session.run(explain_query, params).consume()
                result = session.run(read_query, params)
                rows = [self._normalize_record(dict(record)) for record in result]
            return {"ok": True, "rows": rows, "error": None}
        except Neo4jError as exc:
            logger.exception("Read-only graph query failed")
            return {"ok": False, "error": str(exc), "rows": []}

    def ensure_graphrag_vector_index(
        self,
        *,
        index_name: str,
        label: str,
        dimensions: int,
        property_name: str = "embedding_vector",
        similarity_function: str = "cosine",
    ) -> bool:
        if self._block_write("GraphRAG vector index creation"):
            return False
        if self._driver is None:
            return False
        normalized_index = str(index_name or "").strip()
        normalized_label = str(label or "").strip()
        normalized_property = str(property_name or "").strip()
        if _GRAPHRAG_VECTOR_INDEXES.get(normalized_index) != normalized_label:
            return False
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", normalized_property):
            return False
        if dimensions <= 0:
            return False
        similarity = str(similarity_function or "cosine").strip().lower()
        if similarity not in {"cosine", "euclidean"}:
            similarity = "cosine"

        statement = (
            f"CREATE VECTOR INDEX {normalized_index} IF NOT EXISTS "
            f"FOR (n:{normalized_label}) ON (n.{normalized_property}) "
            "OPTIONS {indexConfig: "
            "{`vector.dimensions`: $dimensions, `vector.similarity_function`: $similarity}}"
        )
        try:
            with self._driver.session(database=self._database) as session:
                session.run(statement, {"dimensions": int(dimensions), "similarity": similarity}).consume()
            return True
        except Neo4jError:
            logger.exception("Failed to ensure GraphRAG vector index %s", normalized_index)
            return False

    def set_graphrag_node_embedding(
        self,
        *,
        label: str,
        sys_id: str,
        embedding_vector: List[float],
        embedding_text_hash: str,
        embedding_model: str,
    ) -> bool:
        if self._block_write("GraphRAG embedding update"):
            return False
        if self._driver is None:
            return False
        normalized_label = str(label or "").strip()
        normalized_sys_id = str(sys_id or "").strip()
        if normalized_label not in _GRAPHRAG_VECTOR_INDEXES.values() or not normalized_sys_id:
            return False
        vector_payload = [float(value) for value in (embedding_vector or [])]
        if not vector_payload:
            return False

        query = (
            f"MATCH (n:{normalized_label} {{sys_id: $sys_id}}) "
            "SET n.embedding_vector = $embedding_vector, "
            "    n.embedding_text_hash = $embedding_text_hash, "
            "    n.embedding_model = $embedding_model, "
            "    n.embedding_updated_at = datetime() "
            "RETURN n.sys_id AS sys_id"
        )
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(
                    query,
                    {
                        "sys_id": normalized_sys_id,
                        "embedding_vector": vector_payload,
                        "embedding_text_hash": str(embedding_text_hash or "").strip(),
                        "embedding_model": str(embedding_model or "").strip(),
                    },
                )
                rows = list(result)
                return bool(rows)
        except Neo4jError:
            logger.exception("Failed to set GraphRAG embedding for %s:%s", normalized_label, normalized_sys_id)
            return False

    def execute_graphrag_vector_lookup(
        self,
        *,
        index_name: str,
        embedding: List[float],
        top_k: int = 12,
    ) -> Dict[str, Any]:
        if self._driver is None:
            return {"ok": False, "error": "Neo4j runtime unavailable", "rows": []}

        normalized_index = str(index_name or "").strip()
        label = _GRAPHRAG_VECTOR_INDEXES.get(normalized_index)
        if not label:
            return {"ok": False, "error": "Unsupported GraphRAG vector index", "rows": []}

        vector_payload = [float(value) for value in (embedding or [])]
        if not vector_payload:
            return {"ok": False, "error": "Embedding payload is empty", "rows": []}

        safe_top_k = max(1, min(int(top_k or 12), 40))
        query = (
            "CALL db.index.vector.queryNodes($index_name, $top_k, $embedding) "
            "YIELD node, score "
            "RETURN labels(node)[0] AS label, "
            "       coalesce(node.sys_id, '') AS sys_id, "
            "       coalesce(node.number, '') AS number, "
            "       coalesce(node.short_description, '') AS short_description, "
            "       coalesce(node.description, '') AS description, "
            "       coalesce(node.opened_at, '') AS opened_at, "
            "       coalesce(node.state, '') AS state, "
            "       toFloat(score) AS score"
        )
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(
                    query,
                    {
                        "index_name": normalized_index,
                        "top_k": safe_top_k,
                        "embedding": vector_payload,
                    },
                )
                rows = [self._normalize_record(dict(record)) for record in result]
        except Neo4jError as exc:
            logger.exception("GraphRAG vector lookup failed for index %s", normalized_index)
            return {"ok": False, "error": str(exc), "rows": []}

        filtered = [row for row in rows if str(row.get("label") or "") == label]
        return {"ok": True, "error": None, "rows": filtered}

    def ensure_vectorrag_vector_index(
        self,
        *,
        index_name: str,
        label: str,
        dimensions: int,
        property_name: str = "embedding_vector",
        similarity_function: str = "cosine",
    ) -> bool:
        if self._block_write("VectorRAG vector index creation"):
            return False
        if self._driver is None:
            return False
        normalized_index = str(index_name or "").strip()
        normalized_label = str(label or "").strip()
        normalized_property = str(property_name or "").strip()
        if _VECTORRAG_VECTOR_INDEXES.get(normalized_index) != normalized_label:
            return False
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", normalized_property):
            return False
        if dimensions <= 0:
            return False
        similarity = str(similarity_function or "cosine").strip().lower()
        if similarity not in {"cosine", "euclidean"}:
            similarity = "cosine"

        statement = (
            f"CREATE VECTOR INDEX {normalized_index} IF NOT EXISTS "
            f"FOR (n:{normalized_label}) ON (n.{normalized_property}) "
            "OPTIONS {indexConfig: "
            "{`vector.dimensions`: $dimensions, `vector.similarity_function`: $similarity}}"
        )
        try:
            with self._driver.session(database=self._database) as session:
                session.run(statement, {"dimensions": int(dimensions), "similarity": similarity}).consume()
            return True
        except Neo4jError:
            logger.exception("Failed to ensure VectorRAG vector index %s", normalized_index)
            return False

    def upsert_vectorrag_solution_document(self, payload: Dict[str, Any]) -> bool:
        if self._block_write("VectorRAG solution document upsert"):
            return False
        if self._driver is None:
            return False
        doc_id = str(payload.get("doc_id") or "").strip()
        if not doc_id:
            return False

        query = """
MERGE (doc:SolutionDocument {doc_id: $doc_id})
SET doc.name = $name,
    doc.display_name = $display_name,
    doc.content = $content,
    doc.source_file = $source_file,
    doc.source_index = $source_index,
    doc.content_hash = $content_hash,
    doc.embedding_vector = $embedding_vector,
    doc.embedding_model = $embedding_model,
    doc.embedding_updated_at = datetime(),
    doc.updated_at = datetime()
RETURN doc.doc_id AS doc_id
""".strip()
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(
                    query,
                    {
                        "doc_id": doc_id,
                        "name": str(payload.get("name") or "").strip(),
                        "display_name": str(payload.get("display_name") or "").strip(),
                        "content": str(payload.get("content") or "").strip(),
                        "source_file": str(payload.get("source_file") or "").strip(),
                        "source_index": int(payload.get("source_index") or 0),
                        "content_hash": str(payload.get("content_hash") or "").strip(),
                        "embedding_vector": [float(v) for v in (payload.get("embedding_vector") or [])],
                        "embedding_model": str(payload.get("embedding_model") or "").strip(),
                    },
                )
                return bool(list(result))
        except Neo4jError:
            logger.exception("Failed to upsert VectorRAG solution document %s", doc_id)
            return False

    def delete_vectorrag_solution_documents_not_in_ids(self, *, source_file: str, doc_ids: List[str]) -> bool:
        if self._block_write("VectorRAG stale document delete"):
            return False
        if self._driver is None:
            return False
        query = """
MATCH (doc:SolutionDocument)
WHERE doc.source_file = $source_file
  AND NOT doc.doc_id IN $doc_ids
DETACH DELETE doc
""".strip()
        try:
            with self._driver.session(database=self._database) as session:
                session.run(
                    query,
                    {
                        "source_file": str(source_file or "").strip(),
                        "doc_ids": [str(doc_id).strip() for doc_id in doc_ids if str(doc_id or "").strip()],
                    },
                ).consume()
            return True
        except Neo4jError:
            logger.exception("Failed deleting stale VectorRAG solution documents")
            return False

    def execute_vectorrag_solution_lookup(
        self,
        *,
        index_name: str,
        embedding: List[float],
        top_k: int = 5,
    ) -> Dict[str, Any]:
        if self._driver is None:
            return {"ok": False, "error": "Neo4j runtime unavailable", "rows": []}

        normalized_index = str(index_name or "").strip()
        label = _VECTORRAG_VECTOR_INDEXES.get(normalized_index)
        if not label:
            return {"ok": False, "error": "Unsupported VectorRAG vector index", "rows": []}

        vector_payload = [float(value) for value in (embedding or [])]
        if not vector_payload:
            return {"ok": False, "error": "Embedding payload is empty", "rows": []}

        safe_top_k = max(1, min(int(top_k or 5), 30))
        query = (
            "CALL db.index.vector.queryNodes($index_name, $top_k, $embedding) "
            "YIELD node, score "
            "RETURN labels(node)[0] AS label, "
            "       coalesce(node.doc_id, '') AS doc_id, "
            "       coalesce(node.name, '') AS name, "
            "       coalesce(node.display_name, '') AS display_name, "
            "       coalesce(node.content, '') AS content, "
            "       coalesce(node.source_file, '') AS source_file, "
            "       coalesce(node.source_index, 0) AS source_index, "
            "       toFloat(score) AS score"
        )
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(
                    query,
                    {
                        "index_name": normalized_index,
                        "top_k": safe_top_k,
                        "embedding": vector_payload,
                    },
                )
                rows = [self._normalize_record(dict(record)) for record in result]
        except Neo4jError as exc:
            logger.exception("VectorRAG vector lookup failed for index %s", normalized_index)
            return {"ok": False, "error": str(exc), "rows": []}

        filtered = [row for row in rows if str(row.get("label") or "") == label]
        return {"ok": True, "error": None, "rows": filtered}

    def upsert_vectorrag_corpus_metadata(
        self,
        *,
        source_file: str,
        corpus_hash: str,
        doc_count: int,
        embedding_model: str,
    ) -> bool:
        if self._block_write("VectorRAG corpus metadata upsert"):
            return False
        if self._driver is None:
            return False
        query = """
MERGE (meta:VectorRAGMeta {source_file: $source_file})
SET meta.corpus_hash = $corpus_hash,
    meta.doc_count = $doc_count,
    meta.embedding_model = $embedding_model,
    meta.last_indexed_at = datetime(),
    meta.updated_at = datetime()
RETURN meta.source_file AS source_file
""".strip()
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(
                    query,
                    {
                        "source_file": str(source_file or "").strip(),
                        "corpus_hash": str(corpus_hash or "").strip(),
                        "doc_count": int(max(0, doc_count)),
                        "embedding_model": str(embedding_model or "").strip(),
                    },
                )
                return bool(list(result))
        except Neo4jError:
            logger.exception("Failed to upsert VectorRAG corpus metadata")
            return False

    def read_vectorrag_corpus_metadata(self, *, source_file: str) -> Optional[Dict[str, Any]]:
        if self._driver is None:
            return None
        query = """
MATCH (meta:VectorRAGMeta {source_file: $source_file})
RETURN meta.source_file AS source_file,
       meta.corpus_hash AS corpus_hash,
       meta.doc_count AS doc_count,
       meta.embedding_model AS embedding_model,
       toString(meta.last_indexed_at) AS last_indexed_at
LIMIT 1
""".strip()
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(query, {"source_file": str(source_file or "").strip()})
                rows = [self._normalize_record(dict(record)) for record in result]
                return rows[0] if rows else None
        except Neo4jError:
            logger.exception("Failed to read VectorRAG corpus metadata")
            return None

    def validate_readonly_query(self, cypher_query: str) -> Optional[str]:
        normalized = str(cypher_query or "").strip()
        if not normalized:
            return "Cypher query is empty"
        if ";" in normalized:
            return "Cypher query must not contain semicolons"
        if "RETURN" not in normalized.upper():
            return "Cypher query must include a RETURN clause"

        upper = normalized.upper()
        for pattern in _BLOCKED_CYPHER_PATTERNS:
            if re.search(pattern, upper, flags=re.IGNORECASE):
                return f"Cypher query contains blocked clause matching pattern: {pattern}"

        strict_mode = self._schema_strict_mode()
        if strict_mode == "servicenow_strict":
            allowed_labels = _SERVICENOW_ALLOWED_LABELS
            allowed_relationships = _SERVICENOW_ALLOWED_RELATIONSHIPS
        else:
            metadata = self.live_schema_metadata()
            allowed_labels = set(metadata.get("labels", []) or [])
            allowed_relationships = set(metadata.get("relationships", []) or [])

        labels = set()
        for node_pattern in re.findall(r"\(([^)]*)\)", normalized):
            labels.update(re.findall(r":([A-Za-z][A-Za-z0-9_]*)", node_pattern))
        rel_types = set(re.findall(r"\[:([A-Z_][A-Z0-9_]*)", normalized))
        if (
            strict_mode != "servicenow_strict"
            and (not allowed_labels and not allowed_relationships)
            and self._driver is not None
            and (labels or rel_types)
        ):
            return "Live schema metadata is unavailable for dynamic validation"
        for label in labels:
            if label.isupper():
                continue
            if label not in allowed_labels and label not in allowed_relationships:
                return f"Cypher query references unsupported label or relationship: {label}"

        for rel_type in rel_types:
            if rel_type not in allowed_relationships:
                return f"Cypher query references unsupported relationship type: {rel_type}"

        param_names = set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", normalized))
        for param_name in param_names:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", param_name):
                return f"Cypher query references invalid parameter name: {param_name}"

        return None

    def _normalize_parameters(self, parameters: Dict[str, Any], limit_cap: int) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {
            "subject_terms": self._normalize_term_list(parameters.get("subject_terms")),
            "field_terms": self._normalize_term_list(parameters.get("field_terms")),
            "table_terms": self._normalize_term_list(parameters.get("table_terms")),
            "state_terms": self._normalize_term_list(parameters.get("state_terms")),
            "ticket_numbers": self._normalize_term_list(parameters.get("ticket_numbers")),
            "requester_terms": self._normalize_term_list(parameters.get("requester_terms")),
            "requester_sys_ids": self._normalize_term_list(parameters.get("requester_sys_ids")),
            "item_terms": self._normalize_term_list(parameters.get("item_terms")),
            "category_terms": self._normalize_term_list(parameters.get("category_terms")),
            "catalog_item_sys_ids": self._normalize_term_list(parameters.get("catalog_item_sys_ids")),
            "request_item_sys_ids": self._normalize_term_list(parameters.get("request_item_sys_ids")),
            "parent_item_terms": self._normalize_term_list(parameters.get("parent_item_terms")),
            "parent_category_terms": self._normalize_term_list(parameters.get("parent_category_terms")),
            "parent_catalog_item_sys_ids": self._normalize_term_list(parameters.get("parent_catalog_item_sys_ids")),
            "parent_request_item_sys_ids": self._normalize_term_list(parameters.get("parent_request_item_sys_ids")),
            "segment_one_terms": self._normalize_term_list(parameters.get("segment_one_terms")),
            "segment_two_terms": self._normalize_term_list(parameters.get("segment_two_terms")),
            "requester_one_terms": self._normalize_term_list(parameters.get("requester_one_terms")),
            "requester_two_terms": self._normalize_term_list(parameters.get("requester_two_terms")),
            "requester_one_sys_ids": self._normalize_term_list(parameters.get("requester_one_sys_ids")),
            "requester_two_sys_ids": self._normalize_term_list(parameters.get("requester_two_sys_ids")),
            "set_operation": self._normalize_set_operation(parameters.get("set_operation")),
            "opened_start": str(parameters.get("opened_start") or "").strip(),
            "opened_end": str(parameters.get("opened_end") or "").strip(),
            "measure": str(parameters.get("measure") or "").strip().lower(),
            "threshold": max(0, int(parameters.get("threshold") or 0)),
            "ticket_type": str(parameters.get("ticket_type") or "").strip().upper() or None,
            "offset": max(0, int(parameters.get("offset") or 0)),
            "sys_id": str(parameters.get("sys_id") or "").strip(),
            "source_file": str(parameters.get("source_file") or "").strip(),
            "index_name": str(parameters.get("index_name") or "").strip(),
            "sys_ids": [
                str(value).strip()
                for value in (parameters.get("sys_ids") or [])
                if str(value or "").strip()
            ],
            "doc_ids": [
                str(value).strip()
                for value in (parameters.get("doc_ids") or [])
                if str(value or "").strip()
            ],
            "limit": min(limit_cap, max(1, int(parameters.get("limit") or 15))),
        }
        return normalized

    def _normalize_set_operation(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"intersection", "union", "left_difference", "right_difference", "symmetric_difference"}:
            return normalized
        return "intersection"

    def _normalize_term_list(self, values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        normalized: List[str] = []
        seen = set()
        for value in values:
            term = str(value or "").strip().lower()
            if not term or term in seen:
                continue
            normalized.append(term)
            seen.add(term)
        return normalized

    def _normalize_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for key, value in record.items():
            normalized[key] = self._normalize_value(value)
        return normalized

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [self._normalize_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._normalize_value(item) for key, item in value.items()}
        if hasattr(value, "items"):
            try:
                return {str(key): self._normalize_value(item) for key, item in value.items()}
            except Exception:
                pass
        if hasattr(value, "nodes") and hasattr(value, "relationships"):
            try:
                return {
                    "nodes": [self._normalize_value(node) for node in value.nodes],
                    "relationships": [self._normalize_value(rel) for rel in value.relationships],
                }
            except Exception:
                pass
        return str(value)

    def _schema_strict_mode(self) -> str:
        normalized = str(getattr(settings, "graphrag_schema_strict_mode", "dynamic") or "dynamic").strip().lower()
        if normalized in {"dynamic", "servicenow_strict"}:
            return normalized
        return "dynamic"


neo4j_query_runtime_service = Neo4jQueryRuntimeService(settings.graphrag_neo4j_config())
platform_neo4j_query_runtime_service = Neo4jQueryRuntimeService(settings.platform_neo4j_config())
