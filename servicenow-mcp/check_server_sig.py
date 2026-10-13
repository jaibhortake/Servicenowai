#!/usr/bin/env python
"""Check the Server.call_tool signature."""

import inspect
from mcp.server.lowlevel import Server

# Get the signature of call_tool
sig = inspect.signature(Server.call_tool)
print(f"Signature: {sig}")

# Get the actual method signature with annotations
method = Server.call_tool
print(f"Type: {type(method)}")
print(f"Is async: {inspect.iscoroutinefunction(method)}")
print(f"Is coroutine: {inspect.iscoroutinefunction(method)}")

# List all parameters
print("\nParameters:")
for name, param in sig.parameters.items():
    print(f"  {name}: {param}")

# Get the source if possible
try:
    source = inspect.getsource(Server.call_tool)
    print("\nSource:")
    print(source[:500])
except:
    pass