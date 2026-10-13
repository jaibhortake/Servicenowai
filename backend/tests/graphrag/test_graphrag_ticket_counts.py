import sys
import asyncio
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.graphrag_service import (  # noqa: E402
    GraphRAGCypherBlueprint,
    GraphRAGCypherProgram,
    GraphRAGEntities,
    GraphRAGRetrievalPlan,
    graphrag_service,
)
from app.config import Settings  # noqa: E402
from app.services.neo4j_query_runtime import neo4j_query_runtime_service  # noqa: E402


def _structured_breakdown(query):
    plan = graphrag_service._heuristic_plan(query, GraphRAGEntities())
    heuristic = graphrag_service._heuristic_ticket_query_breakdown(query, plan)
    return graphrag_service._normalize_ticket_query_breakdown(query, plan, heuristic)


def test_ticket_count_program_separates_requester_and_segments():
    query = "how many total requests have lucius bagnoli made and how many those are hardware and how many of them are standard laptop"
    plan = graphrag_service._heuristic_plan(query, GraphRAGEntities())

    program = graphrag_service._default_graph_query_program(query, plan)
    params = graphrag_service._build_graph_query_parameters(query, plan, program)

    assert program.requester_terms == ["lucius bagnoli"]
    assert program.segment_one_label == "hardware"
    assert program.segment_two_label == "standard laptop"
    assert "requested_for_name" in program.cypher_query
    assert "segment_one_count" in program.cypher_query
    assert neo4j_query_runtime_service.validate_readonly_query(program.cypher_query) is None

    assert params["requester_terms"] == ["lucius bagnoli"]
    assert "hardware" in params["segment_one_terms"]
    assert "standard laptop" in params["segment_two_terms"]
    assert "lucius" not in params["subject_terms"]
    assert "bagnoli" not in params["subject_terms"]


def test_cypher_blueprint_accepts_filter_goal():
    blueprint = GraphRAGCypherBlueprint(goal="filter")
    assert blueprint.goal == "filter"


def test_default_cypher_blueprint_handles_filter_plan():
    plan = GraphRAGRetrievalPlan(
        domain="catalog",
        intent_family="filter",
        entity_scope="catalog_wide",
        answer_style="enumerated_list",
        category_terms=["mobile"],
        concept_terms=["mobile"],
    )

    blueprint = graphrag_service._default_cypher_blueprint(
        "which mobiles were not bought by both lucius and jimmie",
        plan,
    )

    assert blueprint.goal == "filter"
    assert neo4j_query_runtime_service.validate_readonly_query(blueprint.cypher_query) is None


def test_settings_treat_release_debug_as_false():
    settings = Settings(debug="release", _env_file=None)
    assert settings.debug is False


def test_item_cohort_expansion_parses_tail_followup():
    query = "the users who bought iphone 13 pro what else did they buy"
    parsed = graphrag_service._parse_item_cohort_expansion_query(query)

    assert parsed is not None
    assert parsed.seed_item_phrase == "iphone 13 pro"
    assert parsed.seed_item_terms == ["iphone 13 pro"]
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._item_cohort_expansion_cypher()) is None


def test_item_cohort_expansion_parses_what_other_followup():
    query = "users who requested standard laptop what other items did they request"
    parsed = graphrag_service._parse_item_cohort_expansion_query(query)

    assert parsed is not None
    assert parsed.seed_item_phrase == "standard laptop"
    assert parsed.seed_item_terms == ["standard laptop"]


def test_item_cohort_expansion_parses_leading_followup():
    query = "what else did users who bought iphone 13 pro buy"
    parsed = graphrag_service._parse_item_cohort_expansion_query(query)

    assert parsed is not None
    assert parsed.seed_item_phrase == "iphone 13 pro"


def test_item_requester_parser_strips_followup_tail_if_called_directly():
    parsed = graphrag_service._parse_item_requester_query(
        "the users who bought iphone 13 pro what else did they buy"
    )

    assert parsed is not None
    assert parsed.item_phrase == "iphone 13 pro"


