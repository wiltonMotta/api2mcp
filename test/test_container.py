"""Tests for all 13 container MCP tools (static implementations).

Tests:
  - container_create
  - container_start
  - container_stop
  - container_delete
  - container_execute
  - container_query_list
  - container_query_url
  - container_query_detail
  - container_update_resource
  - container_query_resources
  - container_query_resource_group
  - container_query_allowed_mount_dir
  - container_get_images
"""

import json

import pytest
import httpx

pytestmark = pytest.mark.asyncio

# ── Helpers ──────────────────────────────────────────────────────────

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
            def __init__(self, d): self._d = d
            def get(self, key, default=None): return self._d.get(key, default)
        return HeaderDict(self._headers)

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://ai1.scnet.cn/test")
            response = httpx.Response(
                self.status_code,
                content=self._content or json.dumps(self._json).encode(),
                request=request,
            )
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)


def get_url(mock):
    call = mock.get.call_args
    if not call: return ""
    return call[0][0] if call[0] else call.kwargs.get("url", "")


def get_post_url(mock):
    call = mock.post.call_args
    if not call: return ""
    return call[0][0] if call[0] else call.kwargs.get("url", "")


def get_request_url(mock):
    """Extract the URL from a client.request('GET', url, ...) call."""
    call = mock.request.call_args
    if not call: return ""
    # request(method, url, ...) — first positional arg is method, second is URL
    if call[0]:
        return call[0][1] if len(call[0]) > 1 else ""
    return call.kwargs.get("url", "")


def get_post_body(mock):
    call = mock.post.call_args
    if not call: return {}
    return call.kwargs.get("json", {}) or call.kwargs.get("data", {}) or {}


# ═══════════════════════════════════════════════════════════════════
# container_create
# ═══════════════════════════════════════════════════════════════════

class TestContainerCreate:

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"taskId": "task-001", "instanceServiceId": "inst-001"},
        })
        result = await main.container_create(
            instance_service_name="test-container",
            accelerator_type="cpu",
            image_path="/images/python:3.11",
            version="3.11",
            task_type="ssh",
            resource_group="rg-cpu-01",
            cpu_number=2, ram_size=4096, gpu_number=0,
            timeout_limit="unlimited",
        )
        assert result["code"] == "0"
        assert result["data"]["taskId"] == "task-001"
        body = get_post_body(mock)
        assert body["instanceServiceName"] == "test-container"
        assert body["acceleratorType"] == "cpu"
        assert body["cpuNumber"] == 2
        body_url = get_post_url(mock)
        assert "/ai/openapi/v2/instance-service" in body_url

    async def test_with_mount_info(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-002"},
        })
        result = await main.container_create(
            instance_service_name="test-mount",
            accelerator_type="gpu", image_path="/images/pytorch",
            version="2.1", task_type="jupyter",
            resource_group="rg-gpu-01",
            cpu_number=4, ram_size=16384, gpu_number=1,
            timeout_limit="04:00:00",
            mount_info_list=json.dumps([
                {"sourcePath": "/data", "targetPath": "/mnt/data", "type": "data"},
            ]),
            container_port_info_list=json.dumps([
                {"containerPort": 8888, "protocolType": "HTTP"},
            ]),
        )
        assert result["code"] == "0"
        body = get_post_body(mock)
        assert len(body["mountInfoList"]) == 1
        assert body["mountInfoList"][0]["sourcePath"] == "/data"
        assert len(body["containerPortInfoList"]) == 1

    async def test_invalid_mount_json(self, env):
        main, mock = env
        result = await main.container_create(
            instance_service_name="x", accelerator_type="cpu",
            image_path="/img", version="v1", task_type="ssh",
            resource_group="rg", cpu_number=1, ram_size=1024,
            gpu_number=0, timeout_limit="01:00:00",
            mount_info_list="not valid json",
        )
        assert result.get("error") is True
        assert "JSON 解析失败" in result.get("message", "")

    async def test_invalid_port_json(self, env):
        main, mock = env
        result = await main.container_create(
            instance_service_name="x", accelerator_type="cpu",
            image_path="/img", version="v1", task_type="ssh",
            resource_group="rg", cpu_number=1, ram_size=1024,
            gpu_number=0, timeout_limit="01:00:00",
            container_port_info_list="bad json",
        )
        assert result.get("error") is True
        assert "JSON 解析失败" in result.get("message", "")

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("POST", "https://ai1.scnet.cn/test")
        resp = httpx.Response(500, content=b"Internal Error", request=req)
        mock.post.side_effect = httpx.HTTPStatusError("500", request=req, response=resp)
        result = await main.container_create(
            instance_service_name="x", accelerator_type="cpu",
            image_path="/img", version="v1", task_type="ssh",
            resource_group="rg", cpu_number=1, ram_size=1024,
            gpu_number=0, timeout_limit="01:00:00",
        )
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# container_start
# ═══════════════════════════════════════════════════════════════════

