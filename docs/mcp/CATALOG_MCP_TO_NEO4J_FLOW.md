# Catalog MCP to Neo4j Flow

This file contains the standalone diagram for how catalog data is fetched through MCP and ingested into Neo4j.

---

## Correct MCP to ServiceNow to Neo4j Flow

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

### Meaning
1. The backend starts the sync.
2. The backend asks MCP for catalog data.
3. MCP calls ServiceNow.
4. ServiceNow returns data back to MCP.
5. MCP returns the payload back to the backend.
6. The backend normalizes and enriches the data.
7. The backend builds graph entities and writes them into Neo4j.

### Important Clarification
- Main catalog retrieval path:
  - `Backend -> MCP -> ServiceNow -> MCP -> Backend`
- Extra enrichment path:
  - `Backend -> ServiceNow -> Backend`
- Final graph write path:
  - `Backend -> Neo4j`

---

## Presentation-Friendly Version

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

### What comes through MCP
- Catalog items
- Catalog item details
- Variables / form fields
- Choice lists
- Catalog change feed

### What may still be fetched directly from ServiceNow
- UI policies
- Native dependency metadata
- Client scripts

### What is written to Neo4j
- `CatalogItem`
- `FilterSchema`
- `CatalogVariable`
- `CatalogChoice`
- Relationships between them
