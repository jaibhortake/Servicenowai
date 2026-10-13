import asyncio
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.services.graphrag_v2.cypher_generator import GraphRAGv2CypherGenerator  # noqa: E402
from app.services.graphrag_v2.dynamic_retriever import GraphRAGv2DynamicRetriever  # noqa: E402
from app.services.graphrag_v2.generic_planner import GraphRAGv2GenericPlanner  # noqa: E402
from app.services.graphrag_v2.query_guard import GraphRAGv2QueryGuard  # noqa: E402
from app.services.graphrag_v2.graph_profile import GraphRAGv2GraphProfileService  # noqa: E402
from app.services.graphrag_v2.schema_provider import GraphRAGv2SchemaProvider  # noqa: E402
from app.services.graphrag_v2.service import graphrag_v2_service  # noqa: E402
from app.services.graphrag_v2.embedding_service import GraphRAGv2EmbeddingService  # noqa: E402
from app.services.graphrag_v2.embedding_service import _HybridCandidate  # noqa: E402
from app.services.graphrag_v2.ontology import GraphRAGv2OntologyPlanner  # noqa: E402
from app.services.graphrag_v2.types import GraphRAGv2CypherCandidate, GraphRAGv2ExecutionResult, GraphRAGv2NodeType, GraphRAGv2PropertyProfile, GraphRAGv2RelationshipType, GraphRAGv2SchemaSnapshot, GraphRAGv2SemanticProfile, GraphRAGv2VirtualEdge  # noqa: E402
from app.services.neo4j_query_runtime import neo4j_query_runtime_service  # noqa: E402


def test_graphrag_v2_query_guard_rejects_blocked_call_clause():
    guard = GraphRAGv2QueryGuard()
    candidate = GraphRAGv2CypherCandidate(
        candidate_id="bad_1",
        query_shape="lookup",
        purpose="unsafe",
        cypher_query="MATCH (n) CALL db.labels() RETURN n",
        parameters={},
    )

    validation = guard.validate_candidates([candidate])[0]

    assert validation.valid is False
    assert "blocked pattern" in validation.reason


def test_graphrag_v2_query_guard_rejects_brittle_exact_text_match():
    guard = GraphRAGv2QueryGuard()
    candidate = GraphRAGv2CypherCandidate(
        candidate_id="bad_exact_name",
        query_shape="count",
        purpose="unsafe exact text lookup",
        cypher_query=(
            "MATCH (u:User {name: 'Jimmie Barninger'})-[:REQUESTED]->(t:Ticket) "
            "RETURN count(t) AS request_count"
        ),
        parameters={},
    )

    validation = guard.validate_candidates([candidate])[0]

    assert validation.valid is False
    assert "brittle exact equality" in validation.reason


def test_graphrag_v2_dynamic_resolver_maps_bad_water_taste():
    retriever = GraphRAGv2DynamicRetriever(GraphRAGv2QueryGuard(), object())

    terms = retriever.issue_terms("what are the potential causes for bad water taste")

    assert "taste/odor" in terms
    assert "taste" in terms


def test_graphrag_v2_virtual_join_compiles_from_verified_session_edge():
    retriever = GraphRAGv2DynamicRetriever(GraphRAGv2QueryGuard(), object())
    profile = GraphRAGv2SemanticProfile(
        snapshot_id="snap_1",
        virtual_edges=[
            GraphRAGv2VirtualEdge(
                from_label="HypothesisScore",
                from_property="session",
                to_label="DiagnosisSession",
                to_property="sys_id",
                edge_type="VIRTUAL_SESSION",
                confidence=1.0,
                sample_overlap=10,
            )
        ],
    )

    clause = retriever._virtual_match(
        profile,
        known_var="session",
        known_label="DiagnosisSession",
        new_var="hypothesis",
        new_label="HypothesisScore",
        preferred_properties=("session",),
        optional=True,
    )

    assert clause == "OPTIONAL MATCH (hypothesis:HypothesisScore) WHERE hypothesis.session = session.sys_id"


def test_graphrag_v2_profile_detects_numeric_like_replacement_cycle():
    assert (
        GraphRAGv2GraphProfileService._infer_property_type("replacement_cycle_months", ["6", "12"])
        == "numeric_like_string"
    )


