from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from pydantic import BaseModel, Field

from app.config import Neo4jConnectionConfig, settings
from app.services.neo4j_query_runtime import neo4j_query_runtime_service

from .service import graphrag_v2_service
from .types import GraphRAGv2ExecutionResult, GraphRAGv2SchemaSnapshot, GraphRAGv2SemanticProfile

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class DiagnosisOption(BaseModel):
    value: str
    label: str


class DiagnosisQuestion(BaseModel):
    question_id: str
    question_text: str
    question_type: str = "single_choice"
    options: List[DiagnosisOption] = Field(default_factory=list)
    source_check: str = ""
    help_text: str = ""


class DiagnosisIssueCandidate(BaseModel):
    issue_id: str
    label: str
    subcategory: str = ""
    description: str = ""
    score: float = 0.0


class DiagnosisAnswerRecord(BaseModel):
    answer_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question_id: str
    question_text: str = ""
    answer_value: str = ""
    answer_label: str = ""
    skipped: bool = False
    answered_at: str = Field(default_factory=lambda: _now_iso())


class DiagnosisCause(BaseModel):
    cause_id: str
    name: str
    severity: str = ""
    component: str = ""
    diagnostic_checks: List[str] = Field(default_factory=list)
    resolutions: List[str] = Field(default_factory=list)
    resolution_types: List[str] = Field(default_factory=list)
    score: float = 0.0
    evidence: List[str] = Field(default_factory=list)


class DiagnosisResolutionRecommendation(BaseModel):
    title: str = ""
    action_type: str = ""
    summary: str = ""
    steps: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)


class DiagnosisProgress(BaseModel):
    answered: int = 0
    total: int = 0
    percent: int = 0
    estimated_minutes: int = 0


class DiagnosisLogEntry(BaseModel):
    label: str
    detail: str = ""
    timestamp: str = Field(default_factory=lambda: _now_iso())


class DiagnosisState(BaseModel):
    session_id: str
    status: str = "In Progress"
    step: str = "issue_selection"
    initial_query: str = ""
    selected_issue: Optional[DiagnosisIssueCandidate] = None
    issue_candidates: List[DiagnosisIssueCandidate] = Field(default_factory=list)
    current_question: Optional[DiagnosisQuestion] = None
    answered_questions: List[DiagnosisAnswerRecord] = Field(default_factory=list)
    possible_causes: List[DiagnosisCause] = Field(default_factory=list)
    recommended_resolution: Optional[DiagnosisResolutionRecommendation] = None
    progress: DiagnosisProgress = Field(default_factory=DiagnosisProgress)
    diagnosis_log: List[DiagnosisLogEntry] = Field(default_factory=list)
    context_summary: Dict[str, Any] = Field(default_factory=dict)
    qa_available: bool = True
    message: str = ""
    created_at: str = Field(default_factory=lambda: _now_iso())
    updated_at: str = Field(default_factory=lambda: _now_iso())


class DiagnosisWorkflowUnavailable(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, default=str)


