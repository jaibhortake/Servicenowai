import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.graphrag_service import graphrag_service  # noqa: E402
from app.services.neo4j_catalog_graph import neo4j_catalog_graph_service  # noqa: E402
from app.services.neo4j_schema_graph import neo4j_schema_graph_service  # noqa: E402
from app.services.neo4j_ticket_graph import neo4j_ticket_graph_service  # noqa: E402

BASE_URL = os.getenv("GRAPHRAG_BASE_URL", "http://127.0.0.1:8000")
GRAPHRAG_USER_SYS_ID = os.getenv("GRAPHRAG_USER_SYS_ID")
GRAPHRAG_USER_EMAIL = os.getenv("GRAPHRAG_USER_EMAIL")
TEST_QUERIES = [
    "What fields are required to request a MacBook Pro?",
    "What are the options for the VPN access form?",
    "What columns are on the incident table?",
    "I need remote access",
    "What's the cheapest item?",
    "Which laptop request is cheapest?",
    "How many software request items do we have?",
    "List the Mac-related catalog items.",
    "Compare MacBook Pro and Standard Laptop.",
    "What open tickets do I have?",
    "How many change requests do I have?",
    "Show my latest service requests.",
    "Tell me about the cafeteria dessert menu",
]


async def run_service_checks() -> None:
    print("=" * 80)
    print("DIRECT SERVICE CHECKS")
    print("=" * 80)

    for query in TEST_QUERIES:
        result = await graphrag_service.query(
            query,
            user_context={
                "user_sys_id": GRAPHRAG_USER_SYS_ID,
                "user_email": GRAPHRAG_USER_EMAIL,
            },
        )
        print(f"\nQuery: {query}")
        print("Plan:", json.dumps(result.get("plan", {}), indent=2, ensure_ascii=True))
        print("Entities:", json.dumps(result["entities"], indent=2, ensure_ascii=True))
        print(f"Candidate count: {result.get('candidate_count', 0)}")
        print(f"Context count: {result['context_count']}")
        print(f"Answer: {result['answer']}")


async def run_endpoint_checks() -> None:
    print("\n" + "=" * 80)
    print("HTTP ENDPOINT CHECKS")
    print("=" * 80)

    async with httpx.AsyncClient(timeout=60.0) as client:
        for query in TEST_QUERIES:
            print(f"\nPOST {BASE_URL}/api/graphrag/query")
            print(f"Query: {query}")
            try:
                response = await client.post(
                    f"{BASE_URL}/api/graphrag/query",
                    json={
                        "query": query,
                        "user_sys_id": GRAPHRAG_USER_SYS_ID,
                        "user_email": GRAPHRAG_USER_EMAIL,
                    },
                )
                print(f"Status: {response.status_code}")
                print(json.dumps(response.json(), indent=2, ensure_ascii=True))
            except Exception as exc:
                print(f"HTTP request failed: {exc}")


async def main() -> int:
    neo4j_catalog_graph_service.startup()
    neo4j_schema_graph_service.startup()
    neo4j_ticket_graph_service.startup()
    try:
        print(f"Catalog graph available: {neo4j_catalog_graph_service.is_available()}")
        print(f"Schema graph available: {neo4j_schema_graph_service.is_available()}")
        print(f"Ticket graph available: {neo4j_ticket_graph_service.is_available()}")
        await run_service_checks()
        await run_endpoint_checks()
        return 0
    finally:
        neo4j_catalog_graph_service.shutdown()
        neo4j_schema_graph_service.shutdown()
        neo4j_ticket_graph_service.shutdown()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
