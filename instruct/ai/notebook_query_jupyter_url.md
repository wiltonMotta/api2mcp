# notebook_query_jupyter_url

## 需求

实现一个 MCP tool `notebook_query_jupyter_url`，查询指定 Notebook 实例的 JupyterLab 服务访问地址。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token
- Notebook 实例需处于 `Running`（运行中）状态
- 需要已知 `notebookId`，可通过 `notebook_list` 获取，通过 `notebook_detail` 确认状态

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

- **输出透传**：MCP 工具直接透传上游 API 的 JSON 响应，字段名保持 camelCase，不做 snake_case 映射
- **输入映射**：MCP 参数使用 snake_case，在构造请求时映射为 OpenAPI 所需的 camelCase

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `notebook_id` | string | 是 | — | `notebookId` | query | notebook 实例 ID。可从 `notebook_list` 返回的 `id` 字段获取 |
| `cluster_id` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群 |

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 aiUrls**：通过 `_get_default_token` 或指定 cluster_id 获取 token 和 aiUrls，采用 round-robin 策略
3. **构造请求**：
   - URL: `GET {aiUrl}/ai/openapi/v2/notebook/url`
   - **实现注意**：需使用 `_ai_url(base_url, path)` helper 拼接 URL（strip 重复 `/ai` 前缀，同 `_efile_url` 模式），避免 `aiUrls` 值已含 `/ai` 后缀时产生 `/ai/ai/...` 重复路径
   - Header: `token: {clusterToken}`
   - Query params: `notebookId`
4. **调用 API**：发送 GET 请求，超时 15s
5. **返回结果**：返回 API 响应 JSON，包含 `status` 和 `url`

## API 调用

- URL: `{aiUrls}/ai/openapi/v2/notebook/url`
- Method: GET
- Headers: `{"token": "{clusterToken}"}`
- Query params:
  - `notebookId` — notebook 实例 ID（必填）
- 超时: 15s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | object | 访问地址信息 |

### data 字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `status` | string | 可访问状态：`active`（可访问）、`inactive`（不可访问） |
| `url` | string | JupyterLab 完整访问地址（含 token 参数） |

## 工具关联关系

- **入参依赖**：
  - `notebook_id` ← `notebook_list` 的 `records[].id`
  - 建议先通过 `notebook_detail` 确认 `notebook_status` 为 `Running`
- **前置操作**：若实例未运行，需先调用 `notebook_start` 开机
- **同类工具**：`notebook_query_custom_service_url` — 查询自定义服务地址（非 Jupyter）

## 异常处理

- **未认证**：返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **缺少 notebook_id**：返回 `{"error": true, "message": "notebook_id 为必填参数"}`
- **实例不可访问**：`status` 为 `inactive` 时，建议提示用户检查实例是否处于 Running 状态
- **API 返回错误**：返回 `{"error": true, "message": "查询 Jupyter 地址失败 [{code}]: {msg}"}`
- **网络异常**：返回 `{"error": true, "message": "查询 Jupyter 地址请求异常: {详情}"}`

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

## 返回值示例

### 成功响应

```json
{
    "code": "0",
    "msg": "success",
    "data": {
        "status": "active",
        "url": "https://n-1896476639463936002.ksai.scnet.cn:58043/jupyter-forward/1896050410550136833/lab/tree/root/?token=sothisai_1896476639463936002&schedulerType=k8s&microservices=enabled&userToken=eyJ..."
    }
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表，name 为 `notebook_query_jupyter_url`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_query_jupyter_url` 函数。