class TestContainerStart:

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-start-001"},
        })
        result = await main.container_start(instance_service_id="inst-001")
        assert result["code"] == "0"
        url = get_post_url(mock)
        assert "/task/actions/restart" in url

    async def test_explicit_cluster(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-start-002"},
        })
        result = await main.container_start(instance_service_id="inst-001", cluster_id=2)
        assert result["code"] == "0"
        url = get_post_url(mock)
        assert "ai-second.scnet.cn" in url

    async def test_empty_id_sends_to_api(self, env):
        """Empty instance_service_id is sent to API (no client-side validation)."""
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": None,
        })
        result = await main.container_start(instance_service_id="")
        # The API may reject it, but the tool doesn't validate client-side
        url = get_post_url(mock)
        assert url != ""  # request was made


# ═══════════════════════════════════════════════════════════════════
# container_stop
# ═══════════════════════════════════════════════════════════════════

class TestContainerStop:

    async def test_success_single(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-stop-001"},
        })
        result = await main.container_stop(ids="inst-001")
        assert result["code"] == "0"
        url = get_post_url(mock)
        assert "/task/actions/stop" in url

    async def test_success_batch(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-stop-002"},
        })
        result = await main.container_stop(ids="inst-001,inst-002,inst-003")
        assert result["code"] == "0"
        # IDs are in URL query string
        call = mock.post.call_args
        url = call[0][0] if call[0] else ""
        assert "inst-001" in url and "inst-002" in url

    async def test_empty_ids(self, env):
        main, mock = env
        result = await main.container_stop(ids="")
        assert result.get("error") is True
        assert "容器实例 ID" in result.get("message", "")

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("POST", "https://ai1.scnet.cn/test")
        resp = httpx.Response(404, content=b"Not Found", request=req)
        mock.post.side_effect = httpx.HTTPStatusError("404", request=req, response=resp)
        result = await main.container_stop(ids="inst-001")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# container_delete
# ═══════════════════════════════════════════════════════════════════

class TestContainerDelete:

    async def test_success(self, env):
        main, mock = env
        mock.delete.return_value = MockResponse(json_data={
            "code": "0", "data": {"result": "deleted"},
        })
        result = await main.container_delete(ids="inst-001")
        assert result["code"] == "0"
        url = mock.delete.call_args[0][0] if mock.delete.call_args else ""
        assert "/instance-service" in url

    async def test_success_batch(self, env):
        main, mock = env
        mock.delete.return_value = MockResponse(json_data={
            "code": "0", "data": {"result": "deleted"},
        })
        result = await main.container_delete(ids="inst-001,inst-002")
        assert result["code"] == "0"

    async def test_empty_ids(self, env):
        main, mock = env
        result = await main.container_delete(ids="")
        assert result.get("error") is True

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("DELETE", "https://ai1.scnet.cn/test")
        resp = httpx.Response(500, content=b"Error", request=req)
        mock.delete.side_effect = httpx.HTTPStatusError("500", request=req, response=resp)
        result = await main.container_delete(ids="inst-001")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# container_execute
# ═══════════════════════════════════════════════════════════════════

class TestContainerExecute:

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-exec-001"},
        })
        result = await main.container_execute(
            instance_id="inst-001",
            start_script_content="echo hello",
        )
        assert result["code"] == "0"
        body = get_post_body(mock)
        assert body["startScriptContent"] == "echo hello"

    async def test_with_action_scope(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-exec-002"},
        })
        result = await main.container_execute(
            instance_id="inst-001",
            start_script_content="python train.py",
            start_script_action_scope="header",
        )
        body = get_post_body(mock)
        assert body["startScriptActionScope"] == "header"

    async def test_empty_instance_id_sends_to_api(self, env):
        """Empty instance_id is sent to API (no client-side validation)."""
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": None,
        })
        result = await main.container_execute(
            instance_id="",
            start_script_content="echo hello",
        )
        call = mock.post.call_args
        url = call[0][0] if call[0] else ""
        assert url != ""  # request was made

    async def test_missing_required_params(self, env):
        main, mock = env
        # instance_id is required (no default), start_script_content is required
        # empty strings are allowed but the test just confirms the function accepts them
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-exec-empty"},
        })
        result = await main.container_execute(
            instance_id="",
            start_script_content="",
        )
        assert result["code"] == "0"  # API handles validation

    async def test_success_with_return_code(self, env):
        """Mock response with returnCode field."""
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "taskId": "task-exec-003",
                "returnCode": 0,
            },
        })
        result = await main.container_execute(
            instance_id="inst-001",
            start_script_content="echo ok",
        )
        assert result["code"] == "0"
        assert result["data"]["returnCode"] == 0


