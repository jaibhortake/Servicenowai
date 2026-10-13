# ServiceNow AI Portal Architecture Diagrams

This document describes how the current application works end to end:
- what is fetched from ServiceNow via MCP
- what is fetched directly from ServiceNow
- how data is ingested into Neo4j
- how the frontend, backend, GraphRAG, and ticketing flows connect
- which exact modules are hit at runtime

---

## 0. Correct MCP-to-ServiceNow-to-Neo4j Diagram

Use this one when you need the accurate story of how catalog data moves.

```mermaid
sequenceDiagram
    participant T as Sync Trigger
    participant B as Backend Sync Orchestrator
    participant M as MCP Layer
    participant S as ServiceNow Catalog
    participant E as Normalize and Enrich
    participant G as Graph Builder
    participant N as Neo4j Catalog Graph

    T->>B: Start manual sync or scheduled polling
    B->>M: Request catalog list / changed items / item details / variables
    M->>S: Call ServiceNow APIs
    S-->>M: Return catalog item data, variable data, choice data, change feed
    M-->>B: Return normalized MCP payloads
    B->>E: Prepare data for graph ingestion
    E->>S: Fetch extra enrichment only when needed\n(UI policies, client scripts, native dependencies)
    S-->>E: Return enrichment metadata
    E->>G: Build graph-ready entities
    G->>N: Write nodes and relationships
```

### What this means
1. The backend does not call ServiceNow directly for the main catalog fetch when MCP is being used.
2. The backend calls MCP.
3. MCP calls ServiceNow.
4. ServiceNow returns data to MCP.
5. MCP returns the payload back to the backend.
6. The backend then enriches and converts that payload into graph structures.
7. Neo4j stores the final nodes and relationships.

### Important clarification
- Main catalog retrieval path: `Backend -> MCP -> ServiceNow -> MCP -> Backend`
- Extra enrichment path: `Backend -> ServiceNow -> Backend`
- Final graph write path: `Backend -> Neo4j`

---

## 0A. Presentation-Friendly Flow

```mermaid
flowchart TD
    A[Sync Trigger]
    B[Backend Sync Orchestrator]
    C[MCP fetches catalog data from ServiceNow]
    D[MCP returns catalog payloads to backend]
    E[Backend normalizes and enriches data]
    F[Backend creates graph nodes and relationships]
    G[(Neo4j Catalog Graph)]

    A --> B
    B --> C
    C --> D
    D --> E
    E --> F
    F --> G
```

### What is fetched from ServiceNow through MCP
- Catalog items
- Catalog item details
- Variables / form fields
- Choice lists
- Catalog change feed

### What may still be fetched directly from ServiceNow during catalog ingestion
- UI policies
- Native field dependency metadata
- Client scripts

### What is written into Neo4j
- `CatalogItem`
- `FilterSchema`
- `CatalogVariable`
- `CatalogChoice`
- Relationships between items, schemas, variables, and choices

---

## 1. Full End-to-End Architecture

```mermaid
flowchart LR
    U[User]

    subgraph FE[Frontend React App]
        RP[Request Portal]
        QP[Query Panel / GraphRAG Portal]
        MT[My Tickets]
        AP[Admin Panel]
    end

    subgraph BE[FastAPI Backend]
        IR[Interpret / Match]
        CF[Catalog Form API]
        TK[Ticket API]
        CM[Comments / Conversations]
        GR[GraphRAG API]
        CP[Catalog Polling Service]
        IDX[Catalog Lexical Index]
    end

    subgraph INFRA[Infrastructure]
        MCP[MCP REST Server]
        SN[ServiceNow]
        OAI[OpenAI]
        N4J[(Neo4j)]
    end

    U --> FE

    RP --> IR
    RP --> CF
    RP --> TK
    QP --> GR
    MT --> TK
    MT --> CM
    AP --> CM

    IR --> OAI
    IR --> MCP
    IR --> IDX

    CF --> N4J
    CF --> MCP
    CF --> SN

    TK --> SN
    TK --> N4J

    CM --> SN

    GR --> OAI
    GR --> IDX
    GR --> N4J

    CP --> MCP
    CP --> N4J

    MCP --> SN
```

