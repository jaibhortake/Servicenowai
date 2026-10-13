# Webhook-To-Neo4j MCP Architecture

This document shows the high-level architecture for a webhook-only MCP update model. ServiceNow pushes updates into the MCP layer, MCP syncs those changes into Neo4j, and the application reads grounded graph data through the backend/MCP layer.

---

## 1. Architecture And Responsibility Boundaries

```mermaid
flowchart LR
    subgraph Source[Source System]
        SN[ServiceNow<br/>Owns source records]
    end

    subgraph MCP[MCP Layer]
        W[Webhook Receiver<br/>Receives pushed changes]
        S[Graph Sync Router<br/>Normalizes events and chooses sync path]
        T[MCP Tool Layer<br/>Serves application reads]
    end

    subgraph Graph[Graph Store]
        N[(Neo4j<br/>Catalog, form, ticket, user, item graph)]
    end

    subgraph App[Application Layer]
        B[Backend<br/>Business logic and APIs]
        F[Frontend<br/>User interface]
    end

    SN -->|push updates| W
    W -->|validate webhook| S
    S -->|resync changed record| N
    T -->|read grounded graph data| N
    B -->|request normalized records| T
    T -->|normalized response| B
    F -->|user request| B
    B -->|answer / data| F
```

---

## 2. End-To-End Update And Read Flow

```mermaid
sequenceDiagram
    participant S as ServiceNow
    participant W as Webhook Receiver
    participant R as Graph Sync Router
    participant N as Neo4j
    participant M as MCP Tool Layer
    participant B as Backend
    participant F as Frontend

    S->>W: Push record change
    W->>R: Validate and normalize event
    R->>S: Fetch full changed record when needed
    R->>N: Upsert catalog / form / ticket graph

    F->>B: Ask for data
    B->>M: Request normalized records
    M->>N: Read grounded graph data
    N-->>M: Return latest graph records
    M-->>B: Return normalized response
    B-->>F: Display result
```

---

## 3. Webhook-Only Principle

```mermaid
flowchart LR
    A[ServiceNow changes]
    B[Webhook push]
    C[MCP graph sync]
    D[Neo4j graph update]
    E[Application reads graph]

    A --> B --> C --> D --> E
```

Updates are pushed by ServiceNow. The application does not run scheduled polling to discover updates once webhooks are configured.
