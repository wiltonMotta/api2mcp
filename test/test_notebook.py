"""Tests for all 13 AI / Notebook MCP tools."""

import json

import pytest
import httpx

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TEST_USER = "testuser"
TEST_CLUSTER_ID = 1

# ---------------------------------------------------------------------------
# Helpers
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
        class HeaderDict:
            def __init__(self, d):
                self._d = d
            def get(self, key, default=None):
                return self._d.get(key, default)
        return HeaderDict(self._headers)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://ai1.scnet.cn/test")
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
    request = httpx.Request("POST", "https://ai1.scnet.cn/ai/openapi/v2/notebook/test")
    response = httpx.Response(status_code, content=text.encode(), request=request)
    return httpx.HTTPStatusError(text, request=request, response=response)


def _get_params(mock_client):
    return mock_client.get.call_args.kwargs.get("params", {}) if mock_client.get.call_args else {}

def _get_url(mock_client):
    return mock_client.get.call_args[0][0] if mock_client.get.call_args else ""

def _post_body(mock_client):
    call = mock_client.post.call_args
    return (call.kwargs.get("json", {}) or {}) if call else {}

def _post_url(mock_client):
    return mock_client.post.call_args[0][0] if mock_client.post.call_args else ""

def _post_headers(mock_client):
    return mock_client.post.call_args.kwargs.get("headers", {}) if mock_client.post.call_args else {}

def _get_headers(mock_client):
    return mock_client.get.call_args.kwargs.get("headers", {}) if mock_client.get.call_args else {}


# ===================================================================
# AC URL Tools (acToken, fixed URL)
# ===================================================================

class TestNotebookListResources:
    """notebook_list_resources — GET https://www.scnet.cn/ac/openapi/v2/resources/accelerators"""

    async def test_success_with_cluster_ids(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": [{"clusterId": 11250, "resourceType": "GPU"}],
        })
        result = await main.notebook_list_resources(cluster_ids="11250")
        assert result["code"] == "0"
        assert _get_params(mock)["clusterIds"] == "11250"
        assert "scnet.cn/ac/openapi/v2/resources/accelerators" in _get_url(mock)

    async def test_with_optional_resource_id(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": []})
        result = await main.notebook_list_resources(cluster_ids="11250", resource_id="res-123")
        assert result["code"] == "0"
        assert _get_params(mock)["resourceId"] == "res-123"

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit(); conn.close()
        result = await main.notebook_list_resources(cluster_ids="11250")
        assert result.get("error") is True
        assert "auth_url" in result

    async def test_missing_cluster_ids(self, env):
        main, mock = env
        result = await main.notebook_list_resources(cluster_ids="")
        assert result.get("error") is True

    async def test_http_error(self, env):
        main, mock = env
        mock.get.side_effect = make_http_error(500)
        result = await main.notebook_list_resources(cluster_ids="11250")
        assert result.get("error") is True
        assert "500" in result.get("message", "")


class TestNotebookCreate:
    """notebook_create — POST https://www.scnet.cn/ac/openapi/v2/notebook/actions/create"""

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"taskId": "12345", "notebookId": "nb-001"},
        })
        result = await main.notebook_create(
            cluster_id="11250", image_path="/img/path", image_name="img:v1",
            image_size="1000", accelerator_type="GPU", accelerator_number="1",
        )
        assert result["code"] == "0"
        body = _post_body(mock)
        assert body["clusterId"] == "11250"
        assert body["imagePath"] == "/img/path"
        assert body["imageName"] == "img:v1"
        assert body["acceleratorType"] == "GPU"
        assert "notebook/actions/create" in _post_url(mock)

    async def test_with_mount_info(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"taskId": "222", "notebookId": "nb-002"},
        })
        result = await main.notebook_create(
            cluster_id="11250", image_path="/a", image_name="x:y",
            image_size="500", accelerator_type="DCU", accelerator_number="2",
            mount_info=[{"source_path": "/src", "target_path": "/dst", "permission": "rw"}],
        )
        assert result["code"] == "0"
        body = _post_body(mock)
        assert body["mountInfo"][0]["sourcePath"] == "/src"
        assert body["mountInfo"][0]["targetPath"] == "/dst"
        assert body["mountInfo"][0]["permission"] == "rw"

    async def test_optional_params(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": {"taskId": "t", "notebookId": "n"},
        })
        await main.notebook_create(
            cluster_id="11250", image_path="/a", image_name="x:y",
            image_size="500", accelerator_type="DCU", accelerator_number="2",
            resource_group_code="rg-1", mount_home=False, start_command="echo hi",
        )
        body = _post_body(mock)
        assert body["resourceGroupCode"] == "rg-1"
        assert body["mountHome"] is False
        assert body["startCommand"] == "echo hi"

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit(); conn.close()
        result = await main.notebook_create(
            cluster_id="11250", image_path="/a", image_name="x:y",
            image_size="500", accelerator_type="DCU", accelerator_number="2",
        )
        assert result.get("error") is True

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(400, '{"code":"10003","msg":"参数不全"}')
        result = await main.notebook_create(
            cluster_id="11250", image_path="/a", image_name="x:y",
            image_size="500", accelerator_type="DCU", accelerator_number="2",
        )
        assert result.get("error") is True


