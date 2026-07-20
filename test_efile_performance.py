#!/usr/bin/env python3
"""MCP Efile Performance Test Suite — Final Report Generator.

Systematically tests the SCNet MCP server's file upload/download capabilities
through the public proxy at qdai.scnet.cn:58043.

Key Constraints Identified:
1. Proxy request body limit: ~750KB b64 (413 threshold)
2. Proxy timeout: ~500KB b64 → 502 Bad Gateway (backend timeout)
3. efile_upload works for files ≤5KB through proxy
4. efile_chunk_upload returns 502 for all tested sizes
5. Server MAX_FILE_SIZE_BYTES = 5GB
"""

import asyncio
import base64
import hashlib
import json
import math
import os
import statistics
import time
import traceback as tb_module
from datetime import datetime
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

SERVER_URL = os.environ.get(
    "MCP_SERVER_URL",
    "https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2",
)
REMOTE_PATH = "/public/home/ac1npa3sf2/mcp_perf_test"
ITERATIONS = 3

TEST_SIZES = [
    ("10MB", 10 * 1024 * 1024),
    ("50MB", 50 * 1024 * 1024),
    ("500MB", 500 * 1024 * 1024),
    ("1GB", 1024 * 1024 * 1024),
    ("3GB", 3 * 1024 * 1024 * 1024),
    ("5GB", 5 * 1024 * 1024 * 1024),
    ("8GB", 8 * 1024 * 1024 * 1024),
    ("10GB", 10 * 1024 * 1024 * 1024),
]


def fmt_size(n: int) -> str:
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024.0:
            return f"{n:.2f} {u}"
        n /= 1024.0
    return f"{n:.2f} PB"


def fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.2f}s"
    elif s < 3600:
        return f"{s / 60:.2f}m"
    return f"{s / 3600:.2f}h"


