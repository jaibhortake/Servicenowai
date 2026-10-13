from __future__ import annotations

from typing import Dict

from .types import GraphModuleAdapter, GraphModuleResolverSpec


def get_graphrag_module_registry() -> Dict[str, GraphModuleAdapter]:
    return {
        "users": GraphModuleAdapter(
            module_name="users",
            entity_types=["User"],
            relationship_paths=[
                "(:User)-[:REQUESTED]->(:Ticket)",
                "(:Ticket {requested_for_name, requested_by_name})",
            ],
            resolver_specs=[
                GraphModuleResolverSpec(
                    entity_type="user",
                    resolver_fields=["User.name", "Ticket.requested_for_name", "Ticket.requested_by_name"],
                    resolver_mode="lexical_plus_embedding",
                )
            ],
            query_families=[
                "requester_measure",
                "grouped_requester_summary",
                "threshold_requester_aggregate",
                "requester_item_set",
            ],
            output_section_types=["count_metric", "list_scope"],
            template_keys=[
                "requester_measure_count",
                "requester_measure_list",
                "grouped_requester_summary",
                "threshold_requester_aggregate",
            ],
            evidence_shape="requester_rows",
        ),
        "catalog": GraphModuleAdapter(
            module_name="catalog",
            entity_types=["CatalogItem"],
            relationship_paths=[
                "(:RequestItem)-[:FULFILLS_CATALOG_ITEM]->(:CatalogItem)",
                "(:CatalogItem {name, display_name, category_name, search_text})",
            ],
            resolver_specs=[
                GraphModuleResolverSpec(
                    entity_type="catalog_item",
                    resolver_fields=[
                        "CatalogItem.name",
                        "CatalogItem.display_name",
                        "CatalogItem.category_name",
                        "CatalogItem.search_text",
                    ],
                    resolver_mode="lexical_plus_embedding",
                )
            ],
            query_families=[
                "item_requester_list",
                "cohort_expansion",
                "catalog_form_lookup",
            ],
            output_section_types=["count_metric", "list_scope"],
            template_keys=[
                "item_requester_list",
                "cohort_expansion",
                "catalog_form_lookup",
            ],
            evidence_shape="item_rows",
        ),
        "service_requests": GraphModuleAdapter(
            module_name="service_requests",
            entity_types=["Ticket", "ServiceRequest"],
            relationship_paths=[
                "(:User)-[:REQUESTED]->(:Ticket {ticket_type:'SR'})",
                "(:Ticket)-[:HAS_ITEM]->(:RequestItem)",
            ],
            resolver_specs=[
                GraphModuleResolverSpec(
                    entity_type="service_request",
                    resolver_fields=["Ticket.number", "Ticket.state", "Ticket.state_display", "Ticket.opened_at"],
                    resolver_mode="lexical",
                ),
                GraphModuleResolverSpec(
                    entity_type="ticket_state",
                    resolver_fields=["Ticket.state", "Ticket.state_display"],
                    resolver_mode="lexical",
                ),
                GraphModuleResolverSpec(
                    entity_type="time_window",
                    resolver_fields=["Ticket.opened_at"],
                    resolver_mode="lexical",
                ),
            ],
            query_families=[
                "requester_measure",
                "requester_request_list",
                "requester_metric_breakdown",
                "grouped_requester_summary",
                "threshold_requester_aggregate",
            ],
            output_section_types=["count_metric", "list_scope"],
            template_keys=[
                "requester_measure_count",
                "requester_measure_list",
                "requester_metric_breakdown",
                "grouped_requester_summary",
                "threshold_requester_aggregate",
            ],
            evidence_shape="ticket_rows",
        ),
        "request_items": GraphModuleAdapter(
            module_name="request_items",
            entity_types=["RequestItem"],
            relationship_paths=[
                "(:Ticket)-[:HAS_ITEM]->(:RequestItem)",
                "(:RequestItem {number, cat_item_name, short_description, description})",
            ],
            resolver_specs=[
                GraphModuleResolverSpec(
                    entity_type="request_item",
                    resolver_fields=[
                        "RequestItem.number",
                        "RequestItem.cat_item_name",
                        "RequestItem.short_description",
                        "RequestItem.description",
                    ],
                    resolver_mode="lexical_plus_embedding",
                )
            ],
            query_families=[
                "requester_request_list",
                "requester_metric_breakdown",
                "item_requester_list",
                "cohort_expansion",
            ],
            output_section_types=["count_metric", "list_scope"],
            template_keys=[
                "requester_measure_list",
                "requester_metric_breakdown",
                "item_requester_list",
                "cohort_expansion",
            ],
            evidence_shape="ticket_rows",
        ),
        "change_requests": GraphModuleAdapter(
            module_name="change_requests",
            entity_types=["Ticket", "ChangeRequest"],
            relationship_paths=[
                "(:User)-[:REQUESTED]->(:Ticket {ticket_type:'CH'})",
                "(:Ticket {state, state_display, number, opened_at})",
            ],
            resolver_specs=[
                GraphModuleResolverSpec(
                    entity_type="change_request",
                    resolver_fields=["Ticket.number", "Ticket.state", "Ticket.state_display", "Ticket.opened_at"],
                    resolver_mode="lexical",
                ),
                GraphModuleResolverSpec(
                    entity_type="ticket_state",
                    resolver_fields=["Ticket.state", "Ticket.state_display"],
                    resolver_mode="lexical",
                ),
            ],
            query_families=[
                "requester_measure",
                "requester_change_count",
                "requester_change_state_breakdown",
            ],
            output_section_types=["count_metric", "list_scope"],
            template_keys=[
                "requester_measure_count",
                "requester_change_count",
                "requester_change_state_breakdown",
            ],
            evidence_shape="ticket_rows",
        ),
    }