def test_item_cohort_expansion_renders_other_items_not_users_only():
    parsed = graphrag_service._parse_item_cohort_expansion_query(
        "the users who bought iphone 13 pro what else did they buy"
    )
    rows = [
        {"name": "Standard Laptop", "requesters": ["Lucius Bagnoli"], "request_count": 1},
        {"name": "VPN Access", "requesters": ["Jimmie Barninger", "Lucius Bagnoli"], "request_count": 2},
    ]

    answer = graphrag_service._render_item_cohort_expansion_answer(parsed, rows)

    assert answer == (
        "Other items requested by users who requested iphone 13 pro:\n"
        "- Standard Laptop (Lucius Bagnoli)\n"
        "- VPN Access (Jimmie Barninger, Lucius Bagnoli)"
    )
    assert "Users who requested iphone 13 pro:" not in answer


def test_item_cohort_expansion_executes_deterministically_with_mocked_graph():
    original_is_available = neo4j_query_runtime_service.is_available
    original_execute = neo4j_query_runtime_service.execute_readonly_query

    def fake_execute(query, params, limit, timeout=None):
        if "MATCH (catalog:CatalogItem)" in query:
            return {
                "ok": True,
                "error": None,
                "rows": [
                    {
                        "sys_id": "catalog-iphone",
                        "request_item_sys_id": None,
                        "name": "Apple iPhone 13 Pro",
                        "category": "Hardware",
                        "match_score": 80,
                        "match_type": "phrase",
                    }
                ],
            }
        if "RETURN other_item_name AS name" in query:
            return {
                "ok": True,
                "error": None,
                "rows": [
                    {"name": "Standard Laptop", "requesters": ["Lucius Bagnoli"], "request_count": 1},
                    {"name": "VPN Access", "requesters": ["Jimmie Barninger"], "request_count": 1},
                ],
            }
        return {"ok": True, "error": None, "rows": []}

    try:
        neo4j_query_runtime_service.is_available = lambda: True
        neo4j_query_runtime_service.execute_readonly_query = fake_execute

        result = asyncio.run(
            graphrag_service._execute_item_cohort_expansion_query(
                "the users who bought iphone 13 pro what else did they buy"
            )
        )
    finally:
        neo4j_query_runtime_service.is_available = original_is_available
        neo4j_query_runtime_service.execute_readonly_query = original_execute

    assert result["handled"] is True
    assert result["answer"] == (
        "Other items requested by users who requested iphone 13 pro:\n"
        "- Standard Laptop (Lucius Bagnoli)\n"
        "- VPN Access (Jimmie Barninger)"
    )
    assert result["cypher"]["parameters"]["catalog_item_sys_ids"] == ["catalog-iphone"]


def test_item_requester_query_parses_user_item_question():
    query = "which users bought iphone 13 pro"
    parsed = graphrag_service._parse_item_requester_query(query)

    assert parsed is not None
    assert parsed.answer_intent == "list"
    assert parsed.item_phrase == "iphone 13 pro"
    assert "iphone 13 pro" in parsed.item_terms
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._item_requester_cypher()) is None


def test_item_requester_query_renders_users_not_tickets():
    parsed = graphrag_service._parse_item_requester_query("which users bought iphone 13 pro")
    rows = [
        {"name": "Lucius Bagnoli", "request_count": 2, "ticket_numbers": ["REQ1", "REQ2"]},
        {"name": "Jimmie Barninger", "request_count": 1, "ticket_numbers": ["REQ3"]},
    ]

    answer = graphrag_service._render_item_requester_answer(parsed, rows)

    assert answer == "Users who requested iphone 13 pro:\n- Lucius Bagnoli\n- Jimmie Barninger"
    assert "REQ1" not in answer


