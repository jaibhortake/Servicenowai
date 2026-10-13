#!/usr/bin/env python
"""Start ServiceNow MCP Server with HTTP (SSE) transport on port 8091."""

import logging
import os
import sys

from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from servicenow_mcp.server_sse import ServiceNowSSEMCP
from servicenow_mcp.utils.config import AuthConfig, AuthType, BasicAuthConfig, ServerConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

def main():
    """Start the MCP server with HTTP transport."""
    load_dotenv()

    # Get configuration from environment
    instance_url = os.getenv("SERVICENOW_INSTANCE_URL", "https://demo.service-now.com")
    username = os.getenv("SERVICENOW_USERNAME", "admin")
    password = os.getenv("SERVICENOW_PASSWORD", "admin")

    logger.info(f"Starting MCP HTTP Server")
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

    # Create and start the MCP server with SSE transport
    mcp_server = ServiceNowSSEMCP(server_config)

    logger.info("Starting HTTP server on 0.0.0.0:8091")
    # Start on port 8091
    mcp_server.start(host="0.0.0.0", port=8091)

if __name__ == "__main__":
    main()