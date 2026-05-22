# get_user_info

## 需求

实现一个 MCP tool `get_user_info`，用于获取当前认证用户的 SCNet 账户信息。

## 认证

- 从 HTTP 请求路径参数中提取当前 `username`（通过 `get_current_username()`）
- 从 `users` 表中查询对应用户的 `acToken`
- 如果用户未认证（无 acToken），返回错误提示 JSON，包含 `auth_url` 字段指向认证页面

## API 调用

- URL: `SCNET_USER_URL`（`https://www.scnet.cn/ac/openapi/v2/user`）
- Method: GET
- Headers: `{"token": acToken}`
- 无请求参数
- 超时 30s

## 返回值

- 直接返回 API 响应的 JSON 数据
- 返回字段包括：country, language, timeZone, address, fullName, userName, computerCenter, accountName, accountStatus, accountBalance 等

## 自动注册

- 从返回数据中通过 `_build_return_schema(data)` 自动生成返回 schema
- 将工具描述文档写入 `APIs` 表（`INSERT OR REPLACE`），name 为 `get_user_info`
- document JSON 包含 url、method、description、parameters（空 schema，format 为 URLParameter）、returns（format 为 JSON，schema 为自动推导）

## 代码位置

`main.py` 中 `@mcp.tool()` 装饰的 `get_user_info` 函数。
