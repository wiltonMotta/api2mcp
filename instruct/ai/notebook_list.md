# notebook_list

## 需求

实现一个 MCP tool `notebook_list`，查询当前用户创建的 Notebook 实例列表，支持按名称、状态筛选和分页。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token

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
- **URL 路由说明**：列表查询直连集群 `{aiUrls}` 以获得更低延迟。创建/开机等调度操作由 AC 平台统一管理。

## 字段命名策略

- **输出透传**：MCP 工具直接透传上游 API 的 JSON 响应，字段名保持 camelCase，不做 snake_case 映射
- **输入映射**：MCP 参数使用 snake_case，在构造请求时映射为 OpenAPI 所需的 camelCase

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `notebook_name` | string | 否 | `None` | `notebookName` | query | notebook 实例名称，支持模糊匹配 |
| `notebook_status` | string | 否 | `None` | `notebookStatus` | query | Notebook 状态：`Creating`（创建中）、`Restarting`（开机中）、`Running`（运行中）、`Terminated`（已关机）、`Failed`（失败）、`Shutting`（关机中） |
| `page` | integer | 否 | `1` | `page` | query | 分页页码，默认 1 |
| `size` | integer | 否 | `20` | `size` | query | 分页大小，默认 20 |
| `cluster_id` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群（`isDefault=true`）。需从 `hpc_hpc_list_available_partitions` 或 `user_cluster` 表获取 |

注：MCP 参数 `notebook_name` 映射到 OpenAPI 的 `notebookName`（camelCase）；`notebook_status` 映射到 `notebookStatus`。

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 aiUrls**：
   - 若未指定 `cluster_id`，通过 `_get_default_token(username)` 获取默认集群的 token 和 aiUrls
   - 若指定了 `cluster_id`，查询指定集群的 token 和 aiUrls
   - aiUrls 为逗号分隔的多个 URL，采用 round-robin 策略选取一个可用 URL
3. **构造请求**：
   - URL: `GET {aiUrl}/ai/openapi/v2/notebook/list`
   - **实现注意**：需使用 `_ai_url(base_url, path)` helper 拼接 URL（strip 重复 `/ai` 前缀，同 `_efile_url` 模式），避免 `aiUrls` 值已含 `/ai` 后缀时产生 `/ai/ai/...` 重复路径
   - Header: `token: {clusterToken}`
   - Query params: `notebookName`, `notebookStatus`, `page`, `size`（仅传递非空参数）
4. **调用 API**：发送 GET 请求，超时 15s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `{aiUrls}/ai/openapi/v2/notebook/list`
- Method: GET
- Headers: `{"token": "{clusterToken}"}`
- Query params:
  - `notebookName` — notebook 实例名称（可选）
  - `notebookStatus` — 状态筛选（可选）
  - `page` — 分页页码
  - `size` — 分页大小
- 超时: 15s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | object | 返回数据 |

### data 字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `total` | integer | notebook 总数 |
| `records` | array | notebook 实例列表 |

### records 数组元素（主要字段）

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `id` | string | notebook 实例 ID（可作为其他 notebook 工具的 `notebook_id` 入参） |
| `notebookName` | string | notebook 实例名称 |
| `notebookStatus` | string | 实例状态 |
| `imageName` | string | 镜像名称 |
| `imagePath` | string | 镜像地址 |
| `cpuNumber` | string | CPU 核心数 |
| `acceleratorType` | string | 加速器类型 |
| `acceleratorNumber` | integer | 加速器数量 |
| `resourceGroupCode` | string | 资源分组 code |
| `ramSize` | string | 内存大小 |
| `imageSize` | string | 镜像大小（byte） |
| `createTime` | string | 创建时间 |
| `updateTime` | string | 更新时间 |
| `sshPassword` | string | SSH 密码（仅在 Running 状态有值） |
| `serviceIp` | string | 服务 IP |
| `taskId` | string | 任务 ID |
| `node` | string | 节点 |
| `command` | string | 自定义服务启动命令（仅配置了自定义服务时有值） |
| `customizePort` | string | 自定义服务监听端口（仅配置了自定义服务时有值） |
| `errorMessage` | string | 失败信息（仅 Failed 状态有值） |
| `message` | string | 失败描述信息（仅 Failed 状态有值） |

## 工具关联关系

- `notebook_list` → `notebook_detail`：`records[].id` 作为 `notebook_detail` 的 `notebook_id` 入参
- `notebook_list` → `notebook_start`：`records[].id` 作为 `notebook_start` 的 `notebook_id` 入参
- `notebook_list` → `notebook_stop`：`records[].id` 作为 `notebook_stop` 的 `notebook_id` 入参
- `notebook_list` → `notebook_release`：`records[].id` 作为 `notebook_release` 的 `notebook_id` 入参
- `notebook_list` → `notebook_rename`：`records[].id` 作为 `notebook_rename` 的 `notebook_id` 入参
- `notebook_list` → `notebook_query_jupyter_url`：`records[].id` 作为 `notebook_query_jupyter_url` 的 `notebook_id` 入参
- `notebook_list` → `notebook_query_custom_service_url`：`records[].id` 作为 `notebook_query_custom_service_url` 的 `notebook_id` 入参
- `notebook_list` → `notebook_start_custom_service`：`records[].id` 作为 `notebook_start_custom_service` 的 `notebook_id` 入参，`records[].customizePort` 作为其 `customize_port` 参考值

## 异常处理

- **未认证**：用户无 `user_cluster.token`，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **无默认集群**：`_get_default_token` 返回 error，直接透传
- **API 返回错误**：`code` 非 `"0"` 时，返回 `{"error": true, "message": "查询 Notebook 列表失败 [{code}]: {msg}"}`
- **网络异常**：捕获 HTTP 异常，返回 `{"error": true, "message": "查询 Notebook 列表请求异常: {详情}"}`

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
        "total": 1,
        "records": [
            {
                "id": "2045437353363222530",
                "notebookName": "2604181740426384",
                "notebookStatus": "Failed",
                "imageName": "jupyterlab-pytorch:2.5.1-ubuntu22.04-dtk25.04.2-py3.10-devel",
                "imagePath": "image.ac.com:5000/dcu/admin/base/jupyterlab-pytorch:2.5.1-ubuntu22.04-dtk25.04.2-py3.10-devel",
                "cpuNumber": "15核心",
                "acceleratorType": "dcu",
                "acceleratorNumber": 1,
                "resourceGroupCode": "hgk100_ainormal93b0dc03",
                "ramSize": "120GB",
                "createTime": "2026-04-18 17:40:43",
                "updateTime": "2026-04-18 17:41:58"
            }
        ]
    }
}
```

### 认证失败

```json
{
    "error": true,
    "message": "未找到认证信息，请先访问 /auth/{username} 完成 AK/SK 认证",
    "auth_url": "https://www.scnet.cn/ac/openapi/v2/mcp/auth/{username}"
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `notebook_list`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_list` 函数。