# ═══════════════════════════════════════════════════════════════════
# container_query_list
# ═══════════════════════════════════════════════════════════════════

class TestContainerQueryList:

    async def test_success(self, env):
        main, mock = env
        # container_query_list uses client.request("GET", ...) not client.get()
        mock.request.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "total": 2, "items": [
                    {"id": "inst-001", "name": "test-ctr", "status": "Running",
                     "acceleratorType": "cpu", "taskType": "ssh"},
                    {"id": "inst-002", "name": "jupyter-ctr", "status": "Stopped",
                     "acceleratorType": "gpu", "taskType": "jupyter"},
                ],
            },
        })
        result = await main.container_query_list()
        assert result["code"] == "0"
        assert result["data"]["total"] == 2
        url = get_request_url(mock)
        assert "/instance-service/" in url

    async def test_with_filters(self, env):
        main, mock = env
        mock.request.return_value = MockResponse(json_data={
            "code": "0", "data": {"total": 0, "items": []},
        })
        result = await main.container_query_list(
            status="Running", task_type="ssh",
            instance_service_name="test",
        )
        assert result["code"] == "0"
        call = mock.request.call_args
        body = call.kwargs.get("json", {})
        assert body.get("status") == "Running"
        assert body.get("taskType") == "ssh"
        assert body.get("instanceServiceName") == "test"

    async def test_pagination(self, env):
        main, mock = env
        mock.request.return_value = MockResponse(json_data={
            "code": "0", "data": {"total": 50, "items": []},
        })
        result = await main.container_query_list(limit=10, start=10)
        call = mock.request.call_args
        body = call.kwargs.get("json", {})
        assert body.get("limit") == 10
        assert body.get("start") == 10


# ═══════════════════════════════════════════════════════════════════
# container_query_url
# ═══════════════════════════════════════════════════════════════════

class TestContainerQueryUrl:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "instanceServiceId": "inst-001",
                "jupyterUrl": "https://jupyter.example.com/abc123",
                "codeServerUrl": "https://codeserver.example.com/xyz789",
            },
        })
        result = await main.container_query_url(instance_id="inst-001")
        assert result["code"] == "0"
        assert "jupyterUrl" in result["data"]

    async def test_explicit_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {"instanceServiceId": "inst-002"},
        })
        result = await main.container_query_url(
            instance_id="inst-002", cluster_id=2,
        )
        assert result["code"] == "0"
        url = get_url(mock)
        assert "ai-second.scnet.cn" in url

    async def test_empty_id_sends_to_api(self, env):
        """Empty instance_id is sent to API."""
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": None,
        })
        result = await main.container_query_url(instance_id="")
        url = get_url(mock)
        assert url != ""  # request was made


# ═══════════════════════════════════════════════════════════════════
# container_query_detail
# ═══════════════════════════════════════════════════════════════════

class TestContainerQueryDetail:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "instanceServiceId": "inst-001",
                "instanceServiceName": "test-container",
                "status": "Running",
                "acceleratorType": "cpu",
                "cpuNumber": 2,
                "ramSize": 4096,
                "gpuNumber": 0,
                "imageName": "python:3.11",
                "createTime": "2026-05-27 10:00:00",
            },
        })
        result = await main.container_query_detail(instance_id="inst-001")
        assert result["code"] == "0"
        assert result["data"]["status"] == "Running"
        assert result["data"]["cpuNumber"] == 2

    async def test_not_found(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": None,
        })
        result = await main.container_query_detail(instance_id="nonexistent")
        # API returns data=None, tool doesn't validate on client side
        assert result["data"] is None

    async def test_explicit_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "instanceServiceId": "inst-002", "status": "Stopped",
            },
        })
        result = await main.container_query_detail(
            instance_id="inst-002", cluster_id=2,
        )
        assert result["code"] == "0"


# ═══════════════════════════════════════════════════════════════════
# container_update_resource
# ═══════════════════════════════════════════════════════════════════

