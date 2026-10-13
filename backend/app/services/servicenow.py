import json
import re

import httpx
from typing import Optional, Dict, Any, List
from app.config import settings
import logging

logger = logging.getLogger(__name__)

SNOW_BASE = settings.servicenow_instance_url.rstrip("/")
AUTH = (settings.servicenow_username, settings.servicenow_password)
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
TIMEOUT = 15.0


_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _is_plain_pair(field: Any) -> bool:
    """True for a non-reference {value, display_value} pair that should collapse to a string."""
    if not isinstance(field, dict) or "link" in field or set(field) - {"value", "display_value", "currency_display_value"}:
        return False
    value, display = field.get("value", ""), field.get("display_value", "")
    is_reference = bool(_HEX32.match(str(value or ""))) and str(display or "") not in ("", str(value))
    return not is_reference


def _flatten_plain_fields(response: httpx.Response) -> None:
    """Newer ServiceNow releases wrap *every* field as {value, display_value} when
    sysparm_display_value=all. The app expects plain strings for non-reference fields
    (sys_id, name, ...) and dicts only for references, so collapse the plain pairs."""
    if "application/json" not in response.headers.get("content-type", ""):
        return
    response.read()
    try:
        payload = response.json()
    except ValueError:
        return
    result = payload.get("result") if isinstance(payload, dict) else None
    rows = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, field in list(row.items()):
            if _is_plain_pair(field):
                row[key] = field.get("display_value", "") if "currency_display_value" in field else field.get("value", "")
                changed = True
    if changed:
        response._content = json.dumps(payload).encode("utf-8")


def _client() -> httpx.Client:
    return httpx.Client(
        event_hooks={"response": [_flatten_plain_fields]},
        auth=AUTH,
        headers=HEADERS,
        timeout=TIMEOUT,
        trust_env=bool(settings.servicenow_trust_env),
        verify=bool(settings.servicenow_verify_ssl),
    )


def _extract_value(field: Any) -> Optional[str]:
    if isinstance(field, dict):
        return field.get("value") or field.get("display_value")
    return str(field) if field not in (None, "") else None


def _extract_display(field: Any) -> Optional[str]:
    if isinstance(field, dict):
        return field.get("display_value") or field.get("value")
    return str(field) if field not in (None, "") else None


# ── User ────────────────────────────────────────────────────────────────────

