# StreamableHTTP MCP 动态工具配置平台 — 实现方案

> 整合了原始方案 + 代码审查评估的补充与优化。
> 最后更新：2026-06-02

---

## Context

当前 api2mcp 是一个 StreamableHTTP MCP 服务，包含 34 个硬编码的 `@mcp.tool()` 工具函数（~3000 行重复样板代码），它们都遵循相同的模式：

```
auth 检查 → 集群 token 解析 → 轮询 URL 选择 → HTTP 调用 → 错误处理 → 自动注册文档
```

代码库已有动态代理工具基础（`load_apis()`、`make_proxy_tool()`、`register_apis()`），但目前的代理工具是**纯 HTTP 代理**，没有 auth context 注入能力。

**目标**：剥离 34 个硬编码工具，让服务只保留认证 + token/URL 缓存核心能力，所有业务工具通过配置动态加载，token/hpcUrls/efileUrls 等变量由 MCP 服务自动注入。

---

## 当前代码库状态快照

| 项目 | 现状 |
|---|---|
| 核心文件 | `main.py`（~4832 行，单文件） |
| 硬编码工具 | 34 个 `@mcp.tool()` 函数，分 HPC / eFile / AI / AC 四类 |
| 动态代理 | `make_proxy_tool()` 存在但为纯 HTTP 代理，无 auth 注入 |
| 数据库 | SQLite (`apis.db`)，4 张表：`APIs`、`users`、`user_cluster`、`cluster_url` |
| 路由 | 单一 `/mcp/{username}` |
| 测试 | `test/conftest.py` + `test/test_efile.py` + `test/test_notebook.py` |
| 已有基础设施 | `auto_commit_restart.py`（代码变更监控 + 自动重启） |

### 已知技术债（重构前应先修复）

**三处 SQL Schema 定义不一致**：

| 列 | `schema.sql` | `main.py` MIGRATION | `init_db.py` MIGRATION |
|---|---|---|---|
| `isDefault` | ❌ 缺失 | ❌ 缺失 | ✅ 有 |
| `JobManager*` 系列 | ❌ 缺失 | ✅ 有（ALTER TABLE 补） | ❌ 缺失 |

**建议**：重构 Step 0 之前统一三处 schema 定义，以 `init_db.py` 的版本为准（最完整）。

---

## 架构概览

```
                                    Parent Starlette App (:8000)
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                                                                              │
 │  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
 │  │ Auth+Admin │  │ FastMCP/HPC  │  │ FastMCP/eFile│  │  FastMCP/native      │ │
 │  │ /auth/{u}  │  │ /mcp/hpc/{u} │  │/mcp/efile/{u}│  │ /mcp/native/{u}      │ │
 │  │ /admin/*   │  │              │  │              │  │                      │ │
 │  │            │  │ submit_job   │  │ list_files   │  │ (原生工具保留)        │ │
 │  │AK/SK→token │  │ cancel_job   │  │ upload_file  │  │ set_default_cluster   │ │
 │  │→ 存 DB    │  │ ...          │  │ ...          │  │ list_avail_partitions │ │
 │  └─────┬──────┘  └──────┬───────┘  └──────┬───────┘  └────────┬─────────────┘ │
 │        │                │                 │                    │               │
 │        ▼                ▼                 ▼                    ▼               │
 │  ┌──────────────────────────────────────────────────────────────────────┐    │
 │  │  SQLite DB (apis.db) — WAL 模式                                      │    │
 │  │  ┌──────────┐ ┌───────┐ ┌───────────┐ ┌──────────────┐              │    │
 │  │  │APIs       │ │users  │ │cluster_url│ │user_cluster  │              │    │
 │  │  │(tools)    │ │       │ │           │ │              │              │    │
 │  │  └──────────┘ └───────┘ └───────────┘ └──────────────┘              │    │
 │  └──────────────────────────────────────────────────────────────────────┘    │
 │                                                                              │
 │  ┌──────────────────────────────────────────────────────────────────────┐    │
 │  │  /mcp/{username}  ← 聚合路由（向后兼容旧客户端，合并所有分组工具）     │    │
 │  └──────────────────────────────────────────────────────────────────────┘    │
 └──────────────────────────────────────────────────────────────────────────────┘
```

