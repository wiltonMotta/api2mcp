# efile_delete

## 需求

实现一个 MCP tool `efile_delete`，删除 HPC 集群文件系统上的文件或文件夹。支持批量删除（多个路径用逗号分隔）。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- efileUrls 获取方式：
  ```sql
  SELECT u.efileUrls
  FROM user_cluster c, cluster_url u
  WHERE u.clusterId = c.clusterId
    AND c.isDefault = true
    AND c.username = {current_username}
  ```
- 如果用户未认证或无默认集群 token，返回错误提示 JSON，包含 `auth_url` 字段

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `paths` | string | 是 | - | `paths` | query | 删除文件的绝对路径，多个路径用英文逗号分隔 |
| `recursive` | boolean | 否 | `false` | `recursive` | query | 是否递归删除。`true` 可删除非空文件夹，序列化为 `"true"` / `"false"` |
| `clusterId` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群 |

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 efileUrls**：
   - 若未指定 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 获取默认集群（`isDefault=true`）的 token 和 efileUrls
   - 若指定了 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 查询指定集群的 token 和 efileUrls
   - efileUrls 为逗号分隔的多个 URL，采用 round-robin 策略选取一个可用 URL
3. **构造请求**：
   - URL: `POST {efileUrl}/efile/openapi/v2/file/remove`
   - Header: `token: {clusterToken}`
   - Query params: `paths={paths}&recursive={recursive_str}`（`recursive_str` 为 `"true"` / `"false"`）
   - Body: 空
4. **调用 API**：发送 POST 请求，超时 30s（大文件夹递归删除可能较慢）
5. **返回结果**：透传上游 API 响应 JSON

## API 调用

- URL: `{efileUrls}/efile/openapi/v2/file/remove`
- Method: POST
- Headers:
  - `token`: `{clusterToken}`
- Query params:
  - `paths` — 要删除的文件/文件夹绝对路径（多个用逗号分隔）
  - `recursive` — `"true"`（递归删除）或 `"false"`（仅删除空文件夹）
- Body: 空
- 超时: 30s

## 输出参数

上游 API 响应透传。

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | string/null | 返回数据（成功时为空字符串） |

> 注：上游 API 成功时 `data` 返回空字符串 `""`，非 null。

## 异常处理

- **未认证**：用户无 `user_cluster.token`，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **无默认集群**：`_get_default_token` 返回 error，直接透传
- **API 返回错误**：`code` 非 `"0"` 时，透传上游 `{code, msg, data}` 响应
- **网络异常**：返回 `{"error": true, "message": "删除文件/文件夹请求异常: {详情}"}`

## 返回值示例

### 成功响应

```json
{
    "code": "0",
    "data": "",
    "msg": "success"
}
```

### 失败响应（上游透传 — 目录非空）

```json
{
    "code": "911502",
    "msg": "目录非空，操作失败",
    "data": null
}
```

常见错误码：

| 错误码 | 说明 |
|--------|------|
| `0` | 成功 |
| `10001` | 内部异常 |
| `10003` | 参数不全 |
| `10004` | 参数无效 |
| `911009` | 区域用户不存在 |
| `911020` | 文件不存在 |
| `911030` | 权限不足 |
| `911502` | 目录非空（recursive=false 时目录非空） |

## 边界条件

- 删除不存在的文件：上游返回 `911020`（文件不存在）
- 删除非空文件夹且 `recursive=false`：上游返回 `911502`（目录非空）
- 批量删除中部分文件失败：上游在 msg 中描述具体失败项

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `efile_delete`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `efile_delete` 函数。