class TestNotebookStart:
    """notebook_start — POST https://www.scnet.cn/ac/openapi/v2/notebook/actions/start"""

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": True,
        })
        result = await main.notebook_start(notebook_id="nb-001")
        assert result["code"] == "0"
        body = _post_body(mock)
        assert body["notebookId"] == "nb-001"
        assert "notebook/actions/start" in _post_url(mock)

    async def test_missing_notebook_id(self, env):
        main, mock = env
        result = await main.notebook_start(notebook_id="")
        assert result.get("error") is True

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit(); conn.close()
        result = await main.notebook_start(notebook_id="nb-001")
        assert result.get("error") is True

    async def test_http_error(self, env):
        main, mock = env
        mock.post.side_effect = make_http_error(500)
        result = await main.notebook_start(notebook_id="nb-001")
        assert result.get("error") is True


# ===================================================================
# Cluster aiUrls Tools
# ===================================================================

class TestNotebookList:
    """notebook_list — GET {aiUrls}/ai/openapi/v2/notebook/list"""

    async def test_success_default_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": {"total": 1, "records": []},
        })
        result = await main.notebook_list(notebook_status="Running", page=1, size=10)
        assert result["code"] == "0"
        params = _get_params(mock)
        assert params["notebookStatus"] == "Running"
        assert params["page"] == 1
        assert params["size"] == 10
        assert "/ai/openapi/v2/notebook/list" in _get_url(mock)

    async def test_explicit_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "ok", "data": {"total": 0, "records": []},
        })
        result = await main.notebook_list(cluster_id=2)
        assert result["code"] == "0"
        assert "ai-second.scnet.cn" in _get_url(mock)

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
        conn.commit(); conn.close()
        result = await main.notebook_list()
        assert result.get("error") is True

    async def test_no_ai_urls(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("UPDATE cluster_url SET aiUrls = '' WHERE clusterId = ?", (TEST_CLUSTER_ID,))
        conn.commit(); conn.close()
        result = await main.notebook_list()
        assert result.get("error") is True
        assert "aiUrls" in result.get("message", "")

    async def test_http_error(self, env):
        main, mock = env
        mock.get.side_effect = make_http_error(500)
        result = await main.notebook_list()
        assert result.get("error") is True


class TestNotebookDetail:
    """notebook_detail — GET {aiUrls}/ai/openapi/v2/notebook/detail"""

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"id": "nb-001", "notebookStatus": "Running"},
        })
        result = await main.notebook_detail(notebook_id="nb-001")
        assert result["code"] == "0"
        assert _get_params(mock)["notebookId"] == "nb-001"

    async def test_missing_notebook_id(self, env):
        main, mock = env
        result = await main.notebook_detail(notebook_id="")
        assert result.get("error") is True

    async def test_explicit_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "ok", "data": {"id": "x", "notebookStatus": "Terminated"},
        })
        result = await main.notebook_detail(notebook_id="x", cluster_id=2)
        assert result["code"] == "0"
        assert "ai-second.scnet.cn" in _get_url(mock)


