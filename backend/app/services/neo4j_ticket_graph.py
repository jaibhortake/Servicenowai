import logging
import re
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.config import Neo4jConnectionConfig, settings

logger = logging.getLogger(__name__)


class Neo4jTicketGraphService:
    def __init__(self, connection_config: Neo4jConnectionConfig | None = None) -> None:
        self._config = connection_config or settings.graphrag_neo4j_config()
        self._database = self._config.database
        self._driver = None

    def startup(self) -> None:
        if not self._config.enabled:
            logger.info("%s ticket graph is disabled", self._config.name)
            return

        if self._driver is not None:
            return

        if not self._config.is_complete:
            logger.warning("%s ticket graph settings are incomplete", self._config.name)
            return

        try:
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                connection_timeout=3.0,
                max_transaction_retry_time=2.0,
            )
            self._driver.verify_connectivity()
            if self._block_write("ticket constraint creation"):
                logger.info("Connected to %s ticket graph in read-only mode", self._config.name)
            else:
                self._ensure_constraints()
            logger.info("Connected to %s ticket graph", self._config.name)
        except Exception:
            logger.exception("Failed to initialize %s ticket graph", self._config.name)
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

    def sync_service_request(
        self,
        ticket: Dict[str, Any],
        request_item: Optional[Dict[str, Any]] = None,
        error_details: Optional[Dict[str, str]] = None,
    ) -> bool:
        if self._block_write("service request sync"):
            if error_details is not None:
                error_details["message"] = self._config.write_protection_reason
            return False
        if self._driver is None:
            return False

        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(self._upsert_service_request, ticket, request_item)
            return True
        except Neo4jError as exc:
            if error_details is not None:
                error_details["message"] = str(exc)
            logger.exception(
                "Failed to sync service request %s (%s)",
                ticket.get("sys_id"),
                ticket.get("number"),
            )
            return False

    def sync_change_request(
        self,
        ticket: Dict[str, Any],
        error_details: Optional[Dict[str, str]] = None,
    ) -> bool:
        if self._block_write("change request sync"):
            if error_details is not None:
                error_details["message"] = self._config.write_protection_reason
            return False
        if self._driver is None:
            return False

        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(self._upsert_change_request, ticket)
            return True
        except Neo4jError as exc:
            if error_details is not None:
                error_details["message"] = str(exc)
            logger.exception(
                "Failed to sync change request %s (%s)",
                ticket.get("sys_id"),
                ticket.get("number"),
            )
            return False

    def sync_incident(
        self,
        ticket: Dict[str, Any],
        error_details: Optional[Dict[str, str]] = None,
    ) -> bool:
        if self._block_write("incident sync"):
            if error_details is not None:
                error_details["message"] = self._config.write_protection_reason
            return False
        if self._driver is None:
            return False

        try:
            with self._driver.session(database=self._database) as session:
                session.execute_write(self._upsert_incident, ticket)
            return True
        except Neo4jError as exc:
            if error_details is not None:
                error_details["message"] = str(exc)
            logger.exception(
                "Failed to sync incident %s (%s)",
                ticket.get("sys_id"),
                ticket.get("number"),
            )
            return False

    def get_ticket_context(
        self,
        sys_id: str,
        ticket_type: Optional[str] = None,
        user_sys_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self._driver is None:
            return None

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(
                    self._read_ticket_context,
                    str(sys_id or "").strip(),
                    str(ticket_type or "").strip().upper() or None,
                    str(user_sys_id or "").strip() or None,
                )
        except Neo4jError:
            logger.exception("Failed to read ticket context for %s", sys_id)
            return None

    def search_tickets(
        self,
        query: str,
        keywords: Optional[List[str]] = None,
        user_sys_id: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        if self._driver is None:
            return []

        phrase_terms: List[str] = []
        if query and query.strip():
            phrase_terms.append(query.strip().lower())
        for keyword in keywords or []:
            normalized = str(keyword or "").strip().lower()
            if normalized:
                phrase_terms.append(normalized)

        token_terms: List[str] = []
        for value in phrase_terms:
            token_terms.extend(_tokenize(value))

        all_terms = _dedupe_preserve_order(token_terms)
        phrase_terms = _dedupe_preserve_order(
            [term for term in phrase_terms if len(term) >= 2]
        )

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(
                    self._search_tickets,
                    all_terms,
                    phrase_terms,
                    str(user_sys_id or "").strip() or None,
                    max(1, limit),
                )
        except Neo4jError:
            logger.exception("Failed to search ticket graph")
            return []

    def list_tickets_for_user(
        self,
        user_sys_id: Optional[str] = None,
        ticket_type: Optional[str] = None,
        states: Optional[List[str]] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        if self._driver is None:
            return []

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(
                    self._list_tickets_for_user,
                    str(user_sys_id or "").strip() or None,
                    str(ticket_type or "").strip().upper() or None,
                    [state.strip().lower() for state in (states or []) if state and state.strip()],
                    max(1, limit),
                )
        except Neo4jError:
            logger.exception("Failed to list tickets for user %s", user_sys_id)
            return []

    def list_all_cases(
        self,
        ticket_type: Optional[str] = None,
        states: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if self._driver is None:
            return []

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(
                    self._list_all_cases,
                    str(ticket_type or "").strip().upper() or None,
                    [state.strip().lower() for state in (states or []) if state and state.strip()],
                    max(1, limit),
                )
        except Neo4jError:
            logger.exception("Failed to list cases from ticket graph")
            return []

    def count_tickets_for_user(
        self,
        user_sys_id: Optional[str] = None,
        ticket_type: Optional[str] = None,
        states: Optional[List[str]] = None,
    ) -> int:
        if self._driver is None:
            return 0

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(
                    self._count_tickets_for_user,
                    str(user_sys_id or "").strip() or None,
                    str(ticket_type or "").strip().upper() or None,
                    [state.strip().lower() for state in (states or []) if state and state.strip()],
                )
        except Neo4jError:
            logger.exception("Failed to count tickets for user %s", user_sys_id)
            return 0

    def rank_catalog_items_by_requests(
        self,
        keywords: Optional[List[str]] = None,
        catalog_sys_ids: Optional[List[str]] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        if self._driver is None:
            return []

        normalized_keywords = _dedupe_preserve_order(
            [str(term or "").strip().lower() for term in (keywords or []) if str(term or "").strip()]
        )
        normalized_sys_ids = [
            str(sys_id or "").strip()
            for sys_id in (catalog_sys_ids or [])
            if str(sys_id or "").strip()
        ]

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(
                    self._rank_catalog_items_by_requests,
                    normalized_keywords,
                    normalized_sys_ids,
                    max(1, limit),
                )
        except Neo4jError:
            logger.exception("Failed to rank catalog items by request frequency")
            return []

    def status(self) -> Dict[str, Any]:
        if self._driver is None:
            return {
                "available": False,
                "counts": {
                    "tickets": 0,
                    "service_requests": 0,
                    "change_requests": 0,
                    "request_items": 0,
                    "users": 0,
                },
                "relationship_count": 0,
            }

        try:
            with self._driver.session(database=self._database) as session:
                counts = session.execute_read(self._read_graph_counts)
        except Neo4jError as exc:
            logger.exception("Failed to read ticket graph status")
            return {
                "available": True,
                "counts": {
                    "tickets": 0,
                    "service_requests": 0,
                    "change_requests": 0,
                    "request_items": 0,
                    "users": 0,
                },
                "relationship_count": 0,
                "status_error": str(exc),
            }

        return {"available": True, **counts}

    def _ensure_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT ticket_sys_id IF NOT EXISTS FOR (n:Ticket) REQUIRE n.sys_id IS UNIQUE",
            "CREATE CONSTRAINT request_item_sys_id IF NOT EXISTS FOR (n:RequestItem) REQUIRE n.sys_id IS UNIQUE",
            "CREATE CONSTRAINT ticket_user_sys_id IF NOT EXISTS FOR (n:User) REQUIRE n.sys_id IS UNIQUE",
        ]
        with self._driver.session(database=self._database) as session:
            for statement in statements:
                session.run(statement).consume()

    @staticmethod
    def _upsert_service_request(tx, ticket: Dict[str, Any], request_item: Optional[Dict[str, Any]]) -> None:
        tx.run(
            """
            MERGE (root:Platform {name: 'ServiceNow'})
            MERGE (ticket:Ticket:ServiceRequest {sys_id: $sys_id})
            SET ticket.number = $number,
                ticket.name = coalesce($number, $short_description, $requested_for_name),
                ticket.display_name = coalesce($number, $short_description, $requested_for_name),
                ticket.ticket_type = 'SR',
                ticket.short_description = $short_description,
                ticket.description = $description,
                ticket.state = $state,
                ticket.state_display = $state_display,
                ticket.opened_at = $opened_at,
                ticket.requested_for_sys_id = $requested_for_sys_id,
                ticket.requested_for_name = $requested_for_name,
                ticket.assigned_to_sys_id = $assigned_to_sys_id,
                ticket.assigned_to_name = $assigned_to_name,
                ticket.ritm_sys_id = $ritm_sys_id,
                ticket.search_text = toLower(coalesce($number, '') + ' ' + coalesce($short_description, '') + ' ' + coalesce($description, '') + ' ' + coalesce($requested_for_name, '')),
                ticket.updated_at = datetime()
            MERGE (root)-[:HAS_TICKET]->(ticket)
            """,
            **ticket,
        ).consume()

        tx.run(
            """
            MATCH (ticket:Ticket:ServiceRequest {sys_id: $sys_id})-[rel:HAS_ITEM]->(:RequestItem)
            DELETE rel
            """,
            sys_id=ticket["sys_id"],
        ).consume()

        tx.run(
            """
            MATCH (requester:User)-[rel:REQUESTED]->(ticket:Ticket {sys_id: $sys_id})
            DELETE rel
            """,
            sys_id=ticket["sys_id"],
        ).consume()

        tx.run(
            """
            MATCH (assignee:User)-[rel:ASSIGNED_TO]->(ticket:Ticket {sys_id: $sys_id})
            DELETE rel
            """,
            sys_id=ticket["sys_id"],
        ).consume()

        if ticket.get("requested_for_sys_id"):
            tx.run(
                """
                MATCH (ticket:Ticket:ServiceRequest {sys_id: $ticket_sys_id})
                MERGE (user:User {sys_id: $user_sys_id})
                SET user.name = $user_name,
                    user.display_name = $user_name
                MERGE (user)-[:REQUESTED]->(ticket)
                """,
                ticket_sys_id=ticket["sys_id"],
                user_sys_id=ticket["requested_for_sys_id"],
                user_name=ticket.get("requested_for_name"),
            ).consume()

        if ticket.get("assigned_to_sys_id"):
            tx.run(
                """
                MATCH (ticket:Ticket:ServiceRequest {sys_id: $ticket_sys_id})
                MERGE (user:User {sys_id: $user_sys_id})
                SET user.name = $user_name,
                    user.display_name = $user_name
                MERGE (user)-[:ASSIGNED_TO]->(ticket)
                """,
                ticket_sys_id=ticket["sys_id"],
                user_sys_id=ticket["assigned_to_sys_id"],
                user_name=ticket.get("assigned_to_name"),
            ).consume()

        if request_item and request_item.get("sys_id"):
            tx.run(
                """
                MATCH (ticket:Ticket:ServiceRequest {sys_id: $ticket_sys_id})
                MERGE (item:RequestItem {sys_id: $sys_id})
                SET item.number = $number,
                    item.name = coalesce($number, $short_description, $cat_item_name),
                    item.display_name = coalesce($number, $short_description, $cat_item_name),
                    item.short_description = $short_description,
                    item.description = $description,
                    item.state = $state,
                    item.state_display = $state_display,
                    item.cat_item_sys_id = $cat_item_sys_id,
                    item.cat_item_name = $cat_item_name,
                    item.search_text = toLower(coalesce($number, '') + ' ' + coalesce($short_description, '') + ' ' + coalesce($description, '') + ' ' + coalesce($cat_item_name, '')),
                    item.updated_at = datetime()
                MERGE (ticket)-[:HAS_ITEM]->(item)
                """,
                ticket_sys_id=ticket["sys_id"],
                **request_item,
            ).consume()

            if request_item.get("cat_item_sys_id"):
                tx.run(
                    """
                    MATCH (item:RequestItem {sys_id: $item_sys_id})
                    MERGE (catalog:CatalogItem {sys_id: $catalog_sys_id})
                    ON CREATE SET catalog.name = $catalog_name,
                                  catalog.display_name = $catalog_name
                    ON MATCH SET catalog.name = coalesce(catalog.name, $catalog_name),
                                 catalog.display_name = coalesce(catalog.display_name, catalog.name, $catalog_name)
                    MERGE (item)-[:FULFILLS_CATALOG_ITEM]->(catalog)
                    """,
                    item_sys_id=request_item["sys_id"],
                    catalog_sys_id=request_item["cat_item_sys_id"],
                    catalog_name=request_item.get("cat_item_name"),
                ).consume()

    @staticmethod
    def _upsert_change_request(tx, ticket: Dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (root:Platform {name: 'ServiceNow'})
            MERGE (ticket:Ticket:ChangeRequest {sys_id: $sys_id})
            SET ticket.number = $number,
                ticket.name = coalesce($number, $short_description, $requested_by_name),
                ticket.display_name = coalesce($number, $short_description, $requested_by_name),
                ticket.ticket_type = 'CH',
                ticket.short_description = $short_description,
                ticket.description = $description,
                ticket.state = $state,
                ticket.state_display = $state_display,
                ticket.opened_at = $opened_at,
                ticket.requested_by_sys_id = $requested_by_sys_id,
                ticket.requested_by_name = $requested_by_name,
                ticket.assigned_to_sys_id = $assigned_to_sys_id,
                ticket.assigned_to_name = $assigned_to_name,
                ticket.risk = $risk,
                ticket.impact = $impact,
                ticket.change_type = $change_type,
                ticket.search_text = toLower(coalesce($number, '') + ' ' + coalesce($short_description, '') + ' ' + coalesce($description, '') + ' ' + coalesce($requested_by_name, '')),
                ticket.updated_at = datetime()
            MERGE (root)-[:HAS_TICKET]->(ticket)
            """,
            **ticket,
        ).consume()

        tx.run(
            """
            MATCH (requester:User)-[rel:REQUESTED]->(ticket:Ticket {sys_id: $sys_id})
            DELETE rel
            """,
            sys_id=ticket["sys_id"],
        ).consume()

        tx.run(
            """
            MATCH (assignee:User)-[rel:ASSIGNED_TO]->(ticket:Ticket {sys_id: $sys_id})
            DELETE rel
            """,
            sys_id=ticket["sys_id"],
        ).consume()

        if ticket.get("requested_by_sys_id"):
            tx.run(
                """
                MATCH (ticket:Ticket:ChangeRequest {sys_id: $ticket_sys_id})
                MERGE (user:User {sys_id: $user_sys_id})
                SET user.name = $user_name,
                    user.display_name = $user_name
                MERGE (user)-[:REQUESTED]->(ticket)
                """,
                ticket_sys_id=ticket["sys_id"],
                user_sys_id=ticket["requested_by_sys_id"],
                user_name=ticket.get("requested_by_name"),
            ).consume()

        if ticket.get("assigned_to_sys_id"):
            tx.run(
                """
                MATCH (ticket:Ticket:ChangeRequest {sys_id: $ticket_sys_id})
                MERGE (user:User {sys_id: $user_sys_id})
                SET user.name = $user_name,
                    user.display_name = $user_name
                MERGE (user)-[:ASSIGNED_TO]->(ticket)
                """,
                ticket_sys_id=ticket["sys_id"],
                user_sys_id=ticket["assigned_to_sys_id"],
                user_name=ticket.get("assigned_to_name"),
            ).consume()

    @staticmethod
    def _upsert_incident(tx, ticket: Dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (root:Platform {name: 'ServiceNow'})
            MERGE (ticket:Ticket:Incident {sys_id: $sys_id})
            SET ticket.number = $number,
                ticket.name = coalesce($number, $short_description, $caller_name),
                ticket.display_name = coalesce($number, $short_description, $caller_name),
                ticket.ticket_type = 'INC',
                ticket.short_description = $short_description,
                ticket.description = $description,
                ticket.state = $state,
                ticket.state_display = $state_display,
                ticket.incident_state = $incident_state,
                ticket.incident_state_display = $incident_state_display,
                ticket.opened_at = $opened_at,
                ticket.caller_sys_id = $caller_sys_id,
                ticket.caller_name = $caller_name,
                ticket.assigned_to_sys_id = $assigned_to_sys_id,
                ticket.assigned_to_name = $assigned_to_name,
                ticket.priority = $priority,
                ticket.urgency = $urgency,
                ticket.impact = $impact,
                ticket.category = $category,
                ticket.subcategory = $subcategory,
                ticket.close_code = $close_code,
                ticket.close_notes = $close_notes,
                ticket.resolved_at = $resolved_at,
                ticket.resolved_by_sys_id = $resolved_by_sys_id,
                ticket.resolved_by_name = $resolved_by_name,
                ticket.resolution_notes = $resolution_notes,
                ticket.workaround = $workaround,
                ticket.cause_notes = $cause_notes,
                ticket.work_notes = $work_notes,
                ticket.comments = $comments,
                ticket.journal_text = $journal_text,
                ticket.search_text = toLower(
                    coalesce($number, '') + ' ' +
                    coalesce($short_description, '') + ' ' +
                    coalesce($description, '') + ' ' +
                    coalesce($caller_name, '') + ' ' +
                    coalesce($state_display, '') + ' ' +
                    coalesce($incident_state_display, '') + ' ' +
                    coalesce($close_code, '') + ' ' +
                    coalesce($close_notes, '') + ' ' +
                    coalesce($resolved_by_name, '') + ' ' +
                    coalesce($resolution_notes, '') + ' ' +
                    coalesce($workaround, '') + ' ' +
                    coalesce($cause_notes, '') + ' ' +
                    coalesce($comments, '') + ' ' +
                    coalesce($work_notes, '')
                ),
                ticket.updated_at = datetime()
            MERGE (root)-[:HAS_TICKET]->(ticket)
            """,
            **ticket,
        ).consume()

        tx.run(
            """
            MATCH (requester:User)-[rel:REQUESTED]->(ticket:Ticket {sys_id: $sys_id})
            DELETE rel
            """,
            sys_id=ticket["sys_id"],
        ).consume()

        tx.run(
            """
            MATCH (assignee:User)-[rel:ASSIGNED_TO]->(ticket:Ticket {sys_id: $sys_id})
            DELETE rel
            """,
            sys_id=ticket["sys_id"],
        ).consume()

        if ticket.get("caller_sys_id"):
            tx.run(
                """
                MATCH (ticket:Ticket:Incident {sys_id: $ticket_sys_id})
                MERGE (user:User {sys_id: $user_sys_id})
                SET user.name = $user_name,
                    user.display_name = $user_name
                MERGE (user)-[:REQUESTED]->(ticket)
                """,
                ticket_sys_id=ticket["sys_id"],
                user_sys_id=ticket["caller_sys_id"],
                user_name=ticket.get("caller_name"),
            ).consume()

        if ticket.get("assigned_to_sys_id"):
            tx.run(
                """
                MATCH (ticket:Ticket:Incident {sys_id: $ticket_sys_id})
                MERGE (user:User {sys_id: $user_sys_id})
                SET user.name = $user_name,
                    user.display_name = $user_name
                MERGE (user)-[:ASSIGNED_TO]->(ticket)
                """,
                ticket_sys_id=ticket["sys_id"],
                user_sys_id=ticket["assigned_to_sys_id"],
                user_name=ticket.get("assigned_to_name"),
            ).consume()

    @staticmethod
    def _read_ticket_context(tx, sys_id: str, ticket_type: Optional[str], user_sys_id: Optional[str]) -> Optional[Dict[str, Any]]:
        query = """
            MATCH (ticket:Ticket {sys_id: $sys_id})
            WHERE ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
              AND (
                    $user_sys_id IS NULL OR
                    ticket.requested_for_sys_id = $user_sys_id OR
                    ticket.requested_by_sys_id = $user_sys_id
                  )
            OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
            OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
            RETURN ticket {
                .*,
                labels: labels(ticket)
            } AS ticket,
            item {
                .sys_id,
                .number,
                .short_description,
                .description,
                .state,
                .state_display,
                .cat_item_sys_id,
                .cat_item_name
            } AS request_item,
            catalog {
                .sys_id,
                .name
            } AS catalog_item
            LIMIT 1
        """
        record = tx.run(
            query,
            sys_id=sys_id,
            ticket_type=ticket_type,
            user_sys_id=user_sys_id,
        ).single()
        if not record:
            return None
        return dict(record)

    @staticmethod
    def _search_tickets(
        tx,
        terms: List[str],
        phrase_terms: List[str],
        user_sys_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        query = """
            MATCH (ticket:Ticket)
            OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
            OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
            WHERE (
                $user_sys_id IS NULL OR
                ticket.requested_for_sys_id = $user_sys_id OR
                ticket.requested_by_sys_id = $user_sys_id
            )
            WITH ticket, item, catalog,
                 reduce(score = 0, term IN $terms |
                    score +
                    CASE WHEN toLower(coalesce(ticket.number, '')) = term THEN 80 ELSE 0 END +
                    CASE WHEN toLower(coalesce(ticket.number, '')) CONTAINS term THEN 40 ELSE 0 END +
                    CASE WHEN toLower(coalesce(ticket.short_description, '')) CONTAINS term THEN 18 ELSE 0 END +
                    CASE WHEN toLower(coalesce(ticket.description, '')) CONTAINS term THEN 10 ELSE 0 END +
                    CASE WHEN toLower(coalesce(ticket.ticket_type, '')) CONTAINS term THEN 8 ELSE 0 END +
                    CASE WHEN toLower(coalesce(ticket.state_display, '')) CONTAINS term OR toLower(coalesce(ticket.state, '')) CONTAINS term THEN 8 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.short_description, '')) CONTAINS term THEN 10 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.cat_item_name, '')) CONTAINS term THEN 12 ELSE 0 END +
                    CASE WHEN toLower(coalesce(catalog.name, '')) CONTAINS term THEN 12 ELSE 0 END
                 ) AS token_score,
                 reduce(score = 0, phrase IN $phrase_terms |
                    score +
                    CASE WHEN toLower(coalesce(ticket.number, '')) = phrase THEN 100 ELSE 0 END +
                    CASE WHEN toLower(coalesce(ticket.short_description, '')) CONTAINS phrase THEN 22 ELSE 0 END +
                    CASE WHEN toLower(coalesce(ticket.description, '')) CONTAINS phrase THEN 14 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.short_description, '')) CONTAINS phrase THEN 14 ELSE 0 END +
                    CASE WHEN toLower(coalesce(item.cat_item_name, '')) CONTAINS phrase THEN 18 ELSE 0 END +
                    CASE WHEN toLower(coalesce(catalog.name, '')) CONTAINS phrase THEN 18 ELSE 0 END
                 ) AS phrase_score
            WITH ticket, item, catalog, token_score + phrase_score AS score
            WHERE score > 0
            RETURN ticket.sys_id AS sys_id,
                   ticket.number AS number,
                   ticket.ticket_type AS ticket_type,
                   ticket.short_description AS short_description,
                   ticket.description AS description,
                   ticket.state AS state,
                   ticket.state_display AS state_display,
                   ticket.opened_at AS opened_at,
                   ticket.requested_for_sys_id AS requested_for_sys_id,
                   ticket.requested_for_name AS requested_for_name,
                   ticket.requested_by_sys_id AS requested_by_sys_id,
                   ticket.requested_by_name AS requested_by_name,
                   ticket.assigned_to_sys_id AS assigned_to_sys_id,
                   ticket.assigned_to_name AS assigned_to_name,
                   ticket.risk AS risk,
                   ticket.impact AS impact,
                   ticket.change_type AS change_type,
                   item.sys_id AS request_item_sys_id,
                   item.number AS request_item_number,
                   item.short_description AS request_item_short_description,
                   item.cat_item_sys_id AS catalog_item_sys_id,
                   item.cat_item_name AS catalog_item_name,
                   score AS score
            ORDER BY score DESC, coalesce(ticket.opened_at, '') DESC
            LIMIT $limit
        """
        result = tx.run(
            query,
            terms=terms,
            phrase_terms=phrase_terms,
            user_sys_id=user_sys_id,
            limit=limit,
        )
        return [dict(record) for record in result]

    @staticmethod
    def _list_tickets_for_user(
        tx,
        user_sys_id: Optional[str],
        ticket_type: Optional[str],
        states: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        query = """
            MATCH (ticket:Ticket)
            OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
            WHERE (
                $user_sys_id IS NULL OR
                ticket.requested_for_sys_id = $user_sys_id OR
                ticket.requested_by_sys_id = $user_sys_id
            )
              AND ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
              AND (
                    size($states) = 0 OR
                    ANY(state IN $states WHERE
                        toLower(coalesce(ticket.state_display, '')) CONTAINS state OR
                        toLower(coalesce(ticket.state, '')) CONTAINS state
                    )
                  )
            RETURN ticket.sys_id AS sys_id,
                   ticket.number AS number,
                   ticket.ticket_type AS ticket_type,
                   ticket.short_description AS short_description,
                   ticket.description AS description,
                   ticket.state AS state,
                   ticket.state_display AS state_display,
                   ticket.opened_at AS opened_at,
                   ticket.requested_for_sys_id AS requested_for_sys_id,
                   ticket.requested_for_name AS requested_for_name,
                   ticket.requested_by_sys_id AS requested_by_sys_id,
                   ticket.requested_by_name AS requested_by_name,
                   ticket.assigned_to_sys_id AS assigned_to_sys_id,
                   ticket.assigned_to_name AS assigned_to_name,
                   ticket.risk AS risk,
                   ticket.impact AS impact,
                   ticket.change_type AS change_type,
                   item.sys_id AS request_item_sys_id,
                   item.number AS request_item_number,
                   item.short_description AS request_item_short_description,
                   item.cat_item_sys_id AS catalog_item_sys_id,
                   item.cat_item_name AS catalog_item_name
            ORDER BY coalesce(ticket.opened_at, '') DESC, coalesce(ticket.number, '') DESC
            LIMIT $limit
        """
        result = tx.run(
            query,
            user_sys_id=user_sys_id,
            ticket_type=ticket_type,
            states=states,
            limit=limit,
        )
        return [dict(record) for record in result]

    @staticmethod
    def _list_all_cases(
        tx,
        ticket_type: Optional[str],
        states: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        query = """
            CALL {
                MATCH (ticket:Ticket)
                RETURN ticket
                UNION
                MATCH (ticket:Incident)
                WHERE NOT ticket:Ticket
                RETURN ticket
            }
            WITH
                ticket,
                coalesce(
                    ticket.ticket_type,
                    CASE
                        WHEN ticket:Incident THEN 'INC'
                        WHEN ticket:ChangeRequest THEN 'CH'
                        WHEN ticket:ServiceRequest THEN 'SR'
                        ELSE null
                    END
                ) AS ticket_type
            OPTIONAL MATCH (ticket)-[:HAS_ITEM]->(item:RequestItem)
            OPTIONAL MATCH (item)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
            WHERE ($ticket_type IS NULL OR ticket_type = $ticket_type)
              AND (
                    size($states) = 0 OR
                    ANY(state IN $states WHERE
                        toLower(coalesce(ticket.state_display, '')) CONTAINS state OR
                        toLower(coalesce(ticket.state, '')) CONTAINS state OR
                        toLower(coalesce(ticket.incident_state_display, '')) CONTAINS state OR
                        toLower(coalesce(ticket.incident_state, '')) CONTAINS state
                    )
                  )
            RETURN ticket.sys_id AS sys_id,
                   ticket.number AS number,
                   ticket_type AS ticket_type,
                   labels(ticket) AS labels,
                   ticket.name AS name,
                   ticket.display_name AS display_name,
                   ticket.short_description AS short_description,
                   ticket.description AS description,
                   ticket.state AS state,
                   ticket.state_display AS state_display,
                   ticket.incident_state AS incident_state,
                   ticket.incident_state_display AS incident_state_display,
                   ticket.opened_at AS opened_at,
                   ticket.resolved_at AS resolved_at,
                   ticket.priority AS priority,
                   ticket.urgency AS urgency,
                   ticket.impact AS impact,
                   ticket.category AS category,
                   ticket.subcategory AS subcategory,
                   ticket.close_code AS close_code,
                   ticket.close_notes AS close_notes,
                   ticket.resolution_notes AS resolution_notes,
                   ticket.workaround AS workaround,
                   ticket.cause_notes AS cause_notes,
                   ticket.work_notes AS work_notes,
                   ticket.comments AS comments,
                   ticket.caller_sys_id AS caller_sys_id,
                   ticket.caller_name AS caller_name,
                   ticket.requested_for_sys_id AS requested_for_sys_id,
                   ticket.requested_for_name AS requested_for_name,
                   ticket.requested_by_sys_id AS requested_by_sys_id,
                   ticket.requested_by_name AS requested_by_name,
                   ticket.assigned_to_sys_id AS assigned_to_sys_id,
                   ticket.assigned_to_name AS assigned_to_name,
                   ticket.risk AS risk,
                   ticket.change_type AS change_type,
                   ticket.updated_at AS updated_at,
                   item.sys_id AS request_item_sys_id,
                   item.number AS request_item_number,
                   item.short_description AS request_item_short_description,
                   item.description AS request_item_description,
                   item.cat_item_sys_id AS catalog_item_sys_id,
                   item.cat_item_name AS catalog_item_name,
                   catalog.sys_id AS catalog_item_graph_sys_id,
                   catalog.name AS catalog_item_graph_name
            ORDER BY coalesce(ticket.opened_at, '') DESC, coalesce(ticket.number, '') DESC
            LIMIT $limit
        """
        result = tx.run(
            query,
            ticket_type=ticket_type,
            states=states,
            limit=limit,
        )
        return [dict(record) for record in result]

    @staticmethod
    def _count_tickets_for_user(
        tx,
        user_sys_id: Optional[str],
        ticket_type: Optional[str],
        states: List[str],
    ) -> int:
        query = """
            MATCH (ticket:Ticket)
            WHERE (
                $user_sys_id IS NULL OR
                ticket.requested_for_sys_id = $user_sys_id OR
                ticket.requested_by_sys_id = $user_sys_id
            )
              AND ($ticket_type IS NULL OR ticket.ticket_type = $ticket_type)
              AND (
                    size($states) = 0 OR
                    ANY(state IN $states WHERE
                        toLower(coalesce(ticket.state_display, '')) CONTAINS state OR
                        toLower(coalesce(ticket.state, '')) CONTAINS state
                    )
                  )
            RETURN count(ticket) AS total
        """
        record = tx.run(
            query,
            user_sys_id=user_sys_id,
            ticket_type=ticket_type,
            states=states,
        ).single()
        return int(record["total"]) if record else 0

    @staticmethod
    def _read_graph_counts(tx) -> Dict[str, Any]:
        ticket_count = tx.run("MATCH (n:Ticket) RETURN count(n) AS total").single()
        service_request_count = tx.run("MATCH (n:ServiceRequest) RETURN count(n) AS total").single()
        change_request_count = tx.run("MATCH (n:ChangeRequest) RETURN count(n) AS total").single()
        request_item_count = tx.run("MATCH (n:RequestItem) RETURN count(n) AS total").single()
        user_count = tx.run("MATCH (n:User) RETURN count(n) AS total").single()
        relationship_count = tx.run("MATCH ()-[r]->() RETURN count(r) AS total").single()
        return {
            "counts": {
                "tickets": int(ticket_count["total"]) if ticket_count else 0,
                "service_requests": int(service_request_count["total"]) if service_request_count else 0,
                "change_requests": int(change_request_count["total"]) if change_request_count else 0,
                "request_items": int(request_item_count["total"]) if request_item_count else 0,
                "users": int(user_count["total"]) if user_count else 0,
            },
            "relationship_count": int(relationship_count["total"]) if relationship_count else 0,
        }

    @staticmethod
    def _rank_catalog_items_by_requests(
        tx,
        keywords: List[str],
        catalog_sys_ids: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        query = """
            MATCH (:Ticket:ServiceRequest)-[:HAS_ITEM]->(item:RequestItem)-[:FULFILLS_CATALOG_ITEM]->(catalog:CatalogItem)
            WITH catalog, item,
                 [term IN $keywords WHERE
                    toLower(coalesce(catalog.name, '')) CONTAINS term OR
                    toLower(coalesce(catalog.short_description, '')) CONTAINS term OR
                    toLower(coalesce(catalog.description, '')) CONTAINS term OR
                    toLower(coalesce(catalog.category, '')) CONTAINS term OR
                    toLower(coalesce(item.short_description, '')) CONTAINS term OR
                    toLower(coalesce(item.description, '')) CONTAINS term OR
                    toLower(coalesce(item.cat_item_name, '')) CONTAINS term
                 ] AS matched_terms
            WHERE (
                size($catalog_sys_ids) = 0 OR
                catalog.sys_id IN $catalog_sys_ids
            )
              AND (
                size($keywords) = 0 OR
                size(matched_terms) > 0
              )
            RETURN catalog.sys_id AS sys_id,
                   catalog.name AS name,
                   catalog.short_description AS short_description,
                   catalog.description AS description,
                   catalog.category AS category,
                   catalog.price AS price,
                   count(DISTINCT item) AS request_count,
                   size(matched_terms) AS matched_term_count
            ORDER BY request_count DESC, matched_term_count DESC, coalesce(catalog.name, '') ASC
            LIMIT $limit
        """
        result = tx.run(
            query,
            keywords=keywords,
            catalog_sys_ids=catalog_sys_ids,
            limit=limit,
        )
        return [dict(record) for record in result]


def _tokenize(text: Optional[str]) -> List[str]:
    if not text:
        return []
    tokens = re.findall(r"[a-z0-9_]+", str(text).lower())
    return [token for token in tokens if len(token) >= 2]


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        normalized = str(value or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


neo4j_ticket_graph_service = Neo4jTicketGraphService(settings.graphrag_neo4j_config())
platform_neo4j_ticket_graph_service = Neo4jTicketGraphService(settings.platform_neo4j_config())
