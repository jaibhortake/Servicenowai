"""
HTTP REST wrapper for ServiceNow MCP server.

Provides HTTP REST endpoints that wrap the MCP protocol,
allowing external services to call MCP tools via HTTP POST.
"""

import argparse
import json
import logging
import os
from typing import Any, Dict, Optional

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from servicenow_mcp.server import ServiceNowMCP
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_http_app(mcp_server: ServiceNowMCP) -> Starlette:
    """Create Starlette application with HTTP REST endpoints for MCP tools."""

    async def health(request: Request):
        """Health check endpoint."""
        return JSONResponse({"status": "ok"})

    async def call_tool(request: Request):
        """Call an MCP tool via HTTP POST."""
        tool_name = request.path_params.get("tool_name")
        try:
            body = await request.json() if request.method == "POST" else {}
            logger.info(f"Calling tool: {tool_name} with args: {body}")

            # Call the MCP tool
            result = await mcp_server.call_tool(tool_name, body)

            # Extract text content
            if result.isError:
                return JSONResponse({
                    "success": False,
                    "error": result.content[0].text if result.content else "Unknown error"
                }, status_code=400)
            else:
                text_result = ""
                if result.content:
                    text_result = result.content[0].text if hasattr(result.content[0], 'text') else str(result.content[0])

                # Try to parse as JSON, return as is if not JSON
                try:
                    parsed_result = json.loads(text_result) if isinstance(text_result, str) else text_result
                    return JSONResponse({
                        "success": True,
                        "result": parsed_result
                    })
                except (json.JSONDecodeError, ValueError):
                    return JSONResponse({
                        "success": True,
                        "result": text_result
                    })

        except Exception as e:
            logger.error(f"Tool call failed: {tool_name} - {str(e)}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    routes = [
        Route("/health", endpoint=health, methods=["GET"]),
        Route("/tools/{tool_name}", endpoint=call_tool, methods=["POST"]),
    ]

    return Starlette(routes=routes)


class ServiceNowHTTPMCP(ServiceNowMCP):
    """ServiceNow MCP with HTTP REST wrapper."""

    def __init__(self, config):
        super().__init__(config)

    async def call_tool(self, name: str, arguments: Dict[str, Any]):
        """Call an MCP tool and return structured result."""
        try:
            # Get the tool and call it
            response = await self.mcp_server.call_tool(name, arguments or {})
            return response
        except Exception as e:
            logger.error(f"Tool call failed: {name} - {str(e)}", exc_info=True)
            # Return error response
            from mcp.types import CallToolResult, TextContent
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=str(e))]
            )

    def start(self, host: str = "0.0.0.0", port: int = 8091):
        """Start HTTP REST wrapper server."""
        app = create_http_app(self)
        uvicorn.run(app, host=host, port=port)


def create_servicenow_http_mcp(instance_url: str, username: str, password: str):
    """Create ServiceNow MCP with HTTP wrapper."""
    auth_config = AuthConfig(
        type=AuthType.BASIC,
        basic=BasicAuthConfig(username=username, password=password)
    )
    config = ServerConfig(instance_url=instance_url, auth=auth_config)
    return ServiceNowHTTPMCP(config)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run ServiceNow MCP HTTP wrapper")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8091, help="Port to listen on")
    args = parser.parse_args()

    server = create_servicenow_http_mcp(
        instance_url=os.getenv("SERVICENOW_INSTANCE_URL"),
        username=os.getenv("SERVICENOW_USERNAME"),
        password=os.getenv("SERVICENOW_PASSWORD"),
    )
    server.start(host=args.host, port=args.port)


if __name__ == "__main__":
    main()