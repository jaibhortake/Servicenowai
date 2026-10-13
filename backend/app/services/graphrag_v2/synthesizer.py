from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from app.config import settings
from app.services.ai_service import get_async_client

from .types import GraphRAGv2ExecutionResult


class GraphRAGv2Synthesizer:
    async def synthesize_sections(
        self,
        *,
        question: str,
        execution_results: List[GraphRAGv2ExecutionResult],
    ) -> str:
        sections = [
            {
                "candidate_id": execution.candidate_id,
                "query_shape": execution.query_shape,
                "purpose": execution.purpose,
                "cypher_query": execution.cypher_query,
                "rows": execution.rows[:20],
            }
            for execution in execution_results
        ]
        sections_json = json.dumps(sections, ensure_ascii=True, default=str, indent=2)
        async_client = get_async_client()
        completion = await asyncio.wait_for(
            async_client.chat.completions.create(
                model=settings.openai_chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Answer strictly from executed Neo4j rows. "
                            "Do not invent values, entities, relationships, or totals. "
                            "Use every section only if it is supported by the rows. "
                            "If the question asks for both counts and lists, include both only when rows support both. "
                            "When rows contain incident_number values, cite incident-backed claims inline as [INCxxxxxxx]. "
                            "Do not cite incident numbers that are not present in the rows."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question:\n{question}\n\n"
                            f"Executed sections:\n{sections_json}\n\n"
                            "Respond in under 180 words. No preamble."
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=420,
            ),
            timeout=12.0,
        )
        text = str(completion.choices[0].message.content or "").strip()
        return text or self._fallback_sections_answer(question, execution_results)

    async def synthesize(
        self,
        *,
        question: str,
        execution_result: GraphRAGv2ExecutionResult,
    ) -> str:
        rows_json = json.dumps(execution_result.rows[:20], ensure_ascii=True, default=str, indent=2)
        async_client = get_async_client()
        completion = await asyncio.wait_for(
            async_client.chat.completions.create(
                model=settings.openai_chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Answer strictly from executed Neo4j rows. "
                            "Do not invent values, entities, relationships, or totals. "
                            "If the rows only support a count, answer with the count. "
                            "If the rows support a list, keep the answer concise and grounded. "
                            "When rows contain incident_number values, cite incident-backed claims inline as [INCxxxxxxx]. "
                            "Do not cite incident numbers that are not present in the rows."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question:\n{question}\n\n"
                            f"Executed Cypher:\n{execution_result.cypher_query}\n\n"
                            f"Rows:\n{rows_json}\n\n"
                            "Respond in under 140 words. No preamble."
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=300,
            ),
            timeout=10.0,
        )
        text = str(completion.choices[0].message.content or "").strip()
        return text or self._fallback_answer(question, execution_result.rows)

    def fallback_answer(self, question: str, rows: List[Dict[str, Any]]) -> str:
        return self._fallback_answer(question, rows)

    def fallback_sections_answer(
        self,
        question: str,
        execution_results: List[GraphRAGv2ExecutionResult],
    ) -> str:
        return self._fallback_sections_answer(question, execution_results)

    def _fallback_answer(self, question: str, rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "No matching data found in the graph for this question."
        if len(rows) == 1:
            row = rows[0]
            if "count" in row:
                return f"Count: {int(row.get('count') or 0)}"
            if "request_count" in row:
                return f"Request count: {int(row.get('request_count') or 0)}"
        lines = []
        for row in rows[:8]:
            display = []
            for key in (
                "incident_number",
                "name",
                "number",
                "short_description",
                "description",
                "state",
                "opened_at",
                "count",
                "request_count",
            ):
                value = row.get(key)
                if value in (None, "", []):
                    continue
                display.append(f"{key}={value}")
            if display:
                lines.append("- " + ", ".join(display))
        if lines:
            return "\n".join(lines)
        return "\n".join("- " + self._format_row(row) for row in rows[:8])

    def _fallback_sections_answer(
        self,
        question: str,
        execution_results: List[GraphRAGv2ExecutionResult],
    ) -> str:
        if not execution_results:
            return "No matching data found in the graph for this question."

        lines: List[str] = []
        for execution in execution_results:
            rows = execution.rows or []
            if execution.query_shape == "count" and rows:
                row = rows[0]
                count_key = next(
                    (key for key, value in row.items() if isinstance(value, (int, float)) and "count" in key.lower()),
                    None,
                )
                if count_key:
                    lines.append(f"{execution.purpose or 'Count'}: {int(row.get(count_key) or 0)}")
                    continue
            if execution.query_shape == "list" and rows:
                row_lines: List[str] = []
                for row in rows[:8]:
                    parts = []
                    for key in (
                        "incident_number",
                        "user_name",
                        "name",
                        "ticket_number",
                        "number",
                        "catalog_item_name",
                        "short_description",
                        "description",
                        "opened_at",
                        "state",
                    ):
                        value = row.get(key)
                        if value in (None, "", []):
                            continue
                        parts.append(str(value))
                    if parts:
                        row_lines.append("- " + " | ".join(parts))
                if row_lines:
                    lines.append(f"{execution.purpose or 'Matching rows'}:")
                    lines.extend(row_lines)
                    continue
            if rows:
                lines.append(f"{execution.purpose or execution.candidate_id}:")
                for row in rows[:6]:
                    lines.append("- " + self._format_row(row))
        return "\n".join(lines) if lines else "No matching data found in the graph for this question."

    def _format_row(self, row: Dict[str, Any]) -> str:
        parts: List[str] = []
        for key, value in row.items():
            if value in (None, "", []):
                continue
            parts.append(f"{key}={self._format_value(value)}")
            if len(parts) >= 8:
                break
        return ", ".join(parts) if parts else "empty row"

    def _format_value(self, value: Any) -> str:
        if isinstance(value, list):
            formatted = [self._format_value(item) for item in value[:5] if item not in (None, "", [])]
            suffix = "" if len(value) <= 5 else f" +{len(value) - 5} more"
            return "[" + "; ".join(formatted) + suffix + "]"
        if isinstance(value, dict):
            items = [
                f"{key}: {self._format_value(item)}"
                for key, item in list(value.items())[:4]
                if item not in (None, "", [])
            ]
            return "{" + ", ".join(items) + "}"
        return str(value)

