# MCP Efile Performance Test Report

**Date:** 2026-06-03 20:51:32
**Server:** https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2
**Iterations per file size:** 3

## 1. Proxy Request Body Size Limit

The public proxy at `qdai.scnet.cn:58043` limits HTTP request body size.

| Raw Size | B64 Size | Result |
|----------|----------|--------|
| 1KB | 1KB | ✅ SUCCESS |
| 2KB | 2KB | ✅ SUCCESS |
| 5KB | 6KB | ✅ SUCCESS |
| 10KB | 13KB | ⏱ 502 502_TIMEOUT |
| 20KB | 26KB | ⏱ 502 502_TIMEOUT |
| 50KB | 66KB | ⏱ 502 502_TIMEOUT |
| 100KB | 133KB | ⏱ 502 502_TIMEOUT |
| 200KB | 266KB | ⏱ 502 502_TIMEOUT |
| 300KB | 400KB | ⏱ 502 502_TIMEOUT |
| 400KB | 533KB | ⏱ 502 502_TIMEOUT |
| 500KB | 666KB | ⏱ 502 502_TIMEOUT |
| 600KB | 800KB | ⏱ 502 502_TIMEOUT |
| 700KB | 933KB | ⏱ 502 502_TIMEOUT |
| 750KB | 1000KB | ⏱ 502 502_TIMEOUT |
| 800KB | 1066KB | ❌ 413 413_REJECTED |

**Safe ceiling: 5KB raw (6KB b64)**
**Above this: 413 Request Entity Too Large**
**~700KB b64: 502 Bad Gateway (backend timeout)**

## 2. Server Upload Size Limits

The MCP server enforces `MAX_FILE_SIZE_BYTES = 5 GB`.

| Size | Allowed | Strategy |
|------|---------|----------|
|  10MB | ✅ | single |
|  50MB | ✅ | single |
| 500MB | ✅ | chunked |
|   1GB | ✅ | chunked |
|   3GB | ✅ | chunked |
|   5GB | ✅ | chunked |
|   8GB | ❌ | N/A |
|  10GB | ❌ | N/A |

**8GB and 10GB are rejected before contacting the upload endpoint.**

## 3. Upload/Download Performance

### 10MB (10.00 MB) [❌] (0/9)

| Metric | Avg | Min/Max |
|--------|-----|---------|
| Upload | N/A | N/A / N/A |
| Download | N/A | N/A / N/A |
| Upload Speed | N/A | — |
| Download Speed | N/A | — |

**Failures:**
  - Iter 0: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 0: chunked_upload — Chunk upload failed (26 chunks needed)
  - Iter 0: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F
  - Iter 1: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 1: chunked_upload — Chunk upload failed (26 chunks needed)
  - Iter 1: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F
  - Iter 2: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 2: chunked_upload — Chunk upload failed (26 chunks needed)
  - Iter 2: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F

### 50MB (50.00 MB) [❌] (0/9)

| Metric | Avg | Min/Max |
|--------|-----|---------|
| Upload | N/A | N/A / N/A |
| Download | N/A | N/A / N/A |
| Upload Speed | N/A | — |
| Download Speed | N/A | — |

**Failures:**
  - Iter 0: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 0: chunked_upload — Chunk upload failed (128 chunks needed)
  - Iter 0: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F
  - Iter 1: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 1: chunked_upload — Chunk upload failed (128 chunks needed)
  - Iter 1: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F
  - Iter 2: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 2: chunked_upload — Chunk upload failed (128 chunks needed)
  - Iter 2: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F

### 500MB (500.00 MB) [❌] (0/9)

| Metric | Avg | Min/Max |
|--------|-----|---------|
| Upload | N/A | N/A / N/A |
| Download | N/A | N/A / N/A |
| Upload Speed | N/A | — |
| Download Speed | N/A | — |

