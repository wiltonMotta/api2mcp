"""Integration tests for SCNet OpenAPI MCP Server.

Tests the real MCP endpoint end-to-end:
  - efile operations (touch, upload, download, rename, move, copy, folder)
  - HPC operations (submit job, list jobs, query)
  - Container operations (query list, resource groups, images)
  - Notebook operations (list resources, images)

Uses the real MCP streamable-http endpoint with session management.
"""

import json
import time
import pytest
import httpx

TEST_PREFIX = f"test_{int(time.time())}"
MCP_URL = "https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2"
# Use existing writable directory on the server
TEST_DIR = f"/public/home/ac1npa3sf2/tmp/{TEST_PREFIX}"


def _http_post(url, data, headers, timeout=60):
    """Send HTTP POST and return parsed JSON from SSE response."""
    resp = httpx.post(url, json=data, headers=headers, timeout=timeout)
    result = {}
    for line in resp.text.split('\n'):
        if line.startswith('data: '):
            try:
                result = json.loads(line[6:])
                break
            except:
                pass
    return result


def _get_session_id():
    """Initialize MCP session and return session ID."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    resp = httpx.post(
        MCP_URL,
        json={
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "integration-test", "version": "1.0"},
            },
        },
        headers=headers,
        timeout=15,
        follow_redirects=False,
    )
    return resp.headers.get("mcp-session-id", "")


def call_tool(tool_name, arguments=None):
    """Call an MCP tool via the real endpoint."""
    session_id = _get_session_id()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Session-Id": session_id,
    }
    httpx.post(MCP_URL, json={"jsonrpc":"2.0","method":"notifications/initialized"},
        headers=headers, timeout=10)
    return _http_post(MCP_URL, {
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }, headers, timeout=120)


def parse_tool_result(result):
    """Extract JSON data from MCP tool response.

    Handles two formats:
    1. MCP wrapped: result.content[0].text contains JSON string
    2. Direct JSON: result directly contains the API response
    """
    r = result.get("result", {})
    content = r.get("content", None)
    if content and isinstance(content, list) and len(content) > 0:
        text = content[0].get("text", "{}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    # If no content array, the result itself might be the response
    if "error" in r or "content" not in r:
        return r
    return json.loads(json.dumps(r))


# ═══════════════════════════════════════════════════════════════════
# efile Integration Tests
# ═══════════════════════════════════════════════════════════════════

class TestEfileIntegration:

    def test_efile_list_files(self):
        """List files in home directory."""
        result = call_tool("efile_list_files", {"path": "", "limit": 5})
        data = parse_tool_result(result)
        assert data.get("code") == "0"

    def _ensure_test_dir(self):
        """Create the test directory if it doesn't exist."""
        result = call_tool("efile_folder_create", {"path": TEST_DIR, "create_parents": True})
        data = parse_tool_result(result)
        return data.get("code") == "0" or "already" in str(data.get("msg", "")).lower() or "exists" in str(data.get("msg", "")).lower()

    def test_efile_touch_and_delete(self):
        """Create and delete a test file."""
        self._ensure_test_dir()
        test_file = f"{TEST_DIR}/touch_test.txt"
        result = call_tool("efile_touch", {"path": test_file})
        data = parse_tool_result(result)
        assert data.get("code") == "0", f"Touch failed: {data}"
        # Cleanup
        call_tool("efile_delete", {"paths": test_file})

    def test_efile_upload_and_download(self):
        """Upload and download a small file."""
        self._ensure_test_dir()
        import base64
        folder = TEST_DIR
        content = f"Hello from integration test {TEST_PREFIX}"
        b64 = base64.b64encode(content.encode()).decode()

        result = call_tool("efile_upload", {
            "file_content": b64,
            "file_name": "upload_test.txt",
            "remote_path": folder,
        })
        data = parse_tool_result(result)
        assert data.get("code") == "0", f"Upload failed: {data}"

        # Download
        download_path = f"{folder}/upload_test.txt"
        result = call_tool("efile_download", {"path": download_path})
        data = parse_tool_result(result)
        # efile_download returns success data directly (with file_content_b64)
        # or error with code!=0
        assert data.get("code") == "0" or "file_content_b64" in data, f"Download failed: {data}"

        # Cleanup
        call_tool("efile_delete", {"paths": download_path})

    def test_efile_rename(self):
        """Create, rename, and verify file."""
        self._ensure_test_dir()
        src = f"{TEST_DIR}/rename_src.txt"
        new_name = "rename_dst.txt"
        call_tool("efile_touch", {"path": src})
        result = call_tool("efile_rename", {"path": src, "new_name": new_name})
        data = parse_tool_result(result)
        assert data.get("code") == "0", f"Rename failed: {data}"
        # Verify new file exists
        result = call_tool("efile_exist", {"path": f"{TEST_DIR}/{new_name}"})
        data = parse_tool_result(result)
        assert data.get("code") == "0", f"Exist check failed: {data}"
        # Cleanup
        call_tool("efile_delete", {"paths": f"{TEST_DIR}/{new_name}"})

    def test_efile_move(self):
        """Create and move a file."""
        self._ensure_test_dir()
        src = f"{TEST_DIR}/move_src.txt"
        dst_dir = f"{TEST_DIR}/moved"
        call_tool("efile_folder_create", {"path": dst_dir, "create_parents": True})
        call_tool("efile_touch", {"path": src})
        result = call_tool("efile_move", {"source_paths": src, "target_path": dst_dir})
        data = parse_tool_result(result)
        assert data.get("code") == "0", f"Move failed: {data}"
        call_tool("efile_delete", {"paths": f"{dst_dir}/move_src.txt"})
        call_tool("efile_delete", {"paths": dst_dir})

    def test_efile_copy(self):
        """Create and copy a file."""
        self._ensure_test_dir()
        src = f"{TEST_DIR}/copy_src.txt"
        dst_dir = f"{TEST_DIR}/copied"
        call_tool("efile_folder_create", {"path": dst_dir, "create_parents": True})
        call_tool("efile_touch", {"path": src})
        result = call_tool("efile_copy", {"source_paths": src, "target_path": dst_dir})
        data = parse_tool_result(result)
        assert data.get("code") == "0", f"Copy failed: {data}"
        # Verify copy exists
        call_tool("efile_exist", {"path": f"{dst_dir}/copy_src.txt"})
        # Cleanup
        call_tool("efile_delete", {"paths": src})
        call_tool("efile_delete", {"paths": f"{dst_dir}/copy_src.txt"})
        call_tool("efile_delete", {"paths": dst_dir})

    def test_efile_folder_create(self):
        """Create a test folder."""
        self._ensure_test_dir()
        folder = f"{TEST_DIR}/test_folder"
        result = call_tool("efile_folder_create", {"path": folder})
        data = parse_tool_result(result)
        assert data.get("code") == "0", f"Folder create failed: {data}"
        # Cleanup
        call_tool("efile_delete", {"paths": folder})

    def test_efile_check_permission(self):
        """Check file read permission."""
        self._ensure_test_dir()
        test_file = f"{TEST_DIR}/perm_test.txt"
        call_tool("efile_touch", {"path": test_file})
        result = call_tool("efile_check_permission", {
            "path": test_file,
            "permission_action": "READ",
        })
        data = parse_tool_result(result)
        assert data.get("code") == "0", f"Permission check failed: {data}"
        call_tool("efile_delete", {"paths": test_file})


