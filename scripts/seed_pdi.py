#!/usr/bin/env python
"""Seed a fresh ServiceNow Personal Developer Instance (PDI) with this project's data.

What it does (all through the standard Table API, idempotent - safe to re-run):
  1. Creates the custom u_app_* tables and their columns from csv_ingest/*.csv headers.
  2. Adds the custom u_* columns the project expects on incident and problem.
  3. Imports the CSVs in csv_ingest/import_order.csv order.
  4. Creates demo service-catalog items with variables, choices and UI policies
     (used by the dynamic-form / conditional-field features).

Usage:
    python scripts/seed_pdi.py                # everything
    python scripts/seed_pdi.py --only schema  # schema | data | catalog
    python scripts/seed_pdi.py --dry-run

Credentials come from backend/.env (SERVICENOW_INSTANCE_URL / _USERNAME / _PASSWORD).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "csv_ingest"
ENV = {**dotenv_values(ROOT / "backend" / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("SERVICENOW_")}}

BASE = str(ENV.get("SERVICENOW_INSTANCE_URL", "")).rstrip("/")
USER = ENV.get("SERVICENOW_USERNAME", "admin")
PASS = ENV.get("SERVICENOW_PASSWORD", "")
VERIFY = str(ENV.get("SERVICENOW_VERIFY_SSL", "false")).lower() in {"1", "true", "yes"}

PRIORITY_STATE = {"New": "1", "In Progress": "2", "Awaiting Info": "3", "Resolved": "6", "Closed": "7"}
PROBLEM_STATE = {"New": "101", "Assess": "102", "Root Cause Analysis": "103", "Known Error": "103", "Fix in Progress": "104", "Resolved": "106", "Closed": "107"}

# Extra custom columns on stock tables (name -> (label, max_length))
INCIDENT_COLUMNS = {
    "u_external_id": ("External ID", 40),
    "u_appliance_ci_id": ("Appliance CI ID", 40),
    "u_customer_id": ("Customer ID", 40),
    "u_source_systems_used": ("Source Systems Used", 255),
    "u_ai_triage_status": ("AI Triage Status", 40),
    "u_likely_cause": ("Likely Cause", 255),
    "u_confidence_score": ("Confidence Score", 20),
}
PROBLEM_COLUMNS = {
    "u_external_id": ("External ID", 40),
    "u_problem_domain": ("Problem Domain", 100),
    "u_related_incidents_json": ("Related Incidents (JSON)", 4000),
    "u_detected_pattern": ("Detected Pattern", 1000),
    "u_business_impact": ("Business Impact", 1000),
}

client = httpx.Client(
    base_url=BASE,
    auth=(USER, PASS),
    headers={"Accept": "application/json", "Content-Type": "application/json"},
    timeout=60.0,
    verify=VERIFY,
    trust_env=False,
)


def log(msg: str) -> None:
    print(msg, flush=True)


def api(method: str, path: str, **kw: Any) -> Any:
    for attempt in range(4):
        try:
            r = client.request(method, path, **kw)
        except httpx.TransportError:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json().get("result") if r.content else None
    raise RuntimeError(f"{method} {path} failed after retries")


def query(table: str, q: str, fields: str = "sys_id", limit: int = 1) -> List[Dict[str, Any]]:
    return api("GET", f"/api/now/table/{table}", params={"sysparm_query": q, "sysparm_fields": fields, "sysparm_limit": limit}) or []


def first_id(table: str, q: str) -> Optional[str]:
    rows = query(table, q)
    return rows[0]["sys_id"] if rows else None


def insert(table: str, body: Dict[str, Any]) -> str:
    return api("POST", f"/api/now/table/{table}", json=body)["sys_id"]


# ----------------------------------------------------------------------------- schema


def read_csv(name: str) -> List[Dict[str, str]]:
    with open(CSV_DIR / f"{name}.csv", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def label_of(name: str) -> str:
    return re.sub(r"^u_(app_)?", "", name).replace("_", " ").title()


def ensure_table(name: str, label: str) -> None:
    if first_id("sys_db_object", f"name={name}"):
        return
    insert("sys_db_object", {"name": name, "label": label, "is_extendable": "false", "create_access_controls": "true"})
    log(f"  + table {name}")


def ensure_column(table: str, element: str, label: str, max_length: int) -> None:
    if first_id("sys_dictionary", f"name={table}^element={element}"):
        return
    insert(
        "sys_dictionary",
        {"name": table, "element": element, "column_label": label, "internal_type": "string", "max_length": str(max_length)},
    )


def width_for(values: List[str]) -> int:
    longest = max((len(v) for v in values), default=0)
    if longest <= 40:
        return 100
    if longest <= 200:
        return 255
    return 4000


def seed_schema() -> None:
    log("== Schema")
    order = read_csv("import_order")
    for row in order:
        table = row["servicenow_table"]
        if not table.startswith("u_app_"):
            continue
        rows = read_csv(row["sheet_or_csv"])
        ensure_table(table, label_of(table))
        for col in rows[0].keys():
            ensure_column(table, col, label_of(col), width_for([r[col] for r in rows]))
        log(f"  ok {table}: {len(rows[0])} columns")
    for table, columns in (("incident", INCIDENT_COLUMNS), ("problem", PROBLEM_COLUMNS)):
        for col, (label, length) in columns.items():
            ensure_column(table, col, label, length)
        log(f"  ok {table}: custom columns")


# ----------------------------------------------------------------------------- data


def existing_keys(table: str, field: str) -> set:
    keys, offset = set(), 0
    while True:
        rows = api("GET", f"/api/now/table/{table}", params={"sysparm_fields": field, "sysparm_limit": 1000, "sysparm_offset": offset}) or []
        keys.update(r.get(field, "") for r in rows)
        if len(rows) < 1000:
            return keys
        offset += 1000


def import_rows(table: str, rows: List[Dict[str, Any]], key_field: str) -> None:
    have = existing_keys(table, key_field)
    todo = [r for r in rows if r.get(key_field) not in have]

    def push(r: Dict[str, Any]) -> bool:
        try:
            insert(table, r)
            return True
        except Exception as exc:
            log(f"    ! {table} {r.get(key_field)}: {exc}")
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        ok = sum(pool.map(push, todo))
    log(f"  {table}: {ok} inserted, {len(rows) - len(todo)} already present, {len(todo) - ok} failed")


_user_cache: Dict[str, str] = {}
_group_cache: Dict[str, str] = {}


def user_for(name: str, company: str) -> Optional[str]:
    if not name:
        return None
    if name not in _user_cache:
        found = first_id("sys_user", f"name={name}")
        if not found:
            slug = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")[:40]
            found = insert("sys_user", {"user_name": slug, "first_name": name, "last_name": "", "name": name, "email": f"{slug}@example.com"})
        _user_cache[name] = found
    return _user_cache[name]


def group_for(name: str) -> Optional[str]:
    if not name:
        return None
    if name not in _group_cache:
        found = first_id("sys_user_group", f"name={name}") or insert("sys_user_group", {"name": name})
        _group_cache[name] = found
    return _group_cache[name]


def lead(value: str) -> str:
    """'2 - High' -> '2'"""
    return (value or "").split(" ", 1)[0].strip()


def incident_body(r: Dict[str, str]) -> Dict[str, Any]:
    state = PRIORITY_STATE.get(r["state"], "2")
    body: Dict[str, Any] = {
        "number": r["number"],
        "short_description": r["short_description"],
        "description": r["description"],
        "category": "inquiry",
        "contact_type": {"Portal": "self-service", "Phone": "phone", "Monitoring Event": "email"}.get(r["contact_type"], "self-service"),
        "impact": lead(r["impact"]),
        "urgency": lead(r["urgency"]),
        "opened_at": r["opened_at"],
        "state": state,
        "caller_id": user_for(r["caller_id"], r["company"]),
        "assignment_group": group_for(r["assignment_group"]),
        "u_external_id": r["u_external_id"],
        "u_appliance_ci_id": r["u_appliance_ci_id"],
        "u_customer_id": r["u_customer_id"],
        "u_source_systems_used": r["u_source_systems_used"],
        "u_ai_triage_status": r["u_ai_triage_status"],
        "u_likely_cause": r["u_likely_cause"],
        "u_confidence_score": r["u_confidence_score"],
        # The category/subcategory text from the dataset is kept as a work-note style prefix in the description.
        "work_notes": f"Dataset category: {r['category']} / {r['subcategory']}. CI: {r['cmdb_ci']}. Company: {r['company']}.",
    }
    if state == "3":
        body["hold_reason"] = "5"  # Awaiting Caller
    if state in {"6", "7"}:
        body.update({"close_code": "Solution provided", "close_notes": f"Resolved: {r['u_likely_cause']}"})
    return {k: v for k, v in body.items() if v not in (None, "")}


PROBLEM_PATH = ["102", "103", "104", "106", "107"]  # Assess -> RCA -> Fix in Progress -> Resolved -> Closed


def import_problems(rows: List[Dict[str, str]]) -> None:
    have = existing_keys("problem", "u_external_id")
    me = first_id("sys_user", f"user_name={USER}")
    ok = 0
    for r in rows:
        if r["u_external_id"] in have:
            continue
        body = {k: v for k, v in r.items() if (k.startswith("u_") or k in {"number", "short_description", "opened_at"}) and v}
        body["state"] = "101"
        try:
            sid = insert("problem", body)
            target = PROBLEM_STATE.get(r["state"], "101")
            for step in PROBLEM_PATH:
                if int(step) > int(target):
                    break
                patch = {"state": step, "assigned_to": me}
                if step == "103" and r["state"] == "Known Error":
                    patch["known_error"] = "true"
                if step in {"106", "107"}:
                    patch.update({"resolution_code": "fix_applied", "fix_notes": "Resolved"})
                api("PATCH", f"/api/now/table/problem/{sid}", json=patch)
            ok += 1
        except Exception as exc:
            log(f"    ! problem {r['u_external_id']}: {str(exc)[:200]}")
    log(f"  problem: {ok} inserted, {len(have)} already present")


def seed_data() -> None:
    log("== Data")
    for row in read_csv("import_order"):
        table, sheet = row["servicenow_table"], row["sheet_or_csv"]
        if table == "N/A":
            continue
        rows = read_csv(sheet)
        if table == "incident":
            have = existing_keys("incident", "u_external_id")
            todo = [r for r in rows if r["u_external_id"] not in have]
            bodies = [incident_body(r) for r in todo]  # resolves users/groups sequentially (cached)
            import_rows_direct("incident", bodies, len(rows) - len(todo))
        elif table == "problem":
            import_problems(rows)
        else:
            import_rows(table, [{k: v for k, v in r.items() if v != ""} for r in rows], "u_external_id")


def import_rows_direct(table: str, bodies: List[Dict[str, Any]], skipped: int) -> None:
    ok = 0
    for body in bodies:
        try:
            insert(table, body)
            ok += 1
        except Exception as exc:
            log(f"    ! {table} {body.get('number')}: {exc}")
    log(f"  {table}: {ok} inserted, {skipped} already present, {len(bodies) - ok} failed")


# ----------------------------------------------------------------------------- catalog

# type codes: 6 single-line text, 2 multi-line text, 5 select box, 7 checkbox, 1 yes/no, 9 date
CATALOG = [
    {
        "name": "Request a New Laptop",
        "short_description": "Order a standard or high-performance laptop for a new or existing employee.",
        "description": "Choose a laptop model and optional accessories. Approval is required for high-performance models.",
        "variables": [
            {"name": "laptop_model", "question_text": "Laptop model", "type": "5", "mandatory": "true", "order": 100,
             "choices": [("Standard Laptop (14\")", "standard"), ("Developer Laptop (16\")", "developer"), ("Ultralight Laptop (13\")", "ultralight")]},
            {"name": "needs_accessories", "question_text": "Need accessories?", "type": "7", "order": 200},
            {"name": "accessory_list", "question_text": "Which accessories?", "type": "6", "order": 300},
            {"name": "business_justification", "question_text": "Business justification", "type": "2", "mandatory": "true", "order": 400},
            {"name": "needed_by", "question_text": "Needed by", "type": "9", "order": 500},
        ],
        "policies": [("Show accessory list when accessories needed", "needs_accessories", "true", "accessory_list")],
    },
    {
        "name": "Software Access Request",
        "short_description": "Request a licence or access to a business application.",
        "description": "Pick the application and the access level you need.",
        "variables": [
            {"name": "application", "question_text": "Application", "type": "5", "mandatory": "true", "order": 100,
             "choices": [("Microsoft 365", "m365"), ("Jira", "jira"), ("GitHub", "github"), ("Salesforce", "salesforce"), ("Other", "other")]},
            {"name": "other_application", "question_text": "Name of the application", "type": "6", "order": 200},
            {"name": "access_level", "question_text": "Access level", "type": "5", "mandatory": "true", "order": 300,
             "choices": [("Read only", "read"), ("Standard user", "user"), ("Administrator", "admin")]},
            {"name": "admin_reason", "question_text": "Why is admin access required?", "type": "2", "order": 400},
        ],
        "policies": [
            ("Show application name for Other", "application", "other", "other_application"),
            ("Show admin reason for Administrator", "access_level", "admin", "admin_reason"),
        ],
    },
    {
        "name": "VPN and Network Access",
        "short_description": "Request remote VPN access or a firewall rule change.",
        "description": "Describe the access you need and for how long.",
        "variables": [
            {"name": "access_type", "question_text": "Access type", "type": "5", "mandatory": "true", "order": 100,
             "choices": [("Remote VPN account", "vpn"), ("Firewall rule", "firewall"), ("Wi-Fi guest access", "guest")]},
            {"name": "target_system", "question_text": "Target system or IP range", "type": "6", "order": 200},
            {"name": "temporary", "question_text": "Is this temporary?", "type": "7", "order": 300},
            {"name": "end_date", "question_text": "Access end date", "type": "9", "order": 400},
            {"name": "details", "question_text": "Details", "type": "2", "mandatory": "true", "order": 500},
        ],
        "policies": [
            ("Show target for firewall", "access_type", "firewall", "target_system"),
            ("Show end date when temporary", "temporary", "true", "end_date"),
        ],
    },
    {
        "name": "Report Appliance Issue",
        "short_description": "Open a support case for a storage / gateway / VM Analytics appliance.",
        "description": "Used with the AI diagnostic assistant for appliance-support incidents.",
        "variables": [
            {"name": "appliance_type", "question_text": "Appliance type", "type": "5", "mandatory": "true", "order": 100,
             "choices": [("Storage array", "array"), ("Storage gateway", "gateway"), ("VM Analytics collector", "collector"), ("Upgrade edge agent", "upgrade")]},
            {"name": "site", "question_text": "Site / data centre", "type": "6", "mandatory": "true", "order": 200},
            {"name": "symptom", "question_text": "What is happening?", "type": "2", "mandatory": "true", "order": 300},
            {"name": "production_impact", "question_text": "Production impacted?", "type": "7", "order": 400},
            {"name": "impact_details", "question_text": "Describe the business impact", "type": "2", "order": 500},
        ],
        "policies": [("Show impact details when production impacted", "production_impact", "true", "impact_details")],
    },
]


def seed_catalog() -> None:
    log("== Catalog")
    category = first_id("sc_category", "title=Services") or first_id("sc_category", "active=true")
    catalog = first_id("sc_catalog", "title=Service Catalog") or first_id("sc_catalog", "active=true")
    for item in CATALOG:
        item_id = first_id("sc_cat_item", f"name={item['name']}")
        if item_id:
            log(f"  = {item['name']} (exists)")
            continue
        body = {
            "name": item["name"],
            "short_description": item["short_description"],
            "description": item["description"],
            "active": "true",
            "sc_catalogs": catalog,
            "category": category,
            "order": "100",
        }
        item_id = insert("sc_cat_item", {k: v for k, v in body.items() if v})
        ids: Dict[str, str] = {}
        for var in item["variables"]:
            vbody = {
                "cat_item": item_id,
                "name": var["name"],
                "question_text": var["question_text"],
                "type": var["type"],
                "mandatory": var.get("mandatory", "false"),
                "order": str(var["order"]),
                "active": "true",
            }
            vid = insert("item_option_new", vbody)
            ids[var["name"]] = vid
            for idx, (text, value) in enumerate(var.get("choices", []), start=1):
                insert("question_choice", {"question": vid, "text": text, "value": value, "order": str(idx * 100)})
        for title, dep_var, dep_value, target_var in item["policies"]:
            policy_id = insert(
                "catalog_ui_policy",
                {
                    "catalog_item": item_id,
                    "short_description": title,
                    "active": "true",
                    "applies_to": "item",
                    "catalog_conditions": f"IO:{ids[dep_var]}={dep_value}^EQ",
                    "reverse_if_false": "true",
                },
            )
            insert("catalog_ui_policy_action", {"ui_policy": policy_id, "catalog_variable": f"IO:{ids[target_var]}", "visible": "true", "mandatory": "true"})
        log(f"  + {item['name']}: {len(item['variables'])} variables, {len(item['policies'])} UI policies")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=["schema", "data", "catalog"])
    parser.add_argument("--dry-run", action="store_true", help="only verify credentials and print the plan")
    args = parser.parse_args()

    if not BASE or not PASS:
        log("Set SERVICENOW_INSTANCE_URL / SERVICENOW_USERNAME / SERVICENOW_PASSWORD in backend/.env first.")
        return 2
    who = api("GET", "/api/now/table/sys_user", params={"sysparm_query": f"user_name={USER}", "sysparm_fields": "user_name,name", "sysparm_limit": 1})
    log(f"Connected to {BASE} as {who[0]['user_name'] if who else USER}")
    if args.dry_run:
        for row in read_csv("import_order"):
            log(f"  would load {row['sheet_or_csv']} -> {row['servicenow_table']} ({len(read_csv(row['sheet_or_csv'])) if row['servicenow_table'] != 'N/A' else 0} rows)")
        return 0
    steps = [args.only] if args.only else ["schema", "data", "catalog"]
    if "schema" in steps:
        seed_schema()
    if "data" in steps:
        seed_data()
    if "catalog" in steps:
        seed_catalog()
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