### Summary
- The frontend talks only to the FastAPI backend.
- MCP is mainly used for catalog retrieval and catalog change detection.
- Ticket creation, ticket fetch, comments, and user lookup still go directly to ServiceNow.
- Neo4j stores catalog, schema, and ticket graph data.
- GraphRAG queries Neo4j and uses OpenAI for planning, compilation, and answer synthesis.

---

## 1A. Module-Level Runtime Hit Map

This diagram shows the exact file/module path that gets hit for the main user journeys.

```mermaid
flowchart TD
    subgraph Frontend
        F1[frontend/src/pages/RequestPortal.jsx]
        F2[frontend/src/pages/GraphRAGPortal.jsx]
        F3[frontend/src/api/client.js]
    end

    subgraph FastAPI Routers
        R1[backend/app/routers/interpret.py]
        R2[backend/app/routers/catalog.py]
        R3[backend/app/routers/tickets.py]
        R4[backend/app/routers/graphrag.py]
        R5[backend/app/routers/comments.py]
        R6[backend/app/routers/conversations.py]
        R7[backend/app/routers/users.py]
    end

    subgraph Backend Services
        S1[backend/app/services/ai_service.py]
        S2[backend/app/services/mcp_client_service.py]
        S3[backend/app/services/catalog_polling_service.py]
        S4[backend/app/services/catalog_startup.py]
        S5[backend/app/services/ticket_service.py]
        S6[backend/app/services/graphrag_service.py]
        S7[backend/app/services/query_expansion.py]
        S8[backend/app/services/semantic_query_mapper.py]
        S9[backend/app/services/servicenow.py]
        S10[backend/app/services/neo4j_catalog_graph.py]
        S11[backend/app/services/neo4j_schema_graph.py]
        S12[backend/app/services/neo4j_ticket_graph.py]
        S13[backend/app/services/neo4j_query_runtime.py]
    end

    subgraph External Systems
        E1[OpenAI API]
        E2[MCP REST Server]
        E3[ServiceNow]
        E4[(Neo4j)]
    end

    F1 --> F3
    F2 --> F3

    F3 --> R1
    F3 --> R2
    F3 --> R3
    F3 --> R4
    F3 --> R5
    F3 --> R6
    F3 --> R7

    R1 --> S1
    R1 --> S2
    R1 --> S9

    R2 --> S2
    R2 --> S9
    R2 --> S10

    R3 --> S5
    R3 --> S12

    R4 --> S6

    R5 --> S9
    R6 --> S1
    R6 --> S9
    R7 --> S9
    R7 --> S2

    S5 --> S9
    S5 --> S12

    S6 --> S1
    S6 --> S7
    S6 --> S8
    S6 --> S10
    S6 --> S11
    S6 --> S12
    S6 --> S13

    S3 --> S2
    S3 --> S10
    S4 --> S2
    S4 --> S9

    S1 --> E1
    S2 --> E2
    S9 --> E3
    S10 --> E4
    S11 --> E4
    S12 --> E4
    S13 --> E4
    E2 --> E3
```

### How to read it
- `GraphRAGPortal.jsx -> client.js -> routers/graphrag.py -> services/graphrag_service.py` is the query-panel path.
- `RequestPortal.jsx -> client.js -> routers/catalog.py` is the catalog/form path.
- `RequestPortal.jsx -> client.js -> routers/tickets.py -> ticket_service.py -> servicenow.py` is the ticket creation path.

---

## 2. MCP to ServiceNow Catalog Fetch Path

These are the main MCP tools currently used by the backend:
- `list_catalog_items`
- `get_catalog_item`
- `list_catalog_item_variables`
- `list_catalog_changes`

```mermaid
flowchart TD
    API[FastAPI Backend<br/>routers/catalog.py<br/>routers/interpret.py<br/>services/catalog_polling_service.py<br/>services/catalog_startup.py]
    MCP[backend/app/services/mcp_client_service.py]
    T1[list_catalog_items]
    T2[get_catalog_item]
    T3[list_catalog_item_variables]
    T4[list_catalog_changes]
    SN[ServiceNow]

    API --> MCP
    MCP --> T1
    MCP --> T2
    MCP --> T3
    MCP --> T4

    T1 --> SN
    T2 --> SN
    T3 --> SN
    T4 --> SN
```

### What comes through MCP
- Catalog item lists
- Catalog item metadata
- Catalog variables and choices
- Catalog change detection for polling

