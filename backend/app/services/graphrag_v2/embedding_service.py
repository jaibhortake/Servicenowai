from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.config import settings
from app.services.ai_service import get_client
from app.services.neo4j_query_runtime import neo4j_query_runtime_service

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "same",
    "similar",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "which",
    "with",
}

_EMBEDDING_MODEL_DIMENSIONS = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

_LABEL_CONFIG: Dict[str, Dict[str, Any]] = {
    "Incident": {
        "index_name": "incident_embedding_idx",
        "fields": [
            "short_description",
            "description",
            "category",
            "subcategory",
            "resolution_notes",
            "close_notes",
        ],
    },
    "Problem": {
        "index_name": "problem_embedding_idx",
        "fields": [
            "short_description",
            "description",
            "category",
            "cause_notes",
            "workaround",
            "resolution_notes",
            "fix_notes",
        ],
    },
    "ServiceRequest": {
        "index_name": "service_request_embedding_idx",
        "fields": [
            "short_description",
            "description",
            "resolution_notes",
            "priority",
        ],
    },
}


@dataclass
class _HybridCandidate:
    label: str
    sys_id: str
    number: str
    short_description: str
    description: str
    opened_at: str
    state: str
    vector_score: float = 0.0
    lexical_score: float = 0.0
    shared_problem_score: int = 0
    linkage_score: int = 0

    def as_row(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "sys_id": self.sys_id,
            "number": self.number,
            "short_description": self.short_description,
            "description": self.description or self.short_description,
            "opened_at": self.opened_at,
            "state": self.state,
            "shared_problem_score": self.shared_problem_score,
            "linkage_score": self.linkage_score,
            "vector_score": round(self.vector_score, 6),
            "lexical_score": round(self.lexical_score, 6),
        }


