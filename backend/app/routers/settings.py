from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, Optional
import logging

from app.providers import provider_config
from app.providers.provider_registry import get_provider_instance

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ProviderConfigRequest(BaseModel):
    provider: str
    settings: Dict[str, Any]


class ProviderQuestion(BaseModel):
    id: str
    label: str
    type: str
    placeholder: Optional[str] = None
    required: bool
    help: Optional[str] = None


class ProviderTemplate(BaseModel):
    name: str
    description: str
    icon: str
    questions: list


@router.get("/providers")
async def list_providers():
    """List all available providers"""
    providers = provider_config.list_providers()
    return {
        "providers": providers,
        "current": provider_config.get_current_provider()
    }


@router.get("/providers/{provider_name}")
async def get_provider_template(provider_name: str):
    """Get provider template with questions"""
    template = provider_config.get_provider_template(provider_name)
    if not template:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")
    return template


@router.get("/current")
async def get_current_config():
    """Get current provider configuration"""
    return {
        "provider": provider_config.get_current_provider(),
        "settings": {k: v if k != "password" and k != "api_token" else "***" 
                     for k, v in provider_config.get_current_settings().items()}
    }


@router.post("/configure")
async def configure_provider(request: ProviderConfigRequest):
    """Configure a new provider"""
    try:
        # Validate provider exists
        template = provider_config.get_provider_template(request.provider)
        if not template:
            raise HTTPException(status_code=404, detail=f"Provider '{request.provider}' not found")
        
        # Validate required fields
        questions = template.get("questions", [])
        for question in questions:
            if question.get("required") and question["id"] not in request.settings:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required field: {question['label']}"
                )
        
        # Save configuration
        success = provider_config.save_config(request.provider, request.settings)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save configuration")
        
        # Force reload of provider instance
        try:
            _instance = get_provider_instance()
            logger.info(f"Provider '{request.provider}' configured successfully")
        except Exception as e:
            logger.error(f"Failed to initialize provider: {e}")
            raise HTTPException(status_code=500, detail=f"Provider initialization failed: {str(e)}")
        
        return {
            "success": True,
            "provider": request.provider,
            "message": f"Provider '{request.provider}' configured successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error configuring provider")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test-connection")
async def test_provider_connection(request: ProviderConfigRequest):
    """Test connection to configured provider"""
    try:
        # Validate provider
        template = provider_config.get_provider_template(request.provider)
        if not template:
            raise HTTPException(status_code=404, detail=f"Provider '{request.provider}' not found")
        
        # Validate required settings present before reporting status.
        questions = template.get("questions", [])
        for question in questions:
            if question.get("required") and question["id"] not in request.settings:
                return {
                    "success": False,
                    "error": f"Missing required field: {question['label']}"
                }

        # Real provider-specific connection tests are not yet implemented.
        # Returning 501 lets the UI show an honest "not implemented" badge
        # instead of a misleading green check.
        raise HTTPException(
            status_code=501,
            detail=f"Connection test for provider '{request.provider}' is not yet implemented.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error testing provider connection")
        return {
            "success": False,
            "error": str(e)
        }
