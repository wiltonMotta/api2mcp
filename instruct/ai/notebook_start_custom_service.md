# notebook_start_custom_service

## 需求

实现一个 MCP tool `notebook_start_custom_service`，在指定 Notebook 容器实例中启动用户自定义服务（如 WebUI、API 服务等），指定监听端口和启动命令。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token
- Notebook 实例需处于 `Running`（运行中）状态
- 需要已知 `notebookId`，可通过 `notebook_list` 获取
- 建议先通过 `notebook_detail` 查看当前实例的 `command` 和 `customizePort` 作为参考

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
| `notebook_id` | string | 是 | — | `id` | body | notebook 实例 ID。可从 `notebook_list` 返回的 `id` 字段获取 |
| `customize_port` | string | 是 | — | `customizePort` | body | 自定义服务监听的端口号。可从 `notebook_detail` 的 `customizePort` 字段获取参考值 |
| `command` | string | 否 | `None` | `command` | body | 自定义服务启动命令。可从 `notebook_detail` 的 `command` 字段获取参考值 |
| `cluster_id` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群 |

注：MCP 参数 `notebook_id` 映射到 OpenAPI 的 `id`（**注意：上游 API startService 使用 `id`，而 stop 使用 `notebookId`，这是上游 API 自身的差异**）；`customize_port` 映射到 `customizePort`。

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 aiUrls**：通过 `_get_default_token` 或指定 cluster_id 获取 token 和 aiUrls，采用 round-robin 策略
3. **构造请求**：
   - URL: `POST {aiUrl}/ai/openapi/v2/notebook/customize-service/actions/start`
   - **实现注意**：需使用 `_ai_url(base_url, path)` helper 拼接 URL（strip 重复 `/ai` 前缀，同 `_efile_url` 模式），避免 `aiUrls` 值已含 `/ai` 后缀时产生 `/ai/ai/...` 重复路径
   - Header: `token: {clusterToken}`, `Content-Type: application/json`
   - Body: `{"id": "...", "customizePort": "1223", "command": "..."}`
4. **调用 API**：发送 POST 请求，超时 30s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `{aiUrls}/ai/openapi/v2/notebook/customize-service/actions/start`
- Method: POST
- Headers: `{"token": "{clusterToken}", "Content-Type": "application/json"}`
- Body (JSON):
  - `id` — notebook 实例 ID（必填）
  - `customizePort` — 自定义服务端口（必填）
  - `command` — 启动命令（可选）
- 超时: 30s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | object | 启动结果 |

### data 字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `execSuccess` | boolean | 端口检测结果（**不等于整体操作成功与否**，见下方返回值语义说明） |
| `errorMsg` | string | 提示信息。端口未检测到服务时包含系统自动生成的外部访问地址 |
| `output` | string/null | 执行命令的标准输出 |

### 返回值语义说明（重要）

本接口的返回值判断逻辑较为特殊：

1. **`code === "0"` 且 `data.execSuccess === true`**：端口检测成功，服务已启动并可访问。
2. **`code === "0"` 且 `data.execSuccess === false`**：端口检测未发现服务，但系统可能已自动建立网络通路。此时应检查 `data.errorMsg` —— 如果其中包含 URL（如 `https://c-xxx.ksai.scnet.cn:xxxxx`），则服务通路已建立，可调用 `notebook_query_custom_service_url` 获取正式访问地址。
3. **`code !== "0"`**：API 调用失败，需按错误码处理。

**调用方正确处理流程**：
```
result = notebook_start_custom_service(notebook_id, port, command)
if result.code == "0":
    # 无论 execSuccess 是 true 还是 false，都尝试查询访问地址
    url_result = notebook_query_custom_service_url(notebook_id)
    if url_result.data.status == "active":
        return url_result.data.url  # 服务可访问
```

## 工具关联关系

- **入参依赖**：
  - `notebook_id` ← `notebook_list` 的 `records[].id`
  - `customize_port` ← `notebook_detail` 的 `customizePort`（参考值）
  - `command` ← `notebook_detail` 的 `command`（参考值）
- **前置操作**：
  - 需先通过 `notebook_start` 确保实例处于 `Running` 状态
  - 可通过 `notebook_detail` 查看创建时配置的命令和端口
- **后续操作**：
  - 无论 `execSuccess` 值如何（只要 `code === "0"`），都应调用 `notebook_query_custom_service_url` 获取外部访问地址

## 异常处理

- **未认证**：返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **缺少必填参数**：返回 `{"error": true, "message": "notebook_id 和 customize_port 为必填参数"}`
- **API 返回错误**：返回 `{"error": true, "message": "启动自定义服务失败 [{code}]: {msg}"}`
- **网络异常**：返回 `{"error": true, "message": "启动自定义服务请求异常: {详情}"}`

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

### 端口检测失败但通路已建立（常见情况）

```json
{
    "code": "0",
    "msg": "success",
    "data": {
        "execSuccess": false,
        "errorMsg": "根据您提供的端口，未检测到可用的服务。若您的服务为特定的API接口，系统将自动建立通路，对应的外部地址为：https://c-1896476639463936002.ksai.scnet.cn:58043",
        "output": null
    }
}
```

### 端口检测成功

```json
{
    "code": "0",
    "msg": "success",
    "data": {
        "execSuccess": true,
        "errorMsg": null,
        "output": "Service started on port 1223\n"
    }
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表，name 为 `notebook_start_custom_service`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_start_custom_service` 函数。
