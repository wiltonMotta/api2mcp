# notebook_create

## 需求

实现一个 MCP tool `notebook_create`，创建 Notebook 容器实例，支持指定镜像、加速器类型/数量、资源分组、挂载目录和启动命令。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `users` 表中存在有效的 `acToken`
- 建议先调用 `notebook_list_images` 获取可用镜像列表（获取 `image_path`、`image_name`、`image_size`）
- 建议先调用 `notebook_list_resources` 获取可用资源信息（获取 `cluster_id`、`resource_group_code`、`accelerator_type`）

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- **Token 类型**：本接口使用平台级 AC URL（`www.scnet.cn/ac/openapi/v2/...`），因此使用 `users.acToken` 而非集群 token。参考 `list_history_jobs`（main.py）中 AC URL 的认证模式。
- acToken 获取方式：
  ```sql
  SELECT acToken FROM users WHERE userName = {current_username}
  ```
- 如果用户未认证（`acToken` 为 NULL），返回错误提示 JSON，包含 `auth_url` 字段
- **URL 路由说明**：创建操作由 AC 平台统一调度，因此使用平台级静态 URL 而非集群级 `{aiUrls}`。后续的状态变更操作（stop/release/rename）直连集群 `{aiUrls}` 以获得更低延迟。

## 字段命名策略

- **输出透传**：MCP 工具直接透传上游 API 的 JSON 响应，字段名保持 camelCase（如 `taskId`、`notebookId`），不做 snake_case 映射
- **输入映射**：MCP 参数使用 snake_case，在构造请求时映射为 OpenAPI 所需的 camelCase

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `cluster_id` | string | 是 | — | `clusterId` | body | 区域 ID。可从 `notebook_list_resources` 获取 |
| `image_path` | string | 是 | — | `imagePath` | body | 镜像地址。可从 `notebook_list_images` 的 `path` 字段获取 |
| `image_name` | string | 是 | — | `imageName` | body | 镜像名称。可从 `notebook_list_images` 的 `name`:`tag` 组合获取 |
| `image_size` | string | 是 | — | `imageSize` | body | 镜像大小（byte）。可从 `notebook_list_images` 的 `image_size` 字段获取 |
| `accelerator_type` | string | 是 | — | `acceleratorType` | body | 加速器类型（如 DCU、GPU）。可从 `notebook_list_resources` 获取 |
| `accelerator_number` | string | 是 | — | `acceleratorNumber` | body | 加速器数量 |
| `resource_group_code` | string | 否 | `None` | `resourceGroupCode` | body | 资源分组 code。可从 `notebook_list_resources` 的 `resource_group_code` 获取 |
| `mount_home` | boolean | 否 | `True` | `mountHome` | body | 是否挂载用户主目录 |
| `start_command` | string | 否 | `None` | `startCommand` | body | 启动容器时执行的命令 |
| `mount_info` | array | 否 | `None` | `mountInfo` | body | 自定义挂载信息列表 |
| `mount_info[].source_path` | string | 是 | — | `sourcePath` | body | 挂载源路径（用户主目录下路径） |
| `mount_info[].target_path` | string | 是 | — | `targetPath` | body | 挂载目标路径 |
| `mount_info[].permission` | string | 是 | — | `permission` | body | 权限：`ro`（只读）、`rw`（读写），默认 `ro` |

注：由于创建 Notebook 使用平台级静态 URL（非 aiUrls），不需要 round-robin URL 选择。token 使用 `users.acToken`（平台级 token，非集群 token）。

## 后端处理逻辑

1. **认证校验**：从 `users` 表查询 `acToken`，若为 NULL 则返回认证错误
2. **获取 token**：使用 `users.acToken` 作为 HTTP header `token`（AC URL 使用 acToken，非集群 token）
3. **构造请求**：
   - URL: `POST https://www.scnet.cn/ac/openapi/v2/notebook/actions/create`
   - Header: `token: {acToken}`, `Content-Type: application/json`
   - Body: JSON 格式的创建参数（`clusterId`, `imagePath`, `imageName`, `imageSize`, `acceleratorType`, `acceleratorNumber` 为必填）
   - `mountInfo` 以 JSON 数组传入，每个元素包含 `sourcePath`、`targetPath`、`permission` 三个字段
4. **调用 API**：发送 POST 请求，超时 30s（创建操作耗时较长）
5. **返回结果**：返回 API 响应 JSON，包含 `taskId` 和 `notebookId`
6. **异步跟踪**：创建是异步操作，返回的 `taskId` 可用于跟踪创建进度。当前暂无独立的异步任务查询工具，建议通过 `notebook_list` 轮询实例状态直至不再是 `Creating`。

## API 调用

- URL: `https://www.scnet.cn/ac/openapi/v2/notebook/actions/create`
- Method: POST
- Headers: `{"token": "{acToken}", "Content-Type": "application/json"}`
- Body (JSON):
  - `clusterId` — 区域 ID（必填）
  - `imagePath` — 镜像地址（必填）
  - `imageName` — 镜像名称（必填）
  - `imageSize` — 镜像大小（必填）
  - `acceleratorType` — 加速器类型（必填）
  - `acceleratorNumber` — 加速器数量（必填）
  - `resourceGroupCode` — 资源分组 code（可选）
  - `mountHome` — 是否挂载主目录（可选）
  - `startCommand` — 启动命令（可选）
  - `mountInfo` — 自定义挂载信息（可选）
- 超时: 30s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | object | 创建结果 |

### data 字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `taskId` | string | 异步任务 ID。创建是异步操作，可通过 `notebook_list` 轮询实例状态跟踪进度 |
| `notebookId` | string | 新创建的 Notebook 实例 ID（可用于后续 `notebook_detail`、`notebook_start` 等操作） |

## 工具关联关系

- **入参依赖**：
  - `image_path`、`image_name`、`image_size` ← `notebook_list_images` 返回的镜像信息
  - `cluster_id`、`resource_group_code`、`accelerator_type` ← `notebook_list_resources` 返回的资源信息
- **被依赖**：本工具返回的 `notebook_id` 可用于：
  - `notebook_detail` — 查询实例详情
  - `notebook_start` — 开机
  - `notebook_stop` — 关机
  - `notebook_rename` — 重命名
  - `notebook_release` — 释放

## 异常处理

- **未认证**：`acToken` 为 NULL，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **缺少必填参数**：返回 `{"error": true, "message": "缺少必填参数: {参数名}"}`
- **API 返回错误**：返回 `{"error": true, "message": "创建 Notebook 失败 [{code}]: {msg}"}`
- **网络异常**：返回 `{"error": true, "message": "创建 Notebook 请求异常: {详情}"}`

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
    "data": {
        "taskId": "1821067185941626882",
        "notebookId": "1821067171420946434"
    }
}
```

### mountInfo 参数示例

```json
{
    "mount_info": [
        {
            "source_path": "/public/home/test/data",
            "target_path": "/root/test",
            "permission": "rw"
        }
    ]
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表，name 为 `notebook_create`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_create` 函数。
