# 聚合查询实时作业列表接口

## 概述

跨区域聚合查询实时作业列表（运行中/排队中等活跃状态的作业）。通过 AC 服务统一入口，单次请求返回所有区域的实时作业记录，无需逐区域遍历。

- **服务地址**: `https://www.scnet.cn`
- **接口路径**: `POST /ac/openapi/v2/jobs/monitor/page-list`
- **认证方式**: AC Token（请求头 `token` 字段）
- **超时建议**: 5 秒
- **说明**: 实时作业指当前正在运行（`statR`）、排队（`statQ`）、挂起（`statS`）、保留（`statH`）等非终态的作业

---

## 请求参数

### Headers

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `token` | string | 是 | 取自users表的acToken |
| `Content-Type` | string | 是 | 固定值 `application/json` |

### Body（JSON）

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `page` | integer | 否 | `1` | 页码，从 1 开始 |
| `size` | integer | 否 | `10` | 每页记录数 |
| `clusterId` | string | 否 | `""` | 区域/集群 ID 筛选，传空表示所有区域 |
| `queue` | string | 否 | `""` | 队列名称筛选，传空表示所有队列 |
| `jobState` | string | 否 | `""` | 作业状态筛选，传空表示所有状态。取值: `statR`(运行), `statQ`(排队), `statH`(保留), `statS`(挂起), `statW`(等待) |
| `showGroupJobs` | boolean | 否 | `false` | 是否展示组内所有成员作业 |
| `clusterUserName` | string | 否 | `""` | 按用户名筛选作业，传空不过滤 |
| `showAllData` | boolean | 否 | `false` | 是否返回所有字段（含计费、扩展属性等完整数据） |

### 作业状态枚举（jobState）

实时作业活跃状态:

| 值 | 含义 |
|----|------|
| `statR` | 运行中 |
| `statQ` | 排队中 |
| `statH` | 保留 |
| `statS` | 挂起 |
| `statW` | 等待 |

> `statE`(退出), `statC`(完成), `statDE`(取消), `statD`(失败), `statT`(超时), `statN`(节点异常), `statRQ`(重新运行) 等终态作业请使用历史作业接口（`/ac/openapi/v2/jobs/history/page-list`）查询。

---

## 响应格式

### 成功响应（showAllData=false，默认）

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

### 成功响应（showAllData=true，完整字段）

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

### 失败响应

```json
{
    "msg": "auth_fail",
    "code": "10008",
    "data": "token is invalid"
}
{
    "timestamp": "2026-05-26T09:24:07.358+0000",
    "status": 400,
    "error": "Bad Request",
    "path": "/v2/jobs/monitor/page-list"
}
```

> - `code` 为 `"0"`（字符串类型）表示成功，非 `"0"` 表示失败
> - 数据在 `data.records` 中（文档中为 `data.list`，兼容两种取值）
> - `data.total` 为符合条件的总记录数，`data.pages` 为总页数
> - 网络异常时 `code` 为 `-1`，`msg` 包含具体错误信息

---

## 响应字段说明

### 顶层结构

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `msg` | string | 响应消息，成功时返回 `success` | `"success"` |
| `code` | string | 状态码，`"0"` 表示成功 | `"0"` |
| `data` | object | 数据体 | — |
| `data.total` | int | 符合条件的总记录数 | `100` |
| `data.pages` | int | 总页数 | `10` |
| `data.records` | array | 作业记录列表（文档中别名 `data.list`） | — |

### 作业记录对象（records[]）—— showAllData=false 核心字段

#### 集群与调度器

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `clusterId` | integer | 区域/集群 ID | `20078` |
| `clusterName` | string | 区域/集群名称 | `"华中三区【武汉】"` |
| `jobManagerId` | string | 调度器 ID | `"1615443225"` |
| `jobManagerName` | string | 调度器名称 | `"whcs"` |
| `jobManagerType` | string | 调度器类型 | `"SLURM"` |

#### 标识信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `jobId` | string | 作业 ID | `"63436"` |
| `jobName` | string | 作业名 | `"sleep_0526_170904"` |
| `clusterUserName` | string | 集群用户名 | `"ac1npa3sf2"` |