class TestNotebookStop:
    """notebook_stop — POST {aiUrls}/ai/openapi/v2/notebook/actions/stop"""

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": True,
        })
        result = await main.notebook_stop(notebook_id="nb-001", save_env=True)
        assert result["code"] == "0"
        body = _post_body(mock)
        assert body["notebookId"] == "nb-001"
        assert body["saveEnv"] is True

    async def test_default_save_env_false(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "success", "data": True})
        await main.notebook_stop(notebook_id="nb-001")
        assert _post_body(mock)["saveEnv"] is False


class TestNotebookRelease:
    """notebook_release — POST {aiUrls}/ai/openapi/v2/notebook/actions/release"""

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": True,
        })
        result = await main.notebook_release(notebook_id="nb-001")
        assert result["code"] == "0"
        body = _post_body(mock)
        # release uses "id" not "notebookId"
        assert body["id"] == "nb-001"
        assert "notebookId" not in body


class TestNotebookRename:
    """notebook_rename — POST {aiUrls}/ai/openapi/v2/notebook/name"""

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success", "data": True,
        })
        result = await main.notebook_rename(notebook_id="nb-001", notebook_name="新名称")
        assert result["code"] == "0"
        body = _post_body(mock)
        assert body["id"] == "nb-001"
        assert body["notebookName"] == "新名称"

    async def test_missing_name(self, env):
        main, mock = env
        result = await main.notebook_rename(notebook_id="nb-001", notebook_name="")
        assert result.get("error") is True


class TestNotebookListImages:
    """notebook_list_images — POST {aiUrls}/ai/openapi/v2/image/images"""

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"total": 1, "data": [{"path": "/img", "imageSize": "100"}]},
        })
        result = await main.notebook_list_images(access="public", order_by="create_time", sort="DESC")
        assert result["code"] == "0"
        body = _post_body(mock)
        assert body["orderBy"] == "create_time"
        assert body["access"] == "public"

    async def test_with_filters(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": {"total": 0, "data": []}})
        await main.notebook_list_images(name="pytorch", type="JupyterLab", accelerator_type="dcu")
        body = _post_body(mock)
        assert body["name"] == "pytorch"
        assert body["type"] == "JupyterLab"
        assert body["acceleratorType"] == "dcu"


class TestNotebookListModelImages:
    """notebook_list_model_images — POST {aiUrls}/ai/openapi/v2/image/models"""

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"total": 1, "records": [{"path": "/model"}]},
        })
        result = await main.notebook_list_model_images(page=1, size=20)
        assert result["code"] == "0"
        body = _post_body(mock)
        assert body["page"] == 1
        assert body["size"] == 20

    async def test_with_accelerator(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok", "data": {"total": 0, "records": []}})
        await main.notebook_list_model_images(accelerator_type="gpu")
        body = _post_body(mock)
        assert body["acceleratorType"] == "gpu"


class TestNotebookQueryJupyterUrl:
    """notebook_query_jupyter_url — GET {aiUrls}/ai/openapi/v2/notebook/url"""

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"status": "active", "url": "https://n-xyz.ksai.scnet.cn/lab?token=abc"},
        })
        result = await main.notebook_query_jupyter_url(notebook_id="nb-001")
        assert result["code"] == "0"
        assert result["data"]["status"] == "active"
        assert _get_params(mock)["notebookId"] == "nb-001"

    async def test_inactive_status(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"status": "inactive", "url": ""},
        })
        result = await main.notebook_query_jupyter_url(notebook_id="nb-001")
        assert result["code"] == "0"
        assert result["data"]["status"] == "inactive"


class TestNotebookQueryCustomServiceUrl:
    """notebook_query_custom_service_url — GET {aiUrls}/ai/openapi/v2/notebook/customize-service/url"""

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"status": "active", "url": "https://c-xyz.ksai.scnet.cn:58043"},
        })
        result = await main.notebook_query_custom_service_url(notebook_id="nb-001")
        assert result["code"] == "0"
        assert "customize-service/url" in _get_url(mock)