def test_item_requester_query_executes_deterministically_with_mocked_graph():
    original_is_available = neo4j_query_runtime_service.is_available
    original_execute = neo4j_query_runtime_service.execute_readonly_query

    def fake_execute(query, params, limit, timeout=None):
        if "MATCH (catalog:CatalogItem)" in query:
            return {
                "ok": True,
                "error": None,
                "rows": [
                    {
                        "sys_id": "catalog-iphone",
                        "request_item_sys_id": None,
                        "name": "Apple iPhone 13 Pro",
                        "category": "Hardware",
                        "match_score": 100,
                        "match_type": "exact",
                    }
                ],
            }
        if "RETURN requester_name AS name" in query:
            return {
                "ok": True,
                "error": None,
                "rows": [
                    {"name": "Lucius Bagnoli", "request_count": 2, "ticket_numbers": ["REQ1", "REQ2"]},
                    {"name": "Jimmie Barninger", "request_count": 1, "ticket_numbers": ["REQ3"]},
                ],
            }
        return {"ok": True, "error": None, "rows": []}

    try:
        neo4j_query_runtime_service.is_available = lambda: True
        neo4j_query_runtime_service.execute_readonly_query = fake_execute

        result = asyncio.run(
            graphrag_service._execute_item_requester_query("which users bought iphone 13 pro")
        )
    finally:
        neo4j_query_runtime_service.is_available = original_is_available
        neo4j_query_runtime_service.execute_readonly_query = original_execute

    assert result["handled"] is True
    assert result["answer"] == "Users who requested iphone 13 pro:\n- Lucius Bagnoli\n- Jimmie Barninger"
    assert result["cypher"]["parameters"]["catalog_item_sys_ids"] == ["catalog-iphone"]


def test_requester_item_set_query_parses_not_bought_by_both():
    query = "which mobiles were not bought by both lucius and jimmie"
    parsed = graphrag_service._parse_requester_item_set_query(query)

    assert parsed is not None
    assert parsed.operation == "symmetric_difference"
    assert parsed.requester_one == "lucius"
    assert parsed.requester_two == "jimmie"
    assert parsed.item_phrase == "mobile"
    assert "mobile" in parsed.category_terms
    assert "phone" in parsed.category_terms
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._requester_item_set_cypher()) is None


def test_requester_item_set_query_parses_left_difference():
    query = "which mobiles did lucius buy but jimmie did not"
    parsed = graphrag_service._parse_requester_item_set_query(query)

    assert parsed is not None
    assert parsed.operation == "left_difference"
    assert parsed.requester_one == "lucius"
    assert parsed.requester_two == "jimmie"
    assert parsed.item_phrase == "mobile"


def test_requester_item_set_answer_is_deterministic():
    parsed = graphrag_service._parse_requester_item_set_query(
        "which mobiles were not bought by both lucius and jimmie"
    )
    rows = [{"name": "Apple iPhone 13 Pro", "requester_one_count": 1, "requester_two_count": 0}]

    first = graphrag_service._render_requester_item_set_answer(parsed, rows)
    second = graphrag_service._render_requester_item_set_answer(parsed, rows)

    assert first == second
    assert first == "Mobile requested by exactly one of lucius or jimmie:\n- Apple iPhone 13 Pro"


def test_confident_candidate_values_rejects_ambiguous_close_matches():
    candidates = [
        {"sys_id": "user-1", "name": "Lucius Bagnoli", "match_score": 80, "match_type": "phrase"},
        {"sys_id": "user-2", "name": "Lucius Other", "match_score": 79, "match_type": "phrase"},
    ]

    assert graphrag_service._confident_candidate_values(candidates, "sys_id") == []


def test_requester_item_set_query_executes_deterministically_with_mocked_graph():
    original_is_available = neo4j_query_runtime_service.is_available
    original_execute = neo4j_query_runtime_service.execute_readonly_query

    def fake_execute(query, params, limit, timeout=None):
        if "MATCH (ticket:Ticket)" in query and "WITH DISTINCT" in query:
            term = (params.get("requester_terms") or [""])[0]
            name = "Lucius Bagnoli" if term == "lucius" else "Jimmie Barninger"
            sys_id = "user-lucius" if term == "lucius" else "user-jimmie"
            return {
                "ok": True,
                "error": None,
                "rows": [{"sys_id": sys_id, "name": name, "match_score": 80, "match_type": "phrase"}],
            }
        if "$set_operation" in query:
            return {
                "ok": True,
                "error": None,
                "rows": [{"name": "Apple iPhone 13 Pro", "requester_one_count": 1, "requester_two_count": 0}],
            }
        return {"ok": True, "error": None, "rows": []}

    try:
        neo4j_query_runtime_service.is_available = lambda: True
        neo4j_query_runtime_service.execute_readonly_query = fake_execute

        result = asyncio.run(
            graphrag_service._execute_requester_item_set_query(
                "which mobiles were not bought by both lucius and jimmie"
            )
        )
    finally:
        neo4j_query_runtime_service.is_available = original_is_available
        neo4j_query_runtime_service.execute_readonly_query = original_execute

    assert result["handled"] is True
    assert result["answer"] == "Mobile requested by exactly one of lucius or jimmie:\n- Apple iPhone 13 Pro"
    assert result["cypher"]["parameters"]["set_operation"] == "symmetric_difference"