### Exact modules that call MCP
- [backend/app/routers/catalog.py](</c:/Projects/SubhajitServiceNow/backend/app/routers/catalog.py>)
- [backend/app/routers/interpret.py](</c:/Projects/SubhajitServiceNow/backend/app/routers/interpret.py>)
- [backend/app/services/catalog_polling_service.py](</c:/Projects/SubhajitServiceNow/backend/app/services/catalog_polling_service.py>)
- [backend/app/services/catalog_startup.py](</c:/Projects/SubhajitServiceNow/backend/app/services/catalog_startup.py>)
- [backend/app/services/ai_service.py](</c:/Projects/SubhajitServiceNow/backend/app/services/ai_service.py>) indirectly through interpret/search flows

### What does not currently go through MCP
- Ticket creation
- Ticket fetch
- Comments / work notes
- User lookup
- Some catalog enrichment calls like UI policies and client scripts

---

## 3. Catalog Ingestion into Neo4j

```mermaid
flowchart TD
    CPS[backend/app/services/catalog_polling_service.py]
    M1[backend/app/services/mcp_client_service.py<br/>list_catalog_items]
    M2[backend/app/services/mcp_client_service.py<br/>list_catalog_changes]
    M3[backend/app/services/mcp_client_service.py<br/>get_catalog_form]
    SNX[backend/app/services/servicenow.py<br/>UI Policies / Native Depends-On / Client Scripts]
    FORM[backend/app/models/catalog.py<br/>CatalogFormResponse]
    NCG[backend/app/services/neo4j_catalog_graph.py]
    N4J[(Neo4j Catalog Graph)]

    CPS --> M1
    CPS --> M2
    M1 --> M3
    M2 --> M3
    M3 --> FORM
    FORM --> SNX
    SNX --> NCG
    NCG --> N4J
```

### Neo4j catalog graph content
- `CatalogItem`
- `FilterSchema`
- `CatalogVariable`
- `CatalogChoice`

### Neo4j catalog relationships
- `(:CatalogItem)-[:USES_SCHEMA]->(:FilterSchema)`
- `(:FilterSchema)-[:HAS_FIELD]->(:CatalogVariable)`
- `(:CatalogVariable)-[:HAS_CHOICE]->(:CatalogChoice)`

### Catalog sync entry points
- `POST /api/catalog/{sys_id}/graph/sync`
- `POST /api/catalog/graph/sync-all`
- background polling via `catalog_polling_service`

### Exact runtime hit
1. `routers/catalog.py` or `catalog_polling_service.py`
2. `mcp_client_service.get_catalog_form()`
3. `servicenow.py` enrichment helpers
4. `neo4j_catalog_graph_service.sync_catalog_form()`
5. Neo4j write

---

## 4. Ticket Ingestion into Neo4j

Tickets do not currently use MCP. They go directly through the ServiceNow service layer.

```mermaid
flowchart TD
    FE[frontend/src/pages/RequestPortal.jsx]
    API[backend/app/routers/tickets.py]
    TS[backend/app/services/ticket_service.py]
    SN[backend/app/services/servicenow.py]
    NTG[backend/app/services/neo4j_ticket_graph.py]
    N4J[(Neo4j Ticket Graph)]

    FE --> API
    API --> TS
    TS --> SN
    TS --> NTG
    NTG --> N4J
```

### Ticket graph nodes
- `Ticket`
- `ServiceRequest`
- `ChangeRequest`
- `RequestItem`
- `User`

### Ticket graph relationships
- `(:User)-[:REQUESTED]->(:Ticket)`
- `(:User)-[:ASSIGNED_TO]->(:Ticket)`
- `(:Ticket)-[:HAS_ITEM]->(:RequestItem)`
- `(:RequestItem)-[:FULFILLS_CATALOG_ITEM]->(:CatalogItem)`

### Ticket sync behavior
- Automatic sync after new SR / CH creation
- Manual sync via:
  - `POST /api/tickets/graph/sync-all`
  - `POST /api/tickets/{ticket_type}/{sys_id}/graph/sync`
- Opportunistic sync on ticket fetch

