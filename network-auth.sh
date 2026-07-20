#!/bin/bash
# ============================================================
# 内网认证自动登录脚本
# 检测断网 → 探测 captive portal → 自动提交认证表单
# 日志: /tmp/network-auth.log
# ============================================================

set -e

USERNAME="sunjg"
PASSWORD="sjg19850223"
LOG_FILE="/tmp/network-auth.log"
CHECK_URLS=("http://captive.apple.com/hotspot-detect.html" "http://www.baidu.com" "http://httpbin.org/get")
MAX_RETRY=3
LOCK_FILE="/tmp/network-auth.lock"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# --- 防止并发 (macOS 兼容: 用 mkdir 原子操作替代 flock) ---
if ! mkdir "${LOCK_FILE}.dir" 2>/dev/null; then
    exit 0  # 已有实例在运行
fi
trap 'rm -rf "${LOCK_FILE}.dir"' EXIT

# --- 1. 检查网络是否正常 ---
check_network() {
    for url in "${CHECK_URLS[@]}"; do
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 -L "$url" 2>/dev/null)
        if [[ "$code" == "200" ]]; then
            return 0  # 网络正常
        fi
    done
    return 1  # 网络异常
}

# --- 2. 探测 captive portal URL ---
detect_portal() {
    # 通过访问 HTTP 站点，跟踪重定向找到 portal 地址
    local redirect_url
    redirect_url=$(curl -s -o /dev/null -w "%{redirect_url}" --max-time 5 -L "http://captive.apple.com/hotspot-detect.html" 2>/dev/null || true)

    if [[ -n "$redirect_url" && "$redirect_url" != *"captive.apple.com"* ]]; then
        echo "$redirect_url"
        return 0
    fi

    # 尝试访问几个常见触发点
    redirect_url=$(curl -s -o /dev/null -w "%{redirect_url}" --max-time 5 "http://httpbin.org/get" 2>/dev/null || true)
    if [[ -n "$redirect_url" && "$redirect_url" != *"httpbin.org"* ]]; then
        echo "$redirect_url"
        return 0
    fi

    return 1
}

# --- 3. 尝试认证 ---
do_auth() {
    local portal_url="$1"

    log "检测到认证页面: $portal_url"

    # 3a. 先获取登录页面，解析 form
    local page_html
    page_html=$(curl -s --max-time 10 -L "$portal_url" 2>/dev/null || true)
    if [[ -z "$page_html" ]]; then
        log "无法获取认证页面内容"
        return 1
    fi

    # 提取 form action
    local form_action
    form_action=$(echo "$page_html" | grep -oPi '<form[^>]*action\s*=\s*["'\'']\K[^"'\'' ]+' | head -1 || true)

    # 如果没有显式 action，使用当前 URL
    if [[ -z "$form_action" ]]; then
        form_action="$portal_url"
    elif [[ "$form_action" == /* || "$form_action" == ./* ]]; then
        # 相对路径 → 拼接完整 URL
        local base
        base=$(echo "$portal_url" | grep -oP '^https?://[^/]+')
        form_action="${base}${form_action#.}"
    fi

    log "提交目标: $form_action"

    # 3b. 尝试多种常见的认证参数组合
    local attempts=(
        "username=${USERNAME}&password=${PASSWORD}"
        "user=${USERNAME}&pass=${PASSWORD}"
        "userName=${USERNAME}&userPassword=${PASSWORD}"
        "auth_user=${USERNAME}&auth_pass=${PASSWORD}"
        "userid=${USERNAME}&passwd=${PASSWORD}"
    )

    for params in "${attempts[@]}"; do
        log "尝试认证参数模式: ${params%%=*} / ${params##*&}"
        local resp
        resp=$(curl -s --max-time 10 -X POST \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" \
            -d "$params" \
            -w "\n%{http_code}" \
            -L "$form_action" 2>/dev/null || true)

        local http_code
        http_code=$(echo "$resp" | tail -1)
        local body
        body=$(echo "$resp" | sed '$d')

        if [[ "$http_code" == "200" || "$http_code" == "302" || "$http_code" == "303" ]]; then
            # 提交后等一下再检测
            sleep 2
            if check_network; then
                log "认证成功! (HTTP $http_code, 参数模式匹配)"
                return 0
            fi
        fi
        log "  返回 HTTP $http_code，网络未恢复"
    done

    # 3c. 智能解析：从页面提取表单字段名
    log "尝试从页面智能解析表单..."
    local input_names
    input_names=$(echo "$page_html" | grep -oPi '<input[^>]*name\s*=\s*["'\'']\K[^"'\'' ]+' 2>/dev/null || true)

    if [[ -n "$input_names" ]]; then
        log "页面表单字段: $(echo $input_names | tr '\n' ' ')"

        # 构建参数：用户名类字段 → USERNAME，密码类字段 → PASSWORD
        local custom_params=""
        while IFS= read -r name; do
            local lower_name="${name,,}"
            if [[ "$lower_name" =~ (user|name|login|uid|account) ]]; then
                custom_params+="&${name}=${USERNAME}"
            elif [[ "$lower_name" =~ (pass|pwd|pin|secret) ]]; then
                custom_params+="&${name}=${PASSWORD}"
            fi
        done <<< "$input_names"
        custom_params="${custom_params#&}"

        if [[ -n "$custom_params" ]]; then
            log "自动构建参数: $custom_params"
            curl -s --max-time 10 -X POST \
                -H "Content-Type: application/x-www-form-urlencoded" \
                -d "$custom_params" \
                -L "$form_action" > /dev/null 2>&1 || true

            sleep 2
            if check_network; then
                log "智能解析认证成功!"
                return 0
            fi
        fi
    fi

    log "所有认证方式均失败，页面内容前 500 字符: ${page_html:0:500}"
    return 1
}

# --- 主流程 ---
main() {
    if check_network; then
        # 网络正常，记录心跳
        # log "网络正常"  # 取消注释可记录详细心跳
        exit 0
    fi

    log "⚠️  检测到网络中断，开始认证..."

    for ((i=1; i<=MAX_RETRY; i++)); do
        log "第 $i/$MAX_RETRY 次尝试..."

        local portal
        portal=$(detect_portal)

        if [[ -z "$portal" ]]; then
            log "未探测到 captive portal 重定向，等待 5 秒..."
            sleep 5
            # 可能是网卡还没获取到 IP，等待 DHCP
            if [[ $i -eq 1 ]]; then
                sleep 10
            fi
            continue
        fi

        if do_auth "$portal"; then
            log "✅ 网络已恢复"
            exit 0
        fi

        sleep 5
    done

    log "❌ 自动认证失败，请手动登录桌面填写认证。"
}

main "$@"