def test_render_compiled_rows_formats_segmented_counts():
    query = "how many total requests have lucius bagnoli made and how many those are hardware and how many of them are standard laptop"
    program = GraphRAGCypherProgram(
        intent_summary="ticket:count_segments",
        cypher_query="RETURN 1 AS total_count",
        requester_terms=["lucius bagnoli"],
        segment_one_label="hardware",
        segment_two_label="standard laptop",
    )

    answer = graphrag_service._render_compiled_rows_deterministically(
        query,
        program,
        [{"total_count": 9, "segment_one_count": 4, "segment_two_count": 2}],
    )

    assert answer == "Total requests: 9\nhardware: 4\nstandard laptop: 2"


def test_exact_business_query_parses_distinct_products_measure():
    parsed = graphrag_service._parse_exact_business_query("how many products did lucius order")

    assert parsed is not None
    assert parsed.family == "requester_measure"
    assert parsed.measure == "distinct_catalog_item_count"
    assert parsed.requester_terms == ["lucius"]


def test_exact_business_query_parses_last_week_request_list():
    parsed = graphrag_service._parse_exact_business_query("what orders did lucius make last week")

    assert parsed is not None
    assert parsed.family == "requester_request_list"
    assert parsed.time_window is not None
    assert parsed.time_window.label == "last week"
    assert parsed.requester_terms == ["lucius"]


def test_exact_business_query_parses_grouped_requester_summary():
    parsed = graphrag_service._parse_exact_business_query(
        "for each requester, show total requests, hardware requests, and mobile requests"
    )

    assert parsed is not None
    assert parsed.family == "grouped_requester_summary"
    assert parsed.category_terms == ["hardware", "mobile"]


def test_exact_business_query_parses_threshold_requester_aggregate():
    parsed = graphrag_service._parse_exact_business_query(
        "which users requested more than one mobile device, and how many mobile requests did each user make?"
    )

    assert parsed is not None
    assert parsed.family == "threshold_requester_aggregate"
    assert parsed.threshold == 1
    assert parsed.category_terms == ["mobile"]


def test_route_classifier_prefers_exact_graph_query_for_structured_ticket_question():
    decision = graphrag_service._classify_graphrag_route(
        "how many total requests have lucius bagnoli made and how many those are hardware and how many of them are standard laptop"
    )

    assert decision.route == "exact_graph_query"
    assert decision.retrieval_mode == "fixed_cypher_template"


def test_route_classifier_prefers_subgraph_reasoning_for_item_requester_question():
    decision = graphrag_service._classify_graphrag_route("which users bought iphone 13 pro")

    assert decision.route == "subgraph_reasoning_query"
    assert decision.retrieval_mode == "bounded_subgraph"


