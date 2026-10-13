import asyncio
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

from app.config import settings
from app.services import servicenow as snow
from app.services.catalog_graph_sync_service import sync_catalog_item_to_graph
from app.services.ticket_service import sync_ticket_to_graph

logger = logging.getLogger(__name__)

WEBHOOK_SECRET_HEADERS = (
    "x-servicenow-webhook-secret",
    "x-webhook-secret",
    "x-mcp-webhook-secret",
)
WEBHOOK_SIGNATURE_HEADERS = (
    "x-servicenow-signature",
    "x-webhook-signature",
    "x-mcp-webhook-signature",
)

CATALOG_GRAPH_TABLES = {
    "sc_cat_item",
    "item_option_new",
    "question_choice",
    "catalog_ui_policy",
    "catalog_ui_policy_action",
}
SERVICE_REQUEST_TABLES = {"sc_request"}
REQUEST_ITEM_TABLES = {"sc_req_item"}
CHANGE_REQUEST_TABLES = {"change_request"}
INCIDENT_TABLES = {"incident"}
SUPPORTED_TABLES = (
    CATALOG_GRAPH_TABLES
    | SERVICE_REQUEST_TABLES
    | REQUEST_ITEM_TABLES
    | CHANGE_REQUEST_TABLES
    | INCIDENT_TABLES
)


@dataclass(frozen=True)
class ServiceNowWebhookEvent:
    table: str
    sys_id: str
    operation: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    dedupe_key: str = ""


def validate_servicenow_webhook_auth(
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: Optional[str] = None,
) -> bool:
    webhook_secret = (secret if secret is not None else settings.servicenow_webhook_secret).strip()
    if not webhook_secret:
        return False

    for header_name in WEBHOOK_SECRET_HEADERS:
        header_value = _get_header(headers, header_name)
        if header_value and hmac.compare_digest(header_value.strip(), webhook_secret):
            return True

    expected_signature = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    for header_name in WEBHOOK_SIGNATURE_HEADERS:
        signature = _normalize_signature_header(_get_header(headers, header_name))
        if signature and hmac.compare_digest(signature, expected_signature):
            return True

    return False


def normalize_servicenow_webhook_payload(
    payload: Mapping[str, Any],
    table_hint: str = "",
) -> ServiceNowWebhookEvent:
    raw = dict(payload or {})
    nested_payload = _first_mapping(raw, ("payload", "record", "current", "data", "resource"))
    record_payload = nested_payload or raw
    candidate_maps = (raw, record_payload)

    table = _normalize_table_name(
        _first_text(candidate_maps, ("table", "table_name", "source_table", "sys_table_name"))
        or table_hint
    )
    sys_id = _first_ref_value(
        candidate_maps,
        ("sys_id", "record_sys_id", "document_id", "target_sys_id"),
    )
    operation = _normalize_operation(
        _first_text(candidate_maps, ("operation", "action", "event", "webhook_action"))
    )
    timestamp = (
        _first_text(candidate_maps, ("timestamp", "sys_updated_on", "updated_on", "event_time"))
        or _utc_now()
    )
    dedupe_source = _first_text(candidate_maps, ("dedupe_key", "event_id", "message_id")) or timestamp
    dedupe_key = ":".join(part for part in (table, sys_id, operation, str(dedupe_source)) if part)

    return ServiceNowWebhookEvent(
        table=table,
        sys_id=sys_id,
        operation=operation,
        payload=record_payload,
        timestamp=str(timestamp),
        dedupe_key=dedupe_key,
    )