def test_graphrag_v2_generic_planner_resolves_sample_value_anchor():
    planner = GraphRAGv2GenericPlanner()
    profile = GraphRAGv2SemanticProfile(
        snapshot_id="snap_1",
        property_profiles=[
            GraphRAGv2PropertyProfile(
                label="Department",
                property_name="name",
                property_type="enum",
                sample_values=["Human Resources", "Finance"],
                searchable_score=90,
            )
        ],
        searchable_fields_by_label={"Department": ["name"]},
    )

    resolved = planner.resolve_values("show people in human resources", profile)

    assert resolved
    assert resolved[0].label == "Department"
    assert resolved[0].value == "Human Resources"


def test_graphrag_v2_generic_planner_compiles_rank_from_schema_relationship():
    planner = GraphRAGv2GenericPlanner()
    snapshot = GraphRAGv2SchemaSnapshot(
        snapshot_id="snap_1",
        generated_at="2026-06-03T00:00:00",
        summary_text="schema",
        node_types=[
            GraphRAGv2NodeType(label="Customer", count=3, properties=["id", "name"]),
            GraphRAGv2NodeType(label="Order", count=10, properties=["id", "created_at", "status"]),
        ],
        relationships=[
            GraphRAGv2RelationshipType(from_label="Customer", rel_type="PLACED", to_label="Order"),
        ],
        allowed_labels=["Customer", "Order"],
        allowed_relationships=["PLACED"],
    )
    profile = GraphRAGv2SemanticProfile(
        snapshot_id="snap_1",
        property_profiles=[
            GraphRAGv2PropertyProfile(label="Customer", property_name="name", property_type="text", searchable_score=90),
            GraphRAGv2PropertyProfile(label="Order", property_name="status", property_type="enum", searchable_score=45),
            GraphRAGv2PropertyProfile(label="Order", property_name="created_at", property_type="datetime_like", searchable_score=0),
        ],
        searchable_fields_by_label={"Customer": ["name"], "Order": ["status"]},
    )

    candidates = planner.build_candidates(
        question="rank customers by number of orders",
        snapshot=snapshot,
        profile=profile,
        desired_shape="rank",
        time_window=None,
    )

    assert any(candidate.candidate_id == "generic_rank_by_related__Customer__Order" for candidate in candidates)


def test_neo4j_query_runtime_allows_collect_map_keys_without_misreading_labels():
    query = (
        "MATCH (u:User)-[:REQUESTED]->(sr:ServiceRequest)-[:HAS_ITEM]->(ri:RequestItem)-[:FULFILLS_CATALOG_ITEM]->(ci:CatalogItem) "
        "WHERE toLower(ci.name) CONTAINS 'iphone 13' "
        "WITH u.name AS user_name, ci.name AS catalog_item_name, ri.number AS request_number, "
        "ri.short_description AS request_description, sr.opened_at AS request_date, "
        "CASE WHEN toLower(ci.name) CONTAINS 'iphone 13 pro' THEN 'iphone 13 pro' ELSE 'iphone 13' END AS iphone_model "
        "RETURN user_name, iphone_model, collect({request_number: request_number, request_description: request_description, request_date: request_date}) AS requests "
        "ORDER BY user_name, iphone_model LIMIT 20"
    )

    original_metadata = neo4j_query_runtime_service.live_schema_metadata
    original_mode = settings.graphrag_schema_strict_mode
    try:
        settings.graphrag_schema_strict_mode = "dynamic"
        neo4j_query_runtime_service.live_schema_metadata = lambda refresh=False, ttl_seconds=300: {
            "labels": ["User", "ServiceRequest", "RequestItem", "CatalogItem"],
            "relationships": ["REQUESTED", "HAS_ITEM", "FULFILLS_CATALOG_ITEM"],
            "properties_by_label": {
                "User": ["name"],
                "ServiceRequest": ["opened_at"],
                "RequestItem": ["number", "short_description"],
                "CatalogItem": ["name"],
            },
            "time_field_candidates": ["opened_at"],
        }

        validation_error = neo4j_query_runtime_service.validate_readonly_query(query)
        assert validation_error is None
    finally:
        settings.graphrag_schema_strict_mode = original_mode
        neo4j_query_runtime_service.live_schema_metadata = original_metadata


