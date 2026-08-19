# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Forge skills MCP server — standalone image (thin wrapper).
#
# Primary use is NOT this image: the server is stdlib-only Python and is baked
# into the session-runner image at /opt/forge/skills_mcp.py, where OpenCode
# launches it over stdio (["python3", "/opt/forge/skills_mcp.py"]). This
# Dockerfile exists so the server can also run standalone, e.g. for debugging
# or for wiring into another MCP host:
#
#   docker build -t forge-skills-mcp mcp/skills-server
#   docker run -i --rm -v forge-skills:/skills:ro forge-skills-mcp
#
# It speaks MCP over stdio, so run it with -i (stdin open) and no TTY.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

# Same non-root convention as the rest of Forge (uid 1000, PLAN §7).
RUN useradd -m -u 1000 -U -s /usr/sbin/nologin forge && mkdir -p /skills

COPY server.py /opt/forge/skills_mcp.py

USER forge
ENV SKILLS_DIR=/skills
ENTRYPOINT ["python3", "/opt/forge/skills_mcp.py"]
