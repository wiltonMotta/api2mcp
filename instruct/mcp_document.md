# MCP Tool Documents Registry

> Auto-generated from `APIs` table. Total: 15 tools with stored documents.
> 
> **Registration flow**: Each `@mcp.tool()` decorated function is registered by FastMCP at import time. On first successful call, the tool writes its full schema (parameters + return type) to the `APIs` table via `INSERT OR REPLACE`. At subsequent startups, `register_apis()` creates proxy tools from these persisted entries. Tools without entries below will auto-register on first successful call.

---

## efile_check_permission

**Description**: 校验当前用户对指定文件是否具有读、写或执行权限。

**URL**: `{efileUrls}/efile/openapi/v2/file/permission`
**Method**: `POST`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `string` | Yes | 所校验文件的绝对路径 |
| `permission_action` | `string` | Yes | 权限类型：READ/WRITE/EXECUTE |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `object` | No | data |
| `msg` | `string` | No | msg |

---

## efile_copy

**Description**: 在 HPC 集群文件系统上复制文件，支持批量复制。

**URL**: `{efileUrls}/efile/openapi/v2/file/copy`
**Method**: `POST`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `source_paths` | `string` | Yes | 源文件绝对路径，多个用英文逗号分隔 |
| `target_path` | `string` | Yes | 目标目录绝对路径 |
| `cover` | `string` | No | 覆盖策略：cover/uncover，默认 uncover |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `any` | No | data |
| `msg` | `string` | No | msg |

---

## efile_delete

**Description**: 删除 HPC 集群文件系统上的文件或文件夹，支持批量删除。

**URL**: `{efileUrls}/efile/openapi/v2/file/remove`
**Method**: `POST`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `paths` | `string` | Yes | 删除文件的绝对路径，多个用英文逗号分隔 |
| `recursive` | `boolean` | No | 是否递归删除 |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `any` | No | data |
| `msg` | `string` | No | msg |

---

## efile_download

**Description**: 从 HPC 集群文件系统下载文件或文件夹。文件内容以 base64 编码字符串返回，文件夹以 zip 包返回。

**URL**: `{efileUrls}/efile/openapi/v2/file/download`
**Method**: `GET`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `string` | Yes | 要下载的文件/文件夹绝对路径 |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file_name` | `string` | No | file_name |
| `file_content` | `string` | No | file_content |
| `file_size` | `integer` | No | file_size |
| `content_type` | `string` | No | content_type |

---

## efile_exist

**Description**: 判断指定的文件或文件夹是否存在于 HPC 集群文件系统中。

**URL**: `{efileUrls}/efile/openapi/v2/file/exist`
**Method**: `POST`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `string` | Yes | 文件/文件夹的绝对路径 |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `object` | No | data |
| `msg` | `string` | No | msg |

---

## efile_folder_create

**Description**: 在 HPC 集群文件系统上创建文件夹。

**URL**: `{efileUrls}/efile/openapi/v2/file/mkdir`
**Method**: `POST`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `string` | Yes | 文件夹绝对路径 |
| `create_parents` | `boolean` | No | 父目录不存在时是否自动创建 |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `any` | No | data |
| `msg` | `string` | No | msg |

---

## efile_list_files

**Description**: 查询 HPC 集群上用户文件目录中的文件列表。支持按目录路径浏览、关键字搜索、排序和分页。token 取自 user_cluster 表。

**URL**: `{efileUrls}/efile/openapi/v2/file/list`
**Method**: `GET`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `string` | No | 目标文件夹路径（绝对路径），空时默认为用户家目录 |
| `keyword` | `string` | No | 搜索关键字，模糊匹配文件/文件夹名称 |
| `order` | `string` | No | 排序方式：asc（升序）或 desc（降序） |
| `order_by` | `string` | No | 排序字段：name（文件名）、size（文件大小）、lastModifiedTime（修改时间） |
| `start` | `integer` | No | 起始索引位置，从 0 开始 |
| `limit` | `integer` | No | 每页返回条数，最大 1000 |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `object` | No | data |
| `msg` | `string` | No | msg |

---

## efile_move

**Description**: 在 HPC 集群文件系统上移动文件，支持批量移动。

**URL**: `{efileUrls}/efile/openapi/v2/file/move`
**Method**: `POST`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `source_paths` | `string` | Yes | 源文件绝对路径，多个用英文逗号分隔 |
| `target_path` | `string` | Yes | 目标目录绝对路径 |
| `cover` | `string` | No | 覆盖策略：cover/uncover，默认 uncover |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `any` | No | data |
| `msg` | `string` | No | msg |

---

## efile_preview_file

**Description**: 预览 HPC 集群上的文本文件内容，支持分页读取。

**URL**: `{efileUrls}/efile/openapi/v2/file/preview`
**Method**: `POST`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `string` | Yes | 预览文件的绝对路径 |
| `force` | `boolean` | No | true 强制打开，false 默认方式 |
| `start_index` | `integer` | No | 起始字符位置，从 0 开始 |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `object` | No | data |
| `msg` | `string` | No | msg |

---

## efile_rename

**Description**: 重命名 HPC 集群文件系统上的文件。

**URL**: `{efileUrls}/efile/openapi/v2/file/rename`
**Method**: `POST`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `string` | Yes | 源文件绝对路径 |
| `new_name` | `string` | Yes | 文件修改后的新名称（仅文件名，不含路径） |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `any` | No | data |
| `msg` | `string` | No | msg |

---

## efile_upload

**Description**: 上传文件到 HPC 集群文件系统的指定路径。文件内容通过 base64 编码字符串传入。

**URL**: `{efileUrls}/efile/openapi/v2/file/upload`
**Method**: `POST`

**Parameter Format**: `JSON`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `file_content` | `string` | Yes | 文件内容的 base64 编码字符串 |
| `file_name` | `string` | Yes | 原始文件名 |
| `remote_path` | `string` | Yes | 远程目标文件夹路径（绝对路径） |
| `cover` | `string` | No | 覆盖策略：cover/uncover，默认 uncover |
| `clusterId` | `integer` | No | 集群 ID，为空时使用默认集群 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `data` | `any` | No | data |
| `msg` | `string` | No | msg |

---

## get_user_info

**Description**: Get current user's SCNet account information including country, language, timeZone, address, fullName, userName, computerCenter, accountName, accountStatus, accountBalance

**URL**: `https://www.scnet.cn/ac/openapi/v2/user`
**Method**: `GET`

