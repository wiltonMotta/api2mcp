# 聚合检索历史作业列表接口

## 概述

跨区域聚合查询历史作业列表。通过 AC 服务统一入口，单次请求返回所有区域的作业记录，无需逐区域遍历。

- **服务地址**: `https://www.scnet.cn`
- **接口路径**: `POST /ac/openapi/v2/jobs/history/page-list`
- **认证方式**: AC Token（请求头 `token` 字段）
- **超时建议**: 5 秒
- **默认时间范围**: 最近 7 天（起始日 `00:00:00` ~ 当前时间）

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
| `jobState` | string | 否 | `""` | 作业状态筛选，传空表示所有状态。取值: `statE`(退出), `statC`(完成), `statDE`(取消), `statD`(失败), `statT`(超时), `statN`(节点异常), `statRQ`(重新运行) |
| `startTime` | string | 否 | 7天前 `00:00:00` | 查询开始时间，格式 `YYYY-MM-DD HH:MM:SS` |
| `endTime` | string | 否 | 当前时间 | 查询结束时间，格式 `YYYY-MM-DD HH:MM:SS` |
| `showGroupJobs` | boolean | 否 | `false` | 是否展示组内所有成员作业 |
| `clusterUserName` | string | 否 | `""` | 按用户名筛选作业，传空不过滤 |
| `showAllData` | boolean | 否 | `false` | 是否返回所有字段 |

### 作业状态枚举（jobState）

| 值 | 含义 |
|----|------|
| `statE` | 退出 |
| `statC` | 完成 |
| `statDE` | 取消 |
| `statD` | 失败 |
| `statT` | 超时 |
| `statN` | 节点异常 |
| `statRQ` | 重新运行 |

> 以上为历史作业 API 的状态值。注意：API 返回的 `jobState` 字段可能返回全大写语义值（如 `TIMEOUT`），具体以实际返回为准。

---

## 响应格式

### 成功响应

showAllData为true时：
```json
{
    "code": "0",
    "msg": "success",
    "data": {
        "total": 41,
        "pages": 41,
        "records": [
            {
                "jobId": "6508505",
                "jobName": "hello_job",
                "clusterUserName": "ac1npa3sf2",
                "jobManagerId": 1719103188,
                "jobManagerName": "Cluster_admin01",
                "clusterId": 20058,
                "clusterName": "西南一区【四川】",
                "appType": "BASE",
                "queue": "comp",
                "jobExitStatus": 0,
                "nodEct": 1,
                "jobQueueTime": "2026-03-23 18:11:47",
                "jobEndTime": "2026-03-23 18:11:49",
                "jobWallTimeUsed": 1,
                "workDir": "/work/home/ac1npa3sf2/jobs",
                "jobState": "COMPLETED",
                "taskType": "HPC",
                "tags": null,
                "nodeNumUsed": 1,
                "procNumUsed": 1,
                "dcuNumUsed": 0,
                "gpuNumUsed": 0,
                "jobQueueTimeUsed": 0,
                "jobStartTime": "2026-03-23 18:11:48",
                "stdout": "/work/home/ac1npa3sf2/jobs/std.out.6508505",
                "stderr": "/work/home/ac1npa3sf2/jobs/std.err.6508505",
                "acctTime": "2026-03-23T10:12:25.000+0000",
                "userName": "ac1npa3sf2",
                "groupName": "ac1npa3sf2",
                "walltime": 300,
                "jobWaitTime": 1,
                "jobResponseTime": 2,
                "jobCpuTime": 0,
                "jobDcuNum": 0,
                "jobProcNum": 1,
                "jobGpuNum": 0,
                "jobMluNum": 0,
                "scale": "A",
                "account": "ac1npa3sf2",
                "jobExecHost": "mlu5",
                "jobExecGpus": null,
                "jobMemUsed": 2348,
                "jobVmemUsed": 501180,
                "reqCpu": 1.0,
                "reqGpu": 0.0,
                "reqDcu": 0.0,
                "reqMlu": 0,
                "reqMem": "7800M",
                "reqNodes": 1.0,
                "needNodes": "mlu5",
                "command": "/work/home/ac1npa3sf2/jobs/job_BASE.slurm",
                "isSinglejob": false,
                "historyQueuerate": 1.0,
                "historyAccount": "ac1npa3sf2",
                "jobExtAttr": null,
                "jobExecCpus": null,
                "jobExecDcus": null,
                "jobExecMlus": null
            }
        ]
    }
}
```
不传showAllData，或为false时：
```
{
    "code": "0",
    "msg": "success",
    "data": {
        "total": 1,
        "pages": 1,
        "records": [
            {
                "jobId": "6508505",
                "jobName": "hello_job",
                "clusterUserName": "ac1npa3sf2",
                "jobManagerId": 1719103188,
                "jobManagerName": "Cluster_admin01",
                "clusterId": 20058,
                "clusterName": "西南一区【四川】",
                "appType": "BASE",
                "queue": "comp",
                "jobExitStatus": 0,
                "nodEct": 1,
                "jobQueueTime": "2026-03-23 18:11:47",
                "jobEndTime": "2026-03-23 18:11:49",
                "jobWallTimeUsed": 1,
                "workDir": "/work/home/ac1npa3sf2/jobs",
                "jobState": "statC",
                "taskType": "HPC",
                "tags": null,
                "nodeNumUsed": 1,
                "procNumUsed": 1,
                "dcuNumUsed": 0,
                "gpuNumUsed": 0,
                "jobQueueTimeUsed": 1,
                "jobStartTime": "2026-03-23 18:11:48",
                "stdout": "/work/home/ac1npa3sf2/jobs/std.out.6508505",
                "stderr": "/work/home/ac1npa3sf2/jobs/std.err.6508505"
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
```