class ServiceNowWebhookGraphSyncService:
    def __init__(self) -> None:
        self.received = 0
        self.accepted = 0
        self.rejected = 0
        self.synced = 0
        self.failed = 0
        self.ignored = 0
        self.duplicate = 0
        self.last_event_time: Optional[str] = None
        self.last_error: Optional[str] = None
        self._dedupe_seen_at: Dict[str, float] = {}

    async def handle_webhook(
        self,
        payload: Mapping[str, Any],
        table_hint: str = "",
    ) -> Dict[str, Any]:
        self.received += 1
        self.last_event_time = _utc_now()
        event = normalize_servicenow_webhook_payload(payload, table_hint=table_hint)

        if not event.table or not event.sys_id:
            self.rejected += 1
            self.last_error = "Webhook payload is missing table or sys_id"
            return {
                "success": False,
                "status": "rejected",
                "reason": self.last_error,
                "event": _event_summary(event),
            }

        self.accepted += 1
        if self._is_duplicate(event.dedupe_key):
            self.duplicate += 1
            return {
                "success": True,
                "status": "duplicate",
                "event": _event_summary(event),
            }

        if event.table not in SUPPORTED_TABLES:
            self.ignored += 1
            return {
                "success": True,
                "status": "ignored",
                "reason": f"Unsupported ServiceNow table: {event.table}",
                "event": _event_summary(event),
            }

        if event.operation == "delete":
            self.ignored += 1
            return {
                "success": True,
                "status": "ignored",
                "reason": "Delete webhooks are accepted but non-destructive graph delete/marking is not implemented in v1",
                "event": _event_summary(event),
            }

        try:
            result = await self._sync_event(event)
        except Exception as exc:
            logger.exception("Failed to process ServiceNow webhook event")
            self.failed += 1
            self.last_error = str(exc)
            return {
                "success": False,
                "status": "failed",
                "reason": str(exc),
                "event": _event_summary(event),
            }

        if result.get("synced"):
            self.synced += 1
            return {
                "success": True,
                "status": "synced",
                **result,
                "event": _event_summary(event),
            }

        self.failed += 1
        self.last_error = str(result.get("reason") or "Webhook graph sync returned false")
        return {
            "success": False,
            "status": "failed",
            **result,
            "event": _event_summary(event),
        }

    def record_rejected(self, reason: str) -> None:
        self.received += 1
        self.rejected += 1
        self.last_event_time = _utc_now()
        self.last_error = reason

    def status(self) -> Dict[str, Any]:
        return {
            "received": self.received,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "synced": self.synced,
            "failed": self.failed,
            "ignored": self.ignored,
            "duplicate": self.duplicate,
            "last_event_time": self.last_event_time,
            "last_error": self.last_error,
            "supported_tables": sorted(SUPPORTED_TABLES),
        }

    def reset_for_tests(self) -> None:
        self.received = 0
        self.accepted = 0
        self.rejected = 0
        self.synced = 0
        self.failed = 0
        self.ignored = 0
        self.duplicate = 0
        self.last_event_time = None
        self.last_error = None
        self._dedupe_seen_at.clear()

    async def _sync_event(self, event: ServiceNowWebhookEvent) -> Dict[str, Any]:
        if event.table in CATALOG_GRAPH_TABLES:
            return await self._sync_catalog_event(event)
        if event.table in SERVICE_REQUEST_TABLES:
            synced = await asyncio.to_thread(sync_ticket_to_graph, event.sys_id, "SR")
            return {
                "synced": bool(synced),
                "sync_target": "service_request_ticket_graph",
                "reason": None if synced else "Service request graph sync returned false",
            }
        if event.table in REQUEST_ITEM_TABLES:
            return await self._sync_request_item_event(event)
        if event.table in CHANGE_REQUEST_TABLES:
            synced = await asyncio.to_thread(sync_ticket_to_graph, event.sys_id, "CH")
            return {
                "synced": bool(synced),
                "sync_target": "change_request_ticket_graph",
                "reason": None if synced else "Change request graph sync returned false",
            }
        if event.table in INCIDENT_TABLES:
            synced = await asyncio.to_thread(sync_ticket_to_graph, event.sys_id, "INC")
            return {
                "synced": bool(synced),
                "sync_target": "incident_ticket_graph",
                "reason": None if synced else "Incident graph sync returned false",
            }
        return {"synced": False, "reason": f"Unsupported ServiceNow table: {event.table}"}

    async def _sync_catalog_event(self, event: ServiceNowWebhookEvent) -> Dict[str, Any]:
        catalog_item_sys_id = await self._resolve_catalog_item_sys_id(event)
        if not catalog_item_sys_id:
            return {
                "synced": False,
                "sync_target": "catalog_graph",
                "reason": f"Could not resolve parent catalog item for {event.table}:{event.sys_id}",
            }

        synced = await asyncio.to_thread(sync_catalog_item_to_graph, catalog_item_sys_id, True)
        return {
            "synced": bool(synced),
            "sync_target": "catalog_graph",
            "catalog_item_sys_id": catalog_item_sys_id,
            "reason": None if synced else "Catalog graph sync returned false",
        }

    async def _sync_request_item_event(self, event: ServiceNowWebhookEvent) -> Dict[str, Any]:
        request_sys_id = _extract_ref_value(event.payload.get("request"))
        if not request_sys_id:
            ritm = await asyncio.to_thread(snow.get_request_item, event.sys_id)
            request_sys_id = _extract_ref_value((ritm or {}).get("request"))

        if not request_sys_id:
            return {
                "synced": False,
                "sync_target": "service_request_ticket_graph",
                "reason": f"Could not resolve parent request for sc_req_item:{event.sys_id}",
            }

        synced = await asyncio.to_thread(sync_ticket_to_graph, request_sys_id, "SR")
        return {
            "synced": bool(synced),
            "sync_target": "service_request_ticket_graph",
            "request_sys_id": request_sys_id,
            "reason": None if synced else "Service request graph sync returned false",
        }

    async def _resolve_catalog_item_sys_id(self, event: ServiceNowWebhookEvent) -> Optional[str]:
        if event.table == "sc_cat_item":
            return event.sys_id

        direct = _first_ref_value(
            (event.payload,),
            ("cat_item", "catalog_item", "sc_cat_item", "item"),
        )
        if direct:
            return direct

        if event.table == "item_option_new":
            variable = await asyncio.to_thread(snow.get_table_record, "item_option_new", event.sys_id)
            return _extract_ref_value((variable or {}).get("cat_item"))

        if event.table == "question_choice":
            question_sys_id = _extract_ref_value(event.payload.get("question"))
            if not question_sys_id:
                choice = await asyncio.to_thread(snow.get_table_record, "question_choice", event.sys_id)
                question_sys_id = _extract_ref_value((choice or {}).get("question"))
            if question_sys_id:
                variable = await asyncio.to_thread(snow.get_table_record, "item_option_new", question_sys_id)
                return _extract_ref_value((variable or {}).get("cat_item"))

        if event.table == "catalog_ui_policy":
            policy = await asyncio.to_thread(snow.get_table_record, "catalog_ui_policy", event.sys_id)
            return _extract_ref_value((policy or {}).get("catalog_item"))

        if event.table == "catalog_ui_policy_action":
            action = event.payload
            if not any(action.get(key) for key in ("catalog_variable", "ui_policy")):
                action = await asyncio.to_thread(snow.get_table_record, "catalog_ui_policy_action", event.sys_id) or {}

            variable_sys_id = _extract_ref_value(action.get("catalog_variable"))
            if variable_sys_id:
                variable_sys_id = variable_sys_id.replace("IO:", "").strip()
                variable = await asyncio.to_thread(snow.get_table_record, "item_option_new", variable_sys_id)
                catalog_item_sys_id = _extract_ref_value((variable or {}).get("cat_item"))
                if catalog_item_sys_id:
                    return catalog_item_sys_id

            policy_sys_id = _extract_ref_value(action.get("ui_policy"))
            if policy_sys_id:
                policy = await asyncio.to_thread(snow.get_table_record, "catalog_ui_policy", policy_sys_id)
                return _extract_ref_value((policy or {}).get("catalog_item"))

        return None

    def _is_duplicate(self, dedupe_key: str) -> bool:
        if not dedupe_key:
            return False

        now = time.time()
        window = max(int(settings.servicenow_webhook_dedupe_seconds or 0), 0)
        if window:
            expired = [key for key, seen_at in self._dedupe_seen_at.items() if now - seen_at > window]
            for key in expired:
                self._dedupe_seen_at.pop(key, None)

        if dedupe_key in self._dedupe_seen_at:
            return True

        self._dedupe_seen_at[dedupe_key] = now
        return False


