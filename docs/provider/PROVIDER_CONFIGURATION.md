# Provider Configuration System

## Overview

A flexible, config-driven provider system that allows users to dynamically configure and switch between different service providers (ServiceNow, Jira, Freshdesk, Zendesk, etc.) without code changes.

## Architecture

```
Frontend Chatbot UI
     ↓
Backend API Endpoints (/api/settings/*)
     ↓
Provider Configuration System
     ↓
Provider Registry (loads correct provider)
     ↓
Repository Pattern (CatalogRepository interface)
     ↓
Provider-Specific Implementation
```

## How It Works

### 1. User Selects Provider

User opens the configuration chatbot and selects a provider (ServiceNow, Jira, etc.)

### 2. Dynamic Form Generation

Backend serves provider-specific questions based on `provider_templates.json`:

- Instance URL
- API credentials
- Project/workspace settings
- etc.

### 3. Configuration Saved

Settings are saved to `provider_config.json` and environment variables are updated

### 4. Provider Loaded Dynamically

When tools are called, the system:

1. Reads current provider from config
2. Loads appropriate repository implementation
3. Calls provider-specific API

## Adding a New Provider

### Step 1: Add Template to `provider_templates.json`

```json
{
  "myProvider": {
    "name": "My Service Provider",
    "description": "My provider description",
    "icon": "myprovider",
    "questions": [
      {
        "id": "instance_url",
        "label": "Instance URL",
        "type": "text",
        "required": true,
        "placeholder": "https://example.com",
        "help": "Your provider instance URL"
      },
      {
        "id": "api_key",
        "label": "API Key",
        "type": "password",
        "required": true,
        "help": "Generate from provider settings"
      }
    ],
    "env_vars": {
      "instance_url": "MYPROVIDER_URL",
      "api_key": "MYPROVIDER_API_KEY"
    }
  }
}
```

### Step 2: Create Repository Implementation

Create `app/repositories/my_provider_repository.py`:

```python
from app.repositories import CatalogRepository
from typing import Any, Dict, List, Optional

class MyProviderRepository(CatalogRepository):
    def __init__(self):
        import os
        self.url = os.getenv("MYPROVIDER_URL")
        self.api_key = os.getenv("MYPROVIDER_API_KEY")

    def list_items(self, query: Optional[str] = None, limit: int = 100,
                   offset: int = 0, active: bool = True) -> List[Dict[str, Any]]:
        # Call provider API and return items
        pass

    def get_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        # Call provider API and return single item
        pass

    def list_item_variables(self, item_id: str) -> List[Dict[str, Any]]:
        # Call provider API and return variables
        pass

    def list_changes(self, since: Optional[str] = None,
                     limit: int = 200) -> Dict[str, Any]:
        # Call provider API and return changes
        pass
```

### Step 3: Register Provider

In `app/providers/provider_registry.py`:

```python
from .my_provider_repository import MyProviderRepository

# Add to startup code or application initialization:
register_provider("myProvider", MyProviderRepository)
```

Or add to imports in `app/providers/__init__.py`.

## Backend API Endpoints

### List Available Providers

```
GET /api/settings/providers
```

Response:

```json
{
  "providers": {
    "servicenow": {
      "name": "ServiceNow",
      "description": "ServiceNow ITSM",
      "icon": "cloud"
    },
    "jira": { ... }
  },
  "current": "servicenow"
}
```

### Get Provider Template

```
GET /api/settings/providers/{provider_name}
```

Response:

```json
{
  "name": "ServiceNow",
  "description": "...",
  "icon": "cloud",
  "questions": [...]
}
```

### Get Current Configuration

```
GET /api/settings/current
```

Response:

```json
{
  "provider": "servicenow",
  "settings": {
    "instance_url": "https://...",
    "username": "admin",
    "password": "***"
  }
}
```

### Save Configuration

```
POST /api/settings/configure

{
  "provider": "servicenow",
  "settings": {
    "instance_url": "https://dev12345.service-now.com",
    "username": "api_user",
    "password": "secret"
  }
}
```

## Frontend Integration

### Vue 3 Component

Use the provided `ProviderConfigChatbot.vue` component:

```vue
<template>
  <ProviderConfigChatbot />
</template>

<script setup>
import ProviderConfigChatbot from "@/components/ProviderConfigChatbot.vue";
</script>
```

### Configuration

Set `VUE_APP_API_URL` environment variable:

```bash
VUE_APP_API_URL=http://localhost:8099
```

Or update the component's `API_BASE` constant.

## Security Considerations

### Password Fields

- Passwords/API tokens are **never** returned in GET endpoints
- Replaced with `***` in current configuration display
- Stored securely in environment variables

### Validation

- Required fields are validated before saving
- Provider templates define which fields are required
- Backend validates all inputs

### Configuration File

- `app/providers/provider_config.json` stores current config
- Should be added to `.gitignore` to prevent accidental commits
- Sensitive data stored in environment variables instead

## Environment Variables

Provider configuration updates these environment variables at runtime:

### ServiceNow

- `SERVICENOW_INSTANCE_URL`
- `SERVICENOW_USERNAME`
- `SERVICENOW_PASSWORD`

### Jira

- `JIRA_INSTANCE_URL`
- `JIRA_EMAIL`
- `JIRA_API_TOKEN`
- `JIRA_PROJECT_KEY`

### Freshdesk

- `FRESHDESK_DOMAIN`
- `FRESHDESK_API_KEY`

### Zendesk

- `ZENDESK_SUBDOMAIN`
- `ZENDESK_EMAIL`
- `ZENDESK_API_TOKEN`

## Troubleshooting

### Configuration Not Applied

1. Check that `provider_config.json` exists in `app/providers/`
2. Verify environment variables are set correctly
3. Restart the backend service
4. Check logs for provider initialization errors

### Provider Not Available

1. Ensure provider template exists in `provider_templates.json`
2. Verify repository implementation is registered
3. Check that all required fields are configured

### API Connection Errors

1. Verify connection details (URL, credentials)
2. Test connection from API endpoint
3. Check provider-specific logs for authentication errors
4. Ensure firewall/network allows outbound connections

## Testing Provider Configuration

```bash
# Test via API
curl -X POST http://localhost:8099/api/settings/configure \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "servicenow",
    "settings": {
      "instance_url": "https://dev12345.service-now.com",
      "username": "admin",
      "password": "password123"
    }
  }'

# Get current config
curl http://localhost:8099/api/settings/current

# List providers
curl http://localhost:8099/api/settings/providers
```

## File Structure

```
app/
├── providers/
│   ├── __init__.py
│   ├── provider_config.py       # Configuration management
│   ├── provider_registry.py     # Provider factory
│   ├── provider_templates.json  # Provider definitions
│   └── provider_config.json     # Current configuration (generated)
├── repositories/
│   ├── __init__.py
│   ├── catalog_repository.py    # Abstract interface + ServiceNow impl
│   └── other_providers/         # Future: other provider implementations
├── routers/
│   └── settings.py              # Configuration API endpoints
├── mcp/
│   └── catalog_tools.py         # Uses provider registry
└── main.py                      # Includes settings router

frontend/
└── ProviderConfigChatbot.vue    # Configuration UI chatbot
```