> - `code` 为 `"0"`（字符串类型）表示成功，非 `"0"` 表示失败
> - 数据在 `data.records` 中，`data.total` 为符合条件的总记录数
> - 网络异常时 `code` 为 `-1`，`msg` 包含具体错误信息

---

## 响应字段说明

### 顶层结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `msg` | string | 响应消息，成功时返回 `success` |
| `code` | string | 状态码，`"0"` 表示成功 |
| `data` | object | 数据体 |
| `data.records` | array | 作业记录列表 |
| `data.total` | integer | 符合筛选条件的总记录数 |
| `data.pages` | integer | 符合筛选条件的总页数 |


### 作业记录对象（records[]）

#### 标识信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `jobId` | string | 作业 ID | `"12"` |
| `jobName` | string | 作业名 | `"STDIN_1104_175546"` |
| `userName` | string | 用户名 | `"test"` |
| `groupName` | string | 用户组名 | `"test"` |
| `clusterUserName` | string | 集群用户名 | `"ac1npa3sf2"` |
| `owner` | string | 作业拥有者 | `"test"` |
| `account` | string | 计费账号 | `"test"` |
| `historyAccount` | string | 作业运行结束时提交者所属的账号 | `"test"` |

#### 集群信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `clusterId` | integer | 区域/集群 ID | `11250` |
| `clusterName` | string | 区域/集群名称 | `"华东一区【昆山】"` |
| `jobmanagerId` | long | 区域/调度器 ID | `1634819344` |
| `jobmanagerName` | string | 区域/调度器名称 | `"Cluster_node248"` |

#### 作业状态与分类

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `jobState` | string | 作业状态：`statE`(退出), `statC`(完成), `statDE`(取消), `statD`(失败), `statT`(超时), `statN`(节点异常), `statRQ`(重新运行) | `"statC"` |
| `jobExitStatus` | long | 作业退出代码，0 为正常 | `0` |
| `appType` | string | 应用类型 | `"BASE"` |
| `taskType` | string | 任务类型 | `"HPC"` |
| `queue` | string | 作业提交队列 | `"debug"` |
| `scale` | string | 作业规模 | `""` |
| `isSinglejob` | boolean | 是否为独占节点的作业 | `false` |
| `tags` | string \| null | 标签 | `null` |
| `jobExtAttr` | any \| null | 作业扩展属性 | `null` |
| `startCount` | string | 作业的启动次数 | `""` |

