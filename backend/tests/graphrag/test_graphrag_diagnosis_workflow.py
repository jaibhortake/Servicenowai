import pytest

from app.services.graphrag_v2.diagnosis_workflow import (
    DiagnosisAnswerRecord,
    DiagnosisCause,
    DiagnosisWorkflowUnavailable,
    GraphRAGDiagnosisWorkflowService,
)


def test_diagnosis_question_maps_cartridge_replacement_check():
    question = GraphRAGDiagnosisWorkflowService._question_for_check("recent_cartridge_replacement")

    assert question.question_id == "q_recent_cartridge_replacement"
    assert question.question_type == "single_choice"
    assert [option.value for option in question.options] == [
        "less_than_3_months",
        "3_to_6_months",
        "6_to_12_months",
        "more_than_12_months",
        "not_sure",
    ]


def test_diagnosis_cause_score_uses_answers_and_resolution_evidence():
    cause = DiagnosisCause(
        cause_id="expired_cartridge",
        name="Expired Cartridge",
        severity="High",
        diagnostic_checks=["recent_cartridge_replacement"],
        resolutions=["Replace cartridge"],
    )
    answers = [
        DiagnosisAnswerRecord(
            question_id="q_recent_cartridge_replacement",
            answer_value="more_than_12_months",
            answer_label="More than 12 months",
        )
    ]

    assert GraphRAGDiagnosisWorkflowService._cause_score(cause, answers) > 80


def test_diagnosis_store_write_protection_blocks_session_start():
    class ProtectedStore:
        def startup(self):
            return None

        def shutdown(self):
            return None

        def is_available(self):
            return True

        def is_write_protected(self):
            return True

    service = GraphRAGDiagnosisWorkflowService(store=ProtectedStore())

    with pytest.raises(DiagnosisWorkflowUnavailable):
        service._require_writable_store()