#### 作业状态与分类

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `jobState` | string | 作业状态: `statR`(运行), `statQ`(排队), `statH`(保留), `statS`(挂起), `statW`(等待) | `"statR"` |
| `appType` | string | 应用类型 | `"BASE"` |
| `taskType` | string | 任务类型 | `"HPC"` |
| `queue` | string | 作业提交队列 | `"whhcnormal"` |
| `priority` | string | 作业优先级 | `"1256"` |
| `reason` | string | 作业状态原因 | `"None"` |
| `requeue` | string | 是否允许重新排队 | `"0"` |
| `tags` | string \| null | 标签 | `null` |

#### 时间信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `jobStartTime` | string | 作业启动时间 | `"2026-05-26 17:09:14"` |
| `jobQueueTime` | string | 作业入队时间 | `"2026-05-26 17:09:07"` |
| `jobRunTime` | string | 作业已运行时长，格式 `HH:MM:SS` 或 `D-HH:MM:SS` | `"00:01:31"` |
| `wallTimeReq` | string | 申请的最大运行时长，格式 `D-HH:MM:SS` | `"1-00:00:00"` |
| `jobQueueTimeUsed` | integer | 排队等待时间（秒） | `7` |

#### 资源使用

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `nodeUsed` | string | 使用的节点名/列表 | `"b08r1n14"` |
| `nodeNumUsed` | integer | 使用节点数 | `1` |
| `procNumUsed` | int | 使用的 CPU 核心数 | `1` |
| `dcuNumUsed` | integer | 使用 DCU 加速卡数 | `0` |
| `gpuNumUsed` | integer | 使用 GPU 卡数 | `0` |

#### 路径信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `workDir` | string | 作业工作路径 | `"/work/home/ac1npa3sf2/_job_2026_05_26_170904"` |
| `outputPath` | string | 标准输出文件路径 | `"/work/home/.../std.out.63436"` |
| `errorPath` | string | 标准错误输出路径 | `"/work/home/.../std.err.63436"` |

#### VNC 信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `jobVncSessionInfo` | object \| null | 作业 VNC 会话信息，无 VNC 时为 null | `null` |

**`jobVncSessionInfo` 子字段（非 null 时）:**

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `archive` | string | VncViewer.jar 文件 | `"VncViewer.jar"` |
| `iClientNumber` | int | 连接该会话的客户端数 | `0` |
| `iPixelDepth` | string | 像素深度 | `"0"` |
| `listClients` | array | 会话客户端地址列表 | `[]` |
| `locale` | string | 编码方式 | `"zh_CN.UTF-8"` |
| `loginPasswd` | string | 会话登录密码 | `"d2fe93bf"` |
| `mapSessionExtraAttrs` | map | 其它会话属性 | `{}` |
| `strAuthType` | string | 认证方式 | `""` |
| `strGeometry` | string | 宽和高 | `"1280x1088"` |
| `strJobManagerAddr` | string | 会话所属区域的地址 | `"10.0.35.248"` |
| `strJobManagerID` | string | 调度器 ID | `"1634819344"` |
| `strJobManagerName` | string | 会话所属区域的名称 | `"Cluster"` |
| `strRelateJobID` | string | 相关作业号 | `"110"` |
| `strServerAddr` | string | 会话所在主机的地址 | `"10.0.35.248"` |
| `strServerName` | string | 会话所在主机的主机名 | `"node248"` |
| `strSessionCTime` | string | 会话创建时间 | `"2021-11-04 17:57:40"` |
| `strSessionHeight` | string | 会话高度 | `"1088"` |
| `strSessionID` | string | 会话 ID | `"1"` |
| `strSessionOwner` | string | 会话所有者 | `"demo"` |
| `strSessionType` | string | 会话类型 | `""` |
| `strSessionWidth` | string | 会话宽度 | `"1280"` |
| `vncCode` | string | .class 文件 | `"com.tigervnc.vncviewer.VncViewer.class"` |

### 作业记录对象（records[]）—— showAllData=true 额外字段

#### 调度器状态校正

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `initContentAttr` | string | JSON 字符串，含底层调度器原始状态，用于校正平台状态延迟 | `"{\"JobState\":\"RUNNING\",...}"` |

