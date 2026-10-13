@echo off
rem Starts backend (8099), MCP bridge (8091) and frontend (5200) in separate windows.
rem Requires: backend\.venv, servicenow-mcp\.venv, frontend\node_modules, Ollama and Neo4j running.
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%backend\.venv\Scripts\python.exe" (echo Missing backend\.venv - see README & exit /b 1)
if not exist "%ROOT%servicenow-mcp\.venv\Scripts\python.exe" (echo Missing servicenow-mcp\.venv - see README & exit /b 1)
if not exist "%ROOT%frontend\node_modules" (echo Missing frontend\node_modules - run npm install & exit /b 1)

start "Backend :8099" /D "%ROOT%backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8099"
start "MCP :8091" /D "%ROOT%servicenow-mcp" cmd /k "set MCP_REST_HOST=127.0.0.1&& set MCP_REST_PORT=8091&& .venv\Scripts\python.exe mcp_rest_server.py"
start "Frontend :5200" /D "%ROOT%frontend" cmd /k "npm run dev"

echo Started. Open http://localhost:5200
endlocal