def test_graphrag_v2_schema_provider_builds_module_hints():
    provider = GraphRAGv2SchemaProvider(ttl_seconds=300)
    hints = provider._module_hints(
        [
            GraphRAGv2NodeType(label="User", count=1, properties=["sys_id", "name"]),
            GraphRAGv2NodeType(label="Ticket", count=1, properties=["number", "opened_at"]),
            GraphRAGv2NodeType(label="RequestItem", count=1, properties=["number"]),
            GraphRAGv2NodeType(label="CatalogItem", count=1, properties=["name"]),
        ],
        [
            GraphRAGv2RelationshipType(from_label="User", rel_type="REQUESTED", to_label="Ticket"),
            GraphRAGv2RelationshipType(from_label="Ticket", rel_type="HAS_ITEM", to_label="RequestItem"),
            GraphRAGv2RelationshipType(from_label="RequestItem", rel_type="FULFILLS_CATALOG_ITEM", to_label="CatalogItem"),
        ],
        "servicenow_like",
        True,
        ["opened_at"],
    )

    assert any("Users request tickets" in hint for hint in hints)
    assert any("RequestItem -> FULFILLS_CATALOG_ITEM -> CatalogItem" in hint for hint in hints)


def test_graphrag_v2_ranks_matching_shape_first():
    candidates = [
        GraphRAGv2CypherCandidate(
            candidate_id="list_1",
            query_shape="list",
            purpose="list tickets",
            cypher_query="MATCH (t:Ticket) RETURN t.number AS number LIMIT 20",
            parameters={},
        ),
        GraphRAGv2CypherCandidate(
            candidate_id="count_1",
            query_shape="count",
            purpose="count tickets",
            cypher_query="MATCH (t:Ticket) RETURN count(DISTINCT t) AS count",
            parameters={},
        ),
    ]

    ranked = graphrag_v2_service._rank_candidates(candidates, "count")

    assert ranked[0].candidate_id == "count_1"


def test_graphrag_v2_falls_back_to_legacy_when_all_candidates_invalid():
    original_provider = graphrag_v2_service.schema_provider.get_snapshot
    original_generator = graphrag_v2_service.cypher_generator.generate_candidates
    original_fallback = graphrag_v2_service._fallback

    class _Snapshot:
        snapshot_id = "snap_1"
        summary_text = "schema"

    async def fake_generate_candidates(**kwargs):
        return [
            GraphRAGv2CypherCandidate(
                candidate_id="bad_1",
                query_shape="count",
                purpose="invalid",
                cypher_query="MATCH (n) CALL db.labels() RETURN n",
                parameters={},
            )
        ]

    async def fake_fallback(**kwargs):
        return {"answer": "legacy fallback answer", "evidence": {"fallback_used": True}}

    try:
        graphrag_v2_service.schema_provider.get_snapshot = lambda: _Snapshot()
        graphrag_v2_service.cypher_generator.generate_candidates = fake_generate_candidates
        graphrag_v2_service._fallback = fake_fallback

        result = asyncio.run(graphrag_v2_service.query("how many requests did jimmie make"))
    finally:
        graphrag_v2_service.schema_provider.get_snapshot = original_provider
        graphrag_v2_service.cypher_generator.generate_candidates = original_generator
        graphrag_v2_service._fallback = original_fallback

    assert result["answer"] == "legacy fallback answer"


def test_graphrag_v2_treats_zero_request_count_as_weak_execution():
    class _Execution:
        ok = True
        row_count = 1
        rows = [{"request_count": 0}]

    assert graphrag_v2_service._is_weak_execution(_Execution()) is True


def test_graphrag_v2_resolves_last_week_window():
    window = graphrag_v2_service._resolve_time_window("Which users requested Apple iPhone 13 Pro last week?")

    assert window is not None
    assert window.label == "last week"
    datetime.strptime(window.start, "%Y-%m-%d %H:%M:%S")
    datetime.strptime(window.end, "%Y-%m-%d %H:%M:%S")


