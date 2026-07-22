FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir -e .

# mcp-tools speaks MCP over stdio, so there's no port to expose — an MCP client
# launches the container with -i and talks JSON-RPC on stdin/stdout:
#
#   docker run -i --rm ghcr.io/egnaro9/mcp-tools
#
# To keep a trail of grade_answer / model_drift results, mount a directory and
# point MCPTOOLS_DB at a file inside it:
#
#   docker run -i --rm -v "$PWD/data:/data" -e MCPTOOLS_DB=/data/history.db mcp-tools
ENTRYPOINT ["python", "-m", "mcptools"]
