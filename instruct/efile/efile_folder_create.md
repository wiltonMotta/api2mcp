# efile_folder_create

## 需求

实现一个 MCP tool `efile_folder_create`，在 HPC 集群文件系统上创建文件夹。

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
| `path` | string | 是 | - | `path` | query | 文件夹绝对路径 |
| `create_parents` | boolean | 否 | `false` | `createParents` | query | 父目录不存在时是否自动创建。序列化为 `"true"` / `"false"` |
| `clusterId` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群 |

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 efileUrls**：
   - 若未指定 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 获取默认集群（`isDefault=true`）的 token 和 efileUrls
   - 若指定了 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 查询指定集群的 token 和 efileUrls
   - efileUrls 为逗号分隔的多个 URL，采用 round-robin 策略选取一个可用 URL
3. **构造请求**：
   - URL: `POST {efileUrl}/efile/openapi/v2/file/mkdir`
   - Header: `token: {clusterToken}`
   - Query params: `path={path}&createParents={create_parents_str}`（`create_parents_str` 为 `"true"` / `"false"`）
   - Body: 空
4. **调用 API**：发送 POST 请求，超时 15s
5. **返回结果**：透传上游 API 响应 JSON

## API 调用

- URL: `{efileUrls}/efile/openapi/v2/file/mkdir`
- Method: POST
- Headers:
  - `token`: `{clusterToken}`
- Query params:
  - `path` — 文件夹绝对路径
  - `createParents` — `"true"` 或 `"false"`（boolean → 字符串）
- Body: 空
- 超时: 15s

## 输出参数

上游 API 响应透传。

### 顶层字段

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 提示信息 |
| `data` | string/null | 返回数据（成功时为空字符串） |

## 异常处理

- **未认证**：用户无 `user_cluster.token`，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **无默认集群**：`_get_default_token` 返回 error，直接透传
- **API 返回错误**：`code` 非 `"0"` 时，透传上游 `{code, msg, data}` 响应
- **网络异常**：返回 `{"error": true, "message": "创建文件夹请求异常: {详情}"}`

## 返回值示例

### 成功响应

```json
{
    "code": "0",
    "data": "",
    "msg": "success"
}
```

### 失败响应（上游透传）

```json
{
    "code": "911020",
    "msg": "目录不存在",
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
| `911020` | 父目录不存在（create_parents=false 时） |
| `911021` | 文件已存在 |
| `911022` | 目标地址不是一个文件夹 |
| `911030` | 权限不足 |
| `911501` | 存储空间不足 |

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `efile_folder_create`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `efile_folder_create` 函数。