### 路由分组

| 路由 | 实例 | 工具示例 |
|---|---|---|
| `/mcp/hpc/{username}` | FastMCP_HPC | `hpc_submit_job`, `hpc_cancel_job`... |
| `/mcp/efile/{username}` | FastMCP_eFile | `efile_list_files`, `efile_upload`... |
| `/mcp/ai/{username}` | FastMCP_AI | `notebook_create`, `notebook_start`... |
| `/mcp/ac/{username}` | FastMCP_AC | `get_user_info`, `hpc_list_history_jobs`... |
| `/mcp/native/{username}` | FastMCP_native | `set_default_cluster`, `hpc_list_available_partitions` |
| `/mcp/{username}` | **聚合路由** | 所有分组合并（**向后兼容旧客户端**） |

> ⚠️ **关键设计决策**：保留 `/mcp/{username}` 作为合并所有分组的聚合端点。方案最初未包含此路由，但这会 100% 破坏所有已配置的 MCP 客户端。聚合路由确保旧客户端零改动迁移。

### 跨分组工具处理

某些工具的 auth 来源与分组不一致（如 `hpc_list_history_jobs` 使用 AC token 而非集群 token）。方案中一个工具的 `group` 字段应为**数组**而非单值：

```json
{
  "name": "hpc_list_history_jobs",
  "groups": ["hpc", "ac"],
  "auth": { "type": "acToken" }
}
```

工具在所有声明的分组中注册，但在每个分组中共享相同的 auth 配置。

---

## 增强型 Document JSON Schema

### 完整 Schema（整合了评估中发现的缺失字段）

```jsonc
{
  // ── 基础字段 ──
  "url": "{efileUrls}/efile/openapi/v2/file/list",
  "method": "GET",
  "description": "列出目录下的文件",
  "groups": ["efile"],                          // ★ 数组：工具可属于多个分组
  "tags": ["file", "read"],
  "enabled": true,
  "deprecated": false,

  // ── 参数定义 ──
  "parameters": {
    "format": "QueryParameter",                 // URLParameter | QueryParameter | JSON | Body | FormData
    "schema": {
      "path": { "type": "string", "description": "...", "optional": true }
    }
  },

  // ── 返回值定义 ──
  "returns": {
    "format": "JSON",                           // JSON | Binary | Stream
    "schema": {}
  },

  // ── 认证配置 ──
  "auth": {
    "type": "clusterToken|acToken|none",
    "tokenHeader": "token",
    "tokenPrefix": "",
    "additionalHeaders": {}
  },

  // ── ★ 集群解析（新增）──
  "clusterResolution": {
    "paramName": "clusterId",
    "tokenSource": "user_cluster",              // user_cluster | users (acToken)
    "urlField": "efileUrls",                    // hpcUrls | efileUrls | aiUrls | eshellUrls
    "promoteToDefault": true                    // 如果指定了 clusterId，是否更新 isDefault
  },

  // ── URL 构建 ──
  "roundRobin": "hpcUrls|efileUrls|aiUrls|none",
  "urlVariables": {
    "efileUrls": "$efileUrls",                  // 变量 → DB 字段映射
    "jobmanagerId": "$jobManagerId"
  },
  "pathHelpers": ["efile"],                     // ★ 新增：URL 拼接辅助函数

  // ── ★ 参数提取（新增）──
  "paramExtraction": {
    "jobmanagerId": {
      "source": "clusterResolution",
      "field": "jobManagerId"
    }
  },

  // ── 请求配置 ──
  "timeout": 30.0,
  "contentType": "application/json",            // application/json | application/x-www-form-urlencoded | multipart/form-data
  "fileUpload": false,
  "bodyTemplate": null,                         // ★ 用于 form-urlencoded 等特殊格式
  "conditionalBody": false,                     // ★ 新增：仅非空参数加入 body

  // ── ★ 响应后处理（新增）──
  "responseTransform": {
    "fieldMapping": {                           // 字段重命名 (camelCase → snake_case)
      "isDirectory": "is_directory",
      "lastModifiedTime": "last_modified_time"
    },
    "injectFields": {                           // 注入额外字段
      "token": "$token",
      "hpcUrls": "$hpcUrls"
    },
    "unwrapData": false,                        // 是否解包 data 字段
    "fieldDescriptions": {                      // 字段描述增强
      "jobState": "作业状态: statR(运行)..."
    }
  },

  // ── 依赖声明 ──
  "dependsOn": ["hpc_list_available_partitions"]
}
```

