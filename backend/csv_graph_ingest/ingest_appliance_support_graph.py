from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from neo4j import GraphDatabase


CSV_FILES = {
    "assets": "u_app_asset.csv",
    "audit_events": "u_app_case_audit_event.csv",
    "case_evidence": "u_app_case_evidence.csv",
    "case_recommendations": "u_app_case_recommendation.csv",
    "ci_context": "u_app_ci_context.csv",
    "customer_context": "u_app_customer_context.csv",
    "diagnostic_sessions": "u_app_diagnostic_session.csv",
    "escalation_packets": "u_app_escalation_packet.csv",
    "gateway_context": "u_app_gateway_context.csv",
    "graph_relationships": "u_app_graph_relationship.csv",
    "historical_case_matches": "u_app_historical_case_match.csv",
    "hypothesis_score_log": "u_app_hypothesis_score_log.csv",
    "incidents": "incident.csv",
    "kb_runbooks": "u_app_kb_runbook.csv",
    "known_issues": "u_app_known_issue.csv",
    "llm_question_log": "u_app_llm_question_log.csv",
    "monitoring_signals": "u_app_monitoring_signal.csv",
    "problems": "problem.csv",
    "product_models": "u_app_product_model.csv",
    "source_systems": "u_app_source_system.csv",
    "upgrade_context": "u_app_upgrade_context.csv",
    "vmware_context": "u_app_vmware_context.csv",
}

ISSUE_DOMAIN_MAP = {
    "VMware / VM Analytics": {
        "issue_id": "ISSUE_VMWARE_VM_ANALYTICS",
        "name": "VM Analytics is not showing data in Pure1",
        "category": "VMware / VM Analytics",
    },
    "Performance": {
        "issue_id": "ISSUE_PERFORMANCE",
        "name": "Database workload latency increased on production storage",
        "category": "Performance",
    },
    "Capacity": {
        "issue_id": "ISSUE_CAPACITY",
        "name": "Storage capacity is approaching critical threshold",
        "category": "Capacity",
    },
    "Gateway / Hybrid Cloud": {
        "issue_id": "ISSUE_GATEWAY_HYBRID_CLOUD",
        "name": "Cloud-backed storage access is slow from branch site",
        "category": "Gateway / Hybrid Cloud",
    },
    "Upgrade": {
        "issue_id": "ISSUE_UPGRADE",
        "name": "Self-service appliance upgrade failed during pre-check",
        "category": "Upgrade",
    },
    "Security / Resilience": {
        "issue_id": "ISSUE_SECURITY_RESILIENCE",
        "name": "Unusual data activity warning on production array",
        "category": "Security / Resilience",
    },
}

SOURCE_ALIASES = {
    "servicenow": "SRC_SN",
    "servicenow itsm": "SRC_SN",
    "cmdb": "SRC_CMDB",
    "cmdb / asset registry": "SRC_CMDB",
    "asset": "SRC_CMDB",
    "monitoring": "SRC_MON",
    "appliance monitoring": "SRC_MON",
    "fleet health": "SRC_MON",
    "kb": "SRC_KB",
    "runbook": "SRC_KB",
    "knowledge base": "SRC_KB",
    "historical cases": "SRC_CASE_HIST",
    "historical case": "SRC_CASE_HIST",
    "historical case pattern store": "SRC_CASE_HIST",
    "entitlement": "SRC_ENT",
    "contract": "SRC_ENT",
    "vmware": "SRC_VMWARE",
    "vcenter": "SRC_VMWARE",
    "network": "SRC_NET",
    "upgrade": "SRC_UPG",
}

TYPE_TO_NODE = {
    "Issue": (("Issue", "IssueTaxonomy"), "issue_id"),
    "RootCause": (("RootCause",), "cause_id"),
    "Condition": (("Condition", "ConditionDefinition"), "condition_id"),
    "Runbook": (("Runbook",), "external_id"),
    "KnownIssue": (("KnownIssue",), "external_id"),
}

CONTEXT_REL_TYPES = {
    "VMwareContext": "HAS_VMWARE_CONTEXT",
    "GatewayContext": "HAS_GATEWAY_CONTEXT",
    "UpgradeContext": "HAS_UPGRADE_CONTEXT",
}

CONSTRAINTS = [
    ("customer_external_id", "Customer", "external_id"),
    ("product_model_external_id", "ProductModel", "external_id"),
    ("asset_external_id", "Asset", "external_id"),
    ("ci_external_id", "ConfigurationItem", "external_id"),
    ("site_id", "Site", "site_id"),
    ("support_domain_id", "SupportDomain", "domain_id"),
    ("support_group_id", "SupportGroup", "group_id"),
    ("source_system_external_id", "SourceSystem", "external_id"),
    ("incident_external_id", "Incident", "external_id"),
    ("incident_number", "Incident", "number"),
    ("problem_external_id", "Problem", "external_id"),
    ("diagnostic_session_external_id", "DiagnosticSession", "external_id"),
    ("issue_issue_id", "Issue", "issue_id"),
    ("root_cause_id", "RootCause", "cause_id"),
    ("condition_id", "Condition", "condition_id"),
    ("known_issue_external_id", "KnownIssue", "external_id"),
    ("runbook_external_id", "Runbook", "external_id"),
    ("resolution_id", "Resolution", "resolution_id"),
    ("llm_question_external_id", "LLMQuestion", "external_id"),
    ("evidence_external_id", "Evidence", "external_id"),
    ("monitoring_signal_external_id", "MonitoringSignal", "external_id"),
    ("vmware_context_external_id", "VMwareContext", "external_id"),
    ("gateway_context_external_id", "GatewayContext", "external_id"),
    ("upgrade_context_external_id", "UpgradeContext", "external_id"),
    ("context_observation_external_id", "ContextObservation", "external_id"),
    ("hypothesis_score_event_external_id", "HypothesisScoreEvent", "external_id"),
    ("recommendation_external_id", "Recommendation", "external_id"),
    ("escalation_packet_external_id", "EscalationPacket", "external_id"),
    ("historical_case_match_external_id", "HistoricalCaseMatch", "external_id"),
    ("historical_incident_number", "HistoricalIncident", "number"),
    ("audit_event_external_id", "AuditEvent", "external_id"),
    ("actor_id", "Actor", "actor_id"),
    ("case_pattern_id", "CasePattern", "pattern_id"),
]

FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX app_support_search_text IF NOT EXISTS
FOR (n:Incident|CaseRecord|Problem|DiagnosticSession|Issue|IssueTaxonomy|RootCause|Condition|ConditionDefinition|KnownIssue|Runbook|Resolution|Recommendation|Evidence|LLMQuestion|ContextObservation|Product|ProductModel|ConfigurationItem|Asset|Customer|CasePattern)
ON EACH [n.search_text, n.name, n.title, n.description, n.short_description, n.summary, n.number]
""".strip()


def clean(value: Any) -> str:
    return str(value or "").strip()


def empty_to_none(value: Any) -> Optional[str]:
    text = clean(value)
    return text or None


def norm_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", clean(value).lower()).strip("_")
    return normalized or "unknown"


def parse_float(value: Any) -> Optional[float]:
    text = clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any) -> Optional[int]:
    text = clean(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_bool(value: Any) -> Optional[bool]:
    text = clean(value).lower()
    if text in {"yes", "true", "1", "enabled", "healthy", "passed"}:
        return True
    if text in {"no", "false", "0", "disabled", "missing", "failed"}:
        return False
    return None


def parse_json_list(value: Any) -> List[str]:
    text = clean(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return split_multi(text)
    if not isinstance(parsed, list):
        return []
    return dedupe([clean(item) for item in parsed if clean(item)])


def split_multi(value: Any) -> List[str]:
    text = clean(value)
    if not text:
        return []
    return dedupe([part.strip() for part in re.split(r"\s*;\s*|\s*,\s*", text) if part.strip()])


def dedupe(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = clean(value)
        lowered = text.lower()
        if not text or lowered in seen:
            continue
        seen.add(lowered)
        result.append(text)
    return result


def search_text(*parts: Any) -> str:
    values: List[str] = []
    for part in parts:
        if isinstance(part, list):
            values.extend(clean(item) for item in part)
        else:
            values.append(clean(part))
    return " | ".join(dedupe(value for value in values if value))


def rel_type(value: str) -> str:
    candidate = clean(value).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", candidate):
        raise ValueError(f"Unsafe relationship type: {value}")
    return candidate


class GraphWriter:
    def __init__(self, uri: str, username: str, password: str, database: str, dry_run: bool = False) -> None:
        self.uri = uri
        self.username = username
        self.password = password
        self.database = database or "neo4j"
        self.dry_run = dry_run
        self.driver = None
        self.session = None
        self.node_keys = set()
        self.rel_keys = set()
        self.rel_counter: Counter[str] = Counter()
        self.node_batches: Dict[Tuple[Tuple[str, ...], str], List[Dict[str, Any]]] = defaultdict(list)
        self.rel_batches: Dict[Tuple[str, str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)

    def __enter__(self) -> "GraphWriter":
        if not self.dry_run:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))
            self.driver.verify_connectivity()
            self.session = self.driver.session(database=self.database)
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.session is not None:
            self.flush()
        if self.session is not None:
            self.session.close()
        if self.driver is not None:
            self.driver.close()

    def ensure_schema(self) -> None:
        if self.dry_run:
            return
        for name, label, property_name in CONSTRAINTS:
            self.session.run(
                f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{property_name} IS UNIQUE"
            ).consume()
        self.session.run(FULLTEXT_INDEX).consume()

    def merge_node(
        self,
        labels: Sequence[str],
        key_property: str,
        key_value: str,
        properties: Dict[str, Any],
    ) -> None:
        labels = tuple(label for label in labels if label)
        if not labels or not key_value:
            return
        label_clause = ":" + ":".join(labels)
        props = compact_props({**properties, key_property: key_value})
        props["search_text"] = props.get("search_text") or search_text(*props.values())
        self.node_keys.add((labels[0], key_property, key_value))
        if self.dry_run:
            return
        self.node_batches[(labels, key_property)].append({"key_value": key_value, "props": props})

    def merge_relationship(
        self,
        from_label: str,
        from_key: str,
        from_value: str,
        relationship_type: str,
        to_label: str,
        to_key: str,
        to_value: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not from_value or not to_value:
            return
        relationship_type = rel_type(relationship_type)
        props = compact_props(properties or {})
        rel_key = (from_label, from_key, from_value, relationship_type, to_label, to_key, to_value)
        self.rel_keys.add(rel_key)
        self.rel_counter[relationship_type] += 1
        if self.dry_run:
            return
        self.rel_batches[(from_label, from_key, relationship_type, to_label, to_key)].append(
            {"from_value": from_value, "to_value": to_value, "props": props}
        )

    def flush(self) -> None:
        if self.dry_run or self.session is None:
            return
        for (labels, key_property), rows in self.node_batches.items():
            if not rows:
                continue
            label_clause = ":" + ":".join(labels)
            self.session.run(
                f"""
UNWIND $rows AS row
MERGE (n{label_clause} {{{key_property}: row.key_value}})
SET n += row.props
""".strip(),
                {"rows": rows},
            ).consume()
        for (from_label, from_key, relationship_type, to_label, to_key), rows in self.rel_batches.items():
            if not rows:
                continue
            self.session.run(
                f"""
