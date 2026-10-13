import logging
import time
from typing import Dict, Any, List, Optional
from app.models.ticket import CreateSRRequest, CreateCHRequest, CreateIncidentRequest, TicketResponse
from app.services import servicenow as snow
from app.services.catalog_graph_sync_service import sync_catalog_item_to_graph
from app.services.neo4j_ticket_graph import platform_neo4j_ticket_graph_service as neo4j_ticket_graph_service

logger = logging.getLogger(__name__)


def create_sr(req: CreateSRRequest) -> TicketResponse:
    requested_for = _resolve_requested_for(req.requested_for)

    # Use ServiceNow catalog engine to create SR + RITM with proper variable linkage
    order_result = snow.order_catalog_item(
        catalog_item_sys_id=req.catalog_item_id,
        requested_for=requested_for,
        variables=req.variables,
        quantity=1,
    )

    # Extract request_id from order result
    request_id = order_result.get("request_id")
    if not request_id:
        raise ValueError("order_catalog_item did not return request_id")

    # Fetch the created service request
    sr = snow.get_service_request(request_id)
    if not sr:
        raise ValueError(f"Could not fetch created service request {request_id}")

    sr_sys_id = _extract_value(sr.get("sys_id"))

    # Fetch the created request item (RITM). order_now can be eventually consistent,
    # so give ServiceNow a short retry window before we give up.
    ritm = _fetch_request_item_for_request(sr_sys_id)
    ritm_sys_id = _extract_value((ritm or {}).get("sys_id"))

    if sr_sys_id and req.short_description:
        try:
            patched_sr = snow.patch_service_request(sr_sys_id, {
                "short_description": req.short_description,
                "description": req.description or req.short_description,
                "urgency": req.urgency,
            })
            if patched_sr:
                sr = patched_sr
        except Exception:
            logger.exception("Failed to patch service request %s with submitted description", sr_sys_id)

    # The actual catalog submission most users open in ServiceNow is the RITM.
    # Mirror the user-entered text there so the visible request matches the portal.
    if ritm_sys_id and req.short_description:
        try:
            patched_ritm = snow.patch_request_item(ritm_sys_id, {
                "short_description": req.short_description,
                "description": req.description or req.short_description,
            })
            if patched_ritm:
                ritm = patched_ritm
        except Exception:
            logger.exception("Failed to patch request item %s with submitted description", ritm_sys_id)

    _sync_new_service_request_to_graph(sr, ritm)

    return TicketResponse(
        sys_id=sr_sys_id or "",
        number=_extract_value(sr.get("number")) or "",
        short_description=_extract_display(sr.get("short_description")) or req.short_description,
        state=_extract_display(sr.get("state")) or "1",
        opened_at=_extract_display(sr.get("opened_at")),
        ticket_type="SR",
        ritm_sys_id=ritm_sys_id,
        raw=sr,
    )


def create_ch(req: CreateCHRequest) -> TicketResponse:
    payload = {
        "short_description": req.short_description,
        "description": req.description or req.short_description,
        "risk": req.risk.value,
        "impact": req.impact,
        "type": req.change_type,
        "start_date": req.start_date,
        "end_date": req.end_date,
        "requested_by": req.requested_by,
        "assignment_group": req.assignment_group,
    }
    ch = snow.create_change_request({k: v for k, v in payload.items() if v is not None})
    ch_sys_id = _extract_value(ch.get("sys_id"))

    if ch_sys_id and req.short_description:
        try:
            patched_ch = snow.patch_change_request(ch_sys_id, {
                "short_description": req.short_description,
                "description": req.description or req.short_description,
            })
            if patched_ch:
                ch = patched_ch
        except Exception:
            logger.exception("Failed to patch change request %s with submitted description", ch_sys_id)

    _sync_new_change_request_to_graph(ch)

    return TicketResponse(
        sys_id=_extract_value(ch.get("sys_id")) or "",
        number=_extract_value(ch.get("number")) or "",
        short_description=req.short_description,
        state=_extract_display(ch.get("state")) or "-5",
        opened_at=_extract_display(ch.get("opened_at")),
        ticket_type="CH",
        raw=ch,
    )