def test_exact_graph_route_uses_deterministic_structured_breakdown_without_llm():
    original_is_available = neo4j_query_runtime_service.is_available
    original_validate = neo4j_query_runtime_service.validate_readonly_query
    original_execute = graphrag_service._execute_readonly_query_with_timeout
    original_build_breakdown = graphrag_service._build_ticket_query_breakdown
    original_resolve_requester = graphrag_service.resolve_ticket_requester
    original_resolve_item = graphrag_service.resolve_ticket_item

    async def fake_execute(cypher_query, parameters, limit_cap, stage):
        return {"ok": True, "error": None, "rows": [{"count": 6}]}

    try:
        neo4j_query_runtime_service.is_available = lambda: True
        neo4j_query_runtime_service.validate_readonly_query = lambda query: None
        graphrag_service._execute_readonly_query_with_timeout = fake_execute
        graphrag_service._build_ticket_query_breakdown = lambda query, plan: (_ for _ in ()).throw(AssertionError("LLM breakdown should not run"))
        graphrag_service.resolve_ticket_requester = lambda phrase, limit=5: []
        graphrag_service.resolve_ticket_item = lambda phrase, limit=5: []

        result = asyncio.run(
            graphrag_service._execute_exact_graph_route(
                "how many requests from jimmie barninger that are apple iphone 13 pro"
            )
        )
    finally:
        neo4j_query_runtime_service.is_available = original_is_available
        neo4j_query_runtime_service.validate_readonly_query = original_validate
        graphrag_service._execute_readonly_query_with_timeout = original_execute
        graphrag_service._build_ticket_query_breakdown = original_build_breakdown
        graphrag_service.resolve_ticket_requester = original_resolve_requester
        graphrag_service.resolve_ticket_item = original_resolve_item

    assert result["handled"] is True
    assert result["cypher"]["mode"] == "structured_ticket_query"
    assert result["answer"] == "Apple iphone 13 pro requests: 6"


def test_structured_breakdown_separates_requester_and_nested_metrics():
    query = "how many total requests have lucius bagnoli made and how many those are hardware and how many of them are standard laptop"
    breakdown = _structured_breakdown(query)

    assert breakdown.requester_terms == ["lucius bagnoli"]
    assert breakdown.base_ticket_type == "SR"
    assert [metric.label for metric in breakdown.metrics] == [
        "total requests",
        "hardware requests",
        "standard laptop requests",
    ]

    resolved = {}
    total = graphrag_service._resolve_ticket_metric_filters(breakdown.metrics[0], breakdown, resolved)
    resolved[graphrag_service._metric_key(breakdown.metrics[0].label)] = total
    hardware = graphrag_service._resolve_ticket_metric_filters(breakdown.metrics[1], breakdown, resolved)
    resolved[graphrag_service._metric_key(breakdown.metrics[1].label)] = hardware
    standard_laptop = graphrag_service._resolve_ticket_metric_filters(breakdown.metrics[2], breakdown, resolved)
    standard_params = graphrag_service._ticket_metric_parameters(standard_laptop, 10)

    assert total["category_terms"] == []
    assert "hardware" in hardware["category_terms"] + hardware["catalog_item_terms"]
    assert "hardware" in standard_params["parent_category_terms"] + standard_params["parent_item_terms"]
    assert "standard laptop" in standard_params["category_terms"] + standard_params["item_terms"]
    assert "lucius" not in standard_params["category_terms"] + standard_params["item_terms"]


def test_structured_breakdown_handles_adjacent_requester_category_wording():
    query = "how many requests for lucius bagnoli are hardware"
    breakdown = _structured_breakdown(query)
    metric = breakdown.metrics[0]
    filters = graphrag_service._resolve_ticket_metric_filters(metric, breakdown, {})
    params = graphrag_service._ticket_metric_parameters(filters, 10)

    assert breakdown.requester_terms == ["lucius bagnoli"]
    assert metric.label == "hardware requests"
    assert "hardware" in params["category_terms"] + params["item_terms"]
    assert params["requester_terms"] == ["lucius bagnoli"]


def test_structured_breakdown_handles_from_requester_and_specific_item_wording():
    query = "how many requests from jimmie barninger that are apple iphone 13 pro"
    breakdown = _structured_breakdown(query)
    metric = breakdown.metrics[0]
    filters = graphrag_service._resolve_ticket_metric_filters(metric, breakdown, {})
    params = graphrag_service._ticket_metric_parameters(filters, 10)

    assert breakdown.requester_terms == ["jimmie barninger"]
    assert metric.label == "apple iphone 13 pro requests"
    assert "apple iphone 13 pro" in params["item_terms"]
    assert params["category_terms"] == []


def test_structured_breakdown_applies_closed_only_to_subset_metric():
    query = "how many change requests did david loo make and how many are closed"
    breakdown = _structured_breakdown(query)

    assert breakdown.base_ticket_type == "CH"
    assert breakdown.requester_terms == ["david loo"]
    assert [metric.label for metric in breakdown.metrics] == [
        "total change requests",
        "closed change requests",
    ]
    assert breakdown.metrics[0].state_terms == []
    assert breakdown.metrics[1].state_terms == ["closed"]


