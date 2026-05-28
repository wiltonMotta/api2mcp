# notebook_list_resources

## 需求

实现一个 MCP tool `notebook_list_resources`，查询可用的 Notebook 计算资源（加速器）信息，包括 GPU/DCU 型号、可用卡数、资源分组等。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `users` 表中存在有效的 `acToken`
- 需要已知 `clusterId`，可通过 `list_available_partitions` 获取

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- **Token 类型**：本接口使用平台级 AC URL（`www.scnet.cn/ac/openapi/v2/...`），因此使用 `users.acToken` 而非集群 token。参考 `list_history_jobs`（main.py）中 AC URL 的认证模式。
- acToken 获取方式：
  ```sql
  SELECT acToken FROM users WHERE userName = {current_username}
  ```
- 如果用户未认证（`acToken` 为 NULL），返回错误提示 JSON，包含 `auth_url` 字段
- **URL 路由说明**：资源查询由 AC 平台统一提供，使用平台级静态 URL。

## 字段命名策略

- **输出透传**：MCP 工具直接透传上游 API 的 JSON 响应，字段名保持 camelCase（如 `resourceGroupCode`、`clusterId`）
- **输入映射**：MCP 参数使用 snake_case，在构造请求时映射为 OpenAPI 所需的 camelCase

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `cluster_ids` | string | 是 | — | `clusterIds` | query | 区域 ID 列表（逗号分隔，如 `"11250,20057"`）。可从 `list_available_partitions` 获取 |
| `resource_id` | string | 否 | `None` | `resourceId` | query | 资源 ID，用于筛选特定型号。可从本工具的上次调用结果中获取 |
| `cluster_id` | integer | 否 | `None` | — | — | 集群 ID（仅用于获取 acToken 时的上下文参考）。为空时使用默认集群 |

注：MCP 参数 `cluster_ids` 接受逗号分隔的字符串，直接作为 query param 传递；`resource_id` 映射到 `resourceId`。

## 后端处理逻辑

1. **认证校验**：从 `users` 表查询 `acToken`，若为 NULL 则返回认证错误
2. **获取 token**：使用 `users.acToken` 作为 HTTP header `token`（AC URL 使用 acToken，非集群 token）
3. **构造请求**：
   - URL: `GET https://www.scnet.cn/ac/openapi/v2/resources/accelerators`
   - Header: `token: {acToken}`
   - Query params: `clusterIds`（逗号分隔）、`resourceId`（可选）
4. **调用 API**：发送 GET 请求，超时 15s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `https://www.scnet.cn/ac/openapi/v2/resources/accelerators`
- Method: GET
- Headers: `{"token": "{acToken}"}`
- Query params:
  - `clusterIds` — 区域 ID（必填，多值逗号分隔，如 `11250,20057`）
  - `resourceId` — 资源 ID（可选，用于筛选特定型号）
- 超时: 15s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | array | 资源列表（**注意：本接口 `data` 直接为数组类型，非 object 包装**） |

### data 数组元素

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `id` | string | 资源记录 ID |
| `resourceGroupCode` | string | 资源分组 code（可作为 `notebook_create` 的 `resource_group_code` 入参） |
| `resourceGroupName` | string | 资源分组名称 |
| `clusterId` | integer | 区域 ID（可作为 `notebook_create` 的 `cluster_id` 入参） |
| `clusterName` | string | 区域名称 |
| `maxNum` | integer | 单节点最大卡数 |
| `maxFreeNum` | integer | 单节点最大空闲卡数 |
| `cpuNumber` | string | CPU 核心数 |
| `ramSize` | string | 内存大小 |
| `resourceId` | string | 资源 ID |
| `resourceType` | string | 资源类型（GPU/DCU） |
| `resourceName` | string | 资源名称（含型号、显存等信息） |
| `resourceSpec` | string | 资源详情（JSON 字符串，含 memory、cpuCore、gpuModel 等） |

## 工具关联关系

- **入参依赖**：
  - `cluster_ids` ← `list_available_partitions` 的 `clusterId`
- **被依赖**：本工具返回的资源信息供 `notebook_create` 使用：
  - `clusterId` → `notebook_create` 的 `cluster_id`
  - `resourceGroupCode` → `notebook_create` 的 `resource_group_code`
  - `resourceType` → `notebook_create` 的 `accelerator_type`（参考值）
- **协同调用**：配合 `notebook_list_images` 或 `notebook_list_model_images` 使用，完成 notebook 创建所需的全部参数

## 异常处理

- **未认证**：`acToken` 为 NULL，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **缺少 cluster_ids**：返回 `{"error": true, "message": "cluster_ids 为必填参数"}`
- **API 返回错误**：返回 `{"error": true, "message": "查询资源信息失败 [{code}]: {msg}"}`
- **网络异常**：返回 `{"error": true, "message": "查询资源信息请求异常: {详情}"}`

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
    "data": [
        {
            "id": "12",
            "resourceGroupCode": "nvl20normalc1691523",
            "resourceGroupName": "nvl20normal",
            "clusterId": 11250,
            "clusterName": "华东一区【昆山】",
            "maxNum": 8,
            "maxFreeNum": 0,
            "cpuNumber": "10核",
            "ramSize": "220GB",
            "resourceId": "Intel8458P-88C-2T-8*L20",
            "resourceType": "GPU",
            "resourceName": "NVIDIA L20 显存48GB PCIE",
            "resourceSpec": "{\"memory\": \"2048GB\", \"cpuCore\": \"88核\", \"gpuModel\": \"L20\", \"gpuMemory\": \"48GB\"}"
        }
    ]
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表，name 为 `notebook_list_resources`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_list_resources` 函数。
