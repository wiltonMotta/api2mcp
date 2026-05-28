# cancel_job

## 需求

实现一个 MCP tool `cancel_job`，取消/删除 HPC 集群中正在运行或排队的作业。支持批量取消。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `user_cluster` 表中存在有效的集群 token
- 需先调用 `list_available_partitions` 或 `list_running_jobs` 获取 `jobManagerId` 和 `clusterUserName`

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `user_cluster` 表读取 `isDefault=true` 的集群 `token`
- 如果用户未认证或无默认集群 token，返回错误提示 JSON，包含 `auth_url` 字段

## MCP 工具参数（用户传入）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `jobId` | string | 是 | — | 待取消的作业 ID，多个作业以逗号分隔，如 `"63436,63437"` |
| `jobManagerId` | string | 否 | `""` | 调度器 ID。为空时从 `user_cluster` 表 `isDefault=true` 的记录中自动获取 |
| `clusterId` | integer | 否 | `None` | 集群 ID。为空时使用默认集群（`isDefault=true`） |

## 后端处理逻辑

1. **认证校验**：检查 `users` 表中是否有 `acToken`
2. **解析默认集群**：
   - 若未指定 `clusterId`，调用 `_get_default_token(username)` 获取默认集群的 token、hpcUrls、jobManagerId
   - 若指定了 `clusterId`，从 `user_cluster` 表中直接查询对应记录
3. **构造请求**：
   - URL: `DELETE {hpcUrl}/hpc/openapi/v2/jobs`
   - Header: `token: {clusterToken}`, `Content-Type: application/x-www-form-urlencoded`
   - Body（form-urlencoded）:
     - `jobMethod`: 固定值 `"5"`（删除操作）
     - `strJobInfoMap`: 格式 `{jobManagerId},{clusterUserName}:{jobId}:;`
       - 多个作业以相同格式拼接：`{jobManagerId},{user}:{jobId1}:;{jobManagerId},{user}:{jobId2}:;`
4. **调用 API**：发送 DELETE 请求，超时 10s
5. **返回结果**：返回 API 响应 JSON

## API 调用

- URL: `{hpcUrls}/hpc/openapi/v2/jobs`
- Method: DELETE
- Headers: `{"token": "{clusterToken}", "Content-Type": "application/x-www-form-urlencoded"}`
- Body（form-urlencoded）:
  - `jobMethod=5`
  - `strJobInfoMap={jobManagerId},{clusterUserName}:{jobId}:;`
- 超时: 10s

### strJobInfoMap 格式

```
{jobManagerId},{userName}:{jobId}:;
```

- 多个作业拼接：`1638523853,test:197:;1638523853,test:196:;`
- `userName` 取 `user_cluster` 表中的 `homePath` 最后一段（即集群用户名），或从 `_get_default_token` 返回的上下文中获取

## 异常处理

- **未认证**：用户无 `acToken`，返回错误提示，包含 `auth_url` 字段
- **无默认集群**：`_get_default_token` 返回 error，直接透传
- **API 返回错误**：`code` 非 `"0"` 时，返回 `{"error": true, "message": "取消作业失败 [{code}]: {msg}"}`
- **网络异常**：捕获 HTTP 异常，返回 `{"error": true, "message": "取消作业请求异常: {详情}"}`

## 返回值

直接返回 API 响应的 JSON 数据。

### 成功响应

```json
{
    "code": "0",
    "msg": "success",
    "data": {
        "1615443225": "jobManagerId : 1615443225, delete the jobs [ac1npa3sf2:63436:] successfully!"
    }
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
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `cancel_job`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 与其他工具的对比

| 维度 | cancel_job | list_running_jobs | list_history_jobs |
|------|------------|-------------------|-------------------|
| 入口 | 集群 HPC 服务 | AC 统一服务 | AC 统一服务 |
| 认证 token | `user_cluster.token` | `users.acToken` | `users.acToken` |
| 方法 | DELETE | POST | POST |
| 操作 | 取消/删除作业 | 查询列表 | 查询列表 |
| URL | `{hpcUrl}/hpc/openapi/v2/jobs` | `/ac/openapi/v2/jobs/monitor/page-list` | `/ac/openapi/v2/jobs/history/page-list` |

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `cancel_job` 函数。