def create_incident(req: CreateIncidentRequest) -> TicketResponse:
    payload = {
        "short_description": req.short_description,
        "description": req.description or req.short_description,
        "caller_id": req.caller_id,
        "impact": req.impact,
        "urgency": req.urgency,
        "priority": req.priority,
        "category": req.category,
        "subcategory": req.subcategory,
        "assignment_group": req.assignment_group,
        "cmdb_ci": req.cmdb_ci,
        "work_notes": req.work_notes,
        "comments": req.comments,
    }
    incident = snow.create_incident({k: v for k, v in payload.items() if v not in (None, "")})
    _sync_new_incident_to_graph(incident)
    return TicketResponse(
        sys_id=_extract_value(incident.get("sys_id")) or "",
        number=_extract_value(incident.get("number")) or "",
        short_description=_extract_display(incident.get("short_description")) or req.short_description,
        state=_extract_display(incident.get("state")) or "1",
        opened_at=_extract_display(incident.get("opened_at")),
        assigned_to=_extract_display(incident.get("assigned_to")),
        ticket_type="INC",
        raw=incident,
    )


def update_ticket(sys_id: str, ticket_type: str, payload: Dict[str, Any]) -> Optional[TicketResponse]:
    normalized_type = str(ticket_type or "").upper()
    normalized_sys_id = str(sys_id or "").strip()
    if not normalized_sys_id:
        return None

    update_payload = {k: v for k, v in (payload or {}).items() if v not in (None, "")}
    if not update_payload:
        return get_ticket(normalized_sys_id, normalized_type)

    if normalized_type != "INC":
        raise ValueError("Only incident updates are supported by this endpoint")

    incident_state = update_payload.pop("incident_state", None)
    if incident_state in (None, ""):
        incident_state = update_payload.pop("state", None)
    else:
        update_payload.pop("state", None)
    if incident_state not in (None, ""):
        update_payload["incident_state"] = incident_state

    resolution_notes = update_payload.pop("resolution_notes", None)
    if resolution_notes not in (None, ""):
        update_payload["u_solution"] = resolution_notes

    root_cause = update_payload.pop("root_cause", None)
    if root_cause not in (None, ""):
        update_payload["u_root_cause"] = root_cause

    workaround = update_payload.pop("workaround", None)
    if workaround not in (None, ""):
        update_payload["u_workaround"] = workaround

    snow.patch_incident(normalized_sys_id, update_payload)
    incident = snow.get_incident(normalized_sys_id)
    if not incident:
        return None

    incident_state_field = incident.get("incident_state") or incident.get("state")
    _sync_new_incident_to_graph(incident)
    return TicketResponse(
        sys_id=_extract_value(incident.get("sys_id")) or normalized_sys_id,
        number=_extract_value(incident.get("number")) or "",
        short_description=_extract_display(incident.get("short_description")) or "",
        state=_extract_display(incident_state_field) or "",
        opened_at=_extract_display(incident.get("opened_at")),
        assigned_to=_extract_display(incident.get("assigned_to")),
        ticket_type="INC",
        raw=incident,
    )


def get_ticket(sys_id: str, ticket_type: str) -> Optional[TicketResponse]:
    if ticket_type == "CH":
        data = snow.get_change_request(sys_id)
    elif ticket_type == "INC":
        data = snow.get_incident(sys_id)
    else:
        data = snow.get_service_request(sys_id)

    if not data:
        return None

    state_field = data.get("incident_state") if ticket_type == "INC" else data.get("state")
    state_field = state_field or data.get("state")
    return TicketResponse(
        sys_id=_extract_value(data.get("sys_id")) or sys_id,
        number=_extract_value(data.get("number")) or "",
        short_description=_extract_display(data.get("short_description")) or "",
        state=_extract_display(state_field),
        opened_at=_extract_display(data.get("opened_at")),
        assigned_to=_extract_display(data.get("assigned_to")),
        ticket_type=ticket_type,
        raw=data,
    )


