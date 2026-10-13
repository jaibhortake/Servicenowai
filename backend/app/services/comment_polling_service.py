import asyncio
import logging
from typing import Dict, Set
from app.services import servicenow as snow
from app.services.websocket_service import websocket_service

logger = logging.getLogger(__name__)


class CommentPollingService:
    """Background service that polls ServiceNow for new comments and pushes them via WebSocket."""

    def __init__(self, poll_interval_seconds: int = 5):
        self.poll_interval = poll_interval_seconds
        self.running = False
        self._watched_tickets: Dict[str, str] = {}  # ticket_sys_id -> table (sc_request | change_request)
        self._seen_comments: Dict[str, Set[str]] = {}  # ticket_sys_id -> set of seen comment timestamps
        self._task: asyncio.Task = None

    async def startup(self) -> None:
        """Start the background polling task."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[COMMENT_POLLING] Service started")

    async def shutdown(self) -> None:
        """Stop the background polling task."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[COMMENT_POLLING] Service stopped")

    def watch(self, ticket_sys_id: str, table: str = "sc_request") -> None:
        """Start watching a ticket for new comments."""
        if ticket_sys_id not in self._watched_tickets:
            self._watched_tickets[ticket_sys_id] = table
            self._seen_comments[ticket_sys_id] = set()
            logger.debug(f"[COMMENT_POLLING] Now watching ticket {ticket_sys_id}")

    def unwatch(self, ticket_sys_id: str) -> None:
        """Stop watching a ticket."""
        if ticket_sys_id in self._watched_tickets:
            del self._watched_tickets[ticket_sys_id]
            del self._seen_comments[ticket_sys_id]
            logger.debug(f"[COMMENT_POLLING] Stopped watching ticket {ticket_sys_id}")

    async def _poll_loop(self) -> None:
        """Main polling loop — checks for new comments every poll_interval seconds."""
        while self.running:
            try:
                await asyncio.sleep(self.poll_interval)
                if not self._watched_tickets:
                    continue

                logger.info(f"[COMMENT_POLLING] Polling {len(self._watched_tickets)} tickets")

                # Check each watched ticket for new comments
                for ticket_sys_id, table in list(self._watched_tickets.items()):
                    try:
                        comments = snow.get_comments(table, ticket_sys_id) or []
                        work_notes = snow.get_work_notes(table, ticket_sys_id) or []
                        journal_entries = [
                            {**entry, "_source": "comment"} for entry in comments
                        ] + [
                            {**entry, "_source": "work_note"} for entry in work_notes
                        ]

                        if not journal_entries:
                            logger.debug(f"[COMMENT_POLLING] No comments found for ticket {ticket_sys_id}")
                            continue

                        # Find comments we haven't seen yet
                        seen_set = self._seen_comments.get(ticket_sys_id, set())
                        new_comments = []

                        for comment in journal_entries:
                            timestamp = comment.get("sys_created_on", "")
                            element = comment.get("element", "")
                            value = comment.get("value", "")
                            seen_key = f"{timestamp}|{element}|{value}"
                            if timestamp and seen_key not in seen_set:
                                new_comments.append(comment)
                                seen_set.add(seen_key)

                        # Update the seen set
                        self._seen_comments[ticket_sys_id] = seen_set

                        if new_comments:
                            logger.info(f"[COMMENT_POLLING] Found {len(new_comments)} new comments for ticket {ticket_sys_id}")

                        # Push each new comment via WebSocket
                        for comment in new_comments:
                            payload = {
                                "type": "new_comment",
                                "ticket_sys_id": ticket_sys_id,
                                "comment": comment,
                            }
                            await websocket_service.push(ticket_sys_id, payload)
                            logger.info(f"[COMMENT_POLLING] Pushed comment: {comment.get('value', '')[:50]}")

                    except Exception as e:
                        logger.warning(f"[COMMENT_POLLING] Error polling ticket {ticket_sys_id}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[COMMENT_POLLING] Unexpected error in poll loop: {e}")


comment_polling_service = CommentPollingService()
