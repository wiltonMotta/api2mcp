# efile_download

## 需求

实现一个 MCP tool `efile_download`，从 HPC 集群文件系统下载文件或文件夹。文件内容以 base64 编码字符串返回给 MCP 客户端，文件夹以 base64 编码的 zip 包返回。

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
| `path` | string | 是 | - | `path` | query | 要下载的文件/文件夹绝对路径 |
| `clusterId` | integer | 否 | `None` | — | — | 集群 ID。为空时使用默认集群 |

## 后端处理逻辑

1. **认证校验**：检查 `user_cluster` 表中是否有 `isDefault=true` 的 token
2. **获取 efileUrls**：
   - 若未指定 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 获取默认集群（`isDefault=true`）的 token 和 efileUrls
   - 若指定了 `clusterId`，通过 `user_cluster` JOIN `cluster_url` 查询指定集群的 token 和 efileUrls
   - efileUrls 为逗号分隔的多个 URL，采用 round-robin 策略选取一个可用 URL
3. **构造请求**：
   - URL: `GET {efileUrl}/efile/openapi/v2/file/download?path={path}`
   - Header: `token: {clusterToken}`
4. **调用 API**：发送 GET 请求，超时 120s（大文件下载），`stream=True`
5. **编码内容**：将响应流读取为二进制数据，base64 编码
6. **返回结果**：返回包含 base64 编码内容的自定义响应对象

## API 调用

- URL: `{efileUrls}/efile/openapi/v2/file/download`
- Method: GET
- Headers:
  - `token`: `{clusterToken}`
- Query params:
  - `path` — 要下载的文件/文件夹绝对路径
- 超时: 120s
- 响应体: 文件流（普通文件为原始流，文件夹为 zip 压缩流）

## 输出参数

自定义包装响应（非上游透传，因为下载接口返回的是流而非 JSON）。

| 参数名 | 类型 | 说明 |
|--------|------|------|
| `file_name` | string | 从响应头或 path 推导的文件名 |
| `file_content` | string | 文件内容的 base64 编码字符串 |
| `file_size` | integer | 原始文件大小（字节），解压后/base64 前的大小 |
| `content_type` | string | 内容类型（如 `application/zip` 表示文件夹，`application/octet-stream` 表示普通文件） |

## 异常处理

- **未认证**：用户无 `user_cluster.token`，返回 `{"error": true, "message": "未找到认证信息...", "auth_url": "..."}`
- **无默认集群**：`_get_default_token` 返回 error，直接透传
- **API 返回 HTTP 错误状态码**：尝试解析响应体中的 JSON 错误信息并透传
- **API 返回成功但为 JSON 格式**（文件不存在时可能返回 JSON 而非流）：透传上游 `{code, msg, data}` 响应
- **网络异常**：返回 `{"error": true, "message": "下载文件请求异常: {详情}"}`

## 返回值示例

### 成功响应

```json
{
    "file_name": "Linux.pdf",
    "file_content": "JVBERi0xLjQKJdPr6eEKMSAw...",
    "file_size": 1048576,
    "content_type": "application/octet-stream"
}
```

### 成功响应（文件夹下载，zip 包）

```json
{
    "file_name": "my_folder.zip",
    "file_content": "UEsDBBQAAAAIAO1...",
    "file_size": 5242880,
    "content_type": "application/zip"
}
```

### 失败响应（上游透传）

```json
{
    "code": "911020",
    "msg": "文件不存在",
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

## 注意事项

- 文件夹下载以 zip 格式压缩，大文件夹可能需要较长等待时间
- 下载大文件时超时设为 120s
- 由于内容以 base64 返回，实际传输体积比原始文件大约 33%。对于超大文件（>100MB），建议 Agent 先通过 `efile_list_files` 确认文件大小

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `efile_download`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `efile_download` 函数。
