# Appliance Support Graph Ingest

This folder contains an idempotent CSV-to-Neo4j ingester for the `csv_ingest`
dataset. The model is optimized for GraphRAG traversal rather than one-node-per
CSV-row mirroring.

## Ontology Spine

- `(:Issue:IssueTaxonomy)-[:INDICATES]->(:RootCause)`
- `(:RootCause)-[:REQUIRES_CHECK]->(:Condition:ConditionDefinition)`
- `(:RootCause)-[:GUIDED_BY]->(:Runbook)`
- `(:RootCause)-[:RESOLVED_BY]->(:Resolution)`
- `(:Issue:IssueTaxonomy)-[:HAS_KNOWN_ISSUE_SIGNAL]->(:KnownIssue)`

## Operational Layer

- `Customer`, `ProductModel:Product`, `Asset`, `ConfigurationItem`, `Site`,
  `SupportDomain`, `SupportGroup`, and `SourceSystem`
- `Incident:CaseRecord`, `Problem`, and `DiagnosticSession`
- `LLMQuestion`, `Evidence`, telemetry/context nodes, recommendations,
  historical matches, audit events, escalation packets, and score events

## Run

Set these environment variables before running:

```powershell
$env:NEO4J_URI='neo4j+ssc://...'
$env:NEO4J_USERNAME='...'
$env:NEO4J_PASSWORD='...'
$env:NEO4J_DATABASE='...'
```

Dry run:

```powershell
backend\venv\Scripts\python.exe backend\csv_graph_ingest\ingest_appliance_support_graph.py --csv-dir csv_ingest --dry-run
```

Ingest:

```powershell
backend\venv\Scripts\python.exe backend\csv_graph_ingest\ingest_appliance_support_graph.py --csv-dir csv_ingest
```

The ingester does not delete existing graph data. It uses stable IDs and `MERGE`
so it can be safely rerun after CSV changes.