def list_tickets(user_sys_id: Optional[str] = None) -> List[Dict[str, Any]]:
    srs = snow.list_service_requests(user_sys_id=user_sys_id)
    chs = snow.list_change_requests()

    result = []
    for t in srs:
        result.append({**t, "ticket_type": "SR"})
    for t in chs:
        result.append({**t, "ticket_type": "CH"})

    return result


def sync_ticket_to_graph(sys_id: str, ticket_type: str) -> bool:
    ticket_type = str(ticket_type or "").upper()
    if not neo4j_ticket_graph_service.is_available():
        return False

    if ticket_type == "SR":
        sr = snow.get_service_request(sys_id)
        if not sr:
            return False
        ritm = _fetch_request_item_for_request(sys_id)
        return _sync_new_service_request_to_graph(sr, ritm)

    if ticket_type == "CH":
        ch = snow.get_change_request(sys_id)
        if not ch:
            return False
        return _sync_new_change_request_to_graph(ch)

    if ticket_type == "INC":
        incident = snow.get_incident(sys_id)
        if not incident:
            return False
        return _sync_new_incident_to_graph(incident)

    return False


def sync_all_tickets_to_graph(
    batch_size: int = 50,
    max_items: Optional[int] = None,
) -> Dict[str, Any]:
    if not neo4j_ticket_graph_service.is_available():
        return {"synced": 0, "failed": 0, "processed": 0, "complete": False}

    synced = 0
    failed = 0
    processed = 0
    sample_failures: List[Dict[str, str]] = []

    sr_offset = 0
    while True:
        records = snow.list_service_requests_for_graph(limit=batch_size, offset=sr_offset)
        if not records:
            break

        for sr in records:
            processed += 1
            ticket_sys_id = str(_extract_value(sr.get("sys_id")) or "").strip()
            ritm = snow.get_ritm_details_by_request_sysid(ticket_sys_id)
            error_details: Dict[str, str] = {}
            success = neo4j_ticket_graph_service.sync_service_request(
                _normalize_service_request_for_graph(sr, ritm),
                _normalize_request_item_for_graph(ritm) if ritm else None,
                error_details=error_details,
            )
            if success:
                synced += 1
                if ritm and _extract_value(ritm.get("cat_item")):
                    try:
                        sync_catalog_item_to_graph(_extract_value(ritm.get("cat_item")) or "")
                    except Exception:
                        logger.exception(
                            "Failed to sync linked catalog item %s while syncing SR %s",
                            _extract_value(ritm.get("cat_item")) or "unknown",
                            ticket_sys_id or "unknown",
                        )
            else:
                failed += 1
                _record_sync_failure(sample_failures, "SR", ticket_sys_id, error_details.get("message"))

            if max_items is not None and processed >= max_items:
                return _build_sync_result(
                    synced=synced,
                    failed=failed,
                    processed=processed,
                    complete=False,
                    sample_failures=sample_failures,
                )

        if len(records) < batch_size:
            break
        sr_offset += batch_size

    ch_offset = 0
    while True:
        records = snow.list_change_requests_for_graph(limit=batch_size, offset=ch_offset)
        if not records:
            break

        for ch in records:
            processed += 1
            ticket_sys_id = str(_extract_value(ch.get("sys_id")) or "").strip()
            error_details: Dict[str, str] = {}
            success = neo4j_ticket_graph_service.sync_change_request(
                _normalize_change_request_for_graph(ch),
                error_details=error_details,
            )
            if success:
                synced += 1
            else:
                failed += 1
                _record_sync_failure(sample_failures, "CH", ticket_sys_id, error_details.get("message"))

            if max_items is not None and processed >= max_items:
                return _build_sync_result(
                    synced=synced,
                    failed=failed,
                    processed=processed,
                    complete=False,
                    sample_failures=sample_failures,
                )

        if len(records) < batch_size:
            break
        ch_offset += batch_size

    return _build_sync_result(
        synced=synced,
        failed=failed,
        processed=processed,
        complete=True,
        sample_failures=sample_failures,
    )


