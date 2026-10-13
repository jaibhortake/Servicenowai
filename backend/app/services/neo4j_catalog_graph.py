import logging
import hashlib
import json
from typing import Any, Dict, Optional
import re

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.config import Neo4jConnectionConfig, settings
from app.models.catalog import (
    CatalogFormResponse,
    CatalogItem,
    CatalogVariable,
    CatalogVariableChoice,
)

logger = logging.getLogger(__name__)


class Neo4jCatalogGraphService:
    def __init__(self, connection_config: Neo4jConnectionConfig | None = None) -> None:
        self._config = connection_config or settings.graphrag_neo4j_config()
        self._database = self._config.database
        self._driver = None

    def startup(self) -> None:
        if not self._config.enabled:
            logger.info("%s catalog graph is disabled", self._config.name)
            return

        if self._driver is not None:
            return

        if not self._config.is_complete:
            logger.warning("%s catalog graph settings are incomplete", self._config.name)
            logger.warning(
                "  URI: %s, Username: %s, Password: %s",
                bool(self._config.uri),
                bool(self._config.username),
                bool(self._config.password),
            )
            return

        logger.info("Connecting %s catalog graph to Neo4j", self._config.name)
        try:
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                connection_timeout=3.0,
                max_transaction_retry_time=2.0,
            )
            self._driver.verify_connectivity()
            if self._block_write("catalog constraint creation"):
                logger.info("Connected to %s catalog graph in read-only mode", self._config.name)
            else:
                self._ensure_constraints()
            logger.info("✓ Connected to Neo4j catalog graph")
        except Exception:
            logger.exception("✗ Failed to initialize Neo4j catalog graph")
            self.shutdown()

    def shutdown(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def is_available(self) -> bool:
        return self._driver is not None

    def connection_status(self) -> Dict[str, Any]:
        reason = None
        if not self._config.enabled:
            reason = f"{self._config.name} is disabled"
        elif not self._config.is_complete:
            reason = f"{self._config.name} credentials are incomplete"
        elif self._driver is None:
            reason = f"{self._config.name} is not connected"

        return {
            "name": self._config.name,
            "available": self.is_available(),
            "enabled": self._config.enabled,
            "configured": self._config.is_complete,
            "uri_set": bool(self._config.uri),
            "username_set": bool(self._config.username),
            "password_set": bool(self._config.password),
            "database_set": bool(self._config.database),
            "write_protected": self._config.is_write_protected,
            "reason": reason,
        }

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

    def sync_catalog_form(self, form: CatalogFormResponse) -> bool:
        if self._block_write("catalog form sync"):
            return False
        if self._driver is None:
            return False

        payload = _serialize_form(form)
        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(self._upsert_catalog_form, payload)
            return True
        except Neo4jError:
            logger.exception("Failed to sync catalog form %s to Neo4j", form.item.sys_id)
            return False

    def get_catalog_form(self, sys_id: str) -> Optional[CatalogFormResponse]:
        if self._driver is None:
            return None

        try:
            with self._driver.session(database=self._database) as session:
                record = session.execute_read(self._read_catalog_form, sys_id)
        except Neo4jError:
            logger.exception("Failed to read catalog form %s from Neo4j", sys_id)
            return None

        if not record:
            return None
        return _deserialize_form(record)

    def get_catalog_item_context(self, sys_id: str) -> Optional[Dict[str, Any]]:
        if self._driver is None:
            return None

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(self._read_catalog_context, sys_id)
        except Neo4jError:
            logger.exception("Failed to read catalog context %s from Neo4j", sys_id)
            return None

    def get_catalog_form_version(self, sys_id: str) -> Optional[Dict[str, Any]]:
        if self._driver is None:
            return None

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(self._read_catalog_form_version, sys_id)
        except Neo4jError:
            logger.exception("Failed to read catalog form version %s from Neo4j", sys_id)
            return None

    def clear_all_forms(self) -> bool:
        if self._block_write("catalog graph clear"):
            return False
        if self._driver is None:
            return False

        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(self._delete_all_forms)
            logger.info("Cleared all catalog forms from Neo4j")
            return True
        except Neo4jError:
            logger.exception("Failed to clear catalog forms from Neo4j")
            return False

    def get_graph_counts(self) -> Dict[str, int]:
        if self._driver is None:
            return {
                "catalog_items": 0,
                "filter_schemas": 0,
                "catalog_variables": 0,
                "catalog_choices": 0,
                "relationships": 0,
            }

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(self._read_graph_counts)
        except Neo4jError:
            logger.exception("Failed to read catalog graph counts from Neo4j")
            return {
                "catalog_items": 0,
                "filter_schemas": 0,
                "catalog_variables": 0,
                "catalog_choices": 0,
                "relationships": 0,
            }

    def search_catalog_semantic(self, query: str, keywords: list[str] = None) -> list[Dict[str, Any]]:
        if self._driver is None:
            return []

        phrase_terms: list[str] = []
        if query and query.strip():
            phrase_terms.append(query.strip().lower())
        for keyword in keywords or []:
            normalized = str(keyword or "").strip().lower()
            if normalized:
                phrase_terms.append(normalized)

        token_terms: list[str] = []
        for value in phrase_terms:
            token_terms.extend(_tokenize(value))

        all_terms = _dedupe_preserve_order(token_terms)
        phrase_terms = _dedupe_preserve_order(
            [term for term in phrase_terms if len(term) >= 3 and term not in _STOP_WORDS]
        )
        if not all_terms and not phrase_terms:
            return []

        try:
            with self._driver.session(database=self._database) as session:
                results = session.execute_read(self._search_catalog_items, all_terms, phrase_terms)
            logger.info(
                "Neo4j semantic search for tokens=%s phrases=%s returned %d items",
                all_terms,
                phrase_terms,
                len(results),
            )
            return results
        except Neo4jError:
            logger.exception("Failed to search catalog in Neo4j")
            return []

    def list_catalog_items(self, limit: int = 200, priced_only: bool = False) -> list[Dict[str, Any]]:
        if self._driver is None:
            return []

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(self._list_catalog_items, max(1, limit), priced_only)
        except Neo4jError:
            logger.exception("Failed to list catalog items from Neo4j")
            return []

    def count_catalog_items(self, keywords: list[str] = None) -> int:
        if self._driver is None:
            return 0

        normalized_terms = _dedupe_preserve_order(
            [
                normalized
                for normalized in (
                    str(value or "").strip().lower()
                    for value in (keywords or [])
                )
                if normalized
            ]
        )

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(self._count_catalog_items, normalized_terms)
        except Neo4jError:
            logger.exception("Failed to count catalog items in Neo4j")
            return 0

    def suggest_search_keywords(self, query: str, keywords: list[str] = None, limit: int = 8) -> list[str]:
        if self._driver is None:
            return []

        seeds = [query] + (keywords or [])
        seed_tokens: list[str] = []
        for seed in seeds:
            seed_tokens.extend(_tokenize(seed))
        if not seed_tokens:
            return []

        try:
            with self._driver.session(database=self._database) as session:
                rows = session.execute_read(self._collect_keyword_hint_rows, seed_tokens)
        except Neo4jError:
            logger.exception("Failed to collect keyword hints from Neo4j catalog graph")
            return []

        if not rows:
            return []

        frequencies: Dict[str, int] = {}
        for row in rows:
            for field in ("item_name", "short_description", "category", "variable_label", "variable_name"):
                text = row.get(field)
                for token in _tokenize(text):
                    if token in seed_tokens:
                        continue
                    frequencies[token] = frequencies.get(token, 0) + 1

        ranked = sorted(frequencies.items(), key=lambda item: item[1], reverse=True)
        return [token for token, _ in ranked[: max(1, limit)]]

    def _ensure_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT catalog_item_sys_id IF NOT EXISTS FOR (n:CatalogItem) REQUIRE n.sys_id IS UNIQUE",
            "CREATE CONSTRAINT filter_schema_key IF NOT EXISTS FOR (n:FilterSchema) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT catalog_variable_key IF NOT EXISTS FOR (n:CatalogVariable) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT catalog_choice_key IF NOT EXISTS FOR (n:CatalogChoice) REQUIRE n.key IS UNIQUE",
        ]
        with self._driver.session(database=self._database) as session:
            for statement in statements:
                session.run(statement).consume()

    @staticmethod
    def _search_catalog_items(tx, terms: list[str], phrase_terms: list[str]) -> list[Dict[str, Any]]:
        query = """
            MATCH (item:CatalogItem)
            OPTIONAL MATCH (item)-[:USES_SCHEMA]->(:FilterSchema)-[:HAS_FIELD]->(variable:CatalogVariable)
            OPTIONAL MATCH (variable)-[:HAS_CHOICE]->(choice:CatalogChoice)
            WITH item,
                 collect(DISTINCT variable) AS variables,
                 collect(DISTINCT choice) AS choices
            WITH item,
                 variables,
                 choices,
                 reduce(score = 0, term IN $terms |
                    score +
                    CASE WHEN toLower(coalesce(item.name, '')) = term THEN 40 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.name, '')) CONTAINS term THEN 16 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.short_description, '')) CONTAINS term THEN 8 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.description, '')) CONTAINS term THEN 6 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.category, '')) CONTAINS term THEN 5 ELSE 0 END +
                    CASE WHEN ANY(variable IN variables WHERE
                        toLower(coalesce(variable.label, '')) CONTAINS term OR
                        toLower(coalesce(variable.name, '')) CONTAINS term
                    ) THEN 8 ELSE 0 END +
                    CASE WHEN ANY(choice IN choices WHERE
                        toLower(coalesce(choice.label, '')) CONTAINS term OR
                        toLower(coalesce(choice.value, '')) CONTAINS term
                    ) THEN 6 ELSE 0 END
                 ) AS token_score,
                 reduce(score = 0, phrase IN $phrase_terms |
                    score +
                    CASE WHEN toLower(coalesce(item.name, '')) = phrase THEN 50 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.name, '')) CONTAINS phrase THEN 18 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.short_description, '')) CONTAINS phrase THEN 10 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.description, '')) CONTAINS phrase THEN 8 ELSE 0 END +
                    CASE WHEN ANY(variable IN variables WHERE
                        toLower(coalesce(variable.label, '')) CONTAINS phrase OR
                        toLower(coalesce(variable.name, '')) CONTAINS phrase
                    ) THEN 10 ELSE 0 END +
                    CASE WHEN ANY(choice IN choices WHERE
                        toLower(coalesce(choice.label, '')) CONTAINS phrase OR
                        toLower(coalesce(choice.value, '')) CONTAINS phrase
                    ) THEN 8 ELSE 0 END
                 ) AS phrase_score,
                 [term IN $terms WHERE
                    toLower(coalesce(item.name, '')) CONTAINS term OR
                    toLower(coalesce(item.short_description, '')) CONTAINS term OR
                    toLower(coalesce(item.description, '')) CONTAINS term OR
                    toLower(coalesce(item.category, '')) CONTAINS term OR
                    ANY(variable IN variables WHERE
                        toLower(coalesce(variable.label, '')) CONTAINS term OR
                        toLower(coalesce(variable.name, '')) CONTAINS term
                    ) OR
                    ANY(choice IN choices WHERE
                        toLower(coalesce(choice.label, '')) CONTAINS term OR
                        toLower(coalesce(choice.value, '')) CONTAINS term
                    )
                 ] AS matched_terms
            WITH item, token_score, phrase_score, matched_terms
            WHERE token_score + phrase_score > 0
            RETURN item.sys_id as sys_id,
                   item.name as name,
                   item.short_description as short_description,
                   item.description as description,
                   item.category as category,
                   item.icon as icon,
                   item.price as price,
                   token_score + phrase_score AS score,
                   matched_terms[0..12] AS matched_terms
            ORDER BY score DESC, size(matched_terms) DESC, coalesce(item.name, '') ASC
            LIMIT 100
        """
        result = tx.run(query, terms=terms, phrase_terms=phrase_terms)
        records = [dict(record) for record in result]
        return records

    @staticmethod
    def _collect_keyword_hint_rows(tx, terms: list[str]) -> list[Dict[str, Any]]:
        query = """
            MATCH (item:CatalogItem)
            OPTIONAL MATCH (item)-[:USES_SCHEMA]->(:FilterSchema)-[:HAS_FIELD]->(variable:CatalogVariable)
            WHERE ANY(term IN $terms WHERE
                toLower(coalesce(item.name, '')) CONTAINS term OR
                toLower(coalesce(item.short_description, '')) CONTAINS term OR
                toLower(coalesce(item.description, '')) CONTAINS term OR
                toLower(coalesce(item.category, '')) CONTAINS term OR
                toLower(coalesce(variable.label, '')) CONTAINS term OR
                toLower(coalesce(variable.name, '')) CONTAINS term)
            RETURN
                item.name AS item_name,
                item.short_description AS short_description,
                item.category AS category,
                variable.label AS variable_label,
                variable.name AS variable_name
            LIMIT 300
        """
        result = tx.run(query, terms=terms)
        return [dict(record) for record in result]

    @staticmethod
    def _list_catalog_items(tx, limit: int, priced_only: bool) -> list[Dict[str, Any]]:
        query = """
            MATCH (item:CatalogItem)
            OPTIONAL MATCH (item)-[:USES_SCHEMA]->(schema:FilterSchema)
            OPTIONAL MATCH (schema)-[:HAS_FIELD]->(variable:CatalogVariable)
            WITH item, count(DISTINCT variable) AS variable_count
            WHERE NOT $priced_only
               OR (
                    item.price IS NOT NULL AND
                    trim(toString(item.price)) <> '' AND
                    toLower(trim(toString(item.price))) <> '0'
               )
            RETURN item.sys_id AS sys_id,
                   item.name AS name,
                   item.short_description AS short_description,
                   item.description AS description,
                   item.category AS category,
                   item.icon AS icon,
                   item.price AS price,
                   variable_count AS variable_count
            ORDER BY coalesce(item.name, '') ASC
            LIMIT $limit
        """
        result = tx.run(query, limit=limit, priced_only=priced_only)
        return [dict(record) for record in result]

    @staticmethod
    def _count_catalog_items(tx, terms: list[str]) -> int:
        if not terms:
            result = tx.run("MATCH (item:CatalogItem) RETURN count(item) AS total")
            record = result.single()
            return int(record["total"]) if record else 0

        query = """
            MATCH (item:CatalogItem)
            OPTIONAL MATCH (item)-[:USES_SCHEMA]->(:FilterSchema)-[:HAS_FIELD]->(variable:CatalogVariable)
            OPTIONAL MATCH (variable)-[:HAS_CHOICE]->(choice:CatalogChoice)
            WITH item, collect(DISTINCT variable) AS variables, collect(DISTINCT choice) AS choices
            WITH item,
                 [term IN $terms WHERE
                    toLower(coalesce(item.name, '')) CONTAINS term OR
                    toLower(coalesce(item.short_description, '')) CONTAINS term OR
                    toLower(coalesce(item.description, '')) CONTAINS term OR
                    toLower(coalesce(item.category, '')) CONTAINS term OR
                    ANY(variable IN variables WHERE
                        toLower(coalesce(variable.label, '')) CONTAINS term OR
                        toLower(coalesce(variable.name, '')) CONTAINS term
                    ) OR
                    ANY(choice IN choices WHERE
                        toLower(coalesce(choice.label, '')) CONTAINS term OR
                        toLower(coalesce(choice.value, '')) CONTAINS term
                    )
                 ] AS matched_terms
            WHERE size(matched_terms) > 0
            RETURN count(item) AS total
        """
        result = tx.run(query, terms=terms)
        record = result.single()
        return int(record["total"]) if record else 0

    @staticmethod
    def _read_graph_counts(tx) -> Dict[str, int]:
        query = """
            CALL () {
                MATCH (n:CatalogItem)
                RETURN count(n) AS catalog_items
            }
            CALL () {
                MATCH (n:FilterSchema)
                RETURN count(n) AS filter_schemas
            }
            CALL () {
                MATCH (n:CatalogVariable)
                RETURN count(n) AS catalog_variables
            }
            CALL () {
                MATCH (n:CatalogChoice)
                RETURN count(n) AS catalog_choices
            }
            CALL () {
                MATCH ()-[r]->()
                RETURN count(r) AS relationships
            }
            RETURN catalog_items, filter_schemas, catalog_variables, catalog_choices, relationships
        """
        record = tx.run(query).single()
        if not record:
            return {
                "catalog_items": 0,
                "filter_schemas": 0,
                "catalog_variables": 0,
                "catalog_choices": 0,
                "relationships": 0,
            }
        return {
            "catalog_items": int(record["catalog_items"] or 0),
            "filter_schemas": int(record["filter_schemas"] or 0),
            "catalog_variables": int(record["catalog_variables"] or 0),
            "catalog_choices": int(record["catalog_choices"] or 0),
            "relationships": int(record["relationships"] or 0),
        }

    @staticmethod
    def _upsert_catalog_form(tx, payload: Dict[str, Any]) -> None:
        item = payload["item"]
        schema = payload["schema"]
        tx.run(
            """
            MERGE (root:Platform {name: 'ServiceNow'})
            MERGE (item:CatalogItem {sys_id: $sys_id})
            SET item.name = $name,
                item.display_name = coalesce($display_name, $name),
                item.short_description = $short_description,
                item.description = $description,
                item.category = $category,
                item.category_sys_id = $category_sys_id,
                item.category_name = coalesce($category_name, $category),
                item.icon = $icon,
                item.price = $price,
                item.search_text = $search_text,
                item.form_schema_key = $schema_key,
                item.form_schema_name = $schema_name,
                item.form_field_count = $schema_field_count,
                item.updated_at = datetime()
            MERGE (root)-[:HAS_CATALOG_ITEM]->(item)
            """,
            schema_key=schema["key"],
            schema_name=schema["name"],
            schema_field_count=schema["field_count"],
            **item,
        ).consume()

        tx.run(
            """
            MATCH (item:CatalogItem {sys_id: $sys_id})-[rel:USES_SCHEMA]->(:FilterSchema)
            DELETE rel
            """,
            sys_id=item["sys_id"],
        ).consume()

        tx.run(
            """
            MERGE (schema:FilterSchema {key: $key})
            SET schema.name = $name,
                schema.field_count = $field_count,
                schema.signature = $signature,
                schema.updated_at = datetime()
            """,
            **schema,
        ).consume()

        tx.run(
            """
            MATCH (item:CatalogItem {sys_id: $item_sys_id})
            MATCH (schema:FilterSchema {key: $schema_key})
            MERGE (item)-[:USES_SCHEMA]->(schema)
            """,
            item_sys_id=item["sys_id"],
            schema_key=schema["key"],
        ).consume()

        tx.run(
            """
            MATCH (schema:FilterSchema {key: $schema_key})-[rel:HAS_FIELD]->(variable:CatalogVariable)
            DETACH DELETE rel, variable
            """,
            schema_key=schema["key"],
        ).consume()

        # First pass: Create all variable nodes
        for variable in payload["variables"]:
            tx.run(
                """
                MATCH (schema:FilterSchema {key: $schema_key})
                MERGE (variable:CatalogVariable {key: $key})
                SET variable.name = $name,
                    variable.sys_id = $sys_id,
                    variable.label = $label,
                    variable.type = $type,
                    variable.mandatory = $mandatory,
                    variable.default_value = $default_value,
                    variable.depends_on = $depends_on,
                    variable.inactive_on_value = $inactive_on_value,
                    variable.active_on_value = $active_on_value,
                    variable.sort_order = $sort_order
                MERGE (schema)-[:HAS_FIELD]->(variable)
                """,
                schema_key=schema["key"],
                **variable,
            ).consume()

            for choice in variable["choices"]:
                tx.run(
                    """
                    MATCH (variable:CatalogVariable {key: $variable_key})
                    MERGE (choice:CatalogChoice {key: $key})
                    SET choice.label = $label,
                        choice.value = $value,
                        choice.sort_order = $sort_order
                    MERGE (variable)-[:HAS_CHOICE]->(choice)
                    """,
                    variable_key=variable["key"],
                    **choice,
                ).consume()

        # Second pass: Create DEPENDS_ON relationships between variables
        for variable in payload["variables"]:
            if variable.get("depends_on"):
                tx.run(
                    """
                    MATCH (child:CatalogVariable {key: $child_key})
                    MATCH (parent:CatalogVariable {sys_id: $parent_sys_id})
                    MERGE (child)-[r:DEPENDS_ON]->(parent)
                    SET r.active_on_value = $active_on_value
                    """,
                    child_key=variable["key"],
                    parent_sys_id=variable["depends_on"],
                    active_on_value=variable.get("active_on_value"),
                ).consume()

    @staticmethod
    def _read_catalog_form(tx, sys_id: str) -> Optional[Dict[str, Any]]:
        result = tx.run(
            """
            MATCH (item:CatalogItem {sys_id: $sys_id})
            OPTIONAL MATCH (item)-[:USES_SCHEMA]->(schema:FilterSchema)
            OPTIONAL MATCH (schema)-[:HAS_FIELD]->(variable:CatalogVariable)
            OPTIONAL MATCH (variable)-[:HAS_CHOICE]->(choice:CatalogChoice)
            WITH item, schema, variable, choice
            ORDER BY variable.sort_order ASC, choice.sort_order ASC
            WITH item, schema, variable, collect(choice) AS raw_choices
            ORDER BY variable.sort_order ASC
            RETURN item {
                .sys_id,
                .name,
                .short_description,
                .description,
                .category,
                .icon,
                .price
            } AS item,
            schema {
                .key,
                .name,
                .field_count
            } AS schema,
            collect(
                CASE
                    WHEN variable IS NULL THEN NULL
                    ELSE variable {
                        .key,
                        .sys_id,
                        .name,
                        .label,
                        .type,
                        .mandatory,
                        .default_value,
                        .depends_on,
                        .inactive_on_value,
                        .active_on_value,
                        .sort_order,
                        choices: [choice IN raw_choices WHERE choice IS NOT NULL | choice {
                            .label,
                            .value,
                            .sort_order
                        }]
                    }
                END
            ) AS variables
            """,
            sys_id=sys_id,
        )
        record = result.single()
        if not record:
            return None
        return {
            "item": record["item"],
            "schema": record["schema"],
            "variables": [variable for variable in record["variables"] if variable is not None],
        }

    @staticmethod
    def _read_catalog_context(tx, sys_id: str) -> Optional[Dict[str, Any]]:
        result = tx.run(
            """
            MATCH (item:CatalogItem {sys_id: $sys_id})
            OPTIONAL MATCH (item)-[:USES_SCHEMA]->(schema:FilterSchema)
            OPTIONAL MATCH (schema)-[:HAS_FIELD]->(variable:CatalogVariable)
            OPTIONAL MATCH (variable)-[:HAS_CHOICE]->(choice:CatalogChoice)
            WITH item, schema, variable, choice
            ORDER BY variable.sort_order ASC, choice.sort_order ASC
            WITH item, schema, variable, collect(choice) AS raw_choices
            ORDER BY variable.sort_order ASC
            RETURN item {
                .sys_id,
                .name,
                .short_description,
                .description,
                .category,
                .icon,
                .price,
                .form_schema_key,
                .form_schema_name,
                .form_field_count
            } AS item,
            schema {
                .key,
                .name,
                .field_count,
                .signature
            } AS schema,
            collect(
                CASE
                    WHEN variable IS NULL THEN NULL
                    ELSE variable {
                        .key,
                        .sys_id,
                        .name,
                        .label,
                        .type,
                        .mandatory,
                        .default_value,
                        .depends_on,
                        .inactive_on_value,
                        .active_on_value,
                        .sort_order,
                        choices: [choice IN raw_choices WHERE choice IS NOT NULL | choice {
                            .label,
                            .value,
                            .sort_order
                        }]
                    }
                END
            ) AS variables
            """,
            sys_id=sys_id,
        )
        record = result.single()
        if not record:
            return None
        return {
            "item": record["item"],
            "schema": record["schema"],
            "variables": [variable for variable in record["variables"] if variable is not None],
        }

    @staticmethod
    def _delete_all_forms(tx) -> None:
        tx.run("MATCH (n:CatalogItem) DETACH DELETE n").consume()
        tx.run("MATCH (n:CatalogVariable) DETACH DELETE n").consume()
        tx.run("MATCH (n:CatalogChoice) DETACH DELETE n").consume()
        tx.run("MATCH (n:FilterSchema) DETACH DELETE n").consume()

    @staticmethod
    def _read_catalog_form_version(tx, sys_id: str) -> Optional[Dict[str, Any]]:
        result = tx.run(
            """
            MATCH (item:CatalogItem {sys_id: $sys_id})
            OPTIONAL MATCH (item)-[:USES_SCHEMA]->(schema:FilterSchema)
            OPTIONAL MATCH (schema)-[:HAS_FIELD]->(variable:CatalogVariable)
            RETURN item.sys_id AS sys_id,
                   toString(item.updated_at) AS item_updated_at,
                   coalesce(schema.key, '') AS schema_key,
                   count(variable) AS variable_count
            """,
            sys_id=sys_id,
        )
        record = result.single()
        if not record:
            return None
        return {
            "sys_id": record["sys_id"],
            "item_updated_at": record["item_updated_at"],
            "schema_key": record["schema_key"],
            "variable_count": record["variable_count"],
        }


