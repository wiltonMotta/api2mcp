"""Test helpers shared across test modules."""
import os
import sqlite3

TEST_USER = "testuser"

_MODULE_DB_PATH = os.environ.get("MCP_DB_PATH", "")


def seed_test_data(db_path: str) -> None:
    """Insert standard test user/cluster rows."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = "2026-05-27 10:00:00"

    conn.execute("DELETE FROM user_cluster WHERE userName = ?", (TEST_USER,))
    conn.execute("DELETE FROM users WHERE userName = ?", (TEST_USER,))
    conn.execute("DELETE FROM cluster_url WHERE clusterId IN (?, ?)", (1, 2))

    conn.execute(
        "INSERT OR REPLACE INTO users(userName, accessKey, acToken, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (TEST_USER, TEST_USER, "ac-token-xxx", now, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO user_cluster(userName, clusterId, clusterName, homePath, token, "
        "isDefault, JobManagerid, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (TEST_USER, 1, "DefaultCluster", f"/home/{TEST_USER}",
         "test-cluster-token-abc123", True, "12345", now, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cluster_url(clusterId, clusterName, hpcUrls, efileUrls) VALUES (?, ?, ?, ?)",
        (1, "DefaultCluster", "https://hpc1.scnet.cn,https://hpc2.scnet.cn",
         "https://efile1.scnet.cn,https://efile2.scnet.cn"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO user_cluster(userName, clusterId, clusterName, homePath, token, "
        "isDefault, JobManagerid, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (TEST_USER, 2, "SecondCluster", f"/home/{TEST_USER}",
         "token-cluster-2", False, "67890", now, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cluster_url(clusterId, clusterName, hpcUrls, efileUrls) VALUES (?, ?, ?, ?)",
        (2, "SecondCluster", "https://hpc-second.scnet.cn", "https://efile-second.scnet.cn"),
    )
    conn.commit()
    conn.close()
