"""SCNet OpenAPI MCP Server.

StreamableHTTP MCP server with AK/SK-based user authentication.
Each user connects at /mcp/{userName} and authenticates at /auth/{userName}.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html as html_mod
import inspect
import json
import math
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
# File transfer limits & streaming helpers
# ---------------------------------------------------------------------------

# Maximum single-shot upload / download size (before base64): 100 MB
# Above this, chunked transfer is required.
MAX_SINGLE_TRANSFER_BYTES = 100 * 1024 * 1024

# Absolute hard limit for total file size: 5 GB
# Beyond this the request is rejected.
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024 * 1024

# Base64 B64CHUNK: process in multiples of 4 chars (4 b64 chars = 3 raw bytes)
B64_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB base64 → 3 MB raw

# HTTP streaming buffer size for download
HTTP_STREAM_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB

# File size threshold for inline vs chunked/download-link mode
# Files ≤ this size are returned inline as base64; larger files use
# chunked download or generate a direct download link.
B64_INLINE_THRESHOLD = 10 * 1024 * 1024  # ~7.5 MB raw = 10 MB base64

# Default chunk size for chunked download (raw bytes)
CHUNK_DOWNLOAD_SIZE = 5 * 1024 * 1024  # 5 MB

# Chunk upload: each chunk up to 50 MB raw (before base64 ≈ 66.7 MB encoded)
CHUNK_UPLOAD_SIZE = 50 * 1024 * 1024

# Batch chunk upload: at most 10 chunks per batch call
MAX_CHUNKS_PER_BATCH = 10


def _b64_raw_size(b64_string: str) -> int:
    """Calculate exact raw byte count from a base64 string (accounting for padding)."""
    padding = 2 if b64_string.endswith("==") else 1 if b64_string.endswith("=") else 0
    return (len(b64_string) - padding) * 3 // 4


def _validate_file_size(base64_string: str) -> None:
    """Validate that the decoded file does not exceed MAX_FILE_SIZE_BYTES.

    Raises ValueError with a user-friendly message if too large.
    """
    # base64 encodes 3 bytes into 4 characters → raw ≈ len * 3 / 4
    estimated_raw = int(len(base64_string) * 3 / 4)
    if estimated_raw > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"文件过大：约 {estimated_raw / (1024**3):.1f} GB，"
            f"超过最大支持 {MAX_FILE_SIZE_BYTES / (1024**3):.0f} GB。"
            f"请使用 SCP/SFTP 等直连方式传输超大文件。"
        )


def _b64decode_stream(b64_string: str) -> bytes:
    """Decode a large base64 string in chunks to reduce peak memory.

    Splits the string into B64_CHUNK_SIZE (multiple of 4) blocks, decodes
    each block independently, and joins. Peak memory ≈ one block instead of
    the whole string + whole raw data simultaneously.
    """
    _validate_file_size(b64_string)

    if len(b64_string) <= B64_CHUNK_SIZE:
        return base64.b64decode(b64_string)

    parts = []
    for i in range(0, len(b64_string), B64_CHUNK_SIZE):
        block = b64_string[i:i + B64_CHUNK_SIZE]
        # Ensure block length is a multiple of 4 (required by b64decode)
        padding = len(block) % 4
        if padding:
            block += "=" * (4 - padding)
        parts.append(base64.b64decode(block))
    return b"".join(parts)


async def _b64encode_stream(raw_bytes_iter) -> str:
    """Base64-encode binary chunks from an async iterable into a single string.

    Uses an async generator / aiter_bytes pattern to avoid holding the full
    raw bytes + full base64 string in memory simultaneously.
    """
    parts: list[str] = []
    total_raw = 0
    async for chunk in raw_bytes_iter:
        total_raw += len(chunk)
        if total_raw > MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"文件过大：超过 {MAX_FILE_SIZE_BYTES / (1024**3):.0f} GB，下载已中止。"
            )
        parts.append(base64.b64encode(chunk).decode("ascii"))
    return "".join(parts)

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
# Token renewal — transparent refresh when SCNet returns code 10008
# ---------------------------------------------------------------------------

SCNET_RENEW_TOKEN_URL = "https://www.scnet.cn/ac/openapi/v2/tokens/next"
SCNET_TOKEN_STATE_URL = "https://www.scnet.cn/ac/openapi/v2/tokens/state"

# Per-user lock to avoid concurrent token renewal for the same user
_token_renewal_locks: dict[str, asyncio.Lock] = {}


def _get_renewal_lock(username: str) -> asyncio.Lock:
    if username not in _token_renewal_locks:
        _token_renewal_locks[username] = asyncio.Lock()
    return _token_renewal_locks[username]


def _is_token_expired_response(resp_data: dict) -> bool:
    """Check whether a SCNet API JSON response indicates token expiry."""
    return str(resp_data.get("code", "")) == "10008"


def _build_auth_error(username: str, reason: str = "Token 已过期且无法自动续约") -> dict:
    """Build a uniform "please re-authenticate" error response."""
    return {
        "error": True,
        "message": f"认证凭证已失效：{reason}。请重新认证。",
        "auth_url": f"{AUTH_BASE_URL}/auth/{username}",
    }


async def _renew_token_via_api(old_token: str) -> tuple[bool, str]:
    """Attempt token renewal via SCNet.

    Returns ``(True, new_token)`` on success, ``(False, error_msg)`` on failure.
    Reference: https://www.scnet.cn/ac/openapi/doc/2.0/api/safecertification/renewal-token.html
    """
    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            SCNET_RENEW_TOKEN_URL,
            headers={
                "token": old_token,
                "Content-Type": "application/json",
            },
            json={},
            timeout=15.0,
        )
        data = resp.json()
        if data.get("code") == "0" and data.get("data"):
            return True, data["data"]
        # code 10008 or other error → cannot renew
        return False, data.get("msg", "Token 续约失败（可能已超过 24 小时）")
    except Exception as exc:
        return False, f"Token 续约请求异常: {exc}"


async def _check_token_valid(token: str) -> bool:
    """Verify a token is still accepted by SCNet.

    Reference: https://www.scnet.cn/ac/openapi/doc/2.0/api/safecertification/get-token-state.html
    """
    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            SCNET_TOKEN_STATE_URL,
            headers={"token": token},
            timeout=10.0,
        )
        return resp.json().get("code") == "0"
    except Exception:
        return False


async def _renew_and_persist_token(
    username: str,
    old_token: str,
    token_type: str,          # "ac" or "cluster"
    cluster_id: int | None = None,
) -> tuple[bool, str]:
    """Renew a token, persist the new token to DB on success, handle concurrency.

    Args:
        username: SCNet username.
        old_token: The current (possibly expired) token.
        token_type: ``"ac"`` → update ``users.acToken``; ``"cluster"`` → update
                    ``user_cluster.token`` for *cluster_id*.
        cluster_id: Required when *token_type* is ``"cluster"``.

    Returns:
        ``(True, new_token)`` on success, ``(False, error_reason)`` on failure.
    """
    lock = _get_renewal_lock(username)
    async with lock:
        # Double-check: maybe another concurrent call already renewed it
        conn = get_db()
        try:
            if token_type == "ac":
                current = conn.execute(
                    "SELECT acToken FROM users WHERE userName = ?", (username,)
                ).fetchone()
                if current and current["acToken"] != old_token:
                    return True, current["acToken"]  # already renewed
            else:
                current = conn.execute(
                    "SELECT token FROM user_cluster WHERE userName = ? AND clusterId = ?",
                    (username, cluster_id),
                ).fetchone()
                if current and current["token"] != old_token:
                    return True, current["token"]  # already renewed
        finally:
            conn.close()

        # Attempt renewal via SCNet API
        ok, result = await _renew_token_via_api(old_token)
        if not ok:
            return False, result

        new_token = result

        # Persist the new token
        now = time_mod.strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        try:
            if token_type == "ac":
                conn.execute(
                    "UPDATE users SET acToken = ?, updated_at = ? WHERE userName = ?",
                    (new_token, now, username),
                )
            else:
                conn.execute(
                    "UPDATE user_cluster SET token = ?, updated_at = ? "
                    "WHERE userName = ? AND clusterId = ?",
                    (new_token, now, username, cluster_id),
                )
            conn.commit()
        finally:
            conn.close()

        return True, new_token


async def _call_scnet_with_renewal(
    username: str,
    method: str,
    url: str,
    *,
    token: str,
    token_type: str = "cluster",
    cluster_id: int | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
    **httpx_kwargs,
) -> dict:
    """Make an HTTP call to SCNet; if token is expired (code 10008), renew and retry once.

    This is the single entry-point for transparent token renewal. Tools that
    want automatic renewal should call this instead of raw ``client.request()``.

    Args:
        username: SCNet username (for renewal context).
        method: HTTP method.
        url: Full request URL.
        token: The current token to authenticate with.
        token_type: ``"ac"`` or ``"cluster"`` — used to decide which DB table to update.
        cluster_id: Needed when *token_type* is ``"cluster"``.
        headers: Additional headers (token is injected automatically).
        timeout: Per-request timeout.
        **httpx_kwargs: Passed to ``client.request()`` (params, json, data, files, etc.).

    Returns:
        The JSON response dict from SCNet, or an error dict with ``error=True``
        and ``auth_url`` if renewal also fails.
    """
    req_headers = {"token": token}
    if headers:
        req_headers.update(headers)

    client = _get_http_client(timeout=timeout)

    # -- Helper: dispatch to the right httpx method -----------------------------------
    async def _do_request(hdrs: dict) -> dict:
        """Issue the HTTP request and return parsed JSON, or raise on error."""
        method_upper = method.upper()
        if method_upper == "GET":
            resp = await client.get(url, headers=hdrs, timeout=timeout, **httpx_kwargs)
        elif method_upper == "POST":
            resp = await client.post(url, headers=hdrs, timeout=timeout, **httpx_kwargs)
        elif method_upper == "DELETE":
            resp = await client.delete(url, headers=hdrs, timeout=timeout, **httpx_kwargs)
        else:
            resp = await client.request(method, url, headers=hdrs, timeout=timeout, **httpx_kwargs)
        resp.raise_for_status()
        return resp.json()

    # -- First attempt --------------------------------------------------------------
    try:
        data = await _do_request(req_headers)
    except httpx.HTTPStatusError as exc:
        try:
            data = exc.response.json()
        except Exception:
            return {
                "error": True,
                "message": f"请求失败 (HTTP {exc.response.status_code}): {exc.response.text[:500]}",
            }
    except Exception as exc:
        return {"error": True, "message": f"请求异常: {exc}"}

    # If token is not expired, return as-is
    if not _is_token_expired_response(data):
        return data

    # Token expired → attempt renewal
    ok, result = await _renew_and_persist_token(
        username, token, token_type, cluster_id,
    )
    if not ok:
        return _build_auth_error(username, f"Token 续约失败: {result}")

    # Retry with the new token
    new_token = result
    req_headers["token"] = new_token

    try:
        data = await _do_request(req_headers)
        return data
    except httpx.HTTPStatusError as exc:
        try:
            data = exc.response.json()
        except Exception:
            return {
                "error": True,
                "message": f"重试请求失败 (HTTP {exc.response.status_code}): {exc.response.text[:500]}",
            }
        # If the NEW token is also expired (shouldn't happen normally)
        if _is_token_expired_response(data):
            return _build_auth_error(username, "续约后的 token 仍然失效")
        return data
    except Exception as exc:
        return {"error": True, "message": f"重试请求异常: {exc}"}


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

    return data


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

    return results


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


@mcp.tool()
async def hpc_query_job_state(
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询当前用户在 HPC 集群上的作业状态统计信息。

    返回各状态的作业个数：运行、排队、保留、挂起、其他。
    token 取自 user_cluster 表，hpcUrls 取自 cluster_url 表。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.hpcUrls "
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
            hpc_urls = row["hpcUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            hpc_urls = resolved.get("hpcUrls", "")
    finally:
        conn.close()

    if not hpc_urls:
        return {
            "error": True,
            "message": "未查询到集群 HPC 服务 URL。请先调用 hpc_list_available_partitions 获取可用队列。",
        }

    valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 HPC 服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            f"{base_url}/hpc/openapi/v2/view/jobs/state",
            params={"userName": username},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询作业状态统计失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询作业状态统计请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/view/jobs/state",
        "method": "GET",
        "description": (
            "查询当前用户在 HPC 集群上的作业状态统计信息。"
            "返回各状态（运行、排队、保留、挂起、其他）的作业个数。"
        ),
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("hpc_query_job_state", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def hpc_query_core_num(
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询 HPC 集群的 CPU 核心数状态统计信息。

    返回已使用、未使用、不可用三种状态的核心数。
    token 取自 user_cluster 表，hpcUrls 取自 cluster_url 表。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.hpcUrls "
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
            hpc_urls = row["hpcUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            hpc_urls = resolved.get("hpcUrls", "")
    finally:
        conn.close()

    if not hpc_urls:
        return {
            "error": True,
            "message": "未查询到集群 HPC 服务 URL。请先调用 hpc_list_available_partitions 获取可用队列。",
        }

    valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 HPC 服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            f"{base_url}/hpc/openapi/v2/view/cpucore/state",
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询核心数状态统计失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询核心数状态统计请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/view/cpucore/state",
        "method": "GET",
        "description": (
            "查询 HPC 集群的 CPU 核心数状态统计信息。"
            "返回已使用、未使用、不可用三种状态的核心数。"
        ),
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("hpc_query_core_num", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def hpc_query_queue_jobs(
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询当前用户在 HPC 集群各队列中的作业统计信息。

    按队列维度统计各状态（运行 R、排队 Q、保留 H、挂起 S、其他 O）的作业数量。
    token 取自 user_cluster 表，hpcUrls 取自 cluster_url 表。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.hpcUrls "
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
            hpc_urls = row["hpcUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            hpc_urls = resolved.get("hpcUrls", "")
    finally:
        conn.close()

    if not hpc_urls:
        return {
            "error": True,
            "message": "未查询到集群 HPC 服务 URL。请先调用 hpc_list_available_partitions 获取可用队列。",
        }

    valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 HPC 服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            f"{base_url}/hpc/openapi/v2/view/queue/jobs",
            params={"userName": username},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询队列作业统计失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询队列作业统计请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/view/queue/jobs",
        "method": "GET",
        "description": (
            "查询当前用户在 HPC 集群各队列中的作业统计信息。"
            "按队列维度统计各状态（R运行/Q排队/H保留/S挂起/O其他）的作业数量。"
        ),
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("hpc_query_queue_jobs", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def hpc_query_user_quota(
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询当前用户在 HPC 集群上的共享存储配额及使用量。

    返回每个存储路径的配额总量（GB）和已使用量（GB）。
    token 取自 user_cluster 表，hpcUrls 取自 cluster_url 表。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.hpcUrls "
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
            hpc_urls = row["hpcUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            hpc_urls = resolved.get("hpcUrls", "")
    finally:
        conn.close()

    if not hpc_urls:
        return {
            "error": True,
            "message": "未查询到集群 HPC 服务 URL。请先调用 hpc_list_available_partitions 获取可用队列。",
        }

    valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 HPC 服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            f"{base_url}/hpc/openapi/v2/parastor/quota/usernames/{username}",
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询共享存储配额失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询共享存储配额请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/parastor/quota/usernames/{username}",
        "method": "GET",
        "description": (
            "查询当前用户在 HPC 集群上的共享存储配额及使用量。"
            "返回各存储路径的配额总量（threshold，GB）和已使用量（usage，GB）。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("hpc_query_user_quota", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def hpc_query_used_time(
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群（isDefault=true）")] = None,
) -> dict:
    """查询当前用户在 HPC 集群上已使用的 CPU 机时总量。

    返回值为该用户已使用的 CPU 机时（单位：核·小时）。
    token 取自 user_cluster 表，hpcUrls 取自 cluster_url 表。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    conn = get_db()
    try:
        if clusterId is not None:
            row = conn.execute(
                "SELECT uc.token, cu.hpcUrls "
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
            hpc_urls = row["hpcUrls"]
        else:
            resolved = _get_default_token(username)
            if "error" in resolved:
                return resolved
            token = resolved["token"]
            hpc_urls = resolved.get("hpcUrls", "")
    finally:
        conn.close()

    if not hpc_urls:
        return {
            "error": True,
            "message": "未查询到集群 HPC 服务 URL。请先调用 hpc_list_available_partitions 获取可用队列。",
        }

    valid_urls = [u.strip().rstrip("/") for u in hpc_urls.split(",") if u.strip()]
    if not valid_urls:
        return {"error": True, "message": "未找到有效的 HPC 服务 URL。"}

    _cid = str(clusterId) if clusterId is not None else "default"
    _idx = _url_idx_ctx.get(_cid, 0)
    base_url = valid_urls[_idx % len(valid_urls)]
    _url_idx_ctx[_cid] = _idx + 1

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.get(
            f"{base_url}/hpc/openapi/v2/view/walltime/users/{username}",
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询已用机时失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询已用机时请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{hpcUrls}/hpc/openapi/v2/view/walltime/users/{username}",
        "method": "GET",
        "description": (
            "查询当前用户在 HPC 集群上已使用的 CPU 机时总量。"
            "返回值为已使用的 CPU 机时数（单位：核·小时）。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("hpc_query_used_time", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


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
    resolved_cluster_id: int | None = clusterId
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
            resolved_cluster_id = resolved.get("clusterId")
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

    # 4. Call efile API (with transparent token renewal on code 10008)
    result = await _call_scnet_with_renewal(
        username=username,
        method="GET",
        url=_efile_url(base_url, "/efile/openapi/v2/file/list"),
        token=token,
        token_type="cluster",
        cluster_id=resolved_cluster_id,
        params=params,
        timeout=15.0,
    )
    if isinstance(result, dict) and result.get("error"):
        return result

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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


@mcp.tool()
async def efile_upload(
    file_content: Annotated[str, Field(description="文件内容的 base64 编码字符串")],
    file_name: Annotated[str, Field(description="原始文件名（如 result.txt）")],
    remote_path: Annotated[str, Field(description="远程目标文件夹路径（必须为绝对路径）")],
    cover: Annotated[str, Field(description="覆盖策略：cover（强制覆盖）或 uncover（不覆盖）")] = "uncover",
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """上传文件到 HPC 集群文件系统的指定路径。文件内容通过 base64 编码字符串传入。

    小文件（≤100MB）直接上传，大文件（100MB-5GB）自动使用分片上传。
    超过 5GB 的文件将被拒绝。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # Decode base64 file content (streaming to reduce peak memory)
    try:
        file_bytes = _b64decode_stream(file_content)
    except (ValueError, Exception) as exc:
        msg = str(exc) if isinstance(exc, ValueError) and "文件过大" in str(exc) else f"文件内容 base64 解码失败: {exc}"
        return {"error": True, "message": msg}

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
            timeout=60.0 if len(file_bytes) < 50 * 1024 * 1024 else min(300.0, len(file_bytes) / (50 * 1024 * 1024) * 120),
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
        "description": "上传文件到 HPC 集群文件系统的指定路径。≤100MB 直接上传，>100MB 建议使用分片上传。最大 5GB。",
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

    return result


@mcp.tool()
async def efile_download(
    path: Annotated[str, Field(description="要下载的文件/文件夹绝对路径")],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """从 HPC 集群文件系统下载文件或文件夹。

    - **小文件（≤10 MB）**：直接返回 base64 编码内容。
    - **大文件（>10 MB）**：返回下载链接（含 token），可通过浏览器或 curl 直接下载。
    - 超过 5 GB 的文件将被拒绝。
    """
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

    # First, fetch file metadata to check size
    try:
        client = _get_http_client(timeout=30.0)
        resp_meta = await client.get(
            _efile_url(base_url, "/efile/openapi/v2/file/download"),
            params={"path": path},
            headers={"token": token},
            timeout=30.0,
        )

        content_type = resp_meta.headers.get("content-type", "")
        # If API returns JSON (error or metadata), check file size
        if content_type and "json" in content_type:
            meta_json = resp_meta.json()
            if "error" in meta_json or "code" in meta_json:
                return meta_json
            # Meta response may contain file_size
            if isinstance(meta_json, dict):
                file_size = meta_json.get("fileSize", meta_json.get("file_size", 0))
            else:
                file_size = 0
        else:
            # Binary response — check content-length header
            cl = resp_meta.headers.get("content-length")
            file_size = int(cl) if cl else 0

        # Derive file name
        content_disposition = resp_meta.headers.get("content-disposition", "")
        file_name = ""
        if content_disposition:
            match = re.search(r'filename[^;=\n]*=((["\']).*?\2|[^;\n]*)', content_disposition)
            if match:
                file_name = match.group(1).strip().strip('"\'')
        if not file_name:
            file_name = path.rstrip("/").rsplit("/", 1)[-1] if path else "download"

        # --- Small file: return inline base64 ---
        if file_size <= B64_INLINE_THRESHOLD:
            # Re-fetch the actual content (meta response was just for size check)
            _validate_file_size("")  # reset any prior state
            resp = await client.get(
                _efile_url(base_url, "/efile/openapi/v2/file/download"),
                params={"path": path},
                headers={"token": token},
                timeout=300.0,
            )
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if content_type and "json" in content_type:
                return resp.json()

            if hasattr(resp, "aiter_bytes") and callable(resp.aiter_bytes):
                file_content_b64 = await _b64encode_stream(
                    resp.aiter_bytes(chunk_size=HTTP_STREAM_CHUNK_SIZE)
                )
            else:
                file_bytes = resp.content
                file_content_b64 = base64.b64encode(file_bytes).decode("utf-8")

            return {
                "file_name": file_name,
                "file_size": file_size,
                "file_content_b64": file_content_b64,
                "download_method": "inline",
                "content_type": content_type or "application/octet-stream",
                "message": f"文件较小 ({file_size / 1024 / 1024:.1f} MB)，内容已以内联 base64 形式返回。",
            }

        # --- Large file: return download link ---
        # Generate a direct download link with token in query string
        download_url = (
            f"{base_url}/efile/openapi/v2/file/download?"
            f"path={path}&token={token}"
        )

        return {
            "file_name": file_name,
            "file_size": file_size,
            "download_url": download_url,
            "download_method": "link",
            "message": (
                f"文件较大 ({file_size / 1024 / 1024:.1f} MB)，"
                f"内容太大不适合通过 MCP 协议传输。"
                f"\n\n"
                f"💡 下载方式：\n"
                f"  1. 浏览器直接打开: {download_url[:120]}...\n"
                f"  2. curl 下载: curl -H 'token: {token[:12]}...' "
                f"'{download_url[:120]}' -o {file_name}\n"
                f"\n"
                f"如需通过 MCP 分块下载，请使用工具: efile_download_chunk"
            ),
        }

    except ValueError as exc:
        return {"error": True, "message": str(exc)}
    except httpx.HTTPStatusError as exc:
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


# ---------------------------------------------------------------------------
# efile — chunked download & download link helpers
# ---------------------------------------------------------------------------


@mcp.tool()
async def efile_download_chunk(
    path: Annotated[str, Field(description="要下载的文件绝对路径")],
    chunk_index: Annotated[int, Field(description="分块索引，从 0 开始。chunk_index=0 返回第 1 个 5MB 块")] = 0,
    chunk_size: Annotated[Optional[int], Field(description="每个分块的大小（字节），默认 5MB。最小 1MB，最大 100MB")] = None,
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """分块下载文件。按指定分块大小和索引获取文件片段的 base64 编码。

    默认每块 5 MB。chunk_index=0 返回第 1 块，chunk_index=1 返回第 2 块，依此类推。
    适合通过 MCP 协议下载大文件（>10 MB），每次调用返回一个分块。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    if chunk_size is not None:
        chunk_size = max(1024 * 1024, min(chunk_size, 100 * 1024 * 1024))  # clamp [1MB, 100MB]
    else:
        chunk_size = CHUNK_DOWNLOAD_SIZE

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

    start = chunk_index * chunk_size
    end = start + chunk_size - 1

    # First get file size to validate chunk_index
    try:
        client = _get_http_client(timeout=30.0)
        resp_meta = await client.get(
            _efile_url(base_url, "/efile/openapi/v2/file/download"),
            params={"path": path},
            headers={"token": token},
            timeout=30.0,
        )

        content_type = resp_meta.headers.get("content-type", "")
        if content_type and "json" in content_type:
            meta_json = resp_meta.json()
            if "error" in meta_json or "code" in meta_json:
                return meta_json
            file_size = meta_json.get("fileSize", meta_json.get("file_size", 0))
        else:
            cl = resp_meta.headers.get("content-length")
            file_size = int(cl) if cl else 0

        file_name = ""
        content_disposition = resp_meta.headers.get("content-disposition", "")
        if content_disposition:
            match = re.search(r'filename[^;=\n]*=((["\']).*?\2|[^;\n]*)', content_disposition)
            if match:
                file_name = match.group(1).strip().strip('"\'')
        if not file_name:
            file_name = path.rstrip("/").rsplit("/", 1)[-1] if path else "download"

        total_chunks = math.ceil(file_size / chunk_size) if file_size > 0 else 0

        if chunk_index >= total_chunks:
            return {
                "error": True,
                "message": f"分块索引 {chunk_index} 超出范围（文件共 {total_chunks} 个分块）。",
                "file_name": file_name,
                "file_size": file_size,
                "total_chunks": total_chunks,
                "chunk_index": chunk_index,
                "chunk_size": chunk_size,
            }

        actual_end = min(end, file_size - 1)
        range_header = f"bytes={start}-{actual_end}"

        # Download the chunk with Range header
        resp = await client.get(
            _efile_url(base_url, "/efile/openapi/v2/file/download"),
            params={"path": path},
            headers={"token": token, "Range": range_header},
            timeout=60.0,
        )

        if resp.status_code == 206:  # Partial Content
            chunk_data = resp.content
            chunk_b64 = base64.b64encode(chunk_data).decode("ascii")
            return {
                "file_name": file_name,
                "file_size": file_size,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "chunk_size": chunk_size,
                "chunk_start": start,
                "chunk_end": actual_end,
                "chunk_length": len(chunk_data),
                "file_content_b64": chunk_b64,
                "is_last_chunk": chunk_index == total_chunks - 1,
                "message": (
                    f"第 {chunk_index + 1}/{total_chunks} 块已返回 "
                    f"({len(chunk_data) / 1024:.1f} KB raw / {len(chunk_b64) / 1024:.1f} KB b64)。"
                    + (" 这是最后一块。" if chunk_index == total_chunks - 1 else " 还有后续分块。")
                ),
            }
        elif resp.status_code == 416:  # Range Not Satisfiable
            return {
                "error": True,
                "message": f"Range {range_header} 超出文件范围（文件大小: {file_size} bytes）。",
                "file_name": file_name,
                "file_size": file_size,
            }
        else:
            resp.raise_for_status()
            return {
                "error": True,
                "message": f"分块下载返回 HTTP {resp.status_code}，预期 206。",
                "file_name": file_name,
            }

    except httpx.HTTPStatusError as exc:
        try:
            error_data = exc.response.json()
            return error_data
        except Exception:
            return {
                "error": True,
                "message": f"分块下载失败 (HTTP {exc.response.status_code})。详情: {exc.response.text[:500]}",
            }
    except Exception as exc:
        return {"error": True, "message": f"分块下载请求异常: {exc}"}


@mcp.tool()
async def efile_get_download_link(
    path: Annotated[str, Field(description="要下载的文件/文件夹绝对路径")],
    expires_in: Annotated[Optional[int], Field(description="链接有效期（秒），默认 3600 秒（1小时），最大 86400 秒（24小时）")] = 3600,
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """生成文件的直接下载链接。返回一个包含认证 token 的 HTTP URL，
    可通过浏览器、curl 或其他 HTTP 客户端直接下载，无需经过 MCP 协议。

    适合大文件下载，避免 MCP 协议传输大体积 base64 数据。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    expires_in = max(60, min(expires_in or 3600, 86400))

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

    base_url = valid_urls[0]

    # Encode path for URL
    encoded_path = path.replace(" ", "%20")

    download_url = (
        f"{base_url}/efile/openapi/v2/file/download?"
        f"path={encoded_path}&token={token}&expires={expires_in}"
    )

    file_name = path.rstrip("/").rsplit("/", 1)[-1] if path else "download"

    return {
        "file_name": file_name,
        "download_url": download_url,
        "expires_in": expires_in,
        "expires_at": time_mod.time() + expires_in,
        "usage": (
            f"📎 直接下载:\n"
            f"  浏览器: {download_url[:120]}...\n"
            f"  curl: curl -L -o '{file_name}' '{download_url[:120]}'\n"
            f"  wget: wget -O '{file_name}' '{download_url[:120]}'"
        ),
        "warning": "下载链接包含认证 token，请勿分享给他人。",
    }


# ---------------------------------------------------------------------------
# efile — share / async / chunk tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def efile_open_share(
    file_path: Annotated[str, Field(description="要分享的文件的绝对路径")],
    valid_days: Annotated[int, Field(description="链接有效天数，默认 30 天")] = 30,
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """在 HPC 集群文件系统上为指定文件创建分享链接。

    返回包含 serverCurlLink、serverFastransLink、webLink 和有效时长。
    """
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
            _efile_url(base_url, "/efile/openapi/v2/file/open-share"),
            params={"filePath": file_path, "validDays": valid_days},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"开启文件分享失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"开启文件分享请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/open-share",
        "method": "POST",
        "description": "为 HPC 集群文件系统上的指定文件创建分享链接，返回多种访问方式。",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "file_path": {"type": "string", "description": "要分享的文件的绝对路径", "optional": False},
                "valid_days": {"type": "integer", "description": "链接有效天数，默认 30 天", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_open_share", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_close_share(
    file_path: Annotated[str, Field(description="已分享文件的绝对路径")],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """关闭指定文件的分享链接。"""
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
            _efile_url(base_url, "/efile/openapi/v2/file/close-share"),
            params={"filePath": file_path},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"关闭文件分享失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"关闭文件分享请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/close-share",
        "method": "POST",
        "description": "关闭指定文件的分享链接。",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "file_path": {"type": "string", "description": "已分享文件的绝对路径", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_close_share", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_async_copy(
    tasks: Annotated[str, Field(
        description="异步复制任务列表（JSON 数组字符串）。"
        "每个元素包含 sourcePath（源文件绝对路径）、targetPath（目标目录绝对路径）、"
        "duplicateHandleType（重复处理方式：cover 覆盖 / both 保留二者，可选）。"
        "示例: [{\"sourcePath\": \"/home/a.txt\", \"targetPath\": \"/home/backup\", \"duplicateHandleType\": \"cover\"}]"
    )],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """异步复制文件/文件夹。支持批量提交（最多 100 个任务），返回 taskId 用于查询进度和取消。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # Parse tasks JSON
    try:
        tasks_list = json.loads(tasks)
    except json.JSONDecodeError as exc:
        return {"error": True, "message": f"tasks 参数 JSON 解析失败: {exc}"}

    if not isinstance(tasks_list, list) or len(tasks_list) == 0:
        return {"error": True, "message": "tasks 必须是非空 JSON 数组。"}

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
            _efile_url(base_url, "/efile/openapi/v2/file/async-copy"),
            json=tasks_list,
            headers={"token": token, "Content-Type": "application/json"},
            timeout=60.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"异步复制失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"异步复制请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/async-copy",
        "method": "POST",
        "description": "异步复制文件/文件夹，支持批量提交（最多 100 个任务），返回 taskId 用于查询进度和取消。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "tasks": {"type": "string", "description": "JSON 数组，每元素含 sourcePath/targetPath/duplicateHandleType", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_async_copy", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_async_move(
    tasks: Annotated[str, Field(
        description="异步移动任务列表（JSON 数组字符串）。"
        "每个元素包含 sourcePath（源文件绝对路径）、targetPath（目标目录绝对路径）、"
        "duplicateHandleType（重复处理方式：cover 覆盖 / both 保留二者，可选）。"
    )],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """异步移动文件/文件夹。支持批量提交（最多 100 个任务），返回 taskId 用于查询进度和取消。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    try:
        tasks_list = json.loads(tasks)
    except json.JSONDecodeError as exc:
        return {"error": True, "message": f"tasks 参数 JSON 解析失败: {exc}"}

    if not isinstance(tasks_list, list) or len(tasks_list) == 0:
        return {"error": True, "message": "tasks 必须是非空 JSON 数组。"}

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
            _efile_url(base_url, "/efile/openapi/v2/file/async-move"),
            json=tasks_list,
            headers={"token": token, "Content-Type": "application/json"},
            timeout=60.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"异步移动失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"异步移动请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/async-move",
        "method": "POST",
        "description": "异步移动文件/文件夹，支持批量提交（最多 100 个任务），返回 taskId 用于查询进度和取消。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "tasks": {"type": "string", "description": "JSON 数组，每元素含 sourcePath/targetPath/duplicateHandleType", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_async_move", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_async_delete(
    tasks: Annotated[str, Field(
        description="异步删除任务列表（JSON 数组字符串）。"
        "每个元素包含 sourcePath（待删除文件/文件夹的绝对路径）。"
        "示例: [{\"sourcePath\": \"/home/temp/file.txt\"}]"
    )],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """异步删除文件/文件夹。支持批量提交（最多 100 个任务），返回 taskId 用于查询进度和取消。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    try:
        tasks_list = json.loads(tasks)
    except json.JSONDecodeError as exc:
        return {"error": True, "message": f"tasks 参数 JSON 解析失败: {exc}"}

    if not isinstance(tasks_list, list) or len(tasks_list) == 0:
        return {"error": True, "message": "tasks 必须是非空 JSON 数组。"}

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
            _efile_url(base_url, "/efile/openapi/v2/file/async-remove"),
            json=tasks_list,
            headers={"token": token, "Content-Type": "application/json"},
            timeout=60.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"异步删除失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"异步删除请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/async-remove",
        "method": "POST",
        "description": "异步删除文件/文件夹，支持批量提交（最多 100 个任务），返回 taskId 用于查询进度和取消。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "tasks": {"type": "string", "description": "JSON 数组，每元素含 sourcePath", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_async_delete", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_async_task_cancel(
    task_ids: Annotated[str, Field(
        description="要取消的异步任务 ID 列表（JSON 数组字符串）。"
        "示例: [\"254653d8816b4407ba27a9d004342c0e\", \"3dc8b0a...\"]"
    )],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """取消异步文件操作任务（复制/移动/删除）。已完成或已失败的任务无法取消。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    try:
        task_ids_list = json.loads(task_ids)
    except json.JSONDecodeError as exc:
        return {"error": True, "message": f"task_ids 参数 JSON 解析失败: {exc}"}

    if not isinstance(task_ids_list, list) or len(task_ids_list) == 0:
        return {"error": True, "message": "task_ids 必须是非空 JSON 数组。"}

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
            _efile_url(base_url, "/efile/openapi/v2/file/task/cancel"),
            json={"taskIds": task_ids_list},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"取消异步任务失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"取消异步任务请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/task/cancel",
        "method": "POST",
        "description": "取消异步文件操作任务（复制/移动/删除）。已完成或已失败的任务无法取消。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "task_ids": {"type": "string", "description": "JSON 数组，要取消的 taskId 列表", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_async_task_cancel", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_async_task_list(
    task_ids: Annotated[str, Field(
        description="要查询的异步任务 ID 列表（JSON 数组字符串）。"
        "示例: [\"10b10bf643814ebfb1fbf0d7e910e8d1\"]"
    )],
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """查询异步文件操作任务的进度和状态。任务结束后服务端缓存信息 24 小时。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    try:
        task_ids_list = json.loads(task_ids)
    except json.JSONDecodeError as exc:
        return {"error": True, "message": f"task_ids 参数 JSON 解析失败: {exc}"}

    if not isinstance(task_ids_list, list) or len(task_ids_list) == 0:
        return {"error": True, "message": "task_ids 必须是非空 JSON 数组。"}

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
            _efile_url(base_url, "/efile/openapi/v2/file/task/list"),
            json={"taskIds": task_ids_list},
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询异步任务失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询异步任务请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/task/list",
        "method": "POST",
        "description": "查询异步文件操作任务的进度和状态。任务结束后服务端缓存信息 24 小时。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "task_ids": {"type": "string", "description": "JSON 数组，要查询的 taskId 列表", "optional": False},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_async_task_list", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_chunk_upload(
    file_content: Annotated[str, Field(description="当前分片的文件内容（base64 编码字符串）")],
    file_name: Annotated[str, Field(description="原始文件名（如 result.tar.gz）")],
    chunk_number: Annotated[int, Field(description="当前分片序号（从 1 开始）。不分片时填 1")],
    total_chunks: Annotated[int, Field(description="分片总个数。不分片时填 1")],
    total_size: Annotated[int, Field(description="文件总大小，单位：字节")],
    path: Annotated[str, Field(description="远程目标文件夹路径（必须为绝对路径）")],
    relative_path: Annotated[str, Field(description="文件相对于 path 的相对路径（包含文件名）")],
    cover: Annotated[str, Field(description="覆盖策略：cover（强制覆盖）或 uncover（不覆盖），默认 uncover")] = "uncover",
    identifier: Annotated[str, Field(description="文件标识，用于关联同一文件的不同分片。无需分片时可为空")] = "",
    chunk_size: Annotated[int, Field(description="每片字节数，默认 5242880 (5MB)")] = 5242880,
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """分片上传文件到 HPC 集群文件系统。大文件（>5GB）建议分片上传，每片 5MB。

    文件内容通过 base64 编码字符串传入。分片上传完成后需调用 efile_merge_file 合并文件。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # Decode base64 file content (streaming for large chunks)
    try:
        file_bytes = _b64decode_stream(file_content)
    except (ValueError, Exception) as exc:
        msg = str(exc) if isinstance(exc, ValueError) and "文件过大" in str(exc) else f"文件内容 base64 解码失败: {exc}"
        return {"error": True, "message": msg}

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
        client = _get_http_client(timeout=120.0)
        data_fields = {
            "chunkNumber": str(chunk_number),
            "cover": cover,
            "filename": file_name,
            "path": path,
            "relativePath": relative_path,
            "totalChunks": str(total_chunks),
            "totalSize": str(total_size),
            "chunkSize": str(chunk_size),
            "currentChunkSize": str(len(file_bytes)),
        }
        if identifier:
            data_fields["identifier"] = identifier

        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/burst"),
            data=data_fields,
            files={"file": (file_name, file_bytes)},
            headers={"token": token},
            timeout=120.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"分片上传文件失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"分片上传请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/burst",
        "method": "POST",
        "description": (
            "分片上传文件到 HPC 集群文件系统。大文件（>5GB）建议分片上传，每片 5MB。"
            "分片上传完成后需调用 efile_merge_file 合并文件。"
        ),
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "file_content": {"type": "string", "description": "当前分片的文件内容（base64 编码）", "optional": False},
                "file_name": {"type": "string", "description": "原始文件名", "optional": False},
                "chunk_number": {"type": "integer", "description": "当前分片序号（从 1 开始）", "optional": False},
                "total_chunks": {"type": "integer", "description": "分片总个数", "optional": False},
                "total_size": {"type": "integer", "description": "文件总大小（字节）", "optional": False},
                "path": {"type": "string", "description": "远程目标文件夹路径（绝对路径）", "optional": False},
                "relative_path": {"type": "string", "description": "文件相对 path 的路径（含文件名）", "optional": False},
                "cover": {"type": "string", "description": "覆盖策略：cover/uncover，默认 uncover", "optional": True},
                "identifier": {"type": "string", "description": "文件标识，用于关联分片", "optional": True},
                "chunk_size": {"type": "integer", "description": "每片字节数，默认 5242880 (5MB)", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_chunk_upload", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_merge_file(
    path: Annotated[str, Field(description="文件存放的目标文件夹路径（必须为绝对路径）")],
    relative_path: Annotated[str, Field(description="文件相对 path 的路径（含文件名）。必须与分片上传时的 relativePath 一致")],
    cover: Annotated[str, Field(description="覆盖策略：cover（强制覆盖）或 uncover（不覆盖）。必须与分片上传时一致")] = "uncover",
    file_name: Annotated[str, Field(description="文件名。通常 relativePath 已包含文件名")] = "",
    identifier: Annotated[str, Field(description="文件标识。分片上传时若传入则需传入")] = "",
    file_id: Annotated[str, Field(description="文件 ID")] = "",
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """合并分片上传完成的文件。分片上传全部完成后必须调用此接口，否则文件不完整。

    返回合并后的文件路径。
    """
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
        data_fields = {
            "cover": cover,
            "path": path,
            "relativePath": relative_path,
        }
        if file_name:
            data_fields["filename"] = file_name
        if identifier:
            data_fields["identifier"] = identifier
        if file_id:
            data_fields["id"] = file_id

        resp = await client.post(
            _efile_url(base_url, "/efile/openapi/v2/file/merge"),
            data=data_fields,
            headers={"token": token},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"合并文件失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"合并文件请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/merge",
        "method": "POST",
        "description": "合并分片上传完成的文件。分片上传全部完成后必须调用此接口，否则文件不完整。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "path": {"type": "string", "description": "文件存放的目标文件夹路径（绝对路径）", "optional": False},
                "relative_path": {"type": "string", "description": "相对路径（含文件名），必须与分片上传时一致", "optional": False},
                "cover": {"type": "string", "description": "覆盖策略：cover/uncover，默认 uncover", "optional": True},
                "file_name": {"type": "string", "description": "文件名", "optional": True},
                "identifier": {"type": "string", "description": "文件标识", "optional": True},
                "file_id": {"type": "string", "description": "文件 ID", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID，为空时使用默认集群", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_merge_file", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def efile_get_upload_config(
    file_size_bytes: Annotated[int, Field(description="要上传的文件总大小，单位：字节")],
) -> dict:
    """根据文件大小返回推荐的上传策略和配置参数。

    100MB 以下：直接使用 efile_upload 单次上传。
    100MB-5GB：使用 efile_chunk_upload 分片上传，返回推荐的分片大小和总数。
    超过 5GB：返回拒绝提示。
    """
    if file_size_bytes > MAX_FILE_SIZE_BYTES:
        return {
            "allowed": False,
            "message": (
                f"文件大小 {file_size_bytes / (1024**3):.2f} GB 超过最大支持 {MAX_FILE_SIZE_BYTES / (1024**3):.0f} GB。"
                "请使用 SCP/SFTP 等直连方式传输超大文件。"
            ),
        }

    if file_size_bytes <= MAX_SINGLE_TRANSFER_BYTES:
        return {
            "allowed": True,
            "strategy": "single",
            "message": f"文件 {file_size_bytes / (1024**2):.1f} MB，可直接使用 efile_upload 单次上传。",
            "recommended_tool": "efile_upload",
        }

    chunk_size = CHUNK_UPLOAD_SIZE
    total_chunks = math.ceil(file_size_bytes / chunk_size)
    batch_size = min(MAX_CHUNKS_PER_BATCH, total_chunks)
    estimated_batch_calls = math.ceil(total_chunks / batch_size)
    encoded_chunk_size = int(chunk_size * 4 / 3)  # base64 encoded size per chunk

    return {
        "allowed": True,
        "strategy": "chunked",
        "message": (
            f"文件 {file_size_bytes / (1024**3):.2f} GB，建议分 {total_chunks} 片上传，"
            f"每片 {chunk_size / (1024**2):.0f} MB（base64 编码后约 {encoded_chunk_size / (1024**2):.0f} MB）。"
            f"推荐每次批量上传 {batch_size} 片，共约 {estimated_batch_calls} 次调用。"
        ),
        "recommended_tool": "efile_chunk_upload",
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "batch_size": batch_size,
        "estimated_batch_calls": estimated_batch_calls,
        "encoded_chunk_size": encoded_chunk_size,
    }


@mcp.tool()
async def efile_batch_chunk_upload(
    chunks_json: Annotated[str, Field(
        description="分片数据（JSON 数组字符串），最多 10 片。"
        "每片含: chunk_number(分片序号,从1开始), file_content(base64编码的分片内容)。"
        "示例: [{\"chunk_number\":1,\"file_content\":\"aGVsbG8=\"},{\"chunk_number\":2,\"file_content\":\"d29ybGQ=\"}]"
    )],
    file_name: Annotated[str, Field(description="原始文件名（如 result.tar.gz）")],
    total_chunks: Annotated[int, Field(description="分片总个数")],
    total_size: Annotated[int, Field(description="文件总大小，单位：字节")],
    path: Annotated[str, Field(description="远程目标文件夹路径（必须为绝对路径）")],
    relative_path: Annotated[str, Field(description="文件相对于 path 的相对路径（包含文件名）")],
    cover: Annotated[str, Field(description="覆盖策略：cover（强制覆盖）或 uncover（不覆盖），默认 uncover")] = "uncover",
    identifier: Annotated[str, Field(description="文件标识，用于关联同一文件的不同分片")] = "",
    clusterId: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """批量并行上传多个分片到 HPC 集群文件系统（最多 10 片/次）。

    内部并行发送各分片请求，大幅减少 Agent 侧的串行调用次数。
    使用前建议先调用 efile_get_upload_config 获取推荐配置。
    所有分片上传完成后需调用 efile_merge_file 合并文件。
    """
    if total_size > MAX_FILE_SIZE_BYTES:
        return {
            "error": True,
            "message": (
                f"文件大小 {total_size / (1024**3):.2f} GB 超过最大支持 "
                f"{MAX_FILE_SIZE_BYTES / (1024**3):.0f} GB。"
            ),
        }

    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # Parse chunks JSON
    try:
        chunk_list = json.loads(chunks_json)
    except json.JSONDecodeError as exc:
        return {"error": True, "message": f"chunks_json 解析失败: {exc}"}

    if not isinstance(chunk_list, list) or len(chunk_list) == 0:
        return {"error": True, "message": "chunks_json 必须是非空 JSON 数组。"}

    if len(chunk_list) > MAX_CHUNKS_PER_BATCH:
        return {
            "error": True,
            "message": f"单次批量上传最多 {MAX_CHUNKS_PER_BATCH} 片，当前 {len(chunk_list)} 片。",
        }

    # Resolve cluster auth once for all chunks
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

    async def _upload_one(chunk: dict) -> dict:
        """Upload a single chunk and return its result."""
        chunk_number = chunk.get("chunk_number", 0)
        chunk_b64 = chunk.get("file_content", "")
        try:
            chunk_bytes = base64.b64decode(chunk_b64)
        except Exception as exc:
            return {
                "chunk_number": chunk_number,
                "success": False,
                "error": f"base64 解码失败: {exc}",
            }

        try:
            client = _get_http_client(timeout=120.0)
            data_fields = {
                "chunkNumber": str(chunk_number),
                "cover": cover,
                "filename": file_name,
                "path": path,
                "relativePath": relative_path,
                "totalChunks": str(total_chunks),
                "totalSize": str(total_size),
                "chunkSize": str(CHUNK_UPLOAD_SIZE),
                "currentChunkSize": str(len(chunk_bytes)),
            }
            if identifier:
                data_fields["identifier"] = identifier

            resp = await client.post(
                _efile_url(base_url, "/efile/openapi/v2/file/burst"),
                data=data_fields,
                files={"file": (file_name, chunk_bytes)},
                headers={"token": token},
                timeout=120.0,
            )
            resp.raise_for_status()
            return {
                "chunk_number": chunk_number,
                "success": True,
                "response": resp.json(),
            }
        except httpx.HTTPStatusError as exc:
            return {
                "chunk_number": chunk_number,
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            return {
                "chunk_number": chunk_number,
                "success": False,
                "error": str(exc),
            }

    # Upload all chunks in parallel
    results = await asyncio.gather(*[_upload_one(c) for c in chunk_list])

    succeeded = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    summary = {
        "total_chunks_in_batch": len(chunk_list),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "results": results,
    }
    if failed:
        summary["warning"] = (
            f"{len(failed)} 片上传失败，请重试失败的片。"
            f"失败片号: {[f.get('chunk_number') for f in failed]}"
        )

    # Auto-register
    returns_schema = _build_return_schema(summary)
    doc = {
        "url": "{efileUrls}/efile/openapi/v2/file/burst",
        "method": "POST",
        "description": "批量并行上传分片到 HPC 集群（最多 10 片/次）。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "chunks_json": {"type": "string", "description": "分片 JSON 数组", "optional": False},
                "file_name": {"type": "string", "description": "原始文件名", "optional": False},
                "total_chunks": {"type": "integer", "description": "分片总个数", "optional": False},
                "total_size": {"type": "integer", "description": "文件总大小（字节）", "optional": False},
                "path": {"type": "string", "description": "远程目标文件夹路径", "optional": False},
                "relative_path": {"type": "string", "description": "文件相对路径", "optional": False},
                "cover": {"type": "string", "description": "覆盖策略", "optional": True},
                "identifier": {"type": "string", "description": "文件标识", "optional": True},
                "clusterId": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("efile_batch_chunk_upload", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return summary


# ---------------------------------------------------------------------------
# Container Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def container_create(
    instance_service_name: Annotated[str, Field(description="容器实例名称")],
    accelerator_type: Annotated[str, Field(description="加速器类型：mlu / dcu / gpu / cpu")],
    image_path: Annotated[str, Field(description="镜像路径。可从 container_get_images 的 path 字段获取")],
    version: Annotated[str, Field(description="镜像名称。可从 container_get_images 的 version 字段获取")],
    task_type: Annotated[str, Field(description="任务类型：ssh / jupyter / codeserver / rstudio")],
    resource_group: Annotated[str, Field(description="资源分组。可从 container_query_resource_group 获取")],
    cpu_number: Annotated[int, Field(description="CPU 数量")],
    ram_size: Annotated[int, Field(description="内存大小，单位 MB")],
    gpu_number: Annotated[int, Field(description="GPU 数量。若 accelerator_type 为 cpu 则填 0")],
    timeout_limit: Annotated[str, Field(description="自动停止时间，格式 HH:MM:SS 或 unlimited")],
    use_start_script: Annotated[bool, Field(description="是否启用启动脚本")] = False,
    task_number: Annotated[int, Field(description="实例任务数量")] = 1,
    description: Annotated[str, Field(description="描述信息")] = "",
    start_script_action_scope: Annotated[str, Field(description="启动脚本作用范围：all（所有容器）或 header（首个容器）")] = "all",
    start_script_content: Annotated[str, Field(description="启动脚本内容。多行命令每行末尾加 \\n")] = "",
    mount_info_list: Annotated[str, Field(description="挂载信息列表（JSON 数组字符串）。每项含 sourcePath/targetPath/type。示例: [{\"sourcePath\":\"/home/source\",\"targetPath\":\"/mnt/target\",\"type\":\"data\"}]")] = "[]",
    container_port_info_list: Annotated[str, Field(description="服务端口列表（JSON 数组字符串）。每项含 containerPort/protocolType。示例: [{\"containerPort\":18888,\"protocolType\":\"HTTP\"}]")] = "[]",
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """创建容器实例，支持 SSH/Jupyter/CodeServer/RStudio 等多种类型。

    返回任务 ID 用于跟踪创建进度。token 取自 user_cluster 表，aiUrls 取自 cluster_url 表。
    """
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    # Parse JSON list params
    try:
        mount_list = json.loads(mount_info_list) if mount_info_list else []
    except json.JSONDecodeError:
        return {"error": True, "message": "mount_info_list JSON 解析失败"}
    try:
        port_list = json.loads(container_port_info_list) if container_port_info_list else []
    except json.JSONDecodeError:
        return {"error": True, "message": "container_port_info_list JSON 解析失败"}

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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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

    body: dict[str, Any] = {
        "instanceServiceName": instance_service_name,
        "acceleratorType": accelerator_type,
        "imagePath": image_path,
        "version": version,
        "taskType": task_type,
        "resourceGroup": resource_group,
        "cpuNumber": cpu_number,
        "ramSize": ram_size,
        "gpuNumber": gpu_number,
        "timeoutLimit": timeout_limit,
        "useStartScript": use_start_script,
        "taskNumber": task_number,
        "startScriptActionScope": start_script_action_scope,
        "startScriptContent": start_script_content,
        "mountInfoList": mount_list,
        "containerPortInfoList": port_list,
    }
    if description:
        body["description"] = description

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            _ai_url(base_url, "/ai/openapi/v2/instance-service/task"),
            json=body,
            headers={"token": token, "Content-Type": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"创建容器实例失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"创建容器实例请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/task",
        "method": "POST",
        "description": "创建容器实例，支持 SSH/Jupyter/CodeServer/RStudio 等多种类型。返回任务 ID。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "instance_service_name": {"type": "string", "description": "容器实例名称", "optional": False},
                "accelerator_type": {"type": "string", "description": "加速器类型", "optional": False},
                "image_path": {"type": "string", "description": "镜像路径", "optional": False},
                "version": {"type": "string", "description": "镜像名称", "optional": False},
                "task_type": {"type": "string", "description": "任务类型", "optional": False},
                "resource_group": {"type": "string", "description": "资源分组", "optional": False},
                "cpu_number": {"type": "integer", "description": "CPU 数量", "optional": False},
                "ram_size": {"type": "integer", "description": "内存大小 MB", "optional": False},
                "gpu_number": {"type": "integer", "description": "GPU 数量", "optional": False},
                "timeout_limit": {"type": "string", "description": "自动停止时间", "optional": False},
                "use_start_script": {"type": "boolean", "description": "是否启用启动脚本", "optional": True},
                "task_number": {"type": "integer", "description": "实例任务数量", "optional": True},
                "description": {"type": "string", "description": "描述信息", "optional": True},
                "start_script_action_scope": {"type": "string", "description": "脚本作用范围", "optional": True},
                "start_script_content": {"type": "string", "description": "启动脚本内容", "optional": True},
                "mount_info_list": {"type": "string", "description": "挂载信息 JSON 数组", "optional": True},
                "container_port_info_list": {"type": "string", "description": "端口信息 JSON 数组", "optional": True},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_create", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_start(
    instance_service_id: Annotated[str, Field(description="容器实例 ID。可从 container_query_list 获取")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """重新启动停止、失败等状态的容器实例。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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
            f"{_ai_url(base_url, '/ai/openapi/v2/instance-service/task/actions/restart')}",
            params={"instanceServiceId": instance_service_id},
            headers={"token": token},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"启动容器实例失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"启动容器实例请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/task/actions/restart",
        "method": "POST",
        "description": "重新启动停止、失败等状态的容器实例。",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "instance_service_id": {"type": "string", "description": "容器实例 ID", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_start", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_stop(
    ids: Annotated[str, Field(description="待停止的容器实例 ID 列表，多个 ID 用英文逗号分隔")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """批量停止容器实例。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        return {"error": True, "message": "未提供有效的容器实例 ID。"}

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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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

    # Build URL with multiple ids params
    params_list = [("ids", i) for i in id_list]
    query_string = "&".join(f"{k}={v}" for k, v in params_list)

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.post(
            f"{_ai_url(base_url, '/ai/openapi/v2/instance-service/task/actions/stop')}?{query_string}",
            headers={"token": token},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"停止容器实例失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"停止容器实例请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/task/actions/stop",
        "method": "POST",
        "description": "批量停止容器实例。多个 ID 用逗号分隔。",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "ids": {"type": "string", "description": "容器实例 ID 列表（逗号分隔）", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_stop", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_delete(
    ids: Annotated[str, Field(description="待删除的容器实例 ID 列表，多个 ID 用英文逗号分隔")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """批量删除容器实例。"""
    username = get_current_username()

    auth_result = check_auth(username)
    if isinstance(auth_result, dict):
        return auth_result

    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        return {"error": True, "message": "未提供有效的容器实例 ID。"}

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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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

    params_list = [("ids", i) for i in id_list]
    query_string = "&".join(f"{k}={v}" for k, v in params_list)

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.delete(
            f"{_ai_url(base_url, '/ai/openapi/v2/instance-service/task')}?{query_string}",
            headers={"token": token},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"删除容器实例失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"删除容器实例请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/task",
        "method": "DELETE",
        "description": "批量删除容器实例。多个 ID 用逗号分隔。",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "ids": {"type": "string", "description": "容器实例 ID 列表（逗号分隔）", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_delete", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_execute(
    instance_id: Annotated[str, Field(description="容器实例 ID")],
    start_script_content: Annotated[str, Field(description="要执行的脚本内容。多行命令每行末尾加 \\n 换行转义符")],
    start_script_action_scope: Annotated[str, Field(description="执行范围：all（所有容器）或 header（首个容器）")] = "all",
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """对容器实例批量执行脚本。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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
            _ai_url(base_url, "/ai/openapi/v2/instance-service/task/actions/execute-script"),
            json={
                "id": instance_id,
                "startScriptContent": start_script_content,
                "startScriptActionScope": start_script_action_scope,
            },
            headers={"token": token, "Content-Type": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"执行脚本失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"执行脚本请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/task/actions/execute-script",
        "method": "POST",
        "description": "对容器实例批量执行脚本。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "instance_id": {"type": "string", "description": "容器实例 ID", "optional": False},
                "start_script_content": {"type": "string", "description": "脚本内容", "optional": False},
                "start_script_action_scope": {"type": "string", "description": "执行范围：all/header", "optional": True},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_execute", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_query_list(
    limit: Annotated[int, Field(description="每页返回条数")] = 20,
    sort: Annotated[str, Field(description="排序方式：asc / desc")] = "desc",
    start: Annotated[int, Field(description="起始位置，默认 0")] = 0,
    status: Annotated[str, Field(description="状态筛选：Running / Deploying / Waiting / Terminated / Failed / Completed")] = "",
    task_type: Annotated[str, Field(description="任务类型筛选：ssh / jupyter / codeserver / rstudio")] = "",
    instance_service_name: Annotated[str, Field(description="容器实例名称（模糊匹配）")] = "",
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """查询容器实例列表，支持按状态、类型、名称筛选和分页排序。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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

    body: dict[str, Any] = {
        "start": start,
        "limit": limit,
        "sort": sort,
    }
    if status:
        body["status"] = status
    if task_type:
        body["taskType"] = task_type
    if instance_service_name:
        body["instanceServiceName"] = instance_service_name

    try:
        client = _get_http_client(timeout=30.0)
        resp = await client.request(
            "GET",
            _ai_url(base_url, "/ai/openapi/v2/instance-service/task"),
            json=body,
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询容器实例列表失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询容器实例列表请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/task",
        "method": "GET",
        "description": "查询容器实例列表，支持按状态、类型、名称筛选和分页排序。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "limit": {"type": "integer", "description": "每页条数", "optional": True},
                "sort": {"type": "string", "description": "排序方式", "optional": True},
                "start": {"type": "integer", "description": "起始位置", "optional": True},
                "status": {"type": "string", "description": "状态筛选", "optional": True},
                "task_type": {"type": "string", "description": "任务类型筛选", "optional": True},
                "instance_service_name": {"type": "string", "description": "容器名称", "optional": True},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_query_list", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_query_url(
    instance_id: Annotated[str, Field(description="容器实例 ID")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """获取容器实例的访问 URL（如 JupyterLab 地址）。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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
            f"{_ai_url(base_url, '/ai/openapi/v2/instance-service')}/{instance_id}/url",
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"获取容器实例 URL 失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"获取容器实例 URL 请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/{id}/url",
        "method": "GET",
        "description": "获取容器实例的访问 URL。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "instance_id": {"type": "string", "description": "容器实例 ID", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_query_url", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_query_detail(
    instance_id: Annotated[str, Field(description="容器实例 ID")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """查询容器实例的详细信息，包含配置、状态、挂载、端口等完整数据。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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
            f"{_ai_url(base_url, '/ai/openapi/v2/instance-service')}/{instance_id}/detail",
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询容器实例详情失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询容器实例详情请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/{id}/detail",
        "method": "GET",
        "description": "查询容器实例详细信息，包含配置、状态、挂载、端口等完整数据。",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "instance_id": {"type": "string", "description": "容器实例 ID", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_query_detail", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_update_resource(
    instance_id: Annotated[str, Field(description="容器实例 ID")],
    cpu_number: Annotated[int, Field(description="CPU 数量")],
    gpu_number: Annotated[int, Field(description="GPU 数量。若 accelerator_type 为 cpu 则填 0")],
    ram_size: Annotated[int, Field(description="内存大小，单位 MB")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """更新容器实例的资源规格（CPU/GPU/内存）。仅非运行状态可修改。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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
            _ai_url(base_url, "/ai/openapi/v2/instance-service/resource-spec/actions/update"),
            json={
                "id": instance_id,
                "cpuNumber": cpu_number,
                "gpuNumber": gpu_number,
                "ramSize": ram_size,
            },
            headers={"token": token, "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"更新资源规格失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"更新资源规格请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/resource-spec/actions/update",
        "method": "POST",
        "description": "更新容器实例的资源规格（CPU/GPU/内存）。仅非运行状态可修改。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "instance_id": {"type": "string", "description": "容器实例 ID", "optional": False},
                "cpu_number": {"type": "integer", "description": "CPU 数量", "optional": False},
                "gpu_number": {"type": "integer", "description": "GPU 数量", "optional": False},
                "ram_size": {"type": "integer", "description": "内存大小 MB", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_update_resource", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_query_resources(
    accelerator_type: Annotated[str, Field(description="加速器类型：mlu / dcu / gpu / cpu")],
    resource_group: Annotated[str, Field(description="资源分组名称。可从 container_query_resource_group 获取")],
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """查询指定资源分组的节点资源限额（单节点 CPU 核数、GPU 数、内存等）。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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
            _ai_url(base_url, "/ai/openapi/v2/instance-service/resources"),
            params={"acceleratorType": accelerator_type, "resourceGroup": resource_group},
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询节点资源限额失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询节点资源限额请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/resources",
        "method": "GET",
        "description": "查询指定资源分组的节点资源限额（单节点 CPU 核数、GPU 数、内存等）。",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "accelerator_type": {"type": "string", "description": "加速器类型", "optional": False},
                "resource_group": {"type": "string", "description": "资源分组名称", "optional": False},
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_query_resources", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_query_resource_group(
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """获取当前用户可用的资源分组列表，按加速器类型（gpu/dcu/mlu/cpu）分组返回。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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
            _ai_url(base_url, "/ai/openapi/v2/instance-service/resource-group"),
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询资源分组失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询资源分组请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/resource-group",
        "method": "GET",
        "description": "获取当前用户可用的资源分组列表，按加速器类型分组返回。",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_query_resource_group", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_query_allowed_mount_dir(
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """获取当前用户被授权允许挂载的目录列表。"""
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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
            _ai_url(base_url, "/ai/openapi/v2/instance-service/allowed-mount-dir"),
            headers={"token": token},
            timeout=15.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": True,
            "message": (
                f"查询授权挂载路径失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"查询授权挂载路径请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/instance-service/allowed-mount-dir",
        "method": "GET",
        "description": "获取当前用户被授权允许挂载的目录列表。",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "cluster_id": {"type": "integer", "description": "集群 ID", "optional": True},
            },
        },
        "returns": {"format": "JSON", "schema": returns_schema},
    }
    conn2 = get_db()
    try:
        conn2.execute(
            "INSERT OR REPLACE INTO APIs(name, document) VALUES (?, ?)",
            ("container_query_allowed_mount_dir", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


@mcp.tool()
async def container_get_images(
    access: Annotated[str, Field(description="镜像权限：public（公开）或 private（私有）")],
    order_by: Annotated[str, Field(description="排序字段：create_time / share_time / clone_times")] = "create_time",
    sort: Annotated[str, Field(description="排序方式：DESC / ASC")] = "DESC",
    limit: Annotated[int, Field(description="每页数量")] = 20,
    start: Annotated[int, Field(description="起始条数")] = 0,
    name: Annotated[str, Field(description="镜像名称（模糊匹配）")] = "",
    image_type: Annotated[str, Field(description="镜像类型：JupyterLab / CodeServer / RStudio / Base 等")] = "",
    accelerator_type: Annotated[str, Field(description="加速器类型：mlu / dcu / gpu / cpu")] = "",
    cluster_id: Annotated[Optional[int], Field(description="集群 ID。为空时使用默认集群")] = None,
) -> dict:
    """获取可用的容器镜像列表，支持按名称、类型、加速器筛选和分页排序。

    返回的 path、version 可用于 container_create。
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
                return {
                    "error": True,
                    "message": f"集群 clusterId={cluster_id} 不属于用户 '{username}'。",
                }
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

    body: dict[str, Any] = {
        "access": access,
        "start": start,
        "limit": limit,
        "sort": sort,
        "orderBy": order_by,
    }
    if name:
        body["name"] = name
    if image_type:
        body["type"] = image_type
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
            "message": (
                f"获取镜像列表失败 (HTTP {exc.response.status_code})。"
                f"详情: {exc.response.text[:500]}"
            ),
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        return {"error": True, "message": f"获取镜像列表请求异常: {exc}"}

    returns_schema = _build_return_schema(result if isinstance(result, dict) else {})
    doc = {
        "url": "{aiUrls}/ai/openapi/v2/image/images",
        "method": "POST",
        "description": "获取可用的容器镜像列表，支持按名称、类型、加速器筛选和分页排序。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "access": {"type": "string", "description": "镜像权限 public/private", "optional": False},
                "order_by": {"type": "string", "description": "排序字段", "optional": True},
                "sort": {"type": "string", "description": "排序方式", "optional": True},
                "limit": {"type": "integer", "description": "每页数量", "optional": True},
                "start": {"type": "integer", "description": "起始条数", "optional": True},
                "name": {"type": "string", "description": "镜像名称", "optional": True},
                "image_type": {"type": "string", "description": "镜像类型", "optional": True},
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
            ("container_get_images", json.dumps(doc, ensure_ascii=False)),
        )
        conn2.commit()
    finally:
        conn2.close()

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    return result


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

    # Set the max request body size to allow large file uploads
    # h11 default is 16KB which would reject any body > 16KB
    max_request_size = int(
        os.environ.get("MCP_MAX_REQUEST_SIZE", 6 * 1024 * 1024 * 1024)
    )
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "8000")),
        path="/mcp/{username}",
        uvicorn_config={
            "h11_max_incomplete_event_size": max_request_size,
        },
    )


if __name__ == "__main__":
    main()