def test_graphrag_v2_prepares_parameterized_time_window_candidates():
    candidates = [
        GraphRAGv2CypherCandidate(
            candidate_id="time_1",
            query_shape="list",
            purpose="time aware",
            cypher_query=(
                "MATCH (t:Ticket) WHERE toString(coalesce(t.opened_at, '')) >= $opened_start "
                "AND toString(coalesce(t.opened_at, '')) < $opened_end RETURN t.number AS number LIMIT 20"
            ),
            parameters={},
        ),
        GraphRAGv2CypherCandidate(
            candidate_id="plain_1",
            query_shape="list",
            purpose="plain",
            cypher_query="MATCH (t:Ticket) RETURN t.number AS number LIMIT 20",
            parameters={},
        ),
    ]

    window = graphrag_v2_service._resolve_time_window("show requests last week")
    prepared = graphrag_v2_service._prepare_candidates(candidates, window)

    assert prepared[0].candidate_id == "time_1"
    assert prepared[0].parameters["opened_start"] == window.start
    assert prepared[0].parameters["opened_end"] == window.end


def test_graphrag_v2_detects_compound_count_and_list_request():
    shapes = graphrag_v2_service._requested_output_shapes(
        "how many request for macbook were made in the last month and which users have open iphone requests and give names"
    )

    assert shapes == ["count", "list"]


def test_graphrag_v2_selects_count_and_list_sections_for_compound_query():
    executions = [
        GraphRAGv2ExecutionResult(
            candidate_id="c1",
            query_shape="count",
            purpose="count macbook requests",
            cypher_query="MATCH (t:Ticket) RETURN count(t) AS macbook_request_count",
            parameters={},
            rows=[{"macbook_request_count": 6}],
            row_count=1,
            ok=True,
            error="",
            duration_ms=10.0,
        ),
        GraphRAGv2ExecutionResult(
            candidate_id="c2",
            query_shape="list",
            purpose="list matching users",
            cypher_query="MATCH (u:User) RETURN u.name AS user_name LIMIT 20",
            parameters={},
            rows=[{"user_name": "Jimmie Barninger"}],
            row_count=1,
            ok=True,
            error="",
            duration_ms=12.0,
        ),
    ]

    selected = graphrag_v2_service._select_execution_results(executions, ["count", "list"])

    assert [execution.candidate_id for execution in selected] == ["c1", "c2"]


def test_neo4j_query_runtime_dynamic_validation_uses_live_schema():
    original_metadata = neo4j_query_runtime_service.live_schema_metadata
    original_mode = settings.graphrag_schema_strict_mode
    try:
        settings.graphrag_schema_strict_mode = "dynamic"
        neo4j_query_runtime_service.live_schema_metadata = lambda refresh=False, ttl_seconds=300: {
            "labels": ["Person", "Order"],
            "relationships": ["PLACED"],
            "properties_by_label": {"Person": ["name"], "Order": ["created_at"]},
            "time_field_candidates": ["created_at"],
        }

        assert neo4j_query_runtime_service.validate_readonly_query(
            "MATCH (p:Person)-[:PLACED]->(o:Order) RETURN p.name AS name LIMIT 10"
        ) is None
        assert "unsupported" in str(
            neo4j_query_runtime_service.validate_readonly_query(
                "MATCH (p:User)-[:PLACED]->(o:Order) RETURN p.name AS name LIMIT 10"
            )
        )
    finally:
        settings.graphrag_schema_strict_mode = original_mode
        neo4j_query_runtime_service.live_schema_metadata = original_metadata


def test_graphrag_v2_schema_provider_detects_generic_profile_without_servicenow_hints():
    original_mode = settings.graphrag_domain_hint_mode
    try:
        settings.graphrag_domain_hint_mode = "auto"
        provider = GraphRAGv2SchemaProvider(ttl_seconds=300)
        profile = provider._graph_profile(
            [GraphRAGv2NodeType(label="Person", count=2, properties=["name", "created_at"])],
            [GraphRAGv2RelationshipType(from_label="Person", rel_type="KNOWS", to_label="Person")],
        )
        hints_enabled = provider._service_now_hints_enabled(profile)
        hints = provider._module_hints(
            [GraphRAGv2NodeType(label="Person", count=2, properties=["name", "created_at"])],
            [GraphRAGv2RelationshipType(from_label="Person", rel_type="KNOWS", to_label="Person")],
            profile,
            hints_enabled,
            ["created_at"],
        )

        assert profile == "generic_business"
        assert hints_enabled is False
        assert any("not being treated as ServiceNow-specific" in hint for hint in hints)
    finally:
        settings.graphrag_domain_hint_mode = original_mode


