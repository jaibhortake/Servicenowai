import asyncio
import hashlib
import hmac

from app.services import servicenow_webhook_graph_sync as webhook_module
from app.services.servicenow_webhook_graph_sync import (
    ServiceNowWebhookGraphSyncService,
    validate_servicenow_webhook_auth,
)


def _run(coro):
    return asyncio.run(coro)


def test_valid_shared_secret_header_is_accepted():
    assert validate_servicenow_webhook_auth(
        b"{}",
        {"X-ServiceNow-Webhook-Secret": "test-secret"},
        secret="test-secret",
    )


def test_valid_hmac_signature_is_accepted():
    body = b'{"table":"sc_cat_item","sys_id":"cat-1"}'
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

    assert validate_servicenow_webhook_auth(
        body,
        {"X-ServiceNow-Signature": f"sha256={signature}"},
        secret="test-secret",
    )


def test_invalid_webhook_secret_is_rejected():
    assert not validate_servicenow_webhook_auth(
        b"{}",
        {"X-ServiceNow-Webhook-Secret": "wrong"},
        secret="test-secret",
    )


def test_catalog_item_event_maps_to_catalog_graph_sync():
    service = ServiceNowWebhookGraphSyncService()
    calls = []
    original = webhook_module.sync_catalog_item_to_graph

    def fake_sync(sys_id, enrich=True):
        calls.append((sys_id, enrich))
        return True

    webhook_module.sync_catalog_item_to_graph = fake_sync
    try:
        result = _run(service.handle_webhook({
            "table": "sc_cat_item",
            "sys_id": "cat-1",
            "operation": "update",
            "timestamp": "2026-05-11T00:00:00Z",
        }))
    finally:
        webhook_module.sync_catalog_item_to_graph = original

    assert result["status"] == "synced"
    assert result["catalog_item_sys_id"] == "cat-1"
    assert calls == [("cat-1", True)]


def test_catalog_variable_event_uses_parent_catalog_item_from_payload():
    service = ServiceNowWebhookGraphSyncService()
    calls = []
    original = webhook_module.sync_catalog_item_to_graph

    def fake_sync(sys_id, enrich=True):
        calls.append((sys_id, enrich))
        return True

    webhook_module.sync_catalog_item_to_graph = fake_sync
    try:
        result = _run(service.handle_webhook({
            "table": "item_option_new",
            "sys_id": "var-1",
            "operation": "update",
            "payload": {"cat_item": {"value": "cat-1"}},
            "timestamp": "2026-05-11T00:00:00Z",
        }))
    finally:
        webhook_module.sync_catalog_item_to_graph = original

    assert result["status"] == "synced"
    assert result["catalog_item_sys_id"] == "cat-1"
    assert calls == [("cat-1", True)]


def test_service_request_event_maps_to_ticket_graph_sync():
    service = ServiceNowWebhookGraphSyncService()
    calls = []
    original = webhook_module.sync_ticket_to_graph

    def fake_sync(sys_id, ticket_type):
        calls.append((sys_id, ticket_type))
        return True

    webhook_module.sync_ticket_to_graph = fake_sync
    try:
        result = _run(service.handle_webhook({
            "table": "sc_request",
            "sys_id": "req-1",
            "operation": "update",
            "timestamp": "2026-05-11T00:00:00Z",
        }))
    finally:
        webhook_module.sync_ticket_to_graph = original

    assert result["status"] == "synced"
    assert calls == [("req-1", "SR")]


def test_request_item_event_maps_to_parent_service_request_sync():
    service = ServiceNowWebhookGraphSyncService()
    calls = []
    original = webhook_module.sync_ticket_to_graph

    def fake_sync(sys_id, ticket_type):
        calls.append((sys_id, ticket_type))
        return True

    webhook_module.sync_ticket_to_graph = fake_sync
    try:
        result = _run(service.handle_webhook({
            "table": "sc_req_item",
            "sys_id": "ritm-1",
            "operation": "update",
            "payload": {"request": {"value": "req-1"}},
            "timestamp": "2026-05-11T00:00:00Z",
        }))
    finally:
        webhook_module.sync_ticket_to_graph = original

    assert result["status"] == "synced"
    assert result["request_sys_id"] == "req-1"
    assert calls == [("req-1", "SR")]


def test_change_request_event_maps_to_ticket_graph_sync():
    service = ServiceNowWebhookGraphSyncService()
    calls = []
    original = webhook_module.sync_ticket_to_graph

    def fake_sync(sys_id, ticket_type):
        calls.append((sys_id, ticket_type))
        return True

    webhook_module.sync_ticket_to_graph = fake_sync
    try:
        result = _run(service.handle_webhook({
            "table": "change_request",
            "sys_id": "chg-1",
            "operation": "update",
            "timestamp": "2026-05-11T00:00:00Z",
        }))
    finally:
        webhook_module.sync_ticket_to_graph = original

    assert result["status"] == "synced"
    assert calls == [("chg-1", "CH")]


def test_incident_event_maps_to_ticket_graph_sync():
    service = ServiceNowWebhookGraphSyncService()
    calls = []
    original = webhook_module.sync_ticket_to_graph

    def fake_sync(sys_id, ticket_type):
        calls.append((sys_id, ticket_type))
        return True

    webhook_module.sync_ticket_to_graph = fake_sync
    try:
        result = _run(service.handle_webhook({
            "table": "incident",
            "sys_id": "inc-1",
            "operation": "update",
            "timestamp": "2026-05-11T00:00:00Z",
        }))
    finally:
        webhook_module.sync_ticket_to_graph = original

    assert result["status"] == "synced"
    assert calls == [("inc-1", "INC")]


def test_duplicate_event_does_not_process_twice():
    service = ServiceNowWebhookGraphSyncService()
    calls = []
    original = webhook_module.sync_catalog_item_to_graph

    def fake_sync(sys_id, enrich=True):
        calls.append((sys_id, enrich))
        return True

    event = {
        "table": "sc_cat_item",
        "sys_id": "cat-1",
        "operation": "update",
        "timestamp": "2026-05-11T00:00:00Z",
    }

    webhook_module.sync_catalog_item_to_graph = fake_sync
    try:
        first = _run(service.handle_webhook(event))
        second = _run(service.handle_webhook(event))
    finally:
        webhook_module.sync_catalog_item_to_graph = original

    assert first["status"] == "synced"
    assert second["status"] == "duplicate"
    assert calls == [("cat-1", True)]
