# hpc_submit_job

## 需求

实现一个 MCP tool `hpc_submit_job`，根据用户需求和集群队列信息，向 HPC 集群提交一个作业。

## 前置条件

- 调用本工具前，必须先调用 `hpc_hpc_list_available_partitions` 工具获取可用队列信息
- 根据用户需求（如计算资源大小、队列类型等），从返回结果中选择一个最合适的队列

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `users` 表中查询对应用户的 `acToken`
- 如果用户未认证（无 acToken），返回错误提示 JSON，包含 `auth_url` 字段指向认证页面

## API 调用结构

**重要**：官方 API 请求体为**两层嵌套结构**，顶层为 `apptype`、`appname`、`strJobManagerID`，作业参数嵌套在 `mapAppJobInfo` 对象内。

```
POST {hpcUrls}/hpc/openapi/v2/apptemplates/{apptype}/{appname}/job
Headers: token
Body:
{
  "apptype": "BASIC",
  "appname": "BASE",
  "strJobManagerID": 1638523853,
  "mapAppJobInfo": {
    "GAP_CMD_FILE": "sleep 500",
    "GAP_NNODE": "1",
    "GAP_NODE_STRING": "",
    "GAP_SUBMIT_TYPE": "cmd",
    "GAP_JOB_NAME": "STDIN_0910_094758",
    "GAP_WORK_DIR": "/public/home/test/BASE/STDIN_0531_134514",
    "GAP_QUEUE": "debug2",
    "GAP_NPROC": "1",
    "GAP_PPN": "",
    "GAP_NGPU": "",
    "GAP_NDCU": "",
    "GAP_JOB_MEM": "",
    "GAP_WALL_TIME": "24:00:00",
    "GAP_EXCLUSIVE": "",
    "GAP_APPNAME": "BASE",
    "GAP_MULTI_SUB": "",
    "GAP_STD_OUT_FILE": "/public/home/test/BASE/STDIN_0531_134514/std.out.%j",
    "GAP_STD_ERR_FILE": "/public/home/test/BASE/STDIN_0531_134514/std.err.%j"
  }
}
```

## MCP 工具参数（用户传入）

| 参数名 | 类型 | 必填 | 默认值 / 来源说明 |
|--------|------|------|-------------------|
| `clusterId` | integer | 是 | 从 `hpc_hpc_list_available_partitions` 返回结果中选定的集群 ID |
| `GAP_QUEUE` | string | 是 | 队列名称。可从 `hpc_list_available_partitions` 返回的 `queues` 列表中获取（对应 `queueName` 字段） |
| `GAP_CMD_FILE` | string | 是 | 命令行内容（如需换行，请使用 `\n`）。例如：`sleep 500`、`python train.py` |
| `GAP_NNODE` | string | 否 | 节点个数。与 `GAP_NODE_STRING` 互斥——两者不能同时填写有效值。默认 `"1"` |
| `GAP_NODE_STRING` | string | 否 | 指定具体节点。与 `GAP_NNODE` 互斥——两者不能同时填写有效值。默认 `""` |
| `GAP_WALL_TIME` | string | 否 | 最大运行时长，格式 `HH:MM:ss`。默认 `"24:00:00"` |
| `GAP_NPROC` | string | 否 | 总核心数（`GAP_NPROC` 和 `GAP_PPN` 选其一填写） |
| `GAP_PPN` | string | 否 | CPU核心/节点（`GAP_NPROC` 和 `GAP_PPN` 选其一填写） |
| `GAP_NGPU` | string | 否 | GPU卡数/节点 |
| `GAP_NDCU` | string | 否 | DCU卡数/节点 |
| `GAP_JOB_MEM` | string | 否 | 每个节点内存值，单位 MB 或 GB |
| `GAP_EXCLUSIVE` | string | 否 | 是否独占节点，`1` 为独占，空字符串为非独占 |
| `GAP_WORK_DIR` | string | 否 | 工作路径。若未提供，默认为 `user_cluster` 表中该用户的 `homePath` + `/_job_YYYY_mm_dd_HHiiss` |
| `GAP_APPNAME` | string | 否 | BASE（基础应用），支持填写具体的应用英文名称。默认 `"BASE"` |
| `GAP_MULTI_SUB` | string | 否 | 作业组长度，建议为小于等于 50 的正整数 |
| `GAP_STD_OUT_FILE` | string | 否 | 标准输出文件路径。若未提供，默认为 `{GAP_WORK_DIR}/std.out.%j` |
| `GAP_STD_ERR_FILE` | string | 否 | 标准错误文件路径。若未提供，默认为 `{GAP_WORK_DIR}/std.err.%j` |