def test_graphrag_v2_legacy_fallback_allowed_only_for_servicenow_like_snapshots_by_default():
    generic_snapshot = GraphRAGv2SchemaSnapshot(
        snapshot_id="generic",
        generated_at="2026-05-13T00:00:00+00:00",
        summary_text="generic graph",
        graph_profile="generic_business",
        node_types=[],
        relationships=[],
        allowed_labels=["Person"],
        allowed_relationships=["KNOWS"],
        time_field_candidates=["created_at"],
        service_now_hints_enabled=False,
        module_hints=[],
    )
    servicenow_snapshot = GraphRAGv2SchemaSnapshot(
        snapshot_id="snow",
        generated_at="2026-05-13T00:00:00+00:00",
        summary_text="snow graph",
        graph_profile="servicenow_like",
        node_types=[],
        relationships=[],
        allowed_labels=["User", "Ticket"],
        allowed_relationships=["REQUESTED"],
        time_field_candidates=["opened_at"],
        service_now_hints_enabled=True,
        module_hints=[],
    )

    assert graphrag_v2_service._should_allow_legacy_fallback(generic_snapshot) is False
    assert graphrag_v2_service._should_allow_legacy_fallback(servicenow_snapshot) is True


def test_graphrag_v2_ontology_inference_detects_generic_roles():
    planner = GraphRAGv2OntologyPlanner()
    snapshot = GraphRAGv2SchemaSnapshot(
        snapshot_id="graph_1",
        generated_at="2026-05-13T00:00:00+00:00",
        summary_text="graph",
        graph_profile="generic_business",
        node_types=[
            GraphRAGv2NodeType(label="Appliance", count=5, properties=["name", "location", "vendor", "sys_id"]),
            GraphRAGv2NodeType(label="Incident", count=10, properties=["short_description", "opened_at", "state", "resolution_notes"]),
            GraphRAGv2NodeType(label="Problem", count=4, properties=["short_description", "workaround", "fix_notes", "cause_notes"]),
            GraphRAGv2NodeType(label="ServiceRequest", count=3, properties=["number", "state", "description", "opened_at"]),
        ],
        relationships=[
            GraphRAGv2RelationshipType(from_label="Appliance", rel_type="HAS_INCIDENT", to_label="Incident"),
            GraphRAGv2RelationshipType(from_label="Incident", rel_type="LINKED_TO_PROBLEM", to_label="Problem"),
            GraphRAGv2RelationshipType(from_label="Incident", rel_type="TRIGGERED_SR", to_label="ServiceRequest"),
            GraphRAGv2RelationshipType(from_label="ServiceRequest", rel_type="INVOLVES_APPLIANCE", to_label="Appliance"),
        ],
        allowed_labels=["Appliance", "Incident", "Problem", "ServiceRequest"],
        allowed_relationships=["HAS_INCIDENT", "LINKED_TO_PROBLEM", "TRIGGERED_SR", "INVOLVES_APPLIANCE"],
        time_field_candidates=["opened_at"],
        service_now_hints_enabled=False,
        module_hints=[],
    )

    profile = planner.infer_profile(snapshot)

    assert profile.entity_labels == ["Appliance"]
    assert profile.event_labels == ["Incident", "ServiceRequest"]
    assert profile.problem_labels == ["Problem"]
    assert profile.request_labels == ["ServiceRequest"]
    assert "entity_to_event" in profile.role_paths
    assert "event_to_problem" in profile.role_paths