def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sys_user"
    params = {
        "sysparm_query": f"email={email}",
        "sysparm_fields": "sys_id,name,email,department,manager,location,title",
        "sysparm_limit": 1,
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        result = r.json().get("result", [])
        return result[0] if result else None


def get_user_by_id(sys_id: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sys_user/{sys_id}"
    with _client() as c:
        r = c.get(url)
        r.raise_for_status()
        return r.json().get("result")


def list_users(limit: int = 100) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sys_user"
    params = {
        "sysparm_fields": "sys_id,name,email,department,manager,location,title",
        "sysparm_limit": limit,
        "sysparm_query": "active=true",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


def list_tables(
    limit: int = 100,
    offset: int = 0,
    include_system_tables: bool = False,
    name_contains: Optional[str] = None,
) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sys_db_object"
    query_parts = ["nameISNOTEMPTY"]
    if not include_system_tables:
        query_parts.append("nameNOT LIKEsys_")
    if name_contains:
        query_parts.append(f"nameLIKE{name_contains}")

    params = {
        "sysparm_fields": "sys_id,name,label,super_class,sys_scope,provider_class",
        "sysparm_limit": limit,
        "sysparm_offset": offset,
        "sysparm_display_value": "all",
        "sysparm_query": "^".join(query_parts),
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


def list_table_columns(table_name: str) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sys_dictionary"
    params = {
        "sysparm_fields": "sys_id,name,element,column_label,internal_type,max_length,mandatory,reference,default_value",
        "sysparm_limit": 1000,
        "sysparm_display_value": "all",
        "sysparm_query": f"name={table_name}^elementISNOTEMPTY^ORDERBYposition",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


def list_table_rows(table_name: str, limit: int = 1) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/{table_name}"
    params = {
        "sysparm_limit": limit,
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_query": "ORDERBYsys_created_on",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


# ── Catalog ──────────────────────────────────────────────────────────────────

def list_catalog_items(
    category: Optional[str] = None,
    limit: int = 100,
    query: Optional[str] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_cat_item"
    params = {
        "sysparm_fields": "sys_id,name,short_description,description,category,price,icon",
        "sysparm_limit": limit,
        "sysparm_offset": max(0, int(offset or 0)),
        "sysparm_display_value": "all",
        "sysparm_query": "active=true",
    }
    if category:
        params["sysparm_query"] += f"^category={category}"

    # Add name search if query provided
    if query:
        params["sysparm_query"] += f"^ORnameLIKE{query}^ORshort_descriptionLIKE{query}"

    logger.info(f"Fetching catalog items from {url} with query='{query}' | params: {params}")

    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        result = r.json().get("result", [])
        logger.info(f"📡 SERVICENOW API returned {len(result)} items from {url}")

        # Transform ServiceNow response to flatten reference fields
        items = []
        for idx, item in enumerate(result):
            logger.debug(f"  Raw item {idx} keys: {list(item.keys())}")
            logger.debug(f"  Raw item {idx} category type: {type(item.get('category'))}, value: {item.get('category')}")

            transformed = {
                "sys_id": item.get("sys_id", ""),
                "name": item.get("name", ""),
                "display_name": item.get("name", ""),
                "short_description": item.get("short_description", ""),
                "description": item.get("description", ""),
                "price": _extract_display(item.get("price")) or "",
                "icon": _extract_value(item.get("icon")) or "",
                "relevance_score": 0.0,
                "match_reason": None,
            }
            category_field = item.get("category", "")
            transformed["category_sys_id"] = _extract_value(category_field)
            transformed["category_name"] = _extract_display(category_field)
            transformed["category"] = transformed["category_name"] or transformed["category_sys_id"]
            logger.debug(f"  Transformed item {idx}: name={transformed['name']}, category={transformed['category']}")
            items.append(transformed)

        if items:
            logger.info(f"✓ First transformed item: {items[0]}")
        return items


def get_catalog_item(sys_id: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_cat_item/{sys_id}"
    params = {
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        item = r.json().get("result")
        if not item:
            return item
        category_field = item.get("category")
        item["category_sys_id"] = _extract_value(category_field)
        item["category_name"] = _extract_display(category_field)
        item["category"] = item["category_name"] or item["category_sys_id"]
        item["display_name"] = item.get("name")
        return item


def list_catalog_item_variables(sys_id: str) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/item_option_new"
    params = {
        "sysparm_fields": "sys_id,name,question_text,type,mandatory,default_value,order,reference,depends_on,inactive_on_value",
        "sysparm_query": f"cat_item={sys_id}^active=true^ORDERBYorder",
        "sysparm_limit": 100,
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


def get_catalog_form_raw_state(catalog_item_sys_id: str) -> Dict[str, Any]:
    """Fetch comprehensive form state from ServiceNow:
    - Variables with native depends_on / inactive_on_value from item_option_new
    - UI policies and their conditions
    - UI policy actions
    - Client scripts
    Returns raw data useful for debugging and enrichment.
    """
    # 1. Native variable dependencies from item_option_new
    var_url = f"{SNOW_BASE}/api/now/table/item_option_new"
    var_params = {
        "sysparm_fields": (
            "sys_id,name,question_text,type,mandatory,default_value,order,"
            "reference,depends_on,inactive_on_value,display_type,create_roles,"
            "lookup_table,lookup_label,macro,url,use_display_value"
        ),
        "sysparm_query": f"cat_item={catalog_item_sys_id}^active=true^ORDERBYorder",
        "sysparm_limit": 100,
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(var_url, params=var_params)
        r.raise_for_status()
        variables = r.json().get("result", [])

    # 2. UI policies
    policy_url = f"{SNOW_BASE}/api/now/table/catalog_ui_policy"
    policy_params = {
        "sysparm_query": f"catalog_item={catalog_item_sys_id}^active=true",
        "sysparm_fields": "sys_id,short_description,catalog_conditions,reverse_if_false,active",
        "sysparm_limit": 50,
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(policy_url, params=policy_params)
        r.raise_for_status()
        ui_policies = r.json().get("result", [])

    # 3. UI policy actions for all policies
    ui_policy_actions = []
    for policy in ui_policies:
        policy_id = policy.get("sys_id", "")
        if not policy_id:
            continue
        action_url = f"{SNOW_BASE}/api/now/table/catalog_ui_policy_action"
        action_params = {
            "sysparm_query": f"ui_policy={policy_id}",
            "sysparm_fields": "catalog_variable,visible,mandatory,disabled",
            "sysparm_limit": 50,
            "sysparm_display_value": "all",
        }
        with _client() as c:
            r = c.get(action_url, params=action_params)
            r.raise_for_status()
            actions = r.json().get("result", [])
        for action in actions:
            action["_policy_id"] = policy_id
            action["_policy_conditions"] = policy.get("catalog_conditions", "")
            action["_reverse_if_false"] = policy.get("reverse_if_false", "")
        ui_policy_actions.extend(actions)

    # 4. Client scripts
    script_url = f"{SNOW_BASE}/api/now/table/catalog_client_script"
    script_params = {
        "sysparm_query": f"catalog_item={catalog_item_sys_id}^active=true",
        "sysparm_fields": "sys_id,name,script,type,variable_name,applies_to,active",
        "sysparm_limit": 50,
        "sysparm_display_value": "all",
    }
    with _client() as c:
        try:
            r = c.get(script_url, params=script_params)
            r.raise_for_status()
            client_scripts = r.json().get("result", [])
        except Exception:
            client_scripts = []

    return {
        "catalog_item_sys_id": catalog_item_sys_id,
        "variables": variables,
        "ui_policies": ui_policies,
        "ui_policy_actions": ui_policy_actions,
        "client_scripts": client_scripts,
    }


def list_catalog_client_scripts(catalog_item_sys_id: str) -> List[Dict[str, Any]]:
    """Fetch client scripts for a catalog item (onChange, onLoad, etc.)."""
    url = f"{SNOW_BASE}/api/now/table/catalog_client_script"
    params = {
        "sysparm_query": f"catalog_item={catalog_item_sys_id}^active=true",
        "sysparm_fields": "sys_id,name,script,type,variable_name,applies_to",
        "sysparm_limit": 50,
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


def list_catalog_changes(since: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    url = f"{SNOW_BASE}/api/now/table/sc_cat_item"
    query_parts = ["active=true"]
    if since:
        query_parts.append(f"sys_updated_on>={since}")

    params = {
        "sysparm_fields": "sys_id,sys_updated_on",
        "sysparm_query": "^".join(query_parts),
        "sysparm_limit": limit,
        "sysparm_orderby": "sys_updated_on",
    }

    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        result = r.json().get("result", [])

    changed_ids: List[str] = []
    for item in result:
        sys_id = str(item.get("sys_id", "")).strip()
        if sys_id:
            changed_ids.append(sys_id)

    next_since = None
    if result:
        next_since = str(result[-1].get("sys_updated_on") or "").strip() or None

    return {
        "success": True,
        "changed_catalog_item_ids": changed_ids,
        "count": len(changed_ids),
        "next_since": next_since or since,
    }


def list_catalog_ui_policies(catalog_item_sys_id: str) -> List[Dict[str, Any]]:
    """Fetch UI policies and ALL their actions in minimal calls (optimized).
    Returns list of {depends_on, active_on_value, variable_sys_id} mappings.
    Falls back to mock data if ServiceNow is unreachable."""
    logger.info(f"[LIST_UI_POLICIES] Called for {catalog_item_sys_id}")
    try:
        url_policies = f"{SNOW_BASE}/api/now/table/catalog_ui_policy"
        params = {
            "sysparm_query": f"catalog_item={catalog_item_sys_id}^active=true",
            "sysparm_fields": "sys_id,catalog_conditions,reverse_if_false",
            "sysparm_limit": 50,
        }
        with _client() as c:
            r = c.get(url_policies, params=params)
            r.raise_for_status()
            policies = r.json().get("result", [])
    except Exception as e:
        logger.warning(f"[SNOW] Failed to fetch UI policies: {type(e).__name__}: {e}. Using mock...")
        from app.services import servicenow_mock
        mock_result = servicenow_mock.get_mock_ui_policies(catalog_item_sys_id)
        logger.info(f"[SNOW] Mock returned {len(mock_result)} mappings")
        return mock_result

    if not policies:
        return []

    # Collect all policy IDs to fetch actions in single query
    policy_ids = [p.get("sys_id", "") for p in policies if p.get("sys_id")]
    if not policy_ids:
        return []

    # Fetch ALL actions for these policies in ONE call (much faster)
    try:
        url_actions = f"{SNOW_BASE}/api/now/table/catalog_ui_policy_action"
        action_params = {
            "sysparm_query": "^OR".join([f"ui_policy={pid}" for pid in policy_ids]),
            "sysparm_fields": "ui_policy,catalog_variable,visible,mandatory",
            "sysparm_limit": 500,
        }
        with _client() as c:
            r = c.get(url_actions, params=action_params)
            r.raise_for_status()
            all_actions = r.json().get("result", [])
    except Exception as e:
        logger.warning(f"[SNOW] Failed to fetch UI policy actions from ServiceNow: {e}. Returning empty...")
        return []

    # Build mapping from policy_id -> [actions]
    actions_by_policy = {}
    for action in all_actions:
        pid = action.get("ui_policy", "")
        if isinstance(pid, dict):
            pid = pid.get("value", "")
        pid = str(pid).strip()
        if pid:
            actions_by_policy.setdefault(pid, []).append(action)

    mappings = []
    for policy in policies:
        policy_id = str(policy.get("sys_id", "")).strip()
        conditions_str = str(policy.get("catalog_conditions", "") or "")

        # Parse condition: "IO:<field_sys_id>=<value>^EQ"
        dep_field_sys_id = None
        active_value = None
        for part in conditions_str.split("^"):
            part = part.strip()
            if part.startswith("IO:") and "=" in part:
                rest = part[3:]
                dep_field_sys_id, active_value = rest.split("=", 1)
                break

        if not dep_field_sys_id or not active_value:
            continue

        # Get actions for this policy (now fetched already)
        for action in actions_by_policy.get(policy_id, []):
            var_ref = action.get("catalog_variable", "")
            if isinstance(var_ref, dict):
                var_sys_id = var_ref.get("value", "")
            else:
                var_sys_id = str(var_ref or "")
            var_sys_id = var_sys_id.replace("IO:", "").strip()
            if var_sys_id:
                mappings.append({
                    "variable_sys_id": var_sys_id,
                    "depends_on": dep_field_sys_id,
                    "active_on_value": active_value,
                })

    return mappings


def list_variable_choices(variable_sys_id: str) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/question_choice"
    params = {
        "sysparm_fields": "text,value,order",
        "sysparm_query": f"question={variable_sys_id}^ORDERBYorder",
        "sysparm_limit": 100,
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


# ── Service Request ──────────────────────────────────────────────────────────

def get_table_record(table: str, sys_id: str, display_value: str = "all") -> Optional[Dict[str, Any]]:
    """Fetch one ServiceNow table record by sys_id for webhook follow-up reads."""
    table_name = str(table or "").strip()
    record_id = str(sys_id or "").strip()
    if not table_name or not record_id:
        return None

    url = f"{SNOW_BASE}/api/now/table/{table_name}/{record_id}"
    params = {
        "sysparm_display_value": display_value,
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result")


def get_ritm_by_request_sysid(req_sys_id: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_req_item"
    params = {
        "sysparm_query": f"request={req_sys_id}",
        "sysparm_fields": "sys_id,number",
        "sysparm_limit": 1,
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        result = r.json().get("result", [])
        return result[0] if result else None


def get_ritm_details_by_request_sysid(req_sys_id: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_req_item"
    params = {
        "sysparm_query": f"request={req_sys_id}",
        "sysparm_fields": "sys_id,number,short_description,state,cat_item,request",
        "sysparm_limit": 1,
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        result = r.json().get("result", [])
        return result[0] if result else None


def create_service_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{SNOW_BASE}/api/now/table/sc_request"
    with _client() as c:
        r = c.post(url, json=payload)
        r.raise_for_status()
        return r.json().get("result", {})


def create_request_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{SNOW_BASE}/api/now/table/sc_req_item"
    with _client() as c:
        r = c.post(url, json=payload)
        r.raise_for_status()
        return r.json().get("result", {})


def get_service_request(sys_id: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_request/{sys_id}"
    params = {
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result")


def get_request_item(sys_id: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_req_item/{sys_id}"
    params = {
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result")


def patch_service_request(sys_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_request/{sys_id}"
    with _client() as c:
        r = c.patch(url, json=payload)
        r.raise_for_status()
        return r.json().get("result")


def patch_request_item(sys_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_req_item/{sys_id}"
    with _client() as c:
        r = c.patch(url, json=payload)
        r.raise_for_status()
        return r.json().get("result")


def create_incident(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{SNOW_BASE}/api/now/table/incident"
    with _client() as c:
        r = c.post(url, json=payload)
        r.raise_for_status()
        return r.json().get("result", {})


def get_incident(sys_id: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/incident/{sys_id}"
    params = {
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result")


def patch_incident(sys_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/incident/{sys_id}"
    with _client() as c:
        r = c.patch(url, json=payload)
        r.raise_for_status()
        return r.json().get("result")


def patch_change_request(sys_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/change_request/{sys_id}"
    with _client() as c:
        r = c.patch(url, json=payload)
        r.raise_for_status()
        return r.json().get("result")


def order_catalog_item(
    catalog_item_sys_id: str,
    requested_for: str,
    variables: Optional[Dict[str, Any]] = None,
    quantity: int = 1,
) -> Dict[str, Any]:
    """Order a catalog item through the ServiceNow catalog engine.

    This creates the SC_REQUEST and SC_REQ_ITEM records properly with
    variable linkage, unlike direct table inserts.
    """
    url = f"{SNOW_BASE}/api/sn_sc/servicecatalog/items/{catalog_item_sys_id}/order_now"
    payload = {
        "sysparm_quantity": quantity,
        "sysparm_requested_for": requested_for,
    }
    if variables:
        payload["variables"] = variables

    with _client() as c:
        r = c.post(url, json=payload)
        r.raise_for_status()
        return r.json().get("result", {})


def list_service_requests(user_sys_id: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_request"
    query = "active=true"
    if user_sys_id:
        query += f"^requested_for={user_sys_id}"
    params = {
        "sysparm_query": query,
        "sysparm_limit": limit,
        "sysparm_offset": offset,
        "sysparm_orderby": "opened_at^DESC",
        "sysparm_fields": "sys_id,number,short_description,state,opened_at,assigned_to,requested_for",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


def list_service_requests_for_graph(
    user_sys_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sc_request"
    query = "active=true"
    if user_sys_id:
        query += f"^requested_for={user_sys_id}"
    params = {
        "sysparm_query": query,
        "sysparm_limit": limit,
        "sysparm_offset": offset,
        "sysparm_orderby": "opened_at^DESC",
        "sysparm_display_value": "all",
        "sysparm_fields": "sys_id,number,short_description,description,state,opened_at,assigned_to,requested_for",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


# ── Change Request ────────────────────────────────────────────────────────────

def create_change_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{SNOW_BASE}/api/now/table/change_request"
    with _client() as c:
        r = c.post(url, json=payload)
        r.raise_for_status()
        return r.json().get("result", {})


def get_change_request(sys_id: str) -> Optional[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/change_request/{sys_id}"
    params = {
        "sysparm_display_value": "all",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result")


def list_change_requests(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/change_request"
    params = {
        "sysparm_limit": limit,
        "sysparm_offset": offset,
        "sysparm_orderby": "opened_at^DESC",
        "sysparm_fields": "sys_id,number,short_description,state,risk,opened_at,assigned_to,change_type",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


def list_change_requests_for_graph(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/change_request"
    params = {
        "sysparm_limit": limit,
        "sysparm_offset": offset,
        "sysparm_orderby": "opened_at^DESC",
        "sysparm_display_value": "all",
        "sysparm_fields": "sys_id,number,short_description,description,state,risk,impact,opened_at,assigned_to,requested_by,type",
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


# ── Comments ─────────────────────────────────────────────────────────────────

def add_comment(table: str, sys_id: str, comment: str, is_work_note: bool = False) -> Dict[str, Any]:
    url = f"{SNOW_BASE}/api/now/table/{table}/{sys_id}"
    field = "work_notes" if is_work_note else "comments"
    with _client() as c:
        r = c.patch(url, json={field: comment})
        r.raise_for_status()
        return r.json().get("result", {})


def get_journal_entries(table: str, sys_id: str, element: str = "comments") -> List[Dict[str, Any]]:
    url = f"{SNOW_BASE}/api/now/table/sys_journal_field"
    params = {
        "sysparm_query": f"name={table}^element_id={sys_id}^element={element}^ORDERBYDESCsys_created_on",
        "sysparm_fields": "value,sys_created_on,sys_created_by,element",
        "sysparm_limit": 100,
    }
    with _client() as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json().get("result", [])


def get_comments(table: str, sys_id: str) -> List[Dict[str, Any]]:
    return get_journal_entries(table, sys_id, "comments")


def get_work_notes(table: str, sys_id: str) -> List[Dict[str, Any]]:
    return get_journal_entries(table, sys_id, "work_notes")
