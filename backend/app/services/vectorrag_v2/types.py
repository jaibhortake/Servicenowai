from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class VectorRAGSolutionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    name: str
    display_name: str
    content: str
    source_file: str
    source_index: int
    score: float = 0.0


class VectorRAGQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    sources: List[VectorRAGSolutionDocument] = Field(default_factory=list)
    fallback_reason: str = ""


class VectorRAGIndexResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    reason: str = ""
    indexed_count: int = 0
    deleted_count: int = 0
    source_file: str = ""
    corpus_hash: str = ""
    embedding_model: str = ""


class VectorRAGStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    ready: bool = False
    source_file: str = ""
    embedding_model: str = ""
    top_k: int = 5
    similarity_threshold: float = 0.72
    corpus_hash: str = ""
    doc_count: int = 0
    last_indexed_at: str = ""
    index_state: str = ""
    status_error: Optional[str] = None