### 变量引用语法

Schema 中以 `$` 开头的值表示服务端注入的变量：

| 变量 | 来源 |
|---|---|
| `$token` | 当前集群的 token |
| `$hpcUrls` | 轮询后的单个 HPC URL |
| `$efileUrls` | 轮询后的单个 eFile URL |
| `$aiUrls` | 轮询后的单个 AI URL |
| `$jobManagerId` | 集群的 JobManager ID |
| `$clusterId` | 当前集群 ID |
| `$clusterUserName` | 集群用户名 |
| `$homePath` | 用户 homePath |

---

## 特殊工具适配矩阵

通过实际代码审查，以下是每个特殊工具的详细处理方案：

### 特殊格式工具

| 工具 | 特殊点 | 解决方案 |
|---|---|---|
| `hpc_cancel_job` | DELETE + form-urlencoded + `strJobInfoMap` 拼接 | `bodyTemplate: "jobMethod=5&{urlencode(strJobInfoMap)}"` + `contentType: "application/x-www-form-urlencoded"` |
| `efile_upload` | multipart 文件上传 | `fileUpload: true` + `contentType: "multipart/form-data"` |
| `efile_download` | binary response + `Content-Disposition` 解析 | `returns.format: "Binary"` + `responseTransform: { "extractFilename": true }` |
| `efile_touch` / `efile_check_permission` / `efile_move` 等 | POST + form data（非 JSON body） | `parameters.format: "FormData"` |

### 参数构造复杂工具

| 工具 | 复杂点 | 解决方案 |
|---|---|---|
| `hpc_submit_job` | 嵌套 body 构建 + 默认值填充 + job_name 生成 | Schema 支持 `defaultValue` + `conditionalBody` + body 模板 |
| `notebook_list_images` | 仅非空参数才加入 body | `conditionalBody: true` |
| `notebook_start_custom_service` | 多步骤流程 | 拆分为独立工具，声明 `dependsOn` |

### 跨服务类型工具

| 工具 | Token 类型 | URL 来源 | 备注 |
|---|---|---|---|
| `get_user_info` | acToken（users 表） | 静态 URL | 最简单，适合作为第一个迁移目标 |
| `hpc_list_history_jobs` | acToken | 静态 URL | 跨集群聚合查询 |
| `hpc_list_running_jobs` | acToken | 静态 URL | 跨集群聚合查询 |
| 其他 HPC 工具 | clusterToken（user_cluster 表） | hpcUrls（cluster_url 表） | — |
| 其他 eFile 工具 | clusterToken | efileUrls | — |
| 其他 AI 工具 | clusterToken | aiUrls | — |

### 不可动态化的原生工具

| 工具 | 原因 | 处理方式 |
|---|---|---|
| `set_default_cluster` | 纯 DB 状态操作，不是 HTTP 代理 | **始终保留为原生工具** |
| `hpc_list_available_partitions` | 遍历所有集群 + 聚合队列信息 | **始终保留为原生工具** |

---

## 风险矩阵

