# efile_exist

## 需求

实现一个 MCP tool `efile_exist`，判断指定的文件或文件夹是否存在于 HPC 集群文件系统中。

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

## MCP 工具参数（用户传入）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `path` | string | 是 | - | 文件/文件夹的绝对路径 |
| `clusterId` | integer | 否 | `None` | 集群 ID。为空时使用默认集群（`isDefault=true`） |

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 efileUrls**：
   - 若未指定 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 获取默认集群（`isDefault=true`）的 token 和 efileUrls
   - 若指定了 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 查询指定集群的 token 和 efileUrls
   - efileUrls 为逗号分隔的多个 URL，采用 round-robin 策略选取一个可用 URL
3. **构造请求**：
   - URL: `POST {efileUrl}/efile/openapi/v2/file/exist`
   - Header: `token: {clusterToken}`
   - Content-Type: `application/x-www-form-urlencoded`
   - Body: `path={path}`
4. **调用 API**：发送 POST 请求，超时 15s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `{efileUrls}/efile/openapi/v2/file/exist`
- Method: POST
- Headers:
  - `token`: `{clusterToken}`
  - `Content-Type`: `application/x-www-form-urlencoded`
- Body (form-urlencoded):
  - `path` — 文件/文件夹绝对路径
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
| `exist` | boolean | `true` 表示文件/文件夹存在，`false` 表示不存在 |

## 异常处理

- **未认证**：用户无 `user_cluster.token`，返回错误提示，包含 `auth_url` 字段
- **无默认集群**：`_get_default_token` 返回 error，直接透传
- **API 返回错误**：`code` 非 `"0"` 时，返回 `{"error": true, "message": "判断文件是否存在失败 [{code}]: {msg}"}`
- **网络异常**：捕获 HTTP 异常，返回 `{"error": true, "message": "判断文件是否存在请求异常: {详情}"}`

## 返回值示例

### 成功响应（文件存在）

```json
{
    "code": "0",
    "data": {
        "exist": true
    },
    "msg": "success"
}
```

### 成功响应（文件不存在）

```json
{
    "code": "0",
    "data": {
        "exist": false
    },
    "msg": "success"
}
```

### 失败响应

```json
{
    "code": "10003",
    "msg": "参数不全",
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
| `911404` | AC 认证服务端连接异常 |
| `911406` | 服务端 token 认证异常 |

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `efile_exist`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `efile_exist` 函数。
