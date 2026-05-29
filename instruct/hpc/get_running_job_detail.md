# hpc_get_running_job_detail

## 需求

实现一个 MCP tool `hpc_get_running_job_detail`，根据 `jobId` 查询 HPC 集群中某个作业的详细信息（实时状态）。

## 前置条件

-

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `user_cluster` 表联合 `cluster_url` 表查询选定集群的 `token` 和 `hpcUrls`
- 如果用户未认证或选定集群无 token，返回错误提示 JSON，包含 `auth_url` 字段

## MCP 工具参数（用户传入）

| 参数名 | 类型 | 必填 | 默认值 / 来源说明 |
|--------|------|------|-------------------|
| `jobId` | string | 是 | 作业 ID（可从 `hpc_submit_job` 返回的 `jobID` 字段获取） |
| `clusterId` | integer | 否 | 可选：集群 ID。精确匹配可减少查询失败概率。如果省略，后端会自动遍历用户有权限的所有集群尝试查询。 |
| `token` | string | 否 | 可选：集群 token（可从 `hpc_submit_job` 返回的 `token` 字段获取）。如果省略，后端从数据库自动获取。 |
| `hpcUrls` | string | 否 | 可选：集群 hpcUrls（可从 `hpc_submit_job` 返回的 `hpcUrls` 字段获取）。如果省略，后端从数据库自动获取。 |

## 后端处理逻辑

1. **认证校验**：检查 `users` 表中是否有 `acToken`
2. **解析 token/hpcUrls**：优先级为 `显式参数 > 数据库`。如果提供了 `clusterId`，则精确查询该集群；否则遍历用户所有集群，取第一个有效的 token+hpcUrls 组合
3. **遍历查询**：按顺序遍历 `hpcUrls` 中的每个 URL，尝试查询作业详情，成功即返回
4. **返回结果**：直接返回 API 响应的 JSON 数据

## API 调用

- URL: `{base_url}/hpc/openapi/v2/jobs/{jobId}`
  - `{base_url}` 为从 `hpcUrls`（逗号分隔）中依次选取的 URL
  - `{jobId}` 为用户传入的作业 ID
- Method: GET
- Headers: `{"token": token, "Content-Type": "application/json"}`
- 无请求参数（jobId 通过 URL 路径传递）
- 超时 30s

## 异常处理

- **无可用集群**：`user_cluster` 中无任何集群的 token 或 hpcUrls，返回错误提示
- **集群无 URL**：若 `hpcUrls` 解析后为空列表，返回错误提示
- **所有 URL 查询失败**：遍历完所有 `hpcUrls` 后仍失败，返回最后一个失败的 HTTP 错误详情
- **作业不存在/无权限**：API 返回错误时，返回易于理解的中文提示信息

## 返回值

- 直接返回 API 响应的 JSON 数据（包含作业的实时运行状态、资源使用情况、节点信息等）

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `hpc_get_running_job_detail`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中 `@mcp.tool()` 装饰的 `hpc_get_running_job_detail` 函数。
