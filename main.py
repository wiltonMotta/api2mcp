"""SCNet OpenAPI MCP Server.

StreamableHTTP MCP server with AK/SK-based user authentication.
Each user connects at /mcp/{userName} and authenticates at /auth/{userName}.
"""

from __future__ import annotations

import base64
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
from urllib.parse import urlencode

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
    userName   TEXT PRIMARY KEY,r
    acToken    TEXT,
    created_at datetime,
    updated_at datetime
);
CREATE TABLE IF NOT EXISTS user_cluster (
    userName        TEXT,
    clusterId       INTEGER,
    clusterName     TEXT,
    homePath        TEXT,
    token           TEXT NOT NULL,
    isDefault       boolean,
    JobManagerType  TEXT,
    JobManagerAddr  TEXT,
    JobManagerid    TEXT,
    JobManagertext  TEXT,
    JobManagerPort  TEXT,
    created_at      datetime,
    updated_at      datetime,
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


def _efile_url(base_url: str, path: str) -> str:
    """Join a base efile URL with an API path, stripping duplicate /efile prefix."""
    base = base_url.rstrip("/")
    if base.endswith("/efile"):
        base = base[:-6]
    return f"{base}{path}"


def _ai_url(base_url: str, path: str) -> str:
    """Join a base ai URL with an API path, stripping duplicate /ai prefix."""
    base = base_url.rstrip("/")
    if base.endswith("/ai"):
        base = base[:-3]
    return f"{base}{path}"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(MIGRATION_SQL)
        # Add columns that may not exist in older databases
        for col_sql in [
            "ALTER TABLE user_cluster ADD COLUMN JobManagerType TEXT",
            "ALTER TABLE user_cluster ADD COLUMN JobManagerAddr TEXT",
            "ALTER TABLE user_cluster ADD COLUMN JobManagerid TEXT",
            "ALTER TABLE user_cluster ADD COLUMN JobManagertext TEXT",
            "ALTER TABLE user_cluster ADD COLUMN JobManagerPort TEXT",
        ]:
            try:
                conn.execute(col_sql)
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auth utilities
# ---------------------------------------------------------------------------

AUTH_BASE_URL = "https://c-2056205187675406338.qdai.scnet.cn:58043"


def _enrich_auth_error(response: Any, username: str) -> Any:
    """If *response* is a dict with code==\"10008\" (token expired), inject
    an *auth_url* hint so callers know how to re-authenticate.

    Returns the (possibly modified) response dict, or the original value
    unchanged when it does not match the pattern.
    """
    if isinstance(response, dict) and str(response.get("code", "")) == "10008":
        response = dict(response)  # copy so we never mutate upstream data
        msg = response.get("msg", "")
        response["msg"] = (
            f"{msg}。请访问认证页面重新获取访问凭证。"
            if msg
            else "请访问认证页面重新获取访问凭证。"
        )
        response["auth_url"] = f"{AUTH_BASE_URL}/auth/{username}"
    return response


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


def _get_default_token(username: str) -> dict:
    """Return the default cluster token/hpcUrls for *username*.

    Looks up the cluster with ``isDefault = true``.  If no default is
    configured, picks the first cluster whose ``JobManagerid`` is
    non-empty / non-zero, promotes it to the default, and returns it.

    Returns a dict with ``error=True + message`` on failure, or
    ``token, hpcUrls, clusterId, clusterName`` on success.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT uc.clusterId, uc.clusterName, uc.token, uc.JobManagerid, "
            "uc.homePath, cu.hpcUrls, cu.efileUrls, cu.aiUrls "
            "FROM user_cluster uc "
            "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
            "WHERE uc.userName = ? AND uc.isDefault = true",
            (username,),
        ).fetchone()

        if row is None:
            row = conn.execute(
                "SELECT uc.clusterId, uc.clusterName, uc.token, uc.JobManagerid, "
                "uc.homePath, cu.hpcUrls, cu.efileUrls, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? "
                "AND uc.JobManagerid IS NOT NULL "
                "AND uc.JobManagerid != '' "
                "AND uc.JobManagerid != '0'",
                (username,),
            ).fetchone()

            if row is None:
                return {
                    "error": True,
                    "message": (
                        "未找到可用的默认集群。"
                        "请先调用 hpc_list_available_partitions 获取可用队列。"
                    ),
                }

            conn.execute(
                "UPDATE user_cluster SET isDefault = false WHERE userName = ?",
                (username,),
            )
            conn.execute(
                "UPDATE user_cluster SET isDefault = true "
                "WHERE userName = ? AND clusterId = ?",
                (username, row["clusterId"]),
            )
            conn.commit()

        home_path = row["homePath"] or ""
        cluster_user = home_path.rstrip("/").rsplit("/", 1)[-1] if home_path else username
        return {
            "token": row["token"],
            "hpcUrls": row["hpcUrls"],
            "efileUrls": row["efileUrls"],
            "aiUrls": row["aiUrls"],
            "clusterId": row["clusterId"],
            "clusterName": row["clusterName"],
            "jobManagerId": row["JobManagerid"] or "",
            "clusterUserName": cluster_user,
        }
    finally:
        conn.close()


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
                    "INSERT OR IGNORE INTO user_cluster "
                    "(userName, clusterId, clusterName, token, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (username, cid, cname, token, now, now),
                )
                conn.execute(
                    "UPDATE user_cluster SET token = ?, clusterName = ?, updated_at = ? "
                    "WHERE userName = ? AND clusterId = ?",
                    (token, cname, now, username, cid),
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

        # Call cluster API to get JobManager details from cluster_list
        job_manager_type = ""
        job_manager_addr = ""
        job_manager_id_val = ""
        job_manager_text = ""
        job_manager_port = ""

        if hpc_urls:
            valid_hpc_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
            if valid_hpc_urls:
                try:
                    cluster_client = _get_http_client(timeout=30.0)
                    cluster_resp = await cluster_client.get(
                        f"{valid_hpc_urls[0]}/hpc/openapi/v2/cluster",
                        headers={"token": cl["token"], "Content-Type": "application/json"},
                    )
                    cluster_resp.raise_for_status()
                    cluster_data = cluster_resp.json()

                    if isinstance(cluster_data, dict):
                        cluster_list = cluster_data.get("data", cluster_data)
                        if isinstance(cluster_list, list) and cluster_list:
                            first = cluster_list[0]
                            job_manager_type = str(first.get("JobManagerType", ""))
                            job_manager_addr = str(first.get("JobManagerAddr", ""))
                            job_manager_id_val = str(first.get("id", ""))
                            job_manager_text = str(first.get("text", ""))
                            job_manager_port = str(first.get("JobManagerPort", ""))
                        elif isinstance(cluster_list, dict):
                            job_manager_type = str(cluster_list.get("JobManagerType", ""))
                            job_manager_addr = str(cluster_list.get("JobManagerAddr", ""))
                            job_manager_id_val = str(cluster_list.get("id", ""))
                            job_manager_text = str(cluster_list.get("text", ""))
                            job_manager_port = str(cluster_list.get("JobManagerPort", ""))
                except Exception:
                    pass

        conn = get_db()
        try:
            conn.execute(
                "UPDATE user_cluster SET homePath = ?, "
                "JobManagerType = ?, JobManagerAddr = ?, JobManagerid = ?, "
                "JobManagertext = ?, JobManagerPort = ?, updated_at = ? "
                "WHERE userName = ? AND clusterId = ?",
                (home_path, job_manager_type, job_manager_addr, job_manager_id_val,
                 job_manager_text, job_manager_port, now, username, cl["clusterId"]),
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
    """获取当前用户的 SCNet 账号信息。

    返回国家、语言、时区、账号状态、余额等基本信息。
    调用前需先通过 /auth/{username} 完成 AK/SK 认证。
    """
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

    return _enrich_auth_error(data, username)


@mcp.tool()
async def hpc_list_available_partitions() -> list[dict]:
    """列出当前用户在所有集群中真正可用的队列分区。

    自动过滤无可用资源的队列和无队列的集群，返回按集群分组的可用队列信息。
    调用方可根据返回结果选择合适的集群和队列来提交作业。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return [auth_result]

    # Get all clusters for this user with their URLs
    conn = get_db()
    try:
        clusters = conn.execute(
            "SELECT uc.clusterId, uc.clusterName, uc.token, "
            "uc.JobManagerid, "
            "cu.hpcUrls "
            "FROM user_cluster uc "
            "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
            "WHERE uc.userName = ? "
            "AND uc.JobManagerid IS NOT NULL "
            "AND uc.JobManagerid != '' "
            "AND uc.JobManagerid != '0'",
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

        job_manager_id = cluster["JobManagerid"]
        if not job_manager_id:
            continue
        
        # P0-4: Filter empty URLs
        valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
        if not valid_urls:
            continue

        # Round-robin via index counter
        _url_idx = _url_idx_ctx.get(str(cid), 0)
        base_url = valid_urls[_url_idx % len(valid_urls)]
        _url_idx_ctx[str(cid)] = _url_idx + 1

        cluster_result: dict = {"clusterId": cid, "clusterName": cname, "jobManagerID": job_manager_id}

        try:
            client = _get_http_client(timeout=30.0)

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
            ("hpc_list_available_partitions", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(results, username)


@mcp.tool()
async def hpc_submit_job(
    queueName: str,
    GAP_CMD_FILE: str,
    clusterId: Annotated[Optional[int], Field(description="集群 ID。如果省略，使用当前默认集群；如果提供，则将该集群设为默认。")] = None,
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
    """向 HPC 集群提交一个作业。

    调用前需先通过 hpc_list_available_partitions 获取可用队列信息，
    并从中选择最合适的队列。后端会自动处理认证、集群凭据获取、
    调度器 ID 获取、默认值填充以及作业名称生成。
    """
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

    # 1.5 Resolve clusterId — use default if not provided, promote if explicit
    if clusterId is None:
        resolved = _get_default_token(username)
        if "error" in resolved:
            return resolved
        clusterId = resolved["clusterId"]
    else:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE user_cluster SET isDefault = false WHERE userName = ?",
                (username,),
            )
            conn.execute(
                "UPDATE user_cluster SET isDefault = true "
                "WHERE userName = ? AND clusterId = ?",
                (username, clusterId),
            )
            conn.commit()
        finally:
            conn.close()

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
                "请先调用 hpc_list_available_partitions 获取可用队列，然后选择有效的集群。"
            ),
        }
    if row["token"] is None:
        return {
            "error": True,
            "message": (
                f"未在集群 clusterId={clusterId} 中找到您的认证凭证。"
                " 请先调用 hpc_list_available_partitions 获取可用队列，然后选择有效的集群。"
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
            "向 HPC 集群提交一个作业。调用前需先通过 hpc_list_available_partitions 获取可用队列信息，"
            "并从中选择最合适的队列。后端会自动处理认证、集群凭据获取、调度器 ID 获取、"
            "默认值填充以及作业名称生成。"
        ),
        "parameters": {
            "format": "JSON",
            "schema": {
                "clusterId": {"type": "integer", "description": "集群 ID。如果省略，使用当前默认集群；如果提供，则将该集群设为默认。", "optional": True},
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
            ("hpc_submit_job", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    # 返回结果中加入 token 和 hpcUrls
    if isinstance(result, dict):
        result["token"] = token
        result["hpcUrls"] = hpc_urls

    return _enrich_auth_error(result, username)


@mcp.tool()
async def hpc_get_running_job_detail(
    jobId: Annotated[str, Field(description="作业 ID，可从 hpc_submit_job 返回的 jobID 字段获取")],
    clusterId: Annotated[Optional[int], Field(description="可选：集群 ID。如果省略，使用当前默认集群。")] = None,
) -> dict:
    """查询 HPC 集群中指定作业的实时详细信息。

    调用前需先通过 hpc_list_available_partitions 获取可用队列信息。
    适用于查询正在运行或排队中的作业。若作业已结束，请使用 hpc_get_history_job_detail。
    """
    username = get_current_username()

    # 1. Auth check
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # 2. Resolve token and hpcUrls from DB
    if clusterId is not None:
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
                    "请先调用 hpc_list_available_partitions 获取可用队列。"
                ),
            }
        effective_token = row["token"]
        effective_hpc_urls = row["hpcUrls"]
        if not effective_token:
            return {
                "error": True,
                "message": (
                    f"未在集群 clusterId={clusterId} 中找到您的认证凭证。"
                    "请先调用 hpc_list_available_partitions 获取可用队列。"
                ),
            }
    else:
        resolved = _get_default_token(username)
        if "error" in resolved:
            return resolved
        effective_token = resolved["token"]
        effective_hpc_urls = resolved["hpcUrls"]

    if not effective_hpc_urls:
        return {
            "error": True,
            "message": "集群未配置 HPC 服务 URL。请联系管理员配置集群信息。",
        }

    # 3. Query job detail via round-robin on hpcUrls
    valid_urls = [u.strip().rstrip("/") for u in effective_hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {
            "error": True,
            "message": "未找到有效的 HPC 服务 URL。",
        }

    _url_idx = _url_idx_ctx.get(str(clusterId), 0)
    base_url = valid_urls[_url_idx % len(valid_urls)]
    _url_idx_ctx[str(clusterId)] = _url_idx + 1
    job_detail_url = f"{base_url}/hpc/openapi/v2/jobs/{jobId}"

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            job_detail_url,
            headers={"token": effective_token, "Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询作业 {jobId} 失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"查询作业请求异常: {exc}",
        }

    if not result or result.get("data") is None:
        return {
            "error": True,
            "message": (
                f"作业 {jobId} 在当前集群上未找到。"
                "可能原因：1) 作业 ID 不正确；2) 作业属于其他集群，请指定 clusterId 重试；"
                "3) 实时作业已结束，请使用 hpc_get_history_job_detail 查询历史作业。"
            ),
        }

    # 4. Auto-register document in APIs table
    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/jobs/{jobId}",
        "method": "GET",
        "description": (
            "查询 HPC 集群中指定作业的实时详细信息。调用前需先通过 hpc_list_available_partitions "
            "获取可用队列信息，选择正确的集群和 jobId。后端会自动处理认证和集群信息。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "jobId": {"type": "string", "description": "作业 ID，可从 hpc_submit_job 返回的 jobID 字段获取", "optional": False},
                "clusterId": {"type": "integer", "description": "可选：集群 ID，用于精确匹配", "optional": True},
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
            ("hpc_get_running_job_detail", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def hpc_get_history_job_detail(
    jobId: Annotated[str, Field(description="作业 ID，可从 hpc_submit_job 返回的 jobID 字段获取")],
    acctTime: Annotated[Optional[str], Field(description="入账时间（结束时间），建议传入以提升查询性能，格式 YYYY-MM-DD HH:MM:SS")] = None,
) -> dict:
    """查询 HPC 集群中指定历史作业（已完成/已终止）的详细信息。

    调用前需先通过 hpc_list_available_partitions 获取可用队列信息和 jobManagerID，
    并从 hpc_submit_job 返回结果中获取 jobId。传入 acctTime 可显著提升查询性能。
    """
    username = get_current_username()

    # 1. Auth check
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # 2. Resolve token, hpcUrls, and jobManagerId via default cluster
    resolved = _get_default_token(username)
    if "error" in resolved:
        return resolved
    effective_token = resolved["token"]
    hpc_urls = resolved["hpcUrls"]
    jobmanagerId = resolved["jobManagerId"]

    if not hpc_urls:
        return {
            "error": True,
            "message": "未查询到集群 HPC 服务 URL。请先调用 hpc_list_available_partitions 获取可用队列。",
        }

    # 3. Query via round-robin on hpcUrls
    valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {
            "error": True,
            "message": "未找到有效的 HPC 服务 URL。",
        }

    _idx = _url_idx_ctx.get(str(resolved["clusterId"]), 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[str(resolved["clusterId"])] = _idx + 1

    history_job_url = f"{base_url}/hpc/openapi/v2/historyjobs/{jobmanagerId}/{jobId}"

    query_params: dict[str, Any] | None = None
    if acctTime:
        query_params = {"acctTime": acctTime}

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            history_job_url,
            headers={"token": effective_token, "Content-Type": "application/json"},
            params=query_params,
            timeout=10.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询历史作业 {jobId} 失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"查询历史作业请求异常: {exc}",
        }

    if not result or result.get("data") is None:
        return {
            "error": True,
            "message": (
                f"历史作业 {jobId} 未找到。"
                "可能原因：1) 作业 ID 不正确；2) 作业属于其他集群，请指定 clusterId 重试；"
                "3) 作业数据已被清理。"
            ),
        }

    # 5. Auto-register document in APIs table
    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/historyjobs/{jobmanagerId}/{jobId}",
        "method": "GET",
        "description": (
            "查询 HPC 集群中指定历史作业（已完成/已终止）的详细信息。"
            "调用前需先通过 hpc_list_available_partitions 获取可用队列信息和 jobManagerID，"
            "并从提交结果中获取 jobId。后端会自动处理认证和集群信息。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "jobId": {"type": "string", "description": "作业 ID，可从 hpc_submit_job 返回的 jobID 字段获取", "optional": False},
                "acctTime": {"type": "string", "description": "入账时间（结束时间），建议传入以提升查询性能，格式 YYYY-MM-DD HH:MM:SS", "optional": True},
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
            ("hpc_get_history_job_detail", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def set_default_cluster(
    clusterId: Annotated[Optional[int], Field(description="要设为默认集群的 clusterId。与 clusterName 二选一，clusterId 优先")] = None,
    clusterName: Annotated[Optional[str], Field(description="集群名称（模糊匹配）。与 clusterId 二选一，clusterId 优先。匹配到多条时返回候选列表")] = None,
) -> dict:
    """Set the default cluster for the current user.

    Sets isDefault=true for the target cluster and isDefault=false
    for all other clusters belonging to this user. Accepts either
    clusterId (exact) or clusterName (fuzzy LIKE match).
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # Must provide at least one of clusterId / clusterName
    if clusterId is None and not (clusterName and clusterName.strip()):
        return {
            "error": True,
            "message": "请至少指定 clusterId 或 clusterName。",
        }

    conn = get_db()
    try:
        if clusterId is not None:
            # Exact match by clusterId
            row = conn.execute(
                "SELECT clusterId, clusterName FROM user_cluster WHERE userName = ? AND clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": (
                        f"集群 clusterId={clusterId} 不属于用户 '{username}'。"
                        "请先调用 hpc_list_available_partitions 获取可用集群。"
                    ),
                }
            target_id = row["clusterId"]
            target_name = row["clusterName"]
        else:
            # Fuzzy match by clusterName
            like_pattern = f"%{clusterName.strip()}%"
            rows = conn.execute(
                "SELECT clusterId, clusterName FROM user_cluster WHERE userName = ? AND clusterName LIKE ?",
                (username, like_pattern),
            ).fetchall()

            if not rows:
                return {
                    "error": True,
                    "message": (
                        f"用户 '{username}' 下未找到名称包含 "
                        f"'{clusterName.strip()}' 的集群。"
                        "请先调用 hpc_list_available_partitions 获取可用集群。"
                    ),
                }

            if len(rows) == 1:
                target_id = rows[0]["clusterId"]
                target_name = rows[0]["clusterName"]
            else:
                # Multiple matches — return candidates for user/agent to choose
                candidates = [
                    {"clusterId": r["clusterId"], "clusterName": r["clusterName"]}
                    for r in rows
                ]
                return {
                    "success": False,
                    "message": (
                        f"找到 {len(candidates)} 个匹配 '{clusterName.strip()}' 的集群，"
                        "请选择需要切换的 clusterId 后重新调用。"
                    ),
                    "candidates": candidates,
                }

        # Switch default to the target cluster
        conn.execute(
            "UPDATE user_cluster SET isDefault = false WHERE userName = ?",
            (username,),
        )
        conn.execute(
            "UPDATE user_cluster SET isDefault = true WHERE userName = ? AND clusterId = ?",
            (username, target_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "userName": username,
        "defaultClusterId": target_id,
        "defaultClusterName": target_name,
    }


@mcp.tool()
async def hpc_list_history_jobs(
    page: Annotated[int, Field(description="页码，从 1 开始")] = 1,
    size: Annotated[int, Field(description="每页记录数")] = 10,
    clusterId: Annotated[str, Field(description="区域/集群 ID 筛选，传空字符串表示所有区域")] = "",
    queue: Annotated[str, Field(description="队列名称筛选，传空字符串表示所有队列")] = "",
    jobState: Annotated[str, Field(description="作业状态筛选: statE(退出), statC(完成), statDE(取消), statD(失败), statT(超时), statN(节点异常), statRQ(重新运行)。传空表示所有状态")] = "",
    startTime: Annotated[str, Field(description="查询开始时间，格式 YYYY-MM-DD HH:MM:SS。默认7天前")] = "",
    endTime: Annotated[str, Field(description="查询结束时间，格式 YYYY-MM-DD HH:MM:SS。默认当前时间")] = "",
    showGroupJobs: Annotated[bool, Field(description="是否展示组内所有成员作业")] = False,
    jobId: Annotated[str, Field(description="作业 ID 精确匹配，传空表示不过滤")] = "",
    clusterUserName: Annotated[str, Field(description="按用户名筛选作业，传空表示不过滤")] = "",
    showAllData: Annotated[bool, Field(description="是否返回所有字段")] = False,
) -> dict:
    """跨区域聚合查询历史作业列表。

    通过 AC 服务统一入口，单次请求返回所有区域的作业记录。
    与 hpc_get_history_job_detail 不同：本工具做列表分页查询，后者按 jobId 精确查单条。
    """
    username = get_current_username()

    # 1. Auth check — need acToken from users table (AC token, not cluster token)
    conn = get_db()
    try:
        ac_row = conn.execute(
            "SELECT acToken FROM users WHERE userName = ?", (username,)
        ).fetchone()
    finally:
        conn.close()

    if ac_row is None or ac_row["acToken"] is None:
        return {
            "error": True,
            "message": (
                f"用户 '{username}' 未认证。"
                "请先访问认证页面获取访问凭证。"
            ),
            "auth_url": f"{AUTH_BASE_URL}/auth/{username}",
        }

    ac_token = ac_row["acToken"]

    # 2. Apply default time range if not provided
    if not startTime or not endTime:
        from datetime import datetime, timedelta

        now = datetime.now()
        if not endTime:
            endTime = now.strftime("%Y-%m-%d %H:%M:%S")
        if not startTime:
            startTime = (now - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")

    # 3. Build request body
    body: dict[str, Any] = {
        "page": page,
        "size": size,
        "clusterId": clusterId,
        "queue": queue,
        "jobId": jobId,
        "jobState": jobState,
        "startTime": startTime,
        "endTime": endTime,
        "showGroupJobs": showGroupJobs,
        "clusterUserName": clusterUserName,
        "showAllData": showAllData,
    }

    # 4. Call AC unified API
    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            "https://www.scnet.cn/ac/openapi/v2/jobs/history/page-list",
            json=body,
            headers={"token": ac_token, "Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询历史作业列表失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"查询历史作业列表请求异常: {exc}",
        }

    # 4. Auto-register document in APIs table
    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "https://www.scnet.cn/ac/openapi/v2/jobs/history/page-list",
        "method": "POST",
        "description": (
            "跨区域聚合查询历史作业列表。通过 AC 服务统一入口，"
            "单次请求返回所有区域的作业记录，支持按集群、队列、状态、时间范围筛选。"
        ),
        "parameters": {
            "format": "JSON",
            "schema": {
                "page": {"type": "integer", "description": "页码，从 1 开始", "optional": True},
                "size": {"type": "integer", "description": "每页记录数", "optional": True},
                "clusterId": {"type": "string", "description": "区域/集群 ID 筛选", "optional": True},
                "queue": {"type": "string", "description": "队列名称筛选", "optional": True},
                "jobId": {"type": "string", "description": "作业 ID 精确匹配", "optional": True},
                "jobState": {"type": "string", "description": "作业状态筛选", "optional": True},
                "startTime": {"type": "string", "description": "查询开始时间", "optional": True},
                "endTime": {"type": "string", "description": "查询结束时间", "optional": True},
                "showGroupJobs": {"type": "boolean", "description": "是否展示组内所有成员作业", "optional": True},
                "clusterUserName": {"type": "string", "description": "按用户名筛选作业", "optional": True},
                "showAllData": {"type": "boolean", "description": "是否返回所有字段", "optional": True},
            },
        },
        "returns": {
            "format": "JSON",
            "schema": returns_schema,
        },
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("hpc_list_history_jobs", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def hpc_list_running_jobs(
    page: Annotated[int, Field(description="页码，从 1 开始")] = 1,
    size: Annotated[int, Field(description="每页记录数")] = 10,
    clusterId: Annotated[str, Field(description="区域/集群 ID 筛选，传空字符串表示所有区域")] = "",
    queue: Annotated[str, Field(description="队列名称筛选，传空字符串表示所有队列")] = "",
    jobId: Annotated[str, Field(description="作业 ID 精确匹配，传空表示不过滤")] = "",
    jobState: Annotated[str, Field(description="作业状态筛选: statR(运行), statQ(排队), statH(保留), statS(挂起), statW(等待)。传空表示所有活跃状态")] = "",
    showGroupJobs: Annotated[bool, Field(description="是否展示组内所有成员作业")] = False,
    clusterUserName: Annotated[str, Field(description="按用户名筛选作业，传空表示不过滤")] = "",
    showAllData: Annotated[bool, Field(description="是否返回所有字段（含调度器原始状态、资源请求量等完整数据）")] = False,
) -> dict:
    """跨区域聚合查询实时作业列表。

    通过 AC 服务统一入口，单次请求返回所有区域的实时作业（运行中/排队中/挂起等活跃状态）。
    与 hpc_list_history_jobs 不同：本工具查活跃作业，后者查终态历史作业。
    """
    username = get_current_username()

    # 1. Auth check
    conn = get_db()
    try:
        ac_row = conn.execute(
            "SELECT acToken FROM users WHERE userName = ?", (username,)
        ).fetchone()
    finally:
        conn.close()

    if ac_row is None or ac_row["acToken"] is None:
        return {
            "error": True,
            "message": (
                f"用户 '{username}' 未认证。"
                "请先访问认证页面获取访问凭证。"
            ),
            "auth_url": f"{AUTH_BASE_URL}/auth/{username}",
        }

    ac_token = ac_row["acToken"]

    # 2. Build request body
    body: dict[str, Any] = {
        "page": page,
        "size": size,
        "clusterId": clusterId,
        "queue": queue,
        "jobId": jobId,
        "jobState": jobState,
        "showGroupJobs": showGroupJobs,
        "clusterUserName": clusterUserName,
        "showAllData": showAllData,
    }

    # 3. Call AC unified API
    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            "https://www.scnet.cn/ac/openapi/v2/jobs/monitor/page-list",
            json=body,
            headers={"token": ac_token, "Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询实时作业列表失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"查询实时作业列表请求异常: {exc}",
        }

    # 4. Auto-register document in APIs table
    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    _running_job_field_descriptions = {
        # 集群与调度器
        "clusterId": "区域/集群 ID",
        "clusterName": "区域/集群名称",
        "jobManagerId": "调度器 ID",
        "jobManagerName": "调度器名称",
        "jobManagerType": "调度器类型，如 SLURM",
        # 标识信息
        "jobId": "作业 ID",
        "jobName": "作业名",
        "clusterUserName": "集群用户名",
        # 作业状态与分类
        "jobState": "作业状态: statR(运行), statQ(排队), statH(保留), statS(挂起), statW(等待)",
        "appType": "应用类型，如 BASE",
        "taskType": "任务类型，如 HPC",
        "queue": "作业提交队列",
        "priority": "作业优先级",
        "reason": "作业状态原因，NonZeroExitCode 时可能表示实际已失败",
        "requeue": "是否允许重新排队",
        "tags": "标签",
        # 时间信息
        "jobStartTime": "作业启动时间",
        "jobQueueTime": "作业入队时间",
        "jobRunTime": "作业已运行时长，格式 HH:MM:SS 或 D-HH:MM:SS",
        "wallTimeReq": "申请的最大运行时长，格式 D-HH:MM:SS",
        "jobQueueTimeUsed": "排队等待时间（秒）",
        # 资源使用
        "nodeUsed": "使用的节点名/列表",
        "nodeNumUsed": "使用节点数",
        "procNumUsed": "使用的 CPU 核心数",
        "dcuNumUsed": "使用 DCU 加速卡数",
        "gpuNumUsed": "使用 GPU 卡数",
        # 路径信息
        "workDir": "作业工作路径",
        "outputPath": "标准输出文件路径",
        "errorPath": "标准错误输出路径",
        # VNC
        "jobVncSessionInfo": "作业 VNC 会话信息，无 VNC 时为 null",
        # showAllData=true 扩展字段 —— 调度器状态校正
        "initContentAttr": "JSON 字符串，底层调度器原始状态，用于校正平台状态延迟。JobState=RUNNING/FAILED/COMPLETED",
        # showAllData=true 扩展字段 —— 资源请求量
        "cpuCore": "CPU 核心数",
        "gpuNum": "GPU 数量",
        "nodeNumReq": "申请的节点数",
        "procNumReq": "申请的处理器数",
        "gpuNumReq": "申请的 GPU 数",
        "dcuNumReq": "申请的 DCU 数",
        # showAllData=true 扩展字段 —— 资源配置与使用
        "memUsed": "已使用内存，如 7600M",
        "vmemUsed": "已使用虚拟内存",
        "wallTime": "已运行时长（同 jobRunTime）",
        "cpuTime": "CPU 时间",
        "exitCode": "退出码",
        # showAllData=true 扩展字段 —— 调度器执行信息
        "execHost": "执行主机名",
        "execGpus": "分配的 GPU 设备",
        "software": "软件信息",
        "outPath": "标准输出路径（同 outputPath）",
        "vncSessionInfo": "VNC 会话信息（同 jobVncSessionInfo）",
        # showAllData=true 扩展字段 —— 内部/冗余字段
        "id": "作业 ID（冗余，同 jobId）",
        "name": "作业名（冗余，同 jobName）",
        "managerId": "调度器 ID（冗余，同 jobManagerId）",
        "managerName": "调度器名称（冗余，同 jobManagerName）",
        "managerType": "调度器类型（冗余，同 jobManagerType）",
        "owner": "作业所有者（冗余，同 clusterUserName）",
        "status": "作业状态（冗余，同 jobState）",
        "ctime": "创建时间",
        "qtime": "入队时间（冗余，同 jobQueueTime）",
        "etime": "结束时间，运行中为 null",
        "startTime": "启动时间（冗余，同 jobStartTime）",
        "modifyTime": "修改时间",
        "timeStamp": "时间戳",
    }
    if isinstance(returns_schema, dict):
        for key, desc in _running_job_field_descriptions.items():
            if key in returns_schema:
                returns_schema[key]["description"] = desc

    doc = {
        "url": "https://www.scnet.cn/ac/openapi/v2/jobs/monitor/page-list",
        "method": "POST",
        "description": (
            "跨区域聚合查询实时作业列表。通过 AC 服务统一入口，"
            "单次请求返回所有区域的活跃作业（运行中/排队中/挂起等），"
            "支持按集群、队列、状态筛选。"
        ),
        "parameters": {
            "format": "JSON",
            "schema": {
                "page": {"type": "integer", "description": "页码，从 1 开始", "optional": True},
                "size": {"type": "integer", "description": "每页记录数", "optional": True},
                "clusterId": {"type": "string", "description": "区域/集群 ID 筛选", "optional": True},
                "queue": {"type": "string", "description": "队列名称筛选", "optional": True},
                "jobId": {"type": "string", "description": "作业 ID 精确匹配", "optional": True},
                "jobState": {"type": "string", "description": "作业状态筛选: statR/statQ/statH/statS/statW", "optional": True},
                "showGroupJobs": {"type": "boolean", "description": "是否展示组内所有成员作业", "optional": True},
                "clusterUserName": {"type": "string", "description": "按用户名筛选作业", "optional": True},
                "showAllData": {"type": "boolean", "description": "是否返回所有字段", "optional": True},
            },
        },
        "returns": {
            "format": "JSON",
            "schema": returns_schema,
        },
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("hpc_list_running_jobs", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def hpc_cancel_job(
    jobId: Annotated[str, Field(description="待取消的作业 ID，多个作业以英文逗号分隔，如 \"63436,63437\"")],
    jobManagerId: Annotated[str, Field(description="调度器 ID。为空时从默认集群自动获取")] = "",
    clusterId: Annotated[int, Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """取消/删除 HPC 集群中正在运行或排队的作业。支持批量取消。

    取消操作不可逆，请确认作业 ID 正确后再执行。
    """
    username = get_current_username()

    # 1. Auth check
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # 2. Resolve cluster: explicit clusterId or default
    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT token, JobManagerid, hpcUrls, homePath FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": (
                        f"集群 clusterId={clusterId} 不属于用户 '{username}'。"
                        "请先调用 hpc_list_available_partitions 获取可用集群。"
                    ),
                }
            token = row["token"]
            hpc_urls = row["hpcUrls"]
            job_manager_id = jobManagerId or (row["JobManagerid"] or "")
            home_path = row["homePath"] or ""
            cluster_user = home_path.rstrip("/").rsplit("/", 1)[-1] if home_path else username
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            hpc_urls = resolved["hpcUrls"]
            job_manager_id = jobManagerId or resolved["jobManagerId"]
            cluster_user = resolved.get("clusterUserName", username)
    finally:
        conn.close()

    if not hpc_urls:
        return {
            "error": True,
            "message": "未查询到集群 HPC 服务 URL。请先调用 hpc_list_available_partitions 获取可用队列。",
        }

    if not job_manager_id:
        return {
            "error": True,
            "message": "未获取到调度器 ID（jobManagerId）。请先调用 hpc_list_available_partitions 获取。",
        }

    # 3. Build strJobInfoMap: jobManagerId,userName:jobId:; for each jobId
    job_ids = [j.strip() for j in jobId.split(",") if j.strip()]
    if not job_ids:
        return {"error": True, "message": "未提供有效的作业 ID。"}

    str_job_info_map = "".join(
        f"{job_manager_id},{cluster_user}:{jid}:;" for jid in job_ids
    )

    # 4. Call cluster HPC API
    valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 HPC 服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else str(resolved.get("clusterId", "default"))
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    from urllib.parse import urlencode

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.delete(
            f"{base_url}/hpc/openapi/v2/jobs",
            content=f"jobMethod=5&{urlencode({'strJobInfoMap': str_job_info_map})}",
            headers={
                "token": token,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"取消作业失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"取消作业请求异常: {exc}",
        }

    # 5. Auto-register document in APIs table
    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/jobs",
        "method": "DELETE",
        "description": (
            "取消/删除 HPC 集群中正在运行或排队的作业，支持批量取消。"
            "取消操作不可逆，请确认作业 ID 正确后再执行。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "jobId": {"type": "string", "description": "待取消的作业 ID，多个以英文逗号分隔", "optional": False},
                "jobManagerId": {"type": "string", "description": "调度器 ID，为空时从默认集群获取", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {
            "format": "JSON",
            "schema": returns_schema,
        },
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("hpc_cancel_job", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_list_files(
    path: Annotated[str, Field(description="目标文件夹路径（必须为绝对路径）。为空时默认为用户家目录")] = "",
    keyword: Annotated[str, Field(description="搜索关键字，模糊匹配文件/文件夹名称")] = "",
    order: Annotated[str, Field(description="排序方式：asc（升序）或 desc（降序）")] = "asc",
    order_by: Annotated[str, Field(description="排序字段：name（文件名）、size（文件大小）、lastModifiedTime（修改时间）")] = "name",
    start: Annotated[int, Field(description="起始索引位置，从 0 开始")] = 0,
    limit: Annotated[int, Field(description="每页返回条数，最大 1000")] = 10,
    clusterId: Annotated[int, Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询 HPC 集群上用户文件目录中的文件列表。

    支持按目录路径浏览、关键字搜索、排序和分页。
    token 取自 user_cluster 表 isDefault=true 的记录，
    efileUrls 取自 cluster_url 表。
    """
    username = get_current_username()

    # 1. Auth check
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # 2. Resolve cluster: explicit clusterId or default
    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": (
                        f"集群 clusterId={clusterId} 不属于用户 '{username}'。"
                        "请先调用 hpc_list_available_partitions 获取可用集群。"
                    ),
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {
            "error": True,
            "message": "未查询到文件服务 URL（efileUrls）。请先调用 hpc_list_available_partitions 获取可用集群。",
        }

    # 3. Build query params
    params = {}
    if path:
        params["path"] = path
    if keyword:
        params["keyWord"] = keyword
    if order and order != "asc":
        params["order"] = order
    if order_by and order_by != "name":
        params["orderBy"] = order_by
    params["start"] = start
    params["limit"] = min(limit, 1000)

    # 4. Call efile API
    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            _efile_url(base_url, "/efile/openapi/v2/file/list"),
            params=params,
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询文件列表失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {
            "error": True,
            "message": f"查询文件列表请求异常: {exc}",
        }

    # 5. Map field names to snake_case for file list items
    if isinstance(result, dict) and result.get("data") and isinstance(result.get("data"), dict):
        data = result["data"]
        file_list = data.get("fileList", [])
        if file_list:
            mapped_files = []
            for f in file_list:
                mapped = {
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                    "path": f.get("path", ""),
                    "size": f.get("size", 0),
                    "is_directory": f.get("isDirectory", False),
                    "is_regular_file": f.get("isRegularFile", False),
                    "is_symbolic_link": f.get("isSymbolicLink", False),
                    "is_share": f.get("isShare", False),
                    "is_other": f.get("isOther", False),
                    "share_enabled": f.get("shareEnabled", False),
                    "owner": f.get("owner", ""),
                    "group": f.get("group", ""),
                    "permission": f.get("permission", ""),
                    "creation_time": f.get("creationTime", ""),
                    "last_modified_time": f.get("lastModifiedTime", ""),
                    "last_access_time": f.get("lastAccessTime", ""),
                    "type": f.get("type", ""),
                    "file_key": f.get("fileKey", ""),
                    "permission_action": f.get("permissionAction", {}),
                }
                mapped_files.append(mapped)
            data["files"] = mapped_files
            del data["fileList"]

    # 6. Auto-register document in APIs table
    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/list",
        "method": "GET",
        "description": (
            "查询 HPC 集群上用户文件目录中的文件列表。支持按目录路径浏览、"
            "关键字搜索、排序和分页。token 取自 user_cluster 表。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "目标文件夹路径（绝对路径），空时默认为用户家目录", "optional": True},
                "keyword": {"type": "string", "description": "搜索关键字，模糊匹配文件/文件夹名称", "optional": True},
                "order": {"type": "string", "description": "排序方式：asc（升序）或 desc（降序）", "optional": True},
                "order_by": {"type": "string", "description": "排序字段：name（文件名）、size（文件大小）、lastModifiedTime（修改时间）", "optional": True},
                "start": {"type": "integer", "description": "起始索引位置，从 0 开始", "optional": True},
                "limit": {"type": "integer", "description": "每页返回条数，最大 1000", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {
            "format": "JSON",
            "schema": returns_schema,
        },
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_list_files", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_touch(
    path: Annotated[str, Field(description="要创建的文件的绝对路径（含文件名）")],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """在 HPC 集群文件系统上创建空文件。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/touch"),
            data={"fileAbsolutePath": path},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"创建文件失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"创建文件请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/touch",
        "method": "POST",
        "description": "在 HPC 集群文件系统上创建空文件。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "要创建的文件的绝对路径（含文件名）", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_touch", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_check_permission(
    path: Annotated[str, Field(description="所校验文件的绝对路径")],
    permission_action: Annotated[str, Field(description="权限类型：READ（读）、WRITE（写）、EXECUTE（执行）")],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """校验当前用户对指定文件是否具有读、写或执行权限。"""
    username = get_current_username()

    if permission_action not in ("READ", "WRITE", "EXECUTE"):
        return {
            "error": True,
            "message": f"无效的权限类型: {permission_action}，有效值为 READ/WRITE/EXECUTE",
        }

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/permission"),
            data={"path": path, "permissionAction": permission_action},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"权限校验失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"权限校验请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/permission",
        "method": "POST",
        "description": "校验当前用户对指定文件是否具有读、写或执行权限。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "所校验文件的绝对路径", "optional": False},
                "permission_action": {"type": "string", "description": "权限类型：READ/WRITE/EXECUTE", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_check_permission", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_move(
    source_paths: Annotated[str, Field(description="源文件绝对路径，支持批量。多个文件路径用英文逗号分隔")],
    target_path: Annotated[str, Field(description="目标目录绝对路径")],
    cover: Annotated[str, Field(description="覆盖策略：cover（强制覆盖）或 uncover（不覆盖）")] = "uncover",
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """在 HPC 集群文件系统上移动文件，支持批量移动（多个源文件逗号分隔）。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/move"),
            data={
                "sourcePaths": source_paths,
                "cover": cover,
                "targetPath": target_path,
            },
            headers={"token": token},
            timeout=60.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"移动文件失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"移动文件请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/move",
        "method": "POST",
        "description": "在 HPC 集群文件系统上移动文件，支持批量移动。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "source_paths": {"type": "string", "description": "源文件绝对路径，多个用英文逗号分隔", "optional": False},
                "target_path": {"type": "string", "description": "目标目录绝对路径", "optional": False},
                "cover": {"type": "string", "description": "覆盖策略：cover/uncover，默认 uncover", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_move", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_copy(
    source_paths: Annotated[str, Field(description="源文件绝对路径，支持批量。多个文件路径用英文逗号分隔")],
    target_path: Annotated[str, Field(description="目标目录绝对路径")],
    cover: Annotated[str, Field(description="覆盖策略：cover（强制覆盖）或 uncover（不覆盖）")] = "uncover",
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """在 HPC 集群文件系统上复制文件，支持批量复制（多个源文件逗号分隔）。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/copy"),
            data={
                "sourcePaths": source_paths,
                "cover": cover,
                "targetPath": target_path,
            },
            headers={"token": token},
            timeout=60.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"复制文件失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"复制文件请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/copy",
        "method": "POST",
        "description": "在 HPC 集群文件系统上复制文件，支持批量复制。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "source_paths": {"type": "string", "description": "源文件绝对路径，多个用英文逗号分隔", "optional": False},
                "target_path": {"type": "string", "description": "目标目录绝对路径", "optional": False},
                "cover": {"type": "string", "description": "覆盖策略：cover/uncover，默认 uncover", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_copy", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_rename(
    path: Annotated[str, Field(description="源文件绝对路径")],
    new_name: Annotated[str, Field(description="文件修改后的新名称（仅文件名，不含路径）")],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """重命名 HPC 集群文件系统上的文件。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/rename"),
            data={"fileAbsolutePath": path, "newName": new_name},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"重命名文件失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"重命名文件请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/rename",
        "method": "POST",
        "description": "重命名 HPC 集群文件系统上的文件。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "源文件绝对路径", "optional": False},
                "new_name": {"type": "string", "description": "文件修改后的新名称（仅文件名，不含路径）", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_rename", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_delete(
    paths: Annotated[str, Field(description="删除文件的绝对路径，多个路径用英文逗号分隔")],
    recursive: Annotated[bool, Field(description="是否递归删除。true 可删除非空文件夹")] = False,
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """删除 HPC 集群文件系统上的文件或文件夹，支持批量删除。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/remove"),
            params={"paths": paths, "recursive": "true" if recursive else "false"},
            headers={"token": token},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"删除文件/文件夹失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"删除文件/文件夹请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/remove",
        "method": "POST",
        "description": "删除 HPC 集群文件系统上的文件或文件夹，支持批量删除。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "paths": {"type": "string", "description": "删除文件的绝对路径，多个用英文逗号分隔", "optional": False},
                "recursive": {"type": "boolean", "description": "是否递归删除", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_delete", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_exist(
    path: Annotated[str, Field(description="文件/文件夹的绝对路径")],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """判断指定的文件或文件夹是否存在于 HPC 集群文件系统中。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/exist"),
            data={"path": path},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"判断文件是否存在失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"判断文件是否存在请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/exist",
        "method": "POST",
        "description": "判断指定的文件或文件夹是否存在于 HPC 集群文件系统中。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "文件/文件夹的绝对路径", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_exist", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_folder_create(
    path: Annotated[str, Field(description="文件夹绝对路径")],
    create_parents: Annotated[bool, Field(description="父目录不存在时是否自动创建")] = False,
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """在 HPC 集群文件系统上创建文件夹。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/mkdir"),
            params={"path": path, "createParents": "true" if create_parents else "false"},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"创建文件夹失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"创建文件夹请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/mkdir",
        "method": "POST",
        "description": "在 HPC 集群文件系统上创建文件夹。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "文件夹绝对路径", "optional": False},
                "create_parents": {"type": "boolean", "description": "父目录不存在时是否自动创建", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_folder_create", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_preview_file(
    path: Annotated[str, Field(description="预览文件的绝对路径")],
    force: Annotated[bool, Field(description="true 强制打开，false 默认方式")] = False,
    start_index: Annotated[int, Field(description="起始字符位置（从 0 开始）")] = 0,
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """预览 HPC 集群上的文本文件内容，支持分页读取。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/preview"),
            data={
                "path": path,
                "force": "force" if force else "default",
                "startIndex": str(start_index),
            },
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"预览文件失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"预览文件请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/preview",
        "method": "POST",
        "description": "预览 HPC 集群上的文本文件内容，支持分页读取。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "预览文件的绝对路径", "optional": False},
                "force": {"type": "boolean", "description": "true 强制打开，false 默认方式", "optional": True},
                "start_index": {"type": "integer", "description": "起始字符位置，从 0 开始", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_preview_file", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_upload(
    file_content: Annotated[str, Field(description="文件内容的 base64 编码字符串")],
    file_name: Annotated[str, Field(description="原始文件名（如 result.txt）")],
    remote_path: Annotated[str, Field(description="远程目标文件夹路径（必须为绝对路径）")],
    cover: Annotated[str, Field(description="覆盖策略：cover（强制覆盖）或 uncover（不覆盖）")] = "uncover",
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """上传文件到 HPC 集群文件系统的指定路径。文件内容通过 base64 编码字符串传入。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # Decode base64 file content
    try:
        file_bytes = base64.b64decode(file_content)
    except Exception as exc:
        return {"error": True, "message": f"文件内容 base64 解码失败: {exc}"}

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/upload"),
            data={"path": remote_path, "cover": cover},
            files={"file": (file_name, file_bytes)},
            headers={"token": token},
            timeout=60.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"上传文件失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
        }
    except Exception as exc:
        return {"error": True, "message": f"上传文件请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/upload",
        "method": "POST",
        "description": "上传文件到 HPC 集群文件系统的指定路径。文件内容通过 base64 编码字符串传入。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "file_content": {"type": "string", "description": "文件内容的 base64 编码字符串", "optional": False},
                "file_name": {"type": "string", "description": "原始文件名", "optional": False},
                "remote_path": {"type": "string", "description": "远程目标文件夹路径（绝对路径）", "optional": False},
                "cover": {"type": "string", "description": "覆盖策略：cover/uncover，默认 uncover", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_upload", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def efile_download(
    path: Annotated[str, Field(description="要下载的文件/文件夹绝对路径")],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """从 HPC 集群文件系统下载文件或文件夹。文件内容以 base64 编码字符串返回。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.efileUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, clusterId),
            ).fetchone()
            if row is None:
                return {
                    "error": True,
                    "message": f"集群 clusterId={clusterId} 不属于用户 '{username}'。",
                }
            token = row["token"]
            efile_urls = row["efileUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            efile_urls = resolved.get("efileUrls", "")
    finally:
        conn.close()

    if not efile_urls:
        return {"error": True, "message": "未查询到文件服务 URL（efileUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in efile_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的文件服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            _efile_url(base_url, "/efile/openapi/v2/file/download"),
            params={"path": path},
            headers={"token": token},
            timeout=120.0,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        # If API returns JSON (error response), parse and return it
        if content_type and "json" in content_type:
            return resp.json()

        file_bytes = resp.content
        file_content_b64 = base64.b64encode(file_bytes).decode("utf-8")

        # Derive file name from Content-Disposition header or path
        content_disposition = resp.headers.get("content-disposition", "")
        file_name = ""
        if content_disposition:
            match = re.search(r'filename[^;=\n]*=((["\']).*?\2|[^;\n]*)', content_disposition)
            if match:
                file_name = match.group(1).strip().strip('"\'')
        if not file_name:
            file_name = path.rstrip("/").rsplit("/", 1)[-1] if path else "download"

        result = {
            "file_name": file_name,
            "file_content": file_content_b64,
            "file_size": len(file_bytes),
            "content_type": content_type or "application/octet-stream",
        }
    except httpx.HTTPStatusError as exc:
        # Try to parse error JSON from response body
        try:
            error_data = exc.response.json()
            return error_data
        except Exception:
            return {
                "error": True,
                "message": f"下载文件失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            }
    except Exception as exc:
        return {"error": True, "message": f"下载文件请求异常: {exc}"}

    returns_schema = _build_return_schema(result)
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/download",
        "method": "GET",
        "description": "从 HPC 集群文件系统下载文件或文件夹。文件内容以 base64 编码字符串返回，文件夹以 zip 包返回。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "要下载的文件/文件夹绝对路径", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_download", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


# ---------------------------------------------------------------------------
# AI / Notebook Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def notebook_list_resources(
    cluster_ids: Annotated[str, Field(description="区域 ID 列表（逗号分隔，如 \"11250,20057\"）。可从 hpc_list_available_partitions 获取")],
    resource_id: Annotated[Optional[str], Field(description="资源 ID，用于筛选特定型号")] = None,
    cluster_id: Annotated[Optional[int], Field(description="集群 ID（仅用于获取 acToken 时的上下文参考）")] = None,
) -> dict:
    """查询可用的 Notebook 计算资源（加速器）信息，包括 GPU/DCU 型号、可用卡数、资源分组等。

    需要先完成 AK/SK 认证。返回的 clusterId、resourceGroupCode、resourceType 可用于 notebook_create。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    ac_token = auth_result["acToken"]

    if not cluster_ids.strip():
        return {"error": True, "message": "cluster_ids 为必填参数"}

    params: dict[str, Any] = {"clusterIds": cluster_ids}
    if resource_id:
        params["resourceId"] = resource_id

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            "https://www.scnet.cn/ac/openapi/v2/resources/accelerators",
            params=params,
            headers={"token": ac_token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"查询资源信息失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询资源信息请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "https://www.scnet.cn/ac/openapi/v2/resources/accelerators",
        "method": "GET",
        "description": "查询可用的 Notebook 计算资源（加速器）信息，包括 GPU/DCU 型号、可用卡数、资源分组。token 取自 users.acToken。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "cluster_ids": {"type": "string", "description": "区域 ID 列表（逗号分隔）", "optional": False},
                "resource_id": {"type": "string", "description": "资源 ID，用于筛选特定型号", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_list_resources", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_create(
    cluster_id: Annotated[str, Field(description="区域 ID。可从 notebook_list_resources 的 clusterId 字段获取")],
    image_path: Annotated[str, Field(description="镜像地址。可从 notebook_list_images 的 path 字段获取")],
    image_name: Annotated[str, Field(description="镜像名称。可从 notebook_list_images 的 version 字段获取")],
    image_size: Annotated[str, Field(description="镜像大小（byte）。可从 notebook_list_images 的 imageSize 字段获取")],
    accelerator_type: Annotated[str, Field(description="加速器类型（如 DCU、GPU）。可从 notebook_list_resources 获取")],
    accelerator_number: Annotated[str, Field(description="加速器数量")],
    resource_group_code: Annotated[Optional[str], Field(description="资源分组 code。可从 notebook_list_resources 的 resourceGroupCode 字段获取")] = None,
    mount_home: Annotated[Optional[bool], Field(description="是否挂载用户主目录")] = None,
    start_command: Annotated[Optional[str], Field(description="启动容器时执行的命令")] = None,
    mount_info: Annotated[Optional[list[dict]], Field(description="自定义挂载信息列表")] = None,
) -> dict:
    """创建 Notebook 容器实例，支持指定镜像、加速器类型/数量、挂载目录和启动命令。

    需要先完成 AK/SK 认证。建议先调用 notebook_list_images 和 notebook_list_resources 获取所需参数。
    创建是异步操作，返回的 taskId 可用于跟踪创建进度。返回的 notebookId 可用于 notebook_start、notebook_detail 等后续操作。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    ac_token = auth_result["acToken"]

    body: dict[str, Any] = {
        "clusterId": cluster_id,
        "imagePath": image_path,
        "imageName": image_name,
        "imageSize": image_size,
        "acceleratorType": accelerator_type,
        "acceleratorNumber": accelerator_number,
    }
    if resource_group_code is not None:
        body["resourceGroupCode"] = resource_group_code
    if mount_home is not None:
        body["mountHome"] = mount_home
    if start_command is not None:
        body["startCommand"] = start_command
    if mount_info is not None:
        body["mountInfo"] = [
            {
                "sourcePath": m["source_path"],
                "targetPath": m["target_path"],
                "permission": m.get("permission", "ro"),
            }
            for m in mount_info
        ]

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            "https://www.scnet.cn/ac/openapi/v2/notebook/actions/create",
            json=body,
            headers={"token": ac_token, "Content-Type": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"创建 Notebook 失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"创建 Notebook 请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "https://www.scnet.cn/ac/openapi/v2/notebook/actions/create",
        "method": "POST",
        "description": "创建 Notebook 容器实例，支持指定镜像、加速器、挂载目录和启动命令。token 取自 users.acToken。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "cluster_id": {"type": "string", "description": "区域 ID", "optional": False},
                "image_path": {"type": "string", "description": "镜像地址", "optional": False},
                "image_name": {"type": "string", "description": "镜像名称", "optional": False},
                "image_size": {"type": "string", "description": "镜像大小（byte）", "optional": False},
                "accelerator_type": {"type": "string", "description": "加速器类型", "optional": False},
                "accelerator_number": {"type": "string", "description": "加速器数量", "optional": False},
                "resource_group_code": {"type": "string", "description": "资源分组 code", "optional": True},
                "mount_home": {"type": "boolean", "description": "是否挂载主目录", "optional": True},
                "start_command": {"type": "string", "description": "启动命令", "optional": True},
                "mount_info": {"type": "array", "description": "自定义挂载信息", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_create", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_start(
    notebook_id: Annotated[str, Field(description="Notebook 实例 ID。可从 notebook_list 的 id 字段或 notebook_create 返回的 notebookId 字段获取")],
) -> dict:
    """启动（开机）指定的 Notebook 容器实例。

    需要先完成 AK/SK 认证。实例状态应为 Terminated 或 Failed。启动成功后可通过 notebook_query_jupyter_url 获取 Jupyter 访问地址。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    ac_token = auth_result["acToken"]

    if not notebook_id.strip():
        return {"error": True, "message": "notebook_id 为必填参数"}

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            "https://www.scnet.cn/ac/openapi/v2/notebook/actions/start",
            json={"notebookId": notebook_id},
            headers={"token": ac_token, "Content-Type": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"启动 Notebook 失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"启动 Notebook 请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "https://www.scnet.cn/ac/openapi/v2/notebook/actions/start",
        "method": "POST",
        "description": "启动（开机）指定的 Notebook 容器实例。token 取自 users.acToken。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "notebook_id": {"type": "string", "description": "Notebook 实例 ID", "optional": False},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_start", json.dumps(doc, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_list(
    notebook_name: Annotated[Optional[str], Field(description="Notebook 实例名称，支持模糊匹配")] = None,
    notebook_status: Annotated[Optional[str], Field(description="Notebook 状态筛选：Creating/Restarting/Running/Terminated/Failed/Shutting")] = None,
    page: Annotated[Optional[int], Field(description="分页页码")] = 1,
    size: Annotated[Optional[int], Field(description="分页大小")] = 20,
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询当前用户创建的 Notebook 实例列表，支持按名称、状态筛选和分页。

    需要先完成 AK/SK 认证。返回的 records[].id 可作为其他 notebook 工具的 notebook_id 入参。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    params: dict[str, Any] = {}
    if notebook_name:
        params["notebookName"] = notebook_name
    if notebook_status:
        params["notebookStatus"] = notebook_status
    params["page"] = page
    params["size"] = size

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            _ai_url(base_url, "/ai/openapi/v2/notebook/list"),
            params=params,
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"查询 Notebook 列表失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询 Notebook 列表请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/notebook/list",
        "method": "GET",
        "description": "查询当前用户创建的 Notebook 实例列表，支持按名称、状态筛选和分页。token 取自 user_cluster 表。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "notebook_name": {"type": "string", "description": "Notebook 实例名称，支持模糊匹配", "optional": True},
                "notebook_status": {"type": "string", "description": "状态筛选", "optional": True},
                "page": {"type": "integer", "description": "分页页码", "optional": True},
                "size": {"type": "integer", "description": "分页大小", "optional": True},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_list", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_detail(
    notebook_id: Annotated[str, Field(description="Notebook 实例 ID。可从 notebook_list 的 id 字段获取")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询指定 Notebook 实例的详细信息，包括状态、资源配置、镜像、SSH 连接信息等。

    需要先完成 AK/SK 认证。返回的 notebookStatus 可用于判断实例是否可操作，customizePort 和 command 可用于 notebook_start_custom_service。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    if not notebook_id.strip():
        return {"error": True, "message": "notebook_id 为必填参数"}

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            _ai_url(base_url, "/ai/openapi/v2/notebook/detail"),
            params={"notebookId": notebook_id},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"查询 Notebook 详情失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询 Notebook 详情请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/notebook/detail",
        "method": "GET",
        "description": "查询指定 Notebook 实例的详细信息，包括状态、资源配置、镜像、SSH 连接信息。token 取自 user_cluster 表。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "notebook_id": {"type": "string", "description": "Notebook 实例 ID", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_detail", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_stop(
    notebook_id: Annotated[str, Field(description="Notebook 实例 ID。可从 notebook_list 的 id 字段获取")],
    save_env: Annotated[Optional[bool], Field(description="是否保存当前运行环境")] = False,
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """关机指定的 Notebook 容器实例，可选择是否保存运行环境。

    需要先完成 AK/SK 认证。实例状态应为 Running 或 Restarting。关机后（状态变为 Terminated）可调用 notebook_start 重新开机。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    if not notebook_id.strip():
        return {"error": True, "message": "notebook_id 为必填参数"}

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _ai_url(base_url, "/ai/openapi/v2/notebook/actions/stop"),
            json={"notebookId": notebook_id, "saveEnv": save_env},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"关机 Notebook 失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"关机 Notebook 请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/notebook/actions/stop",
        "method": "POST",
        "description": "关机指定的 Notebook 容器实例，可选择是否保存运行环境。token 取自 user_cluster 表。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "notebook_id": {"type": "string", "description": "Notebook 实例 ID", "optional": False},
                "save_env": {"type": "boolean", "description": "是否保存运行环境", "optional": True},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_stop", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_release(
    notebook_id: Annotated[str, Field(description="Notebook 实例 ID。可从 notebook_list 的 id 字段获取")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """释放（删除）指定的 Notebook 容器实例。

    需要先完成 AK/SK 认证。释放操作不可逆，请确认数据已备份。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    if not notebook_id.strip():
        return {"error": True, "message": "notebook_id 为必填参数"}

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _ai_url(base_url, "/ai/openapi/v2/notebook/actions/release"),
            json={"id": notebook_id},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"释放 Notebook 失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"释放 Notebook 请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/notebook/actions/release",
        "method": "POST",
        "description": "释放（删除）指定的 Notebook 容器实例。释放操作不可逆。token 取自 user_cluster 表。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "notebook_id": {"type": "string", "description": "Notebook 实例 ID", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_release", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_rename(
    notebook_id: Annotated[str, Field(description="Notebook 实例 ID。可从 notebook_list 的 id 字段获取")],
    notebook_name: Annotated[str, Field(description="新的 Notebook 名称")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """修改指定 Notebook 实例的名称。

    需要先完成 AK/SK 认证。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    if not notebook_id.strip():
        return {"error": True, "message": "notebook_id 为必填参数"}
    if not notebook_name.strip():
        return {"error": True, "message": "notebook_name 为必填参数"}

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _ai_url(base_url, "/ai/openapi/v2/notebook/name"),
            json={"id": notebook_id, "notebookName": notebook_name},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"重命名 Notebook 失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"重命名 Notebook 请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/notebook/name",
        "method": "POST",
        "description": "修改指定 Notebook 实例的名称。token 取自 user_cluster 表。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "notebook_id": {"type": "string", "description": "Notebook 实例 ID", "optional": False},
                "notebook_name": {"type": "string", "description": "新的名称", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_rename", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_list_images(
    name: Annotated[Optional[str], Field(description="镜像名称（模糊匹配，含版本号）")] = None,
    access: Annotated[Optional[str], Field(description="镜像权限：public（公开）、private（私有）")] = "public",
    type: Annotated[Optional[str], Field(description="镜像类型：JupyterLab、CodeServer、RStudio、Base 等")] = None,
    order_by: Annotated[Optional[str], Field(description="排序字段：create_time/share_time/clone_times")] = "create_time",
    sort: Annotated[Optional[str], Field(description="排序方式：ASC（升序）、DESC（降序）")] = "DESC",
    start: Annotated[Optional[int], Field(description="分页起始条数（从 0 开始）")] = 0,
    limit: Annotated[Optional[int], Field(description="每页数量")] = 20,
    accelerator_type: Annotated[Optional[str], Field(description="加速器类型（如 dcu、gpu）。可从 notebook_list_resources 获取")] = None,
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询可用的 Notebook 镜像列表，支持按名称、类型、加速器类型筛选和分页排序。

    需要先完成 AK/SK 认证。返回的 path、version、imageSize 可用于 notebook_create。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if access:
        body["access"] = access
    if type:
        body["type"] = type
    if order_by:
        body["orderBy"] = order_by
    if sort:
        body["sort"] = sort
    body["start"] = start
    body["limit"] = limit
    if accelerator_type:
        body["acceleratorType"] = accelerator_type

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _ai_url(base_url, "/ai/openapi/v2/image/images"),
            json=body,
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"查询镜像列表失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询镜像列表请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/image/images",
        "method": "POST",
        "description": "查询可用的 Notebook 镜像列表，支持按名称、类型、加速器类型筛选和分页排序。token 取自 user_cluster 表。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "name": {"type": "string", "description": "镜像名称（模糊匹配）", "optional": True},
                "access": {"type": "string", "description": "权限 public/private", "optional": True},
                "type": {"type": "string", "description": "镜像类型", "optional": True},
                "order_by": {"type": "string", "description": "排序字段", "optional": True},
                "sort": {"type": "string", "description": "排序方式", "optional": True},
                "start": {"type": "integer", "description": "起始条数", "optional": True},
                "limit": {"type": "integer", "description": "每页数量", "optional": True},
                "accelerator_type": {"type": "string", "description": "加速器类型", "optional": True},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_list_images", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_list_model_images(
    page: Annotated[Optional[int], Field(description="分页页码")] = 1,
    size: Annotated[Optional[int], Field(description="分页大小")] = 20,
    accelerator_type: Annotated[Optional[str], Field(description="加速器类型（如 dcu、gpu）。可从 notebook_list_resources 获取")] = None,
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询已预置 AI 模型的 Notebook 镜像列表，支持按加速器类型筛选和分页。

    需要先完成 AK/SK 认证。与 notebook_list_images 的区别：本工具返回已预置 AI 模型的特殊镜像。
    返回的 path、version、imageSize 可用于 notebook_create。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    body: dict[str, Any] = {"page": page, "size": size}
    if accelerator_type:
        body["acceleratorType"] = accelerator_type

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _ai_url(base_url, "/ai/openapi/v2/image/models"),
            json=body,
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"查询模型镜像列表失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询模型镜像列表请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/image/models",
        "method": "POST",
        "description": "查询已预置 AI 模型的 Notebook 镜像列表。token 取自 user_cluster 表。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "page": {"type": "integer", "description": "分页页码", "optional": True},
                "size": {"type": "integer", "description": "分页大小", "optional": True},
                "accelerator_type": {"type": "string", "description": "加速器类型", "optional": True},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_list_model_images", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_query_jupyter_url(
    notebook_id: Annotated[str, Field(description="Notebook 实例 ID。可从 notebook_list 的 id 字段获取")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询指定 Notebook 实例的 JupyterLab 服务访问地址。

    需要先完成 AK/SK 认证。实例需处于 Running 状态。若 status 为 inactive，请先调用 notebook_start 开机。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    if not notebook_id.strip():
        return {"error": True, "message": "notebook_id 为必填参数"}

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            _ai_url(base_url, "/ai/openapi/v2/notebook/url"),
            params={"notebookId": notebook_id},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"查询 Jupyter 地址失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询 Jupyter 地址请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/notebook/url",
        "method": "GET",
        "description": "查询指定 Notebook 实例的 JupyterLab 服务访问地址。token 取自 user_cluster 表。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "notebook_id": {"type": "string", "description": "Notebook 实例 ID", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_query_jupyter_url", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_query_custom_service_url(
    notebook_id: Annotated[str, Field(description="Notebook 实例 ID。可从 notebook_list 的 id 字段获取")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询指定 Notebook 实例中用户自定义服务的访问地址。

    需要先调用 notebook_start_custom_service 启动自定义服务。若 status 为 inactive，表示服务不可访问。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    if not notebook_id.strip():
        return {"error": True, "message": "notebook_id 为必填参数"}

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            _ai_url(base_url, "/ai/openapi/v2/notebook/customize-service/url"),
            params={"notebookId": notebook_id},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"查询自定义服务地址失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询自定义服务地址请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/notebook/customize-service/url",
        "method": "GET",
        "description": "查询指定 Notebook 实例中用户自定义服务的访问地址。token 取自 user_cluster 表。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "notebook_id": {"type": "string", "description": "Notebook 实例 ID", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_query_custom_service_url", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


@mcp.tool()
async def notebook_start_custom_service(
    notebook_id: Annotated[str, Field(description="Notebook 实例 ID。可从 notebook_list 的 id 字段获取")],
    customize_port: Annotated[str, Field(description="自定义服务监听的端口号。可从 notebook_detail 的 customizePort 字段获取参考值")],
    command: Annotated[Optional[str], Field(description="自定义服务启动命令。可从 notebook_detail 的 command 字段获取参考值")] = None,
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """在指定 Notebook 容器实例中启动用户自定义服务（如 WebUI、API 服务等）。

    需要先完成 AK/SK 认证，实例需处于 Running 状态。

    重要：返回值 data.execSuccess 为 false 不表示失败——系统可能已自动建立网络通路。
    只要 code === "0"，都应调用 notebook_query_custom_service_url 查询实际访问地址。
    """
    username = get_current_username()
    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    if not notebook_id.strip() or not customize_port.strip():
        return {"error": True, "message": "notebook_id 和 customize_port 为必填参数"}

    conn = get_db()
    try:
        if cluster_id is not None:
            row = conn.execute(
                "SELECT uc.token, cu.aiUrls "
                "FROM user_cluster uc "
                "LEFT JOIN cluster_url cu ON uc.clusterId = cu.clusterId "
                "WHERE uc.userName = ? AND uc.clusterId = ?",
                (username, cluster_id),
            ).fetchone()
            if row is None:
                return {"error": True, "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。"}
            token = row["token"]
            ai_urls = row["aiUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            ai_urls = resolved.get("aiUrls", "")
    finally:
        conn.close()

    if not ai_urls:
        return {"error": True, "message": "未查询到 AI 服务 URL（aiUrls）。"}

    valid_urls = [u.strip().rstrip("/") for u in ai_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 AI 服务 URL。"}

    _cid = str(cluster_id) if cluster_id is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    body: dict[str, Any] = {"id": notebook_id, "customizePort": customize_port}
    if command:
        body["command"] = command

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _ai_url(base_url, "/ai/openapi/v2/notebook/customize-service/actions/start"),
            json=body,
            headers={"token": token, "Content-Type": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": f"启动自定义服务失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"启动自定义服务请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/notebook/customize-service/actions/start",
        "method": "POST",
        "description": "在指定 Notebook 容器实例中启动用户自定义服务。token 取自 user_cluster 表。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "notebook_id": {"type": "string", "description": "Notebook 实例 ID", "optional": False},
                "customize_port": {"type": "string", "description": "监听端口号", "optional": False},
                "command": {"type": "string", "description": "启动命令", "optional": True},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("notebook_start_custom_service", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return _enrich_auth_error(result, username)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Register existing DB proxy tools at import time
_count = register_apis(mcp)
if _count:
    print(f"[mcp] registered {_count} proxy API tool(s) from {DB_PATH}")


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
