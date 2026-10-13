from typing import Dict, Type, Optional
import logging
from app.repositories import CatalogRepository, ServiceNowCatalogRepository
from app.providers.provider_config import get_current_provider

logger = logging.getLogger(__name__)

# Registry of available provider implementations
_provider_registry: Dict[str, Type[CatalogRepository]] = {
    "servicenow": ServiceNowCatalogRepository,
}

# Cache for current provider instance
_current_provider_instance: Optional[CatalogRepository] = None


def register_provider(name: str, repository_class: Type[CatalogRepository]) -> None:
    """Register a new provider implementation"""
    _provider_registry[name] = repository_class
    logger.info(f"Registered provider: {name}")


def get_provider_instance() -> CatalogRepository:
    """Get the current provider instance"""
    global _current_provider_instance
    
    provider_name = get_current_provider()
    
    # If no provider is configured, default to ServiceNow
    if not provider_name:
        provider_name = "servicenow"
    
    # If cached instance matches current provider, return it
    if _current_provider_instance is not None:
        current_type_name = type(_current_provider_instance).__name__
        if current_type_name == _provider_registry.get(provider_name, ServiceNowCatalogRepository).__name__:
            return _current_provider_instance
    
    # Load new provider instance
    if provider_name not in _provider_registry:
        logger.warning(f"Provider '{provider_name}' not found, falling back to ServiceNow")
        provider_name = "servicenow"
    
    provider_class = _provider_registry[provider_name]
    _current_provider_instance = provider_class()
    logger.info(f"Loaded provider: {provider_name}")
    return _current_provider_instance


def list_registered_providers() -> Dict[str, Type[CatalogRepository]]:
    """List all registered providers"""
    return _provider_registry.copy()
