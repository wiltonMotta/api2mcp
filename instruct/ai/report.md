# instruct/ai/ 设计文档评审报告

## 一、严重问题

### 1.1 Token 类型错误：AC 平台 URL 使用了集群 token 而非 acToken

这是最严重的 bug。3 个工具使用了 `https://www.scnet.cn/ac/openapi/v2/...`（平台级 AC URL），但文档指定从 `_get_default_token()` 获取集群 token，而非从 `users` 表获取 `acToken`。

`main.py` 中已有明确惯例：

| URL 类型 | Token 来源 | 已有代码示例 |
|----------|-----------|-------------|
| `www.scnet.cn/ac/openapi/v2/...` | `users.acToken` | `get_user_info` (L830), `list_history_jobs` (L1707), `list_running_jobs` (L1841) |
| `{hpcUrls}/...` / `{aiUrls}/...` / `{efileUrls}/...` | `user_cluster.token` | `submit_job`, `efile_list_files`, `get_running_job_detail` |

**违反此规则的 3 个文档：**

| 文档 | URL | 文档声称的 token 来源 | 应使用的 token |
|------|-----|---------------------|---------------|
| `notebook_create` | `www.scnet.cn/ac/openapi/v2/notebook/actions/create` | `_get_default_token()` (集群 token) | `users.acToken` |
| `notebook_start` | `www.scnet.cn/ac/openapi/v2/notebook/actions/start` | `_get_default_token()` (集群 token) | `users.acToken` |
| `notebook_list_resources` | `www.scnet.cn/ac/openapi/v2/resources/accelerators` | `_get_default_token()` (集群 token) | `users.acToken` |

**修复建议:** 这三个工具应改为从 `users` 表读取 `acToken`，参考 `list_history_jobs` (main.py:1703-1722) 的实现模式。

### 1.2 URL 路由不一致：同类操作使用不同的 API 入口

Notebook 生命周期操作被分裂到两套不同的 URL 入口，且缺乏说明：

| 操作 | 使用 URL | 类型 |
|------|---------|------|
| `notebook_create` | `www.scnet.cn/ac/...` | 平台级 AC URL |
| `notebook_start` | `www.scnet.cn/ac/...` | 平台级 AC URL |
| `notebook_stop` | `{aiUrls}/ai/...` | 集群级 aiUrls |
| `notebook_release` | `{aiUrls}/ai/...` | 集群级 aiUrls |
| `notebook_rename` | `{aiUrls}/ai/...` | 集群级 aiUrls |

同为生命周期操作，create/start 走 AC 平台，但 stop/release/rename 走集群 aiUrls。这种不对称需要解释，否则实现者无法判断是设计意图还是文档错误。

**建议:** 在每个文档中增加一个标注，说明为什么该操作使用特定 URL 入口（平台级 vs 集群级），以及该选择背后的架构决策（例如：AC 平台统一调度创建请求，集群直连更高效地执行状态变更）。

---

## 二、API 设计一致性问题

### 2.1 参数类型不一致

**`page` / `size` 类型：**

| 工具 | `page` 类型 | `size` 类型 |
|------|-----------|-----------|
| `notebook_list` | `integer` | `integer` |
| `notebook_list_model_images` | `string` | `string` |

同一分页概念用了不同数据类型。如果上游 API 接受 string，建议 MCP 层面统一为 `integer` 并在后端转换。

**`notebook_id` 的 API body 字段名不统一：**

| 工具 | 上游 API body 中的字段名 |
|------|------------------------|
| `notebook_start` | `notebookId` |
| `notebook_stop` | `notebookId` |
| `notebook_release` | `id` |
| `notebook_rename` | `id` |
| `notebook_start_custom_service` | `id` |

5 个操作引用同一实体 ID，但上游 API 字段名有两种写法。如果这是上游 API 的真实差异，应在每个文档中显式标注"注意：此 API 使用 `id` 而非 `notebookId`"。如果是文档书写错误，应统一核实修正。

### 2.2 输出字段命名：表格 snake_case vs 示例 camelCase

文档中普遍存在输出参数表使用 snake_case，而 JSON 返回值示例使用 camelCase 的问题：

| 文档 | 参数表写法 | JSON 示例写法 |
|------|-----------|-------------|
| `notebook_create` | `task_id`, `notebook_id` | `taskId`, `notebookId` |
| `notebook_detail` | `notebook_name`, `notebook_status`, `image_name`... | `notebookName`, `notebookStatus`, `imageName`... |
| `notebook_start_custom_service` | `exec_success`, `error_msg` | `execSuccess`, `errorMsg` |

