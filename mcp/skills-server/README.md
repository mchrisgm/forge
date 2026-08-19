# Forge skills MCP server

A stdio MCP server (PLAN §6.5) that gives coding sessions Claude Code-style
progressive disclosure of installed skills: the model sees only skill names
and descriptions until it explicitly loads one.

`server.py` is **stdlib-only Python** (no pip deps, no MCP SDK) so it runs via
the system `python3` inside session containers.

## Protocol

- Transport: MCP over **stdio** — newline-delimited JSON-RPC 2.0 messages on
  stdin/stdout; diagnostics go to stderr only.
- Protocol revision: `2024-11-05`; advertised capability: `tools` only.
- Handled methods: `initialize`, `notifications/initialized` (and all other
  notifications, ignored), `ping`, `tools/list`, `tools/call`. Unknown
  methods with an id get JSON-RPC `-32601`; unparseable lines get `-32700`.
- Unknown **tool** names are protocol errors (`-32602`); unknown **skill**
  names and other execution failures are MCP tool errors
  (`isError: true` results). Malformed `SKILL.md` files never crash the
  server — they degrade to the directory name with an empty description.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_skills` | none | JSON array of `{name, description}`, one entry per `$SKILLS_DIR/<dir>/SKILL.md`, parsed from the YAML frontmatter (`name:` / `description:`, tiny regex parser — plain, quoted, and folded `>-` scalars) |
| `load_skill` | `name` (string; frontmatter name or directory name) | The full `SKILL.md` contents plus a recursive file listing of the skill directory (`.git` skipped), so the agent can read support files directly from the mount |

Skills root: `$SKILLS_DIR`, default `/skills`.

## How it is mounted / launched

Primary path — **inside session containers** (no separate container):

- The session-runner image bakes the server in at `/opt/forge/skills_mcp.py`.
  Because the compose build context for that image is `./session-runner`, the
  Dockerfile embeds a verbatim copy of `server.py` in a `COPY <<heredoc`;
  **this file is the canonical source** — after editing it, regenerate/sync
  the embedded copy (the Dockerfile comment shows the `awk | diff` check).
- The orchestrator renders each session's `opencode.json`
  (`orchestrator/app/opencode_config.py`) with:

  ```json
  "skills": {"type": "local", "command": ["python3", "/opt/forge/skills_mcp.py"], "enabled": true}
  ```

  so OpenCode spawns it over stdio per session.
- The orchestrator mounts the `forge-skills` volume read-only at `/skills`
  in every session container; the installer (`skills_service.py`) clones
  skills into it as `/skills/<name>/SKILL.md` + support files.

Standalone (debugging / other MCP hosts), via the thin Dockerfile here:

```sh
docker build -t forge-skills-mcp mcp/skills-server
docker run -i --rm -v forge-skills:/skills:ro forge-skills-mcp
```

or directly: `SKILLS_DIR=./skills python3 server.py` and type JSON-RPC lines,
e.g. `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`.
