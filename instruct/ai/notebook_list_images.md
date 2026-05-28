# notebook_list_images

## 需求

实现一个 MCP tool `notebook_list_images`，查询可用的 Notebook 镜像列表，支持按名称、类型、加速器类型等条件筛选和分页排序。

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

## 字段命名策略

- **输出透传**：MCP 工具直接透传上游 API 的 JSON 响应，字段名保持 camelCase，不做 snake_case 映射
- **输入映射**：MCP 参数使用 snake_case，在构造请求时映射为 OpenAPI 所需的 camelCase

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `name` | string | 否 | `None` | `name` | body | 镜像名称（模糊匹配，含版本号） |
| `access` | string | 否 | `"public"` | `access` | body | 镜像权限：`public`（公开）、`private`（私有）。默认 `"public"` |
| `type` | string | 否 | `None` | `type` | body | 镜像类型：`JupyterLab`、`CodeServer`、`RStudio`、`Base` 等 |
| `order_by` | string | 否 | `"create_time"` | `orderBy` | body | 排序字段：`create_time`（创建时间）、`share_time`（发布时间）、`clone_times`（克隆次数） |
| `sort` | string | 否 | `"DESC"` | `sort` | body | 排序方式：`ASC`（升序）、`DESC`（降序） |
| `start` | integer | 否 | `0` | `start` | body | 分页起始条数（从 0 开始） |
| `limit` | integer | 否 | `20` | `limit` | body | 每页数量 |
| `accelerator_type` | string | 否 | `None` | `acceleratorType` | body | 加速器类型（如 dcu、gpu）。可从 `notebook_list_resources` 获取 |
| `cluster_id` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群 |

注：MCP 参数 `order_by` 映射到 `orderBy`；`accelerator_type` 映射到 `acceleratorType`。所有参数均通过 JSON body 传递。

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 aiUrls**：通过 `_get_default_token` 或指定 cluster_id 获取 token 和 aiUrls，采用 round-robin 策略
3. **构造请求**：
   - URL: `POST {aiUrl}/ai/openapi/v2/image/images`
   - **实现注意**：需使用 `_ai_url(base_url, path)` helper 拼接 URL（strip 重复 `/ai` 前缀，同 `_efile_url` 模式），避免 `aiUrls` 值已含 `/ai` 后缀时产生 `/ai/ai/...` 重复路径
   - Header: `token: {clusterToken}`, `Content-Type: application/json`
   - Body: JSON 格式的筛选/分页参数（仅传递非空参数）
4. **调用 API**：发送 POST 请求，超时 15s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `{aiUrls}/ai/openapi/v2/image/images`
- Method: POST
- Headers: `{"token": "{clusterToken}", "Content-Type": "application/json"}`
- Body (JSON):
  - `name` — 镜像名称（可选）
  - `access` — 权限 public/private（可选，默认 public）
  - `type` — 镜像类型（可选）
  - `orderBy` — 排序字段（可选，默认 create_time）
  - `sort` — ASC/DESC（可选，默认 DESC）
  - `start` — 起始条数（可选，默认 0）
  - `limit` — 每页数量（可选，默认 20）
  - `acceleratorType` — 加速器类型（可选）
- 超时: 15s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | object | 返回数据，包含 `total` 和 `data`（镜像数组） |

### data 字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `total` | integer | 镜像总数量 |
| `data` | array | 镜像列表（**注意：嵌套在 `data` 对象内的 `data` 数组，即 `response.data.data`**） |

### data 数组元素（与 notebook_create 相关的字段）

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `id` | string | 镜像 ID |
| `name` | string | 镜像名 |
| `tag` | string | 标签/版本号 |
| `path` | string | 镜像完整路径（可作为 `notebook_create` 的 `image_path` 入参） |
| `version` | string | 镜像版本（格式：`name:tag`，可作为 `notebook_create` 的 `image_name` 参考） |
| `imageSize` | string | 镜像大小（byte，可作为 `notebook_create` 的 `image_size` 入参） |
| `type` | string | 镜像类型（JupyterLab/CodeServer/RStudio/Base 等） |
| `access` | string | 权限（public/private） |
| `acceleratorType` | string | 加速器类型 |
| `status` | string | 镜像状态 |
| `description` | string | 镜像描述 |
| `createTime` | string | 创建时间 |
| `user` | string | 上传用户 |
| `cloneTimes` | integer | 克隆次数 |

## 工具关联关系

- **被依赖**：本工具返回的镜像信息供 `notebook_create` 使用：
  - `path` → `notebook_create` 的 `image_path`
  - `name`:`tag` → `notebook_create` 的 `image_name`
  - `imageSize` → `notebook_create` 的 `image_size`
- **协同调用**：配合 `notebook_list_resources` 使用，获取创建 notebook 所需的完整参数

## 异常处理

- **未认证**：返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **API 返回错误**：返回 `{"error": true, "message": "查询镜像列表失败 [{code}]: {msg}"}`
- **网络异常**：返回 `{"error": true, "message": "查询镜像列表请求异常: {详情}"}`

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
        "data": [
            {
                "id": "5e23ad61364e4b49bfcb1b30ff0a49d7",
                "name": "custom",
                "tag": "vllm0.7.2-ubuntu22.04-dtk25.04-rc6-das1.4-py3.10-20250227-rocblas-86461",
                "path": "image.ac.com:5000/dcu/admin/base/custom:vllm0.7.2-ubuntu22.04-dtk25.04-rc6-das1.4-py3.10-20250227-rocblas-86461",
                "version": "custom:vllm0.7.2-ubuntu22.04-dtk25.04-rc6-das1.4-py3.10-20250227-rocblas-86461",
                "imageSize": "6445336813",
                "type": "Base",
                "access": "public",
                "acceleratorType": "dcu",
                "status": "Completed",
                "cloneTimes": 1
            }
        ]
    }
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表，name 为 `notebook_list_images`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_list_images` 函数。
