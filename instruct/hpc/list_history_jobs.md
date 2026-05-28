# list_history_jobs

## 需求

实现一个 MCP tool `list_history_jobs`，跨区域聚合查询历史作业列表。通过 AC 服务统一入口，单次请求返回所有区域的作业记录，无需逐区域遍历。

## 前置条件

- 用户需先完成 AK/SK 认证（`/auth/{username}`），确保 `users` 表中存在有效的 `acToken`

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `users` 表读取 `acToken`（AC 统一认证 token，非集群 token）
- 如果用户未认证或无 `acToken`，返回错误提示 JSON，包含 `auth_url` 字段

## MCP 工具参数（用户传入）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `page` | integer | 否 | `1` | 页码，从 1 开始 |
| `size` | integer | 否 | `10` | 每页记录数 |
| `clusterId` | string | 否 | `""` | 区域/集群 ID 筛选，传空表示所有区域 |
| `queue` | string | 否 | `""` | 队列名称筛选，传空表示所有队列 |
| `jobState` | string | 否 | `""` | 作业状态筛选。取值: `statE`(退出), `statC`(完成), `statDE`(取消), `statD`(失败), `statT`(超时), `statN`(节点异常), `statRQ`(重新运行)。传空表示所有状态 |
| `startTime` | string | 否 | 7天前 `00:00:00` | 查询开始时间，格式 `YYYY-MM-DD HH:MM:SS` |
| `endTime` | string | 否 | 当前时间 | 查询结束时间，格式 `YYYY-MM-DD HH:MM:SS` |
| `showGroupJobs` | boolean | 否 | `false` | 是否展示组内所有成员作业 |
| `jobId` | string | 否 | `""` | 作业 ID 精确匹配，传空表示不过滤 |
| `clusterUserName` | string | 否 | `""` | 按用户名筛选作业，传空表示不过滤（使用当前用户） |
| `showAllData` | boolean | 否 | `false` | 是否返回所有字段。`false` 时返回核心字段子集 |

## 后端处理逻辑

1. **认证校验**：从 `users` 表读取 `acToken`，若为空则返回未认证错误
2. **构造请求**：
   - URL: `POST https://www.scnet.cn/ac/openapi/v2/jobs/history/page-list`
   - Header: `token: {acToken}`, `Content-Type: application/json`
   - Body: JSON 包含所有用户传入的参数
3. **调用 API**：发送 POST 请求，超时 10s
4. **返回结果**：直接返回 API 响应的 JSON 数据

## API 调用

- URL: `https://www.scnet.cn/ac/openapi/v2/jobs/history/page-list`
- Method: POST
- Headers: `{"token": "{acToken}", "Content-Type": "application/json"}`
- Body: `{"page": 1, "size": 10, "clusterId": "", "queue": "", "jobId": "", "jobState": "", "startTime": "", "endTime": "", "showGroupJobs": false, "clusterUserName": "", "showAllData": false}`
- 超时: 10s

## 异常处理

- **未认证**：用户无 `acToken`，返回错误提示，包含 `auth_url` 字段
- **API 返回错误**：`code` 非 `"0"` 时，返回 `{"error": true, "message": "查询失败 [{code}]: {msg}"}`
- **网络异常**：捕获 HTTP 异常，返回 `{"error": true, "message": "查询历史作业请求异常: {详情}"}`

## 返回值

直接返回 API 响应的 JSON 数据。`data` 为分页对象，`data.records` 为作业记录数组。

### 顶层结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 响应消息，成功时返回 `"success"` |
| `data` | object | 数据体 |
| `data.total` | integer | 符合筛选条件的总记录数 |
| `data.pages` | integer | 符合筛选条件的总页数 |
| `data.records` | array | 作业记录列表 |

### 作业记录字段

字段按类别分组。标记 ✅ 的为核心字段（`showAllData=false` 时返回），其余为扩展字段（需 `showAllData=true`）。

#### 标识信息

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `jobId` | string | ✅ | 作业 ID |
| `jobName` | string | ✅ | 作业名 |
| `userName` | string | | 用户名 |
| `groupName` | string | | 用户组名 |
| `clusterUserName` | string | ✅ | 集群用户名 |
| `owner` | string | | 作业拥有者 |
| `account` | string | | 计费账号 |
| `historyAccount` | string | | 作业运行结束时提交者所属的账号 |

#### 集群信息

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `clusterId` | integer | ✅ | 区域/集群 ID |
| `clusterName` | string | ✅ | 区域/集群名称 |
| `jobmanagerId` | long | ✅ | 区域/调度器 ID |
| `jobmanagerName` | string | ✅ | 区域/调度器名称 |

#### 作业状态与分类

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `jobState` | string | ✅ | 作业状态。取值: `statE`(退出), `statC`(完成), `statDE`(取消), `statD`(失败), `statT`(超时), `statN`(节点异常), `statRQ`(重新运行)。注意：API 返回可能为全大写语义值如 `COMPLETED`、`TIMEOUT`，以实际返回为准 |
| `jobExitStatus` | long | ✅ | 作业退出代码，0 表示正常退出 |
| `appType` | string | ✅ | 应用类型，如 `"BASE"` |
| `taskType` | string | ✅ | 任务类型，如 `"HPC"` |
| `queue` | string | ✅ | 作业提交队列 |
| `scale` | string | | 作业规模 |
| `isSinglejob` | boolean | | 是否为独占节点的作业 |
| `tags` | string \| null | ✅ | 标签 |
| `jobExtAttr` | any \| null | | 作业扩展属性 |
| `startCount` | string | | 作业的启动次数 |

