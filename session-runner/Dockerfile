# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Forge session-runner (PLAN §6.3): the per-session coding container image.
# OpenCode + toolchain; spawned by the orchestrator (session_manager.py) as a
# sibling container on forge-internal with /workspace bind-mounted and
# /skills mounted read-only. Requires BuildKit (default in Docker Engine 23+
# and docker compose v2) for the COPY <<heredoc below.
#
# Pinned versions (record of M5 research, 2026-08-19):
#   opencode-ai          1.18.18   (npm)
#   github-mcp-server    v1.9.0    (GitHub release binary, linux amd64+arm64)
#   uv / uvx             0.12.5    (ghcr.io/astral-sh/uv distroless image)
#   mcp-server-fetch     2026.8.18 (PyPI, needs python >=3.10)
#   mcp-searxng          0.1.0     (PyPI, needs python >=3.12 → trixie/3.13)
# ─────────────────────────────────────────────────────────────────────────────

# Pinned uv distribution (multi-arch; provides /uv and /uvx binaries).
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv-dist

# node 22 on Debian trixie: system python3 is 3.13, satisfying mcp-searxng's
# >=3.12 requirement without a uv-managed interpreter download.
FROM node:22-trixie-slim

# Set by BuildKit on `docker build`/`docker buildx build`; default keeps
# legacy builders working on x86_64.
ARG TARGETARCH=amd64

ARG OPENCODE_VERSION=1.18.18
ARG GITHUB_MCP_SERVER_VERSION=1.9.0
ARG GITHUB_MCP_SERVER_SHA256_AMD64=cbf38bd3364518ccf80b6a25587d5ef11655b15d63cbb48bc066384d0b5b5964
ARG GITHUB_MCP_SERVER_SHA256_ARM64=11e14ce34492b6a07ae4bc567d8773fc4cd3dd77e91daf3f9cacc88b15d840ea
ARG MCP_SERVER_FETCH_VERSION=2026.8.18
ARG MCP_SEARXNG_VERSION=0.1.0

ENV DEBIAN_FRONTEND=noninteractive

# Toolchain (PLAN §6.3): git, ssh client, python3, build-essential, ripgrep,
# curl, jq, GNU findutils (the orchestrator's file listing uses
# `find -printf`), gosu for the entrypoint's root→forge privilege drop, and
# acl for the /workspace default ACL the entrypoint sets.
RUN apt-get update && apt-get install -y --no-install-recommends \
        acl \
        build-essential \
        ca-certificates \
        curl \
        findutils \
        git \
        gosu \
        jq \
        openssh-client \
        procps \
        python3 \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# System-scope git defaults so the orchestrator's `docker exec` git endpoints
# (which run as root, the image's default user) work against the forge-owned
# /workspace: no "dubious ownership" refusals, and commits made through the
# UI get the forge identity unless overridden at global/local scope.
RUN git config --system --add safe.directory /workspace \
    && git config --system init.defaultBranch main \
    && git config --system user.name forge \
    && git config --system user.email forge@localhost

# astral uv + uvx, pinned via the uv-dist stage above.
COPY --from=uv-dist /uv /uvx /usr/local/bin/

# OpenCode agent engine (exact pin — PLAN §14 "OpenCode API drift").
RUN npm install -g "opencode-ai@${OPENCODE_VERSION}" && npm cache clean --force

# github-mcp-server: pinned release binary, checksum-verified, amd64 + arm64.
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) asset="Linux_x86_64"; sha256="${GITHUB_MCP_SERVER_SHA256_AMD64}" ;; \
        arm64) asset="Linux_arm64";  sha256="${GITHUB_MCP_SERVER_SHA256_ARM64}" ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/github-mcp-server.tar.gz \
        "https://github.com/github/github-mcp-server/releases/download/v${GITHUB_MCP_SERVER_VERSION}/github-mcp-server_${asset}.tar.gz"; \
    echo "${sha256}  /tmp/github-mcp-server.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/github-mcp-server.tar.gz -C /usr/local/bin github-mcp-server; \
    chmod 0755 /usr/local/bin/github-mcp-server; \
    rm -f /tmp/github-mcp-server.tar.gz; \
    github-mcp-server --help >/dev/null

# Non-root user (PLAN §7): uid 1000, name "forge", HOME=/home/forge. The node
# base image already owns uid 1000 as "node", so replace it.
RUN { userdel -r node 2>/dev/null || rm -rf /home/node; } \
    && useradd -m -u 1000 -U -s /bin/bash forge \
    && mkdir -p /workspace /skills /opt/forge \
    && chown forge:forge /workspace

ENV HOME=/home/forge \
    SKILLS_DIR=/skills \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/home/forge/.local/bin:$PATH

