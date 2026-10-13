import json
import logging
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.config import Neo4jConnectionConfig, settings

logger = logging.getLogger(__name__)


class Neo4jSchemaGraphService:
    def __init__(self, connection_config: Neo4jConnectionConfig | None = None) -> None:
        self._config = connection_config or settings.graphrag_neo4j_config()
        self._database = self._config.database
        self._driver = None

    def startup(self) -> None:
        if not self._config.enabled:
            logger.info("%s schema graph is disabled", self._config.name)
            return

        if self._driver is not None:
            return

        if not self._config.is_complete:
            logger.warning("%s schema graph settings are incomplete", self._config.name)
            return

        try:
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                connection_timeout=3.0,
                max_transaction_retry_time=2.0,
            )
            self._driver.verify_connectivity()
            if self._block_write("schema constraint creation"):
                logger.info("Connected to %s schema graph in read-only mode", self._config.name)
            else:
                self._ensure_constraints()
            logger.info("Connected to %s schema graph", self._config.name)
        except Exception:
            logger.exception("Failed to initialize %s schema graph", self._config.name)
            self.shutdown()
            self.shutdown()

    def shutdown(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def is_available(self) -> bool:
        return self._driver is not None

    def is_write_protected(self) -> bool:
        return self._config.is_write_protected

    def _block_write(self, operation: str) -> bool:
        if not self._config.is_write_protected:
            return False
        logger.warning(
            "Blocked %s on write-protected %s (%s)",
            operation,
            self._config.name,
            self._config.write_protection_reason,
        )
        return True

    def sync_table(
        self,
        table_data: Dict[str, Any],
        columns: List[Dict[str, Any]],
        sample_rows: List[Dict[str, Any]],
    ) -> bool:
        if self._block_write("schema graph sync"):
            return False
        if self._driver is None:
            return False

        payload = _build_table_payload(table_data, columns, sample_rows)
        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(self._upsert_table_graph, payload)
            return True
        except Neo4jError:
            logger.exception("Failed to sync schema for table %s", payload["table"]["name"])
            return False

    def search_tables(self, terms: List[str]) -> List[Dict[str, Any]]:
        if self._driver is None:
            return []

        normalized_terms = [term.strip().lower() for term in terms if term and term.strip()]
        if not normalized_terms:
            return []

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(self._search_tables, normalized_terms)
        except Neo4jError:
            logger.exception("Failed to search schema graph for terms %s", normalized_terms)
            return []

    def get_table_details(self, identifier: str) -> Optional[Dict[str, Any]]:
        if self._driver is None:
            return None

        normalized_identifier = str(identifier or "").strip()
        if not normalized_identifier:
            return None

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(self._read_table_details, normalized_identifier)
        except Neo4jError:
            logger.exception("Failed to read schema graph table details for %s", normalized_identifier)
            return None

    def count_tables(self, terms: List[str]) -> int:
        if self._driver is None:
            return 0

        normalized_terms = [term.strip().lower() for term in terms if term and term.strip()]
        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(self._count_tables, normalized_terms)
        except Neo4jError:
            logger.exception("Failed to count schema tables for terms %s", normalized_terms)
            return 0

    def _ensure_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT table_sys_id IF NOT EXISTS FOR (n:Table) REQUIRE n.sys_id IS UNIQUE",
            "CREATE CONSTRAINT column_key IF NOT EXISTS FOR (n:Column) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT sample_record_key IF NOT EXISTS FOR (n:SampleRecord) REQUIRE n.key IS UNIQUE",
        ]
        with self._driver.session(database=self._database) as session:
            for statement in statements:
                session.run(statement).consume()

    @staticmethod
    def _upsert_table_graph(tx, payload: Dict[str, Any]) -> None:
        table = payload["table"]
        tx.run(
            """
            MERGE (root:Platform {name: 'ServiceNow'})
            MERGE (table:Table {sys_id: $sys_id})
            SET table.name = $name,
                table.label = $label,
                table.scope = $scope,
                table.provider_class = $provider_class,
                table.updated_at = datetime()
            MERGE (root)-[:HAS_TABLE]->(table)
            """,
            **table,
        ).consume()

        tx.run(
            """
            MATCH (table:Table {sys_id: $sys_id})-[rel:EXTENDS]->(:Table)
            DELETE rel
            """,
            sys_id=table["sys_id"],
        ).consume()

        if table.get("super_class_sys_id"):
            tx.run(
                """
                MATCH (table:Table {sys_id: $sys_id})
                MERGE (parent:Table {sys_id: $super_class_sys_id})
                MERGE (table)-[:EXTENDS]->(parent)
                """,
                sys_id=table["sys_id"],
                super_class_sys_id=table["super_class_sys_id"],
            ).consume()

        tx.run(
            """
            MATCH (table:Table {sys_id: $sys_id})-[:HAS_COLUMN]->(column:Column)
            DETACH DELETE column
            """,
            sys_id=table["sys_id"],
        ).consume()

        tx.run(
            """
            MATCH (table:Table {sys_id: $sys_id})-[:HAS_SAMPLE_RECORD]->(record:SampleRecord)
            DETACH DELETE record
            """,
            sys_id=table["sys_id"],
        ).consume()

        for column in payload["columns"]:
            tx.run(
                """
                MATCH (table:Table {sys_id: $table_sys_id})
                MERGE (column:Column {key: $key})
                SET column.name = $name,
                    column.label = $label,
                    column.type = $type,
                    column.max_length = $max_length,
                    column.mandatory = $mandatory,
                    column.default_value = $default_value,
                    column.reference = $reference
                MERGE (table)-[:HAS_COLUMN]->(column)
                """,
                table_sys_id=table["sys_id"],
                **column,
            ).consume()

            if column.get("reference"):
                tx.run(
                    """
                    MATCH (column:Column {key: $key})
                    MERGE (target:Table {name: $reference})
                    MERGE (column)-[:REFERENCES]->(target)
                    """,
                    key=column["key"],
                    reference=column["reference"],
                ).consume()

            for sample_record in payload["sample_rows"]:
                tx.run(
                    """
                    MATCH (table:Table {sys_id: $table_sys_id})
                    MERGE (record:SampleRecord {key: $key})
                SET record.sys_id = $sys_id,
                    record.display_value = $display_value,
                    record.preview_json = $preview_json,
                    record.table_name = $table_name
                SET record += $properties
                MERGE (table)-[:HAS_SAMPLE_RECORD]->(record)
                """,
                table_sys_id=table["sys_id"],
                **sample_record,
            ).consume()

    @staticmethod
    def _search_tables(tx, terms: List[str]) -> List[Dict[str, Any]]:
        result = tx.run(
            """
            MATCH (table:Table)
            OPTIONAL MATCH (table)-[:HAS_COLUMN]->(column:Column)
            WITH table, column,
                 [term IN $terms WHERE
                    toLower(coalesce(table.name, '')) CONTAINS term OR
                    toLower(coalesce(table.label, '')) CONTAINS term OR
                    toLower(coalesce(column.name, '')) CONTAINS term OR
                    toLower(coalesce(column.label, '')) CONTAINS term
                 ] AS matched_terms
            WHERE size(matched_terms) > 0
            WITH table,
                 collect(DISTINCT column.name) AS matched_columns,
                 count(DISTINCT matched_terms) AS raw_match_count,
                 reduce(flat = [], entry IN collect(matched_terms) | flat + entry) AS flattened_terms
            RETURN table.sys_id AS sys_id,
                   table.name AS name,
                   table.label AS label,
                   table.scope AS scope,
                   table.provider_class AS provider_class,
                   size([term IN flattened_terms WHERE term IS NOT NULL]) AS term_match_count,
                   [column_name IN matched_columns WHERE column_name IS NOT NULL][0..10] AS matched_columns
            ORDER BY term_match_count DESC, coalesce(table.label, table.name) ASC
            LIMIT 25
            """,
            terms=terms,
        )
        return [dict(record) for record in result]

    @staticmethod
    def _read_table_details(tx, identifier: str) -> Optional[Dict[str, Any]]:
        result = tx.run(
            """
            MATCH (table:Table)
            WHERE table.sys_id = $identifier
               OR toLower(coalesce(table.name, '')) = toLower($identifier)
               OR toLower(coalesce(table.label, '')) = toLower($identifier)
            OPTIONAL MATCH (table)-[:HAS_COLUMN]->(column:Column)
            WITH table, column
            ORDER BY coalesce(column.label, column.name) ASC
            RETURN table {
                .sys_id,
                .name,
                .label,
                .scope,
                .provider_class
            } AS table,
            collect(
                CASE
                    WHEN column IS NULL THEN NULL
                    ELSE column {
                        .key,
                        .name,
                        .label,
                        .type,
                        .max_length,
                        .mandatory,
                        .default_value,
                        .reference
                    }
                END
            ) AS columns
            LIMIT 1
            """,
            identifier=identifier,
        )
        record = result.single()
        if not record:
            return None
        return {
            "table": record["table"],
            "columns": [column for column in record["columns"] if column is not None],
        }

    @staticmethod
    def _count_tables(tx, terms: List[str]) -> int:
        if not terms:
            result = tx.run("MATCH (table:Table) RETURN count(table) AS total")
            record = result.single()
            return int(record["total"]) if record else 0

        result = tx.run(
            """
            MATCH (table:Table)
            OPTIONAL MATCH (table)-[:HAS_COLUMN]->(column:Column)
            WITH table, column,
                 [term IN $terms WHERE
                    toLower(coalesce(table.name, '')) CONTAINS term OR
                    toLower(coalesce(table.label, '')) CONTAINS term OR
                    toLower(coalesce(column.name, '')) CONTAINS term OR
                    toLower(coalesce(column.label, '')) CONTAINS term
                 ] AS matched_terms
            WHERE size(matched_terms) > 0
            RETURN count(DISTINCT table) AS total
            """,
            terms=terms,
        )
        record = result.single()
        return int(record["total"]) if record else 0