def test_structured_breakdown_handles_request_number_listing_with_item_tail():
    query = "show request numbers for lucius bagnoli standard laptop"
    breakdown = _structured_breakdown(query)
    metric = breakdown.metrics[0]
    filters = graphrag_service._resolve_ticket_metric_filters(metric, breakdown, {})
    params = graphrag_service._ticket_metric_parameters(filters, 100)

    assert breakdown.answer_intent == "list"
    assert breakdown.requester_terms == ["lucius bagnoli"]
    assert "standard laptop" in params["category_terms"] + params["item_terms"]
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._ticket_metric_list_cypher()) is None


def test_structured_breakdown_strips_list_followup_tail_from_subset_metric():
    query = "how many requests did jimmie make last month and how many of them were hardware and what requests were they"
    breakdown = _structured_breakdown(query)

    assert breakdown.requester_terms == ["jimmie"]
    assert breakdown.time_window_label == "last month"
    assert [metric.label for metric in breakdown.metrics] == [
        "total requests",
        "hardware requests",
    ]


def test_structured_ticket_parameters_include_time_window_filters():
    query = "how many requests did jimmie make last week"
    breakdown = _structured_breakdown(query)
    metric = breakdown.metrics[0]
    filters = graphrag_service._resolve_ticket_metric_filters(metric, breakdown, {})
    params = graphrag_service._ticket_metric_parameters(filters, 25)

    assert breakdown.time_window_label == "last week"
    assert params["opened_start"] == breakdown.opened_start
    assert params["opened_end"] == breakdown.opened_end
    assert params["opened_start"] != ""
    assert params["opened_end"] != ""


def test_structured_ticket_output_plan_adds_followup_list_scope():
    query = "how many requests did jimmie make last month and how many of them were hardware and what requests were they"
    breakdown = _structured_breakdown(query)
    plan = graphrag_service._heuristic_ticket_output_plan(query, breakdown)

    assert [(section.section_type, section.metric_label) for section in plan.sections] == [
        ("count_metric", "total requests"),
        ("count_metric", "hardware requests"),
        ("list_scope", "hardware requests"),
    ]
    assert plan.sections[-1].source_metric == "hardware requests"


def test_structured_ticket_output_rendering_supports_counts_and_list_sections():
    answer = graphrag_service._render_structured_ticket_output_answer(
        "how many requests did jimmie make last month and how many of them were hardware and what requests were they",
        [
            {"section_type": "count_metric", "label": "total requests", "count": 9},
            {"section_type": "count_metric", "label": "hardware requests", "count": 3},
            {
                "section_type": "list_scope",
                "label": "hardware requests",
                "rows": [
                    {"number": "REQ001", "description": "Laptop refresh"},
                    {"number": "REQ002", "description": "Monitor request"},
                ],
                "include_descriptions": True,
            },
        ],
    )

    assert answer == (
        "Total requests: 9\n"
        "Hardware requests: 3\n"
        "Hardware requests:\n"
        "- REQ001: Laptop refresh\n"
        "- REQ002: Monitor request"
    )


def test_business_filter_terms_reuse_time_window_parser_terms():
    terms = graphrag_service._extract_business_filter_terms(
        "how many requests did jimmie make last month",
        ["jimmie"],
    )

    assert "last" not in terms["item_terms"] + terms["category_terms"]
    assert "month" not in terms["item_terms"] + terms["category_terms"]


def test_structured_ticket_cypher_validates_as_readonly():
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._ticket_metric_count_cypher()) is None
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._ticket_metric_list_cypher()) is None
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._ticket_requester_resolver_cypher()) is None
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._ticket_catalog_item_resolver_cypher()) is None
    assert neo4j_query_runtime_service.validate_readonly_query(graphrag_service._ticket_request_item_resolver_cypher()) is None


