# SCNet OpenAPI MCP Server - 单元测试计划

## 架构分析

### 两种工具注册方式
1. **动态代理工具** (22个 in APIs DB): 从 `apis.db` 的 `APIs` 表读取 JSON 文档，运行时通过 `make_proxy_tool` 动态生成
2. **静态装饰工具** (43个 @mcp.tool()): 在 `main.py` 中直接定义，包含完整的业务逻辑

### 工具分类及调用链
```
1. 认证/基础
   get_user_info → users.acToken → SCNET_USER_URL
   set_default_cluster → user_cluster.isDefault (仅DB操作)

2. HPC 查询
   hpc_list_available_partitions → hpcUrls/cluster → queuenames API
   hpc_list_running_jobs → AC API (monitor page-list)
   hpc_list_history_jobs → AC API (history page-list)

3. HPC 作业操作
   hpc_submit_job → hpcUrls/cluster → apptemplates/job (POST)
   hpc_get_running_job_detail → hpcUrls → jobs/{jobId}
   hpc_get_history_job_detail → hpcUrls → historyjobs/{id}/{jobId}
   hpc_cancel_job → hpcUrls → jobs (DELETE)
   hpc_query_job_state → hpcUrls → view/jobs/state
   hpc_query_core_num → hpcUrls → view/cpucore/state
   hpc_query_queue_jobs → hpcUrls → view/queue/jobs
   hpc_query_user_quota → hpcUrls → parastor/quota/usernames/{user}
   hpc_query_used_time → hpcUrls → view/walltime/users/{user}

4. 文件系统
   efile_* 系列 (15个) → efileUrls → /efile/openapi/v2/file/*

5. 容器
   container_* 系列 (16个) → aiUrls → ai/openapi/v2/instance/*

6. Notebook
   notebook_* 系列 (13个) → AC API (ac/openapi/v2/notebook/*)
```

## 测试策略

### 第一层：单元测试 (conftest.py 已提供框架)
- 使用 mock_client 模拟 HTTP 响应
- 使用 set_username 设置测试用户上下文
- 验证工具函数参数解析、边界条件、错误处理路径
- 验证 SQL 查询逻辑、路径生成、URL 组装

### 第二层：集成测试 (真实 API 调用)
- 使用真实测试账户 `ac1npa3sf2`
- 通过 MCP 实际调用工具
- 创建资源 → 操作资源 → 清理资源
- 覆盖正向流程和异常流程

## 测试用例设计

### 一、单元工具测试 (静态工具)

#### 1. get_user_info (1 个测试)
- test_get_user_info_success: mock 成功响应
- test_get_user_info_auth_fail: 未认证
- test_get_user_info_http_error: HTTP 500

#### 2. set_default_cluster (4 个测试)
- test_set_by_clusterId: 用 clusterId 设置默认
- test_set_by_clusterName: 用 clusterName 模糊匹配
- test_set_by_clusterName_multiple: 多个匹配返回候选
- test_set_no_params: 缺少参数

#### 3. hpc_submit_job 参数验证 (5 个测试)
- test_submit_empty_cmd: GAP_CMD_FILE 为空
- test_submit_nnode_nodestring_conflict: GAP_NNODE + GAP_NODE_STRING 同时非空
- test_submit_nproc_ppn_conflict: GAP_NPROC + GAP_PPN 同时非空
- test_submit_success: mock 成功提交
- test_submit_no_home_path: 返回错误

### 二、HPC 工具测试 (静态+代理)

#### 4. hpc_list_available_partitions (3 个测试)
- test_list_partitions_success
- test_list_partitions_no_cluster
- test_list_partitions_empty_result

#### 5. hpc_get_running_job_detail (3 个测试)
- test_get_running_success
- test_get_running_not_found
- test_get_running_http_error

#### 6. hpc_get_history_job_detail (3 个测试)
- test_get_history_success
- test_get_history_not_found
- test_get_history_with_acctTime

#### 7. hpc_list_running_jobs (3 个测试)
- test_list_running_default
- test_list_running_with_filters
- test_list_running_http_error

#### 8. hpc_list_history_jobs (3 个测试)
- test_list_history_default
- test_list_history_with_filters
- test_list_history_http_error

#### 9. hpc_cancel_job (3 个测试)
- test_cancel_success
- test_cancel_empty_jobId
- test_cancel_http_error

#### 10-14. HPC 查询工具 (各2-3个测试)
- hpc_query_job_state, hpc_query_core_num, hpc_query_queue_jobs,
  hpc_query_user_quota, hpc_query_used_time

### 三、efile 工具测试 (已有 test_efile.py，补充覆盖)

检查现有覆盖，补充：
- efile_touch, efile_check_permission, efile_move, efile_copy
- efile_rename, efile_delete, efile_exist, efile_folder_create
- efile_preview_file, efile_upload, efile_download
- efile_download_chunk, efile_get_download_link
- efile_open_share, efile_close_share
- efile_async_copy, efile_async_move, efile_async_delete
- efile_async_task_cancel, efile_async_task_list
- efile_chunk_upload, efile_merge_file, efile_get_upload_config
- efile_batch_chunk_upload

### 四、容器工具测试

- container_create, container_start, container_stop
- container_delete, container_execute
- container_query_list, container_query_url
- container_query_detail, container_update_resource
- container_query_resources, container_query_resource_group
- container_query_allowed_mount_dir, container_get_images

### 五、Notebook 工具测试 (已有 test_notebook.py，补充覆盖)

检查现有覆盖，补充缺失的：
- notebook_list_resources, notebook_create, notebook_start
- notebook_list, notebook_detail, notebook_stop
- notebook_release, notebook_rename
- notebook_list_images, notebook_list_model_images
- notebook_query_jupyter_url
- notebook_query_custom_service_url
- notebook_start_custom_service

### 六、集成测试 (真实 API)

按资源生命周期组织：
1. **作业生命周期**: submit(sleep 900) → list_running → query_detail → cancel
2. **文件系统**: touch → list → upload → download → preview → rename → move → copy → delete
3. **异步文件**: touch → async_copy → async_task_list → async_delete
4. **容器**: query_list → query_resource_group → query_images → create → query_detail → stop → delete
5. **Notebook**: list_resources → list_images → create → start → list → detail → query_jupyter_url → stop → release

## 覆盖率目标

| 模块 | 目标覆盖率 |
|------|-----------|
| 静态工具函数 | 95%+ (单元测试 mock) |
| efile 工具 (代理) | 95%+ (集成测试) |
| 容器工具 (静态) | 95%+ (单元测试 mock) |
| Notebook 工具 (静态) | 95%+ (集成测试) |
| 工具函数 (HPC查询) | 90-95% (混合) |
| 辅助函数 | 90%+ |