def _extract_display(field: Any) -> Optional[str]:
    return _coerce_text(field, prefer_display=True)


def _extract_value(field: Any) -> Optional[str]:
    return _coerce_text(field, prefer_display=False)


def _normalize_service_request_for_graph(sr: Dict[str, Any], ritm: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _sanitize_graph_payload({
        "sys_id": str(_extract_value(sr.get("sys_id")) or "").strip(),
        "number": str(_extract_value(sr.get("number")) or "").strip(),
        "short_description": _extract_display(sr.get("short_description")),
        "description": _extract_display(sr.get("description")),
        "state": _extract_value(sr.get("state")),
        "state_display": _extract_display(sr.get("state")),
        "opened_at": _extract_display(sr.get("opened_at")),
        "requested_for_sys_id": _extract_value(sr.get("requested_for")),
        "requested_for_name": _extract_display(sr.get("requested_for")),
        "assigned_to_sys_id": _extract_value(sr.get("assigned_to")),
        "assigned_to_name": _extract_display(sr.get("assigned_to")),
        "ritm_sys_id": _extract_value((ritm or {}).get("sys_id")),
    })


def _normalize_request_item_for_graph(ritm: Dict[str, Any]) -> Dict[str, Any]:
    return _sanitize_graph_payload({
        "sys_id": str(_extract_value(ritm.get("sys_id")) or "").strip(),
        "number": _extract_value(ritm.get("number")),
        "short_description": _extract_display(ritm.get("short_description")),
        "description": _extract_display(ritm.get("description")),
        "state": _extract_value(ritm.get("state")),
        "state_display": _extract_display(ritm.get("state")),
        "cat_item_sys_id": _extract_value(ritm.get("cat_item")),
        "cat_item_name": _extract_display(ritm.get("cat_item")),
    })


def _normalize_change_request_for_graph(ch: Dict[str, Any]) -> Dict[str, Any]:
    return _sanitize_graph_payload({
        "sys_id": str(_extract_value(ch.get("sys_id")) or "").strip(),
        "number": str(_extract_value(ch.get("number")) or "").strip(),
        "short_description": _extract_display(ch.get("short_description")),
        "description": _extract_display(ch.get("description")),
        "state": _extract_value(ch.get("state")),
        "state_display": _extract_display(ch.get("state")),
        "opened_at": _extract_display(ch.get("opened_at")),
        "requested_by_sys_id": _extract_value(ch.get("requested_by")),
        "requested_by_name": _extract_display(ch.get("requested_by")),
        "assigned_to_sys_id": _extract_value(ch.get("assigned_to")),
        "assigned_to_name": _extract_display(ch.get("assigned_to")),
        "risk": _extract_display(ch.get("risk")),
        "impact": _extract_display(ch.get("impact")),
        "change_type": _extract_display(ch.get("type")),
    })


def _normalize_incident_for_graph(incident: Dict[str, Any]) -> Dict[str, Any]:
    sys_id = str(_extract_value(incident.get("sys_id")) or "").strip()
    comments = snow.get_comments("incident", sys_id) if sys_id else []
    work_notes = snow.get_work_notes("incident", sys_id) if sys_id else []
    comments_text = _journal_text(comments)
    work_notes_text = _journal_text(work_notes)
    journal_text = "\n".join(part for part in (comments_text, work_notes_text) if part)
    incident_state_field = incident.get("incident_state") or incident.get("state")
    resolution_notes = (
        _extract_display(incident.get("resolution_notes"))
        or _extract_display(incident.get("u_solution"))
        or _extract_display(incident.get("close_notes"))
    )
    workaround = _extract_display(incident.get("u_workaround"))
    cause_notes = _extract_display(incident.get("u_root_cause"))
    return _sanitize_graph_payload({
        "sys_id": sys_id,
        "number": str(_extract_value(incident.get("number")) or "").strip(),
        "short_description": _extract_display(incident.get("short_description")),
        "description": _extract_display(incident.get("description")),
        "state": _extract_value(incident_state_field),
        "state_display": _extract_display(incident_state_field),
        "incident_state": _extract_value(incident_state_field),
        "incident_state_display": _extract_display(incident_state_field),
        "opened_at": _extract_display(incident.get("opened_at")),
        "caller_sys_id": _extract_value(incident.get("caller_id")),
        "caller_name": _extract_display(incident.get("caller_id")),
        "assigned_to_sys_id": _extract_value(incident.get("assigned_to")),
        "assigned_to_name": _extract_display(incident.get("assigned_to")),
        "priority": _extract_display(incident.get("priority")),
        "urgency": _extract_display(incident.get("urgency")),
        "impact": _extract_display(incident.get("impact")),
        "category": _extract_display(incident.get("category")),
        "subcategory": _extract_display(incident.get("subcategory")),
        "close_code": _extract_display(incident.get("close_code")),
        "close_notes": _extract_display(incident.get("close_notes")),
        "resolved_at": _extract_display(incident.get("resolved_at")),
        "resolved_by_sys_id": _extract_value(incident.get("resolved_by")),
        "resolved_by_name": _extract_display(incident.get("resolved_by")),
        "resolution_notes": resolution_notes,
        "workaround": workaround,
        "cause_notes": cause_notes,
        "comments": comments_text,
        "work_notes": work_notes_text,
        "journal_text": journal_text,
    })


def _coerce_text(field: Any, prefer_display: bool) -> Optional[str]:
    if field in (None, ""):
        return None

    if isinstance(field, dict):
        keys = ("display_value", "value") if prefer_display else ("value", "display_value")
        for key in keys:
            value = _coerce_text(field.get(key), prefer_display=prefer_display)
            if value not in (None, ""):
                return value
        for key in ("name", "label"):
            value = _coerce_text(field.get(key), prefer_display=prefer_display)
            if value not in (None, ""):
                return value
        return None

    if isinstance(field, (list, tuple, set)):
        parts = [
            value
            for value in (_coerce_text(item, prefer_display=prefer_display) for item in field)
            if value not in (None, "")
        ]
        return ", ".join(parts) if parts else None

    return str(field)


def _sanitize_graph_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (dict, list, tuple, set)):
            sanitized[key] = _coerce_text(value, prefer_display=True)
        elif value in (None, ""):
            sanitized[key] = None
        else:
            sanitized[key] = str(value) if not isinstance(value, bool) else value
    return sanitized


