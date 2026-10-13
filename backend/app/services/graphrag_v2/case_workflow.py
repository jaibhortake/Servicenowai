from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase, Query
from neo4j.exceptions import Neo4jError
from pydantic import BaseModel, Field

from app.config import Neo4jConnectionConfig, settings
from app.models.ticket import CreateIncidentRequest, TicketResponse
from app.services import ticket_service

from .service import graphrag_v2_service
from .types import GraphRAGv2SchemaSnapshot, GraphRAGv2SemanticProfile

logger = logging.getLogger(__name__)


_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "need",
    "want", "what", "are", "were", "was", "all", "any", "show", "give",
    "tell", "about", "between", "through", "during", "based", "linked",
    "common", "potential", "case", "create", "issue", "problem", "not",
    "only", "after", "before", "into", "than", "then", "when", "where",
}


class CaseOption(BaseModel):
    value: str
    label: str


class CaseQuestion(BaseModel):
    question_id: str
    question_text: str
    question_type: str = "single_choice"
    options: List[CaseOption] = Field(default_factory=list)
    source_check: str = ""
    help_text: str = ""


class CaseIssueCandidate(BaseModel):
    issue_id: str
    label: str
    description: str = ""
    score: float = 0.0


class CaseAnswerRecord(BaseModel):
    answer_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question_id: str
    question_text: str = ""
    answer_value: str = ""
    answer_label: str = ""
    skipped: bool = False
    answered_at: str = Field(default_factory=lambda: _now_iso())


class CaseCause(BaseModel):
    cause_id: str
    name: str
    severity: str = ""
    diagnostic_checks: List[str] = Field(default_factory=list)
    resolutions: List[str] = Field(default_factory=list)
    runbooks: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    score: float = 0.0
    evidence: List[str] = Field(default_factory=list)


class CaseResolutionRecommendation(BaseModel):
    title: str = ""
    action_type: str = ""
    summary: str = ""
    steps: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)


class CaseProgress(BaseModel):
    answered: int = 0
    total: int = 0
    percent: int = 0
    estimated_minutes: int = 0


class CaseWorkflowLogEntry(BaseModel):
    label: str
    detail: str = ""
    timestamp: str = Field(default_factory=lambda: _now_iso())


class CaseCreatedTicket(BaseModel):
    sys_id: str = ""
    number: str = ""
    short_description: str = ""
    state: str = ""
    opened_at: Optional[str] = None
    assigned_to: Optional[str] = None
    ticket_type: str = "INC"
    raw: Dict[str, Any] = Field(default_factory=dict)


class CaseWorkflowState(BaseModel):
    session_id: str
    status: str = "In Progress"
    step: str = "describe_issue"
    case_number: str = ""
    initial_query: str = ""
    selected_issue: Optional[CaseIssueCandidate] = None
    issue_candidates: List[CaseIssueCandidate] = Field(default_factory=list)
    current_question: Optional[CaseQuestion] = None
    answered_questions: List[CaseAnswerRecord] = Field(default_factory=list)
    possible_causes: List[CaseCause] = Field(default_factory=list)
    recommended_resolution: Optional[CaseResolutionRecommendation] = None
    case_summary: Dict[str, Any] = Field(default_factory=dict)
    progress: CaseProgress = Field(default_factory=CaseProgress)
    workflow_log: List[CaseWorkflowLogEntry] = Field(default_factory=list)
    created_ticket: Optional[CaseCreatedTicket] = None
    qa_available: bool = True
    message: str = ""
    created_at: str = Field(default_factory=lambda: _now_iso())
    updated_at: str = Field(default_factory=lambda: _now_iso())


class CaseWorkflowUnavailable(RuntimeError):
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


def _normalize_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return normalized.strip("_") or "item"


def _humanize(value: str) -> str:
    text = re.sub(r"[_\-]+", " ", str(value or "").strip())
    return re.sub(r"\s+", " ", text).strip().capitalize()


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