class GraphRAGv2EmbeddingService:
    def __init__(self) -> None:
        self._openai_client = None

    def startup(self) -> Dict[str, Any]:
        if not settings.graphrag_vector_enabled:
            return {"enabled": False, "indexes_created": 0}
        if not neo4j_query_runtime_service.is_available():
            return {"enabled": True, "ready": False, "reason": "neo4j query runtime unavailable"}
        if neo4j_query_runtime_service.is_write_protected():
            return {"enabled": True, "ready": False, "reason": "neo4j query runtime is write-protected"}

        created = 0
        for label, config in _LABEL_CONFIG.items():
            index_name = str(config["index_name"])
            dimensions = self._embedding_dimensions(
                settings.openai_embedding_dimension_model(settings.graphrag_embedding_model)
            )
            if neo4j_query_runtime_service.ensure_graphrag_vector_index(
                index_name=index_name,
                label=label,
                dimensions=dimensions,
                property_name="embedding_vector",
            ):
                created += 1
        return {"enabled": True, "indexes_created": created}

    def status(self) -> Dict[str, Any]:
        enabled = bool(settings.graphrag_vector_enabled)
        index_rows: List[Dict[str, Any]] = []
        coverage: Dict[str, Dict[str, int]] = {}

        if neo4j_query_runtime_service.is_available():
            index_result = neo4j_query_runtime_service.execute_readonly_query(
                (
                    "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, state "
                    "RETURN name, type, entityType, labelsOrTypes, properties, state"
                ),
                {},
                200,
                timeout_seconds=6.0,
            )
            if index_result.get("ok"):
                target_names = {cfg["index_name"] for cfg in _LABEL_CONFIG.values()}
                index_rows = [
                    row
                    for row in list(index_result.get("rows", []) or [])
                    if str(row.get("name") or "") in target_names
                ]
            coverage = self._embedding_coverage_counts()

        return {
            "enabled": enabled,
            "model": settings.openai_embedding_model(settings.graphrag_embedding_model),
            "top_k": max(1, int(settings.graphrag_vector_top_k or 12)),
            "batch_size": max(1, int(settings.graphrag_embedding_batch_size or 24)),
            "index_status": index_rows,
            "coverage": coverage,
        }

    def backfill(self, max_nodes: Optional[int] = None) -> Dict[str, Any]:
        if not settings.graphrag_vector_enabled:
            return {"ok": False, "reason": "graphrag_vector_enabled is false"}
        if not settings.openai_embedding_configured:
            return {"ok": False, "reason": "OpenAI embedding provider is not configured"}
        if not neo4j_query_runtime_service.is_available():
            return {"ok": False, "reason": "neo4j query runtime unavailable"}
        if neo4j_query_runtime_service.is_write_protected():
            return {"ok": False, "reason": "neo4j query runtime is write-protected"}

        self.startup()
        batch_size = max(1, int(settings.graphrag_embedding_batch_size or 24))
        remaining = max(1, int(max_nodes)) if max_nodes is not None else None
        scanned = 0
        updated = 0
        skipped = 0
        errors = 0
        model = settings.openai_embedding_model(settings.graphrag_embedding_model)

        for label in _LABEL_CONFIG.keys():
            offset = 0
            while True:
                if remaining is not None and remaining <= 0:
                    break

                fetch_limit = batch_size
                if remaining is not None:
                    fetch_limit = min(fetch_limit, remaining)
                rows = self._fetch_label_rows(label, fetch_limit, offset)
                if not rows:
                    break

                scanned += len(rows)
                offset += len(rows)

                pending: List[Dict[str, Any]] = []
                pending_texts: List[str] = []
                for row in rows:
                    text = self.build_embedding_text(label, row)
                    text_hash = self.compute_text_hash(text)
                    if not text.strip():
                        skipped += 1
                        continue
                    if not self.should_reembed(row, text_hash, model):
                        skipped += 1
                        continue
                    pending.append({**row, "embedding_text": text, "embedding_text_hash": text_hash})
                    pending_texts.append(text)

                vectors: List[List[float]] = []
                if pending_texts:
                    try:
                        vectors = self._embed_texts(pending_texts, model)
                    except Exception:
                        logger.exception("[GRAPHRAG_V2_EMBED] Failed to create embeddings for %s batch", label)
                        errors += len(pending_texts)
                        vectors = []

                for row, vector in zip(pending, vectors):
                    ok = neo4j_query_runtime_service.set_graphrag_node_embedding(
                        label=label,
                        sys_id=str(row.get("sys_id") or ""),
                        embedding_vector=vector,
                        embedding_text_hash=str(row.get("embedding_text_hash") or ""),
                        embedding_model=model,
                    )
                    if ok:
                        updated += 1
                    else:
                        errors += 1

                if remaining is not None:
                    remaining -= len(rows)
                if len(rows) < fetch_limit:
                    break

        return {
            "ok": True,
            "scanned": scanned,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "model": model,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    def retrieve_hybrid_similarity_candidates(self, question: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        if not settings.graphrag_vector_enabled:
            return []
        if not settings.openai_embedding_configured:
            return []
        if not neo4j_query_runtime_service.is_available():
            return []

        effective_top_k = max(1, int(top_k or settings.graphrag_vector_top_k or 12))
        vector_hits = self._vector_candidates_for_question(question, effective_top_k)
        lexical_hits = self._lexical_candidates_for_question(question, effective_top_k)
        merged = self._merge_candidates(vector_hits, lexical_hits)
        if not merged:
            return []

        seed = self._resolve_seed_candidate(merged)
        seed_problem_ids = self._fetch_problem_ids(seed.label, seed.sys_id) if seed is not None else set()
        linkage_scores = self._fetch_linkage_scores([candidate.sys_id for candidate in merged.values()])
        problem_cache: Dict[tuple[str, str], set[str]] = {}

        for candidate in merged.values():
            candidate.linkage_score = int(linkage_scores.get(candidate.sys_id, 0))
            if seed_problem_ids:
                cache_key = (candidate.label, candidate.sys_id)
                if cache_key not in problem_cache:
                    problem_cache[cache_key] = self._fetch_problem_ids(candidate.label, candidate.sys_id)
                candidate.shared_problem_score = len(seed_problem_ids & problem_cache[cache_key])

        ranked = self._rank_hybrid_candidates(list(merged.values()))
        return [candidate.as_row() for candidate in ranked[:effective_top_k]]

    @staticmethod
    def build_embedding_text(label: str, row: Dict[str, Any]) -> str:
        config = _LABEL_CONFIG.get(label)
        if not config:
            return ""

        parts: List[str] = []
        for field_name in config["fields"]:
            value = str(row.get(field_name) or "").strip()
            if not value:
                continue
            parts.append(f"{field_name}: {value}")
        return "\n".join(parts).strip()

    @staticmethod
    def compute_text_hash(text: str) -> str:
        return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()

    @staticmethod
    def should_reembed(row: Dict[str, Any], text_hash: str, embedding_model: str) -> bool:
        has_vector = bool(row.get("has_embedding"))
        stored_hash = str(row.get("embedding_text_hash") or "")
        stored_model = str(row.get("embedding_model") or "")
        if not has_vector:
            return True
        if stored_hash != text_hash:
            return True
        if stored_model != embedding_model:
            return True
        return False

    def _embedding_dimensions(self, model: str) -> int:
        normalized = str(model or "").strip().lower()
        return int(_EMBEDDING_MODEL_DIMENSIONS.get(normalized, int(settings.embedding_dim)))

    def _fetch_label_rows(self, label: str, limit: int, offset: int) -> List[Dict[str, Any]]:
        config = _LABEL_CONFIG[label]
        field_projection = ", ".join([f"n.{field_name} AS {field_name}" for field_name in config["fields"]])
        query = (
            f"MATCH (n:{label}) "
            "WHERE coalesce(n.sys_id, '') <> '' "
            f"RETURN n.sys_id AS sys_id, {field_projection}, "
            "n.embedding_text_hash AS embedding_text_hash, "
            "n.embedding_model AS embedding_model, "
            "CASE WHEN n.embedding_vector IS NULL THEN false ELSE true END AS has_embedding "
            "ORDER BY coalesce(n.opened_at, '') DESC, n.sys_id ASC "
            "SKIP $offset LIMIT $limit"
        )
        result = neo4j_query_runtime_service.execute_readonly_query(
            query,
            {"offset": int(max(0, offset)), "limit": int(max(1, limit))},
            limit_cap=max(1, limit),
            timeout_seconds=7.0,
        )
        if not result.get("ok"):
            return []
        return list(result.get("rows", []) or [])

    def _embed_texts(self, texts: List[str], model: str) -> List[List[float]]:
        client = self._get_openai_client()
        response = client.embeddings.create(model=model, input=texts)
        vectors: List[List[float]] = []
        for item in list(response.data or []):
            embedding = list(item.embedding or [])
            vectors.append([float(v) for v in embedding])
        return vectors

    def _get_openai_client(self):
        return get_client()

    def _vector_candidates_for_question(self, question: str, top_k: int) -> List[Dict[str, Any]]:
        text = str(question or "").strip()
        if not text:
            return []
        vector = self._embed_texts([text], settings.openai_embedding_model(settings.graphrag_embedding_model))[0]
        rows: List[Dict[str, Any]] = []
        for label, config in _LABEL_CONFIG.items():
            vector_result = neo4j_query_runtime_service.execute_graphrag_vector_lookup(
                index_name=str(config["index_name"]),
                embedding=vector,
                top_k=top_k,
            )
            if not vector_result.get("ok"):
                continue
            for row in list(vector_result.get("rows", []) or []):
                if str(row.get("label") or "") != label:
                    continue
                rows.append(row)
        return rows

    def _lexical_candidates_for_question(self, question: str, top_k: int) -> List[Dict[str, Any]]:
        terms = self._question_terms(question)
        if not terms:
            return []
        query = (
            "MATCH (n) "
            "WHERE (n:Incident OR n:Problem OR n:ServiceRequest) "
            "  AND coalesce(n.sys_id, '') <> '' "
            "WITH n, [term IN $subject_terms WHERE "
            "  toLower(coalesce(n.short_description, '')) CONTAINS term OR "
            "  toLower(coalesce(n.description, '')) CONTAINS term OR "
            "  toLower(coalesce(n.number, '')) CONTAINS term] AS hits "
            "WITH n, size(hits) AS hit_count "
            "WHERE hit_count > 0 "
            "RETURN labels(n)[0] AS label, "
            "       n.sys_id AS sys_id, "
            "       n.number AS number, "
            "       n.short_description AS short_description, "
            "       n.description AS description, "
            "       n.opened_at AS opened_at, "
            "       n.state AS state, "
            "       toFloat(hit_count) AS lexical_score "
            "ORDER BY lexical_score DESC, coalesce(n.opened_at, '') DESC "
            "LIMIT $limit"
        )
        result = neo4j_query_runtime_service.execute_readonly_query(
            query,
            {"subject_terms": terms, "limit": top_k},
            limit_cap=top_k,
            timeout_seconds=7.0,
        )
        if not result.get("ok"):
            return []
        return list(result.get("rows", []) or [])

    def _merge_candidates(
        self,
        vector_hits: List[Dict[str, Any]],
        lexical_hits: List[Dict[str, Any]],
    ) -> Dict[str, _HybridCandidate]:
        merged: Dict[str, _HybridCandidate] = {}

        def _upsert(hit: Dict[str, Any], vector: bool) -> None:
            sys_id = str(hit.get("sys_id") or "").strip()
            label = str(hit.get("label") or "").strip()
            if not sys_id or label not in _LABEL_CONFIG:
                return

            existing = merged.get(sys_id)
            if existing is None:
                existing = _HybridCandidate(
                    label=label,
                    sys_id=sys_id,
                    number=str(hit.get("number") or ""),
                    short_description=str(hit.get("short_description") or ""),
                    description=str(hit.get("description") or ""),
                    opened_at=str(hit.get("opened_at") or ""),
                    state=str(hit.get("state") or ""),
                )
                merged[sys_id] = existing

            if vector:
                existing.vector_score = max(existing.vector_score, float(hit.get("score") or 0.0))
            else:
                existing.lexical_score = max(existing.lexical_score, float(hit.get("lexical_score") or 0.0))

        for hit in vector_hits:
            _upsert(hit, vector=True)
        for hit in lexical_hits:
            _upsert(hit, vector=False)
        return merged

    def _resolve_seed_candidate(self, candidates: Dict[str, _HybridCandidate]) -> Optional[_HybridCandidate]:
        if not candidates:
            return None
        return sorted(
            candidates.values(),
            key=lambda candidate: (
                candidate.lexical_score,
                candidate.vector_score,
            ),
            reverse=True,
        )[0]

    def _fetch_problem_ids(self, label: str, sys_id: str) -> set[str]:
        label = str(label or "").strip()
        sys_id = str(sys_id or "").strip()
        if not label or not sys_id:
            return set()

        if label == "Problem":
            return {sys_id}
        if label == "Incident":
            query = (
                "MATCH (n:Incident {sys_id: $sys_id})-[:LINKED_TO_PROBLEM]->(p:Problem) "
                "RETURN collect(DISTINCT p.sys_id) AS problem_ids"
            )
        elif label == "ServiceRequest":
            query = (
                "MATCH (n:ServiceRequest {sys_id: $sys_id}) "
                "OPTIONAL MATCH (n)-[:TRIGGERED_BY]->(i1:Incident)-[:LINKED_TO_PROBLEM]->(p1:Problem) "
                "OPTIONAL MATCH (i2:Incident)-[:TRIGGERED_SR]->(n)-[:TRIGGERED_BY]->(i3:Incident)-[:LINKED_TO_PROBLEM]->(p2:Problem) "
                "WITH collect(DISTINCT p1.sys_id) + collect(DISTINCT p2.sys_id) AS ids "
                "RETURN [id IN ids WHERE id IS NOT NULL] AS problem_ids"
            )
        else:
            return set()

        result = neo4j_query_runtime_service.execute_readonly_query(
            query,
            {"sys_id": sys_id, "limit": 1},
            limit_cap=1,
            timeout_seconds=6.0,
        )
        if not result.get("ok") or not result.get("rows"):
            return set()
        values = result["rows"][0].get("problem_ids") or []
        return {str(value).strip() for value in values if str(value or "").strip()}

    def _fetch_linkage_scores(self, sys_ids: Iterable[str]) -> Dict[str, int]:
        valid_ids = [str(value or "").strip() for value in sys_ids if str(value or "").strip()]
        if not valid_ids:
            return {}

        available = set(neo4j_query_runtime_service.validation_context().get("allowed_relationships", []) or [])
        requested = ["TRIGGERED_BY", "TRIGGERED_SR", "INVOLVES_APPLIANCE"]
        active_types = [rel for rel in requested if rel in available]
        if not active_types:
            return {sys_id: 0 for sys_id in valid_ids}

        rel_clause = "|".join(active_types)
        query = (
            "UNWIND $sys_ids AS target_sys_id "
            "MATCH (n {sys_id: target_sys_id}) "
            f"OPTIONAL MATCH (n)-[out_rel:{rel_clause}]->() "
            "WITH target_sys_id, n, count(out_rel) AS out_count "
            f"OPTIONAL MATCH ()-[in_rel:{rel_clause}]->(n) "
            "RETURN target_sys_id AS sys_id, (out_count + count(in_rel)) AS linkage_score"
        )
        result = neo4j_query_runtime_service.execute_readonly_query(
            query,
            {"sys_ids": valid_ids, "limit": len(valid_ids)},
            limit_cap=max(1, len(valid_ids)),
            timeout_seconds=6.0,
        )
        if not result.get("ok"):
            return {sys_id: 0 for sys_id in valid_ids}
        scores: Dict[str, int] = {}
        for row in list(result.get("rows", []) or []):
            scores[str(row.get("sys_id") or "")] = int(row.get("linkage_score") or 0)
        return scores

    def _embedding_coverage_counts(self) -> Dict[str, Dict[str, int]]:
        coverage: Dict[str, Dict[str, int]] = {}
        for label in _LABEL_CONFIG.keys():
            query = (
                f"MATCH (n:{label}) "
                "RETURN count(n) AS total, "
                "count(CASE WHEN n.embedding_vector IS NOT NULL THEN 1 END) AS embedded"
            )
            result = neo4j_query_runtime_service.execute_readonly_query(
                query,
                {"limit": 1},
                limit_cap=1,
                timeout_seconds=6.0,
            )
            if not result.get("ok") or not result.get("rows"):
                coverage[label] = {"total": 0, "embedded": 0}
                continue
            row = result["rows"][0]
            coverage[label] = {
                "total": int(row.get("total") or 0),
                "embedded": int(row.get("embedded") or 0),
            }
        return coverage

    def _question_terms(self, question: str) -> List[str]:
        terms = []
        seen = set()
        for token in re.findall(r"[A-Za-z0-9_-]+", str(question or "").lower()):
            if len(token) < 3:
                continue
            if token in _STOPWORDS:
                continue
            if token in seen:
                continue
            terms.append(token)
            seen.add(token)
        return terms[:10]

    @staticmethod
    def _rank_hybrid_candidates(candidates: List[_HybridCandidate]) -> List[_HybridCandidate]:
        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.shared_problem_score > 0,
                candidate.shared_problem_score,
                candidate.linkage_score > 0,
                candidate.linkage_score,
                candidate.vector_score,
                candidate.lexical_score,
            ),
            reverse=True,
        )


graphrag_v2_embedding_service = GraphRAGv2EmbeddingService()
