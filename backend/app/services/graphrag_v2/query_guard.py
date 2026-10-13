from __future__ import annotations

import logging
import re
from typing import List

from app.services.neo4j_query_runtime import neo4j_query_runtime_service

from .types import GraphRAGv2CypherCandidate, GraphRAGv2ValidationResult

logger = logging.getLogger(__name__)

_BLOCKED_PATTERNS = [
    r"\bUNION\b",
    r"\bSUBQUERY\b",
    r"\bAPOC\b",
    r"\bCALL\b",
]

_BRITTLE_TEXT_EXACT_MATCH_PATTERNS = [
    r"\{\s*(?:name|title|display_name|short_description|description|category)\s*:\s*['\"][^'\"]+['\"]\s*\}",
    r"(?:toLower\s*\(\s*)?[A-Za-z_][A-Za-z0-9_]*\.(?:name|title|display_name|short_description|description|category)\s*\)?\s*=\s*['\"][^'\"]+['\"]",
]


class GraphRAGv2QueryGuard:
    def validate_candidates(
        self,
        candidates: List[GraphRAGv2CypherCandidate],
    ) -> List[GraphRAGv2ValidationResult]:
        validations: List[GraphRAGv2ValidationResult] = []
        for candidate in candidates:
            reason = self._candidate_validation_error(candidate)
            validations.append(
                GraphRAGv2ValidationResult(
                    candidate_id=candidate.candidate_id,
                    valid=not bool(reason),
                    reason=reason or "",
                )
            )
        return validations

    def valid_candidates(
        self,
        candidates: List[GraphRAGv2CypherCandidate],
        validations: List[GraphRAGv2ValidationResult],
    ) -> List[GraphRAGv2CypherCandidate]:
        valid_ids = {validation.candidate_id for validation in validations if validation.valid}
        return [candidate for candidate in candidates if candidate.candidate_id in valid_ids]

    def _candidate_validation_error(self, candidate: GraphRAGv2CypherCandidate) -> str | None:
        cypher_query = str(candidate.cypher_query or "").strip()
        if not cypher_query:
            return "candidate query is empty"
        for pattern in _BLOCKED_PATTERNS:
            if re.search(pattern, cypher_query, flags=re.IGNORECASE):
                return f"candidate query contains blocked pattern: {pattern}"
        for pattern in _BRITTLE_TEXT_EXACT_MATCH_PATTERNS:
            if re.search(pattern, cypher_query, flags=re.IGNORECASE):
                return (
                    "candidate query uses brittle exact equality on a free-text business field; "
                    "use case-insensitive contains/token matching or graph identifiers instead"
                )
        return neo4j_query_runtime_service.validate_readonly_query(cypher_query)