## 后端处理逻辑

1. **参数校验**：
   - `GAP_CMD_FILE` 不能为空，否则返回错误
   - `GAP_NNODE` 与 `GAP_NODE_STRING` 不能同时填写有效值（互斥校验）
   - `GAP_NPROC` 与 `GAP_PPN` 不能同时填写有效值（互斥校验）
2. **认证校验**：检查 `users` 表中是否有 `acToken`
3. **获取集群凭据**：根据 `clusterId` + `username` 从 `user_cluster` 表查询 `token`
4. **homePath 校验**：用户的 `homePath` 不能为空，否则返回错误（防止路径安全漏洞）
5. **获取调度器 ID**：从 `cluster_url` 表获取 `hpcUrls`，按轮询（round-robin）选取一个作为 `base_url`（而非随机），调用 `GET {base_url}/hpc/openapi/v2/cluster` 获取 `jobManagerID`
6. **生成 GAP_JOB_NAME**：根据用户的 `GAP_CMD_FILE` 提取第一个词 + 时间戳作为作业名称
7. **填充默认值**：
   - `GAP_NNODE`：若未传值，使用 `"1"`
   - `GAP_NODE_STRING`：若未传值，使用 `""`
   - `GAP_WALL_TIME`：若未传值，使用 `"24:00:00"`
   - `GAP_APPNAME`：若未传值，使用 `"BASE"`
   - `GAP_WORK_DIR`：若未传值，拼接 `homePath + "/_job_" + 当前时间戳(YYYY_mm_dd_HHiiss)`
   - `GAP_STD_OUT_FILE`：若未传值，使用 `{GAP_WORK_DIR}/std.out.%j`
   - `GAP_STD_ERR_FILE`：若未传值，使用 `{GAP_WORK_DIR}/std.err.%j`
8. **构建嵌套请求体**：按官方 API 结构组装，顶层包含 `apptype`、`appname`、`strJobManagerID`，作业参数放入 `mapAppJobInfo`
9. **提交作业**：POST 到 `{base_url}/hpc/openapi/v2/apptemplates/{apptype}/{appname}/job`

## API 调用

- URL: `{base_url}/hpc/openapi/v2/apptemplates/{apptype}/{appname}/job`
  - `{base_url}` 为从 `cluster_url` 表中获取的 `hpcUrls` 中按轮询选取的 URL
  - `{apptype}` 默认为 `BASIC`
  - `{appname}` 默认为 `BASE`
- Method: POST
- Headers: `{"token": token, "Content-Type": "application/json"}`
- Body: 按官方文档的两层嵌套结构构建（见上方 API 调用结构示例），其中 `strJobManagerID` 为数字类型（Long），其余均为字符串
- 超时 30s

## 异常处理

- **无可用队列**：若用户未提供 `clusterId`，或 `user_cluster` 中无对应 `clusterId` 的 token，返回错误提示，说明无可提交作业的集群
- **无调度器**：若从 `cluster_url` 表中获取 `hpcUrls` 失败，或调用 cluster API 获取 `jobManagerID` 失败，返回适当提示
- **其他错误**：捕获 HTTP 异常或 API 返回的错误码，返回易于理解的中文提示信息

## 返回值

- 直接返回 API 响应的 JSON 数据（通常包含 `jobID`、`status` 等字段）
- 将入参的token和hpcUrls作为hpcUrl加入到返回值中

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `hpc_submit_job`
- document JSON 包含 url、method、description、parameters（含所有参数的 schema）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中 `@mcp.tool()` 装饰的 `hpc_submit_job` 函数。
