import json
import logging
from contextlib import asynccontextmanager
from json import JSONDecodeError
from typing import Any, Dict

from . import catalog_tools
from app.config import settings
from app.services.neo4j_catalog_graph import platform_neo4j_catalog_graph_service as neo4j_catalog_graph_service
from app.services.neo4j_query_runtime import platform_neo4j_query_runtime_service as neo4j_query_runtime_service
from app.services.neo4j_ticket_graph import platform_neo4j_ticket_graph_service as neo4j_ticket_graph_service
from app.services.servicenow_webhook_graph_sync import (
    servicenow_webhook_graph_sync_service,
    validate_servicenow_webhook_auth,
)
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    graph_services = (
        neo4j_catalog_graph_service,
        neo4j_ticket_graph_service,
        neo4j_query_runtime_service,
    )
    for service in graph_services:
        try:
            service.startup()
        except Exception:
            logger.exception("Failed to start graph service for MCP webhook sync")

    try:
        yield
    finally:
        for service in reversed(graph_services):
            try:
                service.shutdown()
            except Exception:
                logger.exception("Failed to stop graph service for MCP webhook sync")


app = FastAPI(title="ServiceNow MCP Server", lifespan=lifespan)


class SearchRequest(BaseModel):
    query: str
    intent: str = ""
    search_keywords: str = "[]"


class CatalogFormRequest(BaseModel):
    sys_id: str


class ListCatalogItemsRequest(BaseModel):
    query: str = ""
    limit: int = 100
    offset: int = 0
    active: bool = True


class ListCatalogChangesRequest(BaseModel):
    limit: int = 200
    since: str = ""


class ListCatalogItemVariablesRequest(BaseModel):
    catalog_item_id: str
    include_details: bool = False


class GetCatalogItemRequest(BaseModel):
    item_id: str


class ListGraphRecordsRequest(BaseModel):
    limit: int = 50
    offset: int = 0


class GraphRecordRequest(BaseModel):
    sys_id: str


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "servicenow-mcp",
        "webhook_sync": servicenow_webhook_graph_sync_service.status(),
    }


@app.get("/webhooks/status")
async def webhook_status():
    return servicenow_webhook_graph_sync_service.status()


@app.post("/webhooks/servicenow")
async def receive_servicenow_webhook(request: Request):
    return await _receive_webhook(request)


@app.post("/webhooks/generic_update")
async def receive_generic_update_webhook(request: Request):
    return await _receive_webhook(request)


@app.post("/webhooks/catalog_item")
async def receive_catalog_item_webhook(request: Request):
    return await _receive_webhook(request, table_hint="sc_cat_item")


@app.post("/webhooks/form_submission")
async def receive_form_submission_webhook(request: Request):
    return await _receive_webhook(request)


@app.post("/webhooks/ticket_update")
async def receive_ticket_update_webhook(request: Request):
    return await _receive_webhook(request)


async def _receive_webhook(request: Request, table_hint: str = "") -> Dict[str, Any]:
    raw_body = await request.body()
    if not settings.servicenow_webhook_secret:
        reason = "SERVICENOW_WEBHOOK_SECRET is not configured"
        servicenow_webhook_graph_sync_service.record_rejected(reason)
        raise HTTPException(status_code=503, detail=reason)

    if not validate_servicenow_webhook_auth(raw_body, request.headers):
        reason = "Invalid ServiceNow webhook signature or shared secret"
        servicenow_webhook_graph_sync_service.record_rejected(reason)
        raise HTTPException(status_code=401, detail=reason)

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, JSONDecodeError) as exc:
        reason = f"Invalid JSON webhook payload: {exc}"
        servicenow_webhook_graph_sync_service.record_rejected(reason)
        raise HTTPException(status_code=400, detail=reason) from exc

    if not isinstance(payload, dict):
        reason = "Webhook payload must be a JSON object"
        servicenow_webhook_graph_sync_service.record_rejected(reason)
        raise HTTPException(status_code=400, detail=reason)

    result = await servicenow_webhook_graph_sync_service.handle_webhook(
        payload,
        table_hint=table_hint,
    )
    if result.get("status") == "rejected":
        raise HTTPException(status_code=422, detail=result)
    return result


@app.post("/tools/search_catalog")
async def search_catalog(req: SearchRequest):
    try:
        intent_data = {
            "intent": req.intent,
            "search_keywords": json.loads(req.search_keywords or "[]"),
        }
        items = catalog_tools.search_catalog(req.query, intent_data)
        return {
            "success": True,
            "result": json.dumps(items),
        }
    except Exception as e:
        logger.exception("Error in search_catalog")
        return {"success": False, "error": str(e)}