UNWIND $rows AS row
MATCH (a:{from_label} {{{from_key}: row.from_value}})
MATCH (b:{to_label} {{{to_key}: row.to_value}})
MERGE (a)-[r:{relationship_type}]->(b)
SET r += row.props
""".strip(),
                {"rows": rows},
            ).consume()
        self.node_batches.clear()
        self.rel_batches.clear()

    def summary(self) -> Dict[str, Any]:
        return {
            "unique_nodes_touched": len(self.node_keys),
            "unique_relationships_touched": len(self.rel_keys),
            "relationship_types": dict(sorted(self.rel_counter.items())),
        }


def compact_props(properties: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    for key, value in properties.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list):
            filtered = [item for item in value if item not in (None, "")]
            if filtered:
                result[key] = filtered
            continue
        result[key] = value
    return result


class ApplianceSupportGraphIngestor:
    def __init__(self, csv_dir: Path, writer: GraphWriter) -> None:
        self.csv_dir = csv_dir
        self.writer = writer
        self.rows = {name: self.load_csv(filename) for name, filename in CSV_FILES.items()}
        self.source_name_to_id: Dict[str, str] = {}
        self.cause_name_to_id: Dict[str, str] = {}
        self.issue_id_by_domain: Dict[str, str] = {
            spec["category"].lower(): spec["issue_id"] for spec in ISSUE_DOMAIN_MAP.values()
        }

    def load_csv(self, filename: str) -> List[Dict[str, str]]:
        path = self.csv_dir / filename
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def ingest(self) -> Dict[str, Any]:
        self.writer.ensure_schema()
        self.ingest_reference_nodes()
        self.ingest_knowledge_nodes()
        self.ingest_cases()
        self.ingest_diagnostic_execution()
        self.ingest_signals_and_contexts()
        self.ingest_outputs_and_governance()
        return self.writer.summary()

    def ingest_reference_nodes(self) -> None:
        for row in self.rows["source_systems"]:
            source_id = clean(row["u_external_id"])
            self.source_name_to_id[clean(row["u_source_name"]).lower()] = source_id
            self.source_name_to_id[source_id.lower()] = source_id
            self.writer.merge_node(
                ("SourceSystem",),
                "external_id",
                source_id,
                {
                    "name": clean(row["u_source_name"]),
                    "system_type": clean(row["u_system_type"]),
                    "data_contribution": clean(row["u_data_contribution"]),
                    "phase": clean(row["u_phase"]),
                    "trust_level": clean(row["u_trust_level"]),
                    "search_text": search_text(row["u_source_name"], row["u_system_type"], row["u_data_contribution"]),
                },
            )
        for alias, source_id in SOURCE_ALIASES.items():
            self.source_name_to_id[alias] = source_id

        for row in self.rows["customer_context"]:
            customer_id = clean(row["u_external_id"])
            site_id = self.merge_site(row["u_primary_site"], row["u_region"])
            self.writer.merge_node(
                ("Customer",),
                "external_id",
                customer_id,
                {
                    "name": clean(row["u_customer_name"]),
                    "industry": clean(row["u_industry"]),
                    "support_tier": clean(row["u_support_tier"]),
                    "primary_site": clean(row["u_primary_site"]),
                    "region": clean(row["u_region"]),
                    "support_coverage": clean(row["u_support_coverage"]),
                    "criticality": clean(row["u_customer_criticality"]),
                    "entitlement_source": clean(row["u_entitlement_source"]),
                    "search_text": search_text(row["u_customer_name"], row["u_industry"], row["u_primary_site"], row["u_support_tier"]),
                },
            )
            self.writer.merge_relationship("Customer", "external_id", customer_id, "PRIMARY_SITE", "Site", "site_id", site_id)
            entitlement_source = self.source_id(row["u_entitlement_source"])
            self.writer.merge_relationship(
                "Customer",
                "external_id",
                customer_id,
                "ENTITLED_BY",
                "SourceSystem",
                "external_id",
                entitlement_source,
            )

        for row in self.rows["product_models"]:
            model_id = clean(row["u_external_id"])
            domain_id = self.merge_domain(row["u_family"] or row["u_model_type"])
            group_id = self.merge_group(row["u_owner_group"])
            self.writer.merge_node(
                ("ProductModel", "Product"),
                "external_id",
                model_id,
                {
                    "model": clean(row["u_model_name"]),
                    "name": clean(row["u_model_name"]),
                    "model_type": clean(row["u_model_type"]),
                    "family": clean(row["u_family"]),
                    "supported_version_family": clean(row["u_supported_version_family"]),
                    "description": clean(row["u_description"]),
                    "owner_group": clean(row["u_owner_group"]),
                    "search_text": search_text(row["u_model_name"], row["u_model_type"], row["u_family"], row["u_description"]),
                },
            )
            self.writer.merge_relationship("ProductModel", "external_id", model_id, "IN_DOMAIN", "SupportDomain", "domain_id", domain_id)
            self.writer.merge_relationship("ProductModel", "external_id", model_id, "OWNED_BY", "SupportGroup", "group_id", group_id)

        for row in self.rows["assets"]:
            asset_id = clean(row["u_external_id"])
            site_id = self.merge_site(row["u_install_location"], "")
            self.writer.merge_node(
                ("Asset",),
                "external_id",
                asset_id,
                {
                    "asset_tag": clean(row["u_asset_tag"]),
                    "serial_number": clean(row["u_serial_number"]),
                    "customer_id": clean(row["u_customer_id"]),
                    "customer_name": clean(row["u_customer_name"]),
                    "product_model_id": clean(row["u_product_model_id"]),
                    "model_name": clean(row["u_model_name"]),
                    "contract_level": clean(row["u_contract_level"]),
                    "install_location": clean(row["u_install_location"]),
                    "install_date": clean(row["u_install_date"]),
                    "warranty_status": clean(row["u_warranty_status"]),
                    "lifecycle_status": clean(row["u_lifecycle_status"]),
                    "search_text": search_text(row["u_asset_tag"], row["u_serial_number"], row["u_customer_name"], row["u_model_name"], row["u_install_location"]),
                },
            )
            self.writer.merge_relationship("Customer", "external_id", row["u_customer_id"], "OWNS", "Asset", "external_id", asset_id)
            self.writer.merge_relationship("Asset", "external_id", asset_id, "INSTANCE_OF", "ProductModel", "external_id", row["u_product_model_id"])
            self.writer.merge_relationship("Asset", "external_id", asset_id, "INSTALLED_AT", "Site", "site_id", site_id)

        for row in self.rows["ci_context"]:
            ci_id = clean(row["u_external_id"])
            site_id = self.merge_site(row["u_site"], "")
            source_id = self.source_id(row["u_cmdb_source"])
            self.writer.merge_node(
                ("ConfigurationItem",),
                "external_id",
                ci_id,
                {
                    "name": clean(row["u_ci_name"]),
                    "ci_name": clean(row["u_ci_name"]),
                    "asset_external_id": clean(row["u_asset_external_id"]),
                    "serial_number": clean(row["u_serial_number"]),
                    "customer_id": clean(row["u_customer_id"]),
                    "product_model_id": clean(row["u_product_model_id"]),
                    "ci_class": clean(row["u_ci_class"]),
                    "environment": clean(row["u_environment"]),
                    "version": clean(row["u_version"]),
                    "site": clean(row["u_site"]),
                    "monitoring_enabled": clean(row["u_monitoring_enabled"]),
                    "monitoring_enabled_bool": parse_bool(row["u_monitoring_enabled"]),
                    "operational_status": clean(row["u_operational_status"]),
                    "cmdb_source": clean(row["u_cmdb_source"]),
                    "search_text": search_text(row["u_ci_name"], row["u_ci_class"], row["u_environment"], row["u_site"], row["u_operational_status"]),
                },
            )
            self.writer.merge_relationship("Customer", "external_id", row["u_customer_id"], "HAS_CI", "ConfigurationItem", "external_id", ci_id)
            self.writer.merge_relationship("ConfigurationItem", "external_id", ci_id, "REALIZES_ASSET", "Asset", "external_id", row["u_asset_external_id"])
            self.writer.merge_relationship("ConfigurationItem", "external_id", ci_id, "RUNS_MODEL", "ProductModel", "external_id", row["u_product_model_id"])
            self.writer.merge_relationship("ConfigurationItem", "external_id", ci_id, "LOCATED_AT", "Site", "site_id", site_id)
            self.writer.merge_relationship("ConfigurationItem", "external_id", ci_id, "SOURCED_FROM", "SourceSystem", "external_id", source_id)

    def ingest_knowledge_nodes(self) -> None:
        for issue in self.issue_specs():
            domain_id = self.merge_domain(issue["category"])
            self.writer.merge_node(
                ("Issue", "IssueTaxonomy"),
                "issue_id",
                issue["issue_id"],
                {
                    "name": issue["name"],
                    "category": issue["category"],
                    "subcategory": issue["subcategory"],
                    "description": issue["description"],
                    "aliases": issue["aliases"],
                    "search_text": search_text(issue["issue_id"], issue["name"], issue["category"], issue["subcategory"], issue["description"], issue["aliases"]),
                },
            )
            self.writer.merge_relationship("Issue", "issue_id", issue["issue_id"], "IN_DOMAIN", "SupportDomain", "domain_id", domain_id)

        for row in self.rows["known_issues"]:
            known_issue_id = clean(row["u_external_id"])
            domain_id = self.merge_domain(row["u_domain"])
            source_id = self.source_id(row["u_source_system"])
            self.writer.merge_node(
                ("KnownIssue",),
                "external_id",
                known_issue_id,
                {
                    "title": clean(row["u_known_issue_title"]),
                    "name": clean(row["u_known_issue_title"]),
                    "domain": clean(row["u_domain"]),
                    "severity": clean(row["u_severity"]),
                    "detection_signal": clean(row["u_detection_signal"]),
                    "recommended_response": clean(row["u_recommended_response"]),
                    "status": clean(row["u_status"]),
                    "source_system": clean(row["u_source_system"]),
                    "search_text": search_text(row["u_known_issue_title"], row["u_domain"], row["u_severity"], row["u_detection_signal"], row["u_recommended_response"]),
                },
            )
            self.writer.merge_relationship("KnownIssue", "external_id", known_issue_id, "IN_DOMAIN", "SupportDomain", "domain_id", domain_id)
            self.writer.merge_relationship("KnownIssue", "external_id", known_issue_id, "SOURCED_FROM", "SourceSystem", "external_id", source_id)
            issue_id = self.issue_for_domain(row["u_domain"])
            self.writer.merge_relationship("Issue", "issue_id", issue_id, "HAS_KNOWN_ISSUE_SIGNAL", "KnownIssue", "external_id", known_issue_id)

        for row in self.rows["kb_runbooks"]:
            runbook_id = clean(row["u_external_id"])
            domain_id = self.merge_domain(row["u_category"])
            group_id = self.merge_group(row["u_owner_group"])
            source_id = self.source_id(row["u_source_system"])
            self.writer.merge_node(
                ("Runbook",),
                "external_id",
                runbook_id,
                {
                    "title": clean(row["u_title"]),
                    "name": clean(row["u_title"]),
                    "category": clean(row["u_category"]),
                    "summary": clean(row["u_summary"]),
                    "owner_group": clean(row["u_owner_group"]),
                    "approval_status": clean(row["u_approval_status"]),
                    "last_reviewed": clean(row["u_last_reviewed"]),
                    "source_system": clean(row["u_source_system"]),
                    "search_text": search_text(row["u_title"], row["u_category"], row["u_summary"], row["u_owner_group"]),
                },
            )
            self.writer.merge_relationship("Runbook", "external_id", runbook_id, "IN_DOMAIN", "SupportDomain", "domain_id", domain_id)
            self.writer.merge_relationship("Runbook", "external_id", runbook_id, "OWNED_BY", "SupportGroup", "group_id", group_id)
            self.writer.merge_relationship("Runbook", "external_id", runbook_id, "SOURCED_FROM", "SourceSystem", "external_id", source_id)

        self.ingest_conditions()
        self.ingest_root_causes()
        self.ingest_graph_relationships()
        self.ingest_resolution_nodes()

    def ingest_conditions(self) -> None:
        specs: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"questions": [], "answer_options": [], "observed_values": [], "sources": []})
        for row in self.rows["graph_relationships"]:
            if row["u_to_type"] == "Condition":
                specs[clean(row["u_to_id"])]["sources"].append(row["u_source_system"])
        for row in self.rows["llm_question_log"]:
            condition_id = clean(row["u_target_condition_id"])
            if condition_id:
                specs[condition_id]["questions"].append(row["u_question_text"])
                specs[condition_id]["answer_type"] = clean(row["u_answer_type"])
                specs[condition_id]["answer_options"].extend(parse_json_list(row["u_answer_options_json"]))
                specs[condition_id]["sources"].extend(split_multi(row["u_context_sources_used"]))
        for row in self.rows["case_evidence"]:
            condition_id = clean(row["u_condition_id"])
            if condition_id:
                specs[condition_id]["observed_values"].append(row["u_observed_value"])
                specs[condition_id]["sources"].append(row["u_source_system"])

        for condition_id, spec in sorted(specs.items()):
            question_counter = Counter(spec["questions"])
            canonical_question = question_counter.most_common(1)[0][0] if question_counter else ""
            observed_values = dedupe(spec["observed_values"])
            answer_options = dedupe(spec["answer_options"])
            self.writer.merge_node(
                ("Condition", "ConditionDefinition"),
                "condition_id",
                condition_id,
                {
                    "name": condition_id.replace("COND_", "").replace("_", " ").title(),
                    "canonical_question": canonical_question,
                    "question_count": len(spec["questions"]),
                    "answer_type": spec.get("answer_type", ""),
                    "answer_options": answer_options,
                    "observed_values": observed_values,
                    "condition_type": "diagnostic_check",
                    "observation_source": "; ".join(dedupe(spec["sources"])),
                    "search_text": search_text(condition_id, canonical_question, answer_options, observed_values),
                },
            )

    def ingest_root_causes(self) -> None:
        cause_names: Dict[str, str] = {}
        for row in self.rows["case_recommendations"]:
            cause_id = clean(row["u_likely_cause_id"])
            cause_name = clean(row["u_likely_cause"])
            if cause_id and cause_name:
                cause_names[cause_id] = cause_name
                self.cause_name_to_id[cause_name.lower()] = cause_id
        for row in self.rows["hypothesis_score_log"]:
            cause_id = clean(row["u_cause_id"])
            cause_name = clean(row["u_cause_name"])
            if cause_id and cause_name:
                cause_names[cause_id] = cause_name
                self.cause_name_to_id[cause_name.lower()] = cause_id
        for row in self.rows["graph_relationships"]:
            for type_field, id_field in (("u_from_type", "u_from_id"), ("u_to_type", "u_to_id")):
                if row[type_field] == "RootCause":
                    cause_id = clean(row[id_field])
                    cause_names.setdefault(cause_id, cause_id.replace("CAUSE_", "").replace("_", " ").title())

        for cause_id, cause_name in sorted(cause_names.items()):
            self.cause_name_to_id[cause_name.lower()] = cause_id
            self.writer.merge_node(
                ("RootCause",),
                "cause_id",
                cause_id,
                {
                    "name": cause_name,
                    "description": cause_name,
                    "search_text": search_text(cause_id, cause_name),
                },
            )

    def ingest_graph_relationships(self) -> None:
        for row in self.rows["graph_relationships"]:
            from_meta = TYPE_TO_NODE.get(row["u_from_type"])
            to_meta = TYPE_TO_NODE.get(row["u_to_type"])
            if not from_meta or not to_meta:
                continue
            from_labels, from_key = from_meta
            to_labels, to_key = to_meta
            relationship = rel_type(row["u_relationship"])
            self.writer.merge_relationship(
                from_labels[0],
                from_key,
                row["u_from_id"],
                relationship,
                to_labels[0],
                to_key,
                row["u_to_id"],
                {
                    "external_id": clean(row["u_external_id"]),
                    "weight": parse_float(row["u_weight"]),
                    "explanation": clean(row["u_explanation"]),
                    "source_system": clean(row["u_source_system"]),
                },
            )

    def ingest_resolution_nodes(self) -> None:
        runbooks = {row["u_external_id"]: row for row in self.rows["kb_runbooks"]}
        for row in self.rows["graph_relationships"]:
            if row["u_from_type"] != "RootCause" or row["u_relationship"] != "GUIDED_BY" or row["u_to_type"] != "Runbook":
                continue
            cause_id = clean(row["u_from_id"])
            runbook_id = clean(row["u_to_id"])
            runbook = runbooks.get(runbook_id, {})
            resolution_id = f"RES_{cause_id}_{runbook_id}"
            title = clean(runbook.get("u_title")) or f"Follow runbook {runbook_id}"
            summary = clean(runbook.get("u_summary"))
            self.writer.merge_node(
                ("Resolution",),
                "resolution_id",
                resolution_id,
                {
                    "name": title,
                    "title": title,
                    "resolution_type": "Runbook guided action",
                    "steps": [summary] if summary else [],
                    "description": summary,
                    "runbook_id": runbook_id,
                    "cause_id": cause_id,
                    "search_text": search_text(title, summary, cause_id, runbook_id),
                },
            )
            self.writer.merge_relationship("RootCause", "cause_id", cause_id, "RESOLVED_BY", "Resolution", "resolution_id", resolution_id)
            self.writer.merge_relationship("Resolution", "resolution_id", resolution_id, "USES_RUNBOOK", "Runbook", "external_id", runbook_id)

    def ingest_cases(self) -> None:
        for row in self.rows["incidents"]:
            incident_id = clean(row["u_external_id"])
            domain_id = self.merge_domain(row["category"])
            issue_id = self.issue_for_domain(row["category"])
            group_id = self.merge_group(row["assignment_group"])
            self.writer.merge_node(
                ("Incident", "CaseRecord"),
                "external_id",
                incident_id,
                {
                    "number": clean(row["number"]),
                    "caller_id": clean(row["caller_id"]),
                    "company": clean(row["company"]),
                    "contact_type": clean(row["contact_type"]),
                    "category": clean(row["category"]),
                    "subcategory": clean(row["subcategory"]),
                    "short_description": clean(row["short_description"]),
                    "description": clean(row["description"]),
                    "cmdb_ci": clean(row["cmdb_ci"]),
                    "appliance_ci_id": clean(row["u_appliance_ci_id"]),
                    "customer_id": clean(row["u_customer_id"]),
                    "source_systems_used": split_multi(row["u_source_systems_used"]),
                    "assignment_group": clean(row["assignment_group"]),
                    "priority": clean(row["priority"]),
                    "impact": clean(row["impact"]),
                    "urgency": clean(row["urgency"]),
                    "state": clean(row["state"]),
                    "ai_triage_status": clean(row["u_ai_triage_status"]),
                    "likely_cause": clean(row["u_likely_cause"]),
                    "confidence_score": parse_float(row["u_confidence_score"]),
                    "opened_at": clean(row["opened_at"]),
                    "sys_created_on": clean(row["sys_created_on"]),
                    "sys_updated_on": clean(row["sys_updated_on"]),
                    "resolved_at": clean(row["resolved_at"]),
                    "closed_at": clean(row["closed_at"]),
                    "search_text": search_text(row["number"], row["category"], row["subcategory"], row["short_description"], row["description"], row["u_likely_cause"], row["cmdb_ci"], row["company"]),
                },
            )
            self.writer.merge_relationship("Incident", "external_id", incident_id, "REPORTED_FOR", "Customer", "external_id", row["u_customer_id"])
            self.writer.merge_relationship("Incident", "external_id", incident_id, "AFFECTS_CI", "ConfigurationItem", "external_id", row["u_appliance_ci_id"])
            self.writer.merge_relationship("Incident", "external_id", incident_id, "IN_DOMAIN", "SupportDomain", "domain_id", domain_id)
            self.writer.merge_relationship("Incident", "external_id", incident_id, "CLASSIFIED_AS", "Issue", "issue_id", issue_id)
            self.writer.merge_relationship("Incident", "external_id", incident_id, "ASSIGNED_TO", "SupportGroup", "group_id", group_id)
            cause_id = self.cause_id_for_name(row["u_likely_cause"])
            self.writer.merge_relationship("Incident", "external_id", incident_id, "LIKELY_CAUSE", "RootCause", "cause_id", cause_id)
            for source_name in split_multi(row["u_source_systems_used"]):
                self.writer.merge_relationship("Incident", "external_id", incident_id, "USED_SOURCE", "SourceSystem", "external_id", self.source_id(source_name))

        for row in self.rows["diagnostic_sessions"]:
            session_id = clean(row["u_external_id"])
            self.writer.merge_node(
                ("DiagnosticSession",),
                "external_id",
                session_id,
                {
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "incident_number": clean(row["u_incident_number"]),
                    "customer_id": clean(row["u_customer_id"]),
                    "appliance_ci_id": clean(row["u_appliance_ci_id"]),
                    "status": clean(row["u_status"]),
                    "runtime_mode": clean(row["u_runtime_mode"]),
                    "context_sources": parse_json_list(row["u_context_sources_json"]),
                    "initial_symptom": clean(row["u_initial_symptom"]),
                    "started_at": clean(row["u_started_at"]),
                    "completed_at": clean(row["u_completed_at"]),
                    "search_text": search_text(row["u_incident_number"], row["u_status"], row["u_runtime_mode"], row["u_initial_symptom"]),
                },
            )
            self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], "HAS_DIAGNOSTIC_SESSION", "DiagnosticSession", "external_id", session_id)
            self.writer.merge_relationship("DiagnosticSession", "external_id", session_id, "DIAGNOSES", "Incident", "external_id", row["u_incident_external_id"])
            self.writer.merge_relationship("DiagnosticSession", "external_id", session_id, "FOR_CUSTOMER", "Customer", "external_id", row["u_customer_id"])
            self.writer.merge_relationship("DiagnosticSession", "external_id", session_id, "FOR_CI", "ConfigurationItem", "external_id", row["u_appliance_ci_id"])
            for source_name in parse_json_list(row["u_context_sources_json"]):
                self.writer.merge_relationship("DiagnosticSession", "external_id", session_id, "USES_SOURCE", "SourceSystem", "external_id", self.source_id(source_name))

        for row in self.rows["problems"]:
            problem_id = clean(row["u_external_id"])
            domain_id = self.merge_domain(row["u_problem_domain"])
            issue_id = self.issue_for_domain(row["u_problem_domain"])
            self.writer.merge_node(
                ("Problem",),
                "external_id",
                problem_id,
                {
                    "number": clean(row["number"]),
                    "short_description": clean(row["short_description"]),
                    "problem_domain": clean(row["u_problem_domain"]),
                    "related_incidents": parse_json_list(row["u_related_incidents_json"]),
                    "detected_pattern": clean(row["u_detected_pattern"]),
                    "business_impact": clean(row["u_business_impact"]),
                    "state": clean(row["state"]),
                    "opened_at": clean(row["opened_at"]),
                    "search_text": search_text(row["number"], row["short_description"], row["u_problem_domain"], row["u_detected_pattern"], row["u_business_impact"]),
                },
            )
            self.writer.merge_relationship("Problem", "external_id", problem_id, "IN_DOMAIN", "SupportDomain", "domain_id", domain_id)
            self.writer.merge_relationship("Problem", "external_id", problem_id, "HAS_PATTERN_ISSUE", "Issue", "issue_id", issue_id)
            for incident_id in parse_json_list(row["u_related_incidents_json"]):
                self.writer.merge_relationship("Problem", "external_id", problem_id, "SUMMARIZES_PATTERN_FROM", "Incident", "external_id", incident_id)

    def ingest_diagnostic_execution(self) -> None:
        for row in self.rows["llm_question_log"]:
            question_id = clean(row["u_external_id"])
            condition_id = clean(row["u_target_condition_id"])
            evidence_id = clean(row["u_evidence_id"])
            self.writer.merge_node(
                ("LLMQuestion",),
                "external_id",
                question_id,
                {
                    "question_text": clean(row["u_question_text"]),
                    "name": clean(row["u_question_text"]),
                    "target_condition_id": condition_id,
                    "answer_type": clean(row["u_answer_type"]),
                    "answer_options": parse_json_list(row["u_answer_options_json"]),
                    "reason_for_question": clean(row["u_reason_for_question"]),
                    "context_sources_used": split_multi(row["u_context_sources_used"]),
                    "llm_model": clean(row["u_llm_model"]),
                    "prompt_version": clean(row["u_prompt_version"]),
                    "guardrail_status": clean(row["u_guardrail_status"]),
                    "generated_at": clean(row["u_generated_at"]),
                    "answer_received": clean(row["u_answer_received"]),
                    "answer_received_at": clean(row["u_answer_received_at"]),
                    "evidence_id": evidence_id,
                    "search_text": search_text(row["u_question_text"], row["u_answer_options_json"], row["u_reason_for_question"], row["u_answer_received"]),
                },
            )
            if evidence_id:
                self.writer.merge_node(
                    ("Evidence",),
                    "external_id",
                    evidence_id,
                    {"name": evidence_id, "search_text": evidence_id},
                )
            self.writer.merge_relationship("DiagnosticSession", "external_id", row["u_session_id"], "ASKED", "LLMQuestion", "external_id", question_id)
            self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], "HAS_QUESTION", "LLMQuestion", "external_id", question_id)
            self.writer.merge_relationship("LLMQuestion", "external_id", question_id, "TARGETS_CONDITION", "Condition", "condition_id", condition_id)
            self.writer.merge_relationship("LLMQuestion", "external_id", question_id, "PRODUCED_EVIDENCE", "Evidence", "external_id", evidence_id)
            for source_name in split_multi(row["u_context_sources_used"]):
                self.writer.merge_relationship("LLMQuestion", "external_id", question_id, "USED_SOURCE", "SourceSystem", "external_id", self.source_id(source_name))

        for row in self.rows["case_evidence"]:
            evidence_id = clean(row["u_external_id"])
            condition_id = clean(row["u_condition_id"])
            source_id = self.source_id(row["u_source_system"])
            self.writer.merge_node(
                ("Evidence",),
                "external_id",
                evidence_id,
                {
                    "session_id": clean(row["u_session_id"]),
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "evidence_type": clean(row["u_evidence_type"]),
                    "condition_id": condition_id,
                    "observed_value": clean(row["u_observed_value"]),
                    "source_system": clean(row["u_source_system"]),
                    "confidence": parse_float(row["u_confidence"]),
                    "captured_at": clean(row["u_captured_at"]),
                    "linked_question_id": clean(row["u_linked_question_id"]),
                    "search_text": search_text(row["u_evidence_type"], condition_id, row["u_observed_value"], row["u_source_system"]),
                },
            )
            self.writer.merge_relationship("DiagnosticSession", "external_id", row["u_session_id"], "HAS_EVIDENCE", "Evidence", "external_id", evidence_id)
            self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], "HAS_EVIDENCE", "Evidence", "external_id", evidence_id)
            self.writer.merge_relationship("Evidence", "external_id", evidence_id, "OBSERVES_CONDITION", "Condition", "condition_id", condition_id)
            self.writer.merge_relationship("Evidence", "external_id", evidence_id, "FROM_SOURCE", "SourceSystem", "external_id", source_id)
            self.writer.merge_relationship("Evidence", "external_id", evidence_id, "ANSWER_TO", "LLMQuestion", "external_id", row["u_linked_question_id"])

    def ingest_signals_and_contexts(self) -> None:
        for row in self.rows["monitoring_signals"]:
            signal_id = clean(row["u_external_id"])
            source_id = self.source_id(row["u_source_system"])
            self.writer.merge_node(
                ("MonitoringSignal",),
                "external_id",
                signal_id,
                {
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "appliance_ci_id": clean(row["u_appliance_ci_id"]),
                    "signal_time": clean(row["u_signal_time"]),
                    "health_state": clean(row["u_health_state"]),
                    "phone_home_status": clean(row["u_phone_home_status"]),
                    "last_telemetry_age": clean(row["u_last_telemetry_age"]),
                    "alert_count_24h": parse_int(row["u_alert_count_24h"]),
                    "capacity_utilization_pct": parse_float(row["u_capacity_utilization_pct"]),
                    "latency_ms": parse_float(row["u_latency_ms"]),
                    "known_issue_match": clean(row["u_known_issue_match"]),
                    "source_system": clean(row["u_source_system"]),
                    "search_text": search_text(row["u_health_state"], row["u_phone_home_status"], row["u_known_issue_match"], row["u_last_telemetry_age"]),
                },
            )
            self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], "HAS_MONITORING_SIGNAL", "MonitoringSignal", "external_id", signal_id)
            self.writer.merge_relationship("MonitoringSignal", "external_id", signal_id, "FOR_INCIDENT", "Incident", "external_id", row["u_incident_external_id"])
            self.writer.merge_relationship("MonitoringSignal", "external_id", signal_id, "FOR_CI", "ConfigurationItem", "external_id", row["u_appliance_ci_id"])
            self.writer.merge_relationship("MonitoringSignal", "external_id", signal_id, "MATCHES_KNOWN_ISSUE", "KnownIssue", "external_id", row["u_known_issue_match"])
            self.writer.merge_relationship("MonitoringSignal", "external_id", signal_id, "FROM_SOURCE", "SourceSystem", "external_id", source_id)

        for row in self.rows["vmware_context"]:
            context_id = clean(row["u_external_id"])
            source_id = self.source_id(row["u_source_system"])
            self.writer.merge_node(
                ("VMwareContext",),
                "external_id",
                context_id,
                {
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "appliance_ci_id": clean(row["u_appliance_ci_id"]),
                    "vcenter": clean(row["u_vcenter"]),
                    "ova_power_state": clean(row["u_ova_power_state"]),
                    "collector_registered": clean(row["u_collector_registered"]),
                    "last_metric_received": clean(row["u_last_metric_received"]),
                    "dns_status": clean(row["u_dns_status"]),
                    "ntp_status": clean(row["u_ntp_status"]),
                    "outbound_https_443": clean(row["u_outbound_https_443"]),
                    "collector_service_state": clean(row["u_collector_service_state"]),
                    "source_system": clean(row["u_source_system"]),
                    "search_text": search_text(row["u_vcenter"], row["u_ova_power_state"], row["u_collector_registered"], row["u_dns_status"], row["u_ntp_status"], row["u_outbound_https_443"], row["u_collector_service_state"]),
                },
            )
            self.link_context("VMwareContext", context_id, row, source_id)
            self.context_observes("VMwareContext", context_id, "COND_REGISTERED", "collector_registered", row["u_collector_registered"])
            self.context_observes("VMwareContext", context_id, "COND_LAST_DATA", "last_metric_received", row["u_last_metric_received"])
            self.context_observes("VMwareContext", context_id, "COND_FIREWALL", "outbound_https_443", row["u_outbound_https_443"])

        for row in self.rows["gateway_context"]:
            context_id = clean(row["u_external_id"])
            source_id = self.source_id(row["u_source_system"])
            self.writer.merge_node(
                ("GatewayContext",),
                "external_id",
                context_id,
                {
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "appliance_ci_id": clean(row["u_appliance_ci_id"]),
                    "gateway_health": clean(row["u_gateway_health"]),
                    "cloud_endpoint_status": clean(row["u_cloud_endpoint_status"]),
                    "cache_utilization_pct": parse_float(row["u_cache_utilization_pct"]),
                    "network_latency_ms": parse_float(row["u_network_latency_ms"]),
                    "auth_proxy_status": clean(row["u_auth_proxy_status"]),
                    "peak_hour_degradation": clean(row["u_peak_hour_degradation"]),
                    "source_system": clean(row["u_source_system"]),
                    "search_text": search_text(row["u_gateway_health"], row["u_cloud_endpoint_status"], row["u_auth_proxy_status"], row["u_peak_hour_degradation"]),
                },
            )
            self.link_context("GatewayContext", context_id, row, source_id)
            self.context_observes("GatewayContext", context_id, "COND_CLOUD", "cloud_endpoint_status", row["u_cloud_endpoint_status"])
            self.context_observes("GatewayContext", context_id, "COND_CLOUD", "auth_proxy_status", row["u_auth_proxy_status"])
            self.context_observes("GatewayContext", context_id, "COND_PEAK", "peak_hour_degradation", row["u_peak_hour_degradation"])

        for row in self.rows["upgrade_context"]:
            context_id = clean(row["u_external_id"])
            source_id = self.source_id(row["u_source_system"])
            self.writer.merge_node(
                ("UpgradeContext",),
                "external_id",
                context_id,
                {
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "appliance_ci_id": clean(row["u_appliance_ci_id"]),
                    "phone_home_enabled": clean(row["u_phone_home_enabled"]),
                    "edge_management_enabled": clean(row["u_edge_management_enabled"]),
                    "upgrade_agent_version": clean(row["u_upgrade_agent_version"]),
                    "precheck_result": clean(row["u_precheck_result"]),
                    "failed_step": clean(row["u_failed_step"]),
                    "required_customer_action": clean(row["u_required_customer_action"]),
                    "source_system": clean(row["u_source_system"]),
                    "search_text": search_text(row["u_phone_home_enabled"], row["u_edge_management_enabled"], row["u_upgrade_agent_version"], row["u_precheck_result"], row["u_failed_step"], row["u_required_customer_action"]),
                },
            )
            self.link_context("UpgradeContext", context_id, row, source_id)
            self.context_observes("UpgradeContext", context_id, "COND_PHONE_HOME", "phone_home_enabled", row["u_phone_home_enabled"])
            self.context_observes("UpgradeContext", context_id, "COND_EDGE", "edge_management_enabled", row["u_edge_management_enabled"])
            self.context_observes("UpgradeContext", context_id, "COND_FAILED_STEP", "failed_step", row["u_failed_step"])

    def ingest_outputs_and_governance(self) -> None:
        for row in self.rows["case_recommendations"]:
            recommendation_id = clean(row["u_external_id"])
            group_id = self.merge_group(row["u_escalation_group"])
            self.writer.merge_node(
                ("Recommendation",),
                "external_id",
                recommendation_id,
                {
                    "session_id": clean(row["u_session_id"]),
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "recommendation_type": clean(row["u_recommendation_type"]),
                    "likely_cause_id": clean(row["u_likely_cause_id"]),
                    "likely_cause": clean(row["u_likely_cause"]),
                    "confidence_score": parse_float(row["u_confidence_score"]),
                    "recommended_action": clean(row["u_recommended_action"]),
                    "kb_runbook_id": clean(row["u_kb_runbook_id"]),
                    "escalation_group": clean(row["u_escalation_group"]),
                    "created_at": clean(row["u_created_at"]),
                    "search_text": search_text(row["u_recommendation_type"], row["u_likely_cause"], row["u_recommended_action"], row["u_escalation_group"]),
                },
            )
            self.writer.merge_relationship("DiagnosticSession", "external_id", row["u_session_id"], "PRODUCED_RECOMMENDATION", "Recommendation", "external_id", recommendation_id)
            self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], "HAS_RECOMMENDATION", "Recommendation", "external_id", recommendation_id)
            self.writer.merge_relationship("Recommendation", "external_id", recommendation_id, "FOR_CAUSE", "RootCause", "cause_id", row["u_likely_cause_id"])
            self.writer.merge_relationship("Recommendation", "external_id", recommendation_id, "USES_RUNBOOK", "Runbook", "external_id", row["u_kb_runbook_id"])
            self.writer.merge_relationship("Recommendation", "external_id", recommendation_id, "ESCALATES_TO", "SupportGroup", "group_id", group_id)

        for row in self.rows["hypothesis_score_log"]:
            score_id = clean(row["u_external_id"])
            self.writer.merge_node(
                ("HypothesisScoreEvent",),
                "external_id",
                score_id,
                {
                    "session_id": clean(row["u_session_id"]),
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "score_event": clean(row["u_score_event"]),
                    "cause_id": clean(row["u_cause_id"]),
                    "cause_name": clean(row["u_cause_name"]),
                    "score": parse_float(row["u_score"]),
                    "evidence_summary": clean(row["u_evidence_summary"]),
                    "scored_at": clean(row["u_scored_at"]),
                    "search_text": search_text(row["u_score_event"], row["u_cause_name"], row["u_evidence_summary"]),
                },
            )
            self.writer.merge_relationship("DiagnosticSession", "external_id", row["u_session_id"], "HAS_SCORE_EVENT", "HypothesisScoreEvent", "external_id", score_id)
            self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], "HAS_SCORE_EVENT", "HypothesisScoreEvent", "external_id", score_id)
            self.writer.merge_relationship("HypothesisScoreEvent", "external_id", score_id, "SCORES_CAUSE", "RootCause", "cause_id", row["u_cause_id"])

        for row in self.rows["escalation_packets"]:
            packet_id = clean(row["u_external_id"])
            group_id = self.merge_group(row["u_escalation_group"])
            next_owner_id = self.merge_group(row["u_next_owner"])
            self.writer.merge_node(
                ("EscalationPacket",),
                "external_id",
                packet_id,
                {
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "session_id": clean(row["u_session_id"]),
                    "escalation_group": clean(row["u_escalation_group"]),
                    "escalation_reason": clean(row["u_escalation_reason"]),
                    "evidence_included": clean(row["u_evidence_included"]),
                    "next_owner": clean(row["u_next_owner"]),
                    "created_at": clean(row["u_created_at"]),
                    "search_text": search_text(row["u_escalation_group"], row["u_escalation_reason"], row["u_evidence_included"], row["u_next_owner"]),
                },
            )
            self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], "HAS_ESCALATION_PACKET", "EscalationPacket", "external_id", packet_id)
            self.writer.merge_relationship("DiagnosticSession", "external_id", row["u_session_id"], "HAS_ESCALATION_PACKET", "EscalationPacket", "external_id", packet_id)
            self.writer.merge_relationship("EscalationPacket", "external_id", packet_id, "ESCALATES_TO", "SupportGroup", "group_id", group_id)
            self.writer.merge_relationship("EscalationPacket", "external_id", packet_id, "NEXT_OWNER", "SupportGroup", "group_id", next_owner_id)

        for row in self.rows["historical_case_matches"]:
            match_id = clean(row["u_external_id"])
            pattern_id = self.merge_pattern(row["u_matching_pattern"], row["u_resolution_that_worked"])
            self.writer.merge_node(
                ("HistoricalIncident",),
                "number",
                clean(row["u_similar_case_number"]),
                {
                    "name": clean(row["u_similar_case_number"]),
                    "search_text": search_text(row["u_similar_case_number"], row["u_matching_pattern"], row["u_resolution_that_worked"]),
                },
            )
            self.writer.merge_node(
                ("HistoricalCaseMatch",),
                "external_id",
                match_id,
                {
                    "current_incident_external_id": clean(row["u_current_incident_external_id"]),
                    "similar_case_number": clean(row["u_similar_case_number"]),
                    "similarity_score": parse_float(row["u_similarity_score"]),
                    "matching_pattern": clean(row["u_matching_pattern"]),
                    "resolution_that_worked": clean(row["u_resolution_that_worked"]),
                    "source_system": clean(row["u_source_system"]),
                    "search_text": search_text(row["u_similar_case_number"], row["u_matching_pattern"], row["u_resolution_that_worked"]),
                },
            )
            self.writer.merge_relationship("Incident", "external_id", row["u_current_incident_external_id"], "HAS_HISTORICAL_MATCH", "HistoricalCaseMatch", "external_id", match_id)
            self.writer.merge_relationship("HistoricalCaseMatch", "external_id", match_id, "MATCHES_CASE", "HistoricalIncident", "number", row["u_similar_case_number"])
            self.writer.merge_relationship("HistoricalCaseMatch", "external_id", match_id, "MATCHES_PATTERN", "CasePattern", "pattern_id", pattern_id)
            self.writer.merge_relationship(
                "Incident",
                "external_id",
                row["u_current_incident_external_id"],
                "SIMILAR_TO",
                "HistoricalIncident",
                "number",
                row["u_similar_case_number"],
                {
                    "similarity_score": parse_float(row["u_similarity_score"]),
                    "matching_pattern": clean(row["u_matching_pattern"]),
                    "resolution_that_worked": clean(row["u_resolution_that_worked"]),
                },
            )

        for row in self.rows["audit_events"]:
            event_id = clean(row["u_external_id"])
            actor_id = self.merge_actor(row["u_actor"])
            self.writer.merge_node(
                ("AuditEvent",),
                "external_id",
                event_id,
                {
                    "incident_external_id": clean(row["u_incident_external_id"]),
                    "session_id": clean(row["u_session_id"]),
                    "event_type": clean(row["u_event_type"]),
                    "event_description": clean(row["u_event_description"]),
                    "actor": clean(row["u_actor"]),
                    "event_time": clean(row["u_event_time"]),
                    "audit_payload": clean(row["u_audit_payload_json"]),
                    "search_text": search_text(row["u_event_type"], row["u_event_description"], row["u_actor"], row["u_audit_payload_json"]),
                },
            )
            self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], "HAS_AUDIT_EVENT", "AuditEvent", "external_id", event_id)
            self.writer.merge_relationship("DiagnosticSession", "external_id", row["u_session_id"], "HAS_AUDIT_EVENT", "AuditEvent", "external_id", event_id)
            self.writer.merge_relationship("AuditEvent", "external_id", event_id, "PERFORMED_BY", "Actor", "actor_id", actor_id)

    def link_context(self, label: str, context_id: str, row: Dict[str, str], source_id: str) -> None:
        self.writer.merge_relationship("Incident", "external_id", row["u_incident_external_id"], CONTEXT_REL_TYPES[label], label, "external_id", context_id)
        self.writer.merge_relationship(label, "external_id", context_id, "FOR_INCIDENT", "Incident", "external_id", row["u_incident_external_id"])
        self.writer.merge_relationship(label, "external_id", context_id, "FOR_CI", "ConfigurationItem", "external_id", row["u_appliance_ci_id"])
        self.writer.merge_relationship(label, "external_id", context_id, "FROM_SOURCE", "SourceSystem", "external_id", source_id)

    def context_observes(self, label: str, context_id: str, condition_id: str, field_name: str, value: str) -> None:
        if not clean(value):
            return
        observation_id = f"CTXOBS_{norm_id(label)}_{norm_id(context_id)}_{norm_id(condition_id)}_{norm_id(field_name)}"
        self.writer.merge_node(
            ("ContextObservation",),
            "external_id",
            observation_id,
            {
                "context_type": label,
                "context_id": context_id,
                "condition_id": condition_id,
                "field": field_name,
                "observed_value": clean(value),
                "search_text": search_text(label, context_id, condition_id, field_name, value),
            },
        )
        self.writer.merge_relationship(
            label,
            "external_id",
            context_id,
            "HAS_CONTEXT_OBSERVATION",
            "ContextObservation",
            "external_id",
            observation_id,
        )
        self.writer.merge_relationship(
            "ContextObservation",
            "external_id",
            observation_id,
            "OBSERVES_CONDITION",
            "Condition",
            "condition_id",
            condition_id,
        )
        self.writer.merge_relationship(
            label,
            "external_id",
            context_id,
            "OBSERVES_CONDITION",
            "Condition",
            "condition_id",
            condition_id,
            {"field": field_name, "observed_value": clean(value)},
        )

    def issue_specs(self) -> List[Dict[str, Any]]:
        grouped_aliases: Dict[str, List[str]] = defaultdict(list)
        descriptions: Dict[str, List[str]] = defaultdict(list)
        for row in self.rows["incidents"]:
            issue_id = self.issue_for_domain(row["category"])
            grouped_aliases[issue_id].extend([row["subcategory"], row["short_description"]])
            descriptions[issue_id].append(row["description"])

        specs: List[Dict[str, Any]] = []
        for domain, seed in ISSUE_DOMAIN_MAP.items():
            issue_id = seed["issue_id"]
            aliases = dedupe(grouped_aliases.get(issue_id, []) + [seed["name"], domain])
            specs.append(
                {
                    "issue_id": issue_id,
                    "name": seed["name"],
                    "category": domain,
                    "subcategory": aliases[0] if aliases else seed["name"],
                    "description": Counter(descriptions.get(issue_id, [])).most_common(1)[0][0] if descriptions.get(issue_id) else seed["name"],
                    "aliases": aliases,
                }
            )
        return specs

    def source_id(self, value: Any) -> str:
        text = clean(value)
        if not text:
            return ""
        lowered = text.lower()
        if lowered in self.source_name_to_id:
            return self.source_name_to_id[lowered]
        for alias, source_id in SOURCE_ALIASES.items():
            if alias in lowered:
                return source_id
        source_id = text if text.upper().startswith("SRC_") else "SRC_" + norm_id(text).upper()
        self.source_name_to_id[lowered] = source_id
        self.writer.merge_node(
            ("SourceSystem",),
            "external_id",
            source_id,
            {
                "name": text,
                "system_type": "Derived source",
                "data_contribution": "Referenced by CSV records",
                "search_text": text,
            },
        )
        return source_id

    def issue_for_domain(self, domain_value: Any) -> str:
        text = clean(domain_value).lower()
        if text in self.issue_id_by_domain:
            return self.issue_id_by_domain[text]
        for domain, issue_id in self.issue_id_by_domain.items():
            if domain in text or text in domain:
                return issue_id
        if "vm" in text or "analytics" in text:
            return "ISSUE_VMWARE_VM_ANALYTICS"
        if "gateway" in text or "cloud" in text:
            return "ISSUE_GATEWAY_HYBRID_CLOUD"
        if "security" in text or "resilience" in text or "safemode" in text:
            return "ISSUE_SECURITY_RESILIENCE"
        if "capacity" in text:
            return "ISSUE_CAPACITY"
        if "upgrade" in text:
            return "ISSUE_UPGRADE"
        return "ISSUE_PERFORMANCE"

    def cause_id_for_name(self, cause_name: Any) -> str:
        name = clean(cause_name)
        if not name:
            return ""
        existing = self.cause_name_to_id.get(name.lower())
        if existing:
            return existing
        cause_id = "CAUSE_" + norm_id(name).upper()
        self.cause_name_to_id[name.lower()] = cause_id
        self.writer.merge_node(
            ("RootCause",),
            "cause_id",
            cause_id,
            {"name": name, "description": name, "search_text": search_text(cause_id, name)},
        )
        return cause_id

    def merge_domain(self, value: Any) -> str:
        name = clean(value) or "General"
        domain_id = "DOMAIN_" + norm_id(name).upper()
        self.writer.merge_node(
            ("SupportDomain",),
            "domain_id",
            domain_id,
            {"name": name, "search_text": name},
        )
        return domain_id

    def merge_group(self, value: Any) -> str:
        name = clean(value) or "Unassigned"
        group_id = "GROUP_" + norm_id(name).upper()
        self.writer.merge_node(
            ("SupportGroup",),
            "group_id",
            group_id,
            {"name": name, "search_text": name},
        )
        return group_id

    def merge_site(self, site: Any, region: Any) -> str:
        name = clean(site) or "Unknown Site"
        site_id = "SITE_" + norm_id(name)
        self.writer.merge_node(
            ("Site",),
            "site_id",
            site_id,
            {"name": name, "region": clean(region), "search_text": search_text(name, region)},
        )
        return site_id

    def merge_actor(self, value: Any) -> str:
        name = clean(value) or "Unknown Actor"
        actor_id = "ACTOR_" + norm_id(name)
        self.writer.merge_node(
            ("Actor",),
            "actor_id",
            actor_id,
            {"name": name, "search_text": name},
        )
        return actor_id

    def merge_pattern(self, pattern: Any, resolution: Any) -> str:
        text = clean(pattern) or "Unknown pattern"
        pattern_id = "PATTERN_" + norm_id(text)
        self.writer.merge_node(
            ("CasePattern",),
            "pattern_id",
            pattern_id,
            {
                "name": text,
                "matching_pattern": text,
                "resolution_that_worked": clean(resolution),
                "search_text": search_text(text, resolution),
            },
        )
        return pattern_id


def resolve_config(args: argparse.Namespace) -> Tuple[str, str, str, str]:
    uri = args.uri or os.getenv("NEO4J_URI") or ""
    username = args.username or os.getenv("NEO4J_USERNAME") or ""
    password = args.password or os.getenv("NEO4J_PASSWORD") or ""
    database = args.database or os.getenv("NEO4J_DATABASE") or "neo4j"
    missing = [
        name
        for name, value in (
            ("NEO4J_URI", uri),
            ("NEO4J_USERNAME", username),
            ("NEO4J_PASSWORD", password),
            ("NEO4J_DATABASE", database),
        )
        if not value
    ]
    if missing and not args.dry_run:
        raise SystemExit(f"Missing required Neo4j settings: {', '.join(missing)}")
    return uri, username, password, database


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest appliance support CSVs into a GraphRAG-friendly Neo4j ontology.")
    parser.add_argument("--csv-dir", default="csv_ingest", help="Path to the csv_ingest folder.")
    parser.add_argument("--uri", default="", help="Neo4j URI. Defaults to NEO4J_URI.")
    parser.add_argument("--username", default="", help="Neo4j username. Defaults to NEO4J_USERNAME.")
    parser.add_argument("--password", default="", help="Neo4j password. Defaults to NEO4J_PASSWORD.")
    parser.add_argument("--database", default="", help="Neo4j database. Defaults to NEO4J_DATABASE.")
    parser.add_argument("--dry-run", action="store_true", help="Load and map CSVs without connecting to Neo4j.")
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir).resolve()
    uri, username, password, database = resolve_config(args)
    with GraphWriter(uri, username, password, database, dry_run=args.dry_run) as writer:
        summary = ApplianceSupportGraphIngestor(csv_dir, writer).ingest()
    print(json.dumps({"dry_run": args.dry_run, "csv_dir": str(csv_dir), **summary}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
