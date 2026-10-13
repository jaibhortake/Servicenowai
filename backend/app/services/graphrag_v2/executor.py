from __future__ import annotations

import asyncio
import time
from typing import List

from app.services.neo4j_query_runtime import neo4j_query_runtime_service

from .types import GraphRAGv2CypherCandidate, GraphRAGv2ExecutionResult


class GraphRAGv2Executor:
    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = max(2.0, float(timeout_seconds or 8.0))

    async def execute_candidates(
        self,
        candidates: List[GraphRAGv2CypherCandidate],
    ) -> List[GraphRAGv2ExecutionResult]:
        results: List[GraphRAGv2ExecutionResult] = []
        for candidate in candidates:
            results.append(await self.execute_candidate(candidate))
        return results

    async def execute_candidate(self, candidate: GraphRAGv2CypherCandidate) -> GraphRAGv2ExecutionResult:
        started = time.perf_counter()
        try:
            execution = await asyncio.wait_for(
                asyncio.to_thread(
                    neo4j_query_runtime_service.execute_readonly_query,
                    candidate.cypher_query,
                    candidate.parameters,
                    20,
                    self.timeout_seconds,
                ),
                timeout=self.timeout_seconds + 1.0,
            )
            rows = list(execution.get("rows", []) or [])
            return GraphRAGv2ExecutionResult(
                candidate_id=candidate.candidate_id,
                query_shape=candidate.query_shape,
                purpose=candidate.purpose,
                cypher_query=candidate.cypher_query,
                parameters=candidate.parameters,
                rows=rows,
                row_count=len(rows),
                ok=bool(execution.get("ok")),
                error=str(execution.get("error") or ""),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except asyncio.TimeoutError:
            return GraphRAGv2ExecutionResult(
                candidate_id=candidate.candidate_id,
                query_shape=candidate.query_shape,
                purpose=candidate.purpose,
                cypher_query=candidate.cypher_query,
                parameters=candidate.parameters,
                rows=[],
                row_count=0,
                ok=False,
                error="candidate execution timed out",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
