# Backend Tests

Backend tests are grouped by area:

- `api/` for backend API smoke and route tests.
- `graphrag/` for GraphRAG and citation behavior.
- `integration/` for cross-service flow checks.
- `neo4j/` for Neo4j routing and safety checks.
- `vectorrag/` for VectorRAG behavior.
- `webhooks/` for ServiceNow webhook graph-sync behavior.
- `legacy/` for older test/demo modules kept for reference.

Run all backend tests with:

```powershell
python -m pytest backend/tests
```
