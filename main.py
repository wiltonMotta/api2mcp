"""SCNet OpenAPI MCP Server.

StreamableHTTP MCP server with AK/SK-based user authentication.
Each user connects at /mcp/{userName} and authenticates at /auth/{userName}.
"""

from __future__ import annotations

import hashlib
import hmac
import html as html_mod
import inspect
import json
import os
import re
import sqlite3
import time as time_mod
from typing import Annotated, Any, Optional

import httpx
from fastmcp import FastMCP
from fastmcp.server.http import _current_http_request
from pydantic import Field
from starlette.requests import Request
from starlette.responses import HTMLResponse

# P1-2: Module-level httpx client for connection reuse
def _get_http_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """Return a shared AsyncClient instance. Reuses the same connection pool."""
    if not hasattr(_get_http_client, "_client"):
        _get_http_client._client = httpx.AsyncClient(timeout=timeout)
    return _get_http_client._client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = os.environ.get("MCP_DB_PATH", "apis.db")

SCNET_TOKEN_URL = "https://api.scnet.cn/api/user/v3/tokens"
SCNET_CENTER_URL = "https://www.scnet.cn/ac/openapi/v2/center"
SCNET_USER_URL = "https://www.scnet.cn/ac/openapi/v2/user"

TYPE_MAP: dict[str, type] = {
    "integer": int,
    "number": float,
    "string": str,
    "boolean": bool,
    "object": dict,
    "array": list,
    "any": Any,
}

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS users (
    userName   TEXT PRIMARY KEY,
    acToken    TEXT,
    created_at datetime,
    updated_at datetime
);
CREATE TABLE IF NOT EXISTS user_cluster (
    userName    TEXT,
    clusterId   INTEGER,
    clusterName TEXT,
    homePath    TEXT,
    token       TEXT NOT NULL,
    created_at  datetime,
    updated_at  datetime,
    PRIMARY KEY (userName, clusterId)
);
CREATE TABLE IF NOT EXISTS cluster_url (
    clusterId   INTEGER PRIMARY KEY,
    clusterName TEXT,
    hpcUrls     TEXT,
    aiUrls      TEXT,
    efileUrls   TEXT,
    eshellUrls  TEXT
);
"""

AUTH_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCNet Authentication</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 440px; margin: 60px auto; padding: 24px; background: #f9fafb; }}
  .card {{ background: #fff; border-radius: 10px; padding: 32px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size: 22px; margin: 0 0 8px; }}
  .user {{ color: #6b7280; font-size: 14px; margin-bottom: 24px; }}
  label {{ display: block; margin-bottom: 16px; font-size: 14px; font-weight: 500; color: #374151; }}
  input {{ width: 100%; padding: 10px 12px; font-size: 15px; box-sizing: border-box;
          border: 1px solid #d1d5db; border-radius: 6px; margin-top: 4px; }}
  input:focus {{ outline: none; border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37,99,235,.15); }}
  button {{ width: 100%; padding: 11px; background: #2563eb; color: #fff; border: none;
           border-radius: 6px; font-size: 15px; font-weight: 600; cursor: pointer; margin-top: 8px; }}
  button:hover {{ background: #1d4ed8; }}
  .info {{ font-size: 13px; color: #6b7280; margin-bottom: 20px; line-height: 1.5; }}
  .error {{ color: #dc2626; font-size: 14px; margin-top: 12px; }}
  .btn-link {{ display: inline-block; width: 93%; padding: 11px; background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; font-weight: 500; text-align: center; text-decoration: none; cursor: pointer; margin-top: 15px;line-height: 15px; }}
  .btn-link:hover {{ background: #e5e7eb; }}
</style>
</head>
<body>
<div class="card">
  <h1>SCNet Authentication</h1>
  <p class="user">User: <strong>{username}</strong></p>
  <p class="info">Enter your SCNet Access Key and Secret Key.
     Find them in the personal center under <strong>Access Control</strong>.</p>
  <form method="POST" action="/auth/{username}">
    <label>Access Key
      <input type="text" name="accessKey" required autocomplete="off" placeholder="Your AK">
    </label>
    <label>Secret Key
      <input type="password" name="secretKey" required placeholder="Your SK">
    </label>{error_html}
    <button type="submit">Authenticate</button>
    <a class="btn-link" href='https://www.scnet.cn/ui/console/index.html#/personal/auth-manage' target="_blank">Get Access Key & Secret Key</a>
  </form>
</div>
</body>
</html>"""

