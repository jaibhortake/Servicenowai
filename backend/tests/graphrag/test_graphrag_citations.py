import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.graphrag_citations import (  # noqa: E402
    build_servicenow_incident_url,
    build_servicenow_record_url,
    extract_incident_citations,
    extract_record_citations,
    strip_answer_citation_markers,
)


def test_extract_incident_citations_from_rows_and_sections():
    result = {
        "answer": "Use [INC0432562] and compare it with [INC0432578].",
        "evidence": {
            "rows": [
                {
                    "incident_sys_id": "abc123",
                    "incident_number": "INC0432562",
                    "short_description": "High CPU utilization",
                    "state": "2",
                    "opened_at": "2026-05-09 00:00:00",
                }
            ],
            "executed_sections": [
                {
                    "rows": [
                        {
                            "incident_sys_id": "abc123",
                            "incident_number": "INC0432562",
                        },
                        {
                            "sys_id": "def456",
                            "number": "INC0432578",
                            "short_description": "Heap space crash",
                        },
                    ]
                }
            ],
        },
    }

    citations = extract_incident_citations(
        result,
        instance_url="https://example.service-now.com/",
    )

    assert [citation["number"] for citation in citations] == ["INC0432562", "INC0432578"]
    assert citations[0]["sys_id"] == "abc123"
    assert citations[0]["url"] == "https://example.service-now.com/incident.do?sys_id=abc123"
    assert citations[1]["url"] == "https://example.service-now.com/incident.do?sys_id=def456"


def test_extract_incident_citations_filters_to_answer_markers():
    result = {
        "answer": "The final result is supported by [INC001].",
        "evidence": {
            "rows": [
                {"incident_sys_id": "one", "incident_number": "INC001"},
                {"incident_sys_id": "two", "incident_number": "INC002"},
            ]
        },
    }

    citations = extract_incident_citations(result, instance_url="https://example.service-now.com")

    assert [citation["number"] for citation in citations] == ["INC001"]


def test_extract_incident_citations_reads_nested_incident_nodes():
    result = {
        "answer": "The final result is supported by [INC001].",
        "evidence": {
            "rows": [
                {
                    "incident": {
                        "sys_id": "nested",
                        "number": "INC001",
                        "short_description": "Nested incident evidence",
                    }
                }
            ]
        },
    }

    citations = extract_incident_citations(result, instance_url="https://example.service-now.com")

    assert citations[0]["number"] == "INC001"
    assert citations[0]["sys_id"] == "nested"


def test_extract_incident_citations_dedupes_answer_markers():
    result = {
        "answer": "[INC001] and [inc001] describe the same result.",
        "evidence": {
            "rows": [
                {"incident_sys_id": "one", "incident_number": "INC001"},
            ]
        },
    }

    citations = extract_incident_citations(result, instance_url="https://example.service-now.com")

    assert [citation["number"] for citation in citations] == ["INC001"]


def test_extract_incident_citations_uses_number_links_for_answer_markers_missing_from_evidence():
    result = {
        "answer": "The answer cites [INC999], but evidence only has another incident.",
        "evidence": {
            "rows": [
                {"incident_sys_id": "one", "incident_number": "INC001"},
            ]
        },
    }

    citations = extract_incident_citations(result, instance_url="https://example.service-now.com")

    assert [citation["number"] for citation in citations] == ["INC999"]
    assert citations[0]["sys_id"] == ""
    assert citations[0]["url"] == "https://example.service-now.com/incident_list.do?sysparm_query=number%3DINC999"


def test_extract_incident_citations_falls_back_to_result_rows_without_answer_numbers():
    result = {
        "answer": "The answer mentions a memory leak without citing an incident marker.",
        "evidence": {
            "rows": [
                {"incident_sys_id": "one", "incident_number": "INC001"},
            ]
        },
    }

    citations = extract_incident_citations(result, instance_url="https://example.service-now.com")

    assert [citation["number"] for citation in citations] == ["INC001"]


def test_extract_incident_citations_excludes_grounding_source_sections_without_answer_numbers():
    result = {
        "answer": "The answer gives a recommendation without naming incidents.",
        "evidence": {
            "rows": [],
            "executed_sections": [
                {
                    "candidate_id": "grounding_expansion",
                    "purpose": "LLM grounding candidates expanded into graph evidence",
                    "rows": [
                        {"incident_sys_id": "one", "incident_number": "INC001"},
                    ],
                }
            ],
        },
    }

    citations = extract_incident_citations(result, instance_url="https://example.service-now.com")

    assert citations == []


def test_extract_incident_citations_uses_result_sections_without_answer_numbers():
    result = {
        "answer": "The answer gives a recommendation without naming incidents.",
        "evidence": {
            "rows": [],
            "executed_sections": [
                {
                    "candidate_id": "c1",
                    "purpose": "Find matching incidents and recommendations",
                    "rows": [
                        {"incident_sys_id": "one", "incident_number": "INC001"},
                    ],
                }
            ],
        },
    }

    citations = extract_incident_citations(result, instance_url="https://example.service-now.com")

    assert [citation["number"] for citation in citations] == ["INC001"]


