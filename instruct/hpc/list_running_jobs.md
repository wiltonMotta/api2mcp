# hpc_list_running_jobs

## 需求

实现一个 MCP tool `hpc_list_running_jobs`，跨区域聚合查询实时作业列表（运行中/排队中/挂起等活跃状态）。通过 AC 服务统一入口，单次请求返回所有区域的实时作业记录，无需逐区域遍历。

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
| `jobId` | string | 否 | `""` | 作业 ID 精确匹配，传空表示不过滤 |
| `jobState` | string | 否 | `""` | 作业状态筛选。取值: `statR`(运行), `statQ`(排队), `statH`(保留), `statS`(挂起), `statW`(等待)。传空表示所有活跃状态 |
| `showGroupJobs` | boolean | 否 | `false` | 是否展示组内所有成员作业 |
| `clusterUserName` | string | 否 | `""` | 按用户名筛选作业，传空表示不过滤（使用当前用户） |
| `showAllData` | boolean | 否 | `false` | 是否返回所有字段。`false` 时返回核心字段子集；`true` 时额外返回 `initContentAttr`（调度器原始状态，用于状态校正）、资源请求量、内部冗余字段等 |

### 作业状态枚举

实时作业活跃状态：

| 值 | 含义 |
|----|------|
| `statR` | 运行中 |
| `statQ` | 排队中 |
| `statH` | 保留 |
| `statS` | 挂起 |
| `statW` | 等待 |

> 终态作业（`statE`/`statC`/`statD`/`statT` 等）请使用 `hpc_list_history_jobs` 查询。

## 后端处理逻辑

1. **认证校验**：从 `users` 表读取 `acToken`，若为空则返回未认证错误
2. **构造请求**：
   - URL: `POST https://www.scnet.cn/ac/openapi/v2/jobs/monitor/page-list`
   - Header: `token: {acToken}`, `Content-Type: application/json`
   - Body: JSON 包含所有用户传入的参数
3. **调用 API**：发送 POST 请求，超时 10s
4. **返回结果**：直接返回 API 响应的 JSON 数据

## API 调用

- URL: `https://www.scnet.cn/ac/openapi/v2/jobs/monitor/page-list`
- Method: POST
- Headers: `{"token": "{acToken}", "Content-Type": "application/json"}`
- Body: `{"page": 1, "size": 10, "clusterId": "", "queue": "", "jobId": "", "jobState": "", "showGroupJobs": false, "clusterUserName": "", "showAllData": false}`
- 超时: 10s

## 异常处理

- **未认证**：用户无 `acToken`，返回错误提示，包含 `auth_url` 字段
- **API 返回错误**：`code` 非 `"0"` 时，返回 `{"error": true, "message": "查询失败 [{code}]: {msg}"}`
- **网络异常**：捕获 HTTP 异常，返回 `{"error": true, "message": "查询实时作业请求异常: {详情}"}`

## 返回值

直接返回 API 响应的 JSON 数据。`data` 为分页对象，`data.records` 为作业记录数组（兼容 `data.list`）。

### 顶层结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | string | 状态码，`"0"` 表示成功 |
| `msg` | string | 响应消息，成功时返回 `"success"` |
| `data` | object | 数据体 |
| `data.total` | integer | 符合筛选条件的总记录数 |
| `data.pages` | integer | 符合筛选条件的总页数 |
| `data.records` | array | 作业记录列表（别名 `data.list`） |

### 作业记录字段

字段按类别分组。标记 ✅ 的为核心字段（`showAllData=false` 时返回），其余为扩展字段（需 `showAllData=true`）。

#### 集群与调度器

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `clusterId` | integer | ✅ | 区域/集群 ID |
| `clusterName` | string | ✅ | 区域/集群名称 |
| `jobManagerId` | string | ✅ | 调度器 ID |
| `jobManagerName` | string | ✅ | 调度器名称 |
| `jobManagerType` | string | ✅ | 调度器类型，如 `"SLURM"` |

#### 标识信息

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `jobId` | string | ✅ | 作业 ID |
| `jobName` | string | ✅ | 作业名 |
| `clusterUserName` | string | ✅ | 集群用户名 |