def _record_sync_failure(
    sample_failures: List[Dict[str, str]],
    ticket_type: str,
    ticket_sys_id: str,
    message: Optional[str],
) -> None:
    if len(sample_failures) >= 5:
        return
    sample_failures.append({
        "ticket_type": ticket_type,
        "ticket_sys_id": ticket_sys_id or "unknown",
        "reason": message or "Unknown Neo4j sync failure",
    })
    logger.warning(
        "Ticket graph sync failed for %s %s: %s",
        ticket_type,
        ticket_sys_id or "unknown",
        message or "Unknown Neo4j sync failure",
    )


def _build_sync_result(
    *,
    synced: int,
    failed: int,
    processed: int,
    complete: bool,
    sample_failures: List[Dict[str, str]],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "synced": synced,
        "failed": failed,
        "processed": processed,
        "complete": complete,
    }
    if sample_failures:
        result["sample_failures"] = sample_failures
    return result


def _resolve_requested_for(requested_for: str) -> str:
    normalized = str(requested_for or "").strip()
    if not normalized:
        raise ValueError("requested_for is required")

    if "@" not in normalized:
        return normalized

    try:
        user = snow.get_user_by_email(normalized)
    except Exception as exc:
        raise ValueError(f"Failed to resolve requested_for email '{normalized}' in ServiceNow") from exc

    resolved_sys_id = _extract_value((user or {}).get("sys_id"))
    if not resolved_sys_id:
        raise ValueError(f"Could not resolve requested_for email '{normalized}' to a ServiceNow user")
    return resolved_sys_id