#### 时间信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `jobQueueTime` | date | 作业入队时间，对应记账属性 qtime | `"2021-11-04T17:55:49"` |
| `jobStartTime` | date | 作业启动时间，对应属性 start | `"2021-11-04T17:55:49"` |
| `jobEndTime` | date | 作业结束时间，对应属性 end | `"2021-11-04T18:04:10"` |
| `acctTime` | string | 记账时间 | `"2021-11-03T13:56:56"` |
| `walltime` | long | 提交作业时请求的 Walltime 或系统默认的 Walltime（秒） | `86400` |
| `jobWalltimeUsed` | long | 实际使用的 Walltime（秒） | `501` |
| `jobQueueTimeUsed` | long | 排队等待时间（秒） | `0` |
| `jobWaitTime` | long | 作业等待时间 = jobStartTime - jobQueueTime（秒） | `0` |
| `jobResponseTime` | long | 作业响应时间 = jobEndTime - jobQueueTime（秒） | `501` |
| `jobCpuTime` | long | 作业占用的 CPU 时间（秒） | `501` |

#### 资源使用量

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `nodect` | long | 分配的节点数 | `1` |
| `nodeNumUsed` | long | 实际使用节点数 | `1` |
| `procNumUsed` | long | 使用处理器数（核数） | `1` |
| `dcuNumUsed` | long | 使用 DCU 加速卡数 | `0` |
| `gpuNumUsed` | long | 使用 GPU 卡数 | `0` |
| `jobDcuNum` | long | 作业使用的 DCU 数 | `0` |
| `jobProcNum` | long | 作业使用的处理器数（核数） | `1` |
| `jobGpuNum` | long | 作业使用的 GPU 核数 | `0` |
| `jobMluNum` | long | 作业使用的 MLU 数 | `0` |
| `jobMemUsed` | long | 作业使用的物理内存数（KB） | `3972` |
| `jobVmemUsed` | long | 作业使用的虚拟内存数（KB） | `1435680` |

#### 共享 / 独占资源细分

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `shareCputime` | long | 共享作业的 CPU 时间（秒），非独占作业时有值，否则为 0 | `501` |
| `shareMem` | long | 共享作业的内存（KB），非独占作业时有值，否则为 0 | `3972` |
| `shareWalltime` | long | 共享作业的 Walltime（秒），非独占作业时有值，否则为 0 | `501` |
| `exclusiveCputime` | long | 独占作业的 CPU 时间（秒），独占作业时有值，否则为 0 | `0` |
| `exclusiveMem` | long | 独占作业的内存（KB），独占作业时有值，否则为 0 | `0` |
| `exclusiveWalltime` | long | 独占作业的 Walltime（秒），独占作业时有值，否则为 0 | `0` |

#### 资源请求量

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `jobReqCpu` | double | 申请 CPU 核心数 | `1` |
| `jobReqGpu` | double | 申请 GPU 卡数 | `0` |
| `jobReqDcu` | double | 申请 DCU 卡数 | `0` |
| `jobReqMem` | string | 申请内存大小 | `"2153M"` |
| `jobReqNodes` | double | 申请节点数 | `1` |
| `reqMlu` | integer | 申请 MLU 卡数 | `0` |

#### 计费信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `cpuNuclearHour` | string | CPU 核时 | `"0.1392"` |
| `cpuNuclearSec` | string | CPU 核秒 | `"501"` |
| `cpuUnitPrice` | double | 作业提交时 CPU 单价 | `1` |
| `dcuCardHour` | string | DCU 卡时 | `"0"` |
| `dcuCardSec` | string | DCU 卡秒 | `"0"` |
| `dcuUnitPrice` | double | 作业提交时 DCU 单价 | `1` |
| `gpuCardHour` | string | GPU 卡时 | `"0"` |
| `gpuCardSec` | string | GPU 卡秒 | `"0"` |
| `gpuUnitPrice` | double | 作业提交时 GPU 单价 | `1` |
| `efficiencyCpu` | string | CPU 效率 | `"100.00%"` |
| `goldenable` | string | 作业提交时的计费状态 | `"true"` / `"false"` / `"unknown"` |
| `historyQueuerate` | string | 作业提交时的队列费率 | `"1"` |