#### 作业状态与分类

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `jobState` | string | ✅ | 作业状态: `statR`(运行), `statQ`(排队), `statH`(保留), `statS`(挂起), `statW`(等待) |
| `appType` | string | ✅ | 应用类型，如 `"BASE"` |
| `taskType` | string | ✅ | 任务类型，如 `"HPC"` |
| `queue` | string | ✅ | 作业提交队列 |
| `priority` | string | ✅ | 作业优先级 |
| `reason` | string | ✅ | 作业状态原因，如 `"None"`、`"NonZeroExitCode"` |
| `requeue` | string | ✅ | 是否允许重新排队 |
| `tags` | string \| null | ✅ | 标签 |

> **状态校正**: 平台 `jobState` 可能延迟同步。当 `jobState=statC` 但 `reason=NonZeroExitCode` 时，真实状态应为失败（`statD`）。`showAllData=true` 时可进一步通过 `initContentAttr.JobState` 字段校正。

#### 时间信息

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `jobStartTime` | string | ✅ | 作业启动时间 |
| `jobQueueTime` | string | ✅ | 作业入队时间 |
| `jobRunTime` | string | ✅ | 作业已运行时长，格式 `HH:MM:SS` 或 `D-HH:MM:SS` |
| `wallTimeReq` | string | ✅ | 申请的最大运行时长，格式 `D-HH:MM:SS` |
| `jobQueueTimeUsed` | integer | ✅ | 排队等待时间（秒） |

#### 资源使用

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `nodeUsed` | string | ✅ | 使用的节点名/列表 |
| `nodeNumUsed` | integer | ✅ | 使用节点数 |
| `procNumUsed` | integer | ✅ | 使用的 CPU 核心数 |
| `dcuNumUsed` | integer | ✅ | 使用 DCU 加速卡数 |
| `gpuNumUsed` | integer | ✅ | 使用 GPU 卡数 |

#### 路径信息

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `workDir` | string | ✅ | 作业工作路径 |
| `outputPath` | string | ✅ | 标准输出文件路径 |
| `errorPath` | string | ✅ | 标准错误输出路径 |

#### VNC 信息

| 字段 | 类型 | 核心 | 说明 |
|------|------|------|------|
| `jobVncSessionInfo` | object \| null | ✅ | 作业 VNC 会话信息，无 VNC 时为 null |

**`jobVncSessionInfo` 子字段（非 null 时）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `strSessionID` | string | 会话 ID |
| `strSessionOwner` | string | 会话所有者 |
| `strSessionCTime` | string | 会话创建时间 |
| `strServerName` | string | 会话所在主机的主机名 |
| `strServerAddr` | string | 会话所在主机的地址 |
| `strGeometry` | string | 宽和高，如 `"1280x1088"` |
| `strSessionWidth` | string | 会话宽度 |
| `strSessionHeight` | string | 会话高度 |
| `loginPasswd` | string | 会话登录密码 |
| `strSessionType` | string | 会话类型 |
| `strRelateJobID` | string | 相关作业号 |
| `strJobManagerName` | string | 会话所属区域名称 |
| `strJobManagerID` | string | 调度器 ID |
| `strJobManagerAddr` | string | 调度器地址 |
| `iClientNumber` | integer | 连接该会话的客户端数 |
| `listClients` | array | 会话客户端地址列表 |
| `locale` | string | 编码方式 |
| `strAuthType` | string | 认证方式 |
| `iPixelDepth` | string | 像素深度 |
| `archive` | string | VncViewer.jar 文件 |
| `vncCode` | string | .class 文件 |
| `mapSessionExtraAttrs` | map | 其它会话属性 |

#### showAllData=true 额外字段

##### 调度器状态校正

| 字段 | 类型 | 说明 |
|------|------|------|
| `initContentAttr` | string | JSON 字符串，含底层调度器原始状态，用于校正平台状态延迟 |

**`initContentAttr` 解析后的关键字段：**

