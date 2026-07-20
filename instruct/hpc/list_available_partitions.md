# hpc_hpc_list_available_partitions

## 需求

实现一个 MCP tool `hpc_hpc_list_available_partitions`，列出当前用户在所有集群中真正可用的队列分区，过滤掉无可用资源的队列和无队列的集群。

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `users` 表中查询对应用户的 `acToken`
- 如果用户未认证，返回错误提示 JSON，包含 `auth_url` 字段

## 数据源

- 从 `user_cluster` 表 LEFT JOIN `cluster_url` 表，获取当前用户所有集群的 clusterId、clusterName、token、hpcUrls
- 如果用户没有任何集群记录，返回空列表 `[]`

## 处理流程（每个集群）

### 1. 前置检查
- 查询 DB `user_cluster` 表，只取 `JobManagerid IS NOT NULL` 且非空的集群
- 无 `hpcUrls` 的集群 → 静默跳过（`continue`），不报错

### 2. 获取调度器 ID
- 直接使用 DB 中已存储的 `JobManagerid`（认证时已获取并缓存），不再调用 cluster API
- 过滤空 URL 后，按轮询（round-robin）选取一个 URL 作为 base_url

### 3. 获取用户队列
- GET `{base_url}/hpc/openapi/v2/queuenames/users/{username}?strJobManagerID={job_manager_id}`
- header 同上

### 4. 过滤队列
- 从返回的 data 中提取 queues（可能是 list 或单个 dict）
- **过滤规则**：只保留 `queFreeNcpus != 0` 的队列
- 从每个队列对象中移除 `aclHosts` 字段
- 过滤后 queues 为空 → 静默跳过该集群，不加入结果

### 5. 异常处理
- 任何 HTTP 请求异常 → 静默 `continue`，不报错

## 返回值

- 返回 `list[dict]`，每个元素包含：`clusterId`、`clusterName`、`jobManagerID`、`queues`
- 只包含有可用队列的集群
- 无可用集群时返回空列表 `[]`

## 自动注册

- 从结果第一条数据通过 `_build_return_schema()` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `hpc_hpc_list_available_partitions`

## 设计原则

- 静默过滤：不可用的队列不显示，没有队列的集群不显示，没有调度器的集群不显示
- 不报错：所有异常情况静默跳过，调用方只看到真正可用的资源

## 代码位置

`main.py` 中 `@mcp.tool()` 装饰的 `hpc_hpc_list_available_partitions` 函数。