class TestNotebookStartCustomService:
    """notebook_start_custom_service — POST {aiUrls}/ai/openapi/v2/notebook/customize-service/actions/start"""

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"execSuccess": True, "errorMsg": None, "output": "started\n"},
        })
        result = await main.notebook_start_custom_service(
            notebook_id="nb-001", customize_port="1223", command="python app.py",
        )
        assert result["code"] == "0"
        body = _post_body(mock)
        assert body["id"] == "nb-001"  # uses "id" not "notebookId"
        assert body["customizePort"] == "1223"
        assert body["command"] == "python app.py"

    async def test_exec_false_but_code_zero(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"execSuccess": False, "errorMsg": "未检测到服务...外部地址：https://c-xxx.ksai.scnet.cn:58043", "output": None},
        })
        result = await main.notebook_start_custom_service(notebook_id="nb-001", customize_port="1223")
        assert result["code"] == "0"
        assert result["data"]["execSuccess"] is False
        # code=0 means the caller should then call notebook_query_custom_service_url

    async def test_missing_params(self, env):
        main, mock = env
        result = await main.notebook_start_custom_service(notebook_id="", customize_port="")
        assert result.get("error") is True

    async def test_no_command_optional(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"execSuccess": True, "errorMsg": None, "output": None},
        })
        result = await main.notebook_start_custom_service(notebook_id="nb-001", customize_port="8080")
        assert result["code"] == "0"
        body = _post_body(mock)
        assert "command" not in body


# ===================================================================
# Round-Robin tests
# ===================================================================

class TestRoundRobin:
    """Test that aiUrls round-robin cycles through all comma-separated URLs."""

    async def test_second_call_uses_second_url(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={"code": "0", "msg": "ok"})
        mock.post.return_value = MockResponse(json_data={"code": "0", "msg": "ok"})
        # First call
        await main.notebook_list()
        url1 = _get_url(mock)
        # Second call
        await main.notebook_list()
        url2 = _get_url(mock)
        assert "ai2.scnet.cn" in url2
        assert url1 != url2

    async def test_third_call_wraps_to_first_url(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={"code": "0", "msg": "ok"})
        await main.notebook_list()  # ai1
        await main.notebook_list()  # ai2
        await main.notebook_list()  # wraps back to ai1
        url3 = _get_url(mock)
        assert "ai1.scnet.cn" in url3


# ===================================================================
# _ai_url() helper tests
# ===================================================================

class TestAiUrlHelper:
    """Test _ai_url() correctly strips duplicate /ai prefix."""

    async def test_normal_url(self, env):
        main, _ = env
        result = main._ai_url("https://ai1.scnet.cn", "/ai/openapi/v2/notebook/list")
        assert result == "https://ai1.scnet.cn/ai/openapi/v2/notebook/list"

    async def test_strips_duplicate_ai(self, env):
        main, _ = env
        result = main._ai_url("https://ai1.scnet.cn/ai", "/ai/openapi/v2/notebook/list")
        assert result == "https://ai1.scnet.cn/ai/openapi/v2/notebook/list"

    async def test_trailing_slash_stripped(self, env):
        main, _ = env
        result = main._ai_url("https://ai1.scnet.cn/", "/ai/openapi/v2/notebook/list")
        assert result == "https://ai1.scnet.cn/ai/openapi/v2/notebook/list"


# ===================================================================
# _get_default_token() aiUrls tests
# ===================================================================

class TestGetDefaultTokenWithAiUrls:
    """Test that _get_default_token() now returns aiUrls."""

    async def test_returns_ai_urls(self, env):
        main, _ = env
        resolved = main._get_default_token(TEST_USER)
        assert "error" not in resolved
        assert "aiUrls" in resolved
        assert "https://ai1.scnet.cn" in resolved["aiUrls"]
        assert "https://ai2.scnet.cn" in resolved["aiUrls"]
