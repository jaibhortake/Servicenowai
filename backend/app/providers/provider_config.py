import json
import os
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "provider_templates.json")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "provider_config.json")


def load_templates() -> Dict[str, Any]:
    """Load provider templates"""
    try:
        with open(TEMPLATES_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load provider templates: {e}")
        return {}


def load_config() -> Dict[str, Any]:
    """Load current provider configuration"""
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load provider config: {e}")
    return {"provider": None, "settings": {}}


def save_config(provider: str, settings: Dict[str, Any]) -> bool:
    """Save provider configuration and update environment variables"""
    try:
        config = {
            "provider": provider,
            "settings": settings
        }
        
        # Save to file
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        
        # Update environment variables
        templates = load_templates()
        if provider in templates:
            env_vars = templates[provider].get("env_vars", {})
            for setting_key, env_var in env_vars.items():
                if setting_key in settings:
                    os.environ[env_var] = str(settings[setting_key])
        
        logger.info(f"Provider configuration saved: {provider}")
        return True
    except Exception as e:
        logger.error(f"Failed to save provider config: {e}")
        return False


def get_provider_template(provider_name: str) -> Optional[Dict[str, Any]]:
    """Get a specific provider template"""
    templates = load_templates()
    return templates.get(provider_name)


def list_providers() -> Dict[str, Dict[str, Any]]:
    """List all available providers with metadata"""
    templates = load_templates()
    return {
        name: {
            "name": provider.get("name"),
            "description": provider.get("description"),
            "icon": provider.get("icon")
        }
        for name, provider in templates.items()
    }


def get_current_provider() -> Optional[str]:
    """Get currently configured provider"""
    config = load_config()
    return config.get("provider")


def get_current_settings() -> Dict[str, Any]:
    """Get current provider settings"""
    config = load_config()
    return config.get("settings", {})