def test_graphrag_v2_ontology_detects_recommendation_family_and_required_fields():
    planner = GraphRAGv2OntologyPlanner()
    profile = planner.infer_profile(
        GraphRAGv2SchemaSnapshot(
            snapshot_id="graph_2",
            generated_at="2026-05-13T00:00:00+00:00",
            summary_text="graph",
            graph_profile="generic_business",
            node_types=[
                GraphRAGv2NodeType(label="Problem", count=4, properties=["short_description", "workaround", "fix_notes", "cause_notes"]),
            ],
            relationships=[],
            allowed_labels=["Problem"],
            allowed_relationships=[],
            time_field_candidates=[],
            service_now_hints_enabled=False,
            module_hints=[],
        )
    )

    family, required_fields, reason = planner.detect_query_family(
        "What is the triage or troubleshooting recommendation based on the linked problems?",
        profile,
    )

    assert family == "recommendation_from_problem_evidence"
    assert "workaround" in required_fields
    assert "guidance" in reason or "recommendation" in reason


def test_graphrag_v2_ontology_maps_recommendation_search_fields_from_live_schema():
    planner = GraphRAGv2OntologyPlanner()
    profile = planner.infer_profile(
        GraphRAGv2SchemaSnapshot(
            snapshot_id="graph_search_fields",
            generated_at="2026-05-13T00:00:00+00:00",
            summary_text="graph",
            graph_profile="generic_business",
            node_types=[
                GraphRAGv2NodeType(
                    label="Incident",
                    count=2,
                    properties=["short_description", "resolution_notes", "state"],
                ),
                GraphRAGv2NodeType(
                    label="Problem",
                    count=1,
                    properties=["short_description", "workaround", "fix_notes", "category"],
                ),
                GraphRAGv2NodeType(
                    label="ServiceRequest",
                    count=1,
                    properties=["description", "resolution_notes", "priority"],
                ),
            ],
            relationships=[],
            allowed_labels=["Incident", "Problem", "ServiceRequest"],
            allowed_relationships=[],
            time_field_candidates=[],
            service_now_hints_enabled=False,
            module_hints=[],
        )
    )

    search_fields = planner.intent_search_fields("recommendation_from_problem_evidence", profile)

    assert search_fields["Incident"] == ["short_description", "resolution_notes"]
    assert search_fields["Problem"] == ["short_description", "workaround", "fix_notes"]
    assert search_fields["ServiceRequest"] == ["description", "resolution_notes"]
    assert "state" not in search_fields["Incident"]
    assert "category" not in search_fields["Problem"]


def test_graphrag_v2_recommendation_hints_include_useful_fields_and_aliases():
    planner = GraphRAGv2OntologyPlanner()
    profile = planner.infer_profile(
        GraphRAGv2SchemaSnapshot(
            snapshot_id="graph_hint_fields",
            generated_at="2026-05-13T00:00:00+00:00",
            summary_text="graph",
            graph_profile="generic_business",
            node_types=[
                GraphRAGv2NodeType(
                    label="Incident",
                    count=2,
                    properties=["short_description", "description", "resolution_notes", "close_notes"],
                ),
                GraphRAGv2NodeType(
                    label="Problem",
                    count=1,
                    properties=[
                        "short_description",
                        "description",
                        "cause_notes",
                        "workaround",
                        "resolution_notes",
                        "fix_notes",
                    ],
                ),
                GraphRAGv2NodeType(
                    label="ServiceRequest",
                    count=1,
                    properties=["short_description", "description", "resolution_notes"],
                ),
            ],
            relationships=[],
            allowed_labels=["Incident", "Problem", "ServiceRequest"],
            allowed_relationships=[],
            time_field_candidates=[],
            service_now_hints_enabled=False,
            module_hints=[],
        )
    )

    hints = planner.build_planning_hints(
        "recommendation_from_problem_evidence",
        profile,
        ["workaround", "resolution_notes", "fix_notes", "cause_notes"],
    )
    joined = "\n".join(hints)

    assert "Incident: short_description, description, resolution_notes, close_notes" in joined
    assert "Problem: short_description, description, cause_notes, workaround, resolution_notes, fix_notes" in joined
    assert "ServiceRequest: short_description, description, resolution_notes" in joined
    assert "battery backup unit may appear as BBU" in joined
    assert "degradation may appear as degraded, failed, or failure" in joined


