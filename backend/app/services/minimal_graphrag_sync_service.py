import logging
from typing import Any, Dict, Optional

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.config import settings
from app.models.catalog import CatalogFormResponse
from app.services.mcp_client_service import mcp_client_service
from app.services.neo4j_catalog_graph import neo4j_catalog_graph_service
from app.services.neo4j_ticket_graph import neo4j_ticket_graph_service

logger = logging.getLogger(__name__)


MINIMAL_GRAPHRAG_LABELS = [
    "CatalogChoice",
    "CatalogVariable",
    "FilterSchema",
    "CatalogItem",
    "RequestItem",
    "ServiceRequest",
    "ChangeRequest",
    "Ticket",
    "User",
]

SCHEMA_METADATA_LABELS = [
    "SampleRecord",
    "Column",
    "Table",
]


class MinimalGraphRAGSyncService:
    def status(self) -> Dict[str, Any]:
        return {
            "minimal_graph_mode": settings.graphrag_minimal_graph_mode,
            "mcp_available": mcp_client_service.is_available(),
            "catalog_graph_available": neo4j_catalog_graph_service.is_available(),
            "ticket_graph_available": neo4j_ticket_graph_service.is_available(),
        }

    def clear_minimal_graph(self, include_schema_metadata: bool = True) -> Dict[str, Any]:
        labels = MINIMAL_GRAPHRAG_LABELS + (SCHEMA_METADATA_LABELS if include_schema_metadata else [])
        connection = settings.graphrag_neo4j_config()
        if not connection.enabled or not connection.is_complete:
            return {"cleared": False, "deleted": 0, "reason": "Neo4j is not configured"}
        if connection.is_write_protected:
            return {
                "cleared": False,
                "deleted": 0,
                "reason": f"Neo4j graph is write-protected: {connection.write_protection_reason}",
            }

        try:
            driver = GraphDatabase.driver(
                connection.uri,
                auth=(connection.username, connection.password),
                connection_timeout=3.0,
                max_transaction_retry_time=2.0,
            )
            with driver:
                with driver.session(database=connection.database) as session:
                    deleted = 0
                    while True:
                        deleted_batch = session.execute_write(self._delete_labeled_nodes, labels)
                        deleted += deleted_batch
                        if deleted_batch == 0:
                            break
            return {"cleared": True, "deleted": deleted, "labels": labels}
        except Neo4jError as exc:
            logger.exception("Failed to clear minimal GraphRAG graph")
            return {"cleared": False, "deleted": 0, "reason": str(exc), "labels": labels}

    async def bootstrap_from_mcp(
        self,
        *,
        batch_size: int = 25,
        max_catalog_items: Optional[int] = None,
        max_service_requests: Optional[int] = None,
        max_change_requests: Optional[int] = None,
        clear_existing: bool = False,
    ) -> Dict[str, Any]:
        if not mcp_client_service.is_available():
            return {"status": "unavailable", "reason": "MCP client is not connected"}
        if neo4j_catalog_graph_service.is_write_protected() or neo4j_ticket_graph_service.is_write_protected():
            return {"status": "unavailable", "reason": "GraphRAG graph is write-protected"}
        if not neo4j_catalog_graph_service.is_available() or not neo4j_ticket_graph_service.is_available():
            return {"status": "unavailable", "reason": "Neo4j graph services are not available"}

        result: Dict[str, Any] = {
            "status": "ok",
            "cleared": None,
            "catalog": {"synced": 0, "failed": 0, "processed": 0},
            "service_requests": {"synced": 0, "failed": 0, "processed": 0},
            "change_requests": {"synced": 0, "failed": 0, "processed": 0},
        }

        if clear_existing:
            result["cleared"] = self.clear_minimal_graph(include_schema_metadata=True)
            if not result["cleared"].get("cleared"):
                result["status"] = "partial"

        await self._sync_catalog_items(result, batch_size, max_catalog_items)
        await self._sync_service_requests(result, batch_size, max_service_requests)
        await self._sync_change_requests(result, batch_size, max_change_requests)
        return result

    async def _sync_catalog_items(
        self,
        result: Dict[str, Any],
        batch_size: int,
        max_items: Optional[int],
    ) -> None:
        offset = 0
        while max_items is None or result["catalog"]["processed"] < max_items:
            remaining = None if max_items is None else max_items - result["catalog"]["processed"]
            limit = min(batch_size, remaining) if remaining is not None else batch_size
            if limit <= 0:
                break

            items = await mcp_client_service.list_catalog_items(limit=limit, offset=offset)
            if not items:
                break
            offset += len(items)

            for item in items:
                result["catalog"]["processed"] += 1
                sys_id = str(item.get("sys_id") or "").strip()
                if not sys_id:
                    result["catalog"]["failed"] += 1
                    continue
                if await self._sync_catalog_form(sys_id):
                    result["catalog"]["synced"] += 1
                else:
                    result["catalog"]["failed"] += 1

            if len(items) < limit:
                break

    async def _sync_service_requests(
        self,
        result: Dict[str, Any],
        batch_size: int,
        max_items: Optional[int],
    ) -> None:
        offset = 0
        while max_items is None or result["service_requests"]["processed"] < max_items:
            remaining = None if max_items is None else max_items - result["service_requests"]["processed"]
            limit = min(batch_size, remaining) if remaining is not None else batch_size
            if limit <= 0:
                break

            records = await mcp_client_service.list_service_requests_for_graph(limit=limit, offset=offset)
            if not records:
                break
            offset += len(records)

            for record in records:
                result["service_requests"]["processed"] += 1
                ticket = record.get("ticket") or {}
                request_item = record.get("request_item")
                if not ticket.get("sys_id"):
                    result["service_requests"]["failed"] += 1
                    continue
                if neo4j_ticket_graph_service.sync_service_request(ticket, request_item):
                    result["service_requests"]["synced"] += 1
                    catalog_sys_id = str((request_item or {}).get("cat_item_sys_id") or "").strip()
                    if catalog_sys_id:
                        await self._sync_catalog_form(catalog_sys_id)
                else:
                    result["service_requests"]["failed"] += 1

            if len(records) < limit:
                break

    async def _sync_change_requests(
        self,
        result: Dict[str, Any],
        batch_size: int,
        max_items: Optional[int],
    ) -> None:
        offset = 0
        while max_items is None or result["change_requests"]["processed"] < max_items:
            remaining = None if max_items is None else max_items - result["change_requests"]["processed"]
            limit = min(batch_size, remaining) if remaining is not None else batch_size
            if limit <= 0:
                break

            records = await mcp_client_service.list_change_requests_for_graph(limit=limit, offset=offset)
            if not records:
                break
            offset += len(records)

            for record in records:
                result["change_requests"]["processed"] += 1
                ticket = record.get("ticket") or {}
                if not ticket.get("sys_id"):
                    result["change_requests"]["failed"] += 1
                    continue
                if neo4j_ticket_graph_service.sync_change_request(ticket):
                    result["change_requests"]["synced"] += 1
                else:
                    result["change_requests"]["failed"] += 1

            if len(records) < limit:
                break

    async def _sync_catalog_form(self, sys_id: str) -> bool:
        try:
            payload = await mcp_client_service.get_catalog_form(sys_id)
            if not payload:
                return False
            form = CatalogFormResponse.model_validate(payload)
            return neo4j_catalog_graph_service.sync_catalog_form(form)
        except Exception:
            logger.exception("Failed to sync catalog form %s through MCP", sys_id)
            return False

    @staticmethod
    def _delete_labeled_nodes(tx, labels: list[str]) -> int:
        result = tx.run(
            """
            MATCH (node)
            WHERE any(label IN labels(node) WHERE label IN $labels)
            WITH node LIMIT 10000
            WITH collect(node) AS nodes, count(node) AS deleted
            FOREACH (node IN nodes | DETACH DELETE node)
            RETURN deleted
            """,
            labels=labels,
        )
        record = result.single()
        return int(record["deleted"] or 0) if record else 0


minimal_graphrag_sync_service = MinimalGraphRAGSyncService()
