# SCNet MCP Server — 产品需求文档 (PRD)

> **版本**: v2.0  
> **最后更新**: 2026-07-07  
> **项目状态**: 生产运行中 (Production ✅)  
> **代码仓库**: `/root/private_data/sun/code/api2mcp/`

---

## 目录

1. [产品概述](#1-产品概述)
2. [目标与范围](#2-目标与范围)
3. [系统架构](#3-系统架构)
4. [认证体系](#4-认证体系)
5. [MCP 工具目录](#5-mcp-工具目录)
6. [数据库设计](#6-数据库设计)
7. [部署与运维](#7-部署与运维)
8. [测试体系](#8-测试体系)
9. [限制与边界](#9-限制与边界)
10. [风险与应急预案](#10-风险与应急预案)
11. [未来规划](#11-未来规划)
12. [附录](#12-附录)

---

## 1. 产品概述

### 1.1 背景

SCNet（超级计算网络）是一个面向科研与工业计算的高性能计算平台，提供 HPC 作业调度、分布式文件存储、容器/Notebook 开发环境等服务。用户通过 SCNet 门户网站或 API 管理计算资源，但缺乏标准化的 AI Agent 协议接入能力。

### 1.2 产品定位

SCNet MCP Server 是一个 **StreamableHTTP 协议** 的 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 服务端实现，它将 SCNet OpenAPI 的 60+ 个 HTTP 端点封装为 AI Agent 可直接调用的 MCP 工具，让大语言模型能够以自然语言驱动的方式操作 SCNet 计算资源。

### 1.3 核心价值

| 维度 | 说明 |
|------|------|
| **标准化接入** | 通过 MCP 协议统一向 AI Agent（Claude、Cursor 等）暴露 SCNet 能力 |
| **零学习成本** | 用户无需记住 API 文档，用自然语言描述即可完成复杂操作 |
| **安全可控** | AK/SK 双因子认证 + 24h 自动续约 + 链接级文件下载 |
| **动态可扩展** | 工具注册通过 DB 配置驱动，新增 API 无需修改代码 |

### 1.4 用户场景

- **科研人员**: "帮我提交一个 16 核 GPU 作业到昆山集群，运行我的 training.py"
- **运维人员**: "列出哈尔滨集群上我所有的运行中作业"
- **数据分析师**: "把 result.tar.gz 上传到西安集群的 work 目录下"
- **开发者**: "在乌镇集群创建一个 Jupyter Notebook，配 4 核 32GB 内存"
- **跨集群文件操作**: "将文件从华东一区复制到西北一区"

---

## 2. 目标与范围

### 2.1 核心目标

1. **全功能覆盖**: 完整映射 SCNet 的核心 API（HPC 作业、文件系统、容器、Notebook、用户信息）
2. **零信任安全**: 每次工具调用都经过 DB 鉴权，令牌过期自动续约，续约失败提示用户重认证
3. **一致性保证**: 文件操作通过一致性哈希固定后端，消除跨后端 "文件找不到" 问题
4. **大文件处理**: 提供分片上传、流式下载、直链下载三种方式，突破 MCP SSE 传输限制
5. **自助认证**: 网页认证页面引导用户完成 AK/SK 输入，认证结果实时反馈

### 2.2 非目标

- ❌ 不提供 SCNet Web 管理 UI 替代（认证页面仅用于 MCP 凭证输入）
- ❌ 不处理用户注册、密钥管理（由 SCNet 门户网站完成）
- ❌ 不做实时监控面板（未来可能通过可观测性工具补充）

### 2.3 约束条件

| 约束 | 说明 |
|------|------|
| **传输层** | 仅支持 `streamable-http` 传输模式（不支持 stdio） |
| **部署环境** | 运行在线上模型部署容器内，通过 nginx 反向代理对外服务 |
| **单机部署** | SQLite 单节点，不支持多副本水平扩展 |
| **第三方依赖** | `fastmcp>=3.0`、`httpx>=0.27`、`pydantic>=2.0` |

---

## 3. 系统架构

### 3.1 整体架构

```
                        ┌─────────────────────────────────┐
                        │         AI Agent (Claude)        │
                        │  MCP Client (StreamableHTTP)     │
                        └──────────────┬──────────────────┘
                                       │
                              MCP 协议 (HTTP SSE)
                                       │
                        ┌──────────────▼──────────────────┐
                        │     Nginx 反向代理 (443)         │
                        │  /mcp/{ak} → 127.0.0.1:8002     │
                        │  /auth/{ak} → 127.0.0.1:8002    │
                        │  /mcp_test/ → 127.0.0.1:8003    │
                        └──────────────┬──────────────────┘
                                       │
                        ┌──────────────▼──────────────────┐
                        │     SCNet MCP Server (FastMCP)   │
                        │     StreamableHTTP Server        │
                        │     0.0.0.0:8002/8003            │
                        └──────┬──────┬──────┬──────┬─────┘
                               │      │      │      │
                    ┌──────────┘      │      │      └──────────┐
                    ▼                 ▼      ▼                 ▼
             ┌───────────┐   ┌──────────┐ ┌──────────┐ ┌───────────┐
             │ SQLite DB │   │   SCNet  │ │ SCNet    │ │ 集群eFile │
             │ (apis.db) │   │ Token API│ │ Center   │ │ /HPC/AI  │
             └───────────┘   └──────────┘ └──────────┘ └───────────┘
```

### 3.2 路由设计

| 路由 | 说明 | 处理逻辑 |
|------|------|---------|
| `GET /mcp/{accessKey}` | MCP 协议入口 | StreamableHTTP 会话，用于工具发现与调用 |
| `GET /auth/{accessKey}` | 认证页面 | 返回 AK/SK 输入 HTML 表单 |
| `POST /auth/{accessKey}` | 认证提交 | 验证 AK/SK，获取并存储集群 tokens |
| `GET /health` | 健康检查 | 返回 `{"status": "ok"}` |

### 3.3 工具分类体系

60+ 工具分为 5 个功能域：

| 域 | 数量 | 依赖 token 类型 | 依赖 URL 类型 |
|----|------|-----------------|---------------|
| **HPC 作业管理** | 11 | clusterToken / acToken | hpcUrls |
| **eFile 文件系统** | 25 | clusterToken | efileUrls |
| **容器管理** | 11 | clusterToken | aiUrls |
| **Notebook 管理** | 12 | clusterToken | aiUrls |
| **用户 & 集群** | 2 | acToken | 静态 URL |

### 3.4 后端 URL 选择策略

所有 efile/hpc/ai 工具在调用前需从多个后端 URL 中选择一个。项目早期使用**轮询 (round-robin)** 方式，但导致跨后端文件操作的严重不一致（如 `efile_touch` 创建文件到后端 A，`efile_move` 从后端 B 查找不到文件）。

**解决方案**: 一致性哈希 (`_pick_url`)

```python
def _pick_url(valid_urls: list[str], hash_key: str) -> tuple[str, int]:
    """Pick a URL via consistent hashing of *hash_key*."""
    n = len(valid_urls)
    if n == 1:
        return valid_urls[0], 0
    idx = abs(hash(hash_key)) % n
    return valid_urls[idx], idx
```

- `hash_key` 一般为文件路径或集群 ID
- 同一路径始终命中同一后端，消除不一致性
- 已在全部 57 处调用点替换 round-robin

---

## 4. 认证体系

### 4.1 认证流程

```
┌─────────────────────────────────────────────────────────────────┐
│  1. Agent 连接 /mcp/{accessKey}                                  │
│  2. 服务端检查 users 表 → acToken 是否存在                        │
│  3. 不存在 → 返回 auth_url → Agent 提示用户打开浏览器             │
│  4. 用户访问 /auth/{accessKey}                                    │
│  5. 输入 SCNet userName + Secret Key (SK)                         │
│  6. 服务端调用 SCNet Token API 验证 AK/SK/userName 三元组         │
│  7. 验证通过 → 存储 users.acToken + user_cluster.* tokens         │
│  8. 调用 get-center-info 获取各集群的 hpcUrls/efileUrls/aiUrls    │
│  9. 存储 cluster_url 表 → 认证完成                                │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 令牌生命周期

| 令牌 | 存储位置 | 有效期 | 续约策略 |
|------|---------|--------|---------|
| `acToken` | `users.acToken` | 24 小时 | 自动续约 (`_renew_and_persist_token`) |
| `clusterToken` | `user_cluster.token` | 24 小时 | 自动续约，按集群独立加锁 |
| SK (Secret Key) | 不留存 | — | — |

### 4.3 自动续约机制

每次工具调用发现 SCNet API 返回 `code=10008`（令牌过期）时，自动触发续约：

1. **并发防护**: 每个用户一个 `asyncio.Lock`，防止并发续约
2. **双重检查**: 获取锁后再次检查 DB 中的 token 是否已被其他协程更新
3. **调用续约 API**: `POST /ac/openapi/v2/tokens/next`
4. **持久化**: 续约成功后立即更新 DB
5. **重试**: 使用新 token 重试原始请求

若续约失败（如超过 24h 不再支持续约），返回 `auth_url` 引导用户重新认证。

### 4.4 安全设计

- **AK (accessKey) 作为会话标识**: 路径参数中的 accessKey 标识用户身份
- **无 SK 留存**: Secret Key 仅用于初始认证，验证通过后丢弃
- **响应注入 auth_url**: 所有工具返回 token 过期错误时自动附加 `auth_url` 字段
- **下载链接含 token**: `efile_download` 和 `efile_get_download_link` 返回的 URL 直接携带认证 token
- **参数校验**: 14 个关键工具使用 `_require_params` 手动校验缺参情况

---

## 5. MCP 工具目录

### 5.1 HPC 作业管理 (11 工具)

| 工具名 | 描述 | 底层 API | Token |
|--------|------|----------|-------|
| `hpc_list_available_partitions` | 列出所有集群可用队列分区 | 聚合多集群 API | clusterToken |
| `hpc_submit_job` | 提交 HPC 批处理作业 | `hpc/openapi/v2/job/submit` | clusterToken |
| `hpc_cancel_job` | 取消指定作业 | `hpc/openapi/v2/job/action` | clusterToken |
| `hpc_query_job_state` | 查询作业当前状态 | `hpc/openapi/v2/job/state` | clusterToken |
| `hpc_query_queue_jobs` | 查询队列中的作业列表 | `hpc/openapi/v2/job/queue` | clusterToken |
| `hpc_query_core_num` | 查询集群核心/节点数量 | `hpc/openapi/v2/job/core` | clusterToken |
| `hpc_query_user_quota` | 查询用户配额（核时/存储） | `hpc/openapi/v2/job/quota` | clusterToken |
| `hpc_query_used_time` | 查询用户已使用时间 | `hpc/openapi/v2/job/usedtime` | clusterToken |
| `hpc_list_history_jobs` | 列出历史作业（跨所有集群） | `ac/openapi/v2/history` | acToken |
| `hpc_list_running_jobs` | 列出运行中作业（跨所有集群） | `ac/openapi/v2/realtime` | acToken |
| `hpc_get_running_job_detail` | 获取运行中作业详情 | `hpc/openapi/v2/job/detail` | clusterToken |
| `hpc_get_history_job_detail` | 获取已结束作业详情 | `hpc/openapi/v2/job/detail` | clusterToken |

**关键特性**:
- `hpc_list_history_jobs` 和 `hpc_list_running_jobs` 使用 acToken（平台级 token），可跨集群聚合查询
- 其余工具使用 clusterToken（集群级 token），仅操作指定集群
- `hpc_submit_job` 支持 `job_name` 自动生成、`strJobInfoMap` JSON 解析、多节点多核心配置

### 5.2 eFile 文件系统 (25 工具)

#### 5.2.1 基础文件操作 (7 工具)

| 工具名 | 描述 | 底层 API |
|--------|------|----------|
| `efile_list_files` | 列出目录下的文件 | `GET /efile/openapi/v2/file/list` |
| `efile_touch` | 创建空文件或更新修改时间 | `POST /efile/openapi/v2/file/touch` |
| `efile_exist` | 检查文件/目录是否存在 | `POST /efile/openapi/v2/file/exist` |
| `efile_rename` | 重命名文件或目录 | `POST /efile/openapi/v2/file/rename` |
| `efile_move` | 移动文件/文件夹到目标路径 | `POST /efile/openapi/v2/file/move` |
| `efile_copy` | 复制文件/文件夹到目标路径 | `POST /efile/openapi/v2/file/copy` |
| `efile_delete` | 删除文件或空目录 | `POST /efile/openapi/v2/file/remove` |

#### 5.2.2 目录操作 (1 工具)

| 工具名 | 描述 | 底层 API |
|--------|------|----------|
| `efile_folder_create` | 创建文件夹（递归创建） | `POST /efile/openapi/v2/file/mkdir` |

#### 5.2.3 文件上传 (5 工具)

| 工具名 | 描述 | 底层 API |
|--------|------|----------|
| `efile_upload` | 单次上传小文件（≤100MB） | `POST /efile/openapi/v2/file/upload` |
| `efile_get_upload_config` | 根据文件大小推荐上传策略 | —（纯计算） |
| `efile_chunk_upload` | 分片上传（每片 50MB，安全 ≤75MB） | `POST /efile/openapi/v2/file/burst` |
| `efile_batch_chunk_upload` | 批量并行上传（最多10片/次，每片安全 ≤8MB） | `POST /efile/openapi/v2/file/burst` |
| `efile_merge_file` | 合并分片上传的文件 | `POST /efile/openapi/v2/file/merge` |

**上传策略矩阵**:

```
文件大小          | 推荐工具                    | 限制
──────────────────┼─────────────────────────────┼──────────────────────
≤ 100 MB          | efile_upload (单次上传)     | base64 编码后一次传输
100 MB – 5 GB     | efile_chunk_upload          | 每片 ≤75 MB (安全值)
100 MB – 5 GB     | efile_batch_chunk_upload    | 每片 ≤8 MB，每批 ≤80 MB
> 5 GB            | 拒绝，建议 SCP/SFTP         | 硬限制
```

#### 5.2.4 文件下载 (4 工具)

| 工具名 | 描述 | 实现方式 |
|--------|------|---------|
| `efile_download` | 返回含 token 的 HTTP 下载链接 | HEAD 预检 → 返回 `download_url` |
| `efile_download_chunk` | 分块下载，返回 base64 分片 | 支持 Range 请求，每块 5MB |
| `efile_get_download_link` | 生成带自定义有效期的下载链接 | 直接 URL 拼接，`expires_in` 参数 |

**下载策略**:
- 所有文件统一返回 `download_url`（后端不返回 Content-Length，无法内联）
- 小文件（≤200KB）在旧版本中内联返回，现已统一改为链接模式
- `efile_download_chunk` 支持分块下载，突破 SSE 传输限制

#### 5.2.5 文件分享 (2 工具)

| 工具名 | 描述 | 底层 API |
|--------|------|----------|
| `efile_open_share` | 开启文件分享 | `POST /efile/openapi/v2/file/open-share` |
| `efile_close_share` | 关闭文件分享 | `POST /efile/openapi/v2/file/close-share` |

#### 5.2.6 其他操作 (4 工具)

| 工具名 | 描述 | 底层 API |
|--------|------|----------|
| `efile_check_permission` | 检查是否有指定权限 | `POST /efile/openapi/v2/file/permission` |
| `efile_preview_file` | 预览文件内容（文本/图片） | `POST /efile/openapi/v2/file/preview` |
| `efile_get_upload_config` | 获取上传配置推荐 | —（纯计算函数） |
| `efile_get_download_link` | 生成下载链接 | —（URL 拼接） |

#### 5.2.7 异步操作 (5 工具)

| 工具名 | 描述 | 底层 API |
|--------|------|----------|
| `efile_async_copy` | 异步复制大文件/目录 | `POST /efile/openapi/v2/file/async-copy` |
| `efile_async_move` | 异步移动大文件/目录 | `POST /efile/openapi/v2/file/async-move` |
| `efile_async_delete` | 异步删除大文件/目录 | `POST /efile/openapi/v2/file/async-remove` |
| `efile_async_task_cancel` | 取消异步任务 | `POST /efile/openapi/v2/file/task/cancel` |
| `efile_async_task_list` | 列出异步任务列表 | `POST /efile/openapi/v2/file/task/list` |

### 5.3 容器管理 (11 工具)

| 工具名 | 描述 | 底层 API |
|--------|------|----------|
| `container_create` | 创建容器实例 | `ai/openapi/v2/container/create` |
| `container_start` | 启动容器 | `ai/openapi/v2/container/start` |
| `container_stop` | 停止容器 | `ai/openapi/v2/container/stop` |
| `container_delete` | 删除容器 | `ai/openapi/v2/container/delete` |
| `container_execute` | 在容器中执行命令 | `ai/openapi/v2/container/execute` |
| `container_query_list` | 查询容器列表 | `ai/openapi/v2/container/list` |
| `container_query_url` | 查询容器访问 URL | `ai/openapi/v2/container/url` |
| `container_query_detail` | 查询容器详情 | `ai/openapi/v2/container/detail` |
| `container_update_resource` | 更新容器资源配置 | `ai/openapi/v2/container/resize` |
| `container_query_resources` | 查询集群可分配资源 | `ai/openapi/v2/container/resource` |
| `container_query_resource_group` | 查询资源分组 | `ai/openapi/v2/container/group` |
| `container_query_allowed_mount_dir` | 查询允许挂载的目录 | `ai/openapi/v2/container/mount` |
| `container_get_images` | 查询可用镜像列表 | `ai/openapi/v2/container/image` |

**容器类型支持**: SSH / Jupyter / CodeServer / RStudio

### 5.4 Notebook 管理 (12 工具)

| 工具名 | 描述 | 底层 API |
|--------|------|----------|
| `notebook_list_resources` | 列出可用的 Notebook 资源 | `ai/openapi/v2/jupyter/resource` |
| `notebook_create` | 创建 Notebook 实例 | `ai/openapi/v2/jupyter/create` |
| `notebook_start` | 启动 Notebook | `ai/openapi/v2/jupyter/start` |
| `notebook_list` | 列出用户 Notebook 列表 | `ai/openapi/v2/jupyter/list` |
| `notebook_detail` | 查询 Notebook 详情 | `ai/openapi/v2/jupyter/detail` |
| `notebook_stop` | 停止 Notebook | `ai/openapi/v2/jupyter/stop` |
| `notebook_release` | 释放 Notebook 资源 | `ai/openapi/v2/jupyter/release` |
| `notebook_rename` | 重命名 Notebook | `ai/openapi/v2/jupyter/rename` |
| `notebook_list_images` | 列出可用镜像（支持筛选） | `ai/openapi/v2/jupyter/images` |
| `notebook_list_model_images` | 列出模型镜像 | `ai/openapi/v2/jupyter/images` |
| `notebook_query_jupyter_url` | 获取 Jupyter 访问 URL | `ai/openapi/v2/jupyter/url` |
| `notebook_query_custom_service_url` | 获取自定义服务 URL | `ai/openapi/v2/jupyter/url` |
| `notebook_start_custom_service` | 启动自定义服务 | `ai/openapi/v2/jupyter/custom-service` |

### 5.5 用户与集群 (2 工具)

| 工具名 | 描述 | 底层 API | Token |
|--------|------|----------|-------|
| `get_user_info` | 获取当前用户账号信息 | `ac/openapi/v2/user` | acToken |
| `set_default_cluster` | 设置默认集群 | —（DB 操作） | — |

---

## 6. 数据库设计

### 6.1 实体关系

```
users 1────────────N user_cluster N────────────1 cluster_url
  │                      │
  │                      └── token (clusterToken)
  └── acToken (platform token)

APIs: 工具注册表（动态代理工具存储于此）
```

### 6.2 表结构

#### `APIs` — 动态工具注册表

```sql
CREATE TABLE IF NOT EXISTS APIs (
    name     TEXT PRIMARY KEY,   -- MCP 工具名
    document TEXT NOT NULL       -- JSON 文档（URL、方法、参数、返回 schema）
);
```

**document JSON 结构**:

```json
{
  "url": "{efileUrls}/efile/openapi/v2/file/list",
  "method": "GET",
  "description": "列出目录下的文件",
  "parameters": {
    "format": "URLParameter",
    "schema": {
      "path": {"type": "string", "description": "目录绝对路径", "optional": false}
    }
  },
  "returns": {
    "format": "JSON",
    "schema": { "code": {"type": "string"} }
  }
}
```

#### `users` — 用户认证表

```sql
CREATE TABLE IF NOT EXISTS users (
    userName   TEXT PRIMARY KEY,
    accessKey  TEXT UNIQUE,      -- 访问密钥（会话标识）
    acToken    TEXT,             -- 平台令牌（NULL 表示未认证）
    created_at datetime,
    updated_at datetime
);
```

#### `user_cluster` — 用户集群配置

```sql
CREATE TABLE IF NOT EXISTS user_cluster (
    userName        TEXT,
    clusterId       INTEGER,
    clusterName     TEXT,
    homePath        TEXT,           -- 用户 home 目录
    token           TEXT NOT NULL,  -- 集群级令牌
    isDefault       boolean,        -- 是否为默认集群
    JobManagerType  TEXT,           -- 调度器类型 (SLURM)
    JobManagerAddr  TEXT,           -- 调度器地址
    JobManagerid    TEXT,
    JobManagertext  TEXT,
    JobManagerPort  TEXT,
    created_at      datetime,
    updated_at      datetime,
    PRIMARY KEY (userName, clusterId)
);
```

#### `cluster_url` — 集群服务 URL

```sql
CREATE TABLE IF NOT EXISTS cluster_url (
    clusterId   INTEGER PRIMARY KEY,
    clusterName TEXT,
    hpcUrls     TEXT,      -- 逗号分隔的 HPC API URLs
    aiUrls      TEXT,      -- 逗号分隔的 AI/容器 API URLs
    efileUrls   TEXT,      -- 逗号分隔的文件系统 API URLs
    eshellUrls  TEXT       -- 逗号分隔的 Shell API URLs
);
```

---

## 7. 部署与运维

### 7.1 环境要求

| 组件 | 要求 |
|------|------|
| Python | 3.11+ |
| 依赖 | `fastmcp>=3.0` `httpx>=0.27` `pydantic>=2.0` |
| 存储 | SQLite 文件（`apis.db`） |
| 网络 | 可访问 SCNet API 端点 + nginx 代理 |

### 7.2 部署架构

```
Internet → nginx (443 SSL) → localhost:8002 (MCP Server)
                          → localhost:8003 (MCP Test Server)
```

#### nginx 配置要点

```nginx
# 生产环境 (scnet.cn)
location /mcp/    { proxy_pass http://127.0.0.1:8002; }
location /auth/   { proxy_pass http://127.0.0.1:8002; }

# 测试环境 (itos2.sugon.com)
location /mcp_test/ { proxy_pass http://127.0.0.1:8003; }
location /auth_test/ { proxy_pass http://127.0.0.1:8003; }
```

**环境变量**:

| 变量 | 生产环境 | 测试环境 |
|------|---------|---------|
| `SCNET_TOKEN_URL` | `https://api.scnet.cn/...` | `https://itos2.sugon.com/...` |
| `MCP_AUTH_PREFIX` | `auth` | `auth_test` |
| `MCP_PORT` | `8002` | `8003` |

### 7.3 启动方式

```bash
# 生产环境
bash restartMcp_fixed.sh prod   # 端口 8002

# 测试环境
bash restartMcp_fixed.sh test   # 端口 8003

# 监控自动重启
nohup python auto_commit_restart.py > /tmp/api2mcp/auto_commit.log 2>&1 &
```

### 7.4 健康检查

```
GET /health → {"status": "ok"}
```

---

## 8. 测试体系

### 8.1 测试结构

```
test/
├── conftest.py          # 共享 fixtures（mock DB、mock HTTP、auth context）
├── test_efile.py        # efile 文件系统工具测试（25 个工具全覆盖）
├── test_container.py    # 容器管理工具测试
├── test_notebook.py     # Notebook 管理工具测试
├── test_hpc.py          # HPC 作业管理工具测试
├── test_integration.py  # 端到端集成测试（依赖真实的 SCNet API）
```

### 8.2 测试框架

- **框架**: pytest + pytest-asyncio
- **DB 隔离**: 每个测试使用 `tempfile.mkstemp()` 创建临时 SQLite 数据库
- **HTTP Mock**: `unittest.mock.AsyncMock` 模拟 `httpx.AsyncClient`
- **Auth Mock**: 通过 `fastmcp.server.http._current_http_request` ContextVar 注入 mock 请求

### 8.3 测试覆盖率目标

- efile 系列: 25 个工具全覆盖（含缺参校验、权限校验、一致性哈希验证）
- 所有 error 分支: HTTP 异常、JSON 解析异常、base64 解码异常

### 8.4 已知问题 (2026-07-07)

| 问题 | 状态 | 说明 |
|------|------|------|
| efile_download HEAD 无 Content-Length | ⚠️ 已知 | 后端 efile API 的 HEAD 请求不返回 `content-length`，所有文件返回 `file_size=0` 的下载链接 |
| efile_download_chunk key 命名 | ⚠️ 无关 | 测试断言使用 `data` 但实际返回 `file_content_b64`，不影响功能 |

---

## 9. 限制与边界

### 9.1 文件传输限制

| 操作 | 上限 | 说明 |
|------|------|------|
| 单次上传 (efile_upload) | 100 MB | 超过需分片上传 |
| 分片上传单分片 | ≤79 MB (建议安全 ≤75 MB) | 实测上限 |
| 批量上传每片 | ≤10 MB (建议安全 ≤8 MB) | 实测上限 |
| 批量上传每批总大小 | ≤100 MB (建议安全 ≤80 MB) | 最多 10 片 |
| 文件总大小 | 5 GB | 超过拒绝，建议 SCP/SFTP |
| efile_download_chunk 分块 | 默认 5MB/块 | 支持 Range 请求 |

### 9.2 令牌限制

| 项目 | 限制 |
|------|------|
| Token 有效期 | 24 小时 |
| Token 续约窗口 | Token 签发后 24 小时内 |
| 续约失败后恢复 | 需用户重新访问认证页面 |

### 9.3 并发限制

- SQLite WAL 模式未启用（单连接串行）
- 单进程 asyncio 协程并发，非多进程并行
- 每个用户一个续约锁，防止并发续约

### 9.4 其他限制

| 项目 | 限制 |
|------|------|
| 传输协议 | 仅 `streamable-http`，不支持 `stdio` |
| 请求超时 | 默认 30s，文件上传 120s，下载 300s |
| URL 编码 | 路径中的空格手动替换为 `%20`，未使用 `urllib.parse.quote` |

---

## 10. 风险与应急预案

### 10.1 风险矩阵

| # | 风险 | 影响 | 概率 | 应对 |
|---|------|------|------|------|
| R1 | SCNet API 变更 | 工具调用失败 | 低 | 工具多为 HTTP 代理，更新 DB 即可 |
| R2 | Token 大面积过期 | 所有用户暂停 | 中 | 自动续约机制，续约失败报错 + auth_url 引导 |
| R3 | 存储空间不足 | DB 写入失败 | 低 | 定期清理日志，监控磁盘 |
| R4 | 后端 URL 变更 | 工具调用失败 | 中 | 用户重新认证获取最新 URLs |
| R5 | SQLite 并发写入冲突 | 请求失败 | 低 | 单进程架构，冲突概率低 |

### 10.2 应急操作

```bash
# 重启生产服务
bash restartMcp_fixed.sh prod

# 切换到测试环境（修改 MCP 客户端配置指向测试 URL）
# 清理用户数据重新认证
sqlite3 apis.db "DELETE FROM users; DELETE FROM user_cluster;"
```

---

## 11. 未来规划

### 11.1 短期 (1-2 月)

| 项目 | 优先级 | 说明 |
|------|--------|------|
| 动态工具系统 | P0 | 将 60+ 硬编码工具迁移为 DB 配置驱动 |
| 路由分组架构 | P0 | 拆分为 hpc/efile/ai/ac/native 多 FastMCP 实例 |
| 管理 UI | P1 | `/admin` 控制面板：工具 CRUD、调用统计、日志查看 |
| OpenAPI 导入 | P1 | 支持上传 OpenAPI 2.0/3.0 spec 自动生成工具 |

### 11.2 中期 (3-6 月)

| 项目 | 说明 |
|------|------|
| 可观测性 | 调用日志、错误率、P50/P95 延迟 |
| SQLite WAL 模式 | 支持多实例并发 |
| 令牌预刷新 | Token 过期前自动预刷新，避免运行时中断 |
| 多架构部署 | 分离认证服务与工具代理 |

### 11.3 长期

| 项目 | 说明 |
|------|------|
| 多副本水平扩展 | 支持多 MCP 服务实例 |
| RBAC 权限 | 基于角色的精细权限控制 |
| Webhook 通知 | 作业完成、文件传输完成等事件通知 |
| 国际化 | 英文文档 + 多语言认证页面 |

---

## 12. 附录

### 12.1 技术栈

| 组件 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 运行语言 |
| FastMCP | ≥3.0 | MCP 协议框架（StreamableHTTP） |
| HTTPX | ≥0.27 | 异步 HTTP 客户端 |
| Pydantic | ≥2.0 | 参数校验与类型注解 |
| SQLite | 3.x | 嵌入式数据库 |
| Nginx | 1.x | 反向代理 + SSL 终结 |

### 12.2 文件清单

| 文件 | 说明 |
|------|------|
| `main.py` | 核心服务端（单文件） |
| `schema.sql` | 数据库建表 DDL |
| `init_db.py` | 数据库初始化 + 种子数据 |
| `restartMcp_fixed.sh` | 生产/测试环境启停脚本 |
| `auto_commit_restart.py` | 代码变更监控 + 自动重启 |
| `pytest.ini` | 测试配置 |
| `test/conftest.py` | 测试 fixtures |
| `test/test_efile.py` | efile 工具测试 |
| `test/test_hpc.py` | HPC 工具测试 |
| `test/test_container.py` | 容器工具测试 |
| `test/test_notebook.py` | Notebook 工具测试 |
| `test/test_integration.py` | 集成测试（真实 API） |
| `instruct/` | 工具 API 文档 |
| `doc/mcp-gateway-design.md` | 架构重构设计方案 |

### 12.3 关键术语

| 术语 | 说明 |
|------|------|
| **AK** | Access Key，用户会话标识 |
| **SK** | Secret Key，用户密钥（仅首次验证使用） |
| **acToken** | 平台级令牌，用于用户信息查询和跨集群操作 |
| **clusterToken** | 集群级令牌，用于单集群内的 HPC/eFile/AI 操作 |
| **MCP** | Model Context Protocol，AI Agent 工具调用协议 |
| **StreamableHTTP** | MCP 传输模式，基于 HTTP SSE 长连接 |
| **一致性哈希** | 通过 hash 固定 URL 选择，避免跨后端不一致 |

### 12.4 变更历史

| 日期 | 版本 | 变更内容 |
|------|------|---------|
| 2026-07-07 | v2.0 | 当前生产版本，60+ 工具，7 个 bug 修复完成 |