def _build_table_payload(
    table_data: Dict[str, Any],
    columns: List[Dict[str, Any]],
    sample_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    table_sys_id = _string_value(table_data.get("sys_id")) or ""
    table_name = _string_value(table_data.get("name")) or ""
    return {
        "table": {
            "sys_id": table_sys_id,
            "name": table_name,
            "label": _string_value(table_data.get("label")) or table_name,
            "scope": _display_or_value(table_data.get("sys_scope")),
            "provider_class": _string_value(table_data.get("provider_class")),
            "super_class_sys_id": _value_only(table_data.get("super_class")),
        },
        "columns": [
            {
                "key": f"{table_sys_id}:{_string_value(column.get('element'))}",
                "name": _string_value(column.get("element")),
                "label": _string_value(column.get("column_label")) or _string_value(column.get("element")),
                "type": _display_or_value(column.get("internal_type")) or "string",
                "max_length": _string_value(column.get("max_length")),
                "mandatory": _bool_value(column.get("mandatory")),
                "default_value": _string_value(column.get("default_value")),
                "reference": _display_or_value(column.get("reference")),
            }
            for column in columns
            if _string_value(column.get("element"))
        ],
        "sample_rows": [
            {
                "key": f"{table_name}:{_string_value(record.get('sys_id')) or index}",
                "sys_id": _string_value(record.get("sys_id")) or f"sample-{index}",
                "display_value": _derive_display_value(record),
                "preview_json": json.dumps(_row_preview(record), ensure_ascii=True),
                "table_name": table_name,
                "properties": _row_properties(record),
            }
            for index, record in enumerate(sample_rows)
        ],
    }


def _derive_display_value(record: Dict[str, Any]) -> str:
    preferred = ["name", "number", "model", "short_description", "display_name", "sys_id"]
    for key in preferred:
        value = _display_or_value(record.get(key))
        if value:
            return value
    for value in record.values():
        normalized = _display_or_value(value)
        if normalized:
            return normalized
    return "Record"


def _row_preview(record: Dict[str, Any], limit: int = 8) -> Dict[str, str]:
    preview: Dict[str, str] = {}
    for key, value in record.items():
        if len(preview) >= limit:
            break
        normalized = _display_or_value(value)
        if normalized:
            preview[key] = normalized
    return preview


def _row_properties(record: Dict[str, Any], limit: int = 12) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    for key, value in record.items():
        if len(properties) >= limit:
            break
        normalized = _display_or_value(value)
        if normalized is None:
            continue
        properties[key] = normalized
    return properties


def _string_value(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return str(value)


def _display_or_value(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    if value in (None, ""):
        return None
    return str(value)


def _value_only(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("value")
    if value in (None, ""):
        return None
    return str(value)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


neo4j_schema_graph_service = Neo4jSchemaGraphService(settings.graphrag_neo4j_config())
platform_neo4j_schema_graph_service = Neo4jSchemaGraphService(settings.platform_neo4j_config())
