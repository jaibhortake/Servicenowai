from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from neo4j import GraphDatabase


QUERIES = {
    "label_counts": """
MATCH (n)
UNWIND labels(n) AS label
RETURN label, count(*) AS count
ORDER BY count DESC, label ASC
""".strip(),
    "relationship_counts": """
MATCH ()-[r]->()
RETURN type(r) AS relationship_type, count(*) AS count
ORDER BY count DESC, relationship_type ASC
""".strip(),
    "issue_to_resolution_sample": """
MATCH (issue:Issue)-[:INDICATES]->(cause:RootCause)
OPTIONAL MATCH (cause)-[:REQUIRES_CHECK]->(condition:Condition)
OPTIONAL MATCH (cause)-[:RESOLVED_BY]->(resolution:Resolution)-[:USES_RUNBOOK]->(runbook:Runbook)
RETURN issue.issue_id AS issue_id,
       issue.name AS issue,
       cause.name AS cause,
       collect(DISTINCT condition.condition_id)[0..5] AS checks,
       collect(DISTINCT resolution.name)[0..3] AS resolutions,
       collect(DISTINCT runbook.title)[0..3] AS runbooks
ORDER BY issue_id, cause
LIMIT 8
""".strip(),
    "incident_diagnosis_sample": """
MATCH (incident:Incident)-[:HAS_DIAGNOSTIC_SESSION]->(session:DiagnosticSession)
OPTIONAL MATCH (session)-[:ASKED]->(question:LLMQuestion)-[:PRODUCED_EVIDENCE]->(evidence:Evidence)
OPTIONAL MATCH (session)-[:PRODUCED_RECOMMENDATION]->(recommendation:Recommendation)-[:FOR_CAUSE]->(cause:RootCause)
RETURN incident.number AS incident,
       incident.short_description AS symptom,
       session.status AS session_status,
       count(DISTINCT question) AS question_count,
       count(DISTINCT evidence) AS evidence_count,
       collect(DISTINCT cause.name)[0..3] AS likely_causes,
       collect(DISTINCT recommendation.recommended_action)[0..2] AS actions
ORDER BY incident
LIMIT 8
""".strip(),
    "customer_asset_ci_sample": """
MATCH (customer:Customer)-[:OWNS]->(asset:Asset)<-[:REALIZES_ASSET]-(ci:ConfigurationItem)-[:RUNS_MODEL]->(model:ProductModel)
RETURN customer.name AS customer,
       asset.asset_tag AS asset,
       ci.name AS ci,
       model.name AS model,
       ci.operational_status AS status
ORDER BY customer, asset
LIMIT 8
""".strip(),
}


def config() -> tuple[str, str, str, str]:
    values = (
        os.getenv("NEO4J_URI", ""),
        os.getenv("NEO4J_USERNAME", ""),
        os.getenv("NEO4J_PASSWORD", ""),
        os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    missing = [name for name, value in zip(("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"), values[:3]) if not value]
    if missing:
        raise SystemExit(f"Missing required Neo4j settings: {', '.join(missing)}")
    return values


def run_query(driver, database: str, query: str) -> List[Dict[str, Any]]:
    with driver.session(database=database) as session:
        return [dict(record) for record in session.run(query)]


def main() -> None:
    uri, username, password, database = config()
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        driver.verify_connectivity()
        payload = {
            name: run_query(driver, database, query)
            for name, query in QUERIES.items()
        }
        print(json.dumps(payload, indent=2, ensure_ascii=True, default=str))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