| 字段 | 说明 |
|------|------|
| `JobState` | 调度器原始状态（`RUNNING`/`FAILED`/`COMPLETED`）。当 `jobState=statC` 但此处为 `FAILED` 时，真实状态应为 `statD` |
| `StartTime` | 调度器记录的启动时间（ISO 格式） |
| `Requeue` | 是否重新排队 |
| `Comment` | 注释（应用类型） |
| `TimeMin` | 最小时间 |
| `runningErrType` | 运行错误类型 |
| `runningErrMsg` | 运行错误信息 |

##### 资源请求量

| 字段 | 类型 | 说明 |
|------|------|------|
| `cpuCore` | integer | CPU 核心数 |
| `gpuNum` | integer | GPU 数量 |
| `nodeNumReq` | integer | 申请的节点数 |
| `procNumReq` | integer | 申请的处理器数 |
| `gpuNumReq` | integer | 申请的 GPU 数 |
| `dcuNumReq` | integer | 申请的 DCU 数 |

##### 资源配置与使用

| 字段 | 类型 | 说明 |
|------|------|------|
| `memUsed` | string | 已使用内存，如 `"7600M"` |
| `vmemUsed` | string \| null | 已使用虚拟内存 |
| `wallTime` | string | 已运行时长（同 `jobRunTime`） |
| `cpuTime` | string | CPU 时间 |
| `exitCode` | string | 退出码 |

##### 调度器执行信息

| 字段 | 类型 | 说明 |
|------|------|------|
| `execHost` | string | 执行主机名 |
| `execGpus` | string \| null | 分配的 GPU 设备 |
| `software` | string \| null | 软件信息 |
| `outPath` | string | 标准输出路径（同 `outputPath`） |
| `vncSessionInfo` | object \| null | VNC 会话信息（同 `jobVncSessionInfo`） |

##### 内部/冗余字段

与核心字段值相同，MCP 工具以核心字段为准。

| 字段 | 类型 | 说明 | 对应核心字段 |
|------|------|------|-------------|
| `id` | string | 作业 ID | `jobId` |
| `name` | string | 作业名 | `jobName` |
| `managerId` | string | 调度器 ID | `jobManagerId` |
| `managerName` | string | 调度器名称 | `jobManagerName` |
| `managerType` | string | 调度器类型 | `jobManagerType` |
| `owner` | string | 作业所有者 | `clusterUserName` |
| `status` | string | 作业状态 | `jobState` |
| `ctime` | string | 创建时间 | — |
| `qtime` | string | 入队时间 | `jobQueueTime` |
| `etime` | string \| null | 结束时间，运行中为 null | — |
| `startTime` | string | 启动时间 | `jobStartTime` |
| `modifyTime` | string \| null | 修改时间 | — |
| `timeStamp` | string \| null | 时间戳 | — |

### 响应示例

#### showAllData=false（默认，核心字段）

