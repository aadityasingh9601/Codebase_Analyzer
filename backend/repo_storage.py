"""
repo_storage.py — single source of truth for Supabase storage of repo contexts.

Centralizes the storage config (bucket, size limit, remote path layout) and the
upload / download / delete operations so the repo-context scripts stop
duplicating Supabase client logic.

Remote layout (bucket: env SUPABASE_BUCKET, default "repo-contexts"):
    {owner}/{repo}/codebase.json
    {owner}/{repo}/codebase_lightweight.json
    {owner}/{repo}/{user_id}.dill

Usage:
    from repo_storage import (
        BUCKET, upload, download, delete, check_size_limit,
        storage_path_json, storage_path_light_json, storage_path_dill,
    )
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


def _norm(repo_name: str) -> str:
    repo = (repo_name or "").strip().strip("/")
    if not repo or ".." in repo:
        raise ValueError(f"Invalid repo name: {repo_name!r}")
    return repo


def storage_path_json(repo_name: str) -> str:
    return f"{_norm(repo_name)}/codebase.json"


def storage_path_light_json(repo_name: str) -> str:
    return f"{_norm(repo_name)}/codebase_lightweight.json"


def storage_path_dill(repo_name: str, user_id: str) -> str:
    name = (user_id or "").strip().strip("/")
    if not name:
        raise ValueError("user_id is required for the .dill path")
    if not name.endswith(".dill"):
        name = f"{name}.dill"
    return f"{_norm(repo_name)}/{name}"


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
