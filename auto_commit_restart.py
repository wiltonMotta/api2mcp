#!/usr/bin/env python3
"""
自动监控代码变更：修改后自动 git commit 并重启 MCP 服务
Usage: python auto_commit_restart.py
"""

import hashlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# ---------- 配置 ----------
BASE = Path(__file__).parent
WATCH_FILES = [
    BASE / "main.py",
    BASE / "schema.sql",
    BASE / "init_db.py",
    BASE / "CLAUDE.md",
]
# 收集 instruct/ 目录下所有 .md 文件
WATCH_FILES.extend(sorted(BASE.glob("instruct/*.md")))

COMMIT_INTERVAL_SECONDS = 30  # 两次 commit 之间的最小间隔（秒）
MCP_PORT = 8002
LOG_DIR = Path("/tmp/api2mcp")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "auto_commit.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("auto_commit")


def file_hash(path: Path) -> str:
    """计算文件的 MD5 哈希值（用于检测变更）"""
    return hashlib.md5(path.read_bytes()).hexdigest()


def git_add_and_commit(msg: str) -> bool:
    """执行 git add -A && git commit -m 'msg'"""
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(BASE),
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=str(BASE),
            check=True,
            capture_output=True,
            text=True,
        )
        lines = result.stdout.strip().splitlines()
        sha = lines[0].split()[-1] if lines else "unknown"
        log.info(f"git commit: {msg} -> {sha}")
        return True
    except subprocess.CalledProcessError as e:
        log.warning(f"git commit 失败（可能没有变更需要提交）: {e.stderr.strip()}")
        return False


def restart_mcp_server(port: int = MCP_PORT):
    """通过 restartMcp.sh 重启 MCP 服务"""
    script = BASE / "restartMcp.sh"
    if not script.exists():
        log.error(f"restartMcp.sh 不存在: {script}")
        return False
    try:
        result = subprocess.run(
            ["bash", str(script)],
            cwd=str(BASE),
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        log.info(f"MCP 服务重启完成\n{result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        log.error(f"MCP 服务重启失败: {e.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        log.error("MCP 服务重启超时 (60s)")
        return False


def main():
    log.info("=" * 60)
    log.info("  启动代码变更监控 + 自动 commit + 重启")
    log.info(f"  监控文件 ({len(WATCH_FILES)} 个):")
    for f in WATCH_FILES:
        log.info(f"    - {f}")
    log.info(f"  端口: {MCP_PORT}")
    log.info(f"  日志: {LOG_DIR / 'auto_commit.log'}")
    log.info("=" * 60)

    # 初始计算所有文件的哈希
    hashes = {str(p): file_hash(p) for p in WATCH_FILES}
    last_commit_time = 0

    log.info("开始轮询监控（每2秒检查一次）...")
    log.info("监控已就绪，测试自动 commit + 重启完成")

    while True:
        time.sleep(2)

        changed_files = []
        for p in WATCH_FILES:
            if not p.exists():
                continue
            current_hash = file_hash(p)
            if hashes[str(p)] != current_hash:
                changed_files.append(str(p))
                hashes[str(p)] = current_hash

        if changed_files:
            elapsed = time.time() - last_commit_time
            if elapsed < COMMIT_INTERVAL_SECONDS:
                log.info(
                    f"检测到变更: {changed_files}"
                    f"\n  等待 {COMMIT_INTERVAL_SECONDS - elapsed:.0f}s 后执行 commit+restart"
                )
                time.sleep(max(0, COMMIT_INTERVAL_SECONDS - elapsed + 1))

            log.info(f"检测到文件变更: {changed_files}")
            log.info(f"距离上次 commit 已过 {elapsed:.0f}s")

            msg = f"auto: {', '.join(changed_files)}"
            git_add_and_commit(msg)
            last_commit_time = time.time()

            log.info(f"正在重启 MCP 服务 (端口 {MCP_PORT})...")
            restart_mcp_server()
            log.info("等待 5s 让服务完全启动...")
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("用户中断，退出监控")
        sys.exit(0)
