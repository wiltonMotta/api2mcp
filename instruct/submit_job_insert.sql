INSERT OR REPLACE INTO APIs (name, document) VALUES (
    'hpc_submit_job',
    '{
        "url": "{hpcUrls}/hpc/openapi/v2/apptemplates/{apptype}/{appname}/job",
        "method": "POST",
        "description": "向 HPC 集群提交一个作业。调用前需先通过 hpc_hpc_list_available_partitions 获取可用队列信息，并从中选择最合适的队列。后端会自动处理认证、集群凭据获取、调度器 ID 获取、默认值填充以及作业名称生成。",
        "parameters": {
            "format": "JSON",
            "schema": {
                "clusterId": {
                    "type": "integer",
                    "description": "从 hpc_hpc_list_available_partitions 返回结果中选定的集群 ID",
                    "optional": false
                },
                "GAP_QUEUE": {
                    "type": "string",
                    "description": "队列名称。可从 hpc_list_available_partitions 返回的 queues 列表中获取（对应 queueName 字段）",
                    "optional": false
                },
                "GAP_CMD_FILE": {
                    "type": "string",
                    "description": "命令行内容（如需换行，请使用 \\n），例如 sleep 500 或 python train.py",
                    "optional": false
                },
                "GAP_NNODE": {
                    "type": "string",
                    "description": "节点个数。与 GAP_NODE_STRING 互斥，指定 GAP_NNODE 时 GAP_NODE_STRING 必须为 \"\"",
                    "optional": true
                },
                "GAP_NODE_STRING": {
                    "type": "string",
                    "description": "指定具体节点。与 GAP_NNODE 互斥，指定 GAP_NODE_STRING 时 GAP_NNODE 必须为 \"\"",
                    "optional": true
                },
                "GAP_WALL_TIME": {
                    "type": "string",
                    "description": "最大运行时长，格式 HH:MM:ss。默认 24:00:00",
                    "optional": true
                },
                "GAP_NPROC": {
                    "type": "string",
                    "description": "总核心数（GAP_NPROC 和 GAP_PPN 选其一填写）",
                    "optional": true
                },
                "GAP_PPN": {
                    "type": "string",
                    "description": "CPU核心/节点（GAP_NPROC 和 GAP_PPN 选其一填写）",
                    "optional": true
                },
                "GAP_NGPU": {
                    "type": "string",
                    "description": "GPU卡数/节点",
                    "optional": true
                },
                "GAP_NDCU": {
                    "type": "string",
                    "description": "DCU卡数/节点",
                    "optional": true
                },
                "GAP_JOB_MEM": {
                    "type": "string",
                    "description": "每个节点内存值，单位 MB 或 GB",
                    "optional": true
                },
                "GAP_EXCLUSIVE": {
                    "type": "string",
                    "description": "是否独占节点，1 为独占，空字符串为非独占",
                    "optional": true
                },
                "GAP_WORK_DIR": {
                    "type": "string",
                    "description": "工作路径。若未提供，默认为 user_cluster 表中该用户的 homePath 拼接 _job_YYYY_mm_dd_HHiiss",
                    "optional": true
                },
                "GAP_APPNAME": {
                    "type": "string",
                    "description": "BASE（基础应用），支持填写具体的应用英文名称。默认 BASE",
                    "optional": true
                },
                "GAP_MULTI_SUB": {
                    "type": "string",
                    "description": "作业组长度，建议为小于等于 50 的正整数",
                    "optional": true
                },
                "GAP_STD_OUT_FILE": {
                    "type": "string",
                    "description": "标准输出文件路径。若未提供，默认为工作路径/std.out.%j",
                    "optional": true
                },
                "GAP_STD_ERR_FILE": {
                    "type": "string",
                    "description": "标准错误文件路径。若未提供，默认为工作路径/std.err.%j",
                    "optional": true
                }
            }
        },
        "returns": {
            "format": "JSON",
            "schema": {
                "jobID": {
                    "type": "string",
                    "description": "作业 ID",
                    "optional": false
                },
                "status": {
                    "type": "string",
                    "description": "作业提交状态",
                    "optional": false
                },
                "message": {
                    "type": "string",
                    "description": "返回消息",
                    "optional": true
                }
            }
        }
    }'
);
