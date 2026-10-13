# Catalog Repository Abstraction Layer

## Overview
The abstraction layer decouples the MCP tools from ServiceNow API implementation details using the Repository Pattern.

## Architecture

```
MCP Endpoints (server.py)
         ↓
Catalog Tools (catalog_tools.py) 
         ↓
Catalog Repository Interface (catalog_repository.py)
         ↓
ServiceNow Repository Implementation (catalog_repository.py)
         ↓
ServiceNow API (servicenow.py)
```

## When ServiceNow Changes Their API

If ServiceNow updates their API endpoints or response format, **you only need to update two files:**

1. **`app/services/servicenow.py`** - Update the low-level API calls
   - This is the only place that directly touches ServiceNow API
   - Update function implementations to use new API endpoints

2. **`app/repositories/catalog_repository.py`** - Update `ServiceNowCatalogRepository` if response format changes
   - Adapt the new API responses to match the repository interface
   - No changes needed if API responses remain compatible

**Everything else stays the same:**
- ✓ `catalog_tools.py` - No changes
- ✓ `app/mcp/server.py` - No changes  
- ✓ `mcp_client_service.py` - No changes
- ✓ All frontend/client code - No changes

## Example: Switching to a Different Provider

If you want to use a different service provider (not ServiceNow), create a new repository:

```python
from app.repositories import CatalogRepository, set_catalog_repository

class AcmeCatalogRepository(CatalogRepository):
    """Acme-specific implementation"""
    
    def list_items(self, ...):
        # Call Acme API instead
        return self.acme_api.get_items(...)
    
    # ... implement other methods ...

# In your app startup:
set_catalog_repository(AcmeCatalogRepository())
```

All MCP tools continue to work without changes!

## Repository Interface

The `CatalogRepository` abstract class defines:

- `list_items(query, limit, offset, active)` - List catalog items
- `get_item(item_id)` - Get a single item
- `list_item_variables(item_id)` - Get item variables/fields
- `list_changes(since, limit)` - Get recent changes

Any implementation must fulfill these contracts.