```json
{
    "code": "0",
    "msg": "success",
    "data": {
        "total": 1,
        "pages": 1,
        "records": [
            {
                "clusterId": 20078,
                "clusterName": "华中三区【武汉】",
                "jobId": "63436",
                "jobName": "sleep_0526_170904",
                "jobManagerId": "1615443225",
                "jobManagerName": "whcs",
                "jobManagerType": "SLURM",
                "clusterUserName": "ac1npa3sf2",
                "queue": "whhcnormal",
                "jobStartTime": "2026-05-26 17:09:14",
                "appType": "BASE",
                "jobState": "statR",
                "priority": "1256",
                "jobRunTime": "00:01:31",
                "jobVncSessionInfo": null,
                "nodeUsed": "b08r1n14",
                "workDir": "/work/home/ac1npa3sf2/_job_2026_05_26_170904",
                "outputPath": "/work/home/ac1npa3sf2/_job_2026_05_26_170904/std.out.63436",
                "errorPath": "/work/home/ac1npa3sf2/_job_2026_05_26_170904/std.err.63436",
                "reason": "None",
                "requeue": "0",
                "wallTimeReq": "1-00:00:00",
                "jobQueueTime": "2026-05-26 17:09:07",
                "taskType": "HPC",
                "tags": null,
                "nodeNumUsed": 1,
                "procNumUsed": 1,
                "dcuNumUsed": 0,
                "gpuNumUsed": 0,
                "jobQueueTimeUsed": 7
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
        "total": 1,
        "pages": 1,
        "records": [
            {
                "clusterId": 20078,
                "clusterName": "华中三区【武汉】",
                "jobId": "63436",
                "jobName": "sleep_0526_170904",
                "jobManagerId": "1615443225",
                "jobManagerName": "whcs",
                "jobManagerType": "SLURM",
                "clusterUserName": "ac1npa3sf2",
                "queue": "whhcnormal",
                "jobStartTime": "2026-05-26 17:09:14",
                "appType": "BASE",
                "jobState": "statR",
                "priority": "1256",
                "jobRunTime": "00:00:21",
                "jobVncSessionInfo": null,
                "nodeUsed": "b08r1n14",
                "workDir": "/work/home/ac1npa3sf2/_job_2026_05_26_170904",
                "outputPath": "/work/home/ac1npa3sf2/_job_2026_05_26_170904/std.out.63436",
                "errorPath": "/work/home/ac1npa3sf2/_job_2026_05_26_170904/std.err.63436",
                "reason": "None",
                "requeue": "0",
                "wallTimeReq": "1-00:00:00",
                "jobQueueTime": "2026-05-26 17:09:07",
                "taskType": "HPC",
                "tags": null,
                "nodeNumUsed": 1,
                "procNumUsed": 1,
                "dcuNumUsed": 0,
                "gpuNumUsed": 0,
                "jobQueueTimeUsed": 7,
                "id": "63436",
                "name": "sleep_0526_170904",
                "managerId": "1615443225",
                "managerName": "whcs",
                "managerType": "SLURM",
                "owner": "ac1npa3sf2",
                "ctime": "2026-05-26 17:09:07",
                "qtime": "2026-05-26 17:09:07",
                "etime": null,
                "startTime": "2026-05-26 17:09:14",
                "status": "statR",
                "wallTime": "00:00:21",
                "cpuTime": "",
                "memUsed": "7600M",
                "vmemUsed": null,
                "vncSessionInfo": null,
                "execGpus": null,
                "software": null,
                "cpuCore": 1,
                "gpuNum": 0,
                "exitCode": "",
                "nodeNumReq": 1,
                "procNumReq": 1,
                "gpuNumReq": 0,
                "dcuNumReq": 0,
                "modifyTime": null,
                "execHost": "b08r1n14",
                "outPath": "/work/home/ac1npa3sf2/_job_2026_05_26_170904/std.out.63436",
                "initContentAttr": "{\"Requeue\":\"0\",\"Comment\":\"BASE\",\"JobState\":\"RUNNING\",\"TimeMin\":\"N/A\",\"StartTime\":\"2026-05-26T17:09:14\",\"runningErrType\":\"\",\"runningErrMsg\":\"\"}",
                "timeStamp": null
            }
        ]
    }
}
```

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `hpc_list_running_jobs`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 与 hpc_list_history_jobs 的对比

| 维度 | hpc_list_running_jobs | hpc_list_history_jobs |
|------|-------------------|-------------------|
| 入口 | `POST /ac/openapi/v2/jobs/monitor/page-list` | `POST /ac/openapi/v2/jobs/history/page-list` |
| 作业范围 | 运行中、排队中、挂起、保留等活跃状态 | 已完成、失败、超时、取消等终态 |
| 状态枚举 | `statR`/`statQ`/`statH`/`statS`/`statW` | `statC`/`statE`/`statD`/`statT`/`statN` 等 |
| 时间入参 | 无 startTime/endTime | 有 startTime/endTime（默认最近7天） |
| 运行时长 | `jobRunTime`（`HH:MM:SS` 格式） | `jobWalltimeUsed`（秒，整数） |
| 节点字段 | `nodeUsed`（字符串） | `nodect` + `nodeNumUsed` |
| 用户字段 | `clusterUserName` | `userName` / `clusterUserName` |
| 输出文件 | `outputPath` / `errorPath` | `stdout` / `stderr` |
| VNC 信息 | `jobVncSessionInfo` | 无 |
| 状态校正 | 需结合 `reason` 或 `initContentAttr.JobState` 校正状态同步延迟 | 无需校正（终态） |

## 代码位置

`main.py` 中新增 `@mcp.tool()` 装饰的 `hpc_list_running_jobs` 函数。