@app.post("/tools/get_catalog_form")
async def get_catalog_form(req: CatalogFormRequest):
    try:
        form = catalog_tools.get_catalog_form(req.sys_id)
        return {
            "success": True,
            "result": json.dumps(form),
        }
    except Exception as e:
        logger.exception("Error in get_catalog_form")
        return {"success": False, "error": str(e)}


@app.post("/tools/list_catalog_items")
async def list_catalog_items(req: ListCatalogItemsRequest):
    try:
        items = catalog_tools.list_catalog_items(
            query=req.query or None,
            limit=req.limit,
            offset=req.offset,
            active=req.active
        )
        return {
            "success": True,
            "result": json.dumps({"items": items}),
        }
    except Exception as e:
        logger.exception("Error in list_catalog_items")
        return {"success": False, "error": str(e)}


@app.post("/tools/list_catalog_changes")
async def list_catalog_changes(req: ListCatalogChangesRequest):
    try:
        changes = catalog_tools.list_catalog_changes(
            since=req.since or None,
            limit=req.limit
        )
        return {
            "success": True,
            "result": json.dumps(changes),
        }
    except Exception as e:
        logger.exception("Error in list_catalog_changes")
        return {"success": False, "error": str(e)}


@app.post("/tools/get_catalog_item")
async def get_catalog_item(req: GetCatalogItemRequest):
    try:
        item = catalog_tools.get_catalog_item(req.item_id)
        return {
            "success": True,
            "result": json.dumps(item),
        }
    except Exception as e:
        logger.exception("Error in get_catalog_item")
        return {"success": False, "error": str(e)}


@app.post("/tools/list_catalog_item_variables")
async def list_catalog_item_variables(req: ListCatalogItemVariablesRequest):
    try:
        variables = catalog_tools.list_catalog_item_variables(
            catalog_item_id=req.catalog_item_id,
            include_details=req.include_details
        )
        return {
            "success": True,
            "result": json.dumps({"variables": variables}),
        }
    except Exception as e:
        logger.exception("Error in list_catalog_item_variables")
        return {"success": False, "error": str(e)}


@app.post("/tools/list_service_requests_for_graph")
async def list_service_requests_for_graph(req: ListGraphRecordsRequest):
    try:
        payload = catalog_tools.list_service_requests_for_graph(limit=req.limit, offset=req.offset)
        return {"success": True, "result": json.dumps(payload)}
    except Exception as e:
        logger.exception("Error in list_service_requests_for_graph")
        return {"success": False, "error": str(e)}


@app.post("/tools/get_service_request_for_graph")
async def get_service_request_for_graph(req: GraphRecordRequest):
    try:
        payload = catalog_tools.get_service_request_for_graph(req.sys_id)
        return {"success": True, "result": json.dumps(payload)}
    except Exception as e:
        logger.exception("Error in get_service_request_for_graph")
        return {"success": False, "error": str(e)}


@app.post("/tools/get_request_item_for_graph")
async def get_request_item_for_graph(req: GraphRecordRequest):
    try:
        payload = catalog_tools.get_request_item_for_graph(req.sys_id)
        return {"success": True, "result": json.dumps(payload)}
    except Exception as e:
        logger.exception("Error in get_request_item_for_graph")
        return {"success": False, "error": str(e)}


@app.post("/tools/list_change_requests_for_graph")
async def list_change_requests_for_graph(req: ListGraphRecordsRequest):
    try:
        payload = catalog_tools.list_change_requests_for_graph(limit=req.limit, offset=req.offset)
        return {"success": True, "result": json.dumps(payload)}
    except Exception as e:
        logger.exception("Error in list_change_requests_for_graph")
        return {"success": False, "error": str(e)}


@app.post("/tools/get_change_request_for_graph")
async def get_change_request_for_graph(req: GraphRecordRequest):
    try:
        payload = catalog_tools.get_change_request_for_graph(req.sys_id)
        return {"success": True, "result": json.dumps(payload)}
    except Exception as e:
        logger.exception("Error in get_change_request_for_graph")
        return {"success": False, "error": str(e)}


def main():
    import uvicorn

    logger.info("Starting ServiceNow MCP server on http://127.0.0.1:8091")
    uvicorn.run(app, host="127.0.0.1", port=8091, log_level="info")


if __name__ == "__main__":
    main()