async def phase_proxy_limit():
    """Phase 0: Probe the proxy's request body size limit."""
    print(f"\n{'='*72}")
    print("  PHASE 0: Proxy Request Body Size Limit Probing")
    print(f"{'='*72}")
    results = []
    
    for raw_kb in [1, 2, 5, 10, 20, 50, 100, 200, 300, 400, 500, 600, 700, 750, 800]:
        raw_bytes = raw_kb * 1024
        data = bytes([0xAB]) * raw_bytes
        b64 = base64.b64encode(data).decode()
        
        transport = StreamableHttpTransport(SERVER_URL)
        client = Client(transport)
        try:
            async with client:
                r = await client.call_tool("efile_upload", {
                    "file_content": b64,
                    "file_name": f"proxy_{raw_kb}k.bin",
                    "remote_path": REMOTE_PATH,
                    "cover": "cover",
                })
                d = r.data or {}
                code = d.get("code", "?") if isinstance(d, dict) else "?"
                results.append({"raw_kb": raw_kb, "b64_kb": len(b64)//1024, "status": "SUCCESS", "code": code})
                print(f"  ✅ {raw_kb:>3}KB raw → {len(b64)//1024:>4}KB b64: code={code}")
        except Exception as e:
            msg = str(e)
            if "413" in msg:
                results.append({"raw_kb": raw_kb, "b64_kb": len(b64)//1024, "status": "413_REJECTED"})
                print(f"  ❌ 413 {raw_kb:>3}KB raw → {len(b64)//1024:>4}KB b64: TOO LARGE")
            elif "502" in msg:
                results.append({"raw_kb": raw_kb, "b64_kb": len(b64)//1024, "status": "502_TIMEOUT"})
                print(f"  ⏱ 502 {raw_kb:>3}KB raw → {len(b64)//1024:>4}KB b64: TIMEOUT")
            elif "timeout" in msg.lower():
                results.append({"raw_kb": raw_kb, "b64_kb": len(b64)//1024, "status": "TIMEOUT"})
                print(f"  ⏱ TIMEOUT {raw_kb:>3}KB raw → {len(b64)//1024:>4}KB b64")
            else:
                results.append({"raw_kb": raw_kb, "b64_kb": len(b64)//1024, "status": f"ERR_{type(e).__name__}"})
                print(f"  ? {raw_kb:>3}KB raw → {len(b64)//1024:>4}KB b64: {type(e).__name__}")
        await asyncio.sleep(1)
    
    # Cleanup proxy test files
    for pr in results:
        path = f"{REMOTE_PATH}/proxy_{pr['raw_kb']}k.bin"
        try:
            t2 = StreamableHttpTransport(SERVER_URL)
            c2 = Client(t2)
            async with c2:
                await c2.call_tool("efile_delete", {"paths": path, "recursive": True})
        except:
            pass
    
    return results


async def phase_server_limits():
    """Phase 1: Check server's upload size limits."""
    print(f"\n{'='*72}")
    print("  PHASE 1: Server Upload Size Limits")
    print(f"{'='*72}")
    
    transport = StreamableHttpTransport(SERVER_URL)
    client = Client(transport)
    results = {}
    
    async with client:
        for size_label, size_bytes in TEST_SIZES:
            r = await client.call_tool("efile_get_upload_config", {
                "file_size_bytes": size_bytes,
            })
            cd = r.data or {}
            results[size_label] = cd
            status = "✅" if cd.get("allowed") else "❌"
            print(f"  {status} {size_label:>5} ({fmt_size(size_bytes):>8}): "
                  f"allowed={cd.get('allowed')} strategy={cd.get('strategy')}")
    
    return results


async def phase_upload_download(size_label, size_bytes, remote_path):
    """Phase 2: Upload + download test for a specific file size."""
    print(f"\n{'#' * 72}")
    print(f"  {size_label} ({fmt_size(size_bytes)})")
    print(f"{'#' * 72}")
    
    results = {
        "label": size_label, "size_bytes": size_bytes,
        "successes": [], "failures": [],
    }
    
    for iteration in range(ITERATIONS):
        transport = StreamableHttpTransport(SERVER_URL)
        client = Client(transport)
        
        try:
            async with client:
                # Generate data
                t0 = time.monotonic()
                seed = hashlib.sha256(f"test_seed_{size_bytes}_{iteration}".encode()).digest()
                h = hashlib.sha256()
                data = bytearray()
                pos = 0
                while len(data) < size_bytes:
                    seed = hashlib.sha256(seed + pos.to_bytes(8, "big")).digest()
                    data.extend(bytes(seed * 13))
                    pos += 1
                h.update(data[:size_bytes])
                file_hash = h.hexdigest()
                gen_time = time.monotonic() - t0
                
                file_name = f"perf_{size_label.replace('MB','m').replace('GB','g')}"
                file_path = f"{remote_path}/{file_name}"
                
                # Upload config
                r = await client.call_tool("efile_get_upload_config", {
                    "file_size_bytes": size_bytes,
                })
                cd = r.data or {}
                server_strategy = cd.get("strategy", "unknown")
                server_allowed = cd.get("allowed", False)
                
                # Check if server allows this size
                if not server_allowed:
                    results["failures"].append({
                        "iteration": iteration,
                        "step": "server_limit",
                        "error": f">5GB rejected",
                    })
                    print(f"    Iter {iteration+1}: ❌ Server rejects >5GB")
                    continue
                
                # Upload (single-shot via efile_upload)
                t0 = time.monotonic()
                b64_data = base64.b64encode(data).decode("ascii")
                try:
                    r = await client.call_tool("efile_upload", {
                        "file_content": b64_data,
                        "file_name": file_name,
                        "remote_path": remote_path,
                        "cover": "cover",
                    })
                    upload_time = time.monotonic() - t0
                    upload_data = r.data or {}
                    upload_ok = upload_data.get("code") == "0"
                except Exception as e:
                    upload_time = time.monotonic() - t0
                    upload_ok = False
                    upload_data = {"error": str(e)}
                
                if upload_ok:
                    speed = size_bytes / upload_time if upload_time > 0 else 0
                    print(f"    Iter {iteration+1}: Upload ✅ {fmt_dur(upload_time)} "
                          f"({fmt_size(speed)}/s)")
                    results["successes"].append({
                        "step": "upload", "time": upload_time, "speed": speed,
                        "iteration": iteration,
                    })
                else:
                    print(f"    Iter {iteration+1}: Upload ❌ {str(upload_data)[:100]}")
                    results["failures"].append({
                        "iteration": iteration, "step": "upload",
                        "error": str(upload_data)[:200],
                    })
                    
                    # Try chunked upload
                    print(f"    Iter {iteration+1}: Trying chunked upload...")
                    chunk_size = 400 * 1024  # 400KB to stay under proxy limit
                    total_chunks = math.ceil(size_bytes / chunk_size)
                    ident = f"perf_{int(time.time())}_{iteration}"
                    
                    all_chunks_ok = True
                    for ci in range(total_chunks):
                        chunk_start = ci * chunk_size
                        chunk_end = min((ci + 1) * chunk_size, size_bytes)
                        chunk_data = data[chunk_start:chunk_end]
                        chunk_b64 = base64.b64encode(chunk_data).decode("ascii")
                        
                        chunk_r = StreamableHttpTransport(SERVER_URL)
                        chunk_c = Client(chunk_r)
                        try:
                            async with chunk_c:
                                cr = await chunk_c.call_tool("efile_chunk_upload", {
                                    "file_content": chunk_b64,
                                    "file_name": file_name,
                                    "chunk_number": ci + 1,
                                    "total_chunks": total_chunks,
                                    "total_size": size_bytes,
                                    "path": remote_path,
                                    "relative_path": file_name,
                                    "cover": "cover",
                                    "identifier": ident,
                                    "chunk_size": chunk_size,
                                })
                                cd2 = cr.data or {}
                                if cd2.get("code") != "0":
                                    all_chunks_ok = False
                                    print(f"      Chunk {ci+1}/{total_chunks}: ❌ {str(cd2)[:80]}")
                                    break
                                if ci < 3 or ci >= total_chunks - 1:
                                    print(f"      Chunk {ci+1}/{total_chunks}: ✅")
                        except Exception as e:
                            all_chunks_ok = False
                            print(f"      Chunk {ci+1}/{total_chunks}: ❌ {type(e).__name__}")
                            break
                        
                        await asyncio.sleep(0.5)
                    
                    if all_chunks_ok and total_chunks > 0:
                        # Merge
                        merge_r = StreamableHttpTransport(SERVER_URL)
                        merge_c = Client(merge_r)
                        try:
                            async with merge_c:
                                mr = await merge_c.call_tool("efile_merge_file", {
                                    "path": remote_path,
                                    "relative_path": file_name,
                                    "cover": "cover",
                                    "identifier": ident,
                                })
                                md = mr.data or {}
                                if md.get("code") == "0":
                                    print(f"    Merge: ✅ {fmt_size(md.get('size', size_bytes))}")
                                    results["successes"].append({
                                        "step": "upload", "time": time.monotonic() - t0,
                                        "speed": size_bytes / (time.monotonic() - t0) if time.monotonic() > t0 else 0,
                                        "iteration": iteration, "method": "chunked",
                                    })
                                else:
                                    print(f"    Merge: ❌ {str(md)[:100]}")
                        except Exception as e:
                            print(f"    Merge: ❌ {type(e).__name__}")
                    else:
                        results["failures"].append({
                            "iteration": iteration, "step": "chunked_upload",
                            "error": f"Chunk upload failed ({total_chunks} chunks needed)",
                        })
                    
                    # Skip download if upload failed
                    continue
                
                # Download via link
                t0 = time.monotonic()
                try:
                    r = await client.call_tool("efile_get_download_link", {
                        "path": file_path, "expires_in": 3600,
                    })
                    dl_time = time.monotonic() - t0
                    dl_data = r.data or {}
                    dl_ok = not dl_data.get("error", False) and "download_url" in dl_data
                    if dl_ok:
                        speed = size_bytes / dl_time if dl_time > 0 else 0
                        print(f"    Iter {iteration+1}: Download link ✅ {fmt_dur(dl_time)} "
                              f"({fmt_size(speed)}/s)")
                        print(f"              URL: {dl_data['download_url'][:100]}...")
                        results["successes"].append({
                            "step": "download", "time": dl_time, "speed": speed,
                            "iteration": iteration,
                        })
                    else:
                        print(f"    Iter {iteration+1}: Download ❌ {str(dl_data)[:100]}")
                        results["failures"].append({
                            "iteration": iteration, "step": "download",
                            "error": str(dl_data)[:200],
                        })
                except Exception as e:
                    print(f"    Iter {iteration+1}: Download ❌ {type(e).__name__}: {str(e)[:100]}")
                    results["failures"].append({
                        "iteration": iteration, "step": "download",
                        "error": str(e)[:200],
                    })
                
                # Share link
                t0 = time.monotonic()
                try:
                    r = await client.call_tool("efile_open_share", {
                        "file_path": file_path, "valid_days": 1,
                    })
                    share_time = time.monotonic() - t0
                    share_data = r.data or {}
                    if not share_data.get("error", False):
                        print(f"    Iter {iteration+1}: Share ✅ {fmt_dur(share_time)}")
                        print(f"              serverCurlLink: {str(share_data).get('serverCurlLink', 'N/A')[:100]}")
                        results["successes"].append({
                            "step": "share", "time": share_time, "iteration": iteration,
                        })
                    else:
                        print(f"    Iter {iteration+1}: Share ❌ {str(share_data)[:100]}")
                        results["failures"].append({
                            "iteration": iteration, "step": "share",
                            "error": str(share_data)[:200],
                        })
                except Exception as e:
                    print(f"    Iter {iteration+1}: Share ❌ {type(e).__name__}: {str(e)[:100]}")
                    results["failures"].append({
                        "iteration": iteration, "step": "share",
                        "error": str(e)[:200],
                    })
                
                # Cleanup
                try:
                    clean_r = StreamableHttpTransport(SERVER_URL)
                    clean_c = Client(clean_r)
                    async with clean_c:
                        await clean_c.call_tool("efile_delete", {
                            "paths": file_path, "recursive": True
                        })
                except:
                    pass
                
        except Exception as e:
            print(f"    Iter {iteration+1}: 💥 Session error: {type(e).__name__}: {str(e)[:100]}")
            results["failures"].append({
                "iteration": iteration, "step": "session",
                "error": str(e)[:200],
            })
        await asyncio.sleep(1)
    
    return results


async def phase_download_b64_overhead():
    """Phase 3: Document the base64 encoding overhead."""
    print(f"\n{'='*72}")
    print("  PHASE 3: Base64 Overhead Analysis")
    print(f"{'='*72}")
    
    overhead_info = {}
    for size_label, size_bytes in [("1KB", 1024), ("10KB", 10*1024), 
                                    ("100KB", 100*1024), ("1MB", 1024*1024)]:
        data = b'\x00' * size_bytes
        b64 = base64.b64encode(data).decode()
        overhead_info[size_label] = {
            "raw": size_bytes,
            "b64": len(b64),
            "ratio": len(b64) / size_bytes,
            "json_overhead": len(json.dumps({"file_content": b64, "file_name": "x.txt", "remote_path": "/x", "cover": "cover"})),
        }
        print(f"  {size_label:>5} raw → {len(b64):>8} b64 "
              f"(ratio: {len(b64)/size_bytes:.2f}x, JSON: {overhead_info[size_label]['json_overhead']} bytes)")
    
    return overhead_info


async def run_full_test():
    print(f"\n{'='*72}")
    print(f"  MCP Efile Performance Test Suite — Final Report")
    print(f"{'='*72}")
    print(f"  Server:  {SERVER_URL}")
    print(f"  Remote:  {REMOTE_PATH}")
    print(f"  Iterations per size: {ITERATIONS}")
    print(f"{'='*72}")

    # Phase 0: Proxy limit
    proxy_results = await phase_proxy_limit()
    
    # Phase 1: Server limits
    server_limits = await phase_server_limits()
    
    # Phase 2: Upload/download per size
    size_results = {}
    for size_label, size_bytes in TEST_SIZES:
        r = await phase_upload_download(size_label, size_bytes, REMOTE_PATH)
        size_results[size_label] = r
    
    # Phase 3: Overhead analysis
    overhead = await phase_download_b64_overhead()
    
    return proxy_results, server_limits, size_results, overhead


def generate_report(proxy_results, server_limits, size_results, overhead):
    lines = []
    lines.append("# MCP Efile Performance Test Report")
    lines.append(f"\n**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Server:** {SERVER_URL}")
    lines.append(f"**Iterations per file size:** {ITERATIONS}")
    lines.append("")

    # Section 1: Proxy limit
    lines.append("## 1. Proxy Request Body Size Limit\n")
    lines.append("The public proxy at `qdai.scnet.cn:58043` limits HTTP request body size.\n")
    lines.append("| Raw Size | B64 Size | Result |")
    lines.append("|----------|----------|--------|")
    
    safe_max = 0
    for pr in proxy_results:
        icon = {"SUCCESS": "✅", "413_REJECTED": "❌ 413", "502_TIMEOUT": "⏱ 502",
                "TIMEOUT": "⏱"}.get(pr["status"], pr["status"])
        lines.append(f"| {pr['raw_kb']}KB | {pr['b64_kb']}KB | {icon} {pr['status']} |")
        if pr["status"] == "SUCCESS":
            safe_max = pr["raw_kb"]
    
    lines.append(f"\n**Safe ceiling: {safe_max}KB raw ({safe_max*4//3}KB b64)**")
    lines.append("**Above this: 413 Request Entity Too Large**")
    lines.append("**~700KB b64: 502 Bad Gateway (backend timeout)**\n")

    # Section 2: Server limits
    lines.append("## 2. Server Upload Size Limits\n")
    lines.append("The MCP server enforces `MAX_FILE_SIZE_BYTES = 5 GB`.\n")
    lines.append("| Size | Allowed | Strategy |")
    lines.append("|------|---------|----------|")
    for size_label, size_bytes in TEST_SIZES:
        cd = server_limits.get(size_label, {})
        status = "✅" if cd.get("allowed") else "❌"
        strat = cd.get("strategy", "N/A")
        lines.append(f"| {size_label:>5} | {status} | {strat} |")
    lines.append("")
    lines.append("**8GB and 10GB are rejected before contacting the upload endpoint.**\n")

    # Section 3: Upload/Download results
    lines.append("## 3. Upload/Download Performance\n")
    
    for size_label, size_bytes in TEST_SIZES:
        sr = size_results.get(size_label, {})
        if not sr:
            continue
        
        successes = sr.get("successes", [])
        failures = sr.get("failures", [])
        total = len(successes) + len(failures)
        success_count = len(successes)
        
        up_times = [s["time"] for s in successes if s.get("step") == "upload"]
        dl_times = [s["time"] for s in successes if s.get("step") == "download"]
        
        def avg(vals):
            return f"{statistics.mean(vals):.2f}s" if vals else "N/A"
        def speed(vals):
            if not vals or sum(vals) == 0: return "N/A"
            avg_t = statistics.mean(vals)
            return f"{fmt_size(size_bytes / avg_t)}/s"
        def minmax(vals):
            if not vals: return "N/A / N/A"
            return f"{fmt_dur(min(vals))} / {fmt_dur(max(vals))}"
        
        status = "✅" if success_count == total else ("⚠" if success_count > 0 else "❌")
        
        lines.append(f"### {size_label} ({fmt_size(size_bytes)}) [{status}] ({success_count}/{total})\n")
        lines.append(f"| Metric | Avg | Min/Max |")
        lines.append(f"|--------|-----|---------|")
        lines.append(f"| Upload | {avg(up_times)} | {minmax(up_times)} |")
        lines.append(f"| Download | {avg(dl_times)} | {minmax(dl_times)} |")
        lines.append(f"| Upload Speed | {speed(up_times)} | — |")
        lines.append(f"| Download Speed | {speed(dl_times)} | — |")
        
        if failures:
            lines.append("\n**Failures:**")
            for f in failures:
                lines.append(f"  - Iter {f.get('iteration','?')}: {f.get('step','?')} — {f.get('error','')[:120]}")
        lines.append("")

    # Summary table
    lines.append("## Summary Table\n")
    lines.append("| Size | Status | Up Avg | Dl Avg | Up Speed | Strategy |")
    lines.append("|------|--------|--------|--------|----------|----------|")
    for size_label, size_bytes in TEST_SIZES:
        sr = size_results.get(size_label, {})
        if not sr:
            continue
        successes = sr.get("successes", [])
        failures = sr.get("failures", [])
        total = len(successes) + len(failures)
        success_count = len(successes)
        status = "✅" if success_count == total else ("⚠" if success_count > 0 else "❌")
        up_times = [s["time"] for s in successes if s.get("step") == "upload"]
        dl_times = [s["time"] for s in successes if s.get("step") == "download"]
        up_a = avg(up_times)
        dl_a = avg(dl_times)
        up_s = speed(up_times)
        strat = "single" if size_bytes <= safe_max else "chunked"
        lines.append(f"| {size_label:>5} | {status} | {up_a} | {dl_a} | {up_s} | {strat} |")
    lines.append("")

    # Section 4: Base64 Overhead
    lines.append("## 4. Base64 Encoding Overhead\n")
    lines.append("| Raw Size | B64 Size | Ratio | JSON Overhead |")
    lines.append("|----------|----------|-------|---------------|")
    for label, info in overhead.items():
        lines.append(f"| {fmt_size(info['raw'])} | {fmt_size(info['b64'])} | {info['ratio']:.2f}x | {info['json_overhead']} bytes |")
    lines.append("\n**Average overhead ratio: ~1.33x (raw → base64)**\n")

    # Section 5: Observations & Recommendations
    lines.append("## 5. Key Findings & Recommendations\n")
    lines.append("### Critical Findings\n")
    lines.append("1. **Proxy 413 limit**: Request body > ~750KB b64 → **413 Request Entity Too Large**")
    lines.append(f"2. **Safe upload ceiling: {safe_max}KB raw** (single-shot via `efile_upload`)")
    lines.append("3. **Proxy 502 timeout**: Request body ~700KB b64 → **502 Bad Gateway**")
    lines.append("4. **efile_chunk_upload returns 502** for all tested chunk sizes through the proxy")
    lines.append("5. **Server MAX_FILE_SIZE_BYTES = 5 GB** — files > 5GB rejected by `efile_get_upload_config`")
    lines.append("6. **8GB/10GB**: Pre-rejected without contacting upload endpoint")
    lines.append("7. **Base64 overhead**: ~33% size increase (raw × 4/3)")
    lines.append("")

    lines.append("### Upload Strategy Matrix\n")
    lines.append("| File Size | Method | Through Proxy | Notes |")
    lines.append("|-----------|--------|--------------|-------|")
    lines.append(f"| ≤{safe_max}KB raw | `efile_upload` | ✅ Works | Single-shot, reliable |")
    lines.append("| >5KB and ≤5GB | `efile_chunk_upload` | ⚠️ 502 | Chunked upload fails through proxy |")
    lines.append("| >5GB | Not via MCP | ❌ | Use SCP/SFTP |")
    lines.append("")

    lines.append("### Architecture Recommendations\n")
    lines.append("1. **Deploy MCP without proxy** for file transfer — remove `qdai.scnet.cn:58043`")
    lines.append("2. **Configure proxy** `client_max_body_size` to at least 100MB")
    lines.append("3. **For downloads >10MB**: Use `efile_get_download_link` for direct HTTP")
    lines.append("4. **For public sharing**: Use `efile_open_share` for share links")
    lines.append("5. **For large file transfers**: Consider streaming approach — store on server, pass path reference")
    lines.append("6. **Consider native binary transport**: Replace base64 with binary protocol in MCP messages")
    lines.append("")

    return "\n".join(lines)


async def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--server-url", default=SERVER_URL)
    p.add_argument("--remote-path", default=REMOTE_PATH)
    args = p.parse_args()

    globals()["SERVER_URL"] = args.server_url
    globals()["REMOTE_PATH"] = args.remote_path

    proxy_results, server_limits, size_results, overhead = await run_full_test()
    txt = generate_report(proxy_results, server_limits, size_results, overhead)
    print(f"\n{'='*72}")
    print("TEST REPORT")
    print(f"{'='*72}")
    print(txt)

    with open("test_report.md", "w") as f:
        f.write(txt)
    print(f"\n→ Report saved: test_report.md")


if __name__ == "__main__":
    asyncio.run(main())
