import asyncio

from app.services.graphrag_v2 import case_workflow as case_module
from app.services.graphrag_v2.case_workflow import (
    CaseWorkflowState,
    GraphRAGCaseWorkflowService,
)
from app.models.ticket import TicketResponse


GRAPH_ROWS = [
    {
        "issue_id": "ISSUE_VM_ANALYTICS",
        "issue": "VM Analytics Visibility",
        "issue_description": "Pure1 VM Analytics is not showing data.",
        "cause_id": "CAUSE_COLLECTOR_NOT_REGISTERED",
        "cause": "Collector Not Registered",
        "severity": "High",
        "conditions": [
            {
                "condition_id": "COND_COLLECTOR_REGISTERED",
                "name": "Collector Registered",
                "canonical_question": "Has the VM Analytics Collector been installed and registered?",
                "answer_type": "choice",
                "answer_options": ["Yes", "No", "Not Sure"],
                "help_text": "Collector registration confirms data ingestion.",
            }
        ],
        "historical_questions": [
            {
                "question_text": "Has the collector been installed and registered?",
                "answer_type": "choice",
                "answer_options": ["Yes", "No", "Not Sure"],
            }
        ],
        "resolutions": [
            {
                "title": "Register the VM Analytics Collector",
                "action_type": "Runbook guided action",
                "steps": ["Register the collector and verify Pure1 ingestion."],
            }
        ],
        "runbooks": [{"title": "VM Analytics Collector Registration"}],
        "recommendations": [{"action": "Register collector and validate telemetry"}],
    }
]


class MemoryStore:
    def __init__(self):
        self.states = {}

    def startup(self):
        return None

    def shutdown(self):
        return None

    def is_available(self):
        return True

    def is_write_protected(self):
        return False

    def save_state(self, state, error_details=None):
        self.states[state.session_id] = state.model_copy(deep=True)
        return True

    def read_state(self, session_id):
        state = self.states.get(session_id)
        return state.model_copy(deep=True) if state else None


class StubCaseWorkflowService(GraphRAGCaseWorkflowService):
    def _snapshot_and_profile(self):
        return None, None

    def _case_rows(self, query, snapshot, profile):
        return GRAPH_ROWS


def test_case_start_resolves_issue_and_uses_graph_question():
    service = StubCaseWorkflowService(store=MemoryStore())

    state = asyncio.run(
        service.start(
            query="VM Analytics is not showing any data in Pure1",
            user_email="agent@example.com",
            user_sys_id="user-1",
        )
    )

    assert state.selected_issue.label == "VM Analytics Visibility"
    assert state.current_question.question_text == "Has the VM Analytics Collector been installed and registered?"
    assert [option.label for option in state.current_question.options] == ["Yes", "No", "Not Sure"]


def test_case_answer_persists_and_advances_to_review():
    service = StubCaseWorkflowService(store=MemoryStore())
    state = asyncio.run(service.start(query="VM Analytics missing data"))

    state = asyncio.run(
        service.answer(
            session_id=state.session_id,
            question_id=state.current_question.question_id,
            answer_value="no",
            answer_label="No",
        )
    )

    assert state.step == "review"
    assert state.progress.answered == 1
    assert state.answered_questions[0].answer_label == "No"
    assert state.possible_causes[0].score > 60


def test_case_complete_creates_incident(monkeypatch):
    service = StubCaseWorkflowService(store=MemoryStore())
    state = asyncio.run(service.start(query="VM Analytics missing data"))
    state = asyncio.run(
        service.answer(
            session_id=state.session_id,
            question_id=state.current_question.question_id,
            answer_value="no",
            answer_label="No",
        )
    )

    def fake_create_incident(req):
        assert req.short_description == "VM Analytics missing data"
        assert "Collector Not Registered" in req.description
        return TicketResponse(
            sys_id="inc-sys-1",
            number="INC0012345",
            short_description=req.short_description,
            state="New",
            ticket_type="INC",
            raw={"number": "INC0012345"},
        )

    monkeypatch.setattr(case_module.ticket_service, "create_incident", fake_create_incident)

    completed = asyncio.run(
        service.complete(
            session_id=state.session_id,
            user_email="agent@example.com",
            user_sys_id="user-1",
        )
    )

    assert completed.step == "case_created"
    assert completed.case_number == "INC0012345"
    assert completed.created_ticket.ticket_type == "INC"


def test_case_graph_without_structure_degrades_gracefully():
    class EmptyService(StubCaseWorkflowService):
        def _case_rows(self, query, snapshot, profile):
            return []

    service = EmptyService(store=MemoryStore())

    state = asyncio.run(service.start(query="some unknown graph question"))

    assert state.current_question is None
    assert state.qa_available is True
    assert "unavailable" in state.message.lower()