class Neo4jCaseWorkflowStore:
    def __init__(self, connection_config: Neo4jConnectionConfig | None = None) -> None:
        self._config = connection_config or settings.graphrag_neo4j_config()
        self._database = self._config.database
        self._driver = None

    def startup(self) -> None:
        if not self._config.enabled:
            logger.info("%s case workflow store is disabled", self._config.name)
            return
        if self._driver is not None:
            return
        if not self._config.is_complete:
            logger.warning("%s case workflow store settings are incomplete", self._config.name)
            return
        try:
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                connection_timeout=3.0,
                max_transaction_retry_time=2.0,
            )
            self._driver.verify_connectivity()
            if self._block_write("case workflow constraint creation"):
                logger.info("Connected to %s case workflow store in read-only mode", self._config.name)
            else:
                self._ensure_constraints()
            logger.info("Connected to %s case workflow store", self._config.name)
        except Exception:
            logger.exception("Failed to initialize %s case workflow store", self._config.name)
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
            "CREATE CONSTRAINT case_workflow_session_id IF NOT EXISTS FOR (n:CaseWorkflowSession) REQUIRE n.session_id IS UNIQUE",
            "CREATE CONSTRAINT case_workflow_answer_id IF NOT EXISTS FOR (n:CaseWorkflowAnswer) REQUIRE n.answer_id IS UNIQUE",
        ]
        with self._driver.session(database=self._database) as session:
            for statement in statements:
                session.run(statement).consume()

    def save_state(self, state: CaseWorkflowState, error_details: Optional[Dict[str, str]] = None) -> bool:
        if self._block_write("case workflow state save"):
            if error_details is not None:
                error_details["message"] = self._config.write_protection_reason
            return False
        if self._driver is None:
            if error_details is not None:
                error_details["message"] = "Neo4j workflow store unavailable"
            return False

        payload = self._state_payload(state)
        answers = [self._answer_payload(answer) for answer in state.answered_questions]
        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(self._upsert_session, payload)
                session.execute_write(self._replace_answers, state.session_id, answers)
            return True
        except Neo4jError as exc:
            if error_details is not None:
                error_details["message"] = str(exc)
            logger.exception("Failed to save case workflow session %s", state.session_id)
            return False

    def read_state(self, session_id: str) -> Optional[CaseWorkflowState]:
        if self._driver is None:
            self.startup()
        if self._driver is None:
            return None
        try:
            with self._driver.session(database=self._database) as session:
                record = session.execute_read(self._read_session, str(session_id or "").strip())
        except Neo4jError:
            logger.exception("Failed to read case workflow session %s", session_id)
            return None
        if not record:
            return None
        return self._record_to_state(record)

    def read_rows(
        self,
        cypher_query: str,
        parameters: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 8.0,
    ) -> List[Dict[str, Any]]:
        if self._driver is None:
            self.startup()
        if self._driver is None:
            return []
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(Query(cypher_query, timeout=timeout_seconds), parameters or {})
                return [dict(record) for record in result]
        except Neo4jError:
            logger.exception("Case workflow graph lookup failed")
            return []

    @staticmethod
    def _upsert_session(tx, payload: Dict[str, Any]) -> None:
        tx.run(
            """
MERGE (s:CaseWorkflowSession {session_id: $session_id})
SET s.status = $status,
    s.step = $step,
    s.case_number = $case_number,
    s.initial_query = $initial_query,
    s.selected_issue_json = $selected_issue_json,
    s.issue_candidates_json = $issue_candidates_json,
    s.current_question_json = $current_question_json,
    s.possible_causes_json = $possible_causes_json,
    s.recommended_resolution_json = $recommended_resolution_json,
    s.case_summary_json = $case_summary_json,
    s.progress_json = $progress_json,
    s.workflow_log_json = $workflow_log_json,
    s.created_ticket_json = $created_ticket_json,
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
MATCH (s:CaseWorkflowSession {session_id: $session_id})
OPTIONAL MATCH (s)-[:HAS_CASE_ANSWER]->(old:CaseWorkflowAnswer)
DETACH DELETE old
""".strip(),
            {"session_id": session_id},
        ).consume()
        if not answers:
            return
        tx.run(
            """
MATCH (s:CaseWorkflowSession {session_id: $session_id})
UNWIND $answers AS answer
MERGE (a:CaseWorkflowAnswer {answer_id: answer.answer_id})
SET a.question_id = answer.question_id,
    a.question_text = answer.question_text,
    a.answer_value = answer.answer_value,
    a.answer_label = answer.answer_label,
    a.skipped = answer.skipped,
    a.answered_at = answer.answered_at
MERGE (s)-[:HAS_CASE_ANSWER]->(a)
""".strip(),
            {"session_id": session_id, "answers": answers},
        ).consume()

    @staticmethod
    def _read_session(tx, session_id: str) -> Optional[Dict[str, Any]]:
        rows = list(tx.run(
            """
MATCH (s:CaseWorkflowSession {session_id: $session_id})
OPTIONAL MATCH (s)-[:HAS_CASE_ANSWER]->(a:CaseWorkflowAnswer)
WITH s, a ORDER BY a.answered_at ASC
RETURN properties(s) AS session, collect(properties(a)) AS answers
LIMIT 1
""".strip(),
            {"session_id": session_id},
        ))
        return dict(rows[0]) if rows else None

    def _state_payload(self, state: CaseWorkflowState) -> Dict[str, Any]:
        state.updated_at = _now_iso()
        selected_issue = state.selected_issue.model_dump(mode="json") if state.selected_issue else None
        current_question = state.current_question.model_dump(mode="json") if state.current_question else None
        recommendation = (
            state.recommended_resolution.model_dump(mode="json")
            if state.recommended_resolution
            else None
        )
        created_ticket = state.created_ticket.model_dump(mode="json") if state.created_ticket else None
        return {
            "session_id": state.session_id,
            "status": state.status,
            "step": state.step,
            "case_number": state.case_number,
            "initial_query": state.initial_query,
            "selected_issue_json": _json_dump(selected_issue),
            "issue_candidates_json": _json_dump([item.model_dump(mode="json") for item in state.issue_candidates]),
            "current_question_json": _json_dump(current_question),
            "possible_causes_json": _json_dump([item.model_dump(mode="json") for item in state.possible_causes]),
            "recommended_resolution_json": _json_dump(recommendation),
            "case_summary_json": _json_dump(state.case_summary),
            "progress_json": _json_dump(state.progress.model_dump(mode="json")),
            "workflow_log_json": _json_dump([item.model_dump(mode="json") for item in state.workflow_log]),
            "created_ticket_json": _json_dump(created_ticket),
            "qa_available": bool(state.qa_available),
            "message": state.message,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }

    @staticmethod
    def _answer_payload(answer: CaseAnswerRecord) -> Dict[str, Any]:
        return {
            "answer_id": answer.answer_id,
            "question_id": answer.question_id,
            "question_text": answer.question_text,
            "answer_value": answer.answer_value,
            "answer_label": answer.answer_label,
            "skipped": bool(answer.skipped),
            "answered_at": answer.answered_at,
        }

    def _record_to_state(self, record: Dict[str, Any]) -> CaseWorkflowState:
        session = dict(record.get("session") or {})
        answers = [
            answer
            for answer in (record.get("answers") or [])
            if isinstance(answer, dict) and answer.get("answer_id")
        ]
        selected_issue = _json_load(session.get("selected_issue_json"), None)
        current_question = _json_load(session.get("current_question_json"), None)
        recommendation = _json_load(session.get("recommended_resolution_json"), None)
        created_ticket = _json_load(session.get("created_ticket_json"), None)
        return CaseWorkflowState(
            session_id=str(session.get("session_id") or ""),
            status=str(session.get("status") or "In Progress"),
            step=str(session.get("step") or "describe_issue"),
            case_number=str(session.get("case_number") or ""),
            initial_query=str(session.get("initial_query") or ""),
            selected_issue=CaseIssueCandidate.model_validate(selected_issue) if selected_issue else None,
            issue_candidates=[
                CaseIssueCandidate.model_validate(item)
                for item in _json_load(session.get("issue_candidates_json"), [])
                if isinstance(item, dict)
            ],
            current_question=CaseQuestion.model_validate(current_question) if current_question else None,
            answered_questions=[
                CaseAnswerRecord.model_validate(answer)
                for answer in answers
                if isinstance(answer, dict)
            ],
            possible_causes=[
                CaseCause.model_validate(item)
                for item in _json_load(session.get("possible_causes_json"), [])
                if isinstance(item, dict)
            ],
            recommended_resolution=(
                CaseResolutionRecommendation.model_validate(recommendation)
                if recommendation
                else None
            ),
            case_summary=_json_load(session.get("case_summary_json"), {}),
            progress=CaseProgress.model_validate(_json_load(session.get("progress_json"), {})),
            workflow_log=[
                CaseWorkflowLogEntry.model_validate(item)
                for item in _json_load(session.get("workflow_log_json"), [])
                if isinstance(item, dict)
            ],
            created_ticket=CaseCreatedTicket.model_validate(created_ticket) if created_ticket else None,
            qa_available=bool(session.get("qa_available", True)),
            message=str(session.get("message") or ""),
            created_at=str(session.get("created_at") or _now_iso()),
            updated_at=str(session.get("updated_at") or _now_iso()),
        )


