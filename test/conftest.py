"""Shared fixtures for efile MCP tool tests.

Sets up a test database, mocks HTTP request context (so get_current_username
works), and patches httpx so tool functions hit mock servers.
"""

import json
import os
import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Module-level: create a throwaway DB *before* main.py is ever imported by pytest
# ---------------------------------------------------------------------------
_fd, _MODULE_DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["MCP_DB_PATH"] = _MODULE_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    userName   TEXT PRIMARY KEY,
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
CREATE TABLE IF NOT EXISTS APIs(
    name TEXT PRIMARY KEY,
    document TEXT
);
"""

# Ensure schema exists so main.py can import without errors
_conn = sqlite3.connect(_MODULE_DB_PATH)
_conn.row_factory = sqlite3.Row
_conn.executescript(SCHEMA)
_conn.commit()
_conn.close()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TEST_USER = "testuser"
TEST_CLUSTER_ID = 1
TEST_TOKEN = "test-cluster-token-abc123"
TEST_EFILE_URLS = "https://efile1.scnet.cn,https://efile2.scnet.cn"
TEST_HPC_URLS = "https://hpc1.scnet.cn,https://hpc2.scnet.cn"
TEST_AI_URLS = "https://ai1.scnet.cn,https://ai2.scnet.cn"


def _seed_test_data(db_path: str) -> None:
    """Insert a standard set of test user/cluster rows."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = "2026-05-27 10:00:00"

    # Clean existing data for our test user
    conn.execute("DELETE FROM user_cluster WHERE userName = ?", (TEST_USER,))
    conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
    conn.execute("DELETE FROM cluster_url WHERE clusterId IN (?, ?)", (TEST_CLUSTER_ID, 2))

    conn.execute(
        "INSERT OR REPLACE INTO users(userName, acToken, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (TEST_USER, "ac-token-xxx", now, now),
    )
    # Default cluster
    conn.execute(
        "INSERT OR REPLACE INTO user_cluster(userName, clusterId, clusterName, homePath, token, "
        "isDefault, JobManagerid, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (TEST_USER, TEST_CLUSTER_ID, "DefaultCluster", f"/home/{TEST_USER}",
         TEST_TOKEN, True, "12345", now, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cluster_url(clusterId, clusterName, hpcUrls, aiUrls, efileUrls) VALUES (?, ?, ?, ?, ?)",
        (TEST_CLUSTER_ID, "DefaultCluster", TEST_HPC_URLS, TEST_AI_URLS, TEST_EFILE_URLS),
    )
    # Second (non-default) cluster
    conn.execute(
        "INSERT OR REPLACE INTO user_cluster(userName, clusterId, clusterName, homePath, token, "
        "isDefault, JobManagerid, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (TEST_USER, 2, "SecondCluster", f"/home/{TEST_USER}",
         "token-cluster-2", False, "67890", now, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cluster_url(clusterId, clusterName, hpcUrls, aiUrls, efileUrls) VALUES (?, ?, ?, ?, ?)",
        (2, "SecondCluster", "https://hpc-second.scnet.cn", "https://ai-second.scnet.cn", "https://efile-second.scnet.cn"),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Mock HTTP request context
# ---------------------------------------------------------------------------

@pytest.fixture
def set_username():
    """Set the _current_http_request ContextVar so get_current_username() works."""
    from fastmcp.server.http import _current_http_request

    mock_request = MagicMock()
    mock_request.path_params = {"username": TEST_USER}

    token = _current_http_request.set(mock_request)
    yield
    _current_http_request.reset(token)


# ---------------------------------------------------------------------------
# Mock httpx AsyncClient
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client():
    """Patch _get_http_client to return an AsyncMock whose .post/.get return
    a default success MockResponse.  Tests override .return_value or
    .side_effect on the returned mock as needed."""
    client = AsyncMock()
    client.post = AsyncMock()
    client.get = AsyncMock()

    default = MockResponse(json_data={"code": "0", "msg": "success", "data": None})
    client.post.return_value = default
    client.get.return_value = default

    with patch("main._get_http_client", return_value=client):
        yield client


# ---------------------------------------------------------------------------
# Combined fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def env(set_username, mock_client):
    """Yield (main_module, mock_client) with test DB seeded and username set."""
    # Seed fresh test data in the DB that main is already connected to
    _seed_test_data(os.environ["MCP_DB_PATH"])

    # Clear URL round-robin index between tests for predictable results
    import main as _main
    _main._url_idx_ctx.clear()

    return _main, mock_client


# ---------------------------------------------------------------------------
# Mock response helpers (placed after mocks since they need httpx)
# ---------------------------------------------------------------------------

class MockResponse:
    """Simulates httpx.Response for success cases."""

    def __init__(self, json_data=None, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self._json = json_data
        self._content = content
        self._headers = headers or {}
        self.text = json.dumps(json_data) if json_data else ""

    def json(self):
        return self._json

    @property
    def content(self):
        return self._content

    @property
    def headers(self):
        return HeaderDict(self._headers)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://efile1.scnet.cn/test")
            response = httpx.Response(
                self.status_code,
                content=self._content or json.dumps(self._json).encode(),
                request=request,
            )
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response,
            )


class HeaderDict:
    """Minimal dict-like wrapper that supports .get()."""
    def __init__(self, d):
        self._d = d
    def get(self, key, default=None):
        return self._d.get(key, default)