| # | 风险 | 影响 | 概率 | 应对策略 |
|---|---|---|---|---|
| R1 | 动态代理的 auth 注入/URL 变量解析有 bug | 工具调用失败 | 中 | 双轨运行：硬编码 + 动态可瞬间回退 |
| R2 | form-data、binary、multipart 在动态代理中未处理好 | 特定工具不可用 | 中 | 逐个迁移：每个工具经测试通过后才切换 |
| R3 | `get_current_username()` 在多 FastMCP + Starlette Mount 下路径参数提取异常 | 所有工具不可用 | 中-高 | ★ 升级为"中-高"概率：需提前验证 Starlette Mount 对 ContextVar 的传递行为 |
| R4 | OpenAPI 导入产生的工具定义不准确 | 部分端点调用失败 | 中-高 | 预览机制：导入前展示完整参数，用户确认后再写入 |
| R5 | 5 个 FastMCP 实例并发读写 SQLite 导致 `database is locked` | 随机工具调用失败 | 低-中 | ★ 新增：启用 WAL 模式 + 设置合理 busy_timeout |
| R6 | 聚合路由覆盖所有工具后，工具名冲突 | 工具不可用 | 中 | ★ 新增：工具名加分组前缀避免冲突 |

---

## 实现计划（调整后顺序）

> ⚠️ **重要调整**：原始方案的 Step 0→1→2→3 顺序存在问题——Step 0 搭建路由后要等到 Step 3 才能验证，中间有 3 步的"盲区"。调整为：**先增强引擎 → 再搭路由 → 然后双轨验证**。

---

### 前置工作：统一 SQL Schema（0.5 天）

**为什么放在最前面**：当前 `schema.sql`、`main.py MIGRATION_SQL`、`init_db.py MIGRATION_SQL` 三处的表定义不一致。重构前必须统一。

1. 以 `init_db.py` 的 MIGRATION_SQL 为基准（最完整，包含 `isDefault`、`JobManager*` 系列字段）
2. 统一 `schema.sql` 和 `main.py` 中的 MIGRATION_SQL
3. `APIs` 表添加新列：`groups TEXT`、`enabled INTEGER DEFAULT 1`、`tags TEXT`、`deprecated INTEGER DEFAULT 0`
4. 启动时启用 SQLite WAL 模式：`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`

**文件**：`schema.sql`、`main.py`、`init_db.py`

---

### Step 1: 增强型 Document Schema + 变量注入代理（1-2 天）

> 与原始方案 Step 1 相同，但 schema 扩展为上面定义的完整版本。

**在隔离的单元测试中完成，不碰路由**。

核心新增函数：
- `_resolve_auth_context(username, doc)` — 根据 `doc.auth.type` 和 `doc.clusterResolution` 从 DB 获取 token/URLs
- `_resolve_url_variables(url_template, auth_ctx)` — 将 `{efileUrls}` 替换为轮询后的单个 URL
- `_resolve_cluster_id(kwargs, doc)` — 从请求参数提取 clusterId，触发 isDefault 更新
- `_build_request(kwargs, doc, auth_ctx)` — 构造 HTTP 请求（URL、headers、body）
- `_transform_response(response, doc)` — 响应后处理（字段映射、注入、描述增强）

**特殊格式处理函数**：
- `_build_form_urlencoded(template, kwargs)` — 处理 `hpc_cancel_job` 的 form 编码
- `_build_multipart(file_path, kwargs)` — 处理 `efile_upload`
- `_handle_binary_response(response)` — 处理 `efile_download`

**验证**：
- 硬编码工具完整不受影响 ✅
- 在单元测试中手动调用 `make_proxy_tool("test_tool", doc)` 能生成正确的 proxy 函数
- 覆盖所有特殊格式的单元测试

**文件**：`proxy.py`（新建）、`test/test_proxy.py`（新建）

---

### Step 2: 路由分组架构搭建（1 天）

> 此时增强代理已就绪，搭建路由后可以立即验证。

