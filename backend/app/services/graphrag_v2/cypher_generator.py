from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.ai_service import get_async_client

from .types import GraphRAGv2CypherCandidate, GraphRAGv2SchemaSnapshot, GraphRAGv2TimeWindow

logger = logging.getLogger(__name__)


class _GraphRAGv2CandidateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    query_shape: str
    purpose: str
    cypher_query: str


class _GraphRAGv2CandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: List[_GraphRAGv2CandidateEnvelope] = Field(default_factory=list)


class GraphRAGv2CypherGenerator:
    async def generate_candidates(
        self,
        *,
        question: str,
        schema_snapshot: GraphRAGv2SchemaSnapshot,
        desired_shape: str,
        time_window: Optional[GraphRAGv2TimeWindow] = None,
        max_candidates: int = 2,
        query_family: str = "",
        planning_hints: Optional[List[str]] = None,
        required_evidence_fields: Optional[List[str]] = None,
    ) -> List[GraphRAGv2CypherCandidate]:
        async_client = get_async_client()
        completion = await asyncio.wait_for(
            async_client.beta.chat.completions.parse(
                model=settings.openai_chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You generate safe read-only Neo4j Cypher for a live Neo4j graph. "
                            "Return structured JSON only. Never use write clauses. "
                            "Use LIMIT 20 unless counting. Use labels and relationships only from the provided snapshot."
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._build_prompt(
                            question=question,
                            schema_snapshot=schema_snapshot,
                            desired_shape=desired_shape,
                            time_window=time_window,
                            max_candidates=max_candidates,
                            query_family=query_family,
                            planning_hints=planning_hints or [],
                            required_evidence_fields=required_evidence_fields or [],
                        ),
                    },
                ],
                temperature=0.1,
                response_format=_GraphRAGv2CandidateResponse,
            ),
            timeout=14.0,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("GraphRAGv2 candidate generation returned no structured payload")

        candidates: List[GraphRAGv2CypherCandidate] = []
        for index, candidate in enumerate(parsed.candidates[: max(1, min(max_candidates, 2))], start=1):
            cypher_query = str(candidate.cypher_query or "").strip()
            if not cypher_query:
                continue
            candidates.append(
                GraphRAGv2CypherCandidate(
                    candidate_id=str(candidate.candidate_id or f"candidate_{index}"),
                    query_shape=self._normalize_query_shape(candidate.query_shape, desired_shape),
                    purpose=str(candidate.purpose or "").strip(),
                    cypher_query=cypher_query,
                    parameters={},
                )
            )
        return candidates

    async def repair_candidate(
        self,
        *,
        question: str,
        schema_snapshot: GraphRAGv2SchemaSnapshot,
        original_candidate: GraphRAGv2CypherCandidate,
        error_message: str,
        time_window: Optional[GraphRAGv2TimeWindow] = None,
        query_family: str = "",
        planning_hints: Optional[List[str]] = None,
        required_evidence_fields: Optional[List[str]] = None,
    ) -> Optional[GraphRAGv2CypherCandidate]:
        async_client = get_async_client()
        completion = await asyncio.wait_for(
            async_client.beta.chat.completions.parse(
                model=settings.openai_chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You repair a single read-only Neo4j Cypher query. "
                            "Preserve the same question intent, output shape, entities, and time window. "
                            "Do not broaden scope or switch query families. Return structured JSON only."
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._build_repair_prompt(
                            question=question,
                            schema_snapshot=schema_snapshot,
                            original_candidate=original_candidate,
                            error_message=error_message,
                            time_window=time_window,
                            query_family=query_family,
                            planning_hints=planning_hints or [],
                            required_evidence_fields=required_evidence_fields or [],
                        ),
                    },
                ],
                temperature=0.0,
                response_format=_GraphRAGv2CandidateResponse,
            ),
            timeout=12.0,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None or not parsed.candidates:
            return None

        repaired = parsed.candidates[0]
        cypher_query = str(repaired.cypher_query or "").strip()
        if not cypher_query:
            return None

        return GraphRAGv2CypherCandidate(
            candidate_id=f"{original_candidate.candidate_id}_repair",
            query_shape=original_candidate.query_shape,
            purpose=str(repaired.purpose or original_candidate.purpose or "").strip(),
            cypher_query=cypher_query,
            parameters=dict(original_candidate.parameters),
        )

    def _build_prompt(
        self,
        *,
        question: str,
        schema_snapshot: GraphRAGv2SchemaSnapshot,
        desired_shape: str,
        time_window: Optional[GraphRAGv2TimeWindow],
        max_candidates: int,
        query_family: str,
        planning_hints: List[str],
        required_evidence_fields: List[str],
    ) -> str:
        time_window_text = "No explicit time window resolved."
        if time_window and (time_window.start or time_window.end):
            time_window_text = (
                f"Resolved time window: {time_window.label}\n"
                f"- opened_start: {time_window.start}\n"
                f"- opened_end: {time_window.end}"
            )
        hint_mode_text = "ServiceNow-specific hints are disabled."
        if schema_snapshot.service_now_hints_enabled:
            hint_mode_text = "ServiceNow-specific hints are enabled because the live graph profile appears ServiceNow-like."
        time_field_text = "No trusted time-like property candidates were detected from the live schema."
        if schema_snapshot.time_field_candidates:
            time_field_text = "Detected time-like property candidates: " + ", ".join(schema_snapshot.time_field_candidates[:8])
        family_text = query_family or "generic"
        planning_hints_text = "\n".join(f"- {hint}" for hint in planning_hints) or "- No additional planning hints."
        required_evidence_text = ", ".join(required_evidence_fields) if required_evidence_fields else "No explicit required evidence fields."
        return f"""Question:
{question}

Desired answer shape:
{desired_shape}

Detected query family:
{family_text}

Live schema snapshot id:
{schema_snapshot.snapshot_id}

Graph profile:
{schema_snapshot.graph_profile}

{hint_mode_text}

{time_window_text}

{time_field_text}

Live schema and business hints:
{schema_snapshot.summary_text}

Additional planning hints:
{planning_hints_text}

Required evidence fields:
{required_evidence_text}

Return JSON with:
- candidates: array of 1 to {max_candidates} objects

Each candidate object must contain:
- candidate_id
- query_shape: one of count, list, compare, cohort, rank, aggregate, lookup
- purpose
- cypher_query

Rules:
- Return 1-2 candidates only.
- Use read-only Cypher only: MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT.
- Never use CREATE, MERGE, DELETE, DETACH, SET, REMOVE, DROP, ALTER, CALL, APOC, LOAD CSV, or semicolons.
- Prefer exact graph traversals over vague text matching.
- Use only labels, relationship types, and property names that appear in the live schema snapshot.
- Do not assume ServiceNow entities like Ticket, User, RequestItem, or CatalogItem unless they are explicitly present in the snapshot.
- Do not use exact equality on free-text business properties such as name, title, display_name, short_description, description, or category unless the graph identifier is explicitly known.
- For person names from natural language, do not rely on exact node maps like {{name: '...'}} unless the graph identifier is explicitly known.
- For natural-language entity matching, prefer case-insensitive contains/token matching on relevant text properties over brittle exact equality.
- If Additional planning hints include intent search fields, build text matching over those mapped fields across the listed labels.
- For intent search fields, do not use only one description field and do not require the entire user phrase to appear in a single property.
- Prefer matching several meaningful user terms across any mapped fields using case-insensitive CONTAINS with OR groups.
- For recommendation or triage questions, return remediation evidence from linked Problem fields even when the text match starts on Incident or ServiceRequest.
- Prefer graph-native joins based on observed relationships rather than invented hidden joins.
- If a resolved time window is provided, use parameterized string window filters with $opened_start and $opened_end.
- If credible time-like properties exist in the snapshot, do not use date(), datetime(), duration(), localdatetime(), or timestamp arithmetic.
- Instead compare the chosen time field with string-safe filters like toString(coalesce(node.time_field, '')) >= $opened_start and < $opened_end.
- If no credible time-like property exists, do not invent one.
- If the query has a follow-up clause like "in the same time window", reuse the same $opened_start and $opened_end filters for every relevant matched entity set.
- For count questions, return aggregated counts with explicit aliases.
- For list questions, return concise rows with explicit aliases such as name, number, state, opened_at, description, request_count.
- For grouped or split list questions, prefer flat grounded rows over nested collect maps or object literals. The answer can group rows later.
- If required evidence fields are listed above, retrieve them explicitly in the returned rows whenever the graph contains them.
- Do not invent labels or relationships not present in the provided snapshot."""

    def _build_repair_prompt(
        self,
        *,
        question: str,
        schema_snapshot: GraphRAGv2SchemaSnapshot,
        original_candidate: GraphRAGv2CypherCandidate,
        error_message: str,
        time_window: Optional[GraphRAGv2TimeWindow],
        query_family: str,
        planning_hints: List[str],
        required_evidence_fields: List[str],
    ) -> str:
        time_window_text = "No explicit time window resolved."
        if time_window and (time_window.start or time_window.end):
            time_window_text = (
                f"Resolved time window: {time_window.label}\n"
                f"- opened_start: {time_window.start}\n"
                f"- opened_end: {time_window.end}"
            )
        planning_hints_text = "\n".join(f"- {hint}" for hint in planning_hints) or "- No additional planning hints."
        required_evidence_text = ", ".join(required_evidence_fields) if required_evidence_fields else "No explicit required evidence fields."
        return f"""Question:
{question}

Detected query family:
{query_family or 'generic'}

Desired answer shape:
{original_candidate.query_shape}

{time_window_text}

Live schema and business hints:
{schema_snapshot.summary_text}

Additional planning hints:
{planning_hints_text}

Required evidence fields:
{required_evidence_text}

Original candidate purpose:
{original_candidate.purpose}

Original candidate Cypher:
{original_candidate.cypher_query}

Execution or validation error:
{error_message}

Return JSON with exactly one repaired candidate.

Rules:
- Preserve the same output shape, entities, scope, and time window.
- Do not broaden or narrow the query beyond fixing the error.
- Keep it read-only and schema-grounded.
- If Additional planning hints include intent search fields, repair brittle single-field or full-phrase matching by searching those mapped fields with token-level CONTAINS.
- If required evidence fields are listed above, retrieve them explicitly when present in the graph.
- Return only the repaired candidate in structured JSON."""

    def _normalize_query_shape(self, value: str, fallback: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"count", "list", "compare", "cohort", "rank", "aggregate", "lookup"}:
            return normalized
        return fallback