**`initContentAttr` 解析后的关键字段：**

| 字段 | 说明 |
|------|------|
| `JobState` | 调度器原始状态（如 `RUNNING`/`FAILED`/`COMPLETED`），当 `jobState=statC` 但此处为 `FAILED` 时真实状态应为失败 |
| `StartTime` | 调度器记录的启动时间（ISO 格式） |
| `Requeue` | 是否重新排队 |
| `Comment` | 注释（应用类型） |
| `TimeMin` | 最小时间 |
| `runningErrType` | 运行错误类型 |
| `runningErrMsg` | 运行错误信息 |

> 状态校正逻辑: 当平台 `jobState` 为 `statC`（完成）但 `initContentAttr.JobState` 为 `FAILED` 或 `reason` 为 `NonZeroExitCode` 时，真实状态应判定为 `statD`（失败）。

#### 资源请求量

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `cpuCore` | int | CPU 核心数 | `1` |
| `gpuNum` | int | GPU 数量 | `0` |
| `nodeNumReq` | int | 申请的节点数 | `1` |
| `procNumReq` | int | 申请的处理器数 | `1` |
| `gpuNumReq` | int | 申请的 GPU 数 | `0` |
| `dcuNumReq` | int | 申请的 DCU 数 | `0` |

#### 资源配置与使用

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `memUsed` | string | 已使用内存（如 `7600M`） | `"7600M"` |
| `vmemUsed` | string \| null | 已使用虚拟内存 | `null` |
| `wallTime` | string | 已运行时长（同 `jobRunTime`） | `"00:00:21"` |
| `cpuTime` | string | CPU 时间 | `""` |
| `exitCode` | string | 退出码 | `""` |

#### 调度器执行信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `execHost` | string | 执行主机名 | `"b08r1n14"` |
| `execGpus` | string \| null | 分配的 GPU 设备 | `null` |
| `software` | string \| null | 软件信息 | `null` |
| `outPath` | string | 标准输出路径（同 `outputPath`） | `"/work/.../std.out.63436"` |
| `vncSessionInfo` | object \| null | VNC 会话信息（同 `jobVncSessionInfo`） | `null` |

#### 内部/冗余字段（与核心字段重复）

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

> 这些内部字段与核心字段值相同，MCP 工具实现时以核心字段（`jobId`/`jobName`/`jobManagerId`/`jobState`/`jobQueueTime`/`jobStartTime`）为准。

---

## 调用示例

### curl: 查询所有区域运行中的作业（默认字段）

```bash
curl -X POST 'https://www.scnet.cn/ac/openapi/v2/jobs/monitor/page-list' \
  -H 'token: <ac_token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "page": 1,
    "size": 10,
    "clusterId": "",
    "queue": "",
    "jobState": "statR",
    "startTime": "",
    "endTime": "",
    "showGroupJobs": false,
    "clusterUserName": "",
    "showAllData": false
  }'
```

### curl: 查询完整数据（showAllData=true）

```bash
curl -X POST 'https://www.scnet.cn/ac/openapi/v2/jobs/monitor/page-list' \
  -H 'token: <ac_token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "page": 1,
    "size": 20,
    "clusterId": "",
    "queue": "",
    "jobState": "",
    "startTime": "",
    "endTime": "",
    "showGroupJobs": false,
    "clusterUserName": "",
    "showAllData": true
  }'
```

### Python: 查询实时作业（含状态校正）