def test_graphrag_v2_cypher_prompt_instructs_intent_field_search():
    generator = GraphRAGv2CypherGenerator()
    snapshot = GraphRAGv2SchemaSnapshot(
        snapshot_id="graph_prompt_fields",
        generated_at="2026-05-13T00:00:00+00:00",
        summary_text="Labels: Incident(short_description, description), Problem(short_description, workaround)",
        graph_profile="generic_business",
        node_types=[],
        relationships=[],
        allowed_labels=["Incident", "Problem"],
        allowed_relationships=["LINKED_TO_PROBLEM"],
        time_field_candidates=[],
        service_now_hints_enabled=False,
        module_hints=[],
    )

    prompt = generator._build_prompt(
        question="which incidents has triage guidance available for RAID controller battery backup unit degradation",
        schema_snapshot=snapshot,
        desired_shape="list",
        time_window=None,
        max_candidates=2,
        query_family="recommendation_from_problem_evidence",
        planning_hints=[
            "Intent search fields for this family: Incident: short_description, description; Problem: short_description, workaround."
        ],
        required_evidence_fields=["workaround"],
    )

    assert "If Additional planning hints include intent search fields" in prompt
    assert "do not use only one description field" in prompt
    assert "do not require the entire user phrase" in prompt
    assert "return remediation evidence from linked Problem fields" in prompt


def test_graphrag_v2_treats_zero_count_as_grounded_empty():
    execution = GraphRAGv2ExecutionResult(
        candidate_id="c1",
        query_shape="count",
        purpose="count requests",
        cypher_query="MATCH (n) RETURN count(n) AS count",
        parameters={},
        rows=[{"count": 0}],
        row_count=1,
        ok=True,
        error="",
        duration_ms=10.0,
    )

    outcome = graphrag_v2_service._execution_outcome(
        execution_results=[execution],
        selected_executions=[],
        required_evidence_fields=[],
    )

    assert outcome == "grounded_empty"


def test_graphrag_v2_prefers_ontology_route_when_recommendation_evidence_is_richer():
    baseline_execution = GraphRAGv2ExecutionResult(
        candidate_id="baseline_1",
        query_shape="rank",
        purpose="baseline",
        cypher_query="MATCH (p:Problem) RETURN p.short_description AS troubleshooting_step",
        parameters={},
        rows=[{"troubleshooting_step": "Problem title only"}],
        row_count=1,
        ok=True,
        error="",
        duration_ms=10.0,
    )
    ontology_execution = GraphRAGv2ExecutionResult(
        candidate_id="ontology_1",
        query_shape="rank",
        purpose="ontology",
        cypher_query="MATCH (p:Problem) RETURN p.workaround AS workaround",
        parameters={},
        rows=[{"workaround": "Clear sessions and enable HA failover"}],
        row_count=1,
        ok=True,
        error="",
        duration_ms=11.0,
    )

    baseline_route = {
        "route_name": "baseline_v2",
        "query_family": "recommendation_from_problem_evidence",
        "required_evidence_fields": ["workaround", "resolution_notes"],
        "selected_executions": [baseline_execution],
        "chosen_execution": baseline_execution,
        "execution_outcome": "grounded_success",
    }
    ontology_route = {
        "route_name": "ontology_v2",
        "query_family": "recommendation_from_problem_evidence",
        "required_evidence_fields": ["workaround", "resolution_notes"],
        "selected_executions": [ontology_execution],
        "chosen_execution": ontology_execution,
        "execution_outcome": "grounded_success",
    }

    selected_route_name, selected_route, reason = graphrag_v2_service._select_route_result(
        baseline_route,
        ontology_route,
        query_family="recommendation_from_problem_evidence",
    )

    assert selected_route_name == "ontology_v2"
    assert selected_route["chosen_execution"].candidate_id == "ontology_1"
    assert "richer required evidence" in reason


def test_graphrag_v2_should_attempt_repair_for_syntax_and_validation_failures():
    assert graphrag_v2_service._should_attempt_repair("SyntaxError: Variable `cisco` not defined")
    assert graphrag_v2_service._should_attempt_repair("unsupported label or relationship: Ticket")
    assert graphrag_v2_service._should_attempt_repair("candidate validation failed")
    assert graphrag_v2_service._should_attempt_repair("") is False


