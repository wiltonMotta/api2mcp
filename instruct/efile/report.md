# efile 设计文档评审报告

## 一、架构级问题

### 1.1 `efile_upload` 和 `efile_download` 的客户端-服务端文件系统混淆

这是最严重的设计问题。

- **efile_upload** (`efile_upload.md:30`): `local_path` 描述为"本地文件路径（MCP 客户端侧的绝对路径）"，但后端处理逻辑 (`efile_upload.md:43`) 直接从 `local_path` 读取文件内容。MCP 服务端无法访问 MCP 客户端的文件系统。
- **efile_download** (`efile_download.md:30`): `local_dir` 描述为"本地保存目录。默认为当前工作目录"，服务端将文件写入自己的磁盘。但 MCP 客户端无法自动获取服务端磁盘上的文件。

**建议修复方案:**
- `efile_upload`: 接收 base64 编码的文件内容作为参数（`file_content: str` + `file_name: str`），或使用 MCP resources 机制
- `efile_download`: 将文件内容以 base64 字符串形式返回给客户端，而非写入服务端磁盘。保留 `local_dir` 仅作为可选的服务端缓存路径

### 1.2 错误响应格式不一致

文档中存在两套错误格式，边界不清晰：

| 格式 | 示例 | 出现位置 |
|------|------|----------|
| MCP 包装格式 | `{"error": true, "message": "下载文件失败 [911020]: 文件不存在"}` | 异常处理章节 |
| 上游 API 透传 | `{"code": "911020", "msg": "文件不存在", "data": null}` | 返回值示例章节 |

`efile_download.md` 的返回值示例直接展示了上游 API 的 `{"code": "911020", ...}` 格式，而异常处理章节又说要包装为 `{"error": true, ...}`。两者矛盾。

**建议:** 统一规范——MCP 层面的错误（认证失败、参数校验、网络异常）使用 `{"error": true, "message": "..."}` ；上游 API 返回的错误是透传还是包装需要明确决策。参考已实现的 `efile_list_files`（直接透传上游响应），建议统一透传，在 MCP 层面只拦截认证/网络层错误。

---

## 二、API 设计一致性问题

### 2.1 参数命名不统一

| 工具 | 路径参数名 | 备注 |
|------|-----------|------|
| `efile_touch` | `file_absolute_path` | 冗长 |
| `efile_rename` | `file_absolute_path` | 冗长 |
| `efile_download` | `path` | 简洁 |
| `efile_delete` | `paths` | 复数 |
| `efile_copy` | `source_paths` | 复数 |
| `efile_move` | `source_paths` | 复数 |
| `efile_upload` | `local_path` / `remote_path` | — |
| `efile_exist` | `path` | 简洁 |
| `efile_check_permission` | `path` | 简洁 |
| `efile_preview_file` | `path` | 简洁 |
| `efile_folder_create` | `path` | 简洁 |

**建议:** `efile_touch` 和 `efile_rename` 的 `file_absolute_path` 统一为 `path`，与其余 6 个工具保持一致。对于支持批量操作的工具，使用 `paths`（复数）明确表达批量语义。

### 2.2 `efile_download` GET 请求上的 Content-Type

`efile_download.md:56` 在 API 调用章节中列出了 `Content-Type: application/json`，但这是一个 GET 请求（无请求体），不应该有 Content-Type 头。同样的问题也出现在 `efile_delete.md:54`（POST with empty body 但声明了 Content-Type）。

### 2.3 输出格式两类模式混用

- **透传模式** (8个工具): `efile_delete`, `efile_copy`, `efile_move`, `efile_touch`, `efile_rename`, `efile_folder_create`, `efile_exist`, `efile_check_permission`, `efile_preview_file` 直接返回上游 API 的 `{code, msg, data}` 结构
- **包装模式** (1个工具): `efile_download` 返回自定义的 `{local_path, file_name, file_size, content_type}`

`efile_download` 的包装是合理的（因为它在本地做了额外处理），但 `efile_upload` 也应该考虑类似的包装（例如返回上传后的远程文件信息），而不是和其他"写操作"完全一样。

### 2.4 `clusterId` 提升为默认集群的行为未定义

现有 `submit_job` 实现中，当用户显式传入 `clusterId` 时，会将该集群提升为 `isDefault=true`。但 efile 设计文档没有说明是否要沿用此行为。已实现的 `efile_list_files` 没有做提升。需要在文档中明确决策。

---

## 三、文档完整性问题

### 3.1 缺少 `efile_list_files` 设计文档

