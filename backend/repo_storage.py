"""
repo_storage.py — single source of truth for Supabase storage of repo contexts.

Centralizes the storage config (bucket, size limit, remote path layout) and the
upload / streaming download / delete operations so the repo-context scripts
stop duplicating Supabase client logic.

Remote layout (bucket: env SUPABASE_BUCKET, default "repo-contexts"):

    {owner}/{repo}/{sha}/codebase.json                    (server-less/CLI use)
    {user_id}/{owner}/{repo}/{sha}/codebase.json          (server use)

A repository uploaded by the web app is PRIVATE to the user who uploaded it:
the user's id is the first path segment, and every access goes through the
backend, which verifies the caller is that user before touching storage. Two
people uploading the same repo keep two completely separate copies; nothing is
shared between users. The CLI scripts pass no user_id and keep using the
unprefixed layout.

Usage:
    from repo_storage import (
        BUCKET, split_repo, upload, stream_download, delete, check_size_limit,
        storage_path_json, storage_path_light_json, storage_path_dill,
    )

    owner, repo = split_repo("calcom/cal.diy")
    upload("codebase_cal.diy.json", storage_path_json(owner, repo, sha, user_id))
    stream_download(storage_path_dill(owner, repo, sha, "user123"), "user123.dill")
    delete(storage_path_json(owner, repo, sha, user_id))
"""

import os

from dotenv import load_dotenv

load_dotenv()

BUCKET = os.getenv("SUPABASE_BUCKET", "repo-contexts")
MAX_FULL_JSON_MB = 50
DOWNLOAD_CHUNK_BYTES = 8 * 1024
SIGNED_URL_TTL_SECONDS = 300


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


def _norm_user(user_id: str) -> str:
    """Sanitize a user id for use as a storage path segment. Empty means
    anonymous/CLI use, in which case there is no per-user prefix."""
    value = (user_id or "").strip().strip("/")
    if not value:
        return ""
    if ".." in value or "/" in value or value in (".", ".."):
        raise ValueError(f"Invalid user_id: {user_id!r}")
    return value


def storage_path_prefix(owner: str, repo: str, sha: str, user_id: str = "") -> str:
    """Remote folder holding every artifact for one repo at one commit.

    When `user_id` is given the folder is nested under it, scoping the index
    to that one user. Without it (CLI scripts, anonymous dev) the folder is
    unprefixed.
    """
    prefix = "/".join((_norm_part(owner, "owner"), _norm_part(repo, "repo"), _norm_sha(sha)))
    user = _norm_user(user_id)
    return f"{user}/{prefix}" if user else prefix


def storage_path_json(owner: str, repo: str, sha: str, user_id: str = "") -> str:
    return f"{storage_path_prefix(owner, repo, sha, user_id)}/codebase.json"


def storage_path_light_json(owner: str, repo: str, sha: str, user_id: str = "") -> str:
    return f"{storage_path_prefix(owner, repo, sha, user_id)}/codebase_lightweight.json"


def storage_path_dill(owner: str, repo: str, sha: str, user_id: str) -> str:
    # The dill is already scoped per user via the users/<id>.dill segment, so it
    # intentionally does NOT take the user_id path prefix that codebase indexes
    # do: the CLI scripts own this layout.
    return f"{storage_path_prefix(owner, repo, sha)}/users/{_dill_name(user_id)}"


def list_shas(owner: str, repo: str, bucket: str = "", user_id: str = "") -> list:
    """List the commit SHAs that have a stored context for this repo.

    With a `user_id` this only looks under that user's private prefix.
    """
    user = _norm_user(user_id)
    prefix = "/".join((_norm_part(owner, "owner"), _norm_part(repo, "repo")))
    if user:
        prefix = f"{user}/{prefix}"
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


def _signed_url(remote_path: str, bucket: str, expires_in: int = SIGNED_URL_TTL_SECONDS) -> str:
    """Mint a short-lived signed URL for a remote object."""
    response = _client(bucket).create_signed_url(remote_path, expires_in)
    # supabase-py has shipped both spellings of this key across versions.
    return response.get("signedURL") or response["signedUrl"]


def stream_download(remote_path: str, local_path: str, bucket: str = "") -> str:
    """Download a remote object to `local_path` without buffering it in RAM.

    The object is fetched from a short-lived signed URL and written to disk in
    DOWNLOAD_CHUNK_BYTES pieces, so peak memory stays a small constant instead
    of scaling with the file. That matters here because a 50 MB codebase index
    would otherwise be fully materialized in memory by the storage SDK.

    The bytes land on a temporary path and are moved into place only after the
    transfer completes, so an interrupted download can never leave a truncated
    JSON behind that a later step might mistake for a valid context.

    Requires SUPABASE_SECRET_KEY (or another service-role key) to sign URLs; the
    publishable/anon key cannot sign. Missing objects raise StorageApiError, as
    before, so callers can keep treating that as "not stored".
    """
    import httpx

    url = _signed_url(remote_path, bucket)

    parent = os.path.dirname(local_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{local_path}.part"

    # Generous read timeout: a large index on a slow link can stall for a while.
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=300.0, pool=30.0)
    written = 0

    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
            response.raise_for_status()
            expected = int(response.headers.get("content-length") or 0)
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    f.write(chunk)
                    written += len(chunk)
                f.flush()
                os.fsync(f.fileno())

        if expected and written != expected:
            raise RuntimeError(
                f"Incomplete download for {remote_path}: "
                f"got {written} of {expected} bytes"
            )

        os.replace(tmp_path, local_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return local_path


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