SUCCESS_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authentication Successful</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 440px; margin: 60px auto; padding: 24px; background: #f9fafb;
         text-align: center; }}
  .card {{ background: #fff; border-radius: 10px; padding: 40px 32px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .check {{ font-size: 56px; margin-bottom: 16px; color: #16a34a; }}
  h1 {{ color: #16a34a; font-size: 24px; margin: 0 0 12px; }}
  p {{ color: #6b7280; line-height: 1.6; }}
  .clusters {{ text-align: left; margin-top: 20px; background: #f0fdf4; border-radius: 6px;
              padding: 12px 16px; font-size: 13px; }}
  .clusters li {{ margin: 4px 0; color: #374151; }}
</style>
</head>
<body>
<div class="card">
  <div class="check">&#10004;</div>
  <h1>Authentication Successful</h1>
  <p>User <strong>{username}</strong> is now authenticated.<br>
     You can close this page and use SCNet MCP tools.</p>
  {cluster_info}
</div>
</body>
</html>"""

ERROR_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><title>Authentication Failed</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 440px; margin: 60px auto; padding: 24px; background: #f9fafb;
         text-align: center; }}
  .card {{ background: #fff; border-radius: 10px; padding: 40px 32px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .icon {{ font-size: 56px; margin-bottom: 16px; }}
  h1 {{ color: #dc2626; font-size: 24px; margin: 0 0 12px; }}
  p {{ color: #6b7280; margin-bottom: 20px; }}
  a {{ color: #2563eb; text-decoration: none; font-weight: 500; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="card">
  <div class="icon">&#10008;</div>
  <h1>Authentication Failed</h1>
  <p>{message}</p>
  <a href="/auth/{username}">Try again</a>
</div>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

# Round-robin URL index context for P1-3 (replaces random.choice)
_url_idx_ctx: dict[str, int] = {}

mcp = FastMCP("SCNet OpenAPI MCP Server", on_duplicate="ignore")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(MIGRATION_SQL)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auth utilities
# ---------------------------------------------------------------------------

AUTH_BASE_URL = "https://c-2056205187675406338.qdai.scnet.cn:58043"


def check_auth(username: str) -> sqlite3.Row | dict:
    """Check whether a user is authenticated.

    Returns the database ``Row`` on success (with a valid ``acToken``),
    or an error ``dict`` with ``error=True``, ``message``, and
    ``auth_url`` fields when not authenticated.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT acToken FROM users WHERE userName = ?", (username,)
        ).fetchone()
    finally:
        conn.close()

    if row is None or row["acToken"] is None:
        return {
            "error": True,
            "message": (
                f"用户 '{username}' 未认证。"
                "请先访问认证页面获取访问凭证。"
            ),
            "auth_url": f"{AUTH_BASE_URL}/auth/{username}",
        }
    return row


def get_current_username() -> str:
    request = _current_http_request.get()
    if request is None:
        raise RuntimeError("No HTTP request context — not running in streamable-http mode")
    return request.path_params.get("username", "")


def hmac_sha256_sign(secret_key: str, payload: dict) -> str:
    """Compute HMAC-SHA256 signature using modern hmac.HMAC API."""
    sorted_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hmac.HMAC(
        secret_key.encode("utf-8"),
        sorted_json.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Existing proxy-tool infrastructure (unchanged)
# ---------------------------------------------------------------------------

def load_apis(db_path: str = DB_PATH) -> list[tuple[str, dict]]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT name, document FROM APIs")
        rows = cursor.fetchall()
    finally:
        conn.close()

    apis: list[tuple[str, dict]] = []
    for name, document in rows:
        try:
            apis.append((name, json.loads(document)))
        except json.JSONDecodeError as exc:
            print(f"[warn] skipping API {name!r}: invalid JSON document ({exc})")
    return apis


def _format_schema_lines(schema: dict) -> list[str]:
    lines: list[str] = []
    for key, info in schema.items():
        type_name = info.get("type", "any")
        optional = " (optional)" if info.get("optional") else ""
        desc = info.get("description", "")
        lines.append(f"  - {key} [{type_name}]{optional}: {desc}")
    return lines


def build_description(doc: dict) -> str:
    parts: list[str] = []
    if doc.get("description"):
        parts.append(doc["description"].strip())

    parts.append(f"HTTP {doc.get('method', 'GET').upper()} {doc.get('url', '')}")

    params = doc.get("parameters") or {}
    schema = params.get("schema") or {}
    if schema:
        parts.append(f"Parameters ({params.get('format', 'URLParameter')}):")
        parts.extend(_format_schema_lines(schema))

    returns = doc.get("returns") or {}
    return_schema = returns.get("schema") or {}
    if return_schema:
        parts.append(f"Returns ({returns.get('format', 'JSON')}):")
        parts.extend(_format_schema_lines(return_schema))

    return "\n".join(parts)


def _python_type(type_name: str) -> Any:
    return TYPE_MAP.get(type_name, str)


def _safe_tool_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"api_{cleaned}"
    return cleaned


def make_proxy_tool(name: str, doc: dict):
    url_template: str = doc["url"]
    method: str = doc.get("method", "GET").upper()
    params_def: dict = doc.get("parameters") or {}
    param_format: str = params_def.get("format", "URLParameter")
    schema: dict = params_def.get("schema") or {}

    sig_params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    for pname, pinfo in schema.items():
        py_type = _python_type(pinfo.get("type", "string"))
        description = pinfo.get("description", "")
        optional = bool(pinfo.get("optional"))

        if optional:
            annotation = Annotated[Optional[py_type], Field(default=None, description=description)]
            parameter = inspect.Parameter(
                pname, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=annotation,
            )
        else:
            annotation = Annotated[py_type, Field(description=description)]
            parameter = inspect.Parameter(
                pname, inspect.Parameter.KEYWORD_ONLY, annotation=annotation,
            )

        sig_params.append(parameter)
        annotations[pname] = annotation

    annotations["return"] = Any

    async def proxy(**kwargs: Any) -> Any:
        url = url_template
        path_kwargs: dict[str, Any] = {}
        remaining: dict[str, Any] = {}

        for key, value in kwargs.items():
            if value is None:
                continue
            if f":{key}" in url:
                path_kwargs[key] = value
            else:
                remaining[key] = value

        for key, value in path_kwargs.items():
            url = url.replace(f":{key}", str(value))

        query_params: dict[str, Any] | None = None
        json_body: Any = None
        fmt = param_format.lower()

        if fmt in ("urlparameter", "pathparameter"):
            query_params = remaining or None
        elif fmt in ("queryparameter", "query"):
            query_params = remaining or None
        elif fmt in ("json", "jsonbody", "body"):
            json_body = remaining if remaining else None
        else:
            query_params = remaining or None

        client = _get_http_client(timeout=30.0)
        response = await client.request(
            method=method, url=url, params=query_params, json=json_body,
        )
        try:
            return response.json()
        except ValueError:
            return {"status_code": response.status_code, "text": response.text}

    proxy.__name__ = _safe_tool_name(name)
    proxy.__qualname__ = proxy.__name__
    proxy.__doc__ = build_description(doc)
    proxy.__signature__ = inspect.Signature(parameters=sig_params)
    proxy.__annotations__ = annotations
    return proxy


def register_apis(server: FastMCP, db_path: str = DB_PATH) -> int:
    count = 0
    for name, doc in load_apis(db_path):
        fn = make_proxy_tool(name, doc)
        server.tool(name=_safe_tool_name(name), description=build_description(doc))(fn)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Custom HTTP routes — auth page
# ---------------------------------------------------------------------------

@mcp.custom_route("/auth/{username}", methods=["GET"])
async def auth_page(request: Request) -> HTMLResponse:
    username = html_mod.escape(request.path_params["username"])
    return HTMLResponse(AUTH_PAGE_HTML.format(username=username, error_html=""))


@mcp.custom_route("/auth/{username}", methods=["POST"])
async def auth_submit(request: Request) -> HTMLResponse:
    username = request.path_params["username"]
    safe_username = html_mod.escape(username)

    form = await request.form()
    access_key = form.get("accessKey", "").strip()
    secret_key = form.get("secretKey", "").strip()

    if not access_key or not secret_key:
        return HTMLResponse(
            AUTH_PAGE_HTML.format(
                username=safe_username,
                error_html='<p class="error">Both Access Key and Secret Key are required.</p>',
            ),
            status_code=400,
        )

    # 1. Get tokens from SCNet using AK/SK
    timestamp = str(int(time_mod.time()))
    signature = hmac_sha256_sign(secret_key, {
        "accessKey": access_key, "timestamp": timestamp, "user": username,
    })

    headers = {
        "accessKey": access_key,
        "signature": signature,
        "user": username,
        "timestamp": timestamp,
    }

    client = _get_http_client(timeout=30.0)
    try:
        resp = await client.post(SCNET_TOKEN_URL, headers=headers, json={})
        resp.raise_for_status()
        token_data = resp.json()

        # Validate SCNet API response code
        api_code = str(token_data.get("code", ""))
        if api_code != "0":
            api_msg = token_data.get("msg", "Unknown error")

            if "不存在" in api_msg:
                hint = (
                    "The Access Key / Secret Key pair was not found. "
                    "Please verify: (1) The keys were copied correctly from the "
                    "Access Control page without extra spaces or line breaks. "
                    "(2) Access Key goes in the first field and Secret Key in the second. "
                    "(3) The keys have not been revoked or regenerated."
                )
            elif "非本人" in api_msg:
                hint = (
                    "The AK/SK you provided belong to a different SCNet user, "
                    f"not <strong>{html_mod.escape(username)}</strong>. "
                    "Please use the AK/SK that matches this account."
                )
            else:
                hint = f"SCNet API error: {html_mod.escape(api_msg)} (code={api_code})"

            return HTMLResponse(
                ERROR_PAGE_HTML.format(
                    username=safe_username,
                    message=hint,
                ),
                status_code=400,
            )
    except httpx.HTTPStatusError as exc:
        return HTMLResponse(
            ERROR_PAGE_HTML.format(
                username=safe_username,
                message=f"SCNet API error (HTTP {exc.response.status_code}): "
                        f"{exc.response.text[:300]}",
            ),
            status_code=502,
        )
    except Exception as exc:
        return HTMLResponse(
            ERROR_PAGE_HTML.format(
                username=safe_username,
                message=f"Request failed: {html_mod.escape(str(exc))}",
            ),
            status_code=502,
        )

    # 2. Parse clusters from response
    clusters = token_data.get("data", token_data)
    if isinstance(clusters, dict):
        clusters = clusters.get("clusters", [])
    if not isinstance(clusters, list):
        clusters = [clusters] if clusters else []

    now = time_mod.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        other_clusters: list[dict] = []

        for cluster in clusters:
            cid = cluster.get("clusterId", 0)
            cname = cluster.get("clusterName", "")
            token = cluster.get("token")

            if cid == 0 or cname == "ac":
                conn.execute(
                    "INSERT OR REPLACE INTO users(userName, acToken, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (username, token, now, now),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO user_cluster "
                    "(userName, clusterId, clusterName, token, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (username, cid, cname, token, now, now),
                )
                other_clusters.append({"clusterId": cid, "clusterName": cname, "token": token})

        conn.commit()
    finally:
        conn.close()

    # Verify that the acToken was actually stored
    conn = get_db()
    try:
        user_row = conn.execute(
            "SELECT acToken FROM users WHERE userName = ?", (username,)
        ).fetchone()
    finally:
        conn.close()

    if user_row is None:
        return HTMLResponse(
            ERROR_PAGE_HTML.format(
                username=safe_username,
                message="Authentication failed: no platform token (ac) was returned. "
                        "Make sure your Access Key and Secret Key belong to this user.",
            ),
            status_code=400,
        )

    # 3. For each non-ac cluster, call get-center-info
    cluster_names: list[str] = []
    client = _get_http_client(timeout=30.0)
    for cl in other_clusters:
        try:
            ci_resp = await client.get(
                SCNET_CENTER_URL,
                headers={"token": cl["token"], "Content-Type": "application/json"},
            )
            ci_resp.raise_for_status()
            ci_data = ci_resp.json()
        except Exception:
            continue

        ci_payload = ci_data.get("data", ci_data)
        home_path = (
            ci_payload.get("clusterUserInfo", {}).get("homePath", "")
            if isinstance(ci_payload, dict)
            else ""
        )

        def _join_urls(url_list: list | None) -> str:
            if not url_list:
                return ""
            enabled = [u["url"] for u in url_list if isinstance(u, dict) and u.get("enable")]
            return ",".join(enabled)

        hpc_urls = _join_urls(ci_payload.get("hpcUrls")) if isinstance(ci_payload, dict) else ""
        ai_urls = _join_urls(ci_payload.get("aiUrls")) if isinstance(ci_payload, dict) else ""
        efile_urls = _join_urls(ci_payload.get("efileUrls")) if isinstance(ci_payload, dict) else ""
        eshell_urls = _join_urls(ci_payload.get("eshellUrls")) if isinstance(ci_payload, dict) else ""

        conn = get_db()
        try:
            conn.execute(
                "UPDATE user_cluster SET homePath = ?, updated_at = ? "
                "WHERE userName = ? AND clusterId = ?",
                (home_path, now, username, cl["clusterId"]),
            )
            conn.execute(
                "INSERT OR REPLACE INTO cluster_url(clusterId, clusterName, hpcUrls, aiUrls, efileUrls, eshellUrls) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cl["clusterId"], cl["clusterName"], hpc_urls, ai_urls, efile_urls, eshell_urls),
            )
            conn.commit()
        finally:
            conn.close()

        cluster_names.append(cl["clusterName"])

    # 4. Build success page
    if cluster_names:
        cluster_li = "".join(f"<li>{html_mod.escape(c)}</li>" for c in cluster_names)
        cluster_info = (
            f'<div class="clusters"><strong>Authorized clusters:</strong><ul>{cluster_li}</ul></div>'
        )
    else:
        cluster_info = ""

    return HTMLResponse(
        SUCCESS_PAGE_HTML.format(username=safe_username, cluster_info=cluster_info)
    )


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

def _infer_type(value: Any) -> str:
    """Map a Python value to an OpenAPI-like type string."""
    if value is None:
        return "any"
    py_type = type(value).__name__
    type_map = {
        "int": "integer", "float": "number", "str": "string",
        "bool": "boolean", "list": "array", "dict": "object",
    }
    return type_map.get(py_type, "string")


def _build_return_schema(data: Any) -> dict | None:
    """Build a returns.schema dict from sample data keys.

    Handles nested dicts/lists and provides a default schema for empty data.
    Internal helper keys (_sub_schema, _element_type) are stripped from the
    returned dict so they don't leak into the MCP client schema.
    """
    schema: dict = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                schema[key] = {
                    "type": "object",
                    "description": key,
                    "optional": True,
                }
            elif isinstance(value, list):
                schema[key] = {
                    "type": "array",
                    "description": key,
                    "optional": True,
                }
            else:
                schema[key] = {
                    "type": _infer_type(value),
                    "description": key,
                    "optional": True,
                }
    # Strip internal helper keys in case they leaked in
    for v in schema.values():
        v.pop("_sub_schema", None)
        v.pop("_element_type", None)
    return schema if schema else {
        "status": {"type": "string", "description": "Response status", "optional": False},
    }


@mcp.tool()
async def get_user_info() -> dict:
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    token = auth_result["acToken"]

    # P1-2: Use shared client
    client = _get_http_client(timeout=30.0)
    try:
        resp = await client.get(SCNET_USER_URL, headers={"token": token})
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"SCNet API 错误 (HTTP {exc.response.status_code}): {exc.response.text[:300]}",
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"请求异常: {exc}",
        }

    # Auto-generate document and store in APIs table
    doc = {
        "url": SCNET_USER_URL,
        "method": "GET",
        "description": (
            "Get current user's SCNet account information including "
            "country, language, timeZone, address, fullName, userName, "
            "computerCenter, accountName, accountStatus, accountBalance"
        ),
        "parameters": {"format": "URLParameter", "schema": {}},
        "returns": {"format": "JSON", "schema": _build_return_schema(data)},
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("get_user_info", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return data


@mcp.tool()
async def list_available_partitions() -> list[dict]:
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return [auth_result]

    # Get all clusters for this user with their URLs
    conn = get_db()
    try:
        clusters = conn.execute(
            "SELECT uc.clusterId, uc.clusterName, uc.token, "
            "cu.hpcUrls "
            "FROM user_cluster uc "
            "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
            "WHERE uc.userName = ?",
            (username,),
        ).fetchall()
    finally:
        conn.close()

    if not clusters:
        return []

    results: list[dict] = []

    for cluster in clusters:
        cid = cluster["clusterId"]
        cname = cluster["clusterName"]
        token = cluster["token"]
        hpc_urls = cluster["hpcUrls"]

        if not hpc_urls:
            continue

        # P0-4: Filter empty URLs
        valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
        if not valid_urls:
            continue

        # Round-robin via index counter
        _url_idx = _url_idx_ctx.get(str(cid), 0)
        base_url = valid_urls[_url_idx % len(valid_urls)]
        _url_idx_ctx[str(cid)] = _url_idx + 1

        cluster_result: dict = {"clusterId": cid, "clusterName": cname}

        try:
            # 1. Get cluster info → extract strJobManagerID
            client = _get_http_client(timeout=30.0)
            ci_resp = await client.get(
                f"{base_url}/hpc/openapi/v2/cluster",
                headers={"token": token, "Content-Type": "application/json"},
            )
            ci_resp.raise_for_status()
            ci_data = ci_resp.json()

            if not isinstance(ci_data, dict):
                continue

            cluster_list = ci_data.get("data", ci_data)
            if isinstance(cluster_list, list) and cluster_list:
                job_manager_id = str(cluster_list[0].get("id", ""))
            elif isinstance(cluster_list, dict):
                job_manager_id = str(cluster_list.get("id", ""))
            else:
                job_manager_id = ""

            if not job_manager_id:
                continue

            cluster_result["jobManagerID"] = job_manager_id

            # 2. Get user queues
            queue_url = (
                f"{base_url}/hpc/openapi/v2/queuenames/users/{username}"
                f"?strJobManagerID={job_manager_id}"
            )
            q_resp = await client.get(
                queue_url,
                headers={"token": token, "Content-Type": "application/json"},
            )
            q_resp.raise_for_status()
            q_data = q_resp.json()

            if not isinstance(q_data, dict):
                continue

            queues = q_data.get("data", q_data)
            if isinstance(queues, list):
                queues = [
                    q for q in queues
                    if isinstance(q, dict) and q.get("queFreeNcpus", 0) != 0
                ]
                for q in queues:
                    q.pop("aclHosts", None)
            elif isinstance(queues, dict):
                if queues.get("queFreeNcpus", 0) == 0:
                    queues = []
                else:
                    queues.pop("aclHosts", None)

            if not queues:
                continue

            cluster_result["queues"] = queues
            results.append(cluster_result)

        except Exception:
            continue

    # Auto-generate document
    sample_schema = _build_return_schema(results[0] if results else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/queuenames/users/{username}",
        "method": "GET",
        "description": (
            "List available queue partitions across all clusters for the current user. "
            "Returns queue details with clusterId, clusterName, and jobManagerID metadata."
        ),
        "parameters": {"format": "URLParameter", "schema": {}},
        "returns": {"format": "JSON", "schema": sample_schema},
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("list_available_partitions", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return results


@mcp.tool()
async def submit_job(
    clusterId: int,
    queueName: str,
    GAP_CMD_FILE: str,
    GAP_NNODE: Annotated[Optional[str], Field(description="节点个数。与 GAP_NODE_STRING 互斥，指定 GAP_NNODE 时 GAP_NODE_STRING 必须为 ''")] = None,
    GAP_NODE_STRING: Annotated[Optional[str], Field(description="指定具体节点。与 GAP_NNODE 互斥，指定 GAP_NODE_STRING 时 GAP_NNODE 必须为 ''")] = None,
    GAP_WALL_TIME: Annotated[Optional[str], Field(description="最大运行时长，格式 HH:MM:ss。默认 24:00:00")] = None,
    GAP_NPROC: Annotated[Optional[str], Field(description="总核心数（GAP_NPROC 和 GAP_PPN 选其一填写")] = None,
    GAP_PPN: Annotated[Optional[str], Field(description="CPU 核心/节点（GAP_NPROC 和 GAP_PPN 选其一填写")] = None,
    GAP_NGPU: Annotated[Optional[str], Field(description="GPU 卡数/节点")] = None,
    GAP_NDCU: Annotated[Optional[str], Field(description="DCU 卡数/节点")] = None,
    GAP_JOB_MEM: Annotated[Optional[str], Field(description="每个节点内存值，单位 MB 或 GB")] = None,
    GAP_EXCLUSIVE: Annotated[Optional[str], Field(description="是否独占节点，1 为独占，空字符串为非独占")] = None,
    GAP_WORK_DIR: Annotated[Optional[str], Field(description="工作路径。若未提供，默认为 user_cluster 表中该用户的 homePath 拼接 _job_YYYY_mm_dd_HHiiss")] = None,
    GAP_APPNAME: Annotated[Optional[str], Field(description="BASE（基础应用），支持填写具体的应用英文名称。默认 BASE")] = None,
    GAP_MULTI_SUB: Annotated[Optional[str], Field(description="作业组长度，建议为小于等于 50 的正整数")] = None,
    GAP_STD_OUT_FILE: Annotated[Optional[str], Field(description="标准输出文件路径。若未提供，默认为工作路径/std.out.%j")] = None,
    GAP_STD_ERR_FILE: Annotated[Optional[str], Field(description="标准错误文件路径。若未提供，默认为工作路径/std.err.%j")] = None,
) -> dict:
    username = get_current_username()

    # --- P1-5: Parameter validation ---

    # B03: Reject empty GAP_CMD_FILE
    if not GAP_CMD_FILE or not GAP_CMD_FILE.strip():
        return {
            "error": True,
            "message": "GAP_CMD_FILE（作业命令）不能为空。",
        }

    # Mutual exclusion: GAP_NNODE vs GAP_NODE_STRING
    if GAP_NNODE is not None and GAP_NODE_STRING is not None:
        if GAP_NNODE.strip() and GAP_NODE_STRING.strip():
            return {
                "error": True,
                "message": "GAP_NNODE 和 GAP_NODE_STRING 互斥，不能同时填写。"
                           "请只选择一种方式指定节点：指定数量（GAP_NNODE）或指定具体节点（GAP_NODE_STRING）。",
            }

    # Mutual exclusion: GAP_NPROC vs GAP_PPN
    if GAP_NPROC is not None and GAP_PPN is not None:
        if GAP_NPROC.strip() and GAP_PPN.strip():
            return {
                "error": True,
                "message": "GAP_NPROC 和 GAP_PPN 互斥，不能同时填写。"
                           "请只选择一种方式指定核心数：总核心数（GAP_NPROC）或每节点核心数（GAP_PPN）。",
            }

    # 1. Auth check
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # 2. Get cluster token + hpcUrls from user_cluster + cluster_url
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT uc.token, uc.clusterName, cu.hpcUrls, cu.clusterName AS url_clusterName, "
            "uc.homePath "
            "FROM user_cluster uc "
            "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
            "WHERE uc.userName = ? AND uc.clusterId = ?",
            (username, clusterId),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {
            "error": True,
            "message": (
                f"未在集群 clusterId={clusterId} 中找到您的认证凭证。"
                "请先调用 list_available_partitions 获取可用队列，然后选择有效的集群。"
            ),
        }
    if row["token"] is None:
        return {
            "error": True,
            "message": (
                f"未在集群 clusterId={clusterId} 中找到您的认证凭证。"
                " 请先调用 list_available_partitions 获取可用队列，然后选择有效的集群。"
            ),
        }

    if not row["hpcUrls"]:
        return {
            "error": True,
            "message": (
                f"集群 {row.get('clusterName', clusterId)} 未配置 HPC 服务 URL。"
                "请联系管理员配置集群信息。"
            ),
        }

    token = row["token"]
    hpc_urls = row["hpcUrls"]
    home_path = row["homePath"] or ""

    # P0-2: Reject empty homePath to prevent path traversal
    if not home_path:
        return {
            "error": True,
            "message": "用户 homePath 未配置，请联系管理员配置集群信息。",
        }

    # 3. Get jobManagerID from cluster API
    valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {
            "error": True,
            "message": "集群未配置有效的 HPC 服务 URL。",
        }
    _url_idx = _url_idx_ctx.get(str(clusterId), 0)
    base_url = valid_urls[_url_idx % len(valid_urls)]
    _url_idx_ctx[str(clusterId)] = _url_idx + 1
    job_manager_id = None

    try:
        client = _get_http_client(timeout=30.0)
        ci_resp = await client.get(
            f"{base_url}/hpc/openapi/v2/cluster",
            headers={"token": token, "Content-Type": "application/json"},
        )
        ci_resp.raise_for_status()
        ci_data = ci_resp.json()

        if not isinstance(ci_data, dict):
            raise ValueError("Unexpected cluster API response format")

        cluster_list = ci_data.get("data", ci_data)
        if isinstance(cluster_list, list) and cluster_list:
            job_manager_id = str(cluster_list[0].get("id", ""))
        elif isinstance(cluster_list, dict):
            job_manager_id = str(cluster_list.get("id", ""))
        else:
            job_manager_id = ""
    except Exception:
        return {
            "error": True,
            "message": (
                f"无法从集群 {row.get('clusterName', clusterId)} 获取调度器信息。"
                "请确认集群服务正常运行。"
            ),
        }

    if not job_manager_id:
        return {
            "error": True,
            "message": (
                f"集群 {row.get('clusterName', clusterId)} 未返回有效的 jobManagerID。"
                "无法提交作业。"
            ),
        }

    # 4. Fill defaults
    default_nnode = "1"
    default_nodestring = ""
    default_walltime = "24:00:00"
    default_appname = "BASE"
    default_workdir = f"{home_path}/_job_{time_mod.strftime('%Y_%m_%d_%H%M%S')}"
    default_std_out = f"{default_workdir}/std.out.%j" if default_workdir else ""
    default_std_err = f"{default_workdir}/std.err.%j" if default_workdir else ""

    nnode = GAP_NNODE or default_nnode
    node_string = GAP_NODE_STRING or default_nodestring
    wall_time = GAP_WALL_TIME or default_walltime
    appname = GAP_APPNAME or default_appname

    # Generate job name from GAP_CMD_FILE
    # Extract a meaningful name from the command
    cmd_short = GAP_CMD_FILE.strip()
    # Try to get the first word/command as a hint
    first_word = re.match(r'(\w+)', cmd_short)
    prefix = first_word.group(1).strip().replace(" ", "_") if first_word else "job"
    # Truncate if too long, keep alphanumeric + underscore
    prefix = re.sub(r'[^a-zA-Z0-9_]', '', prefix)[:15]
    if not prefix:
        prefix = "job"
    job_name = f"{prefix}_{time_mod.strftime('%m%d_%H%M%S')}"

    work_dir = GAP_WORK_DIR or default_workdir
    std_out_file = GAP_STD_OUT_FILE or default_std_out
    std_err_file = GAP_STD_ERR_FILE or default_std_err

    # 5. Build nested request body per official API spec
    map_app_job_info: dict[str, Any] = {
        "GAP_CMD_FILE": GAP_CMD_FILE,
        "GAP_NNODE": nnode,
        "GAP_NODE_STRING": node_string,
        "GAP_SUBMIT_TYPE": "cmd",
        "GAP_JOB_NAME": job_name,
        "GAP_WORK_DIR": work_dir,
        "GAP_QUEUE": queueName,
        "GAP_WALL_TIME": wall_time,
        "GAP_APPNAME": appname,
        # Optional fields — only include if provided
        "GAP_NPROC": GAP_NPROC or "",
        "GAP_PPN": GAP_PPN or "",
        "GAP_NGPU": GAP_NGPU or "",
        "GAP_NDCU": GAP_NDCU or "",
        "GAP_JOB_MEM": GAP_JOB_MEM or "",
        "GAP_EXCLUSIVE": GAP_EXCLUSIVE or "",
        "GAP_MULTI_SUB": GAP_MULTI_SUB or "",
        "GAP_STD_OUT_FILE": std_out_file,
        "GAP_STD_ERR_FILE": std_err_file,
    }

    request_body: dict[str, Any] = {
        "apptype": "BASIC",
        "appname": appname,
        "strJobManagerID": int(job_manager_id),
        "mapAppJobInfo": map_app_job_info,
    }

    # 6. Submit job
    submit_url = f"{base_url}/hpc/openapi/v2/apptemplates/BASIC/{appname}/job"

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            submit_url,
            headers={"token": token, "Content-Type": "application/json"},
            json=request_body,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        error_text = exc.response.text[:500]
        try:
            error_data = exc.response.json()
        except ValueError:
            error_data = {}
        return {
            "error": True,
            "message": (
                f"作业提交失败 (HTTP {exc.response.status_code})。"
                f"详情: {error_text}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"作业提交请求异常: {exc}",
        }

    # 7. Auto-register document in APIs table
    returns_schema = _build_return_schema(result)
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/apptemplates/{apptype}/{appname}/job",
        "method": "POST",
        "description": (
            "向 HPC 集群提交一个作业。调用前需先通过 list_available_partitions 获取可用队列信息，"
            "并从中选择最合适的队列。后端会自动处理认证、集群凭据获取、调度器 ID 获取、"
            "默认值填充以及作业名称生成。"
        ),
        "parameters": {
            "format": "JSON",
            "schema": {
                "clusterId": {"type": "integer", "description": "从 list_available_partitions 返回结果中选定的集群 ID", "optional": False},
                "queueName": {"type": "string", "description": "从选定集群的 queues 列表中选择的目标队列名称", "optional": False},
                "GAP_CMD_FILE": {"type": "string", "description": "作业要执行的命令行内容", "optional": False},
                "GAP_NNODE": {"type": "string", "description": "节点个数", "optional": True},
                "GAP_NODE_STRING": {"type": "string", "description": "指定具体节点", "optional": True},
                "GAP_WALL_TIME": {"type": "string", "description": "最大运行时长，格式 HH:MM:ss", "optional": True},
                "GAP_NPROC": {"type": "string", "description": "总核心数", "optional": True},
                "GAP_PPN": {"type": "string", "description": "CPU核心/节点", "optional": True},
                "GAP_NGPU": {"type": "string", "description": "GPU卡数/节点", "optional": True},
                "GAP_NDCU": {"type": "string", "description": "DCU卡数/节点", "optional": True},
                "GAP_JOB_MEM": {"type": "string", "description": "每个节点内存值，单位 MB 或 GB", "optional": True},
                "GAP_EXCLUSIVE": {"type": "string", "description": "是否独占节点", "optional": True},
                "GAP_WORK_DIR": {"type": "string", "description": "工作路径", "optional": True},
                "GAP_APPNAME": {"type": "string", "description": "BASE（基础应用）", "optional": True},
                "GAP_MULTI_SUB": {"type": "string", "description": "作业组长度", "optional": True},
                "GAP_STD_OUT_FILE": {"type": "string", "description": "标准输出文件路径", "optional": True},
                "GAP_STD_ERR_FILE": {"type": "string", "description": "标准错误文件路径", "optional": True},
            },
        },
        "returns": {
            "format": "JSON",
            "schema": returns_schema or {"status": {"type": "string", "description": "作业提交状态", "optional": False}},
        },
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("submit_job", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    # 返回结果中加入 token 和 hpcUrls
    if isinstance(result, dict):
        result["token"] = token
        result["hpcUrls"] = hpc_urls

    return result


@mcp.tool()
async def get_running_job_detail(
    jobId: Annotated[str, Field(description="作业 ID，可从 submit_job 返回的 jobID 字段获取")],
    clusterId: Annotated[Optional[int], Field(description="可选：集群 ID。精确匹配可减少查询失败概率。如果省略，后端会自动遍历用户有权限的所有集群尝试查询。")] = None,
    token: Annotated[Optional[str], Field(description="可选：集群 token（可从 submit_job 返回的 token 字段获取）。如果省略，后端从数据库自动获取。")] = None,
    hpcUrls: Annotated[Optional[str], Field(description="可选：集群 hpcUrls（可从 submit_job 返回的 hpcUrls 字段获取）。如果省略，后端从数据库自动获取。")] = None,
) -> dict:
    username = get_current_username()

    # 1. Auth check
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # 2. Resolve token and hpcUrls
    #   Priority: explicit args > DB (with optional clusterId match)
    effective_token = token
    effective_hpc_urls = hpcUrls

    if clusterId is not None:
        # Precise match: look up the specified cluster
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT uc.token, cu.hpcUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return {
                "error": True,
                "message": (
                    f"未在集群 clusterId={clusterId} 中找到您的认证凭证。"
                    "请先调用 list_available_partitions 获取可用队列。"
                ),
            }
        if row["token"] is None and effective_token is None:
            return {
                "error": True,
                "message": (
                    f"未在集群 clusterId={clusterId} 中找到您的认证凭证。"
                    "请先调用 list_available_partitions 获取可用队列。"
                ),
            }
        if row["hpcUrls"] is None and effective_hpc_urls is None:
            return {
                "error": True,
                "message": (
                    f"集群 {clusterId} 未配置 HPC 服务 URL。"
                    "请联系管理员配置集群信息。"
                ),
            }

        if effective_token is None:
            effective_token = row["token"]
        if effective_hpc_urls is None:
            effective_hpc_urls = row["hpcUrls"]
    else:
        # Fallback: fetch all clusters for this user
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT uc.clusterId, uc.token, cu.hpcUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ?",
                (username,),
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return {
                "error": True,
                "message": (
                    "未查询到任何集群的认证凭证 token。"
                    "请先调用 list_available_partitions 获取可用队列。"
                ),
            }

        if effective_token is None and effective_hpc_urls is None:
            # Use the first valid cluster as default
            for r in rows:
                if r["token"] and r["hpcUrls"]:
                    effective_token = r["token"]
                    effective_hpc_urls = r["hpcUrls"]
                    break
            else:
                return {
                    "error": True,
                    "message": (
                        "所有集群均缺少有效的 token 或 hpcUrls。"
                        "请先调用 list_available_partitions。"
                    ),
                }
        elif effective_token is None:
            for r in rows:
                if r["token"]:
                    effective_token = r["token"]
                    break
        elif effective_hpc_urls is None:
            for r in rows:
                if r["hpcUrls"]:
                    effective_hpc_urls = r["hpcUrls"]
                    break

    # 3. Query job detail — try each cluster URL in turn
    base_urls = [u.strip().rstrip("/") for u in effective_hpc_urls.split(",") if u.strip()]
    if not base_urls:
        return {
            "error": True,
            "message": "未找到有效的 HPC 服务 URL。",
        }

    last_err: Exception | None = None
    result: dict | None = None
    client = _get_http_client(timeout=30.0)
    for base_url in base_urls:
        job_detail_url = f"{base_url}/hpc/openapi/v2/jobs/{jobId}"
        try:
            resp = await client.get(
                job_detail_url,
                headers={"token": effective_token, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            result = resp.json()
            break
        except httpx.HTTPStatusError as exc:
            last_err = exc
            continue
        except Exception as exc:
            last_err = exc
            continue
    else:
        # All URLs failed
        if isinstance(last_err, httpx.HTTPStatusError):
            error_text = last_err.response.text[:500]
            return {
                "error": True,
                "message": (
                    f"查询作业 {jobId} 失败 (HTTP {last_err.response.status_code})。"
                    f"详情: {error_text}"
                ),
                "status_code": last_err.response.status_code,
            }
        return {
            "error": True,
            "message": f"查询作业请求异常: {last_err}",
        }

    # 4. Auto-register document in APIs table
    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/jobs/{jobId}",
        "method": "GET",
        "description": (
            "查询 HPC 集群中指定作业的实时详细信息。调用前需先通过 list_available_partitions "
            "获取可用队列信息，选择正确的集群和 jobId。后端会自动处理认证和集群信息。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "jobId": {"type": "string", "description": "作业 ID，可从 submit_job 返回的 jobID 字段获取", "optional": False},
                "clusterId": {"type": "integer", "description": "可选：集群 ID，用于精确匹配", "optional": True},
                "token": {"type": "string", "description": "可选：token，可从 submit_job 返回的 token 字段获取", "optional": True},
                "hpcUrls": {"type": "string", "description": "可选：hpcUrls，可从 submit_job 返回的 hpcUrls 字段获取", "optional": True},
            },
        },
        "returns": {
            "format": "JSON",
            "schema": returns_schema or {
                "jobId": {"type": "string", "description": "作业 ID", "optional": False},
                "jobStatus": {"type": "string", "description": "作业状态", "optional": False},
            },
        },
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("get_running_job_detail", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return result


@mcp.tool()
async def get_history_job_detail(
    jobId: Annotated[str, Field(description="作业 ID，可从 submit_job 返回的 jobID 字段获取")],
    jobmanagerId: Annotated[str, Field(description="调度器 ID（可从 list_available_partitions 返回结果中获取）")],
    acctTime: Annotated[Optional[str], Field(description="入账时间（结束时间），建议传入以提升查询性能，格式 YYYY-MM-DD HH:MM:SS")] = None,
    token: Annotated[Optional[str], Field(description="可选：集群 token（可从 submit_job 返回的 token 字段获取）。如果省略，后端从数据库自动获取。")] = None,
) -> dict:
    username = get_current_username()

    # 1. Auth check
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # 2. Resolve token from explicit param > DB
    effective_token = token
    if effective_token is None:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT uc.token "
                "FROM user_cluster uc "
                "WHERE uc.userName = ?",
                (username,),
            ).fetchone()
        finally:
            conn.close()

        if row is None or row["token"] is None:
            return {
                "error": True,
                "message": (
                    "未查询到集群认证凭证 token。"
                    "请先调用 list_available_partitions 获取可用队列。"
                ),
            }
        effective_token = row["token"]

    # 3. Get hpcUrls from DB
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT cu.hpcUrls "
            "FROM user_cluster uc "
            "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
            "WHERE uc.userName = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not row["hpcUrls"]:
        return {
            "error": True,
            "message": (
                "未查询到集群 HPC 服务 URL。"
                "请先调用 list_available_partitions 获取可用队列。"
            ),
        }
    hpc_urls = row["hpcUrls"]

    # 4. Query — try each cluster URL in turn
    base_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not base_urls:
        return {
            "error": True,
            "message": "未找到有效的 HPC 服务 URL。",
        }

    last_err: Exception | None = None
    result: dict | None = None
    client = _get_http_client(timeout=30.0)

    # Build query params — only include if provided
    query_params: dict[str, Any] | None = None
    if acctTime:
        query_params = {"acctTime": acctTime}

    for base_url in base_urls:
        history_job_url = (
            f"{base_url}/hpc/openapi/v2/historyjobs/{jobmanagerId}/{jobId}"
        )
        try:
            resp = await client.get(
                history_job_url,
                headers={"token": effective_token, "Content-Type": "application/json"},
                params=query_params,
            )
            resp.raise_for_status()
            result = resp.json()
            break
        except httpx.HTTPStatusError as exc:
            last_err = exc
            continue
        except Exception as exc:
            last_err = exc
            continue
    else:
        if isinstance(last_err, httpx.HTTPStatusError):
            error_text = last_err.response.text[:500]
            return {
                "error": True,
                "message": (
                    f"查询历史作业 {jobId} 失败 (HTTP {last_err.response.status_code})。"
                    f"详情: {error_text}"
                ),
                "status_code": last_err.response.status_code,
            }
        return {
            "error": True,
            "message": f"查询历史作业请求异常: {last_err}",
        }

    # 5. Auto-register document in APIs table
    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/historyjobs/{jobmanagerId}/{jobId}",
        "method": "GET",
        "description": (
            "查询 HPC 集群中指定历史作业（已完成/已终止）的详细信息。"
            "调用前需先通过 list_available_partitions 获取可用队列信息和 jobManagerID，"
            "并从提交结果中获取 jobId。后端会自动处理认证和集群信息。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "jobId": {"type": "string", "description": "作业 ID，可从 submit_job 返回的 jobID 字段获取", "optional": False},
                "jobmanagerId": {"type": "string", "description": "调度器 ID，可从 list_available_partitions 返回结果中获取", "optional": False},
                "acctTime": {"type": "string", "description": "入账时间（结束时间），建议传入以提升查询性能，格式 YYYY-MM-DD HH:MM:SS", "optional": True},
                "token": {"type": "string", "description": "可选：token，可从 submit_job 返回的 token 字段获取", "optional": True},
            },
        },
        "returns": {
            "format": "JSON",
            "schema": returns_schema or {
                "jobId": {"type": "string", "description": "作业 ID", "optional": False},
                "jobName": {"type": "string", "description": "作业名称", "optional": False},
                "jobState": {"type": "string", "description": "作业状态", "optional": False},
                "jobStartTime": {"type": "string", "description": "开始时间", "optional": False},
                "jobEndTime": {"type": "string", "description": "结束时间", "optional": False},
                "jobMemUsed": {"type": "number", "description": "已用内存(MB)", "optional": True},
                "jobCpuTime": {"type": "number", "description": "CPU时间(秒)", "optional": True},
                "jobExecHost": {"type": "string", "description": "执行节点", "optional": True},
            },
        },
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("get_history_job_detail", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _register_history_job_detail_doc() -> None:
    """注册 get_history_job_detail 的文档到 APIs 表（启动时执行）。"""
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/historyjobs/{jobmanagerId}/{jobId}",
        "method": "GET",
        "description": (
            "查询 HPC 集群中指定历史作业（已完成/已终止）的详细信息。"
            "调用前需先通过 list_available_partitions 获取可用队列信息和 jobManagerID，"
            "并从提交结果中获取 jobId。后端会自动处理认证和集群信息。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "jobId": {"type": "string", "description": "作业 ID，可从 submit_job 返回的 jobID 字段获取", "optional": False},
                "jobmanagerId": {"type": "string", "description": "调度器 ID，可从 list_available_partitions 返回结果中获取", "optional": False},
                "acctTime": {"type": "string", "description": "入账时间（结束时间），建议传入以提升查询性能，格式 YYYY-MM-DD HH:MM:SS", "optional": True},
                "token": {"type": "string", "description": "可选：token，可从 submit_job 返回的 token 字段获取", "optional": True},
            },
        },
        "returns": {
            "format": "JSON",
            "schema": {
                "acctTime": {"type": "string", "description": "入账时间", "optional": True},
                "jobId": {"type": "string", "description": "作业 ID", "optional": False},
                "jobmanagerId": {"type": "number", "description": "调度器 ID", "optional": False},
                "jobmanagerName": {"type": "string", "description": "集群名称", "optional": True},
                "userName": {"type": "string", "description": "用户名", "optional": True},
                "jobName": {"type": "string", "description": "作业名称", "optional": True},
                "queue": {"type": "string", "description": "队列名称", "optional": True},
                "jobQueueTime": {"type": "string", "description": "排队开始时间", "optional": True},
                "jobStartTime": {"type": "string", "description": "开始时间", "optional": False},
                "jobEndTime": {"type": "string", "description": "结束时间", "optional": False},
                "jobExitStatus": {"type": "number", "description": "退出状态码", "optional": True},
                "jobCpuTime": {"type": "number", "description": "CPU时间(秒)", "optional": True},
                "jobMemUsed": {"type": "number", "description": "已用内存(MB)", "optional": True},
                "jobExecHost": {"type": "string", "description": "执行节点", "optional": True},
                "jobState": {"type": "string", "description": "作业状态", "optional": True},
            },
        },
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("get_history_job_detail", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

# Register existing DB proxy tools at import time
_count = register_apis(mcp)
if _count:
    print(f"[mcp] registered {_count} proxy API tool(s) from {DB_PATH}")

# Pre-register built-in tool docs so they appear in tool list immediately
_register_history_job_detail_doc()
print("[mcp] pre-registered get_history_job_detail in APIs table")


def main() -> None:
    if not os.path.exists(DB_PATH):
        raise SystemExit(
            f"Database not found at {DB_PATH!r}. Run `python init_db.py` first."
        )

    migrate_db()

    mcp.run(
        transport="streamable-http",
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "8000")),
        path="/mcp/{username}",
    )


if __name__ == "__main__":
    main()