class TestContainerUpdateResource:

    async def test_success(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-update-001"},
        })
        result = await main.container_update_resource(
            instance_id="inst-001",
            cpu_number=4, ram_size=8192, gpu_number=1,
        )
        assert result["code"] == "0"
        body = get_post_body(mock)
        assert body["cpuNumber"] == 4
        assert body["ramSize"] == 8192
        assert body["gpuNumber"] == 1

    async def test_empty_instance_id_sends_to_api(self, env):
        """Empty instance_id is sent to API (no client-side validation)."""
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"taskId": "task-update-empty"},
        })
        result = await main.container_update_resource(
            instance_id="", cpu_number=2, ram_size=4096, gpu_number=0,
        )
        # API handles validation
        assert get_post_url(mock) != ""

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("POST", "https://ai1.scnet.cn/test")
        resp = httpx.Response(400, content=b"Bad Request", request=req)
        mock.post.side_effect = httpx.HTTPStatusError("400", request=req, response=resp)
        result = await main.container_update_resource(
            instance_id="inst-001", cpu_number=4, ram_size=8192, gpu_number=0,
        )
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# container_query_resources
# ═══════════════════════════════════════════════════════════════════

class TestContainerQueryResources:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "groupId": "rg-001", "groupName": "gpu-group",
                "cpuPerNode": 64, "gpuPerNode": 4, "memoryPerNode": 256,
            },
        })
        result = await main.container_query_resources(
            accelerator_type="gpu", resource_group="gpu-group",
        )
        assert result["code"] == "0"
        assert result["data"]["cpuPerNode"] == 64
        url = get_url(mock)
        assert "/resources" in url

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("GET", "https://ai1.scnet.cn/test")
        resp = httpx.Response(404, content=b"Not Found", request=req)
        mock.get.side_effect = httpx.HTTPStatusError("404", request=req, response=resp)
        result = await main.container_query_resources(
            accelerator_type="cpu", resource_group="cpu-group",
        )
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# container_query_resource_group
# ═══════════════════════════════════════════════════════════════════

class TestContainerQueryResourceGroup:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "gpu": [
                    {"id": "rg-gpu-01", "name": "NVIDIA A100", "acceleratorType": "gpu"},
                ],
                "cpu": [
                    {"id": "rg-cpu-01", "name": "CPU 普通型", "acceleratorType": "cpu"},
                ],
            },
        })
        result = await main.container_query_resource_group()
        assert result["code"] == "0"
        assert "gpu" in result["data"]
        assert "cpu" in result["data"]
        assert len(result["data"]["gpu"]) == 1
        url = get_url(mock)
        assert "/resource-group" in url

    async def test_empty_result(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {"gpu": [], "cpu": []},
        })
        result = await main.container_query_resource_group()
        assert result["code"] == "0"
        assert result["data"]["gpu"] == []


# ═══════════════════════════════════════════════════════════════════
# container_query_allowed_mount_dir
# ═══════════════════════════════════════════════════════════════════

class TestContainerQueryAllowedMountDir:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": [
                {"path": "/work/home", "mountType": "home", "description": "用户主目录"},
                {"path": "/work/project", "mountType": "data", "description": "项目数据"},
            ],
        })
        result = await main.container_query_allowed_mount_dir()
        assert result["code"] == "0"
        assert len(result["data"]) == 2
        url = get_url(mock)
        assert "/allowed-mount-dir" in url

    async def test_empty_result(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": [],
        })
        result = await main.container_query_allowed_mount_dir()
        assert result["code"] == "0"
        assert result["data"] == []


# ═══════════════════════════════════════════════════════════════════
# container_get_images
# ═══════════════════════════════════════════════════════════════════

class TestContainerGetImages:

    async def test_success_default(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "total": 3, "items": [
                    {"path": "/images/python:3.11", "version": "3.11",
                     "type": "base", "acceleratorType": "cpu"},
                    {"path": "/images/pytorch:2.1", "version": "2.1",
                     "type": "ai", "acceleratorType": "gpu"},
                    {"path": "/images/tensorflow:2.13", "version": "2.13",
                     "type": "ai", "acceleratorType": "gpu"},
                ],
            },
        })
        result = await main.container_get_images(access="public")
        assert result["code"] == "0"
        assert result["data"]["total"] == 3
        url = get_post_url(mock)
        assert "/image" in url

    async def test_with_filters(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"total": 1, "items": []},
        })
        result = await main.container_get_images(
            access="public", image_type="ai", accelerator_type="gpu",
        )
        body = get_post_body(mock)
        assert body.get("type") == "ai"
        assert body.get("acceleratorType") == "gpu"

    async def test_pagination(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"total": 100, "items": []},
        })
        result = await main.container_get_images(access="public", limit=10, start=10)
        body = get_post_body(mock)
        assert body.get("limit") == 10
        assert body.get("start") == 10

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("POST", "https://ai1.scnet.cn/test")
        resp = httpx.Response(500, content=b"Error", request=req)
        mock.post.side_effect = httpx.HTTPStatusError("500", request=req, response=resp)
        result = await main.container_get_images(access="public")
        assert result.get("error") is True