# Pre-warm the MCP servers that opencode.json launches with `uvx ...`:
# `uv tool install` (as forge) pins them into ~/.local/share/uv/tools, and
# uvx prefers an installed tool, so sessions never download at first use.
USER forge
RUN uv tool install "mcp-server-fetch==${MCP_SERVER_FETCH_VERSION}" \
    && uv tool install "mcp-searxng==${MCP_SEARXNG_VERSION}" \
    && uv tool list
USER root

# Skills MCP server, referenced by the rendered opencode.json as
# ["python3", "/opt/forge/skills_mcp.py"] (orchestrator/app/opencode_config.py).
# ┌──────────────────────────────────────────────────────────────────────────┐
# │ EMBEDDED COPY — canonical source: mcp/skills-server/server.py.           │
# │ The compose build context is ./session-runner, which cannot reach ../mcp,│
# │ so the file is inlined verbatim below. Keep the two in sync; verify with:│
# │   awk '/^COPY <<.__FORGE/{f=1;next} /^__FORGE/{f=0} f' Dockerfile \      │
# │     | diff - ../mcp/skills-server/server.py                              │
# └──────────────────────────────────────────────────────────────────────────┘
COPY <<'__FORGE_SKILLS_MCP_PY__' /opt/forge/skills_mcp.py
#!/usr/bin/env python3
"""Forge skills MCP server (PLAN §6.5).

A stdio Model Context Protocol server exposing installed Claude Code-format
skills to the agent with progressive disclosure:

  * list_skills()      -> JSON [{name, description}] from /skills/*/SKILL.md
                          YAML frontmatter (models see only names/descriptions)
  * load_skill(name)   -> the full SKILL.md body plus a recursive file listing
                          of the skill directory (so the agent can read support
                          files directly from the read-only /skills mount)

Deliberately stdlib-only: it runs via the system python3 inside session
containers (invoked by OpenCode as `python3 /opt/forge/skills_mcp.py`), so it
must not require pip, pyyaml, or an MCP SDK. The MCP stdio transport is
implemented by hand: newline-delimited JSON-RPC 2.0 messages on stdin/stdout,
protocol revision 2024-11-05, tools capability only.

The skills root comes from $SKILLS_DIR (default /skills). Malformed or
unreadable SKILL.md files never crash the server — they degrade to the
directory name with an empty description.
"""

from __future__ import annotations

import json
import os
import re
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "forge-skills"
SERVER_VERSION = "1.0.0"

DEFAULT_SKILLS_DIR = "/skills"

_FRONTMATTER_KEY_RE = re.compile(r"^(name|description)\s*:\s*(.*?)\s*$", re.IGNORECASE)