def _fetch_request_item_for_request(
    request_sys_id: Optional[str],
    attempts: int = 4,
    delay_seconds: float = 1.0,
) -> Optional[Dict[str, Any]]:
    normalized_request_sys_id = str(request_sys_id or "").strip()
    if not normalized_request_sys_id:
        return None

    for attempt in range(max(1, attempts)):
        try:
            ritm = snow.get_ritm_details_by_request_sysid(normalized_request_sys_id)
            if ritm:
                return ritm
        except Exception:
            logger.exception(
                "Failed to fetch request item for request %s on attempt %d",
                normalized_request_sys_id,
                attempt + 1,
            )

        if attempt < attempts - 1:
            time.sleep(delay_seconds)

    return None


def _sync_new_service_request_to_graph(
    sr: Optional[Dict[str, Any]],
    ritm: Optional[Dict[str, Any]],
) -> bool:
    if not sr or not neo4j_ticket_graph_service.is_available():
        return False

    ticket_sys_id = _extract_value(sr.get("sys_id")) or "unknown"
    error_details: Dict[str, str] = {}
    synced = neo4j_ticket_graph_service.sync_service_request(
        _normalize_service_request_for_graph(sr, ritm),
        _normalize_request_item_for_graph(ritm) if ritm else None,
        error_details=error_details,
    )
    if synced and ritm and _extract_value(ritm.get("cat_item")):
        try:
            sync_catalog_item_to_graph(_extract_value(ritm.get("cat_item")) or "")
        except Exception:
            logger.exception(
                "Failed to sync linked catalog item %s while syncing SR %s",
                _extract_value(ritm.get("cat_item")) or "unknown",
                ticket_sys_id,
            )
    if not synced:
        logger.warning(
            "Automatic graph sync failed for new SR %s: %s",
            ticket_sys_id,
            error_details.get("message") or "Unknown error",
        )
    return synced


def _sync_new_change_request_to_graph(ch: Optional[Dict[str, Any]]) -> bool:
    if not ch or not neo4j_ticket_graph_service.is_available():
        return False

    ticket_sys_id = _extract_value(ch.get("sys_id")) or "unknown"
    error_details: Dict[str, str] = {}
    synced = neo4j_ticket_graph_service.sync_change_request(
        _normalize_change_request_for_graph(ch),
        error_details=error_details,
    )
    if not synced:
        logger.warning(
            "Automatic graph sync failed for new CH %s: %s",
            ticket_sys_id,
            error_details.get("message") or "Unknown error",
        )
    return synced


def _sync_new_incident_to_graph(incident: Optional[Dict[str, Any]]) -> bool:
    if not incident or not neo4j_ticket_graph_service.is_available():
        return False

    ticket_sys_id = _extract_value(incident.get("sys_id")) or "unknown"
    error_details: Dict[str, str] = {}
    synced = neo4j_ticket_graph_service.sync_incident(
        _normalize_incident_for_graph(incident),
        error_details=error_details,
    )
    if not synced:
        logger.warning(
            "Automatic graph sync failed for incident %s: %s",
            ticket_sys_id,
            error_details.get("message") or "Unknown error",
        )
    return synced


def _journal_text(entries: List[Dict[str, Any]], limit: int = 20) -> str:
    lines: List[str] = []
    for entry in (entries or [])[:limit]:
        value = _extract_display(entry.get("value"))
        author = _extract_display(entry.get("sys_created_by"))
        created_at = _extract_display(entry.get("sys_created_on"))
        if not value:
            continue
        prefix = " ".join(part for part in (author, created_at) if part).strip()
        lines.append(f"{prefix}: {value}" if prefix else value)
    return "\n".join(lines)
