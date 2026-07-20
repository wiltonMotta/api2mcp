你好，以下是 SCNet MCP Server (scnet-api2mcp) 的 efile 系列工具测试报告。请帮忙排查以下问题：

## 测试环境
- MCP Server: SCNet OpenAPI MCP Server v3.3.1
- 测试时间: 2026-07-07
- 用户: ac1npa3sf2 (孙金刚)
- 认证方式: URL 路径中携带 AK (streamable_http)
- mcp配置：{"mcpServers": {"scnet-api2mcp": {"type": "streamable_http","url": "https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/3b268f0c82b543ea9312ffcc1052e98c"}}}

## 关键 Bug 列表

### Bug 1: efile_download 返回值缺失 data 字段（严重）

**现象**: 上传 5MB 文件，分片上传+合并后下载，下载返回的 data 字段只有 6 bytes，原始文件 5MB。

**复现步骤**:
```
1. efile_chunk_upload: 5MB, chunk_number=1, total_chunks=1, identifier="chunk_test" → code=0 OK
2. efile_merge_file: path=/public/home/ac1npa3sf2/xxx, relative_path="chunk_integrity.bin", identifier="chunk_test" → code=0 OK
3. efile_download: path=/public/home/ac1npa3sf2/xxx/chunk_integrity.bin → code=0, 但 structuredContent.data 只有 6 bytes
```

**原始MD5**: `006a88ed7d5826af90d38dba4666fd89`
**下载数据**: 仅 6 bytes
**耗时**: 下载验证耗时 81.4s

**原因推测**:
- 合并后文件可能未正确创建在服务器上
- 或者下载接口读取文件时路径解析有问题
- 或者分片上传/合并逻辑有 bug（文件实际上不存在，但合并返回了 0）

**推荐排查方向**:
1. 检查合并后的文件是否真实存在于服务器文件系统
2. 检查 efile_download 返回的 base64 数据解码后是否真的只有 6 bytes
3. 检查分片上传后文件是否正确存储到临时目录
4. 检查合并接口是否正确将分片数据合并到目标路径

---

### Bug 2: efile_get_upload_config 返回空响应（中等）

**现象**: 调用 efile_get_upload_config 接口，返回 code="?|msg=""，即 no valid SSE response。

**复现步骤**:
```
curl ... -d '{"jsonrpc":"2.0","id":xx,"method":"tools/call","params":{"name":"efile_get_upload_config","arguments":{"file_size_bytes":1024}}}'
```

**实际结果**: code=? (解析失败，无 SSE 响应)

**期望结果**: 返回 code=0, 包含推荐上传策略配置

**原因推测**:
- efile_get_upload_config 接口可能在某个代码路径下抛出异常，导致 SSE 响应格式损坏
- 或者该接口的实现有问题（返回的不是 JSON-RPC 格式）

**推荐排查方向**:
1. 检查 efile_get_upload_config 的实现代码
2. 查看该接口返回的原始 HTTP 响应
3. 检查服务端日志中是否有异常

---

### Bug 3: 缺失必填参数时，部分工具返回 code="?|无响应（中等）

**现象**: 以下工具在缺失必填参数时，没有返回错误码，而是返回空响应或 code="?|

**受影响工具**:
| 工具 | 缺失参数 | 返回 |
|------|---------|------|
| efile_upload | - | code=? |
| efile_download | - | code=? |
| efile_delete | paths | code=? |
| efile_rename | path | code=? |
| efile_rename | new_name | code=? |
| efile_check_permission | permission_action | code=? |
| efile_check_permission | path | code=? |
| efile_exist | path | code=? |
| efile_preview_file | path | code=? |
| efile_touch | path | code=? |
| efile_move | source_paths | code=? |
| efile_move | target_path | code=? |
| efile_copy | - | code=? |

**期望结果**: 应返回 code 非 "0" 的错误码（如 10001/internal_error 或自定义参数校验错误码），msg 中描述缺少哪些参数

**推荐排查方向**:
1. 检查参数校验中间件是否生效
2. 检查参数校验失败时是否正确返回 JSON-RPC 错误格式
3. 查看是否有 panic/exception 导致 SSE 响应断裂

---

### Bug 4: efile_exist 空 path 返回 param_incomplete 错误（低）

**现象**: 调用 efile_exist 时 path=""，返回 code=10003, msg="param_incomplete"

**期望结果**: 
- 如果空 path 应返回家目录，则应 code=0
- 如果空 path 不允许，则 msg 应为更清晰的错误描述

---

### Bug 5: efile_check_permission INVALID 权限返回空响应（低）

**现象**: permission_action="INVALID" 时，返回 no valid SSE response

**期望结果**: 应返回参数校验错误，如 code=10004, msg="permission_action must be one of: READ, WRITE, EXECUTE"

---

### Bug 6: 文件操作路径问题

**现象**: efile_touch 创建的 touch_test.txt 文件存在，但后续 efile_move 时报 "File does not exist" (code=911020)

**复现**:
```
1. efile_touch: path=/public/home/ac1npa3sf2/xxx/move_src.txt → code=0 OK
2. efile_move: source_paths=/public/home/ac1npa3sf2/xxx/move_src.txt → code=911020 "File does not exist"
```

**原因推测**:
- efile_touch 可能实际上没有创建文件（返回 code=0 但文件未真正写入）
- 或者 efile_move 在查找文件时使用了不同的路径逻辑
- 或者集群切换后 home 路径变了

---

### Bug 7: efile_list_files 负 start 返回 param_invalid（可能合理）

**现象**: start=-1 返回 code=10004, msg="param_invalid"

**评价**: 这个行为可能合理（参数校验），但如果希望支持负索引（从末尾计数），需要确认需求。

---

## 正常工作的功能（供参考）

以下功能验证通过，可作为正常工作的参考：
- efile_list_files: 列表、搜索、排序、分页 ✓
- efile_folder_create: 创建目录（含父目录） ✓
- efile_touch: 创建文件 ✓
- efile_delete: 删除文件 ✓
- efile_upload: 上传小文件（10KB, 100KB, 1MB）✓
- efile_chunk_upload: 分片上传 5MB ✓
- efile_merge_file: 合并分片文件 ✓
- efile_check_permission: READ/WRITE/EXECUTE 权限检查 ✓
- efile_preview_file: 预览文件 ✓
- efile_exist: 检查文件/目录是否存在 ✓

## 测试数据

测试目录: `/public/home/ac1npa3sf2/efile_test_1783398377/`
测试文件: 7 个文件已创建在上述目录

请协助排查以上问题，尤其是 Bug 1（下载数据丢失）和 Bug 2（接口无响应）两个最严重的问题。