#### 时间信息

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `jobQueueTime` | string | ✅ | 作业入队时间 |
| `jobStartTime` | string | ✅ | 作业启动时间 |
| `jobEndTime` | string | ✅ | 作业结束时间 |
| `acctTime` | string | | 记账时间 |
| `walltime` | long | | 提交时请求的 Walltime（秒） |
| `jobWalltimeUsed` | long | ✅ | 实际使用的 Walltime（秒） |
| `jobQueueTimeUsed` | long | ✅ | 排队等待时间（秒） |
| `jobWaitTime` | long | | 作业等待时间 = jobStartTime - jobQueueTime（秒） |
| `jobResponseTime` | long | | 作业响应时间 = jobEndTime - jobQueueTime（秒） |
| `jobCpuTime` | long | | 作业占用的 CPU 时间（秒） |

#### 资源使用量

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `nodect` | long | ✅ | 分配的节点数 |
| `nodeNumUsed` | long | ✅ | 实际使用节点数 |
| `procNumUsed` | long | ✅ | 使用处理器核数 |
| `dcuNumUsed` | long | ✅ | 使用 DCU 加速卡数 |
| `gpuNumUsed` | long | ✅ | 使用 GPU 卡数 |
| `jobDcuNum` | long | | 作业使用的 DCU 数 |
| `jobProcNum` | long | | 作业使用的处理器核数 |
| `jobGpuNum` | long | | 作业使用的 GPU 核数 |
| `jobMluNum` | long | | 作业使用的 MLU 数 |
| `jobMemUsed` | long | | 作业使用的物理内存（KB） |
| `jobVmemUsed` | long | | 作业使用的虚拟内存（KB） |

#### 共享 / 独占资源细分

仅 `showAllData=true` 时返回。非独占作业时共享字段有值，独占作业时独占字段有值。

| 字段 | 类型 | 说明 |
|------|------|------|
| `shareCputime` | long | 共享作业的 CPU 时间（秒） |
| `shareMem` | long | 共享作业的内存（KB） |
| `shareWalltime` | long | 共享作业的 Walltime（秒） |
| `exclusiveCputime` | long | 独占作业的 CPU 时间（秒） |
| `exclusiveMem` | long | 独占作业的内存（KB） |
| `exclusiveWalltime` | long | 独占作业的 Walltime（秒） |

#### 资源请求量

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `jobReqCpu` | double | | 申请 CPU 核心数 |
| `jobReqGpu` | double | | 申请 GPU 卡数 |
| `jobReqDcu` | double | | 申请 DCU 卡数 |
| `jobReqMem` | string | | 申请内存大小，如 `"2153M"` |
| `jobReqNodes` | double | | 申请节点数 |
| `reqMlu` | integer | | 申请 MLU 卡数 |

#### 计费信息

仅 `showAllData=true` 时返回。

| 字段 | 类型 | 说明 |
|------|------|------|
| `cpuNuclearHour` | string | CPU 核时 |
| `cpuNuclearSec` | string | CPU 核秒 |
| `cpuUnitPrice` | double | 作业提交时 CPU 单价 |
| `dcuCardHour` | string | DCU 卡时 |
| `dcuCardSec` | string | DCU 卡秒 |
| `dcuUnitPrice` | double | 作业提交时 DCU 单价 |
| `gpuCardHour` | string | GPU 卡时 |
| `gpuCardSec` | string | GPU 卡秒 |
| `gpuUnitPrice` | double | 作业提交时 GPU 单价 |
| `efficiencyCpu` | string | CPU 效率，如 `"100.00%"` |
| `goldenable` | string | 作业提交时计费状态：`"true"` / `"false"` / `"unknown"` |
| `historyQueuerate` | string | 作业提交时的队列费率 |

#### 执行环境

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `workdir` | string | ✅ | 作业的工作路径 |
| `command` | string | ✅ | 作业脚本路径 |
| `commandExist` | string | | 作业脚本是否存在 |
| `stdout` | string | ✅ | 标准输出文件路径 |
| `stderr` | string | ✅ | 标准错误文件路径 |
| `needNodes` | string | | 分配的节点名或节点数 |
| `jobExecHost` | string | | 作业执行节点 |
| `jobExecGpus` | string \| null | | 作业占用的 GPU 节点 |
| `jobExecCpus` | string \| null | | 作业占用的 CPU 核心 |
| `jobExecDcus` | string \| null | | 作业占用的 DCU 节点 |
| `jobExecMlus` | string \| null | | 作业占用的 MLU 节点 |

### 响应示例

#### showAllData=false（默认，核心字段）

