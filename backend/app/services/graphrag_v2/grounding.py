from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.ai_service import get_async_client
from app.services.neo4j_query_runtime import neo4j_query_runtime_service

from .ontology import GraphRAGv2OntologyPlanner
from .types import GraphRAGv2OntologyProfile, GraphRAGv2SchemaSnapshot

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "are",
    "available",
    "based",
    "do",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "me",
    "of",
    "on",
    "the",
    "this",
    "to",
    "what",
    "which",
    "with",
}
_DEFAULT_SEARCH_FIELDS: Dict[str, List[str]] = {
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
}
_SEMANTIC_EXPANSIONS: Dict[str, List[str]] = {
    "bbu": ["battery", "backup", "battery backup", "battery backup unit"],
    "battery": ["bbu", "battery backup", "battery backup unit"],
    "backup": ["bbu", "battery backup", "battery backup unit"],
    "certificate": ["cert", "tls", "ssl"],
    "cert": ["certificate", "tls", "ssl"],
    "degradation": ["degraded", "failure", "failed"],
    "degraded": ["degradation", "failure", "failed"],
    "expiry": ["expired", "expiration", "expires"],
    "expired": ["expiry", "expiration", "expires"],
    "ssl": ["tls", "certificate", "cert"],
    "tls": ["ssl", "certificate", "cert"],
    "wildcard": ["wildcard certificate"],
}


class _GroundingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    broad_category: str = ""
    required_concepts: List[str] = Field(default_factory=list)
    related_terms: List[str] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)
    candidate_labels: List[str] = Field(default_factory=list)


class _GroundingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_keys: List[str] = Field(default_factory=list)


class GraphRAGv2GroundingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    broad_category: str = ""
    terms: List[str] = Field(default_factory=list)
    candidate_count: int = 0
    selected_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_rows: List[Dict[str, Any]] = Field(default_factory=list)
    planning_hints: List[str] = Field(default_factory=list)
    error: str = ""


