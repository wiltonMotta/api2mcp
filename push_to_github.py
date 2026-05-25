"""Push code to GitHub using dulwich (pure Python git)."""
import os
import sys
import dulwich.repo
import dulwich.porcelain as porc
from dulwich.config import ConfigFile

REPO_DIR = "."
REMOTE_URL = "git@github.com:wiltonMotta/api2mcp.git"

os.environ["GIT_SSH_COMMAND"] = "ssh -o StrictHostKeyChecking=no"

print("Opening repo...")
repo = dulwich.repo.Repo(REPO_DIR)

# Set up remote via config file
config_path = os.path.join(REPO_DIR, ".git", "config")
config = ConfigFile.from_path(config_path)
config.set(("remote", "origin"), "url", REMOTE_URL.encode())
with open(config_path, "wb") as f:
    config.write_to_file(f)
print(f"Set remote origin: {REMOTE_URL}")

# Stage all changes
print("\nStaging files...")
porc.add(repo, paths=["."])

# Check status
status = porc.status(repo)
print(f"Staged adds: {[s.decode() for s in status.staged.get('add', [])]}")
print(f"Staged modifies: {[s.decode() for s in status.staged.get('modify', [])]}")
print(f"Staged deletes: {[s.decode() for s in status.staged.get('delete', [])]}")

# Commit
print("\nCommitting...")
commit_sha = porc.commit(
    repo,
    message=b"feat: add JobManager fields to user_cluster table\n"
            b"\n"
            b"- Add 5 new columns: JobManagerType, JobManagerAddr, JobManagerid, JobManagertext, JobManagerPort\n"
            b"- Fetch cluster_list from /hpc/openapi/v2/cluster API during auth flow\n"
            b"- Store JobManager details into user_cluster during authentication\n",
    author=b"wiltonMotta <sjg_223@126.com>",
)
print(f"Commit: {commit_sha.decode()}")

# Push to main branch (empty repo - push to main)
print(f"\nPushing to {REMOTE_URL} (master -> main)...")
try:
    result = porc.push(
        repo,
        remote_location=REMOTE_URL,
        refspecs=[b"refs/heads/master:refs/heads/main"],
    )
    print(f"Push OK: {result.refs}")
except Exception as e:
    print(f"Push error: {e}")
    # Try pushing to master instead
    print("Retrying with master branch...")
    result = porc.push(
        repo,
        remote_location=REMOTE_URL,
        refspecs=[b"refs/heads/master:refs/heads/master"],
    )
    print(f"Push OK: {result.refs}")

print("\nDone!")