`efile_list_files` (main.py:2214-2244) 已建立了明确的 camelCase→snake_case 映射模式，但 ai 系列文档没有说明是否要沿用此策略。如果沿用，需要在"后端处理逻辑"中增加字段映射步骤；如果不沿用（直接透传），则输出参数表应使用 camelCase 与 API 保持一致。

**建议:** 统一决策——要么所有工具透传上游字段名（输出参数表也用 camelCase），要么统一做 snake_case 映射（需要增加代码逻辑，输出参数表用 snake_case）。推荐透传以减少维护成本。

### 2.3 `notebook_list_images` 必填参数过多

`notebook_list_images` 将 `access`、`order_by`、`sort`、`start`、`limit` 全部标为"必填"，但作为"列表查询"工具，用户期望有合理默认值：

| 参数 | 当前 | 建议 |
|------|------|------|
| `access` | 必填 | 默认 `"public"` |
| `order_by` | 必填 | 默认 `"create_time"` |
| `sort` | 必填 | 默认 `"DESC"` |
| `start` | 必填 | 默认 `0` |
| `limit` | 必填 | 默认 `20` |

对比 `notebook_list_model_images` 已将 `page`/`size` 设为可选（有默认值），`notebook_list_images` 应与之对齐。

### 2.4 `notebook_start` 缺少 `cluster_id` 参数

`notebook_start` 是 13 个工具中唯一完全不接受 `cluster_id` 的（除仅需 token 的工具外）。如果平台级 AC URL 能自动路由到正确集群，则无需 `cluster_id`；但此假设应明确记录。

### 2.5 `notebook_list_resources` 的 `cluster_ids` 参数类型

`cluster_ids` 标注为 `array[string]`，但实际传递时以逗号分隔字符串形式放入 query params。MCP 层面应接受 `string`（逗号分隔）或 `array[string]`（后端 join），需明确策略并统一。

---

## 三、文档完整性问题

### 3.1 缺少错误码表

与 efile 系列文档不同，13 个 ai 文档几乎都没有常见错误码表。建议至少为高频操作（create、start、stop、release）补充错误码参考。

### 3.2 认证逻辑重复

13 个文档各自重复了认证 SQL / `_get_default_token` / round-robin 描述。与 efile 系列存在相同问题——应提取公共章节。

### 3.3 `notebook_start_custom_service` 的返回值语义不清晰

返回值示例中 `execSuccess: false` 但 `msg: "success"`，文档注释说"服务通路的建立不依赖于端口检测结果"。这让调用方难以判断操作是否真正成功——不能仅靠 `code === "0"` 判断，还需解读 `data.execSuccess` 和 `data.errorMsg`。这个特殊语义需要在输出参数表中显式说明。

### 3.4 `notebook_detail` 输出字段可能不完整

输出参数表列出了约 30 个字段，但按上游 API 返回值的可变性，某些字段在特定状态下可能为 `null` 或不存在。建议标注哪些字段仅在特定状态下返回（如 `ssh_password` 仅在运行中时有值，`error_message` 仅在 Failed 状态有值）。

### 3.5 缺少 `notebook_create` 的异步模式说明

`notebook_create` 返回 `taskId`，说明创建是异步的。但文档没有说明：
- 如何跟踪 `taskId` 的进度（是否有对应的查询工具？）
- 创建大约需要多长时间
- 创建失败时的状态和清理策略

---

## 四、具体文档问题

### 4.1 `notebook_list_images.md`

- 第 73 行：输出参数表使用 `data.data`（嵌套 data）来存放镜像数组，但 `notebook_list_model_images` 使用 `data.records`。两个"镜像列表"工具使用不同的字段名存放列表数据，不一致。
- 第 22 行：`access` 参数说明中的取值 `public`/`private` 缺少默认值推荐。

### 4.2 `notebook_list_model_images.md`

- 第 11 行：认证章节写"aiUrls 获取方式同 `notebook_list`"，但没有展开具体 SQL，在独立阅读时信息不完整。
- 输出参数表中 `data` 字段类型标注为 `object`，但缺少对该 object 内部结构的描述（仅有 `total` 和 `records`，未列表格化）。

### 4.3 `notebook_list_resources.md`