def _json_load(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _safe_identifier(value: str) -> bool:
    return bool(_IDENTIFIER_RE.fullmatch(str(value or "").strip()))


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _humanize_key(value: str) -> str:
    normalized = str(value or "").strip().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", normalized).strip().capitalize()


class Neo4jDiagnosisWorkflowStore:
    def __init__(self, connection_config: Neo4jConnectionConfig | None = None) -> None:
        self._config = connection_config or settings.graphrag_neo4j_config()
        self._database = self._config.database
        self._driver = None

    def startup(self) -> None:
        if not self._config.enabled:
            logger.info("%s diagnosis workflow store is disabled", self._config.name)
            return
        if self._driver is not None:
            return
        if not self._config.is_complete:
            logger.warning("%s diagnosis workflow store settings are incomplete", self._config.name)
            return
        try:
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                connection_timeout=3.0,
                max_transaction_retry_time=2.0,
            )
            self._driver.verify_connectivity()
            if self._block_write("diagnosis workflow constraint creation"):
                logger.info("Connected to %s diagnosis workflow store in read-only mode", self._config.name)
            else:
                self._ensure_constraints()
            logger.info("Connected to %s diagnosis workflow store", self._config.name)
        except Exception:
            logger.exception("Failed to initialize %s diagnosis workflow store", self._config.name)
            self.shutdown()

    def shutdown(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

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

    def _ensure_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT diagnosis_workflow_session_id IF NOT EXISTS FOR (n:DiagnosisWorkflowSession) REQUIRE n.session_id IS UNIQUE",
            "CREATE CONSTRAINT diagnosis_workflow_answer_id IF NOT EXISTS FOR (n:DiagnosisWorkflowAnswer) REQUIRE n.answer_id IS UNIQUE",
        ]
        with self._driver.session(database=self._database) as session:
            for statement in statements:
                session.run(statement).consume()

    def save_state(self, state: DiagnosisState, error_details: Optional[Dict[str, str]] = None) -> bool:
        if self._block_write("diagnosis workflow state save"):
            if error_details is not None:
                error_details["message"] = self._config.write_protection_reason
            return False
        if self._driver is None:
            if error_details is not None:
                error_details["message"] = "Neo4j workflow store unavailable"
            return False

        payload = self._state_payload(state)
        answer_payloads = [self._answer_payload(answer) for answer in state.answered_questions]
        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(self._upsert_session, payload)
                session.execute_write(self._replace_answers, state.session_id, answer_payloads)
            return True
        except Neo4jError as exc:
            if error_details is not None:
                error_details["message"] = str(exc)
            logger.exception("Failed to save diagnosis workflow session %s", state.session_id)
            return False

    def read_state(self, session_id: str) -> Optional[DiagnosisState]:
        if self._driver is None:
            return None
        try:
            with self._driver.session(database=self._database) as session:
                record = session.execute_read(self._read_session, str(session_id or "").strip())
        except Neo4jError:
            logger.exception("Failed to read diagnosis workflow session %s", session_id)
            return None
        if not record:
            return None
        return self._record_to_state(record)

    @staticmethod
    def _upsert_session(tx, payload: Dict[str, Any]) -> None:
        tx.run(
            """
MERGE (s:DiagnosisWorkflowSession {session_id: $session_id})
SET s.status = $status,
    s.step = $step,
    s.initial_query = $initial_query,
    s.selected_issue_json = $selected_issue_json,
    s.issue_candidates_json = $issue_candidates_json,
    s.current_question_json = $current_question_json,
    s.possible_causes_json = $possible_causes_json,
    s.recommended_resolution_json = $recommended_resolution_json,
    s.progress_json = $progress_json,
    s.diagnosis_log_json = $diagnosis_log_json,
    s.context_summary_json = $context_summary_json,
    s.qa_available = $qa_available,
    s.message = $message,
    s.created_at = coalesce(s.created_at, $created_at),
    s.updated_at = $updated_at
""".strip(),
            payload,
        ).consume()

    @staticmethod
    def _replace_answers(tx, session_id: str, answers: List[Dict[str, Any]]) -> None:
        tx.run(
            """
MATCH (s:DiagnosisWorkflowSession {session_id: $session_id})
OPTIONAL MATCH (s)-[:HAS_ANSWER]->(old:DiagnosisWorkflowAnswer)
DETACH DELETE old
""".strip(),
            {"session_id": session_id},
        ).consume()
        if not answers:
            return
        tx.run(
            """
MATCH (s:DiagnosisWorkflowSession {session_id: $session_id})
UNWIND $answers AS answer
MERGE (a:DiagnosisWorkflowAnswer {answer_id: answer.answer_id})
SET a.question_id = answer.question_id,
    a.question_text = answer.question_text,
    a.answer_value = answer.answer_value,
    a.answer_label = answer.answer_label,
    a.skipped = answer.skipped,
    a.answered_at = answer.answered_at
MERGE (s)-[:HAS_ANSWER]->(a)
""".strip(),
            {"session_id": session_id, "answers": answers},
        ).consume()

    @staticmethod
    def _read_session(tx, session_id: str) -> Optional[Dict[str, Any]]:
        result = tx.run(
            """
MATCH (s:DiagnosisWorkflowSession {session_id: $session_id})
OPTIONAL MATCH (s)-[:HAS_ANSWER]->(a:DiagnosisWorkflowAnswer)
WITH s, a ORDER BY a.answered_at ASC
RETURN properties(s) AS session, collect(properties(a)) AS answers
LIMIT 1
""".strip(),
            {"session_id": session_id},
        )
        rows = list(result)
        return dict(rows[0]) if rows else None

    def _state_payload(self, state: DiagnosisState) -> Dict[str, Any]:
        state.updated_at = _now_iso()
        selected_issue = state.selected_issue.model_dump(mode="json") if state.selected_issue else None
        current_question = state.current_question.model_dump(mode="json") if state.current_question else None
        recommended_resolution = (
            state.recommended_resolution.model_dump(mode="json") if state.recommended_resolution else None
        )
        return {
            "session_id": state.session_id,
            "status": state.status,
            "step": state.step,
            "initial_query": state.initial_query,
            "selected_issue_json": _json_dump(selected_issue),
            "issue_candidates_json": _json_dump([item.model_dump(mode="json") for item in state.issue_candidates]),
            "current_question_json": _json_dump(current_question),
            "possible_causes_json": _json_dump([item.model_dump(mode="json") for item in state.possible_causes]),
            "recommended_resolution_json": _json_dump(recommended_resolution),
            "progress_json": _json_dump(state.progress.model_dump(mode="json")),
            "diagnosis_log_json": _json_dump([item.model_dump(mode="json") for item in state.diagnosis_log]),
            "context_summary_json": _json_dump(state.context_summary),
            "qa_available": bool(state.qa_available),
            "message": state.message,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }

    @staticmethod
    def _answer_payload(answer: DiagnosisAnswerRecord) -> Dict[str, Any]:
        return {
            "answer_id": answer.answer_id,
            "question_id": answer.question_id,
            "question_text": answer.question_text,
            "answer_value": answer.answer_value,
            "answer_label": answer.answer_label,
            "skipped": bool(answer.skipped),
            "answered_at": answer.answered_at,
        }

    def _record_to_state(self, record: Dict[str, Any]) -> DiagnosisState:
        session = dict(record.get("session") or {})
        answers = [
            answer
            for answer in (record.get("answers") or [])
            if isinstance(answer, dict) and answer.get("question_id")
        ]
        selected_issue = _json_load(session.get("selected_issue_json"), None)
        current_question = _json_load(session.get("current_question_json"), None)
        recommended_resolution = _json_load(session.get("recommended_resolution_json"), None)
        return DiagnosisState(
            session_id=str(session.get("session_id") or ""),
            status=str(session.get("status") or "In Progress"),
            step=str(session.get("step") or "issue_selection"),
            initial_query=str(session.get("initial_query") or ""),
            selected_issue=DiagnosisIssueCandidate.model_validate(selected_issue) if selected_issue else None,
            issue_candidates=[
                DiagnosisIssueCandidate.model_validate(item)
                for item in _json_load(session.get("issue_candidates_json"), [])
                if isinstance(item, dict)
            ],
            current_question=DiagnosisQuestion.model_validate(current_question) if current_question else None,
            answered_questions=[DiagnosisAnswerRecord.model_validate(answer) for answer in answers],
            possible_causes=[
                DiagnosisCause.model_validate(item)
                for item in _json_load(session.get("possible_causes_json"), [])
                if isinstance(item, dict)
            ],
            recommended_resolution=(
                DiagnosisResolutionRecommendation.model_validate(recommended_resolution)
                if recommended_resolution
                else None
            ),
            progress=DiagnosisProgress.model_validate(_json_load(session.get("progress_json"), {})),
            diagnosis_log=[
                DiagnosisLogEntry.model_validate(item)
                for item in _json_load(session.get("diagnosis_log_json"), [])
                if isinstance(item, dict)
            ],
            context_summary=_json_load(session.get("context_summary_json"), {}),
            qa_available=bool(session.get("qa_available", True)),
            message=str(session.get("message") or ""),
            created_at=str(session.get("created_at") or _now_iso()),
            updated_at=str(session.get("updated_at") or _now_iso()),
        )


