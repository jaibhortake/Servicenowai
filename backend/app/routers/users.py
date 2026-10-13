from fastapi import APIRouter, HTTPException, Query
from typing import List
from app.models.catalog import UserProfile
from app.services import servicenow as snow
from app.services.mcp_client_service import mcp_client_service
import asyncio
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=List[UserProfile])
async def list_users(limit: int = Query(100, ge=1, le=1000)):
    try:
        users = snow.list_users(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")
    return [_map_user(user) for user in users]


@router.post("/login")
async def login(email: str = Query(..., description="User email address")):
    """Login endpoint that ensures MCP is available when user enters email."""
    logger.info(f"User login attempt: {email}")

    try:
        user = snow.get_user_by_email(email)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")

    if not user:
        raise HTTPException(status_code=404, detail="User not found in ServiceNow")

    # Trigger MCP connection on login
    if not mcp_client_service.is_available():
        logger.info(f"MCP not available, triggering reconnection for user {email}")
        try:
            await asyncio.wait_for(mcp_client_service.startup(), timeout=5.0)
            logger.info(f"✓ MCP reconnected successfully for user {email}")
        except asyncio.TimeoutError:
            logger.warning(f"MCP reconnection timed out for user {email}, continuing without MCP")
        except Exception as e:
            logger.warning(f"MCP reconnection failed for user {email}: {str(e)}, continuing without MCP")
    else:
        logger.info(f"MCP already available for user {email}")

    mcp_status = await mcp_client_service.status()
    user_data = _map_user(user)

    return {
        "user": user_data,
        "mcp_status": mcp_status
    }


@router.get("/me", response_model=UserProfile)
async def get_me(email: str = Query(..., description="User email address")):
    try:
        user = snow.get_user_by_email(email)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow error: {str(e)}")
    if not user:
        raise HTTPException(status_code=404, detail="User not found in ServiceNow")
    return _map_user(user)


@router.get("/{sys_id}", response_model=UserProfile)
async def get_user(sys_id: str):
    try:
        user = snow.get_user_by_id(sys_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _map_user(user)


def _map_user(u: dict) -> UserProfile:
    def _val(f):
        if isinstance(f, dict):
            return f.get("display_value") or f.get("value")
        return str(f) if f else None

    def _link_id(f):
        if isinstance(f, dict):
            link = f.get("link", "")
            return link.split("/")[-1] if link else None
        return None

    dept = u.get("department", {})
    mgr = u.get("manager", {})

    return UserProfile(
        sys_id=u.get("sys_id", ""),
        name=u.get("name", ""),
        email=u.get("email", ""),
        department=_val(dept),
        department_id=_link_id(dept),
        manager=_val(mgr),
        manager_id=_link_id(mgr),
        location=_val(u.get("location")),
        title=u.get("title", ""),
    )
