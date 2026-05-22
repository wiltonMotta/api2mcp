# get_running_job_detail

## 需求

实现一个 MCP tool `get_running_job_detail`，根据 `jobId` 查询 HPC 集群中某个作业的详细信息（实时状态）。

## 前置条件

-

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `user_cluster` 表联合 `cluster_url` 表查询选定集群的 `token` 和 `hpcUrls`
- 如果用户未认证或选定集群无 token，返回错误提示 JSON，包含 `auth_url` 字段

## MCP 工具参数（用户传入）

| 参数名 | 类型 | 必填 | 默认值 / 来源说明 |
|--------|------|------|-------------------|
| `jobId` | string | 是 | 作业 ID（可从 `submit_job` 返回的 `jobID` 字段获取） |
| `token` | string | 是 | token（可从 `submit_job` 返回的 `token` 字段获取） |

## 后端处理逻辑

1. **认证校验**：检查选定集群的 token 是否存在
2.  **调用 API**：GET `{base_url}/hpc/openapi/v2/jobs/{jobId}`，header 带 `token` 和 `Content-Type: application/json`
3. **返回结果**：直接返回 API 响应的 JSON 数据

## API 调用

- URL: `{hpcUrls}/hpc/openapi/v2/jobs/{jobId}`
  - `{hpcUrls}` 为从 `cluster_url` 表中获取的集群 URL（随机选取）
  - `{jobId}` 为用户传入的作业 ID
- Method: GET
- Headers: `{"token": token, "Content-Type": "application/json"}`
- 无请求参数（jobId 通过 URL 路径传递）
- 超时 30s

## 异常处理

- **无可用集群**： `user_cluster` 中无对应 `clusterId` 的 token，返回错误提示，说明无法查询作业
- **集群无 URL**：若从 `cluster_url` 表中获取 `hpcUrls` 失败，返回适当提示
- **作业不存在/无权限**：API 返回错误时，返回易于理解的中文提示信息
- **其他错误**：捕获 HTTP 异常或 API 返回的错误码，返回易于理解的中文提示信息

## 返回值

- 直接返回 API 响应的 JSON 数据（包含作业的实时运行状态、资源使用情况、节点信息等）

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `get_running_job_detail`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中 `@mcp.tool()` 装饰的 `get_running_job_detail` 函数。