```python
import json
import urllib.request
import ssl
from typing import Optional


def query_realtime_jobs(
    token: str,
    page: int = 1,
    size: int = 10,
    cluster_id: str = "",
    queue: str = "",
    job_state: str = "",
    start_time: str = "",
    end_time: str = "",
    show_group_jobs: bool = False,
    cluster_user_name: str = "",
    show_all_data: bool = False,
) -> dict:
    """查询实时作业列表（跨区域聚合）"""
    url = "https://www.scnet.cn/ac/openapi/v2/jobs/monitor/page-list"
    body = {
        "page": page,
        "size": size,
        "clusterId": cluster_id,
        "queue": queue,
        "jobState": job_state,
        "startTime": start_time,
        "endTime": end_time,
        "showGroupJobs": show_group_jobs,
        "clusterUserName": cluster_user_name,
        "showAllData": show_all_data,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"token": token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, context=ssl.create_default_context()) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if str(result.get("code")) == "0":
            return result
        raise RuntimeError(f"查询失败 [{result.get('code')}]: {result.get('msg')}")


def resolve_realtime_status(job: dict) -> str:
    """
    解析实时作业的真实状态。

    平台 jobState 可能延迟同步。
    当 jobState=statC 但底层调度器状态为 FAILED 时，真实状态为 statD（失败）。
    showAllData=false 时通过 reason 字段判断；showAllData=true 时优先用 initContentAttr。
    """
    status = job.get("jobState", "")

    # showAllData=true: 解析 initContentAttr
    init_raw = job.get("initContentAttr", "")
    if init_raw:
        try:
            init_attr = json.loads(init_raw)
            inner_state = init_attr.get("JobState", "")
        except (json.JSONDecodeError, TypeError):
            inner_state = ""
    else:
        inner_state = ""

    reason = job.get("reason", "")

    if status == "statC" and (inner_state == "FAILED" or reason == "NonZeroExitCode"):
        return "statD"
    return status


# 使用示例
result = query_realtime_jobs(
    token="<ac_token>",
    job_state="statR",
    show_all_data=True,
)
records = result["data"]["records"]
total = result["data"]["total"]
print(f"共 {total} 条运行中作业，当前页 {len(records)} 条")
for job in records:
    real_status = resolve_realtime_status(job)
    print(
        f"  [{real_status}] {job['jobId']} {job['jobName']} "
        f"队列:{job['queue']} 节点:{job['nodeUsed']} "
        f"运行:{job['jobRunTime']} ({job['clusterName']})"
    )
```

---

## 与历史作业接口的对比

| 对比项 | 实时作业接口 | 历史作业接口 |
|--------|-------------|-------------|
| 路径 | `/ac/openapi/v2/jobs/monitor/page-list` | `/ac/openapi/v2/jobs/history/page-list` |
| 作业范围 | 运行中、排队中、挂起、保留 | 已完成、失败、超时、取消等终态 |
| 状态字段 | `jobState` | `jobState` |
| 运行时长 | `jobRunTime`（`HH:MM:SS` 或 `D-HH:MM:SS` 格式） | `jobWalltimeUsed`（秒，整数） |
| 节点字段 | `nodeUsed`（字符串） + `nodeNumUsed`（整数） | `nodect` + `nodeNumUsed` |
| 用户字段 | `clusterUserName` | `userName` / `clusterUserName` |
| 输出文件 | `outputPath` / `errorPath` | `stdout` / `stderr` |
| VNC 信息 | `jobVncSessionInfo` | 无 |
| 状态校正 | 需结合 `initContentAttr.JobState` 或 `reason` 校正状态同步延迟 | 无需校正（终态） |
| 入参结构 | 相同 | 相同 |

---

## 注意事项

1. **AC Token 必须可用**: 调用前需确保 AC 认证 token 已初始化
2. **跨区域聚合**: `clusterId` 为空时返回所有区域的作业，结果按 `clusterName` 分组展示
3. **状态同步延迟**: 平台 `jobState` 可能延迟同步。当 `jobState=statC` 但 `initContentAttr.JobState=FAILED` 或 `reason=NonZeroExitCode` 时，真实状态应为失败（`statD`）。MCP 工具做状态判断时建议实现 `resolve_realtime_status` 校正逻辑
4. **showAllData 影响**: `false` 时返回核心字段子集；`true` 时额外返回 `initContentAttr`（调度器原始状态）、资源请求量、内部冗余字段等。做状态校正时建议使用 `showAllData=true`
5. **数据兼容**: `data.records` 和 `data.list` 均可取值，`data.pages` 字段仅在实时作业接口中存在
6. **终态作业**: 已完成/失败/超时等终态作业应使用历史作业接口（`/jobs/history/page-list`）查询
7. **重试策略**: 网络异常（SSL、连接重置、超时等）最多重试 2 次，递增延迟 0.5s~1s