**Failures:**
  - Iter 0: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 0: chunked_upload — Chunk upload failed (1280 chunks needed)
  - Iter 0: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F
  - Iter 1: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 1: chunked_upload — Chunk upload failed (1280 chunks needed)
  - Iter 1: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F
  - Iter 2: upload — {'error': "Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac
  - Iter 2: chunked_upload — Chunk upload failed (1280 chunks needed)
  - Iter 2: session — Client error '413 Request Entity Too Large' for url 'https://c-2056205187675406338.qdai.scnet.cn:58043/mcp/ac1npa3sf2'
F

### 1GB (1.00 GB) [❌] (0/9)

| Metric | Avg | Min/Max |
|--------|-----|---------|
| Upload | N/A | N/A / N/A |
| Download | N/A | N/A / N/A |
| Upload Speed | N/A | — |
| Download Speed | N/A | — |

**Failures:**
  - Iter 0: upload — {'error': ''}
  - Iter 0: chunked_upload — Chunk upload failed (2622 chunks needed)
  - Iter 0: session — 
  - Iter 1: upload — {'error': ''}
  - Iter 1: chunked_upload — Chunk upload failed (2622 chunks needed)
  - Iter 1: session — 
  - Iter 2: upload — {'error': ''}
  - Iter 2: chunked_upload — Chunk upload failed (2622 chunks needed)
  - Iter 2: session — 

### 3GB (3.00 GB) [❌] (0/9)

| Metric | Avg | Min/Max |
|--------|-----|---------|
| Upload | N/A | N/A / N/A |
| Download | N/A | N/A / N/A |
| Upload Speed | N/A | — |
| Download Speed | N/A | — |

**Failures:**
  - Iter 0: upload — {'error': '[BUF] malloc failure (_ssl.c:2427)'}
  - Iter 0: chunked_upload — Chunk upload failed (7865 chunks needed)
  - Iter 0: session — [BUF] malloc failure (_ssl.c:2427)
  - Iter 1: upload — {'error': '[BUF] malloc failure (_ssl.c:2427)'}
  - Iter 1: chunked_upload — Chunk upload failed (7865 chunks needed)
  - Iter 1: session — [BUF] malloc failure (_ssl.c:2427)
  - Iter 2: upload — {'error': '[BUF] malloc failure (_ssl.c:2427)'}
  - Iter 2: chunked_upload — Chunk upload failed (7865 chunks needed)
  - Iter 2: session — [BUF] malloc failure (_ssl.c:2427)

### 5GB (5.00 GB) [❌] (0/9)

| Metric | Avg | Min/Max |
|--------|-----|---------|
| Upload | N/A | N/A / N/A |
| Download | N/A | N/A / N/A |
| Upload Speed | N/A | — |
| Download Speed | N/A | — |

**Failures:**
  - Iter 0: upload — {'error': '[BUF] malloc failure (_ssl.c:2427)'}
  - Iter 0: chunked_upload — Chunk upload failed (13108 chunks needed)
  - Iter 0: session — [BUF] malloc failure (_ssl.c:2427)
  - Iter 1: upload — {'error': '[BUF] malloc failure (_ssl.c:2427)'}
  - Iter 1: chunked_upload — Chunk upload failed (13108 chunks needed)
  - Iter 1: session — [BUF] malloc failure (_ssl.c:2427)
  - Iter 2: upload — {'error': '[BUF] malloc failure (_ssl.c:2427)'}
  - Iter 2: chunked_upload — Chunk upload failed (13108 chunks needed)
  - Iter 2: session — [BUF] malloc failure (_ssl.c:2427)

### 8GB (8.00 GB) [❌] (0/3)

| Metric | Avg | Min/Max |
|--------|-----|---------|
| Upload | N/A | N/A / N/A |
| Download | N/A | N/A / N/A |
| Upload Speed | N/A | — |
| Download Speed | N/A | — |

**Failures:**
  - Iter 0: server_limit — >5GB rejected
  - Iter 1: server_limit — >5GB rejected
  - Iter 2: server_limit — >5GB rejected

### 10GB (10.00 GB) [❌] (0/3)

| Metric | Avg | Min/Max |
|--------|-----|---------|
| Upload | N/A | N/A / N/A |
| Download | N/A | N/A / N/A |
| Upload Speed | N/A | — |
| Download Speed | N/A | — |

**Failures:**
  - Iter 0: server_limit — >5GB rejected
  - Iter 1: server_limit — >5GB rejected
  - Iter 2: server_limit — >5GB rejected

## Summary Table

| Size | Status | Up Avg | Dl Avg | Up Speed | Strategy |
|------|--------|--------|--------|----------|----------|
|  10MB | ❌ | N/A | N/A | N/A | chunked |
|  50MB | ❌ | N/A | N/A | N/A | chunked |
| 500MB | ❌ | N/A | N/A | N/A | chunked |
|   1GB | ❌ | N/A | N/A | N/A | chunked |
|   3GB | ❌ | N/A | N/A | N/A | chunked |
|   5GB | ❌ | N/A | N/A | N/A | chunked |
|   8GB | ❌ | N/A | N/A | N/A | chunked |
|  10GB | ❌ | N/A | N/A | N/A | chunked |

## 4. Base64 Encoding Overhead

| Raw Size | B64 Size | Ratio | JSON Overhead |
|----------|----------|-------|---------------|
| 1.00 KB | 1.34 KB | 1.34x | 1449 bytes |
| 10.00 KB | 13.34 KB | 1.33x | 13737 bytes |
| 100.00 KB | 133.34 KB | 1.33x | 136617 bytes |
| 1.00 MB | 1.33 MB | 1.33x | 1398185 bytes |

**Average overhead ratio: ~1.33x (raw → base64)**

## 5. Key Findings & Recommendations

### Critical Findings

1. **Proxy 413 limit**: Request body > ~750KB b64 → **413 Request Entity Too Large**
2. **Safe upload ceiling: 5KB raw** (single-shot via `efile_upload`)
3. **Proxy 502 timeout**: Request body ~700KB b64 → **502 Bad Gateway**
4. **efile_chunk_upload returns 502** for all tested chunk sizes through the proxy
5. **Server MAX_FILE_SIZE_BYTES = 5 GB** — files > 5GB rejected by `efile_get_upload_config`
6. **8GB/10GB**: Pre-rejected without contacting upload endpoint
7. **Base64 overhead**: ~33% size increase (raw × 4/3)

### Upload Strategy Matrix

| File Size | Method | Through Proxy | Notes |
|-----------|--------|--------------|-------|
| ≤5KB raw | `efile_upload` | ✅ Works | Single-shot, reliable |
| >5KB and ≤5GB | `efile_chunk_upload` | ⚠️ 502 | Chunked upload fails through proxy |
| >5GB | Not via MCP | ❌ | Use SCP/SFTP |

### Architecture Recommendations

1. **Deploy MCP without proxy** for file transfer — remove `qdai.scnet.cn:58043`
2. **Configure proxy** `client_max_body_size` to at least 100MB
3. **For downloads >10MB**: Use `efile_get_download_link` for direct HTTP
4. **For public sharing**: Use `efile_open_share` for share links
5. **For large file transfers**: Consider streaming approach — store on server, pass path reference
6. **Consider native binary transport**: Replace base64 with binary protocol in MCP messages
