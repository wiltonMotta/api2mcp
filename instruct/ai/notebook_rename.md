# notebook_rename

## 需求

实现一个 MCP tool `notebook_rename`，修改指定 Notebook 实例的名称。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token
- 需要已知 `notebookId`，可通过 `notebook_list` 或 `notebook_create` 获取

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- **Token 类型**：本接口使用集群级 `{aiUrls}`，因此使用 `user_cluster.token`（集群 token）
- aiUrls 获取方式：
  ```sql
  SELECT u.aiUrls
  FROM user_cluster c, cluster_url u
  WHERE u.clusterId = c.clusterId
    AND c.isDefault = true
    AND c.username = {current_username}
  ```
- **实现注意**：`_get_default_token()` 当前 SQL 未查询 `cu.aiUrls` 字段，需要在函数中添加 `cu.aiUrls` 到 SELECT 列表
- 如果用户未认证或无默认集群 token，返回错误提示 JSON，包含 `auth_url` 字段

## 字段命名策略

- **输出透传**：MCP 工具直接透传上游 API 的 JSON 响应，字段名保持 camelCase
- **输入映射**：MCP 参数使用 snake_case，在构造请求时映射为 OpenAPI 所需的 camelCase

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `notebook_id` | string | 是 | — | `id` | body | notebook 实例 ID。可从 `notebook_list` 返回的 `id` 字段获取 |
| `notebook_name` | string | 是 | — | `notebookName` | body | 新的 notebook 名称 |
| `cluster_id` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群 |

注：MCP 参数 `notebook_id` 映射到 OpenAPI 的 `id`（**注意：上游 API rename/release/startService 使用 `id`，而 stop 使用 `notebookId`，这是上游 API 自身的差异**）；`notebook_name` 映射到 `notebookName`。

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 aiUrls**：通过 `_get_default_token` 或指定 cluster_id 获取 token 和 aiUrls，采用 round-robin 策略
3. **构造请求**：
   - URL: `POST {aiUrl}/ai/openapi/v2/notebook/name`
   - **实现注意**：需使用 `_ai_url(base_url, path)` helper 拼接 URL（strip 重复 `/ai` 前缀，同 `_efile_url` 模式），避免 `aiUrls` 值已含 `/ai` 后缀时产生 `/ai/ai/...` 重复路径
   - Header: `token: {clusterToken}`, `Content-Type: application/json`
   - Body: `{"id": "...", "notebookName": "..."}`
4. **调用 API**：发送 POST 请求，超时 15s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `{aiUrls}/ai/openapi/v2/notebook/name`
- Method: POST
- Headers: `{"token": "{clusterToken}", "Content-Type": "application/json"}`
- Body (JSON):
  - `id` — notebook 实例 ID（必填）
  - `notebookName` — 新名称（必填）
- 超时: 15s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | boolean | 修改结果，`true` 表示修改成功 |

注：本接口 `data` 为 `boolean` 类型（非 object），与其他返回 object 的工具不同。

## 工具关联关系

- **入参依赖**：
  - `notebook_id` ← `notebook_list` 的 `records[].id`
  - `notebook_id` ← `notebook_create` 的 `data.notebookId`
  - `notebook_name` 为用户自定义的新名称

## 异常处理

- **未认证**：返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **缺少必填参数**：返回 `{"error": true, "message": "notebook_id 和 notebook_name 为必填参数"}`
- **API 返回错误**：返回 `{"error": true, "message": "重命名 Notebook 失败 [{code}]: {msg}"}`
- **网络异常**：返回 `{"error": true, "message": "重命名 Notebook 请求异常: {详情}"}`

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
- 将工具描述文档写入 `APIs` 表，name 为 `notebook_rename`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_rename` 函数。