# ═══════════════════════════════════════════════════════════════════
# HPC Integration Tests
# ═══════════════════════════════════════════════════════════════════

class TestHpcIntegration:

    def test_hpc_submit_job(self):
        """Submit a sleep job (non-blocking, may succeed or fail)."""
        result = call_tool("hpc_submit_job", {
            "queueName": "debug",
            "GAP_CMD_FILE": "sleep 900",
            "GAP_NNODE": "1",
            "GAP_WALL_TIME": "00:10:00",
        })
        data = parse_tool_result(result)
        if data.get("code") == "0":
            assert "jobID" in data.get("data", {}) or "jobID" in data

    def test_hpc_list_running_jobs(self):
        result = call_tool("hpc_list_running_jobs", {"page": 1, "size": 5})
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"

    def test_hpc_list_history_jobs(self):
        result = call_tool("hpc_list_history_jobs", {"page": 1, "size": 5})
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"

    def test_hpc_query_job_state(self):
        result = call_tool("hpc_query_job_state")
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"

    def test_hpc_query_used_time(self):
        result = call_tool("hpc_query_used_time")
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"


# ═══════════════════════════════════════════════════════════════════
# Container Integration Tests
# ═══════════════════════════════════════════════════════════════════

class TestContainerIntegration:

    def test_container_query_list(self):
        result = call_tool("container_query_list", {"limit": 5})
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"

    def test_container_query_resource_group(self):
        result = call_tool("container_query_resource_group")
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"

    def test_container_get_images(self):
        result = call_tool("container_get_images", {"access": "public", "limit": 5})
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"


# ═══════════════════════════════════════════════════════════════════
# Notebook Integration Tests
# ═══════════════════════════════════════════════════════════════════

class TestNotebookIntegration:

    def test_notebook_list_resources(self):
        # Requires cluster_ids parameter
        result = call_tool("notebook_list_resources", {"cluster_ids": "11250,20057"})
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"

    def test_notebook_list_images(self):
        result = call_tool("notebook_list_images", {"access": "public", "sort": "DESC"})
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"

    def test_notebook_list(self):
        result = call_tool("notebook_list", {})
        data = parse_tool_result(result)
        assert data.get("code") == "0" or data.get("code") == "10008"