### Exact runtime hit for SR creation
1. `frontend/src/pages/RequestPortal.jsx`
2. `frontend/src/api/client.js -> createSR()`
3. `backend/app/routers/tickets.py -> create_sr()`
4. `backend/app/services/ticket_service.py -> create_sr()`
5. `backend/app/services/servicenow.py -> order_catalog_item()`
6. `backend/app/services/servicenow.py -> get_service_request()`
7. `backend/app/services/servicenow.py -> get_ritm_details_by_request_sysid()` or request-item fetch helper
8. `backend/app/services/servicenow.py -> patch_service_request() / patch_request_item()`
9. `backend/app/services/neo4j_ticket_graph.py -> sync_service_request()`
10. Neo4j write

---

## 5. Catalog Form Runtime Flow

This is the live request-form path when the frontend asks for a catalog form.

```mermaid
sequenceDiagram
    participant FE as frontend/src/pages/RequestPortal.jsx
    participant API as backend/app/routers/catalog.py GET /api/catalog/{sys_id}/form
    participant N4J as backend/app/services/neo4j_catalog_graph.py
    participant MCP as backend/app/services/mcp_client_service.py
    participant SN as backend/app/services/servicenow.py

    FE->>API: GET catalog form
    API->>N4J: try cache first
    alt cache hit and refresh=false
        N4J-->>API: cached CatalogFormResponse
        API-->>FE: return cached form
    else cache miss or forced refresh
        API->>MCP: get_catalog_form
        MCP-->>API: item + variables
        API->>N4J: sync base form
        API->>SN: UI policies / client scripts / native dependencies
        API->>N4J: sync enriched form
        API-->>FE: enriched form
    end
```

---

## 6. Query Panel / GraphRAG Flow

```mermaid
flowchart TD
    FE[frontend/src/pages/GraphRAGPortal.jsx]
    API[backend/app/routers/graphrag.py<br/>POST /api/graphrag/query]
    GRS[backend/app/services/graphrag_service.py]
    PLAN[LLM retrieval planning]
    SEM[Semantic expansion<br/>backend/app/services/query_expansion.py<br/>backend/app/services/semantic_query_mapper.py]
    COMP[LLM graph query compiler]
    SAFE[backend/app/services/neo4j_query_runtime.py<br/>read-only validation + execution]
    FALL[Fallback domain retrieval]
    NCG[backend/app/services/neo4j_catalog_graph.py]
    NSG[backend/app/services/neo4j_schema_graph.py]
    NTG[backend/app/services/neo4j_ticket_graph.py]
    OAI[OpenAI]
    RESP[Grounded answer]

    FE --> API
    API --> GRS
    GRS --> PLAN
    GRS --> SEM
    GRS --> COMP
    PLAN --> OAI
    COMP --> OAI
    COMP --> SAFE
    SAFE --> NCG
    SAFE --> NSG
    SAFE --> NTG
    GRS --> FALL
    FALL --> NCG
    FALL --> NSG
    FALL --> NTG
    GRS --> OAI
    GRS --> RESP
```

### Query panel request payload
The frontend sends:
- `query`
- `user_sys_id`
- `user_email`
- `session_id`

### Current GraphRAG execution order
1. Build a structured retrieval plan
2. Expand terms semantically
3. Compile a read-only Cypher query against the known graph schema
4. Validate the compiled query using the safe Neo4j runtime
5. Execute the query against Neo4j
6. Generate a grounded answer from returned rows
7. Fall back to the older domain retrieval path if compiled query execution fails or returns no useful rows

### Exact module hit for query panel
1. `frontend/src/pages/GraphRAGPortal.jsx`
2. `frontend/src/api/client.js -> queryGraphRAG()`
3. `backend/app/routers/graphrag.py -> graph_rag_query()`
4. `backend/app/services/graphrag_service.py -> query()`
5. `backend/app/services/ai_service.py -> get_async_client()` for OpenAI calls
6. `backend/app/services/query_expansion.py`
7. `backend/app/services/semantic_query_mapper.py`
8. `backend/app/services/neo4j_query_runtime.py` for validated compiled Cypher execution
9. Fallback to:
   - `backend/app/services/neo4j_catalog_graph.py`
   - `backend/app/services/neo4j_schema_graph.py`
   - `backend/app/services/neo4j_ticket_graph.py`
10. Answer returned to frontend

---

## 7. Lexical Index and Query Expansion

```mermaid
flowchart TD
    START[App Startup]
    CST[catalog_startup.py]
    MCP[MCP list_catalog_items]
    SND[Fallback ServiceNow list_catalog_items]
    IDX[Catalog Lexical Index]
    EXP[query_expansion.py]

    START --> CST
    CST --> MCP
    CST --> SND
    MCP --> IDX
    SND --> IDX
    IDX --> EXP
```

