# get_history_job_detail

## 需求

实现一个 MCP tool `get_history_job_detail`，根据 `jobId` 查询 HPC 集群中某个**历史作业**的详细信息（已完成/已终止的作业）。

## 前置条件

- 调用本工具前，需先调用 `list_available_partitions` 工具获取集群信息和 `jobManagerID`

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `user_cluster` 表联合 `cluster_url` 表查询选定集群的 `token`
- 如果用户未认证或选定集群无 token，返回错误提示 JSON，包含 `auth_url` 字段

## MCP 工具参数（用户传入）

| 参数名 | 类型 | 必填 | 默认值 / 来源说明 |
|--------|------|------|-------------------|
| `jobId` | string | 是 | 作业 ID（可从 `submit_job` 返回的 `jobID` 字段获取） |
| `jobmanagerId` | string | 是 | 调度器 ID（可从 `list_available_partitions` 返回结果中获取） |
| `acctTime` | string | 否 | 入账时间（结束时间），建议传入，能够提升查询性能。格式：`YYYY-MM-DD HH:MM:SS` |
| `token` | string | 否 | token（可从 `submit_job` 返回的 `token` 字段获取）。如果省略，后端从数据库自动获取。 |

## 后端处理逻辑

1. **认证校验**：检查 `users` 表中是否有 `acToken`
2. **解析 token**：优先级为 `显式参数 > 数据库`。如果提供了 `token`，使用显式参数；否则从 `user_cluster` 表自动获取
3. **调用 API**：GET `{base_url}/hpc/openapi/v2/historyjobs/{jobmanagerId}/{jobId}`，header 带 `token` 和 `Content-Type: application/json`
4. **返回结果**：直接返回 API 响应的 JSON 数据

## API 调用

- URL: `{hpcUrls}/hpc/openapi/v2/historyjobs/{jobmanagerId}/{jobId}`
  - `{hpcUrls}` 为从 `cluster_url` 表中获取的集群 URL（随机选取）
  - `{jobmanagerId}` 为用户传入的调度器 ID
  - `{jobId}` 为用户传入的作业 ID
- Method: GET
- Headers: `{"token": token, "Content-Type": "application/json"}`
- 超时 30s

## 异常处理

- **无可用集群**：`user_cluster` 中无对应集群的 token，返回错误提示，说明无法查询作业
- **集群无 URL**：若从 `cluster_url` 表中获取 `hpcUrls` 失败，返回适当提示
- **作业不存在/无权限**：API 返回错误时，返回易于理解的中文提示信息
- **其他错误**：捕获 HTTP 异常或 API 返回的错误码，返回易于理解的中文提示信息

## 返回值

- 直接返回 API 响应的 JSON 数据（包含作业的历史运行状态、资源使用情况、执行节点、耗时统计等）

**返回数据字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `acctTime` | string | 入账时间 |
| `jobId` | string | 作业 ID |
| `jobmanagerId` | number | 调度器 ID |
| `jobmanagerName` | string | 集群名称 |
| `userName` | string | 用户名 |
| `groupName` | string | 用户组名 |
| `jobName` | string | 作业名称 |
| `queue` | string | 队列名称 |
| `jobQueueTime` | string | 排队开始时间 |
| `jobStartTime` | string | 作业开始时间 |
| `owner` | string | 作业所有者 |
| `jobExecHost` | string | 执行节点 |
| `jobExecGpus` | null/array | 执行 GPU |
| `needNodes` | string | 所需节点 |
| `nodect` | number | 节点数 |
| `walltime` | number | 限制墙时间（秒） |
| `jobEndTime` | string | 作业结束时间 |
| `jobWaitTime` | number | 排队等待时间（秒） |
| `jobResponseTime` | number | 响应时间（秒） |
| `jobExitStatus` | number | 退出状态码 |
| `jobCpuTime` | number | CPU 时间（秒） |
| `jobMemUsed` | number | 已用内存（MB） |
| `jobVmemUsed` | number | 已用虚拟内存（MB） |
| `jobProcNum` | number | 进程数 |
| `jobGpuNum` | number | GPU 数 |
| `jobWalltimeUsed` | number | 已用墙时间（秒） |
| `workdir` | string | 工作目录 |
| `isSinglejob` | boolean | 是否为单作业 |
| `command` | string | 作业执行命令路径 |
| `jobReqMem` | string | 请求内存 |
| `jobReqCpu` | number | 请求 CPU 数 |
| `jobReqGpu` | number | 请求 GPU 数 |
| `jobState` | string | 作业状态（如 statC=已终止） |
| `stdout` | null/string | 标准输出 |
| `stderr` | null/string | 标准错误 |
| `jobExtAttr` | null/object | 作业扩展属性 |
| `jobExecCpus` | null/array | 执行 CPU 列表 |
| `jobExecDcus` | null/array | 执行 DCU 列表 |

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `get_history_job_detail`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中 `@mcp.tool()` 装饰的 `get_history_job_detail` 函数。
