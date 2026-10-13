import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from app.services import servicenow as snow, ai_service
from app.models.catalog import CatalogFormResponse, CatalogItem, RankCatalogRequest
from app.services.mcp_client_service import mcp_client_service
from app.services.catalog_graph_sync_service import sync_catalog_form_model_to_graph
from app.services.neo4j_catalog_graph import platform_neo4j_catalog_graph_service as neo4j_catalog_graph_service
from app.services import catalog_startup
from app.services.form_prefill_service import prefill_form
from app.services.semantic_query_mapper import parse_semantic_intent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("", response_model=List[CatalogItem])
async def list_catalogs(category: Optional[str] = Query(None), limit: int = Query(50, le=200)):
    try:
        items = snow.list_catalog_items(category=category, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")
    return [
        CatalogItem(
            sys_id=i.get("sys_id", ""),
            name=i.get("name", ""),
            short_description=i.get("short_description", ""),
            category=_val(i.get("category")),
            price=_val(i.get("price")),
        )
        for i in items
    ]


@router.post("/rank", response_model=List[CatalogItem])
async def rank_catalogs(req: RankCatalogRequest):
    try:
        ranked = await ai_service.rank_catalogs_async(req.query, req.catalogs)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return [CatalogItem(**{k: v for k, v in c.items() if k in CatalogItem.model_fields}) for c in ranked]


@router.get("/search", response_model=List[CatalogItem])
async def search_catalog(query: Optional[str] = Query(None), limit: int = Query(50, le=200)):
    """Search catalog items by query string. This endpoint intentionally
    handles the literal `/api/catalog/search` path so it isn't captured by
    the `{sys_id}` route (which would treat `search` as a sys_id).
    """
    logger.info(f"[SEARCH] query={query} limit={limit}")
    try:
        if query:
            # Parse semantic intent to enable semantic filtering
            semantic_intent = parse_semantic_intent(query)
            intent_data = {"search_keywords": semantic_intent.get("keywords", [])}
            # Prefer MCP when available (returns normalized items with semantic filtering)
            if mcp_client_service.is_available():
                items = await mcp_client_service.search_catalog(query, intent_data)
            else:
                items = await asyncio.to_thread(snow.list_catalog_items, limit=limit, query=query)
        else:
            # No query -> list top items
            if mcp_client_service.is_available():
                items = await mcp_client_service.list_catalog_items(limit=limit, offset=0)
            else:
                items = await asyncio.to_thread(snow.list_catalog_items, limit=limit)
    except Exception as e:
        logger.exception("[SEARCH] ServiceNow/MCP error")
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    return [
        CatalogItem(
            sys_id=i.get("sys_id", ""),
            name=i.get("name", ""),
            short_description=i.get("short_description", ""),
            category=_val(i.get("category")),
            price=_val(i.get("price")),
            relevance_score=float(i.get("relevance_score", 0.0)),
            match_reason=i.get("match_reason"),
        )
        for i in items
    ]


@router.get("/lexical-index/status", response_model=dict)
async def lexical_index_status():
    """Get status of the lexical index for query expansion."""
    return catalog_startup.get_index_status()


@router.post("/lexical-index/rebuild", response_model=dict)
async def rebuild_lexical_index(limit: int = Query(5000)):
    """Manually rebuild the lexical index from catalog."""
    logger.info(f"[REBUILD] Starting manual lexical index rebuild (limit={limit})")
    success = await catalog_startup.initialize_catalog_index_async(limit=limit)
    status = catalog_startup.get_index_status()

    if success:
        logger.info(f"[REBUILD] ✓ Index rebuilt successfully: {status}")
        return {"success": True, "status": status}
    else:
        logger.warning(f"[REBUILD] ✗ Index rebuild failed: {status}")
        return {"success": False, "status": status}


class PrefillRequest(BaseModel):
    query: str
    variables: List[Dict[str, Any]]
    catalog_item: Optional[Dict[str, Any]] = None
    intent_context: Optional[Dict[str, Any]] = None


class PrefillResponse(BaseModel):
    values: Dict[str, Any]


@router.post("/prefill", response_model=PrefillResponse)
async def prefill_catalog_form(req: PrefillRequest):
    logger.info(f"[PREFILL] Request: query='{req.query[:80]}' vars={len(req.variables)}")
    try:
        values = await asyncio.wait_for(
            prefill_form(req.query, req.variables, req.catalog_item, req.intent_context),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        logger.warning("[PREFILL] LLM timeout, returning empty prefill")
        values = {}
    except Exception as e:
        logger.exception(f"[PREFILL] Error: {e}")
        values = {}
    return PrefillResponse(values=values)


@router.get("/{sys_id}", response_model=CatalogItem)
async def get_catalog_item(sys_id: str):
    try:
        item = snow.get_catalog_item(sys_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not item:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    return CatalogItem(
        sys_id=item.get("sys_id", ""),
        name=item.get("name", ""),
        short_description=item.get("short_description", ""),
        description=item.get("description", ""),
        category=_val(item.get("category")),
        price=_val(item.get("price")),
    )


async def _fetch_form_background(sys_id: str) -> None:
    """Fetch fresh form from MCP and update cache in background."""
    logger.info(f"[FETCH_BG] Starting background fetch for sys_id={sys_id}")
    try:
        if not mcp_client_service.is_available():
            logger.info(f"[FETCH_BG] MCP unavailable, skipping fetch")
            return
        logger.info(f"[FETCH_BG] Fetching form from MCP")
        payload = await asyncio.wait_for(mcp_client_service.get_catalog_form(sys_id), timeout=30.0)
        if payload:
            form = CatalogFormResponse.model_validate(payload)
            logger.info(f"[FETCH_BG] Syncing form to Neo4j")
            neo4j_catalog_graph_service.sync_catalog_form(form)
            logger.info(f"[FETCH_BG] Starting enrichment")
            await _do_enrich(form, sys_id)  # Enrich too
            logger.info(f"[FETCH_BG] Cache updated for sys_id={sys_id}")
        else:
            logger.warning(f"[FETCH_BG] No payload from MCP")
    except Exception as e:
        logger.warning(f"[FETCH_BG] Background fetch failed: {e}", exc_info=True)


async def _enrich_form_background(form: CatalogFormResponse, sys_id: str) -> None:
    """Enrich form in background (non-blocking). Saves enriched form to Neo4j."""
    logger.info(f"[ENRICH_BG] Starting background enrichment for sys_id={sys_id}")
    try:
        await _enrich_form_with_ui_policies(form, sys_id)
        logger.info(f"[ENRICH_BG] Background enrichment complete for sys_id={sys_id}")
    except Exception as e:
        logger.warning(f"[ENRICH_BG] Background enrichment failed: {e}", exc_info=True)


async def _enrich_form_with_ui_policies(form: CatalogFormResponse, sys_id: str) -> CatalogFormResponse:
    """Fetch UI policy conditions AND native depends_on in parallel, cap at 15 s."""
    try:
        form = await asyncio.wait_for(_do_enrich(form, sys_id), timeout=15.0)
    except asyncio.TimeoutError:
        logger.warning(f"[ENRICH] Enrichment timed out for sys_id={sys_id}, returning unenriched form")
    except Exception as e:
        logger.warning(f"[ENRICH] Enrichment failed for sys_id={sys_id}: {e}", exc_info=True)
    return form


async def _do_enrich(form: CatalogFormResponse, sys_id: str) -> CatalogFormResponse:
    logger.info(f"[ENRICH] Starting enrichment for sys_id={sys_id}, {len(form.variables)} variables")
    # Run all three ServiceNow lookups in parallel
    ui_task  = asyncio.to_thread(snow.list_catalog_ui_policies, sys_id)
    var_task = asyncio.to_thread(snow.list_catalog_item_variables, sys_id)
    scr_task = asyncio.to_thread(snow.list_catalog_client_scripts, sys_id)

    ui_mappings_raw, raw_vars, scripts = await asyncio.gather(
        ui_task, var_task, scr_task, return_exceptions=True
    )

    for label, result in (("ui_policies", ui_mappings_raw), ("variables", raw_vars), ("scripts", scripts)):
        if isinstance(result, BaseException):
            logger.warning(f"[ENRICH] sys_id={sys_id}: {label} subtask failed: {result!r}")

    # --- Source 1: UI policy mappings ---
    ui_mappings: list = ui_mappings_raw if isinstance(ui_mappings_raw, list) else []
    mapping_by_var = {m["variable_sys_id"]: m for m in ui_mappings}
    logger.info(f"[ENRICH] sys_id={sys_id}: {len(ui_mappings)} UI policy mapping(s)")
    if ui_mappings:
        logger.info(f"[ENRICH] UI policy mappings: {ui_mappings}")
    else:
        logger.info(f"[ENRICH] No UI policy mappings found. raw response type: {type(ui_mappings_raw)}, value: {ui_mappings_raw if not isinstance(ui_mappings_raw, list) else 'empty list'}")

    # --- Source 2: Native depends_on from item_option_new ---
    native_deps: Dict[str, Dict] = {}
    if isinstance(raw_vars, list):
        for rv in raw_vars:
            var_id   = _extract_ref_value(rv.get("sys_id"))
            dep_sys  = _extract_ref_value(rv.get("depends_on")) if rv.get("depends_on") else None
            inact    = _extract_ref_value(rv.get("inactive_on_value")) if rv.get("inactive_on_value") else None
            if dep_sys and var_id:
                native_deps[var_id] = {"depends_on": dep_sys, "inactive_on_value": inact}
    if native_deps:
        logger.info(f"[ENRICH] Native depends_on for {len(native_deps)} var(s): {native_deps}")

    # --- Source 3: Client scripts (logged only) ---
    if isinstance(scripts, list) and scripts:
        logger.info(f"[ENRICH] {len(scripts)} client script(s): {[s.get('name') for s in scripts]}")

    if not ui_mappings and not native_deps:
        logger.info(f"[ENRICH] No conditional data found in ServiceNow for sys_id={sys_id}")
        return form

    # --- Merge enrichment into form variables ---
    enriched = []
    for var in form.variables:
        updates: Dict[str, Any] = {}
        if var.sys_id in mapping_by_var:
            m = mapping_by_var[var.sys_id]
            updates["depends_on"] = m["depends_on"]
            updates["active_on_value"] = m["active_on_value"]
        elif var.sys_id in native_deps and not var.depends_on:
            nd = native_deps[var.sys_id]
            updates["depends_on"] = nd["depends_on"]
            if nd.get("inactive_on_value"):
                updates["inactive_on_value"] = nd["inactive_on_value"]
        if updates:
            var = var.model_copy(update=updates)
        enriched.append(var)

    applied = sum(1 for v in enriched if v.depends_on)
    logger.info(f"[ENRICH] Done: {applied}/{len(enriched)} variables have dependency")
    form = form.model_copy(update={"variables": enriched})
    if applied > 0:
        logger.info(f"[ENRICH] Saving enriched form to Neo4j (applied={applied})")
        try:
            neo4j_catalog_graph_service.sync_catalog_form(form)
            logger.info(f"[ENRICH] Successfully saved enriched form to Neo4j")
        except Exception as e:
            logger.error(f"[ENRICH] Failed to save enriched form to Neo4j: {e}", exc_info=True)
    else:
        logger.info(f"[ENRICH] No dependencies applied, skipping Neo4j save")
    return form


def _parse_ui_policy_mappings(ui_policies: list, ui_policy_actions: list) -> list:
    """Parse UI policies + pre-fetched actions into variable dependency mappings.
    Handles both plain-string and sysparm_display_value=all dict formats."""
    actions_by_policy: Dict[str, list] = {}
    for action in ui_policy_actions:
        # ui_policy field can be a dict (when sysparm_display_value=all) or string
        ui_policy_ref = action.get("ui_policy", "")
        pid = _extract_ref_value(ui_policy_ref) or ""
        pid = str(pid).strip()
        if pid:
            actions_by_policy.setdefault(pid, []).append(action)

    mappings = []
    for policy in ui_policies:
        policy_id = str(policy.get("sys_id") or "").strip()
        raw_conditions = policy.get("catalog_conditions", "")
        conditions_str = str(_extract_ref_value(raw_conditions) or raw_conditions or "")

        dep_field_sys_id = None
        active_value = None
        for part in conditions_str.split("^"):
            part = part.strip()
            if part.startswith("IO:") and "=" in part:
                rest = part[3:]
                dep_field_sys_id, active_value = rest.split("=", 1)
                break

        if not dep_field_sys_id or not active_value:
            logger.debug(f"[POLICY] Skipping policy {policy_id}: no IO condition in '{conditions_str}'")
            continue

        dep_field_sys_id = dep_field_sys_id.strip()
        active_value = active_value.strip()

        policy_actions = actions_by_policy.get(policy_id, [])
        logger.debug(f"[POLICY] {policy_id}: dep={dep_field_sys_id} val={active_value} → {len(policy_actions)} actions")
        for action in policy_actions:
            var_ref = action.get("catalog_variable", "")
            var_sys_id = (_extract_ref_value(var_ref) or "").replace("IO:", "").strip()
            if var_sys_id:
                mappings.append({
                    "variable_sys_id": var_sys_id,
                    "depends_on": dep_field_sys_id,
                    "active_on_value": active_value,
                })
    return mappings


def _extract_ref_value(field: Any) -> Optional[str]:
    """Extract sys_id/value from a ServiceNow reference field (may be dict or string)."""
    if isinstance(field, dict):
        return field.get("value") or field.get("display_value") or ""
    if field:
        return str(field).strip()
    return None


@router.get("/{sys_id}/form", response_model=CatalogFormResponse)
async def get_catalog_form(sys_id: str, refresh: bool = Query(False, description="Force refresh from ServiceNow")):
    logger.info(f"[FORM] Request for sys_id={sys_id}, refresh={refresh}")
    
    # Always try cache first - it's the fastest and most reliable
    cached_form = neo4j_catalog_graph_service.get_catalog_form(sys_id)
    if cached_form is not None and not refresh:
        logger.info("[FORM] Returning from cache")
        return cached_form
    
    # Need fresh data from MCP
    if not mcp_client_service.is_available():
        if cached_form:
            logger.warning("[FORM] MCP unavailable, returning cached form")
            return cached_form
        raise HTTPException(status_code=503, detail="ServiceNow is not available. Please try again later.")

    # Fetch from MCP
    try:
        payload = await asyncio.wait_for(mcp_client_service.get_catalog_form(sys_id), timeout=15.0)
        if payload:
            form = CatalogFormResponse.model_validate(payload)
            neo4j_catalog_graph_service.sync_catalog_form(form)
            logger.info("[FORM] Synced to cache")
            form = await _enrich_form_with_ui_policies(form, sys_id)
            return form
    except asyncio.TimeoutError:
        logger.error("[FORM] MCP timeout")
    except Exception as e:
        logger.error(f"[FORM] MCP error: {e}")

    # Fallback to cache on any failure
    if cached_form:
        logger.warning("[FORM] Using cached form after error")
        return cached_form

    raise HTTPException(status_code=404, detail="Catalog form not found")


@router.get("/{sys_id}/form/version", response_model=dict)
async def get_catalog_form_version(sys_id: str):
    logger.info(f"[VERSION] Request for sys_id={sys_id}")
    version = neo4j_catalog_graph_service.get_catalog_form_version(sys_id)
    logger.info(f"[VERSION] Current version: {version}")

    is_fresh = False
    if version:
        is_fresh = _is_data_fresh(version)
        logger.info(f"[VERSION] Data fresh={is_fresh}")
        if is_fresh:
            logger.info(f"[VERSION] Returning fresh version")
            return version
        logger.info(f"[VERSION] Version stale, will refresh from MCP")

    if not mcp_client_service.is_available():
        logger.warning(f"[VERSION] MCP unavailable")
        if is_fresh:
            logger.info(f"[VERSION] Data fresh, returning from Neo4j")
            return version
        logger.info(f"[VERSION] Data stale and MCP unavailable - returning error to prevent stale data")
        raise HTTPException(status_code=503, detail="MCP unavailable and cached data is stale. Please retry when MCP is reconnected.")

    logger.info(f"[VERSION] Fetching fresh from MCP")
    try:
        payload = await asyncio.wait_for(mcp_client_service.get_catalog_form(sys_id), timeout=45.0)
        logger.info(f"[VERSION] MCP payload received: {payload is not None}")
        if payload:
            form = CatalogFormResponse.model_validate(payload)
            logger.info(f"[VERSION] Syncing form to Neo4j")
            neo4j_catalog_graph_service.sync_catalog_form(form)
            synced_version = neo4j_catalog_graph_service.get_catalog_form_version(sys_id)
            logger.info(f"[VERSION] Synced version: {synced_version}")
            if synced_version is not None:
                return synced_version
            return _version_from_form(form)
    except asyncio.TimeoutError:
        logger.error(f"[VERSION] MCP timeout")
        if version is not None:
            return version
        raise HTTPException(status_code=502, detail="MCP timeout")
    except Exception as e:
        logger.error(f"[VERSION] Error: {str(e)}", exc_info=True)
        if version is not None:
            return version
        raise HTTPException(status_code=502, detail=f"Error: {str(e)}")

    raise HTTPException(status_code=404, detail="Catalog item not found")


@router.get("/{sys_id}/raw-state", response_model=dict)
async def get_catalog_raw_state(sys_id: str):
    """Return raw form state directly from ServiceNow: variables with native depends_on,
    UI policies, UI policy actions, and client scripts. Useful for debugging conditional fields."""
    try:
        raw = await asyncio.to_thread(snow.get_catalog_form_raw_state, sys_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    # Summarise which variables have native depends_on set
    dep_summary = []
    for v in raw.get("variables", []):
        dep_val = v.get("depends_on")
        if dep_val:
            var_id = v.get("sys_id", {})
            dep_summary.append({
                "sys_id": var_id.get("value") if isinstance(var_id, dict) else var_id,
                "name": v.get("name"),
                "label": v.get("question_text"),
                "depends_on_raw": dep_val,
                "inactive_on_value": v.get("inactive_on_value"),
            })

    raw["depends_on_summary"] = dep_summary
    raw["ui_policy_count"] = len(raw.get("ui_policies", []))
    raw["ui_policy_action_count"] = len(raw.get("ui_policy_actions", []))
    raw["client_script_count"] = len(raw.get("client_scripts", []))
    return raw


@router.post("/{sys_id}/graph/sync", response_model=dict)
async def sync_catalog_form_to_graph(sys_id: str):
    try:
        payload = await mcp_client_service.get_catalog_form(sys_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    if not payload:
        raise HTTPException(status_code=404, detail="Catalog item not found")

    form = CatalogFormResponse.model_validate(payload)
    synced = await asyncio.to_thread(sync_catalog_form_model_to_graph, form, sys_id, True)
    if not synced:
        raise HTTPException(status_code=503, detail="Neo4j graph is not available")

    return {"synced": True, "catalog_item_id": sys_id, "variable_count": len(form.variables)}


@router.post("/clear-cache", response_model=dict)
async def clear_catalog_cache():
    if not neo4j_catalog_graph_service.is_available():
        raise HTTPException(status_code=503, detail="Neo4j graph is not available")

    if neo4j_catalog_graph_service.clear_all_forms():
        return {"cleared": True, "message": "All catalog forms cleared from Neo4j"}
    else:
        raise HTTPException(status_code=503, detail="Failed to clear catalog forms")


@router.post("/graph/clear-and-reingest", response_model=dict)
async def clear_and_reingest_catalog(
    batch_size: int = Query(25, ge=1, le=100),
    max_items: int = Query(1000, ge=1, le=5000),
):
    """Clear all catalog forms from Neo4j and re-ingest from ServiceNow."""
    if not neo4j_catalog_graph_service.is_available():
        raise HTTPException(status_code=503, detail="Neo4j graph is not available")

    logger.info(f"[CLEAR-REINGEST] Starting clear and re-ingest (max_items={max_items})")

    # Step 1: Clear existing
    if not neo4j_catalog_graph_service.clear_all_forms():
        raise HTTPException(status_code=503, detail="Failed to clear catalog forms")
    cleared_count = 0
    logger.info("[CLEAR-REINGEST] Cleared all forms")

    # Step 2: Re-ingest
    synced = 0
    failed = 0
    offset = 0

    try:
        while synced < max_items:
            if mcp_client_service.is_available():
                items = await mcp_client_service.list_catalog_items(
                    limit=min(batch_size, max_items - synced),
                    offset=offset,
                )
            else:
                items = await asyncio.to_thread(
                    snow.list_catalog_items,
                    limit=min(batch_size, max_items - synced),
                    query=None,
                    offset=offset,
                )

            if not items:
                break

            offset += len(items)

            for item in items:
                sys_id = str(item.get("sys_id", "")).strip()
                if not sys_id:
                    continue

                try:
                    if mcp_client_service.is_available():
                        form = await mcp_client_service.get_catalog_form(sys_id)
                        if form:
                            payload = {"item": form.get("item", {}), "variables": form.get("variables", [])}
                            form = CatalogFormResponse(**payload)
                    else:
                        item_payload = await asyncio.to_thread(snow.get_catalog_item, sys_id)
                        variables = await asyncio.to_thread(snow.list_catalog_item_variables, sys_id)
                        form = CatalogFormResponse(
                            item=item_payload or item,
                            variables=variables,
                        )

                    if await asyncio.to_thread(sync_catalog_form_model_to_graph, form, sys_id, True):
                        synced += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

            if len(items) < batch_size:
                break

        logger.info(
            f"[CLEAR-REINGEST] Completed: synced={synced}, failed={failed}, total=synced+failed"
        )
        return {
            "status": "ok",
            "cleared": True,
            "synced": synced,
            "failed": failed,
            "total": synced + failed,
        }
    except Exception as e:
        logger.exception(f"[CLEAR-REINGEST] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/graph/sync-all", response_model=dict)
async def sync_all_catalog_forms_to_graph(
    batch_size: int = Query(25, ge=1, le=100),
    max_items: Optional[int] = Query(None, ge=1, le=5000),
):
    if not neo4j_catalog_graph_service.is_available():
        raise HTTPException(status_code=503, detail="Neo4j graph is not available")

    synced = 0
    failed = 0
    offset = 0

    try:
        while True:
            if mcp_client_service.is_available():
                items = await mcp_client_service.list_catalog_items(limit=batch_size, offset=offset)
            else:
                items = await asyncio.to_thread(
                    snow.list_catalog_items,
                    limit=batch_size,
                    offset=offset,
                )

            if not items:
                break

            for item in items:
                sys_id = str(item.get("sys_id", "")).strip()
                if not sys_id:
                    continue

                try:
                    if mcp_client_service.is_available():
                        payload = await mcp_client_service.get_catalog_form(sys_id)
                    else:
                        payload = await asyncio.to_thread(snow.get_catalog_item, sys_id)
                        if payload:
                            variables = await asyncio.to_thread(snow.list_catalog_item_variables, sys_id)
                            payload = {"item": payload, "variables": variables}

                    if not payload:
                        failed += 1
                        continue

                    form = CatalogFormResponse.model_validate(payload)
                    if await asyncio.to_thread(sync_catalog_form_model_to_graph, form, sys_id, True):
                        synced += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

                if max_items is not None and synced + failed >= max_items:
                    return {
                        "synced": synced,
                        "failed": failed,
                        "processed": synced + failed,
                        "complete": False,
                    }

            if len(items) < batch_size:
                break
            offset += batch_size
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    return {
        "synced": synced,
        "failed": failed,
        "processed": synced + failed,
        "complete": True,
        "scope": "catalog-filter-schemas",
    }


def _is_data_fresh(version: dict) -> bool:
    from datetime import datetime, timezone, timedelta
    from app.config import settings
    if not version or not version.get("item_updated_at"):
        logger.debug(f"[FRESH] No version or updated_at")
        return False
    try:
        updated_at = version["item_updated_at"]
        logger.debug(f"[FRESH] Raw updated_at: {updated_at} (type={type(updated_at).__name__})")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_seconds = (now - updated_at).total_seconds()
        is_fresh = age_seconds < max(1, settings.catalog_form_freshness_seconds)
        logger.debug(f"[FRESH] Age={age_seconds:.1f}s, fresh={is_fresh}")
        return is_fresh
    except Exception as e:
        logger.error(f"[FRESH] Error parsing timestamp: {str(e)}")
        return False


def _version_from_form(form: CatalogFormResponse) -> Dict[str, Any]:
    return {
        "item_updated_at": None,
        "schema_key": f"mcp:{form.item.sys_id}:{len(form.variables)}",
        "schema_name": f"{form.item.name} form",
        "variable_count": len(form.variables),
        "source": "mcp",
    }


def _val(field) -> Optional[str]:
    if isinstance(field, dict):
        return field.get("display_value") or field.get("value")
    return str(field) if field else None