### Why this exists
- Keeps query expansion grounded in real catalog vocabulary
- Supports lexical retrieval and semantic hinting
- Feeds both interpret/search flows and GraphRAG assistance

---

## 8. Frontend to Backend API Map

```mermaid
flowchart LR
    FE[Frontend Pages]
    CL[frontend/src/api/client.js]
    BE[FastAPI Routers]

    FE --> CL
    CL --> BE
```

### Main frontend pages
- `RequestPortal`
- `GraphRAGPortal`
- `My Tickets`
- `Admin Panel`

### Main backend routers
- `catalog.py`
- `tickets.py`
- `comments.py`
- `conversations.py`
- `graphrag.py`
- `users.py`
- `interpret.py`

---

## 9. MCP vs Direct ServiceNow Responsibilities

| Area | Through MCP | Direct ServiceNow |
|---|---|---|
| Catalog search | Yes | Fallback sometimes |
| Catalog item fetch | Yes | Sometimes fallback |
| Catalog variables | Yes | Also used for enrichment |
| Catalog change polling | Yes | No |
| Catalog UI policies | No | Yes |
| Catalog client scripts | No | Yes |
| User lookup | No | Yes |
| SR creation | No | Yes |
| CH creation | No | Yes |
| Ticket fetch/list | No | Yes |
| Comments/work notes | No | Yes |
| Ticket graph sync source | No | Yes |

---

## 9A. Direct Module Ownership by Capability

| Capability | Entry Module | Main Service Module | External Hit |
|---|---|---|---|
| Interpret request | `routers/interpret.py` | `services/ai_service.py` | OpenAI, MCP, sometimes ServiceNow |
| Catalog search | `routers/catalog.py` | `services/mcp_client_service.py` | MCP -> ServiceNow |
| Catalog form fetch | `routers/catalog.py` | `services/mcp_client_service.py`, `services/servicenow.py`, `services/neo4j_catalog_graph.py` | MCP + direct ServiceNow + Neo4j |
| Catalog graph polling | startup / polling | `services/catalog_polling_service.py` | MCP -> ServiceNow -> Neo4j |
| Ticket creation | `routers/tickets.py` | `services/ticket_service.py` | direct ServiceNow -> Neo4j |
| Ticket listing/fetch | `routers/tickets.py` | `services/ticket_service.py` | direct ServiceNow |
| Comment sync | `routers/comments.py` | `services/servicenow.py` | direct ServiceNow |
| Conversation relay | `routers/conversations.py` | `services/ai_service.py`, `services/servicenow.py` | OpenAI + direct ServiceNow |
| Query panel | `routers/graphrag.py` | `services/graphrag_service.py` | OpenAI + Neo4j |

---

## 10. Practical Notes

### Cleanest pipeline today
- Catalog ingestion and catalog retrieval are the cleanest MCP-backed flows.

### Direct-ServiceNow-heavy pipeline today
- Ticketing, comments, conversations, and user lookup still go through the direct ServiceNow service layer.

### Query panel capabilities
- Query panel now uses Neo4j as the main retrieval source.
- It supports catalog, schema, and ticket graph querying.
- Complex multi-hop questions depend on:
  - graph completeness
  - correct ticket-to-request-item-to-catalog links
  - enough underlying graph evidence in Neo4j

---

## 11. Key Files

### Frontend
- `frontend/src/pages/GraphRAGPortal.jsx`
- `frontend/src/api/client.js`

### Backend routers
- `backend/app/routers/catalog.py`
- `backend/app/routers/tickets.py`
- `backend/app/routers/graphrag.py`
- `backend/app/routers/comments.py`
- `backend/app/routers/conversations.py`
- `backend/app/routers/users.py`

### Backend services
- `backend/app/services/mcp_client_service.py`
- `backend/app/services/catalog_polling_service.py`
- `backend/app/services/catalog_startup.py`
- `backend/app/services/ticket_service.py`
- `backend/app/services/graphrag_service.py`
- `backend/app/services/neo4j_catalog_graph.py`
- `backend/app/services/neo4j_schema_graph.py`
- `backend/app/services/neo4j_ticket_graph.py`
- `backend/app/services/neo4j_query_runtime.py`