TOOLS = [
    {
        "name": "list_skills",
        "description": (
            "List the installed skills. Returns a JSON array of "
            "{name, description} objects. Call load_skill with a name to get "
            "a skill's full instructions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "load_skill",
        "description": (
            "Load one installed skill by name. Returns the skill's full "
            "SKILL.md instructions plus a recursive listing of the files in "
            "the skill's directory; support files can then be read directly "
            "from the listed paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name as returned by list_skills",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


def log(message: str) -> None:
    """Diagnostics go to stderr only — stdout is the MCP transport."""
    print(f"[{SERVER_NAME}] {message}", file=sys.stderr, flush=True)


def skills_root() -> str:
    return os.environ.get("SKILLS_DIR", DEFAULT_SKILLS_DIR)


def parse_frontmatter(text: str) -> dict:
    """Extract name:/description: from YAML frontmatter without pyyaml.

    Handles plain scalars, quoted scalars, and folded/literal blocks
    (`description: >-` followed by indented lines). Anything it cannot
    understand is simply ignored.
    """
    meta: dict[str, str] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() in ("---", "..."):
            break
        match = _FRONTMATTER_KEY_RE.match(line)
        if match:
            key = match.group(1).lower()
            value = match.group(2)
            if value in (">", ">-", ">+", "|", "|-", "|+", ""):
                # Block scalar: gather the following more-indented lines.
                block: list[str] = []
                j = i + 1
                while j < len(lines):
                    nxt = lines[j]
                    if nxt.strip() == "":
                        j += 1
                        continue
                    if nxt.startswith((" ", "\t")):
                        block.append(nxt.strip())
                        j += 1
                    else:
                        break
                value = " ".join(block)
                i = j - 1
            else:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
            meta.setdefault(key, value.strip())
        i += 1
    return meta


def scan_skills() -> list[dict]:
    """One entry per <skills_root>/<dir>/SKILL.md; never raises."""
    root = skills_root()
    skills: list[dict] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError as exc:
        log(f"cannot read skills dir {root}: {exc}")
        return skills
    for entry in entries:
        if entry in (".git",) or entry.startswith("."):
            continue
        skill_dir = os.path.join(root, entry)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        try:
            if not os.path.isdir(skill_dir) or not os.path.isfile(skill_md):
                continue
            with open(skill_md, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            meta = parse_frontmatter(text)
        except Exception as exc:  # malformed skill must never crash us
            log(f"skipping malformed metadata in {skill_md}: {exc}")
            text, meta = "", {}
        name = (meta.get("name") or entry).strip() or entry
        skills.append(
            {
                "name": name,
                "description": meta.get("description", ""),
                "dir": skill_dir,
                "dir_name": entry,
                "body": text,
            }
        )
    return skills


def find_skill(name: str) -> dict | None:
    skills = scan_skills()
    for skill in skills:
        if name in (skill["name"], skill["dir_name"]):
            return skill
    lowered = name.lower()
    for skill in skills:
        if lowered in (skill["name"].lower(), skill["dir_name"].lower()):
            return skill
    return None


def list_skill_files(skill_dir: str) -> list[str]:
    """Recursive relative file listing, skipping .git, sorted."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            found.append(os.path.relpath(full, skill_dir))
    return sorted(found)


def tool_list_skills() -> tuple[str, bool]:
    listing = [
        {"name": s["name"], "description": s["description"]} for s in scan_skills()
    ]
    return json.dumps(listing, indent=2), False


def tool_load_skill(name: str) -> tuple[str, bool]:
    skill = find_skill(name)
    if skill is None:
        known = ", ".join(s["name"] for s in scan_skills()) or "(none installed)"
        return (
            f"Unknown skill: {name!r}. Installed skills: {known}. "
            "Use list_skills to see names and descriptions.",
            True,
        )
    try:
        files = list_skill_files(skill["dir"])
    except OSError as exc:
        log(f"file listing failed for {skill['dir']}: {exc}")
        files = []
    body = skill["body"].rstrip() or "(SKILL.md is empty)"
    file_lines = "\n".join(files) or "(no files found)"
    text = (
        f"Skill: {skill['name']}\n"
        f"Directory: {skill['dir']}\n\n"
        f"--- SKILL.md ---\n{body}\n\n"
        f"--- Files under {skill['dir']} ---\n{file_lines}"
    )
    return text, False


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def send_result(msg_id, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def send_error(msg_id, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def handle_tools_call(msg_id, params: dict) -> None:
    tool = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    if tool == "list_skills":
        runner = lambda: tool_list_skills()  # noqa: E731
    elif tool == "load_skill":
        skill_name = arguments.get("name")
        if not isinstance(skill_name, str) or not skill_name.strip():
            send_result(
                msg_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "load_skill requires a non-empty string "
                            "argument 'name' (see list_skills).",
                        }
                    ],
                    "isError": True,
                },
            )
            return
        runner = lambda: tool_load_skill(skill_name.strip())  # noqa: E731
    else:
        # Unknown tool is a protocol-level error per MCP 2024-11-05.
        send_error(msg_id, -32602, f"Unknown tool: {tool}")
        return
    try:
        text, is_error = runner()
    except Exception as exc:  # tool execution errors -> isError result
        log(f"tool {tool} failed: {exc}")
        text, is_error = f"Tool {tool} failed: {exc}", True
    send_result(
        msg_id,
        {"content": [{"type": "text", "text": text}], "isError": is_error},
    )


def handle_message(msg: dict) -> None:
    method = msg.get("method")
    has_id = msg.get("id") is not None
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        send_result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    elif method == "ping":
        send_result(msg_id, {})
    elif method == "tools/list":
        send_result(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        handle_tools_call(msg_id, params)
    elif isinstance(method, str) and method.startswith("notifications/"):
        pass  # notifications (initialized, cancelled, ...) need no response
    elif has_id:
        send_error(msg_id, -32601, f"Method not found: {method}")
    # else: unknown notification — ignore silently


def main() -> int:
    log(f"serving skills from {skills_root()}")
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send_error(None, -32700, "Parse error")
            continue
        if not isinstance(msg, dict):
            send_error(None, -32600, "Invalid request: expected a JSON object")
            continue
        try:
            handle_message(msg)
        except BrokenPipeError:
            return 0
        except Exception as exc:  # never let one bad message kill the server
            log(f"error handling {msg.get('method')!r}: {exc}")
            if msg.get("id") is not None:
                try:
                    send_error(msg.get("id"), -32603, f"Internal error: {exc}")
                except Exception:
                    return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, BrokenPipeError):
        sys.exit(0)
__FORGE_SKILLS_MCP_PY__

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

WORKDIR /workspace
EXPOSE 4096

# Deliberately no USER directive: the entrypoint starts as root only to chown
# the bind-mounted /workspace, then gosu-drops to forge (uid 1000) before
# doing anything else. `opencode serve` runs as forge, PID 1.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
