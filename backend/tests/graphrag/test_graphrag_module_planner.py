import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.graphrag_modules import GraphModulePlanner, get_graphrag_module_registry  # noqa: E402
from app.services.graphrag_service import GraphRAGEntities, graphrag_service  # noqa: E402


def _structured_breakdown(query: str):
    plan = graphrag_service._heuristic_plan(query, GraphRAGEntities())
    heuristic = graphrag_service._heuristic_ticket_query_breakdown(query, plan)
    breakdown = graphrag_service._normalize_ticket_query_breakdown(query, plan, heuristic)
    output_plan = graphrag_service._heuristic_ticket_output_plan(query, breakdown)
    return breakdown, output_plan


def test_module_registry_exposes_servicenow_business_modules():
    registry = get_graphrag_module_registry()

    assert set(registry) == {
        "users",
        "catalog",
        "service_requests",
        "request_items",
        "change_requests",
    }


def test_module_planner_builds_structured_request_breakdown_plan():
    planner = GraphModulePlanner()
    breakdown, output_plan = _structured_breakdown(
        "how many requests did jimmie make last month and how many of them were hardware and what requests were they"
    )

    module_plan = planner.validate_plan(
        planner.build_structured_ticket_plan(
            breakdown=breakdown,
            output_plan=output_plan,
        )
    )

    assert module_plan.route == "exact_graph_query"
    assert module_plan.module_path == ["users", "service_requests", "request_items", "catalog"]
    assert module_plan.query_family == "requester_metric_breakdown"
    assert module_plan.template_key == "requester_metric_breakdown"
    assert [section.section_type for section in module_plan.output_sections] == [
        "count_metric",
        "count_metric",
        "list_scope",
    ]
    assert module_plan.time_window["label"] == "last month"
    assert "cypher_query" not in module_plan.model_dump()


def test_module_planner_builds_change_request_exact_plan():
    planner = GraphModulePlanner()
    exact_business_query = graphrag_service._parse_exact_business_query(
        "how many change requests did david loo make and how many are closed"
    )

    assert exact_business_query is not None

    module_plan = planner.validate_plan(
        planner.build_exact_business_plan(
            exact_business_query=exact_business_query,
            time_window=graphrag_service._time_window_payload(exact_business_query.time_window),
        )
    )

    assert module_plan.module_path == ["users", "change_requests"]
    assert module_plan.template_key == "requester_change_state_breakdown"
    assert module_plan.output_sections[0].section_type == "count_metric"


def test_route_classifier_reason_mentions_module_path_for_subgraph_query():
    decision = graphrag_service._classify_graphrag_route("which users bought iphone 13 pro")

    assert decision.route == "subgraph_reasoning_query"
    assert "catalog -> request_items -> service_requests -> users" in decision.reason


def test_route_classifier_reason_mentions_module_path_for_structured_exact_query():
    decision = graphrag_service._classify_graphrag_route(
        "how many requests did jimmie make last month and how many of them were hardware and what requests were they"
    )

    assert decision.route == "exact_graph_query"
    assert "users -> service_requests -> request_items -> catalog" in decision.reason


def test_compose_route_plan_payload_exposes_module_adapter_plan():
    exact_business_query = graphrag_service._parse_exact_business_query(
        "how many requests did lucius bagnoli make"
    )

    assert exact_business_query is not None

    module_plan = graphrag_service._build_module_plan_for_exact_business(exact_business_query)
    payload = graphrag_service._compose_route_plan_payload(
        retrieval_plan={"domain": "ticket", "intent_family": "count"},
        module_plan=module_plan,
    )

    assert payload["retrieval_plan"]["domain"] == "ticket"
    assert payload["module_adapter_plan"]["module_path"] == ["users", "service_requests"]
    assert payload["module_adapter_plan"]["template_key"] == "requester_measure_count"