class GraphRAGCaseWorkflowService:
    def __init__(self, store: Neo4jCaseWorkflowStore | None = None) -> None:
        self.store = store or Neo4jCaseWorkflowStore(settings.graphrag_neo4j_config())

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
    ) -> CaseWorkflowState:
        self._require_writable_store()
        normalized_query = _safe_text(query)
        if not normalized_query:
            raise ValueError("Case description is required")

        session_id = str(uuid.uuid4())
        snapshot, profile = self._snapshot_and_profile()
        rows = self._case_rows(normalized_query, snapshot, profile)
        candidates = self._issue_candidates(normalized_query, rows)
        selected_issue = candidates[0] if candidates else None

        if not rows or not selected_issue:
            state = CaseWorkflowState(
                session_id=session_id,
                status="In Progress",
                step="describe_issue",
                initial_query=normalized_query,
                issue_candidates=candidates,
                selected_issue=selected_issue,
                progress=CaseProgress(),
                case_summary=self._case_summary(
                    initial_query=normalized_query,
                    selected_issue=selected_issue,
                    causes=[],
                    answers=[],
                    user_email=user_email,
                    user_sys_id=user_sys_id,
                ),
                workflow_log=[
                    CaseWorkflowLogEntry(label="Issue Description", detail=normalized_query),
                    CaseWorkflowLogEntry(
                        label="Guided Case Unavailable",
                        detail="The connected graph does not expose enough issue, cause, and condition structure.",
                    ),
                ],
                qa_available=True,
                message="Guided case creation is unavailable for this graph. Use Query Panel for a free-form GraphRAG answer.",
            )
            self._persist_or_raise(state)
            return state

        focused_rows = self._filter_rows_for_issue(rows, selected_issue)
        causes = self._possible_causes(focused_rows, [])
        questions = self._questions_from_rows(focused_rows, causes)
        state = CaseWorkflowState(
            session_id=session_id,
            status="In Progress",
            step="answer_questions" if questions else "review",
            initial_query=normalized_query,
            selected_issue=selected_issue,
            issue_candidates=candidates,
            current_question=questions[0] if questions else None,
            possible_causes=causes,
            recommended_resolution=self._recommend_resolution(causes),
            case_summary=self._case_summary(
                initial_query=normalized_query,
                selected_issue=selected_issue,
                causes=causes,
                answers=[],
                user_email=user_email,
                user_sys_id=user_sys_id,
            ),
            progress=self._progress([], questions),
            workflow_log=[
                CaseWorkflowLogEntry(label="Issue Description", detail=normalized_query),
                CaseWorkflowLogEntry(label="Issue Matched", detail=selected_issue.label),
            ],
            qa_available=True,
            message=(
                "Answer the graph-generated questions to narrow the root cause."
                if questions
                else "Review the graph-backed cause and recommendation."
            ),
        )
        self._persist_or_raise(state)
        return state

    def get(self, session_id: str) -> Optional[CaseWorkflowState]:
        return self.store.read_state(str(session_id or "").strip())

    async def answer(
        self,
        *,
        session_id: str,
        question_id: str,
        answer_value: str = "",
        answer_label: str = "",
        skipped: bool = False,
    ) -> CaseWorkflowState:
        state = self._load_or_raise(session_id)
        if state.status not in {"In Progress", "Open"}:
            return state
        question = self._question_by_id(state, question_id)
        record = CaseAnswerRecord(
            question_id=_safe_text(question_id),
            question_text=question.question_text if question else "",
            answer_value=_safe_text(answer_value),
            answer_label=_safe_text(answer_label),
            skipped=bool(skipped),
        )
        state.answered_questions = [
            answer
            for answer in state.answered_questions
            if answer.question_id != record.question_id
        ]
        state.answered_questions.append(record)
        state.workflow_log.append(
            CaseWorkflowLogEntry(
                label=f"Answered {record.question_id}",
                detail="Skipped" if record.skipped else (record.answer_label or record.answer_value),
            )
        )
        await self._refresh_state(state)
        self._persist_or_raise(state)
        return state

    async def select_issue(
        self,
        *,
        session_id: str,
        issue_id: str,
        issue_label: str = "",
    ) -> CaseWorkflowState:
        state = self._load_or_raise(session_id)
        selected = self._find_issue_candidate(state.issue_candidates, issue_id, issue_label)
        if selected is None:
            selected = CaseIssueCandidate(
                issue_id=_normalize_key(issue_label or issue_id),
                label=_safe_text(issue_label or issue_id),
                score=0.5,
            )
        state.selected_issue = selected
        state.answered_questions = []
        state.workflow_log.append(CaseWorkflowLogEntry(label="Issue Changed", detail=selected.label))
        await self._refresh_state(state)
        self._persist_or_raise(state)
        return state

    async def complete(
        self,
        *,
        session_id: str,
        user_email: str = "",
        user_sys_id: str = "",
    ) -> CaseWorkflowState:
        state = self._load_or_raise(session_id)
        if state.created_ticket:
            return state

        state.recommended_resolution = state.recommended_resolution or self._recommend_resolution(state.possible_causes)
        state.case_summary = self._case_summary(
            initial_query=state.initial_query,
            selected_issue=state.selected_issue,
            causes=state.possible_causes,
            answers=state.answered_questions,
            user_email=user_email,
            user_sys_id=user_sys_id,
        )
        incident_request = self._incident_request(state, user_sys_id=user_sys_id)
        ticket: TicketResponse = await asyncio.to_thread(ticket_service.create_incident, incident_request)
        state.created_ticket = CaseCreatedTicket.model_validate(ticket.model_dump(mode="json"))
        state.case_number = ticket.number
        state.status = "Completed"
        state.step = "case_created"
        state.current_question = None
        state.progress.percent = 100
        state.workflow_log.append(
            CaseWorkflowLogEntry(label="Case Created", detail=ticket.number or ticket.sys_id)
        )
        state.message = "Case created successfully."
        self._persist_or_raise(state)
        return state

    def cancel(self, session_id: str) -> CaseWorkflowState:
        state = self._load_or_raise(session_id)
        state.status = "Cancelled"
        state.step = "cancelled"
        state.current_question = None
        state.workflow_log.append(CaseWorkflowLogEntry(label="Case Cancelled", detail="Workflow cancelled"))
        state.message = "Case creation cancelled."
        self._persist_or_raise(state)
        return state

    async def _refresh_state(self, state: CaseWorkflowState) -> None:
        snapshot, profile = self._snapshot_and_profile()
        lookup_text = state.selected_issue.label if state.selected_issue else state.initial_query
        rows = self._case_rows(lookup_text or state.initial_query, snapshot, profile)
        focused_rows = self._filter_rows_for_issue(rows, state.selected_issue)
        causes = self._possible_causes(focused_rows, state.answered_questions)
        questions = self._questions_from_rows(focused_rows, causes)
        answered_ids = {answer.question_id for answer in state.answered_questions}
        state.possible_causes = causes
        state.recommended_resolution = self._recommend_resolution(causes)
        state.case_summary = self._case_summary(
            initial_query=state.initial_query,
            selected_issue=state.selected_issue,
            causes=causes,
            answers=state.answered_questions,
            user_email=_safe_text(state.case_summary.get("user_email")),
            user_sys_id=_safe_text(state.case_summary.get("user_sys_id")),
        )
        state.progress = self._progress(state.answered_questions, questions)
        state.current_question = next((question for question in questions if question.question_id not in answered_ids), None)
        state.step = "answer_questions" if state.current_question else "review"
        state.message = (
            "Review and confirm the case details."
            if state.step == "review"
            else "Answer the graph-generated questions to narrow the root cause."
        )

    def _require_writable_store(self) -> None:
        if not self.store.is_available():
            self.store.startup()
        if not self.store.is_available():
            raise CaseWorkflowUnavailable("Neo4j case workflow store is unavailable")
        if self.store.is_write_protected():
            raise CaseWorkflowUnavailable("Neo4j case workflow store is write-protected")

    def _persist_or_raise(self, state: CaseWorkflowState) -> None:
        error_details: Dict[str, str] = {}
        if not self.store.save_state(state, error_details=error_details):
            message = error_details.get("message") or "Case workflow persistence failed"
            raise CaseWorkflowUnavailable(message)

    def _load_or_raise(self, session_id: str) -> CaseWorkflowState:
        state = self.get(session_id)
        if state is None:
            raise KeyError("Case workflow session not found")
        return state

    def _snapshot_and_profile(self) -> tuple[GraphRAGv2SchemaSnapshot, GraphRAGv2SemanticProfile]:
        snapshot = graphrag_v2_service.schema_provider.get_snapshot()
        profile = graphrag_v2_service.graph_profile_service.get_profile(snapshot)
        return snapshot, profile

    def _case_rows(
        self,
        query: str,
        snapshot: GraphRAGv2SchemaSnapshot,
        profile: GraphRAGv2SemanticProfile,
    ) -> List[Dict[str, Any]]:
        del profile
        params = self._graph_lookup_params(query, snapshot)
        rows = self._execute_case_lookup(params)
        if rows or not params["terms"]:
            return rows
        broader_params = dict(params)
        broader_params["terms"] = []
        return self._execute_case_lookup(broader_params)

    def _graph_lookup_params(self, query: str, snapshot: GraphRAGv2SchemaSnapshot) -> Dict[str, Any]:
        labels = {node.label for node in snapshot.node_types}
        roles = graphrag_v2_service.dynamic_retriever._infer_roles(
            snapshot,
            graphrag_v2_service.graph_profile_service.get_profile(snapshot),
        )
        return {
            "terms": self._query_terms(query),
            "issue_labels": self._label_candidates(labels, roles.issue, "Issue", "IssueTaxonomy"),
            "cause_labels": self._label_candidates(labels, roles.cause, "RootCause"),
            "condition_labels": self._label_candidates(labels, roles.condition, "Condition", "ConditionDefinition"),
            "question_labels": self._label_candidates(labels, roles.question, "LLMQuestion"),
            "resolution_labels": self._label_candidates(labels, roles.resolution, "Resolution"),
            "recommendation_labels": self._label_candidates(labels, "Recommendation"),
            "limit": 80,
        }

    @staticmethod
    def _label_candidates(labels: set[str], *candidates: str) -> List[str]:
        selected: List[str] = []
        for candidate in candidates:
            if candidate and candidate in labels and candidate not in selected:
                selected.append(candidate)
        return selected or [candidate for candidate in candidates if candidate][:1]

    def _execute_case_lookup(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = """
MATCH (issue)-[:INDICATES]->(cause)
WHERE ANY(issueLabel IN labels(issue) WHERE issueLabel IN $issue_labels)
  AND ANY(causeLabel IN labels(cause) WHERE causeLabel IN $cause_labels)
  AND (
    size($terms) = 0 OR ANY(term IN $terms WHERE
      toLower(coalesce(issue.issue_id, '')) CONTAINS term OR
      toLower(coalesce(issue.name, '')) CONTAINS term OR
      toLower(coalesce(issue.subcategory, '')) CONTAINS term OR
      toLower(coalesce(issue.description, '')) CONTAINS term OR
      toLower(coalesce(issue.search_text, '')) CONTAINS term
    )
  )
OPTIONAL MATCH (cause)-[:REQUIRES_CHECK]->(condition)
WHERE condition IS NULL OR ANY(conditionLabel IN labels(condition) WHERE conditionLabel IN $condition_labels)
OPTIONAL MATCH (question)-[:TARGETS_CONDITION]->(condition)
WHERE question IS NULL OR ANY(questionLabel IN labels(question) WHERE questionLabel IN $question_labels)
OPTIONAL MATCH (cause)-[:RESOLVED_BY]->(resolution)
WHERE resolution IS NULL OR ANY(resolutionLabel IN labels(resolution) WHERE resolutionLabel IN $resolution_labels)
OPTIONAL MATCH (resolution)-[:USES_RUNBOOK]->(runbook)
OPTIONAL MATCH (recommendation)-[:FOR_CAUSE]->(cause)
WHERE recommendation IS NULL OR ANY(recommendationLabel IN labels(recommendation) WHERE recommendationLabel IN $recommendation_labels)
RETURN coalesce(issue.issue_id, issue.sys_id, issue.name, '') AS issue_id,
       coalesce(issue.subcategory, issue.name, issue.issue_id, '') AS issue,
       coalesce(issue.description, issue.search_text, '') AS issue_description,
       coalesce(cause.cause_id, cause.sys_id, cause.name, '') AS cause_id,
       coalesce(cause.name, cause.description, cause.cause_id, '') AS cause,
       coalesce(cause.severity, '') AS severity,
       collect(DISTINCT {
         condition_id: coalesce(condition.condition_id, condition.sys_id, condition.name, ''),
         name: coalesce(condition.name, condition.condition_id, ''),
         canonical_question: coalesce(condition.canonical_question, ''),
         answer_type: coalesce(condition.answer_type, ''),
         answer_options: coalesce(condition.answer_options, []),
         help_text: coalesce(condition.observation_source, condition.search_text, '')
       }) AS conditions,
       collect(DISTINCT {
         question_text: coalesce(question.question_text, question.name, ''),
         answer_type: coalesce(question.answer_type, ''),
         answer_options: coalesce(question.answer_options, []),
         reason: coalesce(question.reason_for_question, '')
       }) AS historical_questions,
       collect(DISTINCT {
         title: coalesce(resolution.title, resolution.name, ''),
         action_type: coalesce(resolution.resolution_type, ''),
         steps: coalesce(resolution.steps, []),
         description: coalesce(resolution.description, '')
       }) AS resolutions,
       collect(DISTINCT {
         title: coalesce(runbook.title, runbook.name, ''),
         summary: coalesce(runbook.summary, ''),
         owner_group: coalesce(runbook.owner_group, '')
       }) AS runbooks,
       collect(DISTINCT {
         action: coalesce(recommendation.recommended_action, ''),
         type: coalesce(recommendation.recommendation_type, ''),
         confidence: coalesce(recommendation.confidence_score, 0)
       }) AS recommendations
ORDER BY issue ASC, severity DESC, cause ASC
LIMIT $limit
""".strip()
        return self.store.read_rows(query, params, timeout_seconds=8.0)

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(query or "").lower())
        terms = [
            token
            for token in normalized.split()
            if len(token) >= 3 and token not in _STOP_WORDS
        ]
        deduped: List[str] = []
        for term in terms:
            if term not in deduped:
                deduped.append(term)
        return deduped[:8]

    def _issue_candidates(self, query: str, rows: List[Dict[str, Any]]) -> List[CaseIssueCandidate]:
        candidates: Dict[str, CaseIssueCandidate] = {}
        for row in rows:
            label = _safe_text(row.get("issue"))
            if not label:
                continue
            issue_id = _safe_text(row.get("issue_id")) or _normalize_key(label)
            key = _normalize_key(issue_id or label)
            score = self._text_match_score(query, label, row.get("issue_description", ""))
            existing = candidates.get(key)
            if existing:
                existing.score = max(existing.score, score)
                continue
            candidates[key] = CaseIssueCandidate(
                issue_id=issue_id,
                label=label,
                description=_safe_text(row.get("issue_description")),
                score=score,
            )
        return sorted(candidates.values(), key=lambda item: (-item.score, item.label))[:8]

    @staticmethod
    def _filter_rows_for_issue(
        rows: List[Dict[str, Any]],
        selected_issue: Optional[CaseIssueCandidate],
    ) -> List[Dict[str, Any]]:
        if not selected_issue:
            return rows
        selected = {
            _safe_text(selected_issue.issue_id).lower(),
            _safe_text(selected_issue.label).lower(),
        }
        selected = {item for item in selected if item}
        filtered = []
        for row in rows:
            row_terms = {
                _safe_text(row.get("issue_id")).lower(),
                _safe_text(row.get("issue")).lower(),
            }
            if selected & row_terms:
                filtered.append(row)
        return filtered or rows

    def _possible_causes(
        self,
        rows: List[Dict[str, Any]],
        answers: List[CaseAnswerRecord],
    ) -> List[CaseCause]:
        by_key: Dict[str, CaseCause] = {}
        for row in rows:
            name = _safe_text(row.get("cause"))
            if not name:
                continue
            cause_id = _safe_text(row.get("cause_id")) or _normalize_key(name)
            cause = by_key.get(cause_id)
            condition_names = [
                self._condition_label(condition)
                for condition in self._clean_dicts(row.get("conditions"))
                if self._condition_key(condition)
            ]
            resolutions = [
                _safe_text(item.get("title") or item.get("description"))
                for item in self._clean_dicts(row.get("resolutions"))
                if _safe_text(item.get("title") or item.get("description"))
            ]
            runbooks = [
                _safe_text(item.get("title"))
                for item in self._clean_dicts(row.get("runbooks"))
                if _safe_text(item.get("title"))
            ]
            recommendations = [
                _safe_text(item.get("action"))
                for item in self._clean_dicts(row.get("recommendations"))
                if _safe_text(item.get("action"))
            ]
            if cause is None:
                cause = CaseCause(
                    cause_id=cause_id,
                    name=name,
                    severity=_safe_text(row.get("severity")),
                    diagnostic_checks=self._dedupe(condition_names),
                    resolutions=self._dedupe(resolutions),
                    runbooks=self._dedupe(runbooks),
                    recommendations=self._dedupe(recommendations),
                )
                by_key[cause_id] = cause
            else:
                cause.diagnostic_checks = self._dedupe(cause.diagnostic_checks + condition_names)
                cause.resolutions = self._dedupe(cause.resolutions + resolutions)
                cause.runbooks = self._dedupe(cause.runbooks + runbooks)
                cause.recommendations = self._dedupe(cause.recommendations + recommendations)
                if not cause.severity and row.get("severity"):
                    cause.severity = _safe_text(row.get("severity"))
        for cause in by_key.values():
            cause.score = self._cause_score(cause, answers)
            cause.evidence = self._cause_evidence(cause, answers)
        return sorted(by_key.values(), key=lambda item: (-item.score, item.name))[:6]

    def _questions_from_rows(
        self,
        rows: List[Dict[str, Any]],
        causes: List[CaseCause],
    ) -> List[CaseQuestion]:
        conditions_by_key: Dict[str, Dict[str, Any]] = {}
        historical_by_condition: Dict[str, List[Dict[str, Any]]] = {}
        cause_checks = {
            _normalize_key(check)
            for cause in causes
            for check in cause.diagnostic_checks
        }
        for row in rows:
            condition_keys = []
            for condition in self._clean_dicts(row.get("conditions")):
                key = self._condition_key(condition)
                if not key:
                    continue
                normalized = _normalize_key(key)
                condition_keys.append(normalized)
                if normalized not in conditions_by_key:
                    conditions_by_key[normalized] = condition
            for question in self._clean_dicts(row.get("historical_questions")):
                if not _safe_text(question.get("question_text")):
                    continue
                for condition_key in condition_keys:
                    historical_by_condition.setdefault(condition_key, []).append(question)

        ordered_keys = [
            key
            for key in conditions_by_key
            if not cause_checks or key in cause_checks or any(key in check for check in cause_checks)
        ]
        if not ordered_keys:
            ordered_keys = list(conditions_by_key)

        questions: List[CaseQuestion] = []
        for key in ordered_keys[:4]:
            condition = conditions_by_key[key]
            historical_questions = historical_by_condition.get(key, [])
            question = self._question_from_condition(key, condition, historical_questions)
            if question:
                questions.append(question)
        if questions:
            return questions
        return [
            CaseQuestion(
                question_id="q_scope",
                source_check="scope",
                question_text="Is this affecting all systems or only specific systems?",
                question_type="single_choice",
                options=[
                    CaseOption(value="all_systems", label="All systems"),
                    CaseOption(value="specific_systems", label="Specific systems"),
                    CaseOption(value="not_sure", label="Not sure"),
                ],
            ),
            CaseQuestion(
                question_id="q_recent_change",
                source_check="recent_change",
                question_text="Did this start after a recent configuration or environment change?",
                question_type="boolean",
                options=self._boolean_options(),
            ),
        ]

    def _question_from_condition(
        self,
        key: str,
        condition: Dict[str, Any],
        historical_questions: List[Dict[str, Any]],
    ) -> Optional[CaseQuestion]:
        question_text = _safe_text(condition.get("canonical_question"))
        answer_type = _safe_text(condition.get("answer_type"))
        options = self._clean_list(condition.get("answer_options"))
        help_text = _safe_text(condition.get("help_text"))
        if not question_text and historical_questions:
            question_text = _safe_text(historical_questions[0].get("question_text"))
            answer_type = answer_type or _safe_text(historical_questions[0].get("answer_type"))
            options = options or self._clean_list(historical_questions[0].get("answer_options"))
            help_text = help_text or _safe_text(historical_questions[0].get("reason"))
        if not question_text:
            question_text = f"What is the result for {_humanize(self._condition_label(condition) or key)}?"
        question_options = self._options_from_values(options, answer_type)
        question_type = self._question_type(answer_type, question_options)
        return CaseQuestion(
            question_id=f"q_{_normalize_key(key)}",
            source_check=self._condition_label(condition) or key,
            question_text=question_text,
            question_type=question_type,
            options=question_options,
            help_text=help_text,
        )

    @staticmethod
    def _question_type(answer_type: str, options: List[CaseOption]) -> str:
        normalized = answer_type.strip().lower()
        labels = {option.label.strip().lower() for option in options}
        if normalized in {"text", "free_text", "string"}:
            return "text"
        if labels <= {"yes", "no", "not sure"} and len(labels) <= 3:
            return "boolean"
        return "single_choice"

    @staticmethod
    def _options_from_values(values: List[str], answer_type: str = "") -> List[CaseOption]:
        cleaned = [value for value in values if _safe_text(value)]
        if cleaned:
            return [
                CaseOption(value=_normalize_key(value), label=_safe_text(value))
                for value in cleaned[:6]
            ]
        if answer_type.strip().lower() in {"text", "free_text", "string"}:
            return []
        return GraphRAGCaseWorkflowService._boolean_options()

    @staticmethod
    def _boolean_options() -> List[CaseOption]:
        return [
            CaseOption(value="yes", label="Yes"),
            CaseOption(value="no", label="No"),
            CaseOption(value="not_sure", label="Not Sure"),
        ]

    def _recommend_resolution(self, causes: List[CaseCause]) -> Optional[CaseResolutionRecommendation]:
        if not causes:
            return None
        top = causes[0]
        title = (
            top.recommendations[0]
            if top.recommendations
            else top.resolutions[0]
            if top.resolutions
            else f"Investigate {top.name}"
        )
        steps = self._dedupe(top.recommendations + top.resolutions + top.runbooks)[:5]
        if not steps:
            steps = [f"Validate diagnostic checks for {top.name} before taking action."]
        return CaseResolutionRecommendation(
            title=title,
            action_type="Graph-backed recommended action",
            summary=f"The strongest graph-backed hypothesis is {top.name} with {round(top.score)}% confidence.",
            steps=steps,
            evidence=top.evidence[:5],
        )

    def _case_summary(
        self,
        *,
        initial_query: str,
        selected_issue: Optional[CaseIssueCandidate],
        causes: List[CaseCause],
        answers: List[CaseAnswerRecord],
        user_email: str = "",
        user_sys_id: str = "",
    ) -> Dict[str, Any]:
        top_cause = causes[0] if causes else None
        return {
            "issue": initial_query,
            "matched_issue": selected_issue.label if selected_issue else "",
            "likely_cause": top_cause.name if top_cause else "",
            "confidence": round(top_cause.score) if top_cause else 0,
            "answers": [
                {
                    "question": answer.question_text,
                    "answer": "Skipped" if answer.skipped else (answer.answer_label or answer.answer_value),
                }
                for answer in answers
            ],
            "recommended_action": (
                causes[0].recommendations[0]
                if causes and causes[0].recommendations
                else causes[0].resolutions[0]
                if causes and causes[0].resolutions
                else ""
            ),
            "user_email": user_email,
            "user_sys_id": user_sys_id,
        }

    def _incident_request(self, state: CaseWorkflowState, *, user_sys_id: str = "") -> CreateIncidentRequest:
        top_cause = state.possible_causes[0] if state.possible_causes else None
        severity = _safe_text(top_cause.severity).lower() if top_cause else ""
        impact = "2" if "high" in severity or "critical" in severity else "3"
        urgency = "2" if "high" in severity or "critical" in severity else "3"
        priority = "2" if impact == "2" or urgency == "2" else "3"
        short_description = state.initial_query[:160] or "Graph-backed support case"
        description = self._incident_description(state)
        caller_id = _safe_text(user_sys_id) or _safe_text(state.case_summary.get("user_sys_id"))
        return CreateIncidentRequest(
            short_description=short_description,
            description=description,
            caller_id=caller_id or None,
            impact=impact,
            urgency=urgency,
            priority=priority,
            category="inquiry",
            subcategory=_safe_text(state.selected_issue.label if state.selected_issue else "") or None,
            work_notes=description,
        )

    @staticmethod
    def _incident_description(state: CaseWorkflowState) -> str:
        lines = [
            f"Problem: {state.initial_query}",
            f"Matched issue: {state.selected_issue.label if state.selected_issue else 'Not selected'}",
        ]
        if state.possible_causes:
            lines.append("Likely causes:")
            for cause in state.possible_causes[:3]:
                lines.append(f"- {cause.name} ({round(cause.score)}% confidence)")
        if state.answered_questions:
            lines.append("Diagnostic answers:")
            for answer in state.answered_questions:
                value = "Skipped" if answer.skipped else (answer.answer_label or answer.answer_value)
                lines.append(f"- {answer.question_text}: {value}")
        if state.recommended_resolution:
            lines.append(f"Recommended action: {state.recommended_resolution.title}")
            for step in state.recommended_resolution.steps[:4]:
                lines.append(f"- {step}")
        return "\n".join(lines)

    @staticmethod
    def _progress(answers: List[CaseAnswerRecord], questions: List[CaseQuestion]) -> CaseProgress:
        total = len(questions)
        answered_ids = {answer.question_id for answer in answers}
        answered = len([question for question in questions if question.question_id in answered_ids])
        percent = int(round((answered / total) * 100)) if total else 0
        return CaseProgress(
            answered=answered,
            total=total,
            percent=percent,
            estimated_minutes=max(1, total - answered) if total else 0,
        )

    @staticmethod
    def _cause_score(cause: CaseCause, answers: List[CaseAnswerRecord]) -> float:
        score = 45.0
        severity = cause.severity.lower()
        if "critical" in severity or "high" in severity:
            score += 15
        elif "medium" in severity:
            score += 8
        if cause.recommendations:
            score += 6
        if cause.resolutions or cause.runbooks:
            score += 6
        cause_checks = {_normalize_key(check) for check in cause.diagnostic_checks}
        for answer in answers:
            if answer.skipped:
                continue
            answer_key = _normalize_key(answer.question_id.replace("q_", ""))
            source_hit = any(answer_key in check or check in answer_key for check in cause_checks)
            if not source_hit:
                continue
            value = (answer.answer_value or answer.answer_label).strip().lower()
            if value in {"no", "not_sure", "not sure", "unknown"}:
                score += 3
            else:
                score += 12
        return min(98.0, max(10.0, score))

    @staticmethod
    def _cause_evidence(cause: CaseCause, answers: List[CaseAnswerRecord]) -> List[str]:
        evidence = []
        if cause.severity:
            evidence.append(f"Graph severity: {cause.severity}")
        if cause.diagnostic_checks:
            evidence.append(f"Required checks: {', '.join(cause.diagnostic_checks[:4])}")
        for answer in answers:
            if answer.skipped:
                continue
            evidence.append(f"{answer.question_text}: {answer.answer_label or answer.answer_value}")
        if cause.runbooks:
            evidence.append(f"Runbook: {cause.runbooks[0]}")
        return evidence[:6]

    @staticmethod
    def _find_issue_candidate(
        candidates: List[CaseIssueCandidate],
        issue_id: str,
        issue_label: str = "",
    ) -> Optional[CaseIssueCandidate]:
        wanted = {_normalize_key(issue_id), _normalize_key(issue_label)}
        wanted = {item for item in wanted if item}
        for candidate in candidates:
            keys = {
                _normalize_key(candidate.issue_id),
                _normalize_key(candidate.label),
            }
            if wanted & keys:
                return candidate
        return None

    @staticmethod
    def _question_by_id(state: CaseWorkflowState, question_id: str) -> Optional[CaseQuestion]:
        if state.current_question and state.current_question.question_id == question_id:
            return state.current_question
        return None

    @staticmethod
    def _text_match_score(query: str, *values: Any) -> float:
        query_terms = set(GraphRAGCaseWorkflowService._query_terms(query))
        text = " ".join(str(value or "") for value in values).lower()
        if not query_terms:
            return 0.5
        hits = len([term for term in query_terms if term in text])
        return round(0.45 + min(0.5, hits / max(1, len(query_terms)) * 0.5), 4)

    @staticmethod
    def _condition_key(condition: Dict[str, Any]) -> str:
        return _safe_text(condition.get("condition_id") or condition.get("name"))

    @staticmethod
    def _condition_label(condition: Dict[str, Any]) -> str:
        return _safe_text(condition.get("name") or condition.get("condition_id"))

    @staticmethod
    def _clean_dicts(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict) and any(_safe_text(v) for v in item.values())]

    @staticmethod
    def _clean_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [_safe_text(item) for item in value if _safe_text(item)]
        if isinstance(value, str):
            loaded = _json_load(value, None)
            if isinstance(loaded, list):
                return [_safe_text(item) for item in loaded if _safe_text(item)]
            return [_safe_text(part) for part in re.split(r"[;,|]", value) if _safe_text(part)]
        return []

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        seen = set()
        deduped = []
        for value in values:
            cleaned = _safe_text(value)
            key = cleaned.lower()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            deduped.append(cleaned)
        return deduped


case_workflow_service = GraphRAGCaseWorkflowService()
