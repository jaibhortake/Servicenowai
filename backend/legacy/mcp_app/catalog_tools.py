from typing import Any, Dict, List, Optional
import logging
from app.providers.provider_registry import get_provider_instance
from app.services import servicenow as snow
from app.services import ticket_service

logger = logging.getLogger(__name__)


def list_catalog_items(query: Optional[str] = None, limit: int = 100, offset: int = 0, active: bool = True) -> List[Dict[str, Any]]:
    """List catalog items"""
    repo = get_provider_instance()
    return repo.list_items(query=query, limit=limit, offset=offset, active=active)


def get_catalog_item(item_id: str) -> Optional[Dict[str, Any]]:
    """Get a single catalog item by sys_id"""
    repo = get_provider_instance()
    return repo.get_item(item_id)


def list_catalog_item_variables(catalog_item_id: str, include_details: bool = False) -> List[Dict[str, Any]]:
    """List variables for a catalog item"""
    repo = get_provider_instance()
    return repo.list_item_variables(catalog_item_id)


def list_catalog_changes(since: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    """List recent changes to catalog items"""
    repo = get_provider_instance()
    return repo.list_changes(since=since, limit=limit)


def search_catalog(query: str, intent_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Search catalog items by query"""
    items = list_catalog_items(query=query)
    return items


def get_catalog_form(sys_id: str) -> Optional[Dict[str, Any]]:
    """Get catalog form with item and variables"""
    item_data = get_catalog_item(sys_id)
    if not item_data or not item_data.get("success"):
        return None

    item = item_data.get("data", {})
    variables = list_catalog_item_variables(sys_id)

    return {
        "item": item,
        "variables": variables
    }


def list_service_requests_for_graph(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Return normalized service request graph records."""
    records = []
    for sr in snow.list_service_requests_for_graph(limit=limit, offset=offset):
        request_sys_id = ticket_service._extract_value(sr.get("sys_id")) or ""
        ritm = snow.get_ritm_details_by_request_sysid(request_sys_id) if request_sys_id else None
        records.append({
            "ticket": ticket_service._normalize_service_request_for_graph(sr, ritm),
            "request_item": ticket_service._normalize_request_item_for_graph(ritm) if ritm else None,
            "raw": {"service_request": sr, "request_item": ritm},
        })
    return {"records": records, "count": len(records), "limit": limit, "offset": offset}


def get_service_request_for_graph(sys_id: str) -> Optional[Dict[str, Any]]:
    """Return one normalized service request and its request item context."""
    sr = snow.get_service_request(sys_id)
    if not sr:
        return None
    ritm = snow.get_ritm_details_by_request_sysid(ticket_service._extract_value(sr.get("sys_id")) or sys_id)
    return {
        "ticket": ticket_service._normalize_service_request_for_graph(sr, ritm),
        "request_item": ticket_service._normalize_request_item_for_graph(ritm) if ritm else None,
        "raw": {"service_request": sr, "request_item": ritm},
    }


def get_request_item_for_graph(sys_id: str) -> Optional[Dict[str, Any]]:
    ritm = snow.get_request_item(sys_id)
    if not ritm:
        return None
    return {
        "request_item": ticket_service._normalize_request_item_for_graph(ritm),
        "raw": {"request_item": ritm},
    }


def list_change_requests_for_graph(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Return normalized change request graph records."""
    records = [
        {
            "ticket": ticket_service._normalize_change_request_for_graph(ch),
            "raw": {"change_request": ch},
        }
        for ch in snow.list_change_requests_for_graph(limit=limit, offset=offset)
    ]
    return {"records": records, "count": len(records), "limit": limit, "offset": offset}


def get_change_request_for_graph(sys_id: str) -> Optional[Dict[str, Any]]:
    ch = snow.get_change_request(sys_id)
    if not ch:
        return None
    return {
        "ticket": ticket_service._normalize_change_request_for_graph(ch),
        "raw": {"change_request": ch},
    }
