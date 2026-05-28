"""Tests for all 12 efile MCP tools."""

import base64
import json

import pytest
import httpx

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Constants (mirror those in conftest.py)
# ---------------------------------------------------------------------------
TEST_USER = "testuser"
TEST_CLUSTER_ID = 1

# ---------------------------------------------------------------------------
# Mock HTTP response helpers
# ---------------------------------------------------------------------------

class MockResponse:
    """Simulates httpx.Response."""

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
        # Return the dict directly wrapped in an object that supports .get()
        class HeaderDict:
            def __init__(self, d):
                self._d = d
            def get(self, key, default=None):
                return self._d.get(key, default)
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


def make_http_error(status_code: int = 500, text: str = '{"code":"10001","msg":"内部异常","data":null}'):
    """Create an httpx.HTTPStatusError for testing error paths."""
    request = httpx.Request("POST", "https://efile1.scnet.cn/efile/openapi/v2/file/test")
    response = httpx.Response(status_code, content=text.encode(), request=request)
    return httpx.HTTPStatusError(text, request=request, response=response)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _post_data(mock_client):
    """Return the dict of form-encoded data sent in the last POST call."""
    call = mock_client.post.call_args
    if not call:
        return {}
    kwargs = call.kwargs
    return kwargs.get("data", {}) or {}


def _post_url(mock_client):
    """Return the full URL from the last POST call."""
    return mock_client.post.call_args[0][0] if mock_client.post.call_args else ""


def _get_params(mock_client):
    """Return the query params from the last GET call."""
    call = mock_client.get.call_args
    if not call:
        return {}
    kwargs = call.kwargs
    return kwargs.get("params", {}) or {}


def _post_params(mock_client):
    """Return the query params from the last POST call."""
    call = mock_client.post.call_args
    if not call:
        return {}
    kwargs = call.kwargs
    return kwargs.get("params", {}) or {}


def _get_url(mock_client):
    """Return the full URL from the last GET call."""
    return mock_client.get.call_args[0][0] if mock_client.get.call_args else ""


# ═══════════════════════════════════════════════════════════════
# efile_list_files
# ═══════════════════════════════════════════════════════════════

class TestEfileListFiles:
    async def test_success_default_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"total": 1, "path": "/home/test", "fileList": []},
        })
        result = await main.efile_list_files(path="/home/test", keyword="test", limit=5)
        assert result["code"] == "0"
        params = _get_params(mock)
        assert params["path"] == "/home/test"
        assert params["keyWord"] == "test"
        assert params["limit"] == 5

    async def test_success_explicit_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "ok", "data": {"total": 0, "path": "/x", "fileList": []},
        })
        result = await main.efile_list_files(clusterId=2)
        assert result["code"] == "0"
        assert "efile-second.scnet.cn" in _get_url(mock)

    async def test_auth_failure(self, env):
        main, mock = env
        # Remove user from DB
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_list_files()
        assert result.get("error") is True
        assert "auth_url" in result
        mock.get.assert_not_called()

    async def test_no_efile_urls(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("UPDATE cluster_url SET efileUrls = '' WHERE clusterId = ?", (TEST_CLUSTER_ID,))
        conn.commit()
        conn.close()
        result = await main.efile_list_files()
        assert result.get("error") is True
        assert "efileUrls" in result.get("message", "")

    async def test_http_error(self, env):
        main, mock = env
        mock.get.side_effect = make_http_error(500, '{"code":"10001","msg":"server error"}')
        result = await main.efile_list_files()
        assert result.get("error") is True
        assert "500" in result.get("message", "")

    async def test_network_exception(self, env):
        main, mock = env
        mock.get.side_effect = Exception("Connection refused")
        result = await main.efile_list_files()
        assert result.get("error") is True
        assert "Connection refused" in result.get("message", "")

    async def test_field_mapping_snake_case(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "ok",
            "data": {
                "total": 1, "path": "/a",
                "fileList": [{
                    "name": "f.txt", "isDirectory": True, "isRegularFile": False,
                    "isSymbolicLink": False, "isShare": True, "isOther": False,
                    "shareEnabled": True, "creationTime": "2021-01-01", "lastModifiedTime": "2021-01-02",
                    "lastAccessTime": "2021-01-03", "fileKey": "abc", "permissionAction": {"read": True},
                    "id": "1", "path": "/a/f.txt", "size": 100, "owner": "u", "group": "g",
                    "permission": "rwx", "type": "f",
                }],
            },
        })
        result = await main.efile_list_files()
        files = result["data"]["files"]
        f = files[0]
        assert f["is_directory"] is True
        assert f["is_regular_file"] is False
        assert f["is_symbolic_link"] is False
        assert f["is_share"] is True
        assert f["is_other"] is False
        assert f["share_enabled"] is True
        assert f["creation_time"] == "2021-01-01"
        assert f["last_modified_time"] == "2021-01-02"
        assert f["last_access_time"] == "2021-01-03"
        assert f["file_key"] == "abc"
        assert f["permission_action"] == {"read": True}