class GraphRAGv2GroundingService:
    async def ground(
        self,
        *,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        ontology_profile: GraphRAGv2OntologyProfile,
        query_family: str,
        max_candidates: int = 24,
        max_selected: int = 8,
    ) -> GraphRAGv2GroundingResult:
        if not neo4j_query_runtime_service.is_available():
            return GraphRAGv2GroundingResult(enabled=False, error="neo4j query runtime unavailable")

        search_fields = self._search_fields(query_family, ontology_profile)
        if not search_fields:
            return GraphRAGv2GroundingResult(enabled=False, error="no schema-backed search fields")

        try:
            plan = await self._build_grounding_plan(question, snapshot, query_family, search_fields)
        except Exception as exc:
            logger.info("[GRAPHRAG_V2_GROUNDING] Falling back to heuristic grounding plan: %s", exc)
            plan = self._heuristic_plan(question, search_fields)

        terms = self._normalized_terms(question, plan)
        candidates = self._fetch_candidates(search_fields, terms, max_candidates)
        if not candidates:
            return GraphRAGv2GroundingResult(
                enabled=True,
                broad_category=plan.broad_category,
                terms=terms,
                candidate_count=0,
                planning_hints=self._planning_hints(plan, terms, []),
            )

        try:
            selected = await self._select_candidates(question, plan, candidates, max_selected)
        except Exception as exc:
            logger.info("[GRAPHRAG_V2_GROUNDING] Falling back to lexical candidate ranking: %s", exc)
            selected = candidates[:max_selected]

        evidence_rows = self._expand_selected_candidates(selected)
        return GraphRAGv2GroundingResult(
            enabled=True,
            broad_category=plan.broad_category,
            terms=terms,
            candidate_count=len(candidates),
            selected_candidates=selected,
            evidence_rows=evidence_rows,
            planning_hints=self._planning_hints(plan, terms, selected),
        )

    async def _build_grounding_plan(
        self,
        question: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        query_family: str,
        search_fields: Dict[str, List[str]],
    ) -> _GroundingPlan:
        if not settings.openai_chat_configured:
            raise RuntimeError("OpenAI chat provider is not configured")
        labels_text = ", ".join(search_fields.keys())
        fields_text = "; ".join(f"{label}: {', '.join(fields)}" for label, fields in sorted(search_fields.items()))
        async_client = get_async_client()
        completion = await asyncio.wait_for(
            async_client.beta.chat.completions.parse(
                model=settings.openai_chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You create broad graph grounding plans for ITSM GraphRAG. "
                            "Return structured JSON only. Prefer concepts and aliases over exact phrases."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question:\n{question}\n\n"
                            f"Detected query family: {query_family or 'generic'}\n"
                            f"Available candidate labels: {labels_text}\n"
                            f"Useful search fields: {fields_text}\n\n"
                            "Return a broad grounding plan. Include normalized IT concepts, acronyms, "
                            "spelled-out variants, and likely operational wording. For example, "
                            "wildcard expiry can imply wildcard certificate expiry, SSL, TLS, cert, expired; "
                            "battery backup unit can imply BBU."
                        ),
                    },
                ],
                temperature=0.0,
                response_format=_GroundingPlan,
            ),
            timeout=8.0,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("grounding plan response was empty")
        allowed_labels = set(search_fields.keys())
        candidate_labels = [label for label in parsed.candidate_labels if label in allowed_labels]
        return parsed.model_copy(update={"candidate_labels": candidate_labels or list(search_fields.keys())})

    async def _select_candidates(
        self,
        question: str,
        plan: _GroundingPlan,
        candidates: List[Dict[str, Any]],
        max_selected: int,
    ) -> List[Dict[str, Any]]:
        if not settings.openai_chat_configured:
            raise RuntimeError("OpenAI chat provider is not configured")
        candidate_text = "\n".join(
            self._candidate_prompt_line(candidate)
            for candidate in candidates[: max(1, max_selected * 3)]
        )
        async_client = get_async_client()
        completion = await asyncio.wait_for(
            async_client.beta.chat.completions.parse(
                model=settings.openai_chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You select graph candidate nodes nearest to a user question. "
                            "Return only keys that are grounded by candidate text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question:\n{question}\n\n"
                            f"Broad category: {plan.broad_category}\n"
                            f"Required concepts: {', '.join(plan.required_concepts)}\n"
                            f"Related terms and aliases: {', '.join(plan.related_terms + plan.aliases)}\n\n"
                            f"Candidate nodes:\n{candidate_text}\n\n"
                            f"Select up to {max_selected} nearest candidate keys. Prefer candidates whose text "
                            "covers the broad category and the important concepts, even when wording differs."
                        ),
                    },
                ],
                temperature=0.0,
                response_format=_GroundingSelection,
            ),
            timeout=8.0,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("grounding selection response was empty")
        by_key = {str(candidate.get("candidate_key") or ""): candidate for candidate in candidates}
        selected = [
            by_key[key]
            for key in parsed.selected_keys
            if key in by_key
        ]
        return selected[:max_selected] or candidates[:max_selected]

    def _search_fields(
        self,
        query_family: str,
        ontology_profile: GraphRAGv2OntologyProfile,
    ) -> Dict[str, List[str]]:
        planner = GraphRAGv2OntologyPlanner()
        fields = planner.intent_search_fields(query_family, ontology_profile)
        if not fields:
            available = {
                label: set(values)
                for label, values in dict(ontology_profile.search_fields_by_label or {}).items()
            }
            for label, desired in _DEFAULT_SEARCH_FIELDS.items():
                if label in available:
                    label_fields = [field for field in desired if field in available[label]]
                    if label_fields:
                        fields[label] = label_fields
        return {
            label: field_names
            for label, field_names in fields.items()
            if _SAFE_NAME.fullmatch(label) and all(_SAFE_NAME.fullmatch(field) for field in field_names)
        }

    def _fetch_candidates(
        self,
        search_fields: Dict[str, List[str]],
        terms: List[str],
        max_candidates: int,
    ) -> List[Dict[str, Any]]:
        if not terms:
            return []
        candidates: List[Dict[str, Any]] = []
        per_label_limit = max(6, min(20, max_candidates))
        for label, fields in search_fields.items():
            text_expr = " + ' ' + ".join(f"coalesce(toString(n.{field}), '')" for field in fields)
            query = (
                f"MATCH (n:{label}) "
                "WHERE coalesce(n.sys_id, '') <> '' "
                f"WITH n, toLower({text_expr}) AS grounding_text "
                "WITH n, grounding_text, [term IN $subject_terms WHERE grounding_text CONTAINS term] AS hits "
                "WHERE size(hits) > 0 "
                f"RETURN '{label}' AS label, "
                "       n.sys_id AS sys_id, "
                "       n.number AS number, "
                "       n.short_description AS short_description, "
                "       n.description AS description, "
                "       n.cause_notes AS cause_notes, "
                "       n.workaround AS workaround, "
                "       n.resolution_notes AS resolution_notes, "
                "       n.fix_notes AS fix_notes, "
                "       n.close_notes AS close_notes, "
                "       hits AS matched_terms, "
                "       toFloat(size(hits)) AS grounding_score "
                "ORDER BY grounding_score DESC, coalesce(n.opened_at, '') DESC "
                "LIMIT $limit"
            )
            result = neo4j_query_runtime_service.execute_readonly_query(
                query,
                {"subject_terms": terms, "limit": per_label_limit},
                limit_cap=per_label_limit,
                timeout_seconds=7.0,
            )
            if not result.get("ok"):
                continue
            for row in list(result.get("rows", []) or []):
                row["candidate_key"] = f"{row.get('label')}:{row.get('sys_id')}"
                candidates.append(row)

        candidates.sort(
            key=lambda row: (
                float(row.get("grounding_score") or 0.0),
                len(str(row.get("short_description") or "")),
            ),
            reverse=True,
        )
        seen = set()
        unique: List[Dict[str, Any]] = []
        for candidate in candidates:
            key = str(candidate.get("candidate_key") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique[:max_candidates]

    def _expand_selected_candidates(self, selected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for candidate in selected:
            label = str(candidate.get("label") or "")
            sys_id = str(candidate.get("sys_id") or "")
            if label == "Problem":
                rows.extend(self._expand_problem(sys_id, candidate))
            elif label == "Incident":
                rows.extend(self._expand_incident(sys_id, candidate))
            elif label == "ServiceRequest":
                rows.extend(self._expand_service_request(sys_id, candidate))
        return self._dedupe_rows(rows)[:20]

    def _expand_problem(self, sys_id: str, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = (
            "MATCH (p:Problem {sys_id: $sys_id}) "
            "OPTIONAL MATCH (i:Incident)-[:LINKED_TO_PROBLEM]->(p) "
            "RETURN i.sys_id AS incident_sys_id, "
            "       i.number AS incident_number, "
            "       i.short_description AS short_description, "
            "       i.state AS state, "
            "       i.opened_at AS opened_at, "
            "       p.sys_id AS problem_sys_id, "
            "       p.short_description AS problem_short_description, "
            "       p.cause_notes AS cause_notes, "
            "       p.workaround AS workaround, "
            "       p.resolution_notes AS resolution_notes, "
            "       p.fix_notes AS fix_notes "
            "LIMIT $limit"
        )
        return self._expanded_rows(query, sys_id, candidate)

    def _expand_incident(self, sys_id: str, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = (
            "MATCH (i:Incident {sys_id: $sys_id}) "
            "OPTIONAL MATCH (i)-[:LINKED_TO_PROBLEM]->(p:Problem) "
            "RETURN i.sys_id AS incident_sys_id, "
            "       i.number AS incident_number, "
            "       i.short_description AS short_description, "
            "       i.state AS state, "
            "       i.opened_at AS opened_at, "
            "       p.sys_id AS problem_sys_id, "
            "       p.short_description AS problem_short_description, "
            "       p.cause_notes AS cause_notes, "
            "       p.workaround AS workaround, "
            "       p.resolution_notes AS resolution_notes, "
            "       p.fix_notes AS fix_notes "
            "LIMIT $limit"
        )
        return self._expanded_rows(query, sys_id, candidate)

    def _expand_service_request(self, sys_id: str, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = (
            "MATCH (sr:ServiceRequest {sys_id: $sys_id}) "
            "OPTIONAL MATCH (sr)-[:TRIGGERED_BY]->(i1:Incident)-[:LINKED_TO_PROBLEM]->(p1:Problem) "
            "OPTIONAL MATCH (i2:Incident)-[:TRIGGERED_SR]->(sr) "
            "OPTIONAL MATCH (i2)-[:LINKED_TO_PROBLEM]->(p2:Problem) "
            "WITH sr, collect(DISTINCT i1) + collect(DISTINCT i2) AS incidents, "
            "     collect(DISTINCT p1) + collect(DISTINCT p2) AS problems "
            "UNWIND CASE WHEN size(incidents) = 0 THEN [null] ELSE incidents END AS i "
            "UNWIND CASE WHEN size(problems) = 0 THEN [null] ELSE problems END AS p "
            "RETURN i.sys_id AS incident_sys_id, "
            "       i.number AS incident_number, "
            "       coalesce(i.short_description, sr.short_description) AS short_description, "
            "       coalesce(i.state, sr.state) AS state, "
            "       coalesce(i.opened_at, sr.opened_at) AS opened_at, "
            "       p.sys_id AS problem_sys_id, "
            "       p.short_description AS problem_short_description, "
            "       p.cause_notes AS cause_notes, "
            "       coalesce(p.workaround, sr.resolution_notes) AS workaround, "
            "       coalesce(p.resolution_notes, sr.resolution_notes) AS resolution_notes, "
            "       p.fix_notes AS fix_notes "
            "LIMIT $limit"
        )
        return self._expanded_rows(query, sys_id, candidate)

    def _expanded_rows(self, query: str, sys_id: str, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = neo4j_query_runtime_service.execute_readonly_query(
            query,
            {"sys_id": sys_id, "limit": 20},
            limit_cap=20,
            timeout_seconds=7.0,
        )
        if not result.get("ok"):
            return []
        rows: List[Dict[str, Any]] = []
        for row in list(result.get("rows", []) or []):
            row["grounding_source_label"] = candidate.get("label")
            row["grounding_source_sys_id"] = candidate.get("sys_id")
            row["grounding_score"] = candidate.get("grounding_score")
            row["grounding_matched_terms"] = candidate.get("matched_terms") or []
            rows.append(row)
        return rows

    def _planning_hints(
        self,
        plan: _GroundingPlan,
        terms: List[str],
        selected: List[Dict[str, Any]],
    ) -> List[str]:
        hints = [
            "Grounding layer ran before Cypher generation.",
            f"Grounding broad category: {plan.broad_category or 'unspecified'}.",
            "Grounding terms and aliases: " + ", ".join(terms[:20]) + ".",
            "Use selected grounding candidates as anchors; prefer sys_id matching over exact phrase text matching.",
        ]
        if selected:
            previews = []
            for candidate in selected[:8]:
                previews.append(
                    f"{candidate.get('label')} sys_id={candidate.get('sys_id')} "
                    f"title={self._compact(candidate.get('short_description') or candidate.get('description'), 100)}"
                )
            hints.append("Selected grounding candidates: " + " | ".join(previews) + ".")
        return hints

    def _heuristic_plan(self, question: str, search_fields: Dict[str, List[str]]) -> _GroundingPlan:
        terms = self._question_terms(question)
        return _GroundingPlan(
            broad_category=" ".join(terms[:6]),
            required_concepts=terms[:6],
            related_terms=[],
            aliases=[],
            candidate_labels=list(search_fields.keys()),
        )

    def _normalized_terms(self, question: str, plan: _GroundingPlan) -> List[str]:
        raw: List[str] = []
        raw.extend(self._question_terms(question))
        for value in [plan.broad_category, *plan.required_concepts, *plan.related_terms, *plan.aliases]:
            raw.extend(self._question_terms(value))
            normalized = str(value or "").strip().lower()
            if normalized and " " in normalized and len(normalized) <= 80:
                raw.append(normalized)

        expanded: List[str] = []
        for term in raw:
            expanded.append(term)
            expanded.extend(_SEMANTIC_EXPANSIONS.get(term, []))
        return self._unique_terms(expanded)[:40]

    def _question_terms(self, value: str) -> List[str]:
        terms: List[str] = []
        for token in re.findall(r"[A-Za-z0-9_.*-]+", str(value or "").lower()):
            token = token.strip("._-*")
            if len(token) < 3 or token in _STOPWORDS:
                continue
            terms.append(token)
        return terms

    def _unique_terms(self, terms: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for term in terms:
            normalized = str(term or "").strip().lower()
            if len(normalized) < 3 or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _candidate_prompt_line(self, candidate: Dict[str, Any]) -> str:
        parts = [
            f"key={candidate.get('candidate_key')}",
            f"label={candidate.get('label')}",
            f"sys_id={candidate.get('sys_id')}",
            f"number={candidate.get('number') or ''}",
            f"title={self._compact(candidate.get('short_description'), 160)}",
            f"description={self._compact(candidate.get('description'), 220)}",
            f"guidance={self._compact(candidate.get('workaround') or candidate.get('resolution_notes') or candidate.get('fix_notes'), 260)}",
            f"matched_terms={', '.join(candidate.get('matched_terms') or [])}",
        ]
        return "- " + "; ".join(parts)

    def _dedupe_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            incident_key = str(row.get("incident_sys_id") or row.get("incident_number") or "")
            problem_key = str(row.get("problem_sys_id") or row.get("problem_short_description") or "")
            key = (incident_key, problem_key)
            if not incident_key and not problem_key:
                key = ("source", str(row.get("grounding_source_sys_id") or ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped

    def _compact(self, value: Any, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

