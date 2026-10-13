#!/usr/bin/env python
"""
REST Server for MCP Tools

Wraps the ServiceNow MCP server and exposes MCP tools via REST endpoints.
Provides /tools/{tool_name} endpoints for easy HTTP integration.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from servicenow_mcp.server import ServiceNowMCP
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global MCP server instance
mcp_server: Optional[ServiceNowMCP] = None

# Create FastAPI app
app = FastAPI(
    title="MCP REST Server",
    description="REST wrapper for ServiceNow MCP tools",
    version="1.0.0"
)


@app.on_event("startup")
async def startup_event():
    """Initialize MCP server on startup."""
    global mcp_server

    load_dotenv()

    # Get configuration from environment
    instance_url = os.getenv("SERVICENOW_INSTANCE_URL", "https://demo.service-now.com")
    username = os.getenv("SERVICENOW_USERNAME", "admin")
    password = os.getenv("SERVICENOW_PASSWORD", "admin")

    logger.info(f"Starting MCP REST Server")
    logger.info(f"ServiceNow Instance: {instance_url}")
    logger.info(f"Username: {username}")

    # Create authentication config
    basic_auth = BasicAuthConfig(username=username, password=password)
    auth_config = AuthConfig(type=AuthType.BASIC, basic=basic_auth)

    # Create server config
    server_config = ServerConfig(
        instance_url=instance_url,
        auth=auth_config,
        debug=True,
        timeout=30,
    )

    # Create the MCP server
    mcp_server = ServiceNowMCP(server_config)
    logger.info("MCP server initialized and ready to serve REST requests")


@app.post("/tools/{tool_name}")
async def call_tool(tool_name: str, request: Request) -> JSONResponse:
    """
    Call an MCP tool via REST.

    POST /tools/{tool_name}
    Body: JSON arguments for the tool

    Returns: {success: bool, result: str, error: str|null}
    """
    global mcp_server

    if not mcp_server:
        raise HTTPException(status_code=503, detail="MCP server not initialized")

    try:
        # Parse request body
        try:
            arguments = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in request body")

        logger.info(f"REST call: /tools/{tool_name} with args: {arguments}")

        # Call the tool implementation directly
        try:
            # Run the blocking tool call in a thread pool to prevent blocking the event loop
            # The _call_tool_impl is async but calls synchronous tool functions internally
            tool_result_content = await mcp_server._call_tool_impl(tool_name, arguments)

            # tool_result_content is a list of TextContent objects
            # Extract text from all content blocks
            text_content = ""
            for block in tool_result_content:
                if hasattr(block, 'text'):
                    text_content += block.text
                elif isinstance(block, dict) and block.get('type') == 'text':
                    text_content += block.get('text', '')

            # Try to parse as JSON if it looks like JSON
            try:
                result_json = json.loads(text_content)
                result_str = json.dumps(result_json)
            except json.JSONDecodeError:
                result_str = text_content

            return JSONResponse(content={
                "success": True,
                "result": result_str,
                "error": None
            })

        except Exception as e:
            logger.error(f"Tool execution failed: {e}", exc_info=True)
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "result": None,
                    "error": str(e)
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Request processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.head("/sse")
async def sse_health_head():
    """Health check for SSE compatibility (for backends expecting SSE servers)."""
    return JSONResponse(content={"status": "ok"})


@app.get("/sse")
async def sse_health():
    """Health check for SSE compatibility (for backends expecting SSE servers)."""
    return {"status": "ok", "service": "mcp-rest-server"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok" if mcp_server else "not ready", "service": "mcp-rest-server"}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "MCP REST Server",
        "version": "1.0.0",
        "status": "ready" if mcp_server else "initializing",
        "endpoints": {
            "/tools/{tool_name}": "POST - Call MCP tool",
            "/health": "GET - Health check"
        }
    }


if __name__ == "__main__":
    port_value = os.getenv("MCP_REST_PORT") or os.getenv("PORT")
    if not port_value:
        raise RuntimeError("MCP_REST_PORT or PORT must be configured")
    host = os.getenv("MCP_REST_HOST") or os.getenv("HOST") or "0.0.0.0"
    port = int(port_value)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