`main.py:2108` 已经实现了 `efile_list_files`，但 `instruct/efile/` 目录下没有对应的设计文档。

### 3.2 认证/获取 URL 的 SQL 逻辑过度重复

11 个文档各自重复了相同的认证 SQL（第 16-23 行区域）和后端处理逻辑（步骤 1-2）。这些重复内容增加了维护成本——如果认证逻辑变更（如 `_get_default_token` 已经支持的 fallback 逻辑），需要同步更新 11 个文档。

**建议:** 提取公共章节引用，文档中只需写"参见通用认证流程"，聚焦于各工具的差异化逻辑。

### 3.3 缺少边界条件和幂等性说明

以下场景在文档中未涉及：
- 重复上传同一文件 (`cover=uncover`) 的行为
- 删除不存在的文件时 API 的行为
- 并发操作冲突
- 大文件上传/下载的中断恢复策略（仅有 download 简要提到 Range 头）

---

## 四、具体文档问题

### 4.1 `efile_delete.md`

- 输出参数表中 `data` 描述为 "返回数据（成功时为空字符串或 null）" — 类型标注 `object/null` 与实际返回的空字符串 `""` 不匹配
- `recursive` 参数缺少"删除非空文件夹需设为 true"的安全提示

### 4.2 `efile_folder_create.md`

- 错误码表列出了 `911022`（目标地址不是一个文件夹）但失败响应示例没有展示该错误
- `create_parents` 参数的类型在 MCP 参数表中为 `boolean`，但在 API 调用的 query params 中没有说明 boolean 如何序列化（`true`/`false` 字符串还是 `1`/`0`）

### 4.3 `efile_preview_file.md`

- `force` 参数类型为 `string`，取值为 `"force"` / `"default"`，但语义更适合用 `boolean`
- 输出参数的 `data` 字段中缺少 `total_size` 或 `total_lines` 等总览信息，用户无法知道文件总共有多少内容
- 缺少对 `start_index` 超出文件大小时行为的说明

### 4.4 `efile_touch.md`

- `file_absolute_path` 的说明为"源文件绝对路径（含文件名）"——这是创建操作，不应该用"源文件"来描述

### 4.5 `efile_copy.md` 和 `efile_move.md`

- 两个文档几乎完全相同（差异仅在于 URL 路径和错误消息），但各自维护一份。可考虑用模板或引用减少重复

### 4.6 `efile_upload.md`

- 第 46 行 `cover` 参数的 form field 名在后端逻辑中写为 `cover`，需确认与 API 实际参数名一致（有时后端 API 使用 camelCase `cover`，有时是其他命名）

---

## 五、与现有代码的对齐问题

### 5.1 `_get_default_token()` 已返回 `efileUrls`

`main.py:265-331` 中 `_get_default_token()` 返回的 dict 已经包含 `efileUrls` 字段。但文档中所有的认证 SQL 都是手动 JOIN `user_cluster` + `cluster_url`。文档应该引用这个已有函数，确保实现与设计一致。

### 5.2 已实现的 `efile_list_files` 中的字段映射模式

`main.py:2214-2244` 将 API 返回的 camelCase 字段映射为 snake_case。其他 efile 工具（如 `efile_preview_file`）返回的 `data` 对象也可能需要类似的映射处理。但设计文档中完全没有提到字段映射策略。

### 5.3 超时设置的全局客户端

`main.py:28-32` 的 `_get_http_client(timeout=30.0)` 创建一个共享客户端。但各 efile 工具有不同的超时需求（15s ~ 120s）。文档中提到的超时值需要通过 `timeout` 参数传递给 `httpx` 请求，而非依赖全局客户端默认值。这在设计文档中应有体现。

---

## 六、总结与优先级建议

| 优先级 | 问题 | 影响 |
|--------|------|------|
| **P0** | `efile_upload` 无法从 MCP 客户端读取文件 | 功能不可用 |
| **P0** | `efile_download` 写入服务端但客户端无法获取 | 功能不完整 |
| **P1** | 错误响应格式不一致 | 客户端解析混乱 |
| **P1** | 参数命名不统一 (`file_absolute_path` vs `path`) | API 碎片化 |
| **P2** | 缺少 `efile_list_files` 设计文档 | 文档不完整 |
| **P2** | 认证 SQL 在 11 个文档中重复 | 维护成本高 |
| **P2** | `clusterId` 提升行为未定义 | 行为不确定 |
| **P3** | GET 请求标注 Content-Type 等小错误 | 实现时可能照抄 |
| **P3** | 缺少幂等性/边界条件说明 | 异常场景未覆盖 |