# ═══════════════════════════════════════════════════════════════
# efile_touch
# ═══════════════════════════════════════════════════════════════

class TestEfileTouch:
    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": None})
        result = await main.efile_touch(path="/home/test/newfile.txt")
        assert result["code"] == "0"
        data = _post_data(mock)
        assert data["fileAbsolutePath"] == "/home/test/newfile.txt"
        assert "path" not in data  # MCP param must map to OpenAPI name

    async def test_success_explicit_cluster(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": None})
        result = await main.efile_touch(path="/x", clusterId=2)
        assert result["code"] == "0"
        assert "efile-second.scnet.cn" in _post_url(mock)

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_touch(path="/some/file.txt")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(500, '{"code":"10001","msg":"fail"}')
        result = await main.efile_touch(path="/x")
        assert result.get("error") is True

    async def test_network_exception(self, env):
        main, mock = env
        mock.post.side_effect = Exception("timeout")
        result = await main.efile_touch(path="/x")
        assert result.get("error") is True
        assert "timeout" in result.get("message", "")


# ═══════════════════════════════════════════════════════════════
# efile_check_permission
# ═══════════════════════════════════════════════════════════════

class TestEfileCheckPermission:
    async def test_success_read(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": {"allowed": True},
        })
        result = await main.efile_check_permission(path="/f", permission_action="READ")
        assert result["code"] == "0"
        assert result["data"]["allowed"] is True
        data = _post_data(mock)
        assert data["path"] == "/f"
        assert data["permissionAction"] == "READ"

    async def test_success_write(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": {"allowed": False},
        })
        result = await main.efile_check_permission(path="/f", permission_action="WRITE")
        assert result["data"]["allowed"] is False
        assert _post_data(mock)["permissionAction"] == "WRITE"

    async def test_success_execute(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": {"allowed": True},
        })
        result = await main.efile_check_permission(path="/f", permission_action="EXECUTE")
        assert _post_data(mock)["permissionAction"] == "EXECUTE"

    async def test_invalid_permission_action(self, env):
        main, mock = env
        result = await main.efile_check_permission(path="/f", permission_action="DELETE")
        assert result.get("error") is True
        assert "无效的权限类型" in result.get("message", "")
        assert "DELETE" in result.get("message", "")
        mock.post.assert_not_called()

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_check_permission(path="/f", permission_action="READ")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(500, '{"code":"911030","msg":"permission denied"}')
        result = await main.efile_check_permission(path="/f", permission_action="READ")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════
# efile_move
# ═══════════════════════════════════════════════════════════════

