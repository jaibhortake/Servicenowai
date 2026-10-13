"""
Graph-oriented request and change tools for ServiceNow MCP.

These tools return normalized payloads that are convenient for downstream
business-graph ingestion.
"""

import logging
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)


class ListServiceRequestsForGraphParams(BaseModel):
    limit: int = Field(50, description="Maximum number of service requests to return")
    offset: int = Field(0, description="Offset to start from")


class GetServiceRequestForGraphParams(BaseModel):
    sys_id: str = Field(..., description="Service request sys_id")


class GetRequestItemForGraphParams(BaseModel):
    sys_id: str = Field(..., description="Request item sys_id")


class ListChangeRequestsForGraphParams(BaseModel):
    limit: int = Field(50, description="Maximum number of change requests to return")
    offset: int = Field(0, description="Offset to start from")


class GetChangeRequestForGraphParams(BaseModel):
    sys_id: str = Field(..., description="Change request sys_id")


def list_service_requests_for_graph(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: ListServiceRequestsForGraphParams,
) -> Dict[str, Any]:
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = auth_manager.get_headers()
    timeout = getattr(server_config, "timeout", 30)

    sr_url = f"{instance_url}/api/now/table/sc_request"
    sr_params = {
        "sysparm_limit": params.limit,
        "sysparm_offset": params.offset,
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": ",".join([
            "sys_id",
            "number",
            "short_description",
            "description",
            "state",
            "opened_at",
            "requested_for",
            "assigned_to",
        ]),
        "sysparm_query": "ORDERBYDESCopened_at",
    }

    response = requests.get(sr_url, headers=headers, params=sr_params, timeout=timeout)
    response.raise_for_status()
    service_requests = response.json().get("result", []) or []

    records: List[Dict[str, Any]] = []
    for sr in service_requests:
        request_sys_id = _extract_value(sr.get("sys_id")) or ""
        ritm = _get_ritm_by_request_sys_id(instance_url, headers, timeout, request_sys_id) if request_sys_id else None
        records.append({
            "ticket": _normalize_service_request_for_graph(sr, ritm),
            "request_item": _normalize_request_item_for_graph(ritm) if ritm else None,
            "raw": {"service_request": sr, "request_item": ritm},
        })

    return {"records": records, "count": len(records), "limit": params.limit, "offset": params.offset}


def get_service_request_for_graph(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: GetServiceRequestForGraphParams,
) -> Optional[Dict[str, Any]]:
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = auth_manager.get_headers()
    timeout = getattr(server_config, "timeout", 30)

    sr = _get_table_record(instance_url, headers, timeout, "sc_request", params.sys_id)
    if not sr:
        return None

    request_sys_id = _extract_value(sr.get("sys_id")) or params.sys_id
    ritm = _get_ritm_by_request_sys_id(instance_url, headers, timeout, request_sys_id)
    return {
        "ticket": _normalize_service_request_for_graph(sr, ritm),
        "request_item": _normalize_request_item_for_graph(ritm) if ritm else None,
        "raw": {"service_request": sr, "request_item": ritm},
    }


def get_request_item_for_graph(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: GetRequestItemForGraphParams,
) -> Optional[Dict[str, Any]]:
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = auth_manager.get_headers()
    timeout = getattr(server_config, "timeout", 30)

    ritm = _get_table_record(instance_url, headers, timeout, "sc_req_item", params.sys_id)
    if not ritm:
        return None
    return {
        "request_item": _normalize_request_item_for_graph(ritm),
        "raw": {"request_item": ritm},
    }


def list_change_requests_for_graph(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: ListChangeRequestsForGraphParams,
) -> Dict[str, Any]:
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = auth_manager.get_headers()
    timeout = getattr(server_config, "timeout", 30)

    ch_url = f"{instance_url}/api/now/table/change_request"
    ch_params = {
        "sysparm_limit": params.limit,
        "sysparm_offset": params.offset,
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": ",".join([
            "sys_id",
            "number",
            "short_description",
            "description",
            "state",
            "opened_at",
            "requested_by",
            "assigned_to",
            "risk",
            "impact",
            "type",
        ]),
        "sysparm_query": "ORDERBYDESCopened_at",
    }

    response = requests.get(ch_url, headers=headers, params=ch_params, timeout=timeout)
    response.raise_for_status()
    changes = response.json().get("result", []) or []
    records = [
        {
            "ticket": _normalize_change_request_for_graph(change),
            "raw": {"change_request": change},
        }
        for change in changes
    ]
    return {"records": records, "count": len(records), "limit": params.limit, "offset": params.offset}


