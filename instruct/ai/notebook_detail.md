# notebook_detail

## 需求

实现一个 MCP tool `notebook_detail`，查询指定 Notebook 实例的详细信息，包括状态、资源配置、镜像、SSH 连接信息等。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token
- 需要已知 `notebookId`，可通过 `notebook_list` 获取

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
| `cluster_id` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群（`isDefault=true`） |

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 aiUrls**：通过 `_get_default_token` 或指定 cluster_id 获取 token 和 aiUrls，采用 round-robin 策略
3. **构造请求**：
   - URL: `GET {aiUrl}/ai/openapi/v2/notebook/detail`
   - **实现注意**：需使用 `_ai_url(base_url, path)` helper 拼接 URL（strip 重复 `/ai` 前缀，同 `_efile_url` 模式），避免 `aiUrls` 值已含 `/ai` 后缀时产生 `/ai/ai/...` 重复路径
   - Header: `token: {clusterToken}`
   - Query params: `notebookId`
4. **调用 API**：发送 GET 请求，超时 15s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `{aiUrls}/ai/openapi/v2/notebook/detail`
- Method: GET
- Headers: `{"token": "{clusterToken}"}`
- Query params:
  - `notebookId` — notebook 实例 ID
- 超时: 15s

## 输出参数

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | object | Notebook 详情 |

### data 字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `id` | string | notebook 实例 ID |
| `notebookName` | string | notebook 实例名称 |
| `notebookStatus` | string | 实例状态：`Creating`/`Restarting`/`Running`/`Terminated`/`Failed`/`Shutting` |
| `imageName` | string | 镜像名称 |
| `imagePath` | string | 镜像地址 |
| `cpuNumber` | string | CPU 核心数 |
| `acceleratorType` | string | 加速器类型（如 dcu、gpu） |
| `acceleratorNumber` | integer | 加速器数量 |
| `resourceGroupCode` | string | 资源分组 code |
| `acceleratorMode` | string | 加速器型号 |
| `ramSize` | string | 内存大小 |
| `imageSize` | string | 镜像大小（byte） |
| `createTime` | string | 创建时间 |
| `releaseTime` | string | 释放时间（Terminated 状态时有值） |
| `updateTime` | string | 更新时间 |
| `progress` | number | 部署进度（Creating 状态时有值） |
| `startType` | string | 启动方式 |
| `sshPassword` | string | SSH 密码（Running 状态时有值） |
| `serviceIp` | string | 服务 IP（Running 状态时有值） |
| `sshUrl` | string | SSH 登录地址（Running 状态时有值） |
| `taskId` | string | 任务 ID（可用于查询任务状态） |
| `enoughResource` | boolean | 资源是否充足 |
| `containerId` | string | 容器 ID |
| `resourceGroupId` | string | 资源分组 ID |
| `maxNumber` | integer | 最大卡数 |
| `freeNumber` | integer | 空闲卡数 |
| `node` | string | 节点地址 |
| `command` | string | 自定义服务启动命令（仅配置了自定义服务时有值） |
| `customizePort` | string | 自定义服务监听端口（仅配置了自定义服务时有值） |
| `errorMessage` | string | 失败信息（仅 Failed 状态有值） |
| `message` | string | 失败描述信息（仅 Failed 状态有值） |

## 工具关联关系

- **入参依赖**：`notebook_id` 可从以下工具获取：
  - `notebook_list` — 返回列表中的 `id` 字段
  - `notebook_create` — 返回结果中的 `notebookId` 字段
- **被依赖**：本工具返回的以下字段可供其他工具使用：
  - `id` → 所有需要 `notebook_id` 的工具
  - `notebookStatus` → 判断实例是否可操作（Running 状态才可查询 URL）
  - `customizePort` → `notebook_start_custom_service` 的端口参考
  - `command` → `notebook_start_custom_service` 的命令参考

## 异常处理

- **未认证**：返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **缺少 notebook_id**：返回 `{"error": true, "message": "notebook_id 为必填参数"}`
- **API 返回错误**：`code` 非 `"0"` 时，返回 `{"error": true, "message": "查询 Notebook 详情失败 [{code}]: {msg}"}`
- **网络异常**：返回 `{"error": true, "message": "查询 Notebook 详情请求异常: {详情}"}`

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
        "id": "1896050410550136833",
        "notebookName": "2503021210191107",
        "notebookStatus": "Terminated",
        "imageName": "jupyterlab-simplesdxl-webui:pytorch2.1.0-py3.10-dtk24.04-model",
        "imagePath": "image.ac.com:5000/dcu/admin/base/jupyterlab-simplesdxl-webui:pytorch2.1.0-py3.10-dtk24.04-model",
        "cpuNumber": "24核心",
        "acceleratorType": "dcu",
        "acceleratorNumber": 1,
        "resourceGroupCode": "hgk100_ainormal93b0dc03",
        "acceleratorMode": "异构加速卡AI",
        "ramSize": "110GB",
        "imageSize": "43726906378",
        "createTime": "2025-03-02 12:10:20",
        "releaseTime": "28天20小时17分钟",
        "updateTime": "2025-03-02 12:10:51",
        "sshPassword": "9pM28jD7606121T",
        "serviceIp": "10.68.206.205",
        "enoughResource": true,
        "maxNumber": 8,
        "freeNumber": 5,
        "node": "10.13.1.15",
        "command": "python /root/SimpleSDXL/entry_with_update.py --listen 0.0.0.0 --port 1223 > /root/sd.log 2>&1",
        "customizePort": "1223"
    }
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表，name 为 `notebook_detail`
- document JSON 包含 url、method、description、parameters、returns

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `notebook_detail` 函数。
