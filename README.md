# Servicenowai

An AI front door for ServiceNow. Describe a problem in plain language and the portal finds the right catalog item, pre-fills the form, files the ticket, and answers support questions using your incident history.

Built entirely on free, open-source parts:

| Layer | Choice |
|---|---|
| ITSM | ServiceNow Personal Developer Instance (free) |
| LLM | Ollama `llama3.2`, with Groq free tier as automatic fallback |
| Embeddings | Ollama `nomic-embed-text` |
| Graph store | Neo4j Community |
| Backend / Frontend | FastAPI (Python 3.12) / React + Vite + Tailwind |

## What it does

- **Chat to ticket:** intent detection, catalog suggestions, dynamic forms, request and incident creation
- **Live comments** on tickets over WebSockets
- **GraphRAG and VectorRAG** for diagnosis and solution lookup
- **Admin panel** and agent dashboard (admin only), sign-in, light/dark theme

## Quick start

You need [Ollama](https://ollama.com) running, plus either Docker or local Python 3.12, Node 20+, Java 21 and Neo4j Community.

```bash
ollama pull llama3.2 && ollama pull nomic-embed-text

cp backend/.env.example backend/.env             # add your ServiceNow + Groq details
cp servicenow-mcp/.env.example servicenow-mcp/.env
```

**Docker**

```bash
docker compose run --rm seed-pdi     # once: fill your ServiceNow instance with demo data
docker compose up --build -d
docker compose run --rm init         # once: load the knowledge graph into Neo4j
```

**Without Docker:** install the dependencies in `backend/`, `servicenow-mcp/` and `frontend/`, start Neo4j, run `python scripts/seed_pdi.py` once, load the graph with `python -m csv_graph_ingest.ingest_appliance_support_graph --csv-dir ../csv_ingest` (from `backend/`), then run `start-all.bat` (Windows).

Open <http://localhost:5200> and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `backend/.env`.

## ServiceNow instance

Get a free instance at <https://developer.servicenow.com>, then put its URL and admin credentials in both `.env` files. `scripts/seed_pdi.py` is safe to re-run. Free instances sleep when idle, so wake yours before use.

## Notes

- All secrets live in git-ignored `.env` files. Never commit them.
- Run the tests with `cd backend && pytest`.
- License: MIT. The bundled `servicenow-mcp/` keeps its own MIT license.
