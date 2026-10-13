import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import settings
from app.routers import interpret, catalog, users, tickets, conversations, comments, graphrag, schema, vectorrag
from app.routers import settings as settings_router
from app.services.catalog_polling_service import catalog_polling_service
from app.services.comment_polling_service import comment_polling_service
from app.services.mcp_client_service import mcp_client_service
from app.services.neo4j_catalog_graph import (
    neo4j_catalog_graph_service,
    platform_neo4j_catalog_graph_service,
)
from app.services.neo4j_query_runtime import (
    neo4j_query_runtime_service,
    platform_neo4j_query_runtime_service,
)
from app.services.neo4j_schema_graph import (
    neo4j_schema_graph_service,
    platform_neo4j_schema_graph_service,
)
from app.services.neo4j_ticket_graph import (
    neo4j_ticket_graph_service,
    platform_neo4j_ticket_graph_service,
)
from app.services.graphrag_v2.embedding_service import graphrag_v2_embedding_service
from app.services.graphrag_v2.diagnosis_workflow import diagnosis_workflow_service
from app.services.graphrag_v2.case_workflow import case_workflow_service
from app.services.vectorrag_v2 import vectorrag_v2_service
from app.services import catalog_startup
import logging
from fastapi import Depends
from app.auth import require_admin, require_user
from app.routers import auth as auth_router
from app.services.llm_client import llm_status

# Enable DEBUG logging when settings.debug is true to capture diagnostic traces.
logging.basicConfig(level=logging.DEBUG if settings.debug else logging.WARNING)


def _validate_required_settings() -> None:
    missing = []
    if not settings.openai_chat_configured:
        missing.append("LLM provider (OLLAMA_BASE_URL or GROQ_API_KEY)")
    if not settings.admin_password:
        missing.append("admin_password (login is disabled until ADMIN_PASSWORD is set)")
    if settings.app_secret_key in {"", "change-me-in-env", "change-this-app-secret", "dev-secret-key"}:
        missing.append("app_secret_key (using insecure default)")
    if not settings.servicenow_username:
        missing.append("servicenow_username")
    if not settings.servicenow_password or settings.servicenow_password == "admin":
        missing.append("servicenow_password (using insecure default)")
    if missing:
        logging.error(
            "Configuration check: %d setting(s) missing or insecure: %s. "
            "AI/ServiceNow features will fail at runtime.",
            len(missing),
            ", ".join(missing),
        )


def _log_neo4j_config_presence() -> None:
    for config in (settings.graphrag_neo4j_config(), settings.platform_neo4j_config()):
        logging.info(
            "%s config: enabled=%s uri_set=%s username_set=%s password_set=%s database_set=%s write_protected=%s",
            config.name,
            config.enabled,
            bool(config.uri),
            bool(config.username),
            bool(config.password),
            bool(config.database),
            config.is_write_protected,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("AI ServiceNow Orchestrator starting up...")
    _validate_required_settings()
    _log_neo4j_config_presence()
    try:
        await mcp_client_service.startup()
    except Exception:
        logging.warning("MCP client startup failed, continuing without MCP")
    platform_neo4j_catalog_graph_service.startup()
    platform_neo4j_query_runtime_service.startup()
    platform_neo4j_schema_graph_service.startup()
    platform_neo4j_ticket_graph_service.startup()

    neo4j_catalog_graph_service.startup()
    neo4j_query_runtime_service.startup()
    if settings.graphrag_schema_enabled and not settings.graphrag_minimal_graph_mode:
        neo4j_schema_graph_service.startup()
    else:
        logging.info("Skipping schema graph startup in minimal GraphRAG mode")
    neo4j_ticket_graph_service.startup()
    diagnosis_workflow_service.startup()
    case_workflow_service.startup()
    graphrag_v2_embedding_service.startup()
    if settings.graphrag_vector_enabled and settings.graphrag_embedding_backfill_enabled:
        try:
            await asyncio.to_thread(graphrag_v2_embedding_service.backfill)
        except Exception:
            logging.exception("GraphRAG vector embedding backfill failed during startup")
    vectorrag_v2_service.startup()
    await catalog_polling_service.startup()
    await comment_polling_service.startup()

    # Initialize catalog lexical index for query expansion
    logging.info("Initializing catalog lexical index for query expansion...")
    try:
        success = await catalog_startup.initialize_catalog_index_async(limit=5000)
        if success:
            logging.info("Catalog lexical index initialized successfully")
        else:
            logging.warning("Catalog lexical index initialization failed (will use fallback)")
    except Exception as e:
        logging.exception(f"Error initializing catalog lexical index: {e}")

    yield
    await catalog_polling_service.shutdown()
    await comment_polling_service.shutdown()
    platform_neo4j_catalog_graph_service.shutdown()
    platform_neo4j_query_runtime_service.shutdown()
    platform_neo4j_schema_graph_service.shutdown()
    platform_neo4j_ticket_graph_service.shutdown()
    neo4j_catalog_graph_service.shutdown()
    neo4j_query_runtime_service.shutdown()
    neo4j_schema_graph_service.shutdown()
    neo4j_ticket_graph_service.shutdown()
    diagnosis_workflow_service.shutdown()
    case_workflow_service.shutdown()
    try:
        await mcp_client_service.shutdown()
    except Exception:
        pass
    logging.info("Shutting down...")


protected = [Depends(require_user)]

app = FastAPI(
    title="AI-Orchestrated ServiceNow System",
    description="Enterprise AI-assisted Service Request & Change Management",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(auth_router.router)
app.include_router(interpret.router, dependencies=protected)
app.include_router(catalog.router, dependencies=protected)
app.include_router(users.router, dependencies=protected)
app.include_router(tickets.router, dependencies=protected)
app.include_router(comments.router, dependencies=protected)
app.include_router(conversations.router, dependencies=protected)
app.include_router(settings_router.router, dependencies=[Depends(require_admin)])
app.include_router(graphrag.router, dependencies=protected)
app.include_router(vectorrag.router, dependencies=protected)
app.include_router(schema.router, dependencies=protected)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-servicenow-orchestrator"}


@app.get("/api/llm/status", dependencies=protected)
async def llm_health():
    return llm_status()


@app.get("/api/mcp/status", dependencies=protected)
async def mcp_status():
    return await mcp_client_service.status()


@app.get("/api/catalog/graph/status", dependencies=protected)
async def catalog_graph_status():
    status = await catalog_polling_service.status()
    status["graph_counts"] = platform_neo4j_catalog_graph_service.get_graph_counts()
    status["graph_available"] = platform_neo4j_catalog_graph_service.is_available()
    graph_connection = platform_neo4j_catalog_graph_service.connection_status()
    status["graph_connection"] = graph_connection
    if not graph_connection["available"]:
        status["graph_unavailable_reason"] = graph_connection["reason"]
    return status


@app.post("/api/catalog/graph/poll-now", dependencies=[Depends(require_admin)])
async def trigger_catalog_poll():
    return await catalog_polling_service.trigger_now()


@app.get("/")
async def root():
    return {
        "service": "AI ServiceNow Orchestrator",
        "version": "1.0.0",
        "docs": "/docs",
    }
