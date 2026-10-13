# Minimal Business GraphRAG Implementation Plan

## Goal

Build a clean, deterministic GraphRAG path for ServiceNow business questions. The graph should contain only the data needed for grounded answers about catalog items, catalog forms, users, service requests, request items, and change requests. Full platform schema metadata is intentionally outside the answering path.

## Architecture

The target flow is:

1. ServiceNow is read through MCP business tools.
2. MCP returns normalized business payloads with raw IDs and display values.
3. Backend graph sync writes a minimal Neo4j business graph.
4. GraphRAG resolves entities and time windows deterministically.
5. Backend-owned Cypher templates answer exact count, list, grouped, set, and cohort queries.
6. LLM fallback is reserved for exploratory catalog/schema questions, not exact operational answers.

## Minimal Graph Model

Required node labels:

- `User`
- `Ticket`, with `ServiceRequest` and `ChangeRequest` subtype labels
- `RequestItem`
- `CatalogItem`
- `CatalogVariable`
- `CatalogChoice`
- `FilterSchema` only for catalog form fields

Required relationships:

- `(:User)-[:REQUESTED]->(:Ticket)`
- `(:User)-[:ASSIGNED_TO]->(:Ticket)`
- `(:Ticket)-[:HAS_ITEM]->(:RequestItem)`
- `(:RequestItem)-[:FULFILLS_CATALOG_ITEM]->(:CatalogItem)`
- `(:CatalogItem)-[:USES_SCHEMA]->(:FilterSchema)`
- `(:FilterSchema)-[:HAS_FIELD]->(:CatalogVariable)`
- `(:CatalogVariable)-[:HAS_CHOICE]->(:CatalogChoice)`

Excluded from the minimal GraphRAG target:

- `Table`
- `Column`
- `SampleRecord`
- broad ServiceNow system/platform schema metadata

## Ingestion Requirements

MCP must expose normalized business reads:

- catalog item list
- full catalog item form
- service request list for graph sync
- service request details with request item context
- request item details
- change request list for graph sync
- change request details

Payloads must include:

- raw sys_id values
- human display names
- category sys_id and category display name
- state raw value and state display name
- canonical timestamps

Backend graph sync must bootstrap from MCP payloads first. Direct ServiceNow sync can remain as a compatibility fallback, but the minimal GraphRAG path should prefer MCP.

## Query Requirements

Exact operational questions must be deterministic and LLM-independent.

Supported general query families:

- count by requester
- distinct products/items by requester
- request/order listing by requester
- requester + item/category/state filters
- grouped requester summaries
- threshold aggregates
- item cohort expansion
- requester/item set comparison
- all of the above with time-window filters

Business noun semantics:

- `requests`, `orders`, and `tickets` mean ticket/request records.
- `products`, `items`, `devices`, and `catalog items` mean distinct catalog items unless request numbers are explicitly requested.
- `bought`, `ordered`, `purchased`, `requested`, and `got` all refer to observed ServiceNow requests.

Time phrases must be resolved in backend code:

- `today`
- `yesterday`
- `this week`
- `last week`
- `this month`
- `last month`
- explicit date ranges when present

## Rollout Steps

1. Point `.env` at the selected Neo4j Aura instance.
2. Enable minimal GraphRAG mode.
3. Start MCP and backend.
4. Run the minimal graph bootstrap endpoint.
5. Validate graph counts and representative exact queries.
6. Keep webhook sync enabled for updates after bootstrap.

## Validation Queries

Use these after bootstrap:

- `how many requests did lucius bagnoli make`
- `how many products did lucius order`
- `what orders did lucius make last week`
- `which users bought iphone 13 pro`
- `the users who bought iphone 13 pro what else did they buy`
- `for each requester, show total requests, hardware requests, and mobile requests`

## Security Notes

Credentials must stay in `.env` only. Documentation must not include ServiceNow, OpenAI, or Neo4j secrets. Rotate any credential that was pasted into chat or committed accidentally.
