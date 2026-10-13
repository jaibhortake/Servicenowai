import asyncio

from app.models.ticket import AddCommentRequest, TicketResponse, UpdateTicketRequest
from app.routers import comments as comments_router
from app.routers import tickets as tickets_router
from app.services import ticket_service


def test_sync_ticket_to_graph_supports_incidents(monkeypatch):
    calls = []

    monkeypatch.setattr(ticket_service.snow, "get_incident", lambda sys_id: {"sys_id": sys_id, "number": "INC001"})
    monkeypatch.setattr(ticket_service, "_sync_new_incident_to_graph", lambda incident: calls.append(incident["sys_id"]) or True)

    assert ticket_service.sync_ticket_to_graph("inc-sys-1", "INC") is True
    assert calls == ["inc-sys-1"]


def test_add_comment_routes_incidents_and_syncs_graph(monkeypatch):
    add_calls = []
    sync_calls = []

    monkeypatch.setattr(
        comments_router.snow,
        "add_comment",
        lambda table, sys_id, body, is_work_note=False: add_calls.append((table, sys_id, body, is_work_note)),
    )
    monkeypatch.setattr(
        comments_router.ticket_service,
        "sync_ticket_to_graph",
        lambda sys_id, ticket_type: sync_calls.append((sys_id, ticket_type)) or True,
    )

    req = AddCommentRequest(
        ticket_sys_id="inc-sys-1",
        ticket_table="INC",
        comment="Investigating from dashboard",
        is_work_note=True,
    )

    result = asyncio.run(comments_router.add_comment("inc-sys-1", req))

    assert result == {"status": "ok"}
    assert add_calls == [("incident", "inc-sys-1", "Investigating from dashboard", True)]
    assert sync_calls == [("inc-sys-1", "INC")]


def test_get_comments_routes_incidents(monkeypatch):
    monkeypatch.setattr(
        comments_router.snow,
        "get_comments",
        lambda table, sys_id: [{"value": "note"}] if table == "incident" and sys_id == "inc-sys-1" else [],
    )

    result = asyncio.run(comments_router.get_comments("inc-sys-1", "INC"))

    assert result == [{"value": "note"}]


def test_update_ticket_router_supports_incident_status_updates(monkeypatch):
    expected = TicketResponse(
        sys_id="inc-sys-1",
        number="INC0012345",
        short_description="Updated incident",
        state="Resolved",
        ticket_type="INC",
        raw={"number": "INC0012345"},
    )

    monkeypatch.setattr(
        tickets_router.ticket_service,
        "update_ticket",
        lambda sys_id, ticket_type, payload: expected
        if sys_id == "inc-sys-1" and ticket_type == "INC" and payload["incident_state"] == "6"
        else None,
    )

    result = asyncio.run(
        tickets_router.update_ticket(
            "INC",
            "inc-sys-1",
            UpdateTicketRequest(incident_state="6"),
        )
    )

    assert result == expected


def test_update_ticket_maps_incident_resolution_fields(monkeypatch):
    patch_calls = []

    monkeypatch.setattr(
        ticket_service.snow,
        "patch_incident",
        lambda sys_id, payload: patch_calls.append((sys_id, payload)) or payload,
    )
    monkeypatch.setattr(
        ticket_service.snow,
        "get_incident",
        lambda sys_id: {
            "sys_id": {"value": sys_id},
            "number": {"value": "INC0012345"},
            "short_description": {"display_value": "Updated incident"},
            "incident_state": {"value": "6", "display_value": "Resolved"},
            "opened_at": {"display_value": "2026-06-04 09:00:00"},
            "assigned_to": {"display_value": "Taylor"},
        },
    )
    monkeypatch.setattr(ticket_service, "_sync_new_incident_to_graph", lambda incident: True)

    result = ticket_service.update_ticket(
        "inc-sys-1",
        "INC",
        {
            "state": "6",
            "close_code": "Solved (Permanently)",
            "close_notes": "Verified the service is healthy again.",
            "resolution_notes": "Restarted the affected service and validated recovery.",
            "root_cause": "Expired certificate on the service endpoint.",
            "workaround": "Route traffic through the backup endpoint until restart completes.",
        },
    )

    assert result is not None
    assert patch_calls == [
        (
            "inc-sys-1",
            {
                "incident_state": "6",
                "close_code": "Solved (Permanently)",
                "close_notes": "Verified the service is healthy again.",
                "u_solution": "Restarted the affected service and validated recovery.",
                "u_root_cause": "Expired certificate on the service endpoint.",
                "u_workaround": "Route traffic through the backup endpoint until restart completes.",
            },
        )
    ]
    assert result.state == "Resolved"


def test_normalize_incident_for_graph_includes_resolution_fields(monkeypatch):
    monkeypatch.setattr(
        ticket_service.snow,
        "get_comments",
        lambda table, sys_id: [{"value": "Customer confirmed the outage", "sys_created_by": "user", "sys_created_on": "2026-06-04 09:00:00"}],
    )
    monkeypatch.setattr(
        ticket_service.snow,
        "get_work_notes",
        lambda table, sys_id: [{"value": "Applied remediation", "sys_created_by": "agent", "sys_created_on": "2026-06-04 09:05:00"}],
    )

    normalized = ticket_service._normalize_incident_for_graph(
        {
            "sys_id": {"value": "inc-sys-1"},
            "number": {"value": "INC0012345"},
            "short_description": {"display_value": "VPN outage"},
            "description": {"display_value": "Users cannot connect"},
            "state": {"value": "1", "display_value": "New"},
            "incident_state": {"value": "6", "display_value": "Resolved"},
            "opened_at": {"display_value": "2026-06-04 08:00:00"},
            "caller_id": {"value": "user-sys-1", "display_value": "Asha"},
            "assigned_to": {"value": "agent-sys-1", "display_value": "Taylor"},
            "priority": {"display_value": "2 - High"},
            "urgency": {"display_value": "1 - High"},
            "impact": {"display_value": "1 - High"},
            "category": {"display_value": "Network"},
            "subcategory": {"display_value": "VPN"},
            "close_code": {"value": "Solved (Permanently)", "display_value": "Solved (Permanently)"},
            "close_notes": {"display_value": "Restarted the gateway and verified access."},
            "resolved_at": {"display_value": "2026-06-04 09:10:00"},
            "resolved_by": {"value": "agent-sys-1", "display_value": "Taylor"},
            "u_solution": {"display_value": "Restarted the gateway and verified access."},
            "u_root_cause": {"display_value": "Gateway process crash"},
            "u_workaround": {"display_value": "Use backup tunnel until restart completes"},
        }
    )

    assert normalized["state"] == "6"
    assert normalized["state_display"] == "Resolved"
    assert normalized["incident_state"] == "6"
    assert normalized["incident_state_display"] == "Resolved"
    assert normalized["close_code"] == "Solved (Permanently)"
    assert normalized["close_notes"] == "Restarted the gateway and verified access."
    assert normalized["resolved_at"] == "2026-06-04 09:10:00"
    assert normalized["resolved_by_sys_id"] == "agent-sys-1"
    assert normalized["resolved_by_name"] == "Taylor"
    assert normalized["resolution_notes"] == "Restarted the gateway and verified access."
    assert normalized["cause_notes"] == "Gateway process crash"
    assert normalized["workaround"] == "Use backup tunnel until restart completes"
    assert "Customer confirmed the outage" in normalized["comments"]
    assert "Applied remediation" in normalized["work_notes"]