def test_extract_incident_citations_uses_answer_marker_order():
    result = {
        "answer": "Compare [INC002] first, then [INC001].",
        "evidence": {
            "rows": [
                {"incident_sys_id": "one", "incident_number": "INC001"},
                {"incident_sys_id": "two", "incident_number": "INC002"},
            ]
        },
    }

    citations = extract_incident_citations(result, instance_url="https://example.service-now.com")

    assert [citation["number"] for citation in citations] == ["INC002", "INC001"]


def test_build_servicenow_incident_url_falls_back_to_number_query():
    url = build_servicenow_incident_url(
        "https://example.service-now.com/",
        number="INC0432562",
    )

    assert url == "https://example.service-now.com/incident_list.do?sysparm_query=number%3DINC0432562"


def test_strip_answer_citation_markers_removes_inline_incident_markers_without_cards():
    answer = "The memory leak affected the appliance [INC001]."

    cleaned = strip_answer_citation_markers(answer)

    assert cleaned == "The memory leak affected the appliance."


def test_extract_record_citations_core_records_adds_non_incident_cards():
    result = {
        "answer": "The same issue appears on Nginx Load Balancer and RITM0010001.",
        "evidence": {
            "rows": [
                {
                    "label": "Appliance",
                    "sys_id": "ci-1",
                    "name": "Nginx Load Balancer",
                    "sys_class_name": "cmdb_ci_nginx_web_server",
                },
                {
                    "problem_sys_id": "problem-1",
                    "problem_short_description": "SSL Wildcard Certificate Mass Expiry",
                },
                {
                    "request_item_sys_id": "ritm-1",
                    "request_item_number": "RITM0010001",
                    "request_item_short_description": "Certificate renewal",
                },
            ]
        },
    }

    citations = extract_record_citations(
        result,
        instance_url="https://example.service-now.com",
        scope="core_records",
    )

    assert [citation["type"] for citation in citations] == ["appliance", "request_item"]
    assert citations[0]["url"] == "https://example.service-now.com/cmdb_ci_nginx_web_server.do?sys_id=ci-1"
    assert citations[1]["url"] == "https://example.service-now.com/sc_req_item.do?sys_id=ritm-1"


def test_extract_record_citations_core_records_excludes_unmentioned_and_problem_cards():
    result = {
        "answer": "The final answer only names Apache Web Server WEB-02.",
        "evidence": {
            "rows": [
                {
                    "label": "Appliance",
                    "sys_id": "ci-1",
                    "name": "Apache Web Server WEB-02",
                    "sys_class_name": "cmdb_ci_web_server",
                },
                {
                    "label": "Appliance",
                    "sys_id": "ci-2",
                    "name": "F5 BIG-IP 2200s",
                    "sys_class_name": "cmdb_ci_hardware",
                },
                {
                    "problem_sys_id": "problem-1",
                    "problem_short_description": "SSL Wildcard Certificate Mass Expiry",
                },
            ]
        },
    }

    citations = extract_record_citations(
        result,
        instance_url="https://example.service-now.com",
        scope="core_records",
    )

    assert [citation["name"] for citation in citations] == ["Apache Web Server WEB-02"]


def test_extract_record_citations_incidents_scope_preserves_incident_only_behavior():
    result = {
        "answer": "The same issue appears on other appliances.",
        "evidence": {
            "rows": [
                {"incident_sys_id": "one", "incident_number": "INC001"},
                {
                    "label": "Appliance",
                    "sys_id": "ci-1",
                    "name": "Nginx Load Balancer",
                    "sys_class_name": "cmdb_ci_nginx_web_server",
                },
            ]
        },
    }

    citations = extract_record_citations(
        result,
        instance_url="https://example.service-now.com",
        scope="incidents",
    )

    assert [citation["type"] for citation in citations] == ["incident"]


def test_extract_record_citations_keeps_answer_marker_order_for_core_tickets():
    result = {
        "answer": "Compare [CHG002] with [REQ001].",
        "evidence": {
            "rows": [
                {"request_sys_id": "req-1", "request_number": "REQ001"},
                {"change_sys_id": "chg-2", "change_number": "CHG002"},
            ]
        },
    }

    citations = extract_record_citations(
        result,
        instance_url="https://example.service-now.com",
        scope="core_records",
    )

    assert [citation["number"] for citation in citations[:2]] == ["CHG002", "REQ001"]
    assert citations[0]["url"] == "https://example.service-now.com/change_request.do?sys_id=chg-2"
    assert citations[1]["url"] == "https://example.service-now.com/sc_request.do?sys_id=req-1"


def test_build_servicenow_record_url_uses_name_query_without_sys_id_or_number():
    url = build_servicenow_record_url(
        "https://example.service-now.com/",
        table="cmdb_ci",
        name="Nginx Load Balancer",
    )

    assert url == "https://example.service-now.com/cmdb_ci_list.do?sysparm_query=nameLIKENginx%20Load%20Balancer"
