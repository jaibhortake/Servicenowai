# Servicenowai - project notes

- Backend: `backend/app` (FastAPI, port 8099). Routers in `app/routers`, logic in `app/services`.
- LLM access goes through `app/services/llm_client.py` (OpenAI-compatible; Ollama primary, Groq fallback). Call sites use `ai_service.get_async_client()` / `get_client()` unchanged.
- Auth: `app/auth.py` + `app/routers/auth.py`; all routers except `/api/auth/login` and `/health` require a bearer token (websockets use `?token=`). Settings/admin routes need the admin role.
- ServiceNow access: `app/services/servicenow.py` (Table API). Its HTTP client flattens the `{value, display_value}` wrappers newer releases return.
- MCP bridge: `servicenow-mcp/` (REST wrapper on 8091). Backend client: `app/services/mcp_client_service.py`.
- Seed a PDI: `scripts/seed_pdi.py` (idempotent). Neo4j graph data: `backend/csv_graph_ingest`.
- Frontend: `frontend/src` (React + Vite, port 5200). Auth in `src/auth`, shell in `src/components/Layout`.
- Secrets live only in git-ignored `backend/.env` and `servicenow-mcp/.env`. Never commit them.
- Run tests: `cd backend && pytest`.