```json
{
    "code": "0",
    "msg": "success",
    "data": {
        "total": 7,
        "pages": 2,
        "records": [
            {
                "jobId": "61934",
                "jobName": "sleep_0522_095054",
                "clusterUserName": "ac1npa3sf2",
                "jobManagerId": 1615443225,
                "jobManagerName": "whcs",
                "clusterId": 20078,
                "clusterName": "华中三区【武汉】",
                "appType": "BASE",
                "queue": "comp",
                "jobExitStatus": 0,
                "nodect": 1,
                "jobQueueTime": "2026-05-22 09:50:54",
                "jobEndTime": "2026-05-22 09:51:57",
                "jobWalltimeUsed": 62,
                "workdir": "/work/home/ac1npa3sf2/jobs",
                "jobState": "statC",
                "taskType": "HPC",
                "tags": null,
                "nodeNumUsed": 1,
                "procNumUsed": 1,
                "dcuNumUsed": 0,
                "gpuNumUsed": 0,
                "jobQueueTimeUsed": 1,
                "jobStartTime": "2026-05-22 09:50:55",
                "stdout": "/work/home/ac1npa3sf2/jobs/std.out.61934",
                "stderr": "/work/home/ac1npa3sf2/jobs/std.err.61934"
            }
        ]
    }
}
```

#### showAllData=true（全部字段）

```json
{
    "code": "0",
    "msg": "success",
    "data": {
        "total": 7,
        "pages": 2,
        "records": [
            {
                "jobId": "61934",
                "jobName": "sleep_0522_095054",
                "clusterUserName": "ac1npa3sf2",
                "jobManagerId": 1615443225,
                "jobManagerName": "whcs",
                "clusterId": 20078,
                "clusterName": "华中三区【武汉】",
                "appType": "BASE",
                "queue": "comp",
                "jobExitStatus": 0,
                "nodect": 1,
                "jobQueueTime": "2026-05-22 09:50:54",
                "jobEndTime": "2026-05-22 09:51:57",
                "jobWalltimeUsed": 62,
                "workdir": "/work/home/ac1npa3sf2/jobs",
                "jobState": "statC",
                "taskType": "HPC",
                "tags": null,
                "nodeNumUsed": 1,
                "procNumUsed": 1,
                "dcuNumUsed": 0,
                "gpuNumUsed": 0,
                "jobQueueTimeUsed": 1,
                "jobStartTime": "2026-05-22 09:50:55",
                "stdout": "/work/home/ac1npa3sf2/jobs/std.out.61934",
                "stderr": "/work/home/ac1npa3sf2/jobs/std.err.61934",
                "acctTime": "2026-05-22T01:52:41.000+0000",
                "userName": "ac1npa3sf2",
                "groupName": "ac1npa3sf2",
                "walltime": 300,
                "jobWaitTime": 1,
                "jobResponseTime": 63,
                "jobCpuTime": 0,
                "jobDcuNum": 0,
                "jobProcNum": 1,
                "jobGpuNum": 0,
                "jobMluNum": 0,
                "scale": "A",
                "account": "ac1npa3sf2",
                "jobExecHost": "c1",
                "jobExecGpus": null,
                "jobMemUsed": 2348,
                "jobVmemUsed": 501180,
                "reqCpu": 1.0,
                "reqGpu": 0.0,
                "reqDcu": 0.0,
                "reqMlu": 0,
                "reqMem": "7800M",
                "reqNodes": 1.0,
                "needNodes": "c1",
                "command": "/work/home/ac1npa3sf2/jobs/job_BASE.slurm",
                "isSinglejob": false,
                "historyQueuerate": "1",
                "historyAccount": "ac1npa3sf2",
                "cpuNuclearHour": "0.0172",
                "cpuNuclearSec": "62",
                "cpuUnitPrice": 1,
                "dcuCardHour": "0",
                "dcuCardSec": "0",
                "dcuUnitPrice": 1,
                "gpuCardHour": "0",
                "gpuCardSec": "0",
                "gpuUnitPrice": 1,
                "efficiencyCpu": "100.00%",
                "goldenable": "true",
                "shareCputime": 0,
                "shareMem": 0,
                "shareWalltime": 0,
                "exclusiveCputime": 0,
                "exclusiveMem": 0,
                "exclusiveWalltime": 0,
                "jobExtAttr": null,
                "jobExecCpus": null,
                "jobExecDcus": null,
                "jobExecMlus": null,
                "commandExist": "true",
                "startCount": ""
            }
        ]
    }
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `list_history_jobs`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 与 get_history_job_detail 的区别

| 维度 | list_history_jobs | get_history_job_detail |
|------|-------------------|------------------------|
| 入口 | AC 统一服务 (`www.scnet.cn`) | 各集群 HPC 服务 |
| 认证 token | `users.acToken`（AC token） | `user_cluster.token`（集群 token） |
| 范围 | 跨区域聚合，一次查全部 | 单集群查询 |
| 查询方式 | 列表分页 | 按 jobId 精确查询 |
| 参数 | 筛选条件（状态、时间、队列等） | jobId + jobmanagerId |
| 返回 | 作业列表（多条） | 单个作业详情 |

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `list_history_jobs` 函数。
