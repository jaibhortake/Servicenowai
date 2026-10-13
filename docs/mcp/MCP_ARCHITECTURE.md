# MCP Architecture Overview

## What is MCP in This Project?

MCP (Model Context Protocol) is a **wrapper/abstraction layer** between the backend and ServiceNow API. It encapsulates all ServiceNow API complexity in one place, providing a clean interface for the backend to call tools.

---

## Two Implementations: SSE vs REST

### 1. SSE-Based MCP (Server-Sent Events)

**Location:** `servicenow-mcp/src/servicenow_mcp/server_sse.py` + `servicenow-mcp/start_http_mcp.py`

**Protocol:** Streaming HTTP with persistent connections

**Architecture:**
```
Client connects to /sse → Server holds connection open → 
Server streams responses → Client sends to /messages/ → Response streamed back
```

**Endpoints:**
- `GET /sse` - Opens persistent streaming connection
- `POST /messages/` - Client sends tool requests

**Characteristics:**
- ❌ Complex bidirectional coordination needed
- ❌ Difficult for simple request-response calls
- ✅ Good for real-time notifications (not our use case)
- ❌ Hard to debug and test

**Status:** ❌ **NOT USED** (was the wrong approach)

---

### 2. REST-Based MCP (HTTP REST)

**Location:** `servicenow-mcp/mcp_rest_server.py`

**Protocol:** Traditional HTTP request-response

**Architecture:**
```
Client sends POST /tools/{tool_name} → Server processes → Returns JSON
```

**Endpoints:**
- `POST /tools/{tool_name}` - Call a specific tool
- `GET /health` - Health check
- `GET /sse` - Fake endpoint for compatibility checks (not used)

**Characteristics:**
- ✅ Simple stateless request-response
- ✅ Easy to test with curl/Postman
- ✅ Works with any HTTP client
- ✅ Perfect for backend-to-backend communication
- ✅ Scalable (stateless)

**Status:** ✅ **CURRENTLY RUNNING** (correct approach)

---

## Side-by-Side Comparison

| Feature | SSE-based | REST-based |
|---------|-----------|-----------|
| **Startup Script** | `start_http_mcp.py` | `mcp_rest_server.py` |
| **Connection Type** | Persistent streaming | Stateless request-response |
| **Tool Call Method** | Send to `/messages/`, wait for stream | POST to `/tools/{name}`, get JSON |
| **Complexity** | High (coordinate bidirectionally) | Low (simple HTTP) |
| **Best For** | Real-time events, notifications | APIs, backend services |
| **Backend Integration** | ❌ Hard | ✅ Easy |
| **Testing** | ❌ Hard (streaming logic hidden) | ✅ Easy (standard HTTP) |
| **Scaling** | ❌ Hard (maintain many connections) | ✅ Easy (stateless) |
| **Debugging** | ❌ Hard | ✅ Easy (use curl) |

---

## Why We Use REST-Based MCP

The backend needs to:
1. Call ServiceNow tools (list items, get forms, etc.)
2. Get JSON responses back
3. Continue processing

REST is perfect for this because:
```
Backend → POST /tools/list_catalog_items → MCP processes → JSON response
Simple, clean, works with any HTTP client.
```

SSE would be overly complex:
```
Backend → Open /sse stream → Send to /messages/ → Wait for streaming response
Unnecessary complexity for request-response pattern.
```

---

## Direct API Calls vs MCP Wrapper

### Without MCP (Direct ServiceNow API Calls)

```
┌─────────────┐                           ┌──────────────────┐
│  Backend    │                           │  ServiceNow API  │
└──────┬──────┘                           └──────┬───────────┘
       │                                          │
       │ GET /api/now/table/catalog_items       │
       ├─────────────────────────────────────────>│
       │ (needs to know ServiceNow API details)  │
       │                                          │
       │ Returns: Raw ServiceNow response        │
       │<─────────────────────────────────────────┤
       │                                          │
       │ Backend parses and transforms data      │
```