1. 创建 `app.py`（父 Starlette 应用 + combined lifespan）
2. 创建 `mcp_groups.py`（5 个分组 FastMCP 实例 + 1 个聚合实例）
3. 将 `set_default_cluster` 和 `hpc_list_available_partitions` 注册到 native 分组
4. 将 `get_user_info`（最简单的 acToken 工具）注册到 ac 分组
5. Auth 路由 `/auth/{username}` 移到父级
6. **新增**：`/mcp/{username}` 聚合路由（注册所有分组的工具，向后兼容）

```python
# 启动模式切换
if os.environ.get("MCP_NEW_ARCH", "false") == "true":
    run_starlette_parent()  # 新架构
else:
    run_legacy_single_mcp()  # 旧架构，完全不变
```

**关键验证点**：
- `MCP_NEW_ARCH=false`：`python main.py` → 一切如旧 ✅
- `MCP_NEW_ARCH=true`：访问 `/mcp/native/{user}` 能看到 `set_default_cluster`
- `MCP_NEW_ARCH=true`：访问 `/mcp/ac/{user}` 能看到 `get_user_info`
- `MCP_NEW_ARCH=true`：访问 `/mcp/{user}` 能看到所有已注册工具（聚合路由）
- **Starlette Mount + ContextVar 传递测试**：确认 `_current_http_request` 在子应用中能正确读取 `username`

**文件**：`app.py`（新建）、`mcp_groups.py`（新建）、`main.py`（修改启动逻辑）

---

### Step 3: Seed Data 生成 + 影子注册（1-2 天）

> 整合了原始方案 Step 2 + Step 3。Seed data 生成的同时就是双轨对比。

**3a. 自动提取脚本**（优先于手工编写）

写一个提取脚本从现有 `@mcp.tool()` 函数中自动生成 seed data JSON：

```python
# tools/extract_seeds.py
# 解析 main.py 的 AST，提取每个 @mcp.tool() 的：
# - 函数签名 → parameters.schema
# - docstring → description
# - doc = {...} 块 → 完整的增强 document
```

34 个工具的 document 块已经存在于代码中（每个工具末尾的 `doc = {...}`），可以精确提取。

**3b. 手动 review 与补充**

自动提取后，对以下特殊字段手动补充：
- `clusterResolution`（原 doc 中没有）
- `responseTransform`（原 doc 中没有）
- `bodyTemplate`（仅 `hpc_cancel_job`）
- `conditionalBody`（仅 `notebook_list_images` 等）
- `pathHelpers`（仅 efile 和 ai 工具）

**3c. 影子注册**

动态工具以 `__v2` 后缀注册到各自的 FastMCP 分组中：
- `efile_list_files__v2` in eFile 分组
- 客户端无感知（不暴露给普通客户端，仅测试用）

**3d. 双轨对比**

`test/test_compare.py`：对每个工具验证 6 个维度的一致性。

| 维度 | 验证内容 | 通过条件 |
|---|---|---|
| 函数签名 | 参数名称、类型、默认值 | 硬编码 == 动态 |
| URL 构建 | path 参数替换、query 参数编码 | 硬编码 == 动态 |
| Header 注入 | token、content-type | 硬编码 == 动态 |
| Request Body | JSON body / form data / multipart | 硬编码 == 动态 |
| 变量解析 | `{efileUrls}` → 轮询 URL | 解析后的 URL 格式正确 |
| 响应处理 | 字段映射、注入、错误转换 | 结构一致 |

**文件**：`tools/extract_seeds.py`（新建）、`seed_data/01_scnet_tools.json`（自动生成 + 手动 review）、`seed_loader.py`（新建）、`test/test_compare.py`（新建）

---

### Step 4: 灰度切换 — 按优先级逐个迁移（2 天）

> 与原始方案 Step 4 相同。

迁移优先级（由易到难）：

**第一批（简单）**：
- [ ] `get_user_info` — AC token，静态 URL，最简单
- [ ] `efile_list_files` — 标准 GET，查询参数
- [ ] `efile_touch` — 标准 POST

