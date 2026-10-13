@echo off
set "BACKEND_ROOT=%~dp0..\.."
for %%I in ("%BACKEND_ROOT%") do set "BACKEND_ROOT=%%~fI"
set "REPO_ROOT=%BACKEND_ROOT%\.."
set "LOG_DIR=%REPO_ROOT%\logs\backend"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
cd /d "%BACKEND_ROOT%"
set "PYTHONPATH=%BACKEND_ROOT%"
echo MCP Server Starting...
python -m legacy.mcp_app.server 2>&1
echo MCP Server Exited with code %ERRORLEVEL%