def get_change_request_for_graph(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: GetChangeRequestForGraphParams,
) -> Optional[Dict[str, Any]]:
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = auth_manager.get_headers()
    timeout = getattr(server_config, "timeout", 30)

    change = _get_table_record(instance_url, headers, timeout, "change_request", params.sys_id)
    if not change:
        return None
    return {
        "ticket": _normalize_change_request_for_graph(change),
        "raw": {"change_request": change},
    }


def _get_ritm_by_request_sys_id(
    instance_url: str,
    headers: Dict[str, str],
    timeout: int,
    request_sys_id: str,
) -> Optional[Dict[str, Any]]:
    url = f"{instance_url}/api/now/table/sc_req_item"
    params = {
        "sysparm_limit": 1,
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": ",".join([
            "sys_id",
            "number",
            "short_description",
            "description",
            "state",
            "cat_item",
            "request",
        ]),
        "sysparm_query": f"request={request_sys_id}^ORDERBYDESCsys_created_on",
    }
    response = requests.get(url, headers=headers, params=params, timeout=timeout)
    response.raise_for_status()
    results = response.json().get("result", []) or []
    return results[0] if results else None


def _get_table_record(
    instance_url: str,
    headers: Dict[str, str],
    timeout: int,
    table_name: str,
    sys_id: str,
) -> Optional[Dict[str, Any]]:
    url = f"{instance_url}/api/now/table/{table_name}/{sys_id}"
    params = {
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
    }
    response = requests.get(url, headers=headers, params=params, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json().get("result")


def _normalize_service_request_for_graph(sr: Dict[str, Any], ritm: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _sanitize_graph_payload({
        "sys_id": _extract_value(sr.get("sys_id")),
        "number": _extract_value(sr.get("number")),
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
        "sys_id": _extract_value(ritm.get("sys_id")),
        "number": _extract_value(ritm.get("number")),
        "short_description": _extract_display(ritm.get("short_description")),
        "description": _extract_display(ritm.get("description")),
        "state": _extract_value(ritm.get("state")),
        "state_display": _extract_display(ritm.get("state")),
        "cat_item_sys_id": _extract_value(ritm.get("cat_item")),
        "cat_item_name": _extract_display(ritm.get("cat_item")),
    })


def _normalize_change_request_for_graph(change: Dict[str, Any]) -> Dict[str, Any]:
    return _sanitize_graph_payload({
        "sys_id": _extract_value(change.get("sys_id")),
        "number": _extract_value(change.get("number")),
        "short_description": _extract_display(change.get("short_description")),
        "description": _extract_display(change.get("description")),
        "state": _extract_value(change.get("state")),
        "state_display": _extract_display(change.get("state")),
        "opened_at": _extract_display(change.get("opened_at")),
        "requested_by_sys_id": _extract_value(change.get("requested_by")),
        "requested_by_name": _extract_display(change.get("requested_by")),
        "assigned_to_sys_id": _extract_value(change.get("assigned_to")),
        "assigned_to_name": _extract_display(change.get("assigned_to")),
        "risk": _extract_display(change.get("risk")),
        "impact": _extract_display(change.get("impact")),
        "change_type": _extract_display(change.get("type")),
    })


def _extract_display(field: Any) -> Optional[str]:
    return _coerce_text(field, prefer_display=True)


def _extract_value(field: Any) -> Optional[str]:
    return _coerce_text(field, prefer_display=False)


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


def _get_instance_url(auth_manager: AuthManager, server_config: ServerConfig) -> str:
    if hasattr(server_config, "instance_url"):
        return server_config.instance_url
    if hasattr(auth_manager, "instance_url"):
        return auth_manager.instance_url
    raise ValueError("Cannot find instance_url in either server_config or auth_manager")