**Problems:**
- Backend must know ServiceNow API structure
- Backend handles all complexity: pagination, auth, error handling
- Backend manages data normalization
- If ServiceNow API changes, backend breaks
- Business logic scattered everywhere
- Hard to reuse (can't be used by other clients)

---

### With MCP (Abstraction Layer)

```
┌─────────────┐        ┌──────────┐                    ┌──────────────────┐
│  Backend    │        │   MCP    │                    │  ServiceNow API  │
└──────┬──────┘        └────┬─────┘                    └──────┬───────────┘
       │                    │                                  │
       │ POST /tools/       │                                  │
       │ list_catalog_items │                                  │
       ├───────────────────>│                                  │
       │                    │ Handles all complexity internally
       │                    ├─────────────────────────────────>│
       │                    │ • Authenticates                  │
       │                    │ • Handles pagination             │
       │                    │ • Makes multiple API calls       │
       │                    │ • Transforms data                │
       │                    │ • Handles errors                 │
       │                    │<─────────────────────────────────┤
       │                    │                                  │
       │ Returns clean JSON │                                  │
       │<───────────────────┤                                  │
```

**Benefits:**
- Backend only knows tool names: `list_catalog_items`, `get_catalog_item`, etc.
- MCP handles all ServiceNow API details
- MCP handles: authentication, pagination, error handling, data transformation
- ServiceNow API changes? Only MCP needs updating
- Reusable by any client (mobile app, CLI, other services, etc.)
- Easier to test and maintain
- Business logic centralized in MCP

---

## Example: List Catalog Items

### Without MCP (Backend must do this):
```python
import requests

# Backend knows ServiceNow API details
response = requests.get(
    "https://servicenow/api/now/table/catalog_items",
    params={
        "sysparm_query": f"name LIKE {query}",
        "sysparm_limit": 10,
        "sysparm_offset": 0,
        "sysparm_fields": "sys_id,name,short_description"
    },
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
)

items = response.json()["result"]

# Backend must handle errors
if response.status_code != 200:
    # error handling logic
    pass

# Backend must normalize data
normalized = []
for item in items:
    normalized.append({
        "sys_id": item["sys_id"],
        "name": item["name"],
        "description": item["short_description"]
    })
```

### With MCP (Backend just calls a tool):
```python
# Backend just calls tool
result = await mcp_client.call_tool("list_catalog_items", {
    "query": query,
    "limit": 10,
    "offset": 0
})

# Result is clean, normalized JSON
items = result  # Already formatted correctly
```

---

## MCP in This Project

### Current Setup

```
Backend API (port 8000)
    ↓
MCP REST Server (port 8091) ← Using mcp_rest_server.py
    ↓
ServiceNow API (https://dev343804.service-now.com/)
```

### Available MCP Tools

The MCP server exposes these tools to the backend:

1. **list_catalog_items** - Search and list catalog items
   - Handles pagination, filtering, sorting
   - Returns normalized data

2. **get_catalog_item** - Get details of a specific catalog item
   - Fetches item metadata
   - Returns clean JSON

3. **list_catalog_item_variables** - Get form fields for a catalog item
   - Includes conditional field metadata (`depends_on`, `inactive_on_value`)
   - Returns field choices and options
   - Normalizes field types

4. **list_catalog_changes** - Get recent changes to catalog
   - Tracks catalog updates
   - Used for cache invalidation

### How Backend Uses MCP

```python
from app.services.mcp_client_service import MCPClientService

mcp_client = MCPClientService()
await mcp_client.startup()  # Connect to MCP

# Call a tool
result = await mcp_client.call_tool("list_catalog_items", {
    "query": "iPhone",
    "limit": 10
})

# Result is clean JSON
items = result
```

---

## Conditional Fields Implementation

One of the key benefits of MCP abstraction:

### ServiceNow API Returns:
```json
{
  "sys_id": "q2_sys_id",
  "question_text": "What was the original phone number?",
  "depends_on": {
    "value": "q1_sys_id"  // Reference to parent field
  },
  "inactive_on_value": "No"  // Value that hides this field
}
```

### MCP Normalizes To:
```json
{
  "sys_id": "q2_sys_id",
  "label": "What was the original phone number?",
  "depends_on": "q1_sys_id",
  "inactive_on_value": "No"
}
```

### Backend Receives:
Clean, normalized structure ready to use directly in frontend logic.

---

## Architecture Benefits

1. **Separation of Concerns**
   - Backend: Handle requests, return responses
   - MCP: Handle ServiceNow API complexity
   - Frontend: Handle UI logic

2. **Reusability**
   - Same MCP can be used by mobile app, CLI, other backends
   - Don't reinvent the wheel for each client

3. **Maintainability**
   - ServiceNow API changes? Update only MCP
   - Business logic centralized
   - Easy to test

4. **Scalability**
   - MCP is stateless (can run multiple instances)
   - Backend is stateless (can run multiple instances)
   - Easy to scale horizontally

5. **Flexibility**
   - Can swap ServiceNow with another system
   - Only need to change MCP, backend unchanged
   - Can add caching, logging, validation in MCP

---

## Files Structure

```
servicenow-mcp/
├── start_http_mcp.py          ❌ WRONG (SSE-based)
├── mcp_rest_server.py         ✅ CORRECT (REST-based)
├── mcp_http_wrapper.py        (unused)
└── src/servicenow_mcp/
    ├── server.py              (MCP core logic)
    ├── server_sse.py          ❌ WRONG (SSE implementation)
    ├── tools/
    │   ├── catalog_items.py   (list/search catalog)
    │   └── catalog_variables.py (get form fields)
    └── auth/
        └── auth_manager.py    (ServiceNow authentication)

backend/
└── app/services/
    └── mcp_client_service.py  (Backend's connection to MCP)
```

---

## Startup Instructions

### Start MCP Server (Correct Way)
```bash
cd servicenow-mcp
source .venv/Scripts/activate
python mcp_rest_server.py
# Server runs on http://127.0.0.1:8091
# Provides: POST /tools/{tool_name}, GET /health
```

### Test MCP
```bash
# Check health
curl http://127.0.0.1:8091/health

# Call a tool
curl -X POST http://127.0.0.1:8091/tools/list_catalog_items \
  -H "Content-Type: application/json" \
  -d '{"query": "iPhone", "limit": 5}'
```

---

## Summary

- **MCP = Smart abstraction layer over ServiceNow API**
- **REST-based MCP = Simple HTTP request-response (what we use)**
- **SSE-based MCP = Complex streaming (not used)**
- **Direct API calls = Backend knows all complexity (bad)**
- **MCP wrapper = Backend knows nothing, MCP handles it (good)**

This architecture makes the system maintainable, scalable, and reusable.