#### 执行环境

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `workdir` | string | 作业的工作路径 | `"/public/home/test/BASE/STDIN_1104_175546"` |
| `command` | string | 作业脚本的位置 | `"/public/home/test/BASE/STDIN_1104_175546/job_BASE.slurm"` |
| `commandExist` | string | 作业脚本是否存在 | `"true"` |
| `stdout` | string | 标准输出文件路径 | `"/public/home/.../std.out.110200796"` |
| `stderr` | string | 标准错误文件路径 | `"/public/home/.../std.err.110200796"` |
| `needNodes` | string | 分配的节点名或节点数 | `"node248"` |
| `jobExecHost` | string | 作业执行节点 | `"node248"` |
| `jobExecGpus` | string \| null | 作业占用的 GPU 节点 | `null` |
| `jobExecCpus` | string \| null | 作业占用的 CPU 核心 | `null` |
| `jobExecDcus` | string \| null | 作业占用的 DCU 节点 | `null` |
| `jobExecMlus` | string \| null | 作业占用的 MLU 节点 | `null` |

---

## 调用示例

### curl: 查询所有区域最近 7 天已完成作业

```bash
curl -X POST 'https://www.scnet.cn/ac/openapi/v2/jobs/history/page-list' \
  -H 'token: <ac_token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "page": 1,
    "size": 10,
    "clusterId": "",
    "queue": "",
    "jobState": "statC",
    "startTime": "",
    "endTime": "",
    "showGroupJobs": false,
    "clusterUserName": "",
    "showAllData": false
  }'
```

### curl: 查询指定区域超时作业（含全部字段）

```bash
curl -X POST 'https://www.scnet.cn/ac/openapi/v2/jobs/history/page-list' \
  -H 'token: <ac_token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "page": 1,
    "size": 20,
    "clusterId": "11250",
    "queue": "comp",
    "jobState": "statT",
    "startTime": "2026-01-01 00:00:00",
    "endTime": "2026-06-30 23:59:59",
    "showGroupJobs": false,
    "clusterUserName": "",
    "showAllData": true
  }'
```

### Python: 分页查询

```python
import json
import urllib.request
import ssl
from typing import Optional

def query_history_jobs(
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
    """查询历史作业列表（跨区域聚合）"""
    url = "https://www.scnet.cn/ac/openapi/v2/jobs/history/page-list"
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

# 使用示例
result = query_history_jobs(
    token="<ac_token>",
    job_state="statT",
    start_time="2026-01-01 00:00:00",
    end_time="2026-06-30 23:59:59",
    show_all_data=True,
)
jobs = result["data"]["records"]
total = result["data"]["total"]
print(f"共 {total} 条，当前页 {len(jobs)} 条")
```

---

## 注意事项

1. **AC Token 必须可用**: 调用前需确保 AC 认证 token 已初始化，否则返回认证错误
2. **跨区域聚合**: `clusterId` 为空时返回所有区域的作业，结果按 `clusterName` 分组展示
3. **时间范围**: 未提供 `startTime`/`endTime` 时默认仅返回最近 7 天数据
4. **字段裁剪**: `showAllData=false` 时返回核心字段子集，设为 `true` 获取全部字段（含计费、独占/共享细分等）
5. **jobState 入参**: 使用标准状态码（`statC`/`statT` 等），注意与 API 返回的实际值可能格式不同
6. **类型注意**: `code` 和 `jobId` 为字符串类型；`clusterId`、`jobmanagerId`、资源量字段为数值类型
7. **共享/独占资源**: 非独占作业时 `shareCputime`/`shareMem`/`shareWalltime` 有值；独占作业时 `exclusiveCputime`/`exclusiveMem`/`exclusiveWalltime` 有值