def _normalize_signature_header(value: Optional[str]) -> str:
    signature = str(value or "").strip()
    if signature.lower().startswith("sha256="):
        signature = signature.split("=", 1)[1].strip()
    return signature.lower()


def _get_header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value or "")
    return ""


def _first_mapping(source: Mapping[str, Any], keys: Sequence[str]) -> Dict[str, Any]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _first_text(sources: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> str:
    for source in sources:
        for key in keys:
            value = source.get(key)
            extracted = _extract_ref_value(value)
            if extracted:
                return extracted
    return ""


def _first_ref_value(sources: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> str:
    return _first_text(sources, keys)


def _extract_ref_value(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("value", "sys_id", "record_sys_id", "display_value"):
            nested = value.get(key)
            if nested not in (None, ""):
                return str(nested).strip()
        return ""
    if value in (None, ""):
        return ""
    return str(value).strip()


def _normalize_table_name(table: str) -> str:
    return str(table or "").strip().lower()


def _normalize_operation(operation: str) -> str:
    normalized = str(operation or "update").strip().lower()
    if normalized in {"delete", "deleted", "remove", "removed"}:
        return "delete"
    if normalized in {"insert", "inserted", "create", "created"}:
        return "insert"
    return "update"


def _event_summary(event: ServiceNowWebhookEvent) -> Dict[str, str]:
    return {
        "table": event.table,
        "sys_id": event.sys_id,
        "operation": event.operation,
        "timestamp": event.timestamp,
        "dedupe_key": event.dedupe_key,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


servicenow_webhook_graph_sync_service = ServiceNowWebhookGraphSyncService()
