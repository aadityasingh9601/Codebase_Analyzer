"""
repo_storage.py — single source of truth for Supabase storage of repo contexts.

Centralizes the storage config (bucket, size limit, remote path layout) and the
upload / download / delete operations so the repo-context scripts stop
duplicating Supabase client logic.

Remote layout (bucket: env SUPABASE_BUCKET, default "repo-contexts"):

    {owner}/{repo}/{sha}/codebase.json
    {owner}/{repo}/{sha}/codebase_lightweight.json
    {owner}/{repo}/{sha}/users/{user_id}.dill

The commit SHA is part of the path, so two users on the same repo but at
different commits never read each other's context. Everything under a given SHA
belongs to that one commit of that one repo and can be shared; only the .dill
is per-user.

Usage:
    from repo_storage import (
        BUCKET, split_repo, upload, download, delete, check_size_limit,
        storage_path_json, storage_path_light_json, storage_path_dill,
    )

    owner, repo = split_repo("calcom/cal.diy")
    upload("codebase_cal.diy.json", storage_path_json(owner, repo, sha))
    download(storage_path_dill(owner, repo, sha, "user123"), "user123.dill")
    delete(storage_path_json(owner, repo, sha))
"""

import os

from dotenv import load_dotenv

load_dotenv()

BUCKET = os.getenv("SUPABASE_BUCKET", "repo-contexts")
MAX_FULL_JSON_MB = 50


def _client(bucket: str):
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase env vars missing")
    return create_client(url, key).storage.from_(bucket or BUCKET)


def split_repo(repo_name: str) -> tuple:
    """Split 'owner/repo' into (owner, repo)."""
    repo = (repo_name or "").strip().strip("/")
    if not repo or ".." in repo or "/" not in repo:
        raise ValueError(f"Invalid repo name: {repo_name!r} (expected 'owner/repo')")
    owner, name = repo.rsplit("/", 1)
    return owner, name


def _norm_part(part: str, label: str) -> str:
    value = (part or "").strip().strip("/")
    if not value or ".." in value or "/" in value:
        raise ValueError(f"Invalid {label}: {part!r}")
    return value


def _norm_sha(sha: str) -> str:
    value = (sha or "").strip()
    if not value:
        raise ValueError("commit sha is required for the storage path")
    if ".." in value or "/" in value or value in (".", ".."):
        raise ValueError(f"Invalid commit sha: {sha!r}")
    return value


def _dill_name(user_id: str) -> str:
    name = (user_id or "").strip().strip("/")
    if not name or "/" in name or ".." in name:
        raise ValueError(f"Invalid user_id: {user_id!r}")
    if not name.endswith(".dill"):
        name = f"{name}.dill"
    return name


def storage_path_prefix(owner: str, repo: str, sha: str) -> str:
    """Remote folder holding every artifact for one repo at one commit."""
    return "/".join((_norm_part(owner, "owner"), _norm_part(repo, "repo"), _norm_sha(sha)))


def storage_path_json(owner: str, repo: str, sha: str) -> str:
    return f"{storage_path_prefix(owner, repo, sha)}/codebase.json"


def storage_path_light_json(owner: str, repo: str, sha: str) -> str:
    return f"{storage_path_prefix(owner, repo, sha)}/codebase_lightweight.json"


def storage_path_dill(owner: str, repo: str, sha: str, user_id: str) -> str:
    return f"{storage_path_prefix(owner, repo, sha)}/users/{_dill_name(user_id)}"


def list_shas(owner: str, repo: str, bucket: str = "") -> list:
    """List the commit SHAs that have a stored context for this repo."""
    prefix = "/".join((_norm_part(owner, "owner"), _norm_part(repo, "repo")))
    seen = []
    for obj in _client(bucket).list(prefix + "/", options={"limit": 1000}):
        name = (obj.get("name") or "").strip("/")
        # Supabase returns names relative to the listed prefix; tolerate both.
        if name.startswith(prefix):
            name = name[len(prefix):].strip("/")
        if not name:
            continue
        sha = name.split("/")[0]
        if sha and sha not in seen:
            seen.append(sha)
    return seen


def resolve_sha(explicit: str = "", *json_paths: str) -> str:
    """Resolve which commit sha to use: the explicit one, else the
    last_commit_sha recorded in the first of `json_paths` that has it."""
    if explicit and explicit.strip():
        return explicit.strip()

    for path in json_paths:
        if not path or not os.path.exists(path):
            continue
        try:
            import json

            with open(path, "r") as f:
                sha = json.load(f).get("last_commit_sha", "")
        except Exception:
            continue
        if sha:
            return str(sha).strip()

    raise RuntimeError(
        "No commit sha available. Pass one explicitly, or use a codebase JSON "
        "that records last_commit_sha."
    )


def upload(
    local_path: str,
    remote_path: str,
    content_type: str = "application/octet-stream",
    bucket: str = "",
) -> None:
    with open(local_path, "rb") as f:
        data = f.read()
    _client(bucket).upload(
        remote_path,
        data,
        {"upsert": "true", "content-type": content_type},
    )


def download(remote_path: str, local_path: str, bucket: str = "") -> None:
    data = _client(bucket).download(remote_path)
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(data)


def stream_download(remote_path: str, local_path: str, bucket: str = "") -> None:
    raise NotImplementedError("stream_download not implemented yet")


def delete(remote_path: str, bucket: str = "") -> bool:
    try:
        _client(bucket).remove([remote_path])
        return True
    except Exception as e:
        print(f"Supabase delete failed ({remote_path}): {e}")
        return False


def check_size_limit(local_path: str, max_mb: int = MAX_FULL_JSON_MB) -> None:
    size_mb = os.path.getsize(local_path) / (1024 * 1024)
    if size_mb > max_mb:
        raise RuntimeError(f"Repo too large: {size_mb:.1f}MB. Max {max_mb}MB.")
