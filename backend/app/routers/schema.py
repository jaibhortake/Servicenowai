from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services import servicenow as snow
from app.services.neo4j_schema_graph import platform_neo4j_schema_graph_service as neo4j_schema_graph_service

router = APIRouter(prefix="/api/schema", tags=["schema"])


@router.post("/graph/sync-all", response_model=dict)
async def sync_all_schemas_to_graph(
    batch_size: int = Query(25, ge=1, le=100),
    sample_rows_per_table: int = Query(1, ge=0, le=10),
    max_tables: Optional[int] = Query(None, ge=1, le=5000),
    include_system_tables: bool = Query(False),
    table_name_contains: Optional[str] = Query(None),
):
    if not neo4j_schema_graph_service.is_available():
        raise HTTPException(status_code=503, detail="Neo4j graph is not available")

    synced = 0
    failed = 0
    offset = 0

    try:
        while True:
            tables = snow.list_tables(
                limit=batch_size,
                offset=offset,
                include_system_tables=include_system_tables,
                name_contains=table_name_contains,
            )
            if not tables:
                break

            for table in tables:
                table_name = (snow._extract_value(table.get("name")) or "").strip()
                if not table_name:
                    continue

                try:
                    columns = snow.list_table_columns(table_name)
                    sample_rows = snow.list_table_rows(table_name, limit=sample_rows_per_table) if sample_rows_per_table > 0 else []
                    if neo4j_schema_graph_service.sync_table(table, columns, sample_rows):
                        synced += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

                if max_tables is not None and synced + failed >= max_tables:
                    return {
                        "synced": synced,
                        "failed": failed,
                        "processed": synced + failed,
                        "complete": False,
                    }

            if len(tables) < batch_size:
                break
            offset += batch_size
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    return {
        "synced": synced,
        "failed": failed,
        "processed": synced + failed,
        "complete": True,
    }