**第二批（中等）**：
- [ ] `efile_delete`, `efile_exist`, `efile_rename`, `efile_move`, `efile_copy`
- [ ] `efile_folder_create`, `efile_preview_file`, `efile_check_permission`
- [ ] `notebook_list`, `notebook_detail`, `notebook_stop`

**第三批（复杂）**：
- [ ] `hpc_submit_job`, `hpc_get_running_job_detail`, `hpc_get_history_job_detail`
- [ ] `notebook_create`, `notebook_start`, `notebook_release`
- [ ] `hpc_list_history_jobs`, `hpc_list_running_jobs`

**第四批（特殊格式）**：
- [ ] `hpc_cancel_job` — form-urlencoded
- [ ] `efile_upload` — multipart
- [ ] `efile_download` — binary response
- [ ] `notebook_list_images` — 参数构造复杂
- [ ] `notebook_start_custom_service` — 流程复杂

每个工具的迁移步骤：
1. 将硬编码工具的注册从 `@mcp.tool()` 改为从 seed data 加载
2. 运行测试对比：请求参数一致、响应处理一致
3. 在测试环境完整调用工具（真实的 API 调用）
4. 通过后，在硬编码函数上加 `@deprecated` 注解，日志记录使用情况
5. 观察 1-2 天无报错 → 移除硬编码函数

**回退保障**：任何时候设置 `MCP_FALLBACK_HARDCODED=true`，服务重启后全部使用硬编码工具。

---

### Step 5: 运行时工具注册（1 天）

> 与原始方案 Step 5 相同。

`refresh_apis(server, db_path)` 替换 `register_apis()`：
- 对比当前已注册工具与 DB 中 enabled 工具的差集
- `server.add_tool()` 添加新增
- `server.remove_tool()` 删除已移除的
- 触发时机：启动时、OpenAPI 导入后、CRUD 后、手动触发

---

### Step 6: 配置管理 UI（2-3 天）

> 与原始方案 Step 6 相同。

| 路由 | 说明 |
|---|---|
| `/admin` | 仪表板：工具统计、认证状态、各分组概览 + 调用统计 |
| `/admin/tools` | 工具列表：搜索、按分组/标签筛选、启用/禁用开关 |
| `/admin/tools/{name}` | 工具详情 + JSON 编辑 + 测试调用 |
| `/admin/tools/new` | 新建工具表单（增强 Schema 的图形化编辑器） |
| `/admin/upload` | OpenAPI 上传 + 预览 |
| `/admin/refresh` | 手动重载工具 |
| `/admin/logs` | ★ 新增：工具调用日志 + 错误追踪 |

设计风格：现代简洁（Tailwind CSS + 轻量 JS）。

**文件**：`admin.py`（新建）、`templates/*.html`（新建）

---

### Step 7: OpenAPI 导入引擎（2 天）

> 与原始方案 Step 7 相同。

支持 OpenAPI 2.0/3.0，JSON/YAML。上传后解析 → 预览 → 确认 → 保存到 DB → `refresh_apis()`。

**文件**：`openapi_parser.py`（新建）

---

### Step 8: 最终清理（0.5 天）

> 与原始方案 Step 8 相同。

在所有工具迁移完成、稳定运行一段时间后：
1. 删除所有硬编码 `@mcp.tool()` 函数
2. 移除 `MCP_FALLBACK_HARDCODED` 标志和关联代码
3. 将 `main.py` 拆分为模块化结构

---

## 回退与应急方案

| 场景 | 动作 | 恢复时间 |
|---|---|---|
| 某个动态工具行为异常 | 设置 `MCP_FALLBACK_HARDCODED=true` 重启 | 30 秒 |
| OpenAPI 导入产生大量错误工具 | DB 回滚 / 删除错误行 → `refresh_apis()` | 1 分钟 |
| 路由分组导致客户端连不上 | 设置 `MCP_NEW_ARCH=false` 回到旧架构 | 30 秒 |
| 聚合路由工具名冲突 | 禁用冲突的动态工具 → `refresh_apis()` | 1 分钟 |
| combined lifespan 故障 | 回退到 `mcp.run()` 单实例模式 | 30 秒 |
| SQLite 并发锁 | 已启用 WAL 模式 + busy_timeout，大概率自动恢复 | < 5 秒 |