class TestEfileMove:
    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": None})
        result = await main.efile_move(
            source_paths="/a/f1.txt,/a/f2.txt",
            target_path="/b",
            cover="cover",
        )
        assert result["code"] == "0"
        data = _post_data(mock)
        assert data["sourcePaths"] == "/a/f1.txt,/a/f2.txt"
        assert data["targetPath"] == "/b"
        assert data["cover"] == "cover"

    async def test_default_cover(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": None})
        await main.efile_move(source_paths="/a", target_path="/b")
        assert _post_data(mock)["cover"] == "uncover"

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_move(source_paths="/a", target_path="/b")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(400, '{"code":"911020","msg":"file not found"}')
        result = await main.efile_move(source_paths="/x", target_path="/y")
        assert result.get("error") is True

    async def test_network_exception(self, env):
        main, mock = env
        mock.post.side_effect = Exception("Connection reset")
        result = await main.efile_move(source_paths="/a", target_path="/b")
        assert result.get("error") is True
        assert "Connection reset" in result.get("message", "")

    async def test_timeout_60s(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": None})
        await main.efile_move(source_paths="/a", target_path="/b")
        call_kwargs = mock.post.call_args.kwargs
        assert call_kwargs.get("timeout") == 60.0


# ═══════════════════════════════════════════════════════════════
# efile_copy
# ═══════════════════════════════════════════════════════════════

class TestEfileCopy:
    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": None})
        result = await main.efile_copy(
            source_paths="/a/f1.txt,/a/f2.txt",
            target_path="/backup",
            cover="uncover",
        )
        assert result["code"] == "0"
        data = _post_data(mock)
        assert data["sourcePaths"] == "/a/f1.txt,/a/f2.txt"
        assert data["targetPath"] == "/backup"
        assert data["cover"] == "uncover"

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_copy(source_paths="/a", target_path="/b")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_timeout_60s(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": None})
        await main.efile_copy(source_paths="/a", target_path="/b")
        assert mock.post.call_args.kwargs.get("timeout") == 60.0


# ═══════════════════════════════════════════════════════════════
# efile_rename
# ═══════════════════════════════════════════════════════════════

class TestEfileRename:
    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": None})
        result = await main.efile_rename(path="/home/test/old.txt", new_name="new.txt")
        assert result["code"] == "0"
        data = _post_data(mock)
        assert data["fileAbsolutePath"] == "/home/test/old.txt"
        assert data["newName"] == "new.txt"
        assert "path" not in data
        assert "new_name" not in data

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_rename(path="/a", new_name="b")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(400, '{"code":"911700","msg":"invalid filename"}')
        result = await main.efile_rename(path="/x", new_name="b")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════
# efile_delete
# ═══════════════════════════════════════════════════════════════

class TestEfileDelete:
    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": ""})
        result = await main.efile_delete(paths="/a/f1.txt,/a/f2.txt")
        assert result["code"] == "0"
        params = _post_params(mock)
        assert params["paths"] == "/a/f1.txt,/a/f2.txt"
        assert params["recursive"] == "false"

    async def test_recursive_true_serialization(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": ""})
        await main.efile_delete(paths="/a/dir", recursive=True)
        assert _post_params(mock)["recursive"] == "true"

    async def test_recursive_false_serialization(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": ""})
        await main.efile_delete(paths="/a/dir", recursive=False)
        assert _post_params(mock)["recursive"] == "false"

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_delete(paths="/a")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_timeout_30s(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": ""})
        await main.efile_delete(paths="/a")
        assert mock.post.call_args.kwargs.get("timeout") == 30.0

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(400, '{"code":"911502","msg":"dir not empty"}')
        result = await main.efile_delete(paths="/a/dir")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════
# efile_exist
# ═══════════════════════════════════════════════════════════════

class TestEfileExist:
    async def test_success_file_exists(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": {"exist": True},
        })
        result = await main.efile_exist(path="/home/test/file.txt")
        assert result["code"] == "0"
        assert result["data"]["exist"] is True

    async def test_success_file_not_exists(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": {"exist": False},
        })
        result = await main.efile_exist(path="/nonexistent")
        assert result["data"]["exist"] is False

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_exist(path="/a")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(500, '{"code":"10001","msg":"internal error"}')
        result = await main.efile_exist(path="/a")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════
# efile_folder_create
# ═══════════════════════════════════════════════════════════════

class TestEfileFolderCreate:
    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": ""})
        result = await main.efile_folder_create(path="/home/test/newdir")
        assert result["code"] == "0"
        params = _post_params(mock)
        assert params["path"] == "/home/test/newdir"
        assert params["createParents"] == "false"

    async def test_create_parents_true(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": ""})
        await main.efile_folder_create(path="/a/b/c", create_parents=True)
        assert _post_params(mock)["createParents"] == "true"

    async def test_create_parents_false(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": ""})
        await main.efile_folder_create(path="/a/b/c", create_parents=False)
        assert _post_params(mock)["createParents"] == "false"

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_folder_create(path="/a")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(400, '{"code":"911020","msg":"parent not exist"}')
        result = await main.efile_folder_create(path="/a/b")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════
# efile_preview_file
# ═══════════════════════════════════════════════════════════════

class TestEfilePreviewFile:
    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"content": "hello", "path": "/f", "start_index": 0, "end_index": 5, "has_next": False},
        })
        result = await main.efile_preview_file(path="/home/test/notes.txt")
        assert result["code"] == "0"
        data = _post_data(mock)
        assert data["path"] == "/home/test/notes.txt"
        assert data["force"] == "default"
        assert data["startIndex"] == "0"

    async def test_force_true_serialization(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "ok",
            "data": {"content": "hello", "path": "/f", "start_index": 0, "end_index": 5, "has_next": False},
        })
        await main.efile_preview_file(path="/f", force=True)
        assert _post_data(mock)["force"] == "force"

    async def test_force_false_serialization(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "ok",
            "data": {"content": "hello", "path": "/f", "start_index": 0, "end_index": 5, "has_next": False},
        })
        await main.efile_preview_file(path="/f", force=False)
        assert _post_data(mock)["force"] == "default"

    async def test_start_index(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "ok",
            "data": {"content": "world", "path": "/f", "start_index": 100, "end_index": 200, "has_next": True},
        })
        await main.efile_preview_file(path="/f", start_index=100)
        assert _post_data(mock)["startIndex"] == "100"

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_preview_file(path="/f")
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(400, '{"code":"911505","msg":"file too large"}')
        result = await main.efile_preview_file(path="/big")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════
