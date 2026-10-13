#!/usr/bin/env python
"""Configure allowlisted ServiceNow outbound webhooks for MCP graph sync.

This script intentionally does not contain credentials or public tunnel URLs.
It reads all sensitive values from environment variables and defaults to a
dry run unless --apply is passed.
"""

import argparse
import base64
import os
from typing import Dict, Iterable, List

import requests


DEFAULT_TABLE_ALLOWLIST = [
    "sc_cat_item",
    "item_option_new",
    "question_choice",
    "catalog_ui_policy",
    "catalog_ui_policy_action",
    "sc_request",
    "sc_req_item",
    "change_request",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure ServiceNow webhooks for MCP graph sync")
    parser.add_argument("--apply", action="store_true", help="Create webhook records instead of dry-running")
    parser.add_argument(
        "--table",
        action="append",
        dest="tables",
        help="Allowlisted table to configure. May be passed multiple times.",
    )
    args = parser.parse_args()

    instance_url = _require_env("SERVICENOW_INSTANCE_URL").rstrip("/")
    username = _require_env("SERVICENOW_USERNAME")
    password = _require_env("SERVICENOW_PASSWORD")
    webhook_url = _require_env("SERVICENOW_WEBHOOK_URL")
    webhook_secret = _require_env("SERVICENOW_WEBHOOK_SECRET")
    verify_ssl = _env_bool("SERVICENOW_VERIFY_SSL", default=True)

    tables = _validate_tables(args.tables or DEFAULT_TABLE_ALLOWLIST)
    headers = _basic_auth_headers(username, password)

    print("ServiceNow MCP webhook configuration")
    print(f"Instance: {instance_url}")
    print(f"Webhook URL: {webhook_url}")
    print(f"Tables: {', '.join(tables)}")
    print(f"Mode: {'apply' if args.apply else 'dry run'}")

    for table in tables:
        payload = _build_webhook_payload(table, webhook_url, webhook_secret)
        if not args.apply:
            print(f"[DRY RUN] Would configure webhook for {table}")
            continue

        response = requests.post(
            f"{instance_url}/api/now/table/sys_outbound_http_request",
            headers=headers,
            json=payload,
            verify=verify_ssl,
            timeout=20,
        )
        if response.status_code in {200, 201}:
            print(f"[OK] Configured webhook for {table}")
        else:
            print(f"[FAILED] {table}: HTTP {response.status_code} {response.text[:300]}")


def _build_webhook_payload(table: str, webhook_url: str, webhook_secret: str) -> Dict[str, str]:
    return {
        "name": f"MCP Neo4j Sync - {table}",
        "endpoint": webhook_url,
        "http_method": "POST",
        "table": table,
        "active": "true",
        "insert": "true",
        "update": "true",
        "delete": "true",
        "exclude_reference_link": "false",
        "http_headers": f"X-ServiceNow-Webhook-Secret: {webhook_secret}",
    }


def _validate_tables(tables: Iterable[str]) -> List[str]:
    requested = [str(table or "").strip() for table in tables]
    requested = [table for table in requested if table]
    invalid = sorted(set(requested) - set(DEFAULT_TABLE_ALLOWLIST))
    if invalid:
        allowed = ", ".join(DEFAULT_TABLE_ALLOWLIST)
        raise ValueError(f"Unsupported webhook table(s): {', '.join(invalid)}. Allowed: {allowed}")
    return requested


def _basic_auth_headers(username: str, password: str) -> Dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    main()
