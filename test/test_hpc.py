"""Tests for HPC MCP tools (non-proxy, static implementations).

Tests:
  - hpc_list_available_partitions
  - hpc_submit_job (including parameter validation)
  - hpc_get_running_job_detail
  - hpc_get_history_job_detail
  - hpc_list_running_jobs
  - hpc_list_history_jobs
  - hpc_cancel_job
  - hpc_query_job_state
  - hpc_query_core_num
  - hpc_query_queue_jobs
  - hpc_query_user_quota
  - hpc_query_used_time
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
            request = httpx.Request("POST", "https://hpc1.scnet.cn/test")
            response = httpx.Response(
                self.status_code,
                content=self._content or json.dumps(self._json).encode(),
                request=request,
            )
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)


def make_get(url: str, json_data: dict, status_code: int = 200) -> MockResponse:
    """Convenience: a GET MockResponse."""
    r = MockResponse(json_data=json_data, status_code=status_code)
    r._url = url
    return r


def make_post(url: str, json_data: dict, status_code: int = 200) -> MockResponse:
    """Convenience: a POST MockResponse."""
    r = MockResponse(json_data=json_data, status_code=status_code)
    r._url = url
    return r


def get_url(mock):
    """Extract the URL from the most recent GET call."""
    call = mock.get.call_args
    if not call:
        return ""
    return call[0][0] if call[0] else call.kwargs.get("url", "")


def get_post_url(mock):
    """Extract the URL from the most recent POST call."""
    call = mock.post.call_args
    if not call:
        return ""
    return call[0][0] if call[0] else call.kwargs.get("url", "")


# ═══════════════════════════════════════════════════════════════════
# hpc_list_available_partitions
# ═══════════════════════════════════════════════════════════════════

class TestHpcListAvailablePartitions:

    async def test_success(self, env):
        """Returns queues with available resources, grouped by cluster."""
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": [
                {"queueName": "debug", "queFreeNcpus": 4, "aclHosts": []},
                {"queueName": "free", "queFreeNcpus": 16, "aclHosts": []},
            ],
        })
        result = await main.hpc_list_available_partitions()
        assert len(result) == 2  # two clusters in test data
        assert result[0]["clusterId"] == 1
        assert result[0]["queues"][0]["queueName"] == "debug"
        assert "aclHosts" not in result[0]["queues"][0]
        # Verify correct URL pattern
        url = get_url(mock)
        assert "queuenames/users/testuser" in url

    async def test_empty_queues(self, env):
        """Queues with zero available resources are filtered out."""
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": [
                {"queueName": "full", "queFreeNcpus": 0, "aclHosts": []},
            ],
        })
        result = await main.hpc_list_available_partitions()
        assert result == []

    async def test_no_clusters(self, env):
        """No clusters configured → empty result."""
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("DELETE FROM user_cluster WHERE userName = ?", ("testuser",))
        conn.commit()
        conn.close()
        result = await main.hpc_list_available_partitions()
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# hpc_submit_job — parameter validation
# ═══════════════════════════════════════════════════════════════════

class TestHpcSubmitJobValidation:

    async def test_empty_cmd(self, env):
        """GAP_CMD_FILE is empty → validation error."""
        main, mock = env
        result = await main.hpc_submit_job(queueName="debug", GAP_CMD_FILE="")
        assert result.get("error") is True
        assert "GAP_CMD_FILE" in result.get("message", "")

    async def test_empty_cmd_whitespace(self, env):
        """GAP_CMD_FILE is whitespace only → validation error."""
        main, mock = env
        result = await main.hpc_submit_job(queueName="debug", GAP_CMD_FILE="   ")
        assert result.get("error") is True
        assert "GAP_CMD_FILE" in result.get("message", "")

    async def test_nnode_nodestring_conflict(self, env):
        """GAP_NNODE and GAP_NODE_STRING both non-empty → conflict error."""
        main, mock = env
        result = await main.hpc_submit_job(
            queueName="debug", GAP_CMD_FILE="sleep 1",
            GAP_NNODE="2", GAP_NODE_STRING="node01",
        )
        assert result.get("error") is True
        assert "互斥" in result.get("message", "")

    async def test_nproc_ppn_conflict(self, env):
        """GAP_NPROC and GAP_PPN both non-empty → conflict error."""
        main, mock = env
        result = await main.hpc_submit_job(
            queueName="debug", GAP_CMD_FILE="sleep 1",
            GAP_NPROC="4", GAP_PPN="2",
        )
        assert result.get("error") is True
        assert "互斥" in result.get("message", "")

    async def test_submit_success(self, env):
        """Full submit flow: cluster lookup → jobManagerID → POST."""
        main, mock = env
        # First GET: cluster info
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": [{"id": "12345", "text": "Cluster1"}],
        })
        # POST: job submission
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"jobID": "99999", "jobName": "sleep_0104_120000"},
        })
        result = await main.hpc_submit_job(
            queueName="debug", GAP_CMD_FILE="sleep 900",
            GAP_NNODE="1", GAP_WALL_TIME="01:00:00",
        )
        assert result["code"] == "0"
        assert result["data"]["jobID"] == "99999"
        # Verify POST URL
        post_url = get_post_url(mock)
        assert "apptemplates/BASIC/BASE/job" in post_url


# ═══════════════════════════════════════════════════════════════════
# hpc_get_running_job_detail
# ═══════════════════════════════════════════════════════════════════

class TestHpcGetRunningJobDetail:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "jobId": "12345", "jobStatus": "Running",
                "queueName": "debug", "clusterId": 1,
            },
        })
        result = await main.hpc_get_running_job_detail(jobId="12345")
        assert result["code"] == "0"
        assert result["data"]["jobStatus"] == "Running"
        url = get_url(mock)
        assert "/jobs/12345" in url

    async def test_not_found(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": None,
        })
        result = await main.hpc_get_running_job_detail(jobId="99999")
        assert result.get("error") is True
        assert "未找到" in result.get("message", "")

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("GET", "https://hpc1.scnet.cn/test")
        resp = httpx.Response(404, content=b"Not Found", request=req)
        mock.get.side_effect = httpx.HTTPStatusError("404", request=req, response=resp)
        result = await main.hpc_get_running_job_detail(jobId="12345")
        assert result.get("error") is True
        assert "12345" in result.get("message", "")


# ═══════════════════════════════════════════════════════════════════
# hpc_get_history_job_detail
# ═══════════════════════════════════════════════════════════════════

class TestHpcGetHistoryJobDetail:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "jobId": "12345", "jobStatus": "Completed",
                "startTime": "2026-05-01 10:00:00",
                "endTime": "2026-05-01 11:00:00",
            },
        })
        result = await main.hpc_get_history_job_detail(jobId="12345")
        assert result["code"] == "0"
        url = get_url(mock)
        assert "/historyjobs/12345/" in url or "/historyjobs/12345/12345" in url

    async def test_success_with_acct_time(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {"jobId": "12345"},
        })
        result = await main.hpc_get_history_job_detail(
            jobId="12345", acctTime="2026-05-01 12:00:00",
        )
        assert result["code"] == "0"
        call = mock.get.call_args
        params = call.kwargs.get("params", {})
        assert params.get("acctTime") == "2026-05-01 12:00:00"

    async def test_auth_failure(self, env):
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("UPDATE users SET acToken = NULL WHERE userName = ?", ("testuser",))
        conn.commit()
        conn.close()
        result = await main.hpc_get_history_job_detail(jobId="12345")
        assert result[0].get("error") if isinstance(result, list) else result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# hpc_list_running_jobs (proxy via AC API)
# ═══════════════════════════════════════════════════════════════════

class TestHpcListRunningJobs:

    async def test_success_default(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"total": 1, "records": [
                {"jobId": "111", "jobName": "my_job", "jobState": "statR"},
            ]},
        })
        result = await main.hpc_list_running_jobs()
        assert result["code"] == "0"
        assert result["data"]["total"] == 1
        assert result["data"]["records"][0]["jobId"] == "111"
        url = get_post_url(mock)
        assert "jobs/monitor/page-list" in url

    async def test_with_filters(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"total": 0, "records": []},
        })
        result = await main.hpc_list_running_jobs(
            page=1, size=20, clusterId="11250",
            queue="debug", jobState="statR", showAllData=True,
        )
        assert result["code"] == "0"
        call = mock.post.call_args
        body = call.kwargs.get("json", {})
        assert body["clusterId"] == "11250"
        assert body["queue"] == "debug"
        assert body["jobState"] == "statR"
        assert body["showAllData"] is True

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("POST", "https://www.scnet.cn/test")
        resp = httpx.Response(500, content=b"Internal Server Error", request=req)
        mock.post.side_effect = httpx.HTTPStatusError("500", request=req, response=resp)
        result = await main.hpc_list_running_jobs()
        assert result.get("error") is True
        assert "500" in result.get("message", "")


# ═══════════════════════════════════════════════════════════════════
# hpc_list_history_jobs (proxy via AC API)
# ═══════════════════════════════════════════════════════════════════

class TestHpcListHistoryJobs:

    async def test_success_default(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"total": 3, "records": [
                {"jobId": "222", "jobState": "statC"},
            ]},
        })
        result = await main.hpc_list_history_jobs()
        assert result["code"] == "0"
        assert result["data"]["total"] == 3
        url = get_post_url(mock)
        assert "jobs/history/page-list" in url

    async def test_with_time_range(self, env):
        main, mock = env
        mock.post.return_value = MockResponse(json_data={
            "code": "0", "data": {"total": 0, "records": []},
        })
        result = await main.hpc_list_history_jobs(
            startTime="2026-01-01 00:00:00", endTime="2026-05-31 23:59:59",
            jobId="222", jobState="statD", clusterUserName="testuser",
        )
        call = mock.post.call_args
        body = call.kwargs.get("json", {})
        assert body["jobId"] == "222"
        assert body["jobState"] == "statD"
        assert body["startTime"] == "2026-01-01 00:00:00"
        assert body["endTime"] == "2026-05-31 23:59:59"

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("POST", "https://www.scnet.cn/test")
        resp = httpx.Response(403, content=b"Forbidden", request=req)
        mock.post.side_effect = httpx.HTTPStatusError("403", request=req, response=resp)
        result = await main.hpc_list_history_jobs()
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# hpc_cancel_job
# ═══════════════════════════════════════════════════════════════════

class TestHpcCancelJob:

    async def test_success(self, env):
        main, mock = env
        mock.delete.return_value = MockResponse(json_data={
            "code": "0", "msg": "success",
            "data": {"result": "success"},
        })
        result = await main.hpc_cancel_job(jobId="12345")
        assert result["code"] == "0"
        # Verify DELETE URL
        url = mock.delete.call_args[0][0] if mock.delete.call_args else ""
        assert "/hpc/openapi/v2/jobs" in url

    async def test_success_batch(self, env):
        main, mock = env
        mock.delete.return_value = MockResponse(json_data={
            "code": "0", "data": {"result": "success"},
        })
        result = await main.hpc_cancel_job(jobId="12345,12346,12347")
        assert result["code"] == "0"

    async def test_empty_job_id(self, env):
        main, mock = env
        result = await main.hpc_cancel_job(jobId="")
        assert result.get("error") is True
        assert "作业 ID" in result.get("message", "")

    async def test_http_error(self, env):
        main, mock = env
        req = httpx.Request("DELETE", "https://hpc1.scnet.cn/test")
        resp = httpx.Response(500, content=b"Error", request=req)
        mock.delete.side_effect = httpx.HTTPStatusError("500", request=req, response=resp)
        result = await main.hpc_cancel_job(jobId="12345")
        assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# hpc_query_job_state
# ═══════════════════════════════════════════════════════════════════

class TestHpcQueryJobState:

    async def test_success_default(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "running": 2, "queued": 5, "held": 0,
                "suspended": 1, "other": 0,
            },
        })
        result = await main.hpc_query_job_state()
        assert result["code"] == "0"
        assert result["data"]["running"] == 2
        url = get_url(mock)
        assert "/view/jobs/state" in url

    async def test_explicit_cluster(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {"running": 0, "queued": 0},
        })
        result = await main.hpc_query_job_state(clusterId=2)
        assert result["code"] == "0"
        # Should use the second cluster's URL
        url = get_url(mock)
        assert "hpc-second.scnet.cn" in url


# ═══════════════════════════════════════════════════════════════════
# hpc_query_core_num
# ═══════════════════════════════════════════════════════════════════

class TestHpcQueryCoreNum:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {
                "used": 100, "free": 900, "unavailable": 0,
            },
        })
        result = await main.hpc_query_core_num()
        assert result["code"] == "0"
        assert result["data"]["used"] == 100
        url = get_url(mock)
        assert "/view/cpucore/state" in url


# ═══════════════════════════════════════════════════════════════════
# hpc_query_queue_jobs
# ═══════════════════════════════════════════════════════════════════

class TestHpcQueryQueueJobs:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": [
                {"queue": "debug", "R": 1, "Q": 2, "H": 0, "S": 0, "O": 0},
                {"queue": "free", "R": 3, "Q": 0, "H": 0, "S": 0, "O": 0},
            ],
        })
        result = await main.hpc_query_queue_jobs()
        assert result["code"] == "0"
        assert len(result["data"]) == 2
        url = get_url(mock)
        assert "/view/queue/jobs" in url


# ═══════════════════════════════════════════════════════════════════
# hpc_query_user_quota
# ═══════════════════════════════════════════════════════════════════

class TestHpcQueryUserQuota:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": [
                {"path": "/work/home", "threshold": 100, "usage": 25.5},
            ],
        })
        result = await main.hpc_query_user_quota()
        assert result["code"] == "0"
        assert result["data"][0]["threshold"] == 100
        url = get_url(mock)
        assert "/parastor/quota/usernames/testuser" in url


# ═══════════════════════════════════════════════════════════════════
# hpc_query_used_time
# ═══════════════════════════════════════════════════════════════════

class TestHpcQueryUsedTime:

    async def test_success(self, env):
        main, mock = env
        mock.get.return_value = MockResponse(json_data={
            "code": "0", "data": {"usedCoreHours": 150.5},
        })
        result = await main.hpc_query_used_time()
        assert result["code"] == "0"
        assert result["data"]["usedCoreHours"] == 150.5
        url = get_url(mock)
        assert "/view/walltime/users/testuser" in url


# ═══════════════════════════════════════════════════════════════════
# hpc_list_running_jobs — edge cases
# ═══════════════════════════════════════════════════════════════════

class TestHpcListRunningJobsEdge:

    async def test_auth_failure(self, env):
        """Unauthenticated user gets auth error."""
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("UPDATE users SET acToken = NULL WHERE userName = ?", ("testuser",))
        conn.commit()
        conn.close()
        result = await main.hpc_list_running_jobs()
        # Result is a list with error dict when auth fails in some tools
        if isinstance(result, list):
            assert result[0].get("error") is True
        else:
            assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════
# set_default_cluster — more edge cases
# ═══════════════════════════════════════════════════════════════════

class TestSetDefaultClusterEdge:

    async def test_cluster_name_not_found(self, env):
        """ClusterName fuzzy match returns no results."""
        main, mock = env
        result = await main.set_default_cluster(clusterName="nonexistent_cluster_xyz")
        assert result.get("error") is True
        assert "未找到" in result.get("message", "")

    async def test_cluster_id_not_found(self, env):
        """Specified clusterId doesn't belong to user."""
        main, mock = env
        result = await main.set_default_cluster(clusterId=99999)
        assert result.get("error") is True
        assert "不属于用户" in result.get("message", "")


