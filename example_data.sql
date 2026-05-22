-- Example seed data for the APIs table.
-- Each row registers one MCP tool that reverse-proxies a public
-- jsonplaceholder.typicode.com endpoint.

INSERT OR REPLACE INTO APIs (name, document) VALUES (
    'get_todo_by_id',
    '{
        "url": "https://jsonplaceholder.typicode.com/todos/:id",
        "method": "GET",
        "description": "Get one todo item by its ID.",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "id": {
                    "type": "integer",
                    "description": "todo item''s ID",
                    "optional": false
                }
            }
        },
        "returns": {
            "format": "JSON",
            "schema": {
                "userId":    {"type": "integer", "description": "User''s ID",            "optional": false},
                "id":        {"type": "integer", "description": "Todo item''s ID",       "optional": false},
                "title":     {"type": "string",  "description": "Todo item''s title",    "optional": true},
                "completed": {"type": "boolean", "description": "Whether it is done",   "optional": false}
            }
        }
    }'
);

INSERT OR REPLACE INTO APIs (name, document) VALUES (
    'list_todos_by_user',
    '{
        "url": "https://jsonplaceholder.typicode.com/todos",
        "method": "GET",
        "description": "List todo items, optionally filtered by user ID.",
        "parameters": {
            "format": "QueryParameter",
            "schema": {
                "userId": {
                    "type": "integer",
                    "description": "Filter todos by user ID",
                    "optional": true
                }
            }
        },
        "returns": {
            "format": "JSON",
            "schema": {
                "userId":    {"type": "integer", "description": "User''s ID",         "optional": false},
                "id":        {"type": "integer", "description": "Todo item''s ID",    "optional": false},
                "title":     {"type": "string",  "description": "Todo item''s title", "optional": true},
                "completed": {"type": "boolean", "description": "Whether done",      "optional": false}
            }
        }
    }'
);

INSERT OR REPLACE INTO APIs (name, document) VALUES (
    'get_user_by_id',
    '{
        "url": "https://jsonplaceholder.typicode.com/users/:id",
        "method": "GET",
        "description": "Fetch a single user record by its numeric ID.",
        "parameters": {
            "format": "URLParameter",
            "schema": {
                "id": {
                    "type": "integer",
                    "description": "User''s ID",
                    "optional": false
                }
            }
        },
        "returns": {
            "format": "JSON",
            "schema": {
                "id":       {"type": "integer", "description": "User''s ID",      "optional": false},
                "name":     {"type": "string",  "description": "Full name",      "optional": false},
                "username": {"type": "string",  "description": "Login name",     "optional": false},
                "email":    {"type": "string",  "description": "Email address",  "optional": false}
            }
        }
    }'
);

INSERT OR REPLACE INTO APIs (name, document) VALUES (
    'create_post',
    '{
        "url": "https://jsonplaceholder.typicode.com/posts",
        "method": "POST",
        "description": "Create a new post (returns the echoed body with an assigned id).",
        "parameters": {
            "format": "JSON",
            "schema": {
                "userId": {"type": "integer", "description": "Author''s user ID",       "optional": false},
                "title":  {"type": "string",  "description": "Post title",              "optional": false},
                "body":   {"type": "string",  "description": "Post body content",       "optional": false}
            }
        },
        "returns": {
            "format": "JSON",
            "schema": {
                "id":     {"type": "integer", "description": "Generated post ID",  "optional": false},
                "userId": {"type": "integer", "description": "Author''s user ID",  "optional": false},
                "title":  {"type": "string",  "description": "Post title",         "optional": false},
                "body":   {"type": "string",  "description": "Post body",          "optional": false}
            }
        }
    }'
);
