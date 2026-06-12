# efile_list_files

## 需求

实现一个 MCP tool `efile_list_files`，查询 HPC 集群上用户文件目录中的文件列表，支持搜索、排序和分页。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token
- 需先调用 `hpc_hpc_list_available_partitions` 或从 `user_cluster` 表获取 efileUrls

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
| `path` | string | 否 | `""` | `path` | query | 目标文件夹路径（必须为绝对路径）。为空时默认为用户家目录 |
| `keyword` | string | 否 | `""` | `keyWord` | query | 搜索关键字，模糊匹配文件/文件夹名称 |
| `order` | string | 否 | `"asc"` | `order` | query | 排序方式：`asc`（升序）或 `desc`（降序） |
| `order_by` | string | 否 | `"name"` | `orderBy` | query | 排序字段：`name`（文件名）、`size`（文件大小）、`lastModifiedTime`（修改时间） |
| `start` | integer | 否 | `0` | `start` | query | 起始索引位置（从 0 开始） |
| `limit` | integer | 否 | `10` | `limit` | query | 每页返回条数（最大 1000） |
| `clusterId` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群（`isDefault=true`） |

注：MCP 参数 `keyword` 映射到 OpenAPI 的 `keyWord`（camelCase）；`order_by` 映射到 `orderBy`。

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 efileUrls**：
   - 若未指定 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 获取默认集群（`isDefault=true`）的 token 和 efileUrls
   - 若指定了 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 查询指定集群的 token 和 efileUrls
   - efileUrls 为逗号分隔的多个 URL，采用 round-robin 策略选取一个可用 URL
3. **构造请求**：
   - URL: `GET {efileUrl}/efile/openapi/v2/file/list`
   - Header: `token: {clusterToken}`
   - Query params: `limit`, `order`, `orderBy`, `path`, `start`, `keyWord`
4. **调用 API**：发送 GET 请求，超时 15s
5. **返回结果**：返回 API 响应 JSON，映射为 snake_case 字段名

## API 调用

- URL: `{efileUrls}/efile/openapi/v2/file/list`
- Method: GET
- Headers: `{"token": "{clusterToken}"}`
- Query params:
  - `path` — 目标文件夹路径
  - `keyword` — 搜索关键字
  - `order` — asc/desc
  - `orderBy` — name/size/lastModifiedTime
  - `start` — 起始索引
  - `limit` — 每页条数
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
| `total` | integer | 文件总条目数 |
| `path` | string | 当前文件夹路径 |
| `keyword` | string | 搜索关键词（仅搜索时有值） |
| `files` | array | 文件列表 |

### files 数组元素

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `id` | string | 文件 ID |
| `name` | string | 文件名 |
| `path` | string | 文件完整路径 |
| `size` | integer | 文件大小（字节） |
| `is_directory` | boolean | 是否为文件夹 |
| `is_regular_file` | boolean | 是否为常规文件 |
| `is_symbolic_link` | boolean | 是否为符号链接 |
| `is_share` | boolean | 是否已分享 |
| `is_other` | boolean | 是否其他类型 |
| `share_enabled` | boolean | 分享功能是否开启 |
| `owner` | string | 文件所有者用户名 |
| `group` | string | 文件所属分组 |
| `permission` | string | 文件权限字符串（如 `rwxrw-r--`） |
| `creation_time` | string | 创建时间 |
| `last_modified_time` | string | 最后修改时间 |
| `last_access_time` | string | 最后访问时间 |
| `type` | string | 文件类型标识 |
| `file_key` | string | 文件 key（内部标识，备用） |
| `permission_action` | object | 当前用户对该文件的权限：`read`/`write`/`execute`/`allowed`（允许重命名删除） |

## 异常处理

- **未认证**：用户无 `user_cluster.token`，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **无默认集群**：`_get_default_token` 返回 error，直接透传
- **API 返回错误**：`code` 非 `"0"` 时，返回 `{"error": true, "message": "查询文件列表失败 [{code}]: {msg}"}`
- **网络异常**：捕获 HTTP 异常，返回 `{"error": true, "message": "查询文件列表请求异常: {详情}"}`

## 返回值示例

### 成功响应

```json
{
    "code": "0",
    "msg": "操作成功",
    "data": {
        "total": 3,
        "path": "/public/home/test/BASE",
        "files": [
            {
                "id": "769921355",
                "name": "00-HPC-CASE",
                "path": "/public/home/test/BASE/00-HPC-CASE",
                "size": 8192,
                "is_directory": true,
                "is_regular_file": false,
                "is_symbolic_link": false,
                "is_share": false,
                "is_other": false,
                "share_enabled": false,
                "owner": "test",
                "group": "test",
                "permission": "rwxrwxr-x",
                "creation_time": "2021-09-06 15:02:57",
                "last_modified_time": "2021-09-06 15:02:57",
                "last_access_time": "2021-09-06 15:02:46",
                "type": "",
                "file_key": "2137452981",
                "permission_action": {
                    "read": true,
                    "write": true,
                    "execute": true,
                    "allowed": true
                }
            }
        ]
    }
}
```

### 失败响应（上游透传）

```json
{
    "code": "10003",
    "msg": "参数不全",
    "data": null
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

其中 `code` 为 `"0"` 时表示成功。常见错误码：

| 错误码 | 说明 |
|--------|------|
| `0` | 成功 |
| `10001` | 内部错误 |
| `10003` | 参数不全 |
| `10004` | 参数无效 |
| `10007` | 用户已被冻结 |
| `10008` | 权限不足 |
| `10009` | 没有权限访问接口 |

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `efile_list_files`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `efile_list_files` 函数。