def test_resolved_entities_prefer_sys_ids_in_generated_parameters():
    query = "how many requests from jimmie barninger that are apple iphone 13 pro"
    breakdown = _structured_breakdown(query)
    metric = breakdown.metrics[0]
    resolution_context = {
        "requesters": {
            "jimmie barninger": [
                {
                    "sys_id": "user-1",
                    "name": "Jimmie Barninger",
                    "match_score": 100,
                    "match_type": "exact",
                }
            ]
        },
        "items": {
            "apple iphone 13 pro": [
                {
                    "sys_id": "catalog-1",
                    "request_item_sys_id": "ritm-1",
                    "name": "Apple iPhone 13 Pro",
                    "category": "Hardware",
                    "match_score": 100,
                    "match_type": "exact",
                }
            ]
        },
    }

    filters = graphrag_service._resolve_ticket_metric_filters(metric, breakdown, {}, resolution_context)
    params = graphrag_service._ticket_metric_parameters(filters, 10)

    assert params["requester_sys_ids"] == ["user-1"]
    assert params["catalog_item_sys_ids"] == ["catalog-1"]
    assert params["request_item_sys_ids"] == ["ritm-1"]
    assert params["requester_terms"] == ["jimmie barninger"]
    assert params["item_terms"] == ["apple iphone 13 pro"]


def test_resolve_ticket_requester_ranks_exact_match_first():
    original_is_available = neo4j_query_runtime_service.is_available
    original_execute = neo4j_query_runtime_service.execute_readonly_query

    try:
        neo4j_query_runtime_service.is_available = lambda: True
        neo4j_query_runtime_service.execute_readonly_query = lambda query, params, limit, timeout=None: {
            "ok": True,
            "error": None,
            "rows": [
                {"sys_id": "user-2", "name": "Jimmie Other", "match_score": 10, "match_type": "token"},
                {"sys_id": "user-1", "name": "Jimmie Barninger", "match_score": 100, "match_type": "exact"},
            ],
        }

        candidates = graphrag_service.resolve_ticket_requester("jimmie barninger")
    finally:
        neo4j_query_runtime_service.is_available = original_is_available
        neo4j_query_runtime_service.execute_readonly_query = original_execute

    assert candidates[0]["sys_id"] == "user-1"
    assert candidates[0]["match_type"] == "exact"


def test_resolve_ticket_item_merges_catalog_and_request_item_candidates():
    original_is_available = neo4j_query_runtime_service.is_available
    original_execute = neo4j_query_runtime_service.execute_readonly_query

    def fake_execute(query, params, limit, timeout=None):
        if "MATCH (catalog:CatalogItem)" in query:
            return {
                "ok": True,
                "error": None,
                "rows": [
                    {
                        "sys_id": "catalog-1",
                        "request_item_sys_id": None,
                        "name": "Apple iPhone 13 Pro",
                        "category": "Hardware",
                        "match_score": 100,
                        "match_type": "exact",
                    }
                ],
            }
        return {
            "ok": True,
            "error": None,
            "rows": [
                {
                    "sys_id": "catalog-1",
                    "request_item_sys_id": "ritm-1",
                    "name": "Apple iPhone 13 Pro",
                    "category": "Hardware",
                    "match_score": 80,
                    "match_type": "phrase",
                }
            ],
        }

    try:
        neo4j_query_runtime_service.is_available = lambda: True
        neo4j_query_runtime_service.execute_readonly_query = fake_execute

        candidates = graphrag_service.resolve_ticket_item("apple iphone 13 pro")
    finally:
        neo4j_query_runtime_service.is_available = original_is_available
        neo4j_query_runtime_service.execute_readonly_query = original_execute

    assert candidates[0]["sys_id"] == "catalog-1"
    assert candidates[0]["name"] == "Apple iPhone 13 Pro"
    assert candidates[0]["match_type"] == "exact"


def test_structured_count_rendering_is_deterministic():
    results = [
        {"label": "total requests", "count": 9},
        {"label": "hardware requests", "count": 4},
        {"label": "standard laptop requests", "count": 2},
    ]

    first = graphrag_service._render_structured_ticket_count_answer(results)
    second = graphrag_service._render_structured_ticket_count_answer(results)

    assert first == second
    assert first == "Total requests: 9\nHardware requests: 4\nStandard laptop requests: 2"
