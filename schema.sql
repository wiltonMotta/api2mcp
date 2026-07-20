-- Schema for the MCP reverse-proxy API registry.
-- Each row in `APIs` represents a single HTTP endpoint that the MCP server
-- exposes as a dynamically-generated MCP tool. The `document` column stores
-- a JSON document (see instruct.md for the full schema) that describes the
-- URL, method, request parameters and the shape of the response.

CREATE TABLE IF NOT EXISTS APIs (
    name     TEXT PRIMARY KEY,
    document TEXT NOT NULL
);

-- Users authenticated via AK/SK. acToken is NULL until the user completes
-- the auth flow at /auth/{userName}.
CREATE TABLE IF NOT EXISTS users (
    userName   TEXT PRIMARY KEY,
    acToken    TEXT,
    created_at datetime,
    updated_at datetime
);

-- Per-cluster tokens obtained during AK/SK authentication.
CREATE TABLE IF NOT EXISTS user_cluster (
    userName        TEXT,
    clusterId       INTEGER,
    clusterName     TEXT,
    homePath        TEXT,
    token           TEXT NOT NULL,
    JobManagerType  TEXT,
    JobManagerAddr  TEXT,
    JobManagerid    TEXT,
    JobManagertext  TEXT,
    JobManagerPort  TEXT,
    created_at      datetime,
    updated_at      datetime,
    PRIMARY KEY (userName, clusterId)
);

-- Cluster service URLs discovered via get-center-info.
CREATE TABLE IF NOT EXISTS cluster_url (
    clusterId   INTEGER PRIMARY KEY,
    clusterName TEXT,
    hpcUrls     TEXT,
    aiUrls      TEXT,
    efileUrls   TEXT,
    eshellUrls  TEXT
);
