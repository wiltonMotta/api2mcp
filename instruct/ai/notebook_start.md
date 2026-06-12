# notebook_start

## 需求

实现一个 MCP tool `notebook_start`，启动（开机）指定的 Notebook 容器实例。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `users` 表中存在有效的 `acToken`
- 需要已知 `notebookId`，可通过 `notebook_list` 或 `notebook_create` 获取
- Notebook 实例当前状态应为 `Terminated`（已关机）或 `Failed`（失败）

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- **Token 类型**：本接口使用平台级 AC URL（`www.scnet.cn/ac/openapi/v2/...`），因此使用 `users.acToken` 而非集群 token。参考 `hpc_list_history_jobs`（main.py）中 AC URL 的认证模式。
- acToken 获取方式：
  ```sql
  SELECT acToken FROM users WHERE userName = {current_username}
  ```
- 如果用户未认证（`acToken` 为 NULL），返回错误提示 JSON，包含 `auth_url` 字段
- **URL 路由说明**：开机操作由 AC 平台统一调度，因此使用平台级静态 URL 而非集群级 `{aiUrls}`。后续的状态变更操作（stop/release/rename）直连集群 `{aiUrls}` 以获得更低延迟。

## 字段命名策略

- **输出透传**：MCP 工具直接透传上游 API 的 JSON 响应，字段名保持 camelCase
- **输入映射**：MCP 参数使用 snake_case，在构造请求时映射为 OpenAPI 所需的 camelCase

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `notebook_id` | string | 是 | — | `notebookId` | body | Notebook 实例 ID。可从 `notebook_list` 返回的 `id` 字段或 `notebook_create` 返回的 `notebookId` 字段获取 |
| `cluster_id` | integer | 否 | `None` | — | — | 集群 ID（仅用于获取 acToken 时的上下文参考，不影响 API 路由）。为空时使用默认集群 |

## 后端处理逻辑

1. **认证校验**：从 `users` 表查询 `acToken`，若为 NULL 则返回认证错误
2. **获取 token**：使用 `users.acToken` 作为 HTTP header `token`（AC URL 使用 acToken，非集群 token）
3. **构造请求**：
   - URL: `POST https://www.scnet.cn/ac/openapi/v2/notebook/actions/start`
   - Header: `token: {acToken}`, `Content-Type: application/json`
   - Body: `{"notebookId": "..."}`
4. **调用 API**：发送 POST 请求，超时 30s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `https://www.scnet.cn/ac/openapi/v2/notebook/actions/start`
- Method: POST
- Headers: `{"token": "{acToken}", "Content-Type": "application/json"}`
- Body (JSON):
  - `notebookId` — Notebook 实例 ID（必填）
- 超时: 30s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | boolean | 开机结果，`true` 表示开机成功 |

注：本接口 `data` 为 `boolean` 类型（非 object），与其他返回 object 的工具不同。

## 工具关联关系

- **入参依赖**：
  - `notebook_id` ← `notebook_list` 的 `records[].id`
  - `notebook_id` ← `notebook_create` 的 `data.notebookId`
- **后续操作**：开机成功后（状态变为 `Running`），可调用：
  - `notebook_query_jupyter_url` — 获取 Jupyter 访问地址
  - `notebook_query_custom_service_url` — 获取自定义服务地址

## 异常处理

- **未认证**：`acToken` 为 NULL，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **缺少 notebook_id**：返回 `{"error": true, "message": "notebook_id 为必填参数"}`
- **API 返回错误**：返回 `{"error": true, "message": "启动 Notebook 失败 [{code}]: {msg}"}`
- **网络异常**：返回 `{"error": true, "message": "启动 Notebook 请求异常: {详情}"}`

## 常见错误码

| 错误码 | 说明 |
|--------|------|
| `0` | 成功 |
| `10001` | 内部错误 |
| `10003` | 参数不全 |
| `10004` | 参数无效 |
| `10007` | 用户已被冻结 |
| `10008` | 权限不足 |
| `10009` | 没有权限访问接口 |
| `716865` | 创建任务错误 |

## 返回值示例

### 成功响应

```json
{
    "code": "0",
    "msg": "success",
    "data": true
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表，name 为 `notebook_start`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_start` 函数。
