@echo off
set "BACKEND_ROOT=%~dp0..\.."
for %%I in ("%BACKEND_ROOT%") do set "BACKEND_ROOT=%%~fI"
set "REPO_ROOT=%BACKEND_ROOT%\.."
set "LOG_DIR=%REPO_ROOT%\logs\backend"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
cd /d "%BACKEND_ROOT%"
set "PYTHONPATH=%BACKEND_ROOT%"

:loop
echo [%date% %time%] Starting MCP Server...
python -m legacy.mcp_app.server >> "%LOG_DIR%\legacy_mcp_server.log" 2>> "%LOG_DIR%\legacy_mcp_server_err.log"
echo [%date% %time%] MCP Server exited with code %ERRORLEVEL%, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
