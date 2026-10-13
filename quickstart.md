# Quickstart (local, no Docker)

1. Copy `backend/.env.example` to `backend/.env` and `servicenow-mcp/.env.example` to `servicenow-mcp/.env`; fill in your PDI URL and credentials (and `GROQ_API_KEY` if you want the fallback).
2. Make sure Ollama is running with `llama3.2` and `nomic-embed-text` pulled, and Neo4j Community is running on `bolt://localhost:7687`.
3. Seed the instance once: `python scripts/seed_pdi.py`
4. Start everything with `start-all.bat` (Windows) or the commands in the README.
5. Open <http://localhost:5200> and sign in.

API docs: <http://localhost:8099/docs>
