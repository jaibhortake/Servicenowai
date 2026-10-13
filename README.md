# Servicenowai

AI-assisted IT service management on top of ServiceNow. Describe a problem in plain language; the portal finds the right catalog item, pre-fills the form, files the ticket, and answers support questions with GraphRAG / VectorRAG over your incident history.

Everything runs on free, open-source parts:

| Layer | Choice |
|---|---|
| ITSM | ServiceNow **Personal Developer Instance** (free) |
| LLM | **Ollama** (`llama3.2`) by default, **Groq** free tier as automatic fallback |
| Embeddings | Ollama `nomic-embed-text` (768-dim) |
| Graph / vector store | **Neo4j Community** |
| Backend | FastAPI (Python 3.12) |
| Frontend | React 18, Vite, Tailwind |
| Bridge | ServiceNow MCP server (REST wrapper, port 8091) |

## Features

- Chat to ticket: intent detection, catalog ranking, dynamic forms with ServiceNow UI-policy conditional fields, SR / change creation
- Live comments and conversation threads over WebSockets
- GraphRAG query panel and "Create Case" diagnostic flow (ontology: Issue → RootCause → Condition → Runbook)
- VectorRAG over `backend/data/Solutions.txt`
- Admin panel and agent dashboard (admin only)
- Sign-in (JWT), light/dark theme, collapsible sidebar, Ctrl+K page search

## Quick start (Docker)

Prerequisites: Docker, [Ollama](https://ollama.com) running on the host.

```bash
ollama pull llama3.2
ollama pull nomic-embed-text

cp backend/.env.example backend/.env            # fill in ServiceNow + Groq values
cp servicenow-mcp/.env.example servicenow-mcp/.env

docker compose run --rm seed-pdi                 # once: tables, data, catalog items in your PDI
docker compose up --build -d
docker compose run --rm init                     # once: load csv_ingest into Neo4j
```

Open <http://localhost:5200> and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `backend/.env`.

## Local development (no Docker)

```bash
# backend
cd backend && python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8099

# MCP bridge
cd servicenow-mcp && python -m venv .venv && pip install -e .
MCP_REST_PORT=8091 python mcp_rest_server.py

# frontend
cd frontend && npm install && npm run dev     # http://localhost:5200 (proxies /api to :8099)
```

Neo4j Community 5.x is needed locally for the graph features (`neo4j start`, or `docker compose up neo4j`).

## ServiceNow developer instance

1. Sign up at <https://developer.servicenow.com> and request a PDI.
2. Put its URL and admin credentials in `backend/.env` and `servicenow-mcp/.env`.
3. Run `python scripts/seed_pdi.py` (or the `seed-pdi` compose job). It is idempotent and creates:
   - the `u_app_*` tables and columns from `csv_ingest/`, plus custom columns on `incident` and `problem`
   - 50 incidents, 5 problems and the supporting context rows
   - four demo catalog items with variables and conditional UI policies

PDIs hibernate when idle and are reclaimed after about 10 days of inactivity; re-run the seed script on a new instance. ServiceNow may force a password change on first login, so keep `.env` in sync.

## Configuration

All settings live in `backend/.env` (see [backend/.env.example](backend/.env.example)). Notable ones:

- `LLM_PROVIDER` `ollama` | `groq`; `GROQ_API_KEY` enables the fallback
- `CATALOG_POLLING_ENABLED=true` since a PDI cannot call webhooks on your machine
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `APP_SECRET_KEY` for portal sign-in

Secrets stay in git-ignored `.env` files. Never commit them.

## Tests

```bash
cd backend && pytest
```

## License

MIT. See [LICENSE](LICENSE) (the bundled `servicenow-mcp/` keeps its own MIT license).