def test_graphrag_v2_embedding_text_builders_ignore_empty_fields():
    service = GraphRAGv2EmbeddingService()

    incident_text = service.build_embedding_text(
        "Incident",
        {
            "short_description": "SSL handshake error",
            "description": "",
            "category": "network",
            "subcategory": None,
            "resolution_notes": "Renew cert",
            "close_notes": "",
        },
    )
    problem_text = service.build_embedding_text(
        "Problem",
        {
            "short_description": "Certificate expiry issue",
            "cause_notes": "No renewal alert",
            "workaround": "Deploy temporary cert",
            "resolution_notes": "",
        },
    )

    assert "short_description: SSL handshake error" in incident_text
    assert "category: network" in incident_text
    assert "description:" not in incident_text
    assert "cause_notes: No renewal alert" in problem_text
    assert "workaround: Deploy temporary cert" in problem_text


def test_graphrag_v2_embedding_hash_staleness_checks():
    service = GraphRAGv2EmbeddingService()
    text = "short_description: SSL handshake error"
    text_hash = service.compute_text_hash(text)

    assert service.should_reembed(
        {"has_embedding": False, "embedding_text_hash": "", "embedding_model": ""},
        text_hash,
        "text-embedding-3-small",
    ) is True
    assert service.should_reembed(
        {"has_embedding": True, "embedding_text_hash": text_hash, "embedding_model": "text-embedding-3-small"},
        text_hash,
        "text-embedding-3-small",
    ) is False
    assert service.should_reembed(
        {"has_embedding": True, "embedding_text_hash": "stale", "embedding_model": "text-embedding-3-small"},
        text_hash,
        "text-embedding-3-small",
    ) is True


def test_neo4j_query_runtime_vector_lookup_rejects_unsupported_index():
    original_driver = neo4j_query_runtime_service._driver
    try:
        neo4j_query_runtime_service._driver = object()
        result = neo4j_query_runtime_service.execute_graphrag_vector_lookup(
            index_name="not_allowed_idx",
            embedding=[0.1, 0.2, 0.3],
            top_k=5,
        )
    finally:
        neo4j_query_runtime_service._driver = original_driver

    assert result["ok"] is False
    assert "Unsupported GraphRAG vector index" in str(result["error"])


def test_graphrag_v2_embedding_ranking_prioritizes_problem_and_linkage_signals():
    ranked = GraphRAGv2EmbeddingService._rank_hybrid_candidates(
        [
            _HybridCandidate(
                label="Incident",
                sys_id="A",
                number="INC1",
                short_description="A",
                description="",
                opened_at="",
                state="",
                vector_score=0.99,
                lexical_score=0.9,
                shared_problem_score=0,
                linkage_score=0,
            ),
            _HybridCandidate(
                label="Problem",
                sys_id="B",
                number="PRB1",
                short_description="B",
                description="",
                opened_at="",
                state="",
                vector_score=0.7,
                lexical_score=0.3,
                shared_problem_score=2,
                linkage_score=0,
            ),
            _HybridCandidate(
                label="ServiceRequest",
                sys_id="C",
                number="RITM1",
                short_description="C",
                description="",
                opened_at="",
                state="",
                vector_score=0.8,
                lexical_score=0.4,
                shared_problem_score=0,
                linkage_score=3,
            ),
        ]
    )

    assert ranked[0].sys_id == "B"
    assert ranked[1].sys_id == "C"
    assert ranked[2].sys_id == "A"


def test_graphrag_v2_embedding_retrieval_falls_back_when_embeddings_unconfigured():
    service = GraphRAGv2EmbeddingService()
    original_vector_enabled = settings.graphrag_vector_enabled
    original_base_url = settings.ollama_base_url
    try:
        settings.graphrag_vector_enabled = True
        settings.ollama_base_url = ""
        rows = service.retrieve_hybrid_similarity_candidates("similar issue for SSL errors")
    finally:
        settings.graphrag_vector_enabled = original_vector_enabled
        settings.ollama_base_url = original_base_url

    assert rows == []