- 第 57 行：输出参数表将 `data` 类型标注为 `array`，这与所有其他工具（`data` 为 `object`）不一致。此处的 `data` 直接是数组而非包装对象，需要显式标注。
- `data` 数组元素的字段名全部使用 snake_case（`resource_group_code`），但 JSON 示例使用 camelCase（`resourceGroupCode`），与 2.2 节问题相同。

### 4.4 `notebook_release.md`

- 第 56 行：`data` 类型标注为 `boolean`，这与其他工具 `data` 为 `object` 的惯例不同。如果上游 API 确实返回 `boolean`，应加标注说明。

### 4.5 `notebook_rename.md`

- 第 57 行：同样 `data` 类型为 `boolean`，与 `notebook_release` 相同的问题。

### 4.6 `notebook_start.md`

- 第 23 行：参数表缺少 `cluster_id`（见 2.4 节），且没有说明为何不需要。
- 第 52 行：`data` 类型标注为 `boolean`。

### 4.7 `notebook_stop.md`

- 第 59 行：`data` 类型标注为 `boolean`。

### 4.8 `notebook_create.md`

- 第 28 行：`mount_info` 标注为 `array` 类型，但参数说明中嵌套了子字段（`mount_info[].source_path` 等）。在 MCP 工具签名中，array 参数需要定义为 `list[dict]` 或以 JSON 字符串传入，文档应明确序列化方式。
- 第 81 行：输出字段表用 snake_case（`task_id`），但返回值示例用 camelCase（`taskId`）。

---

## 五、与现有代码的对齐问题

### 5.1 `_get_default_token()` 已返回 `aiUrls`

`main.py:265-331` 中 `_get_default_token()` 返回的 dict 包含 `token`、`hpcUrls`、`efileUrls`，但注意它没有显式返回 `aiUrls` 字段。查看 L283-284：

```python
"SELECT uc.clusterId, uc.clusterName, uc.token, uc.JobManagerid, "
"uc.homePath, cu.hpcUrls, cu.efileUrls "
```

SQL 查询中没有 `cu.aiUrls`！这意味着 `_get_default_token()` 当前不返回 aiUrls。使用 `_get_default_token()` 获取 aiUrls 的工具需要额外查询或修改此函数。

这是一个**实现阻塞问题**：依赖 `_get_default_token()` 获取 aiUrls 的文档（`notebook_detail`、`notebook_list`、`notebook_list_images` 等）需要先在 `_get_default_token()` 的 SQL 中添加 `cu.aiUrls`。

### 5.2 平台级 AC URL 的 token 获取模式

`main.py` 中 `list_history_jobs` (L1706-1722) 和 `list_running_jobs` (L1841-1856) 为 AC URL 获取 `acToken` 的模式是：
1. 从 `users` 表查 `acToken`
2. 检查是否为 None
3. 直接使用 `acToken` 作为 HTTP header `token`

notebook 文档中使用 AC URL 的工具应遵循此模式，而非 `_get_default_token()`。

---

## 六、总结与优先级

| 优先级 | 问题 | 影响范围 |
|--------|------|---------|
| **P0** | 3 个工具 AC URL 使用集群 token 而非 acToken | `notebook_create`, `notebook_start`, `notebook_list_resources` — 认证必然失败 |
| **P0** | `_get_default_token()` SQL 缺少 `aiUrls` 字段 | 所有依赖 aiUrls 的工具无法获取 URL |
| **P1** | create/start 走 AC URL 但 stop/release/rename 走 aiUrls | 架构不一致，实现者困惑 |
| **P1** | 输出字段 snake_case vs camelCase 映射策略未定义 | 所有 13 个文档 |
| **P1** | `notebook_id` 的 API body 字段名不统一 (`id` vs `notebookId`) | 5 个工具 |
| **P2** | `page`/`size` 类型不一致 (string vs integer) | `notebook_list`, `notebook_list_model_images` |
| **P2** | `notebook_list_images` 必填参数过多 | 用户体验差 |
| **P2** | `notebook_start` 缺少 `cluster_id` | 功能可能受限 |
| **P2** | `notebook_start_custom_service` 返回值语义模糊 | 调用方错误处理困难 |
| **P3** | 认证逻辑在 13 个文档中重复 | 维护成本 |
| **P3** | 缺少错误码表 | 异常处理参考不足 |
| **P3** | `notebook_create` 异步模式未说明 | 集成体验不完整 |
| **P3** | `notebook_list_resources` data 类型为 array（其他为 object） | 解析不一致 |