def _serialize_form(form: CatalogFormResponse) -> Dict[str, Any]:
    schema_signature = _schema_signature(form)
    schema_key = _schema_key(form, schema_signature)
    return {
        "item": {
            "sys_id": form.item.sys_id,
            "name": form.item.name,
            "display_name": form.item.display_name or form.item.name,
            "short_description": form.item.short_description,
            "description": form.item.description,
            "category": form.item.category_name or form.item.category,
            "category_sys_id": form.item.category_sys_id,
            "category_name": form.item.category_name or form.item.category,
            "icon": form.item.icon,
            "price": form.item.price,
            "search_text": _catalog_search_text(form),
        },
        "schema": {
            "key": schema_key,
            "name": _schema_name(form),
            "field_count": len(form.variables),
            "signature": schema_signature,
        },
        "variables": [
            {
                "key": f"{schema_key}:{variable.sys_id or variable.name}:{index}",
                "sys_id": variable.sys_id,
                "name": variable.name,
                "label": variable.label,
                "type": variable.type,
                "mandatory": variable.mandatory,
                "default_value": variable.default_value,
                "depends_on": variable.depends_on,
                "inactive_on_value": variable.inactive_on_value,
                "active_on_value": variable.active_on_value,
                "sort_order": index,
                "choices": [
                    {
                        "key": f"{schema_key}:{variable.sys_id or variable.name}:{choice.value or choice.label}:{choice_index}",
                        "label": choice.label,
                        "value": choice.value,
                        "sort_order": choice_index,
                    }
                    for choice_index, choice in enumerate(variable.choices)
                ],
            }
            for index, variable in enumerate(form.variables)
        ],
    }