---

## 变更文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `main.py` | 重构 | 支持双架构启动，逐步精简 |
| `app.py` | 新建 | 父 Starlette 应用 + combined lifespan |
| `mcp_groups.py` | 新建 | 5 个分组 + 1 个聚合 FastMCP 实例管理 |
| `proxy.py` | 新建 | 增强型动态代理工厂 + auth 变量解析 + 响应后处理 |
| `config.py` | 新建 | 常量提取 |
| `db.py` | 新建 | 数据库操作（含 WAL 模式启用） |
| `auth.py` | 新建 | 认证逻辑提取 |
| `openapi_parser.py` | 新建 | OpenAPI spec 解析 |
| `admin.py` | 新建 | 管理 UI |
| `seed_loader.py` | 新建 | seed data 加载器 |
| `seed_data/01_scnet_tools.json` | 新建 | 34 个工具 seed data（自动提取 + 手动 review） |
| `tools/extract_seeds.py` | 新建 | 从 main.py AST 自动提取 seed data |
| `schema.sql` | 修改 | 统一表定义 + APIs 表添加 group/enabled/tags/deprecated 列 |
| `init_db.py` | 修改 | 统一 MIGRATION_SQL + 支持 seed_data 导入 |
| `requirements.txt` | 修改 | 添加 jinja2、PyYAML |
| `test/test_compare.py` | 新建 | 双轨对比测试：硬编码 vs 动态（6 维验证） |
| `test/test_proxy.py` | 新建 | 增强代理单元测试（覆盖所有特殊格式） |
| `templates/*.html` | 新建 | Jinja2 模板（管理 UI） |
| `auto_commit_restart.py` | 修改 | 监控路径扩展为 `api2mcp/` 目录 |

---

## 验证方案

1. **架构回退验证**：`MCP_NEW_ARCH=false` → 完全旧行为
2. **回退恢复验证**：`MCP_FALLBACK_HARDCODED=true` → 回到硬编码模式
3. **双轨一致性验证**：`pytest test_compare.py` → 每个工具的 6 维请求参数一致
4. **特殊格式验证**：`pytest test_proxy.py` → 覆盖 form-urlencoded / multipart / binary / form-data
5. **真实调用验证**：每个工具在实际场景中调用一次
6. **OpenAPI 导入验证**：上传 spec，导入后工具可用
7. **测试回归**：`pytest` 全部通过
8. **客户端兼容验证**：`example_client.py` 完整流程通过（新旧路由均可用）
9. **Starlette Mount 验证**：`_current_http_request` ContextVar 在子应用中正确传递
10. **SQLite 并发验证**：多个分组同时调用工具，无 `database is locked` 错误

---

## 可观测性（新增）

方案原始版本缺失可观测性设计，补充如下：

### 日志规范

```
[mcp:hpc] tool=hpc_submit_job user=xxx clusterId=1 url=... status=200 latency=1.2s
[mcp:efile] tool=efile_list_files user=xxx error="HTTP 502: ..." latency=0.8s
[proxy] tool=efile_upload__v2 resolved clusterId=1 → token OK, urls=2
```

### APIs 表扩展（统计列）

```sql
ALTER TABLE APIs ADD COLUMN call_count INTEGER DEFAULT 0;
ALTER TABLE APIs ADD COLUMN error_count INTEGER DEFAULT 0;
ALTER TABLE APIs ADD COLUMN last_error TEXT;
ALTER TABLE APIs ADD COLUMN last_called_at TEXT;
ALTER TABLE APIs ADD COLUMN avg_latency_ms REAL;
```

### 管理 UI 统计面板

- 工具调用热力图（按时间/分组）
- 错误率排行
- 慢工具排行（P50/P95/P99 延迟）