class GraphRAGDiagnosisWorkflowService:
    def __init__(self, store: Neo4jDiagnosisWorkflowStore | None = None) -> None:
        self.store = store or Neo4jDiagnosisWorkflowStore(settings.graphrag_neo4j_config())

    def startup(self) -> None:
        self.store.startup()

    def shutdown(self) -> None:
        self.store.shutdown()

    async def start(
        self,
        *,
        query: str,
        user_email: str = "",
        user_sys_id: str = "",
        incident_sys_id: str = "",
        product_hint: str = "",
    ) -> DiagnosisState:
        self._require_writable_store()
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("Diagnosis query is required")

        session_id = str(uuid.uuid4())
        snapshot, profile = self._snapshot_and_profile()
        rows = await self._cause_rows(normalized_query, snapshot, profile)
        issue_candidates = self._issue_candidates(normalized_query, rows, snapshot, profile)
        selected_issue = issue_candidates[0] if issue_candidates else None

        if not self._guided_diagnosis_available(snapshot, profile) or not selected_issue:
            state = DiagnosisState(
                session_id=session_id,
                status="In Progress",
                step="issue_selection",
                initial_query=normalized_query,
                issue_candidates=issue_candidates,
                selected_issue=selected_issue,
                current_question=None,
                progress=DiagnosisProgress(answered=0, total=0, percent=0, estimated_minutes=0),
                context_summary=self._context_summary(
                    selected_issue=selected_issue,
                    user_email=user_email,
                    user_sys_id=user_sys_id,
                    incident_sys_id=incident_sys_id,
                    product_hint=product_hint,
                ),
                diagnosis_log=[
                    DiagnosisLogEntry(label="Problem Stated", detail=normalized_query),
                    DiagnosisLogEntry(
                        label="Guided Diagnosis Unavailable",
                        detail="The connected graph does not expose enough issue/cause/check structure for guided diagnosis.",
                    ),
                ],
                qa_available=True,
                message="Guided diagnosis is unavailable for this graph. Use Ask GraphRAG for a grounded free-form answer.",
            )
            self._persist_or_raise(state)
            return state

        focused_rows = self._filter_rows_for_issue(rows, selected_issue)
        causes = self._possible_causes(focused_rows, [])
        questions = self._questions_from_causes(causes, selected_issue)
        current_question = questions[0] if questions else None
        state = DiagnosisState(
            session_id=session_id,
            status="In Progress",
            step="diagnosis" if current_question else "possible_cause",
            initial_query=normalized_query,
            selected_issue=selected_issue,
            issue_candidates=issue_candidates,
            current_question=current_question,
            answered_questions=[],
            possible_causes=causes,
            recommended_resolution=self._recommend_resolution(causes),
            progress=self._progress([], questions),
            context_summary=self._context_summary(
                selected_issue=selected_issue,
                user_email=user_email,
                user_sys_id=user_sys_id,
                incident_sys_id=incident_sys_id,
                product_hint=product_hint,
            ),
            diagnosis_log=[
                DiagnosisLogEntry(label="Problem Stated", detail=normalized_query),
                DiagnosisLogEntry(label="Issue Selected", detail=selected_issue.label),
            ],
            qa_available=True,
            message="Answer the diagnosis questions to narrow the possible root cause.",
        )
        self._persist_or_raise(state)
        return state

    def get(self, session_id: str) -> Optional[DiagnosisState]:
        return self.store.read_state(str(session_id or "").strip())

    async def answer(
        self,
        *,
        session_id: str,
        question_id: str,
        answer_value: str = "",
        answer_label: str = "",
        skipped: bool = False,
    ) -> DiagnosisState:
        state = self._load_or_raise(session_id)
        if state.status not in {"In Progress", "Open"}:
            return state
        question = self._question_by_id(state, question_id)
        record = DiagnosisAnswerRecord(
            question_id=str(question_id or "").strip(),
            question_text=question.question_text if question else "",
            answer_value=str(answer_value or "").strip(),
            answer_label=str(answer_label or "").strip(),
            skipped=bool(skipped),
        )
        state.answered_questions = [
            answer for answer in state.answered_questions if answer.question_id != record.question_id
        ]
        state.answered_questions.append(record)
        state.diagnosis_log.append(
            DiagnosisLogEntry(
                label=f"Answered {record.question_id}",
                detail="Skipped" if record.skipped else (record.answer_label or record.answer_value),
            )
        )
        await self._refresh_diagnosis_state(state)
        self._persist_or_raise(state)
        return state

    async def select_issue(self, *, session_id: str, issue_id: str, issue_label: str = "") -> DiagnosisState:
        state = self._load_or_raise(session_id)
        selected = self._find_issue_candidate(state.issue_candidates, issue_id, issue_label)
        if selected is None:
            selected = DiagnosisIssueCandidate(
                issue_id=_normalize_key(issue_label or issue_id),
                label=str(issue_label or issue_id).strip(),
                score=0.5,
            )
        state.selected_issue = selected
        state.answered_questions = []
        state.diagnosis_log.append(DiagnosisLogEntry(label="Issue Changed", detail=selected.label))
        await self._refresh_diagnosis_state(state)
        self._persist_or_raise(state)
        return state

    def complete(self, session_id: str) -> DiagnosisState:
        state = self._load_or_raise(session_id)
        state.status = "Completed"
        state.step = "complete"
        state.current_question = None
        state.recommended_resolution = state.recommended_resolution or self._recommend_resolution(state.possible_causes)
        state.progress.percent = 100
        state.diagnosis_log.append(DiagnosisLogEntry(label="Diagnosis Completed", detail="Recommendation generated"))
        state.message = "Diagnosis completed. Review the recommended action before updating any external record."
        self._persist_or_raise(state)
        return state

    def cancel(self, session_id: str) -> DiagnosisState:
        state = self._load_or_raise(session_id)
        state.status = "Cancelled"
        state.step = "complete"
        state.current_question = None
        state.diagnosis_log.append(DiagnosisLogEntry(label="Diagnosis Cancelled", detail="Session cancelled by user"))
        state.message = "Diagnosis cancelled."
        self._persist_or_raise(state)
        return state

    async def _refresh_diagnosis_state(self, state: DiagnosisState) -> None:
        snapshot, profile = self._snapshot_and_profile()
        selected_label = state.selected_issue.label if state.selected_issue else state.initial_query
        rows = await self._cause_rows(selected_label or state.initial_query, snapshot, profile)
        focused_rows = self._filter_rows_for_issue(rows, state.selected_issue)
        causes = self._possible_causes(focused_rows, state.answered_questions)
        questions = self._questions_from_causes(causes, state.selected_issue)
        answered_ids = {answer.question_id for answer in state.answered_questions}
        state.possible_causes = causes
        state.recommended_resolution = self._recommend_resolution(causes)
        state.progress = self._progress(state.answered_questions, questions)
        state.current_question = next((question for question in questions if question.question_id not in answered_ids), None)
        state.step = "diagnosis" if state.current_question else "possible_cause"
        state.message = (
            "Review the ranked causes and recommended action."
            if state.step == "possible_cause"
            else "Answer the diagnosis questions to narrow the possible root cause."
        )

    def _require_writable_store(self) -> None:
        if not self.store.is_available():
            self.store.startup()
        if not self.store.is_available():
            raise DiagnosisWorkflowUnavailable("Neo4j diagnosis workflow store is unavailable")
        if self.store.is_write_protected():
            raise DiagnosisWorkflowUnavailable("Neo4j diagnosis workflow store is write-protected")

    def _persist_or_raise(self, state: DiagnosisState) -> None:
        error_details: Dict[str, str] = {}
        if not self.store.save_state(state, error_details=error_details):
            message = error_details.get("message") or "Diagnosis session persistence failed"
            raise DiagnosisWorkflowUnavailable(message)

    def _load_or_raise(self, session_id: str) -> DiagnosisState:
        state = self.get(session_id)
        if state is None:
            raise KeyError("Diagnosis session not found")
        return state

    def _snapshot_and_profile(self) -> tuple[GraphRAGv2SchemaSnapshot, GraphRAGv2SemanticProfile]:
        snapshot = graphrag_v2_service.schema_provider.get_snapshot()
        profile = graphrag_v2_service.graph_profile_service.get_profile(snapshot)
        return snapshot, profile

    def _guided_diagnosis_available(
        self,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
    ) -> bool:
        roles = graphrag_v2_service.dynamic_retriever._infer_roles(snapshot, profile)
        return bool(
            roles.issue
            and roles.cause
            and graphrag_v2_service.dynamic_retriever._has_rel(snapshot, roles.issue, "INDICATES", roles.cause)
        )

    async def _cause_rows(
        self,
        query: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
    ) -> List[Dict[str, Any]]:
        lookup_question = str(query or "").strip()
        if "cause" not in lookup_question.lower() and "resolution" not in lookup_question.lower():
            lookup_question = f"what are the potential causes for {lookup_question}"
        dynamic_route = await graphrag_v2_service.dynamic_retriever.run(
            question=lookup_question,
            snapshot=snapshot,
            profile=profile,
            desired_shape="lookup",
            requested_shapes=["lookup"],
            time_window=None,
        )
        if not dynamic_route:
            return []
        executions = list(dynamic_route.get("selected_executions") or [])
        rows: List[Dict[str, Any]] = []
        for execution in executions:
            if isinstance(execution, GraphRAGv2ExecutionResult) and execution.ok:
                rows.extend(list(execution.rows or []))
        return rows

    def _issue_candidates(
        self,
        query: str,
        rows: List[Dict[str, Any]],
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
    ) -> List[DiagnosisIssueCandidate]:
        candidates: Dict[str, DiagnosisIssueCandidate] = {}
        for row in rows:
            label = str(row.get("subcategory") or row.get("issue") or "").strip()
            if not label:
                continue
            key = _normalize_key(label)
            existing = candidates.get(key)
            score = self._text_match_score(query, label, row.get("issue_description", ""))
            if existing:
                existing.score = max(existing.score, score)
                continue
            candidates[key] = DiagnosisIssueCandidate(
                issue_id=key,
                label=label,
                subcategory=str(row.get("subcategory") or ""),
                description=str(row.get("issue_description") or ""),
                score=score,
            )
        if candidates:
            return sorted(candidates.values(), key=lambda item: (-item.score, item.label))[:8]
        return self._issue_candidates_from_graph(query, snapshot, profile)

    def _issue_candidates_from_graph(
        self,
        query: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
    ) -> List[DiagnosisIssueCandidate]:
        roles = graphrag_v2_service.dynamic_retriever._infer_roles(snapshot, profile)
        if not roles.issue or not _safe_identifier(roles.issue):
            return []
        terms = graphrag_v2_service.dynamic_retriever.issue_terms(query)
        if not terms:
            return []
        result = neo4j_query_runtime_service.execute_readonly_query(
            f"""
MATCH (issue:{roles.issue})
WHERE ANY(term IN $subject_terms WHERE
    toLower(coalesce(issue.name, '')) CONTAINS term OR
    toLower(coalesce(issue.subcategory, '')) CONTAINS term OR
    toLower(coalesce(issue.description, '')) CONTAINS term
)
RETURN coalesce(issue.subcategory, issue.name) AS label,
       coalesce(issue.subcategory, '') AS subcategory,
       coalesce(issue.description, '') AS description
ORDER BY label ASC
LIMIT $limit
""".strip(),
            {"subject_terms": terms, "limit": 8},
            limit_cap=12,
            timeout_seconds=5.0,
        )
        if not result.get("ok"):
            return []
        candidates: List[DiagnosisIssueCandidate] = []
        seen = set()
        for row in result.get("rows", []) or []:
            label = str(row.get("label") or "").strip()
            key = _normalize_key(label)
            if not label or key in seen:
                continue
            seen.add(key)
            candidates.append(
                DiagnosisIssueCandidate(
                    issue_id=key,
                    label=label,
                    subcategory=str(row.get("subcategory") or ""),
                    description=str(row.get("description") or ""),
                    score=self._text_match_score(query, label, row.get("description", "")),
                )
            )
        return sorted(candidates, key=lambda item: (-item.score, item.label))[:8]

    @staticmethod
    def _filter_rows_for_issue(
        rows: List[Dict[str, Any]],
        selected_issue: Optional[DiagnosisIssueCandidate],
    ) -> List[Dict[str, Any]]:
        if not selected_issue:
            return rows
        selected_terms = {
            str(selected_issue.label or "").strip().lower(),
            str(selected_issue.subcategory or "").strip().lower(),
        }
        selected_terms = {term for term in selected_terms if term}
        if not selected_terms:
            return rows
        filtered = []
        for row in rows:
            row_terms = {
                str(row.get("subcategory") or "").strip().lower(),
                str(row.get("issue") or "").strip().lower(),
            }
            if selected_terms & row_terms:
                filtered.append(row)
        return filtered or rows

    def _possible_causes(
        self,
        rows: List[Dict[str, Any]],
        answers: List[DiagnosisAnswerRecord],
    ) -> List[DiagnosisCause]:
        by_key: Dict[str, DiagnosisCause] = {}
        for row in rows:
            name = str(row.get("potential_cause") or row.get("cause") or "").strip()
            if not name:
                continue
            key = _normalize_key(name)
            cause = by_key.get(key)
            if cause is None:
                cause = DiagnosisCause(
                    cause_id=key,
                    name=name,
                    severity=str(row.get("severity") or ""),
                    component=str(row.get("component") or ""),
                    diagnostic_checks=self._clean_list(row.get("diagnostic_checks")),
                    resolutions=self._clean_list(row.get("resolutions")),
                    resolution_types=self._clean_list(row.get("resolution_types")),
                )
                by_key[key] = cause
            else:
                cause.diagnostic_checks = self._merge_lists(cause.diagnostic_checks, self._clean_list(row.get("diagnostic_checks")))
                cause.resolutions = self._merge_lists(cause.resolutions, self._clean_list(row.get("resolutions")))
                cause.resolution_types = self._merge_lists(cause.resolution_types, self._clean_list(row.get("resolution_types")))
                if not cause.component and row.get("component"):
                    cause.component = str(row.get("component") or "")
        for cause in by_key.values():
            cause.score = self._cause_score(cause, answers)
            cause.evidence = self._cause_evidence(cause, answers)
        return sorted(by_key.values(), key=lambda item: (-item.score, item.name))[:6]

    def _questions_from_causes(
        self,
        causes: List[DiagnosisCause],
        selected_issue: Optional[DiagnosisIssueCandidate],
    ) -> List[DiagnosisQuestion]:
        checks: List[str] = []
        for cause in causes:
            checks = self._merge_lists(checks, cause.diagnostic_checks)
        questions = [self._question_for_check(check) for check in checks[:4]]
        if questions:
            return questions[:3]
        label = (selected_issue.label if selected_issue else "").lower()
        if "taste" in label or "odor" in label:
            return [
                self._question_for_check("recent_cartridge_replacement"),
                self._question_for_check("all_outlets_affected"),
                self._question_for_check("source_water_quality_change"),
            ]
        return [
            self._question_for_check("recent_cartridge_replacement"),
            self._question_for_check("issue_all_outlets"),
            self._question_for_check("visible_leak_or_scale"),
        ]

    @staticmethod
    def _question_for_check(check: str) -> DiagnosisQuestion:
        key = _normalize_key(check)
        if key in {"recent_cartridge_replacement", "cartridge_age_months"}:
            return DiagnosisQuestion(
                question_id=f"q_{key}",
                source_check=key,
                question_text="When was the cartridge last replaced?",
                question_type="single_choice",
                options=[
                    DiagnosisOption(value="less_than_3_months", label="Less than 3 months"),
                    DiagnosisOption(value="3_to_6_months", label="3 to 6 months"),
                    DiagnosisOption(value="6_to_12_months", label="6 to 12 months"),
                    DiagnosisOption(value="more_than_12_months", label="More than 12 months"),
                    DiagnosisOption(value="not_sure", label="Not sure"),
                ],
            )
        if key in {"flow_pattern"}:
            return DiagnosisQuestion(
                question_id=f"q_{key}",
                source_check=key,
                question_text="How would you describe the flow pattern?",
                question_type="single_choice",
                options=[
                    DiagnosisOption(value="constant", label="Constantly reduced"),
                    DiagnosisOption(value="intermittent", label="Intermittent"),
                    DiagnosisOption(value="peak_hours", label="Mostly during peak hours"),
                    DiagnosisOption(value="not_sure", label="Not sure"),
                ],
            )
        if key in {"leak_source"}:
            return DiagnosisQuestion(
                question_id=f"q_{key}",
                source_check=key,
                question_text="Where does the leak appear to come from?",
                question_type="single_choice",
                options=[
                    DiagnosisOption(value="filter_head", label="Filter head"),
                    DiagnosisOption(value="cartridge", label="Cartridge area"),
                    DiagnosisOption(value="inlet_outlet", label="Inlet or outlet fitting"),
                    DiagnosisOption(value="not_visible", label="No visible leak source"),
                ],
            )
        text_by_key = {
            "cartridge_model_matches": "Does the installed cartridge model match the product requirement?",
            "inlet_valve_open": "Is the inlet valve fully open?",
            "scale_observed": "Is scale buildup visible around the cartridge or outlet?",
            "peak_hour_failure": "Does the issue happen mainly during peak usage?",
            "recent_installation": "Was the product installed or reinstalled recently?",
            "all_outlets_affected": "Is the taste or odor present at all dispensing outlets?",
            "issue_all_outlets": "Is the issue present at all dispensing outlets?",
            "source_water_quality_change": "Has the source water quality changed recently?",
            "visible_leak_or_scale": "Is there visible leakage or scale buildup?",
        }
        return DiagnosisQuestion(
            question_id=f"q_{key}",
            source_check=key,
            question_text=text_by_key.get(key, f"What is the result for {_humanize_key(key)}?"),
            question_type="boolean",
            options=[
                DiagnosisOption(value="yes", label="Yes"),
                DiagnosisOption(value="no", label="No"),
                DiagnosisOption(value="not_sure", label="Not sure"),
            ],
        )

    def _recommend_resolution(self, causes: List[DiagnosisCause]) -> Optional[DiagnosisResolutionRecommendation]:
        if not causes:
            return None
        top = causes[0]
        title = top.resolutions[0] if top.resolutions else f"Investigate {top.name}"
        action_type = top.resolution_types[0] if top.resolution_types else "Recommended action"
        steps = top.resolutions[:3] or [f"Validate diagnostic checks for {top.name} before taking action."]
        return DiagnosisResolutionRecommendation(
            title=title,
            action_type=action_type,
            summary=f"The strongest graph-backed hypothesis is {top.name} with {round(top.score)}% confidence.",
            steps=steps,
            evidence=top.evidence[:4],
        )

    @staticmethod
    def _progress(
        answers: List[DiagnosisAnswerRecord],
        questions: List[DiagnosisQuestion],
    ) -> DiagnosisProgress:
        total = len(questions)
        answered_ids = {answer.question_id for answer in answers}
        answered = len([question for question in questions if question.question_id in answered_ids])
        percent = int(round((answered / total) * 100)) if total else 0
        return DiagnosisProgress(
            answered=answered,
            total=total,
            percent=percent,
            estimated_minutes=max(1, total - answered) if total else 0,
        )

    @staticmethod
    def _context_summary(
        *,
        selected_issue: Optional[DiagnosisIssueCandidate],
        user_email: str,
        user_sys_id: str,
        incident_sys_id: str,
        product_hint: str,
    ) -> Dict[str, Any]:
        return {
            "issue": selected_issue.label if selected_issue else "",
            "incident": str(incident_sys_id or "").strip(),
            "product": str(product_hint or "").strip(),
            "user_email": str(user_email or "").strip(),
            "user_sys_id": str(user_sys_id or "").strip(),
        }

    @staticmethod
    def _text_match_score(query: str, label: str, description: Any = "") -> float:
        tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
            if len(token) >= 3
        }
        haystack = f"{label} {description}".lower()
        if not tokens:
            return 0.5
        matches = sum(1 for token in tokens if token in haystack)
        bonus = 0.25 if "taste" in tokens and ("taste" in haystack or "odor" in haystack) else 0.0
        return round(min(1.0, 0.35 + (matches / max(1, len(tokens))) + bonus), 3)

    @staticmethod
    def _cause_score(cause: DiagnosisCause, answers: List[DiagnosisAnswerRecord]) -> float:
        severity_base = {
            "critical": 82.0,
            "high": 76.0,
            "medium": 62.0,
            "low": 48.0,
        }
        score = severity_base.get(cause.severity.strip().lower(), 55.0)
        source_checks = {_normalize_key(check) for check in cause.diagnostic_checks}
        for answer in answers:
            if answer.skipped:
                continue
            source = answer.question_id.removeprefix("q_")
            if source in source_checks and answer.answer_value not in {"no", "not_sure", ""}:
                score += 7.5
            elif answer.answer_value not in {"not_sure", ""}:
                score += 1.5
        if cause.resolutions:
            score += 3.0
        if cause.component:
            score += 2.0
        return round(min(98.0, score), 1)

    @staticmethod
    def _cause_evidence(cause: DiagnosisCause, answers: List[DiagnosisAnswerRecord]) -> List[str]:
        evidence = []
        if cause.severity:
            evidence.append(f"Severity: {cause.severity}")
        if cause.component:
            evidence.append(f"Linked component: {cause.component}")
        if cause.diagnostic_checks:
            evidence.append(f"Checks: {', '.join(cause.diagnostic_checks[:4])}")
        matched_answers = [
            answer.answer_label or answer.answer_value
            for answer in answers
            if answer.question_id.removeprefix("q_") in {_normalize_key(check) for check in cause.diagnostic_checks}
            and not answer.skipped
        ]
        if matched_answers:
            evidence.append(f"User answers: {', '.join(matched_answers[:3])}")
        return evidence

    @staticmethod
    def _clean_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            value = [value] if value else []
        result = []
        seen = set()
        for item in value:
            normalized = str(item or "").strip()
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            seen.add(lowered)
            result.append(normalized)
        return result

    @staticmethod
    def _merge_lists(left: List[str], right: List[str]) -> List[str]:
        result = list(left or [])
        seen = {item.lower() for item in result}
        for item in right or []:
            normalized = str(item or "").strip()
            if not normalized or normalized.lower() in seen:
                continue
            result.append(normalized)
            seen.add(normalized.lower())
        return result

    @staticmethod
    def _question_by_id(state: DiagnosisState, question_id: str) -> Optional[DiagnosisQuestion]:
        if state.current_question and state.current_question.question_id == question_id:
            return state.current_question
        for cause in state.possible_causes:
            for check in cause.diagnostic_checks:
                question = GraphRAGDiagnosisWorkflowService._question_for_check(check)
                if question.question_id == question_id:
                    return question
        return None

    @staticmethod
    def _find_issue_candidate(
        candidates: List[DiagnosisIssueCandidate],
        issue_id: str,
        issue_label: str,
    ) -> Optional[DiagnosisIssueCandidate]:
        normalized_id = _normalize_key(issue_id)
        normalized_label = str(issue_label or "").strip().lower()
        for candidate in candidates:
            if candidate.issue_id == normalized_id or candidate.label.lower() == normalized_label:
                return candidate
        return None


diagnosis_workflow_service = GraphRAGDiagnosisWorkflowService()