def _deserialize_form(payload: Dict[str, Any]) -> CatalogFormResponse:
    item = CatalogItem(**payload["item"])
    variables = [
        CatalogVariable(
            sys_id=str(variable.get("sys_id", "")),
            name=str(variable.get("name") or variable.get("sys_id") or ""),
            label=str(variable.get("label") or variable.get("name") or "Field"),
            type=str(variable.get("type") or "text"),
            mandatory=bool(variable.get("mandatory")),
            default_value=variable.get("default_value"),
            depends_on=variable.get("depends_on"),
            inactive_on_value=variable.get("inactive_on_value"),
            active_on_value=variable.get("active_on_value"),
            choices=[
                CatalogVariableChoice(
                    label=str(choice.get("label") or choice.get("value") or ""),
                    value=str(choice.get("value") or choice.get("label") or ""),
                )
                for choice in variable.get("choices", [])
            ],
        )
        for variable in payload.get("variables", [])
    ]
    return CatalogFormResponse(item=item, variables=variables)


neo4j_catalog_graph_service = Neo4jCatalogGraphService(settings.graphrag_neo4j_config())
platform_neo4j_catalog_graph_service = Neo4jCatalogGraphService(settings.platform_neo4j_config())


def _schema_signature(form: CatalogFormResponse) -> str:
    fields = [
        {
            "name": variable.name,
            "label": variable.label,
            "type": variable.type,
            "mandatory": variable.mandatory,
            "choices": [
                {"label": choice.label, "value": choice.value}
                for choice in variable.choices
            ],
        }
        for variable in form.variables
    ]
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema_key(form: CatalogFormResponse, signature: str) -> str:
    category = (form.item.category or "uncategorized").strip().lower().replace(" ", "_")
    return f"{category}:{signature[:16]}"


def _schema_name(form: CatalogFormResponse) -> str:
    if form.item.category:
        return f"{form.item.category} filters"
    return f"{form.item.name} filters"


def _catalog_search_text(form: CatalogFormResponse) -> str:
    parts = [
        form.item.name,
        form.item.display_name,
        form.item.short_description,
        form.item.description,
        form.item.category,
        form.item.category_name,
    ]
    parts.extend(variable.name for variable in form.variables)
    parts.extend(variable.label for variable in form.variables)
    return " ".join(str(part or "").strip() for part in parts if str(part or "").strip()).lower()


_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "need", "want",
    "your", "you", "our", "are", "get", "new", "request", "service", "item", "catalog",
    "please", "user", "users", "system", "access", "type", "field", "fields",
}


def _tokenize(text: Optional[str]) -> list[str]:
    if not text:
        return []
    tokens = re.findall(r"[a-z0-9]+", str(text).lower())
    return [token for token in tokens if len(token) >= 3 and token not in _STOP_WORDS]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
