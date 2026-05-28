# efile_check_permission

## 需求

实现一个 MCP tool `efile_check_permission`，校验当前用户对指定文件是否具有读、写或执行权限。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `user_cluster` 表读取 `isDefault=true` 的集群 `token` 和 `efileUrls`
- 如果用户未认证或无默认集群 token，返回错误提示 JSON，包含 `auth_url` 字段

## MCP 工具参数（用户传入）与 OpenAPI 参数映射

| MCP 参数 | 类型 | 必填 | 默认值 | OpenAPI 参数 | 参数位置 | 说明 |
|----------|------|------|--------|-------------|---------|------|
| `path` | string | 是 | - | `path` | body (form-urlencoded) | 所校验文件的绝对路径 |
| `permission_action` | string | 是 | - | `permissionAction` | body (form-urlencoded) | 权限类型：`READ`（读）、`WRITE`（写）、`EXECUTE`（执行） |
| `clusterId` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群（`isDefault=true`） |

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 efileUrls**：
   - 若未指定 `clusterId`，调用 `_get_default_token(username)` 获取默认集群的 token、efileUrls
   - 若指定了 `clusterId`，从 `user_cluster` + `cluster_url` 表中直接查询
3. **参数校验**：确认 `permission_action` 取值为 `READ`、`WRITE` 或 `EXECUTE` 之一
4. **构造请求**：
   - URL: `POST {efileUrl}/efile/openapi/v2/file/permission`
   - Header: `token: {clusterToken}`
   - Content-Type: `application/x-www-form-urlencoded`
   - Body: `path={path}&permissionAction={permission_action}`
5. **调用 API**：发送 POST 请求，超时 15s
6. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `{efileUrls}/efile/openapi/v2/file/permission`
- Method: POST
- Headers:
  - `token`: `{clusterToken}`
  - `Content-Type`: `application/x-www-form-urlencoded`
- Body (form-urlencoded):
  - `path` — 所校验文件的绝对路径
  - `permissionAction` — `READ` / `WRITE` / `EXECUTE`
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
| `allowed` | boolean | `true` 表示允许该操作，`false` 表示不允许 |

## 异常处理

- **未认证**：用户无 `user_cluster.token`，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **无默认集群**：`_get_default_token` 返回 error，直接透传
- **无效 permission_action**：返回 `{"error": true, "message": "无效的权限类型: {permission_action}，有效值为 READ/WRITE/EXECUTE"}`
- **API 返回错误**：`code` 非 `"0"` 时，透传上游 `{code, msg, data}` 响应
- **网络异常**：返回 `{"error": true, "message": "权限校验请求异常: {详情}"}`

## 返回值示例

### 成功响应（有权限）

```json
{
    "code": "0",
    "data": {
        "allowed": true
    },
    "msg": "success"
}
```

### 成功响应（无权限）

```json
{
    "code": "0",
    "data": {
        "allowed": false
    },
    "msg": "success"
}
```

### 失败响应（上游透传）

```json
{
    "code": "911030",
    "msg": "权限不足，禁止操作",
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

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `efile_check_permission`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `efile_check_permission` 函数。
