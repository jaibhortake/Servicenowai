# Backend Legacy Code

This folder keeps older backend code paths that are not loaded by the current FastAPI entrypoint, `app.main:app`.

- `mcp_app/`: older backend-hosted MCP/webhook API. The current MCP process is `servicenow-mcp/mcp_rest_server.py`.
- `catalog_retrieval/`: older catalog retrieval experiments kept for reference.
