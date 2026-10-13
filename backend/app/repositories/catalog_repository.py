from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class CatalogRepository(ABC):
    """Abstract interface for catalog operations"""

    @abstractmethod
    def list_items(self, query: Optional[str] = None, limit: int = 100, offset: int = 0, active: bool = True) -> List[Dict[str, Any]]:
        """List catalog items"""
        pass

    @abstractmethod
    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get a single catalog item"""
        pass

    @abstractmethod
    def list_item_variables(self, item_id: str) -> List[Dict[str, Any]]:
        """List variables for a catalog item"""
        pass

    @abstractmethod
    def list_changes(self, since: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        """List recent changes to catalog items"""
        pass


class ServiceNowCatalogRepository(CatalogRepository):
    """ServiceNow-specific implementation of CatalogRepository"""

    def __init__(self):
        from app.services import servicenow as snow
        self.snow = snow

    def list_items(self, query: Optional[str] = None, limit: int = 100, offset: int = 0, active: bool = True) -> List[Dict[str, Any]]:
        """List catalog items from ServiceNow"""
        try:
            return self.snow.list_catalog_items(category=None, limit=limit, query=query)
        except Exception as e:
            logger.error(f"Failed to list catalog items: {e}")
            return []

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get a single catalog item from ServiceNow"""
        try:
            item = self.snow.get_catalog_item(item_id)
            if item:
                return {"success": True, "data": item}
            return {"success": False}
        except Exception as e:
            logger.error(f"Failed to get catalog item {item_id}: {e}")
            return {"success": False}

    def list_item_variables(self, item_id: str) -> List[Dict[str, Any]]:
        """List variables for a catalog item from ServiceNow"""
        try:
            return self.snow.list_catalog_item_variables(item_id)
        except Exception as e:
            logger.error(f"Failed to list catalog item variables for {item_id}: {e}")
            return []

    def list_changes(self, since: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        """List recent changes to catalog items"""
        try:
            return self.snow.list_catalog_changes(since=since, limit=limit)
        except Exception as e:
            logger.error(f"Failed to list catalog changes: {e}")
            return {
                "success": False,
                "message": str(e),
                "changed_catalog_item_ids": [],
                "next_since": since,
            }


_catalog_repository: Optional[CatalogRepository] = None


def get_catalog_repository() -> CatalogRepository:
    """Get the catalog repository instance"""
    global _catalog_repository
    if _catalog_repository is None:
        _catalog_repository = ServiceNowCatalogRepository()
    return _catalog_repository


def set_catalog_repository(repository: CatalogRepository) -> None:
    """Set a custom catalog repository (useful for testing)"""
    global _catalog_repository
    _catalog_repository = repository
