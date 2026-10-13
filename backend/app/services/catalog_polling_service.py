import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.config import settings
from app.models.catalog import CatalogFormResponse
from app.services.mcp_client_service import mcp_client_service
from app.services.neo4j_catalog_graph import platform_neo4j_catalog_graph_service as neo4j_catalog_graph_service

logger = logging.getLogger(__name__)


class CatalogPollingService:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._bootstrapped = False
        self._last_change_cursor: Optional[str] = None
        self._last_run_started_at: Optional[str] = None
        self._last_run_finished_at: Optional[str] = None
        self._last_result: Dict[str, Any] = {
            "status": "idle",
            "processed": 0,
            "synced": 0,
            "failed": 0,
            "mode": "idle",
        }

    async def startup(self) -> None:
        if self._task is not None or not settings.catalog_polling_enabled:
            return

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="catalog-polling-service")
        logger.info(
            "Catalog polling enabled with interval=%ss batch_size=%s max_items=%s",
            settings.catalog_poll_interval_seconds,
            settings.catalog_poll_batch_size,
            settings.catalog_poll_max_items,
        )

    async def shutdown(self) -> None:
        task = self._task
        stop_event = self._stop_event
        self._task = None
        self._stop_event = None

        if stop_event is not None:
            stop_event.set()

        if task is None:
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def status(self) -> Dict[str, Any]:
        return {
            "enabled": settings.catalog_polling_enabled,
            "running": self._task is not None and not self._task.done(),
            "bootstrapped": self._bootstrapped,
            "interval_seconds": settings.catalog_poll_interval_seconds,
            "batch_size": settings.catalog_poll_batch_size,
            "max_items": settings.catalog_poll_max_items,
            "last_change_cursor": self._last_change_cursor,
            "last_run_started_at": self._last_run_started_at,
            "last_run_finished_at": self._last_run_finished_at,
            **self._last_result,
        }

    async def trigger_now(self) -> Dict[str, Any]:
        result = await self._sync_cycle()
        self._last_result = result
        return result

    async def _run_loop(self) -> None:
        try:
            while True:
                try:
                    self._last_result = await self._sync_cycle()
                except Exception:
                    logger.exception("Catalog polling cycle failed")
                    self._last_result = {
                        "status": "error",
                        "mode": "delta",
                        "processed": 0,
                        "synced": 0,
                        "failed": 1,
                    }
                assert self._stop_event is not None
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=max(5, settings.catalog_poll_interval_seconds),
                    )
                    return
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Catalog polling loop crashed")

    async def _sync_cycle(self) -> Dict[str, Any]:
        self._last_run_started_at = _utcnow()

        if not neo4j_catalog_graph_service.is_available():
            result = {
                "status": "skipped",
                "mode": "graph-unavailable",
                "processed": 0,
                "synced": 0,
                "failed": 0,
            }
            self._last_run_finished_at = _utcnow()
            return result

        if not mcp_client_service.is_available():
            result = {
                "status": "skipped",
                "mode": "mcp-unavailable",
                "processed": 0,
                "synced": 0,
                "failed": 0,
            }
            self._last_run_finished_at = _utcnow()
            return result

        try:
            if not self._bootstrapped:
                result = await self._bootstrap_graph()
                if result["status"] == "ok":
                    self._bootstrapped = True
                    self._last_change_cursor = self._last_run_started_at or _utcnow()
                return result

            result = await self._sync_changed_catalogs()
            if result["status"] == "ok":
                self._last_change_cursor = result.get("next_since") or self._last_change_cursor
            return result
        finally:
            self._last_run_finished_at = _utcnow()

    async def _bootstrap_graph(self) -> Dict[str, Any]:
        processed = 0
        synced = 0
        failed = 0
        offset = 0
        batch_size = max(1, settings.catalog_poll_batch_size)
        max_items = max(1, settings.catalog_poll_max_items)

        while processed < max_items:
            items = await mcp_client_service.list_catalog_items(
                limit=min(batch_size, max_items - processed),
                offset=offset,
            )
            if not items:
                break

            for item in items:
                sys_id = str(item.get("sys_id", "")).strip()
                if not sys_id:
                    continue

                processed += 1
                if await self._sync_catalog_item(sys_id):
                    synced += 1
                else:
                    failed += 1

                await asyncio.sleep(0.05)

                if processed >= max_items:
                    break

            if len(items) < batch_size:
                break
            offset += batch_size

        logger.info(
            "Catalog bootstrap completed: processed=%s synced=%s failed=%s",
            processed,
            synced,
            failed,
        )
        return {
            "status": "ok",
            "mode": "bootstrap",
            "processed": processed,
            "synced": synced,
            "failed": failed,
        }

    async def _sync_changed_catalogs(self) -> Dict[str, Any]:
        since_cursor = self._last_change_cursor or (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).strftime("%Y-%m-%d %H:%M:%S")
        payload = await mcp_client_service.list_catalog_changes(
            since=since_cursor,
            limit=settings.catalog_poll_max_items,
        )
        if not payload.get("success"):
            logger.warning("Catalog change detection failed: %s", payload.get("message"))
            return {
                "status": "error",
                "mode": "delta",
                "processed": 0,
                "synced": 0,
                "failed": 1,
                "next_since": payload.get("next_since") or self._last_change_cursor,
            }

        changed_ids = [
            str(sys_id).strip()
            for sys_id in payload.get("changed_catalog_item_ids", [])
            if str(sys_id).strip()
        ]
        processed = 0
        synced = 0
        failed = 0

        for sys_id in changed_ids:
            processed += 1
            if await self._sync_catalog_item(sys_id):
                synced += 1
            else:
                failed += 1

        if changed_ids:
            logger.info(
                "Catalog delta sync completed: changed=%s synced=%s failed=%s",
                len(changed_ids),
                synced,
                failed,
            )

        return {
            "status": "ok",
            "mode": "delta",
            "processed": processed,
            "synced": synced,
            "failed": failed,
            "next_since": payload.get("next_since") or self._last_change_cursor,
        }

    async def _sync_catalog_item(self, sys_id: str) -> bool:
        try:
            payload = await mcp_client_service.get_catalog_form(sys_id)
            if not payload:
                return False

            form = CatalogFormResponse.model_validate(payload)
            return neo4j_catalog_graph_service.sync_catalog_form(form)
        except Exception:
            logger.exception("Failed to sync catalog item %s", sys_id)
            return False


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


catalog_polling_service = CatalogPollingService()