**Parameter Format**: `URLParameter`

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `msg` | `string` | No | msg |
| `data` | `object` | No | data |

---

## notebook_create

**Description**: 创建 Notebook 容器实例，支持指定镜像、加速器、挂载目录和启动命令。token 取自 users.acToken。

**URL**: `https://www.scnet.cn/ac/openapi/v2/notebook/actions/create`
**Method**: `POST`

**Parameter Format**: `JSON`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `cluster_id` | `string` | Yes | 区域 ID |
| `image_path` | `string` | Yes | 镜像地址 |
| `image_name` | `string` | Yes | 镜像名称 |
| `image_size` | `string` | Yes | 镜像大小（byte） |
| `accelerator_type` | `string` | Yes | 加速器类型 |
| `accelerator_number` | `string` | Yes | 加速器数量 |
| `resource_group_code` | `string` | No | 资源分组 code |
| `mount_home` | `boolean` | No | 是否挂载主目录 |
| `start_command` | `string` | No | 启动命令 |
| `mount_info` | `array` | No | 自定义挂载信息 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `msg` | `string` | No | msg |

---

## notebook_list_images

**Description**: 查询可用的 Notebook 镜像列表，支持按名称、类型、加速器类型筛选和分页排序。token 取自 user_cluster 表。

**URL**: `{aiUrls}/ai/openapi/v2/image/images`
**Method**: `POST`

**Parameter Format**: `JSON`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | `string` | No | 镜像名称（模糊匹配） |
| `access` | `string` | No | 权限 public/private |
| `type` | `string` | No | 镜像类型 |
| `order_by` | `string` | No | 排序字段 |
| `sort` | `string` | No | 排序方式 |
| `start` | `integer` | No | 起始条数 |
| `limit` | `integer` | No | 每页数量 |
| `accelerator_type` | `string` | No | 加速器类型 |
| `cluster_id` | `integer` | No | 集群 ID |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `msg` | `string` | No | msg |
| `data` | `object` | No | data |

---

## notebook_list_resources

**Description**: 查询可用的 Notebook 计算资源（加速器）信息，包括 GPU/DCU 型号、可用卡数、资源分组。token 取自 users.acToken。

**URL**: `https://www.scnet.cn/ac/openapi/v2/resources/accelerators`
**Method**: `GET`

**Parameter Format**: `URLParameter`

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `cluster_ids` | `string` | Yes | 区域 ID 列表（逗号分隔） |
| `resource_id` | `string` | No | 资源 ID，用于筛选特定型号 |

### Returns

**Format**: `JSON`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | `string` | No | code |
| `msg` | `string` | No | msg |
| `data` | `array` | No | data |

---

## Tools Without Stored Documents

The following 19 tools are defined with `@mcp.tool()` decorators but haven't been called yet. Their full schemas will be registered on first successful call:

| Tool Name | Description |
|-----------|-------------|
| `hpc_list_available_partitions` | 列出当前用户在所有集群中真正可用的队列分区 |
| `hpc_submit_job` | 向 HPC 集群提交一个作业 |
| `hpc_get_running_job_detail` | 查询 HPC 集群中指定作业的实时详细信息 |
| `hpc_get_history_job_detail` | 查询 HPC 集群中指定历史作业（已完成/已终止）的详细信息 |
| `hpc_list_history_jobs` | 跨区域聚合查询历史作业列表 |
| `hpc_list_running_jobs` | 跨区域聚合查询实时作业列表 |
| `hpc_cancel_job` | 取消/删除 HPC 集群中正在运行或排队的作业 |
| `set_default_cluster` | Set the default cluster for the current user |
| `efile_touch` | 创建空文件或更新文件时间戳 |
| `notebook_start` | 启动 Notebook 实例 |
| `notebook_list` | 查询 Notebook 实例列表 |
| `notebook_detail` | 查询 Notebook 实例详细信息 |
| `notebook_stop` | 停止 Notebook 实例 |
| `notebook_release` | 释放 Notebook 实例 |
| `notebook_rename` | 重命名 Notebook 实例 |
| `notebook_list_model_images` | 查询模型镜像列表 |
| `notebook_query_jupyter_url` | 查询 JupyterLab 访问地址 |
| `notebook_query_custom_service_url` | 查询自定义服务的访问地址 |
| `notebook_start_custom_service` | 在 Notebook 实例中启动用户自定义服务 |