# efile_upload
# ═══════════════════════════════════════════════════════════════

class TestEfileUpload:
    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": None})
        file_bytes = b"hello world"
        b64_content = base64.b64encode(file_bytes).decode()
        result = await main.efile_upload(
            file_content=b64_content,
            file_name="hello.txt",
            remote_path="/home/test",
        )
        assert result["code"] == "0"

        # Check POST args
        call_data = mock.post.call_args.kwargs.get("data")
        call_files = mock.post.call_args.kwargs.get("files")
        assert call_data["path"] == "/home/test"
        assert call_data["cover"] == "uncover"
        assert call_files["file"][0] == "hello.txt"
        assert call_files["file"][1] == file_bytes

    async def test_cover_override(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": None})
        await main.efile_upload(
            file_content=base64.b64encode(b"x").decode(),
            file_name="f.txt",
            remote_path="/dst",
            cover="cover",
        )
        assert mock.post.call_args.kwargs["data"]["cover"] == "cover"

    async def test_base64_decode_failure(self, env):
        main, mock = env
        result = await main.efile_upload(
            file_content="!!!not-valid-base64!!!",
            file_name="f.txt",
            remote_path="/dst",
        )
        assert result.get("error") is True
        assert "base64" in result.get("message", "")
        mock.post.assert_not_called()

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_upload(
            file_content=base64.b64encode(b"x").decode(),
            file_name="f.txt",
            remote_path="/dst",
        )
        assert result.get("error") is True
        mock.post.assert_not_called()

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(400, '{"code":"911501","msg":"disk full"}')
        result = await main.efile_upload(
            file_content=base64.b64encode(b"x").decode(),
            file_name="f.txt",
            remote_path="/dst",
        )
        assert result.get("error") is True

    async def test_timeout_60s(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": None})
        await main.efile_upload(
            file_content=base64.b64encode(b"x").decode(),
            file_name="f.txt",
            remote_path="/dst",
        )
        assert mock.post.call_args.kwargs.get("timeout") == 60.0

    async def test_large_file_content(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": None})
        large_data = base64.b64encode(b"x" * 1024 * 1024).decode()  # 1MB base64
        result = await main.efile_upload(
            file_content=large_data,
            file_name="large.bin",
            remote_path="/dst",
        )
        assert result["code"] == "0"
        # Verify the decoded bytes are correct
        call_files = mock.post.call_args.kwargs.get("files")
        assert len(call_files["file"][1]) == 1024 * 1024


# ═══════════════════════════════════════════════════════════════
# efile_download
# ═══════════════════════════════════════════════════════════════

class TestEfileDownload:
    async def test_success_file_download(self, env):
        main, mock = env
        file_bytes = b"binary content here"
        mock.get.return_value = MockResponse(
            content=file_bytes,
            headers={"content-type": "application/octet-stream",
                     "content-disposition": 'attachment; filename="report.pdf"'},
        )
        result = await main.efile_download(path="/home/test/report.pdf")
        assert result["file_name"] == "report.pdf"
        assert result["file_content"] == base64.b64encode(file_bytes).decode()
        assert result["file_size"] == len(file_bytes)
        assert result["content_type"] == "application/octet-stream"

    async def test_success_folder_download(self, env):
        main, mock = env
        zip_bytes = b"PK\x03\x04..."  # fake zip header
        mock.get.return_value = MockResponse(
            content=zip_bytes,
            headers={"content-type": "application/zip"},
        )
        result = await main.efile_download(path="/home/test/mydir")
        assert result["content_type"] == "application/zip"
        assert result["file_content"] == base64.b64encode(zip_bytes).decode()
        # File name derived from path when no Content-Disposition
        assert result["file_name"] == "mydir"

    async def test_filename_from_path_fallback(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(
            content=b"data",
            headers={"content-type": "application/octet-stream"},
        )
        result = await main.efile_download(path="/a/b/c/data.txt")
        assert result["file_name"] == "data.txt"

    async def test_json_error_response(self, env):
        """When API returns a JSON error instead of a file stream."""
        main, mock = env
        mock.get.return_value = MockResponse(
            json_data={"code": "911020", "msg": "file not found", "data": None},
            headers={"content-type": "application/json"},
        )
        result = await main.efile_download(path="/nonexistent")
        assert result["code"] == "911020"
        assert result["msg"] == "file not found"

    async def test_http_error_with_json_body(self, env):
        main, mock = env
        request = httpx.Request("GET", "https://efile1.scnet.cn/dl")
        response = httpx.Response(
            404,
            content=b'{"code":"911020","msg":"not found","data":null}',
            headers={"content-type": "application/json"},
            request=request,
        )
        mock.get.side_effect = httpx.HTTPStatusError(
            "Not Found", request=request, response=response,
        )
        result = await main.efile_download(path="/x")
        # Should parse the JSON error body
        assert result["code"] == "911020"

    async def test_http_error_non_json(self, env):
        main, mock = env
        request = httpx.Request("GET", "https://efile1.scnet.cn/dl")
        response = httpx.Response(500, content=b"Internal Server Error", request=request)
        mock.get.side_effect = httpx.HTTPStatusError(
            "Error", request=request, response=response,
        )
        result = await main.efile_download(path="/x")
        assert result.get("error") is True
        assert "500" in result.get("message", "")

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.efile_download(path="/f")
        assert result.get("error") is True
        mock.get.assert_not_called()

    async def test_timeout_120s(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(
            content=b"x",
            headers={"content-type": "application/octet-stream"},
        )
        await main.efile_download(path="/f")
        assert mock.get.call_args.kwargs.get("timeout") == 120.0

    async def test_no_efile_urls(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("UPDATE cluster_url SET efileUrls = '' WHERE clusterId = ?", (TEST_CLUSTER_ID,))
        conn.commit()
        conn.close()
        result = await main.efile_download(path="/f")
        assert result.get("error") is True

    async def test_explicit_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(
            content=b"data",
            headers={"content-type": "application/octet-stream"},
        )
        result = await main.efile_download(path="/f", clusterId=2)
        assert result["file_content"] is not None
        assert "efile-second.scnet.cn" in _get_url(mock)


# ═══════════════════════════════════════════════════════════════
# Round-robin URL selection
# ═══════════════════════════════════════════════════════════════

class TestRoundRobin:
    """Verify round-robin URL selection across multiple comma-separated URLs."""

    async def test_second_call_uses_second_url(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": None})

        # First call
        await main.efile_touch(path="/a")
        url1 = _post_url(mock)
        assert "efile1.scnet.cn" in url1

        # Second call with same clusterId should rotate
        await main.efile_touch(path="/b")
        url2 = _post_url(mock)
        assert "efile2.scnet.cn" in url2

    async def test_third_call_wraps_to_first_url(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": None})

        await main.efile_touch(path="/a")  # url1
        await main.efile_touch(path="/b")  # url2
        await main.efile_touch(path="/c")  # url1 again
        url3 = _post_url(mock)
        assert "efile1.scnet.cn" in url3


# ═══════════════════════════════════════════════════════════════
# set_default_cluster
# ═══════════════════════════════════════════════════════════════

class TestSetDefaultCluster:
    async def test_set_by_cluster_id(self, env):
        """Exact clusterId match — switches default cluster."""
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.row_factory = sqlite3.Row
        # Verify isDefault starts true for cluster 1
        row = conn.execute(
            "SELECT isDefault FROM user_cluster WHERE userName = ? AND clusterId = 1",
            (TEST_USER,),
        ).fetchone()
        assert row["isDefault"] == 1
        # Switch to cluster 2
        result = await main.set_default_cluster(clusterId=2)
        assert result["success"] is True
        assert result["defaultClusterId"] == 2
        assert result["defaultClusterName"] == "SecondCluster"
        # Verify DB state
        row1 = conn.execute(
            "SELECT isDefault FROM user_cluster WHERE userName = ? AND clusterId = 1",
            (TEST_USER,),
        ).fetchone()
        row2 = conn.execute(
            "SELECT isDefault FROM user_cluster WHERE userName = ? AND clusterId = 2",
            (TEST_USER,),
        ).fetchone()
        conn.close()
        assert row1["isDefault"] == 0
        assert row2["isDefault"] == 1

    async def test_set_by_cluster_name_exact(self, env):
        """clusterName fuzzy match returns exactly 1 result → auto-switch."""
        main, mock = env
        result = await main.set_default_cluster(clusterName="Second")
        assert result["success"] is True
        assert result["defaultClusterId"] == 2
        assert result["defaultClusterName"] == "SecondCluster"

    async def test_set_by_cluster_name_fuzzy_multiple(self, env):
        """clusterName fuzzy match returns multiple results → candidate list."""
        main, mock = env
        import sqlite3
        # Seed extra clusters with similar names
        conn = sqlite3.connect(main.DB_PATH)
        now = "2026-05-27 10:00:00"
        conn.execute(
            "INSERT OR REPLACE INTO user_cluster(userName, clusterId, clusterName, token, isDefault, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (TEST_USER, 3, "ProdCluster", "tok3", False, now, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO user_cluster(userName, clusterId, clusterName, token, isDefault, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (TEST_USER, 4, "ProdCluster-Backup", "tok4", False, now, now),
        )
        conn.commit()
        conn.close()

        result = await main.set_default_cluster(clusterName="Prod")
        assert result["success"] is False
        assert "2 个匹配" in result["message"]
        candidates = result["candidates"]
        assert len(candidates) == 2
        names = {c["clusterName"] for c in candidates}
        assert names == {"ProdCluster", "ProdCluster-Backup"}

    async def test_set_by_cluster_name_no_match(self, env):
        """clusterName fuzzy match returns 0 results → error."""
        main, mock = env
        result = await main.set_default_cluster(clusterName="NonExistent")
        assert result.get("error") is True
        assert "未找到名称包含" in result["message"]

    async def test_neither_provided(self, env):
        main, mock = env
        result = await main.set_default_cluster()
        assert result.get("error") is True
        assert "clusterId" in result["message"] or "clusterName" in result["message"]

    async def test_cluster_id_priority(self, env):
        """When both clusterId and clusterName are given, clusterId takes priority."""
        main, mock = env
        result = await main.set_default_cluster(clusterId=1, clusterName="Second")
        assert result["success"] is True
        assert result["defaultClusterId"] == 1
        # clusterId takes priority — should NOT match SecondCluster
        assert result["defaultClusterName"] == "DefaultCluster"

    async def test_cluster_id_not_found(self, env):
        main, mock = env
        result = await main.set_default_cluster(clusterId=999)
        assert result.get("error") is True
        assert "999" in result["message"]

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit()
        conn.close()
        result = await main.set_default_cluster(clusterId=1)
        assert result.get("error") is True
        assert "auth_url" in result
