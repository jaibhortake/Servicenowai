FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .

ENV MCP_REST_HOST=0.0.0.0 MCP_REST_PORT=8091
EXPOSE 8091
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8091/health',timeout=3)" || exit 1

CMD ["python", "mcp_rest_server.py"]