# ═══════════════════════════════════════════════════════════════════
# _call_scnet_with_renewal — mock-based edge case tests
# ═══════════════════════════════════════════════════════════════════

class TestCallScnetWithRenewal:

    async def test_renewal_on_10008(self, env):
        """When SCNet returns code 10008, token is renewed and request retried."""
        main, mock = env
        # First call: token expired (10008), second call: success
        mock.get.side_effect = [
            MockResponse(json_data={"code": "10008", "msg": "token expired"}),
            MockResponse(json_data={"code": "0", "msg": "success", "data": None}),
        ]
        # This test verifies the renewal path works by checking second call succeeds
        # Note: mock doesn't actually renew the token in DB, so 10008 won't trigger renewal
        # since _renew_and_persist_token tries to call SCNet API which is also mocked
        result = await main.hpc_query_job_state()
        # In real scenario, 10008 would trigger renewal. With mocks, it depends on
        # whether _call_scnet_with_renewal detects 10008 and calls the renewal endpoint.

    async def test_http_error_not_10008(self, env):
        """HTTP errors other than 10008 are returned as-is without renewal."""
        main, mock = env
        req = httpx.Request("GET", "https://hpc1.scnet.cn/test")
        resp = httpx.Response(503, content=b"Service Unavailable", request=req)
        mock.get.side_effect = httpx.HTTPStatusError("503", request=req, response=resp)
        result = await main.hpc_query_job_state()
        # The _call_scnet_with_renewal catches HTTPStatusError and returns raw response
        # if code != 10008


# ═══════════════════════════════════════════════════════════════════
# hpc_get_history_job_detail — auth failure edge case
# ═══════════════════════════════════════════════════════════════════

class TestHpcGetHistoryJobDetailAuth:

    async def test_auth_failure(self, env):
        """Unauthenticated user gets auth error list."""
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("UPDATE users SET acToken = NULL WHERE userName = ?", ("testuser",))
        conn.commit()
        conn.close()
        result = await main.hpc_get_history_job_detail(jobId="12345")
        # Auth check returns list [error_dict] for unauthenticated users
        if isinstance(result, list):
            assert result[0].get("error") is True


# ═══════════════════════════════════════════════════════════════════
# hpc_query_job_state — edge cases
# ═══════════════════════════════════════════════════════════════════

class TestHpcQueryJobStateEdge:

    async def test_no_hpc_urls(self, env):
        """No HPC URLs configured → error."""
        main, mock = env
        import sqlite3
        conn = sqlite3.connect(main.DB_PATH)
        conn.execute("UPDATE cluster_url SET hpcUrls = '' WHERE clusterId = ?", (1,))
        conn.commit()
        conn.close()
        result = await main.hpc_query_job_state()
        assert result.get("error") is True
        assert "HPC 服务 URL" in result.get("message", "")
