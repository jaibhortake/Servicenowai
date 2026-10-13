"""
HTTP REST wrapper for MCP tools.

Translates HTTP REST calls to MCP tool invocations.
Provides endpoints like /tools/{tool_name} for easy integration.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="MCP HTTP Wrapper", version="1.0.0")

# Connection to the upstream MCP SSE server
MCP_SSE_URL = os.getenv("MCP_SSE_URL")
MCP_SESSION_ID = None
HTTP_CLIENT: Optional[httpx.AsyncClient] = None


@app.on_event("startup")
async def startup():
    """Initialize HTTP client."""
    global HTTP_CLIENT
    if not MCP_SSE_URL:
        raise RuntimeError("MCP_SSE_URL must be configured")
    HTTP_CLIENT = httpx.AsyncClient(timeout=30.0)
    logger.info(f"HTTP wrapper started, connecting to MCP server at {MCP_SSE_URL}")


@app.on_event("shutdown")
async def shutdown():
    """Close HTTP client."""
    global HTTP_CLIENT
    if HTTP_CLIENT:
        await HTTP_CLIENT.aclose()


async def initialize_mcp_session():
    """Initialize MCP session via SSE."""
    global MCP_SESSION_ID, HTTP_CLIENT

    if MCP_SESSION_ID:
        return MCP_SESSION_ID

    if not HTTP_CLIENT:
        raise RuntimeError("HTTP client not initialized")

    try:
        # Send initialize request
        init_message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-http-wrapper", "version": "1.0.0"}
            }
        }

        response = await HTTP_CLIENT.post(
            f"{MCP_SSE_URL}/messages/",
            json=init_message,
            timeout=10.0
        )
        response.raise_for_status()

        result = response.json()
        logger.info(f"MCP initialization response: {result}")
        MCP_SESSION_ID = "active"
        return MCP_SESSION_ID
    except Exception as e:
        logger.error(f"Failed to initialize MCP session: {e}")
        raise


async def call_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call an MCP tool via the SSE server.

    Uses the JSON-RPC protocol to invoke tools.
    """
    global HTTP_CLIENT

    if not HTTP_CLIENT:
        raise RuntimeError("HTTP client not initialized")

    try:
        # Ensure session is initialized
        await initialize_mcp_session()

        # Build JSON-RPC call
        rpc_call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        logger.info(f"Calling MCP tool: {tool_name} with args: {arguments}")

        response = await HTTP_CLIENT.post(
            f"{MCP_SSE_URL}/messages/",
            json=rpc_call,
            timeout=30.0
        )
        response.raise_for_status()

        result = response.json()
        logger.debug(f"MCP tool response: {result}")

        # Extract the tool result
        if "result" in result:
            return {
                "success": True,
                "result": json.dumps(result["result"]),
                "error": None
            }
        elif "error" in result:
            return {
                "success": False,
                "result": None,
                "error": result["error"].get("message", "Unknown error")
            }
        else:
            return {
                "success": True,
                "result": json.dumps(result),
                "error": None
            }
    except Exception as e:
        logger.error(f"MCP tool call failed: {e}", exc_info=True)
        return {
            "success": False,
            "result": None,
            "error": str(e)
        }


@app.post("/tools/{tool_name}")
async def call_tool(tool_name: str, request: Request) -> JSONResponse:
    """
    Call an MCP tool via REST.

    POST /tools/{tool_name}
    Body: JSON arguments for the tool

    Returns: {success: bool, result: str|null, error: str|null}
    """
    try:
        body = await request.json()
        logger.info(f"REST call: /tools/{tool_name} with body: {body}")

        result = await call_mcp_tool(tool_name, body)
        return JSONResponse(content=result)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")
    except Exception as e:
        logger.error(f"Error calling tool {tool_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "mcp-http-wrapper"}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "MCP HTTP Wrapper",
        "version": "1.0.0",
        "upstream_mcp": MCP_SSE_URL,
        "endpoints": {
            "/tools/{tool_name}": "POST - Call MCP tool",
            "/health": "GET - Health check"
        }
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8091,
        log_level="info"
    )
