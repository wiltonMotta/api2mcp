# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

SCNet OpenAPI MCP Server — a StreamableHTTP server that exposes HTTP API endpoints as dynamically generated MCP tools. Each user connects at `/mcp/{username}` and authenticates via AK/SK at `/auth/{username}`.

## Commands

```bash
# Set up the database (creates apis.db from schema + example seed data)
python init_db.py

# Create DB without example data
python init_db.py --no-seed

# Run the MCP server (default: 0.0.0.0:8000)
python main.py

# Run with custom host/port
MCP_HOST=127.0.0.1 MCP_PORT=9000 python main.py

# Run the example client (in-process mode)
python example_client.py

# Run the example client (stdio subprocess mode)
python example_client.py --stdio

# Manage server process (stop + restart)
bash restartMcp.sh  # 重启服务，端口 8002
```

Set `MCP_DB_PATH` to override the default SQLite database path (`apis.db`).

## Architecture

**Single file: `main.py`** — Everything lives here. The file is organized into these sections:

1. **Constants** (lines 27–176) — DB path, external URLs, `TYPE_MAP` for JSON→Python type conversion, HTML templates for auth pages.
2. **Database helpers** (lines 178–197) — `get_db()` returns a `sqlite3.Connection` with `row_factory=sqlite3.Row`. `migrate_db()` creates auth-related tables on existing databases.
3. **Auth utilities** (lines 199–217) — `get_current_username()` reads the username from the HTTP request path; `hmac_sha256_sign()` produces signatures for SCNet API auth.
4. **Proxy-tool infrastructure** (lines 220–367) — The dynamic tool system:
   - `load_apis()` reads `(name, document)` rows from the `APIs` table.
   - `make_proxy_tool(name, doc)` builds an async function whose signature is derived from the document's parameter schema using `inspect.Parameter` + Pydantic `Annotated`/`Field`. At call time it substitutes `:path_params`, sends remaining params as query or JSON body, and returns the parsed response via `httpx`.
   - `register_apis()` iterates all DB rows and registers each as an MCP tool. Runs at import time.
5. **Custom HTTP routes** (lines 370–580) — GET/POST `/auth/{username}` — HTML form for AK/SK input, then authenticates against SCNet, stores tokens in `users` and `user_cluster` tables, and fetches cluster service URLs.
6. **Built-in MCP tools** (lines 582–789) — `get_user_info` and `list_available_partitions` are registered as regular MCP tools but also auto-generate their own `APIs` table entries for tool discovery.

**Document JSON schema** (stored in the `document` column of the `APIs` table):
- `url` — URL template with `:param` placeholders for path parameters
- `method` — HTTP method (GET, POST, etc.)
- `parameters.format` — Controls parameter delivery: `URLParameter`/`PathParameter`/`QueryParameter` → query string; `JSON`/`Body` → request body.
- `parameters.schema` — Dict of `{name: {type, description, optional}}`
- `returns.schema` — Dict describing the response shape (used for MCP tool description only)

**Database** (`apis.db`, SQLite):
- `APIs(name, document)` — Tool definitions. `name` is the MCP tool name.
- `users(userName, acToken, created_at, updated_at)` — Auth state. `acToken` is NULL until authentication completes.
- `user_cluster(userName, clusterId, clusterName, homePath, token, created_at, updated_at)` — Per-cluster tokens.
- `cluster_url(clusterId, clusterName, hpcUrls, aiUrls, efileUrls, eshellUrls)` — Discovered service URLs.

**`schema.sql`** — Base schema (creates the 4 tables).

**`init_db.py`** — Recreates `apis.db` from `schema.sql`, optionally seeding with `example_data.sql`. Supports `--migrate-only` to add auth tables to an existing DB without dropping it.

**`example_data.sql`** — Seed data: 4 tools proxying jsonplaceholder.typicode.com endpoints, demonstrating URLParameter, QueryParameter, and JSON parameter formats.

**`example_client.py`** — Client demonstrating StreamableHTTP transport connection and tool invocation.

**`instruct/`** — Generated documentation files produced by the built-in tools (auto-generated on each tool execution).
