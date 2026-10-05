"""
main.py — HTTP API for RepoChat.

    POST /api/loadrepo   index a GitHub repository and store it in Supabase
    GET  /api/health     liveness probe

`POST /api/loadrepo` is the only endpoint the frontend needs to go from a pasted
URL to a chat session. It runs the same three steps the scripts do by hand:

    1. look up whether this repo at this commit is already indexed
       (repo_storage.list_shas — a real query against the storage bucket)
    2. if not, shallow-clone it           (fetch_github_repo.clone_repo)
    3. parse it into the two codebase JSONs and upload both
       (parse_github_repo.parse_repo + upload_to_supabase)

The response carries the resolved `repo_name`, commit `sha`, `branch` and file
count so the frontend can show what it actually got instead of a placeholder.

Everything before the clone is a fast check; the clone and parse are slow and
blocking (up to minutes on a large repo), so they run in a worker thread and
the event loop stays free to serve other requests.

Auth: callers send the Supabase access token as `Authorization: Bearer <jwt>`,
the same token the browser holds. It is verified against the project's JWKS, so
the endpoint is not open to anyone. Set `SUPABASE_JWKS_URL` in the environment
to enable that; with it unset the endpoint is anonymous (handy for curl during
development, unsafe in production).

Usage:
    uvicorn main:app --port 8000
    python main.py --port 8000 --reload

Environment (see backend/.env):
    SUPABASE_URL, SUPABASE_SECRET_KEY, SUPABASE_BUCKET  for storage
    SUPABASE_JWKS_URL                                  for token verification
    GITHUB_TOKEN                                       optional, raises API rate limits
"""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

# The pipeline modules live beside this file as flat scripts. Inserting the
# directory keeps `import fetch_github_repo` working no matter which cwd the
# server was started from.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

load_dotenv()

from fetch_github_repo import clone_repo  # noqa: E402  (needs the path above)
from parse_github_repo import (  # noqa: E402
    get_repo_info,
    parse_repo,
    upload_to_supabase,
)
from repo_storage import (  # noqa: E402
    check_size_limit,
    list_shas,
    storage_path_json,
)

log = logging.getLogger("repochat")

app = FastAPI(
    title="RepoChat API",
    version="1.0.0",
    description="Indexes GitHub repositories so the chat can answer from real source.",
)

# Cloning is disk- and network-heavy. More than a couple at once makes a
# laptop thrash without finishing any faster.
MAX_CONCURRENT_INDEXES = int(os.getenv("MAX_CONCURRENT_INDEXES", "2"))

_slots: asyncio.Semaphore | None = None
_repo_locks: dict[str, asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IndexingError(Exception):
    """Cloning or parsing failed. The message is safe to show a user."""


class RepoTooLargeError(IndexingError):
    """The generated index exceeds the storage size cap."""


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoadRepoRequest(BaseModel):
    repo_url: str = Field(
        ...,
        description="GitHub repository URL or owner/repo.",
        examples=["https://github.com/calcom/cal.diy", "calcom/cal.diy"],
    )
    branch: str = Field(
        "main",
        description="Branch to index. The latest commit on it becomes the stored sha.",
    )


class LoadRepoResponse(BaseModel):
    repo_name: str = Field(..., examples=["calcom/cal.diy"])
    url: str = Field(..., examples=["https://github.com/calcom/cal.diy"])
    sha: str = Field(..., description="Commit that was indexed.")
    branch: str = Field(..., examples=["main"])
    file_count: int = Field(..., description="Files in the index.")
    total_lines: int = Field(..., description="Sum of indexed lines.")
    description: str = Field("", description="Repository description, when known.")
    cached: bool = Field(
        ...,
        description="True when this exact commit was already indexed and nothing was rebuilt.",
    )
    storage_path: str = Field(
        ..., description="Bucket path holding the index, for later retrieval."
    )


# ---------------------------------------------------------------------------
# GitHub URL parsing
# ---------------------------------------------------------------------------

# Deliberately stricter than parse_github_repo.parseGitHubRepo on the TS side:
# this server runs `git clone` against whatever comes back, so an unvalidated
# host would turn the endpoint into an open proxy for arbitrary URLs.
_SCP_SSH = re.compile(r"^git@([^:]+):([^/]+)/(.+)$")
_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_ALLOWED_HOSTS = {"github.com", "www.github.com"}


def parse_github_repo_url(value: str) -> tuple[str, str]:
    """Return (owner, repo) for a GitHub URL or bare slug.

    Accepts what people actually paste:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        github.com/owner/repo
        git@github.com:owner/repo.git
        owner/repo

    Raises ValueError with a message meant for the user.
    """
    raw = (value or "").strip().replace("\\", "/").removesuffix(".git").rstrip("/")
    if not raw:
        raise ValueError("No repository URL given.")

    hint = "That doesn't look like a GitHub repository. Try https://github.com/owner/repo"

    ssh = _SCP_SSH.match(raw)
    if ssh:
        host, owner, repo = ssh.groups()
        if host.lower() not in _ALLOWED_HOSTS:
            raise ValueError(f"Only github.com repositories are supported (got {host}).")
    else:
        for scheme in ("https://", "http://"):
            if raw.lower().startswith(scheme):
                raw = raw[len(scheme):]
                break

        parts = [part for part in raw.split("/") if part]

        if len(parts) == 3 and parts[0].lower() in _ALLOWED_HOSTS:
            _, owner, repo = parts
        elif len(parts) == 2:
            # A bare `owner/repo`. `github.com/repo` lands here too, and means the
            # owner was forgotten rather than that github.com is the owner.
            if parts[0].lower() in _ALLOWED_HOSTS:
                raise ValueError(hint)
            owner, repo = parts
        else:
            raise ValueError(hint)

    if not _NAME.match(owner) or not _NAME.match(repo):
        raise ValueError("That repository name contains unsupported characters.")

    return owner, repo


# ---------------------------------------------------------------------------
# GitHub metadata
# ---------------------------------------------------------------------------


def _github_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RepoChat",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_repo_meta(owner: str, repo: str) -> dict:
    """Default branch and description. Returns {} when GitHub is unreachable."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        response = httpx.get(url, headers=_github_headers(), timeout=15.0)
    except Exception as e:
        log.warning("GitHub metadata lookup failed for %s/%s: %s", owner, repo, e)
        return {}

    if response.status_code != 200:
        log.warning(
            "GitHub metadata lookup for %s/%s returned %s",
            owner,
            repo,
            response.status_code,
        )
        return {}

    try:
        body = response.json()
    except Exception:
        return {}

    return {
        "default_branch": body.get("default_branch") or "",
        "description": body.get("description") or "",
        "private": bool(body.get("private")),
    }


def github_head_sha(owner: str, repo: str, branch: str) -> str:
    """Latest commit sha on `branch`, or "" if it cannot be determined."""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
    try:
        response = httpx.get(url, headers=_github_headers(), timeout=15.0)
    except Exception as e:
        log.warning("GitHub commit lookup failed for %s/%s@%s: %s", owner, repo, branch, e)
        return ""

    if response.status_code != 200:
        log.warning(
            "GitHub commit lookup for %s/%s@%s returned %s",
            owner,
            repo,
            branch,
            response.status_code,
        )
        return ""

    try:
        return str(response.json().get("sha") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def verify_bearer_token(authorization: str) -> str:
    """Verify the caller's Supabase access token and return their user id.

    Returns "" when token verification is not configured, so local development
    against curl still works.
    """
    scheme, _, token = (authorization or "").partition(" ")

    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    jwks_url = os.getenv("SUPABASE_JWKS_URL")
    if not jwks_url:
        return ""

    try:
        signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    except Exception as e:
        log.warning("Rejected session token: %s", e)
        raise HTTPException(status_code=401, detail="Invalid or expired session token.")

    return str(claims.get("sub") or "")


# ---------------------------------------------------------------------------
# Storage lookup
# ---------------------------------------------------------------------------


def stored_shas(owner: str, repo: str) -> list[str]:
    """Commit shas that already have an index in the bucket.

    Returns [] on any failure: an unreachable bucket must not look like a cache
    hit, it should just mean we rebuild.
    """
    try:
        return list_shas(owner, repo)
    except Exception as e:
        log.warning("Storage lookup failed for %s/%s: %s", owner, repo, e)
        return []


# ---------------------------------------------------------------------------
# The indexing job (blocking — runs in a worker thread)
# ---------------------------------------------------------------------------


def clone_error(owner: str, repo: str, exc: Exception) -> str:
    """Turn a raw `git clone` failure into something worth showing a user.

    The underlying message carries git's full output — a local temp path, the
    resolved URL, several lines of stderr. That is noise in a UI and leaks the
    server's filesystem layout, so only the parts that explain the failure are
    kept.
    """
    text = str(exc).lower()

    if "repository not found" in text or "could not read from remote repository" in text:
        return (
            f"{owner}/{repo} could not be found. Check the URL, and that the "
            "repository is public."
        )
    if "authentication failed" in text or "terminal prompts disabled" in text:
        return (
            f"{owner}/{repo} could not be cloned without credentials. Private "
            "repositories are not supported yet."
        )
    if "permission denied" in text or "403" in text:
        return f"GitHub refused the clone of {owner}/{repo}. It may be private."

    # Keep the first meaningful line, trimmed of git's prefixes.
    first = str(exc).strip().splitlines()
    detail = next(
        (line.strip() for line in first if line.strip() and "Cloning into" not in line),
        "",
    )
    detail = detail.removeprefix("fatal:").removeprefix("error:")
    # `clone_repo` wraps git's output; drop that prefix too so the message is
    # not "Could not clone a/b. Clone failed: fatal: early EOF".
    detail = detail.removeprefix("Clone failed:").strip()
    detail = detail.removeprefix("fatal:").strip()
    if len(detail) > 160:
        detail = detail[:157] + "..."

    return f"Could not clone {owner}/{repo}. {detail}".strip()


def index_repository(
    owner: str,
    repo: str,
    branch: str,
    description: str = "",
) -> dict:
    """Clone, parse and upload. Returns the fields of LoadRepoResponse.

    Raises RepoTooLargeError or IndexingError with a user-safe message.
    """
    clone_url = f"https://github.com/{owner}/{repo}.git"
    # Unique per job: parse_repo writes codebase_<repo>.json into output_dir, so
    # two jobs for different repos sharing a basename (ownerA/web vs ownerB/web)
    # would otherwise overwrite each other's index.
    workdir = tempfile.mkdtemp(prefix="repochat-")
    repo_dir = os.path.join(workdir, "repo")
    output_dir = os.path.join(workdir, "codebases")

    try:
        log.info("[%s/%s] cloning %s @ %s", owner, repo, clone_url, branch)
        try:
            clone_repo(clone_url, repo_dir, branch=branch or "")
        except Exception as e:
            raise IndexingError(clone_error(owner, repo, e)) from e

        repo_info = get_repo_info(repo_dir)
        sha = str(repo_info.get("last_commit_sha") or "")
        resolved_name = str(repo_info.get("repo_name") or f"{owner}/{repo}")
        resolved_branch = str(repo_info.get("default_branch") or branch)

        if not sha:
            raise IndexingError(
                f"{owner}/{repo} was cloned but no commit could be read from it."
            )

        log.info("[%s/%s] parsing at commit %s", owner, repo, sha[:12])
        try:
            full_json, light_json = parse_repo(repo_dir, output_dir, description)
        except Exception as e:
            raise IndexingError(f"Could not index {owner}/{repo}: {e}") from e

        file_count, total_lines = _summarize(light_json)

        # Gate before uploading: past this size neither file is stored, and the
        # chat has nothing to read.
        try:
            check_size_limit(full_json)
        except Exception as e:
            raise RepoTooLargeError(str(e)) from e

        log.info("[%s/%s] uploading %d files", owner, repo, file_count)
        if not upload_to_supabase(full_json, light_json, resolved_name, sha):
            raise IndexingError(
                f"Indexed {file_count} files but could not store them. "
                "The storage bucket rejected the upload."
            )

        # The index lives in Supabase now; the local copies would just go stale.
        for path in (full_json, light_json):
            try:
                os.remove(path)
            except OSError:
                pass

        return {
            "repo_name": resolved_name,
            "url": f"https://github.com/{owner}/{repo}",
            "sha": sha,
            "branch": resolved_branch,
            "file_count": file_count,
            "total_lines": total_lines,
            "description": description,
            "cached": False,
            "storage_path": storage_path_json(owner, repo, sha),
        }
    finally:
        # Always drop the clone: it is by far the largest thing here and is
        # worthless once parsed.
        shutil.rmtree(workdir, ignore_errors=True)


def _summarize(light_json: str) -> tuple[int, int]:
    """(file count, total lines) from a local lightweight index."""
    try:
        with open(light_json, "r", encoding="utf-8") as f:
            light = json.load(f)
    except Exception:
        return 0, 0

    metadata = light.get("files_metadata") or {}
    total_lines = sum(int(meta.get("size_lines") or 0) for meta in metadata.values())

    return len(metadata), total_lines


def summarize_stored(owner: str, repo: str, sha: str) -> tuple[int, int]:
    """(file count, total lines) for an index already in the bucket.

    Pulls the lightweight index only — metadata, no file contents — so this
    stays cheap. A cache hit that reported "0 files" would be worse than no
    count at all, so it is worth the one small download.
    """
    from repo_storage import storage_path_light_json, stream_download

    workdir = tempfile.mkdtemp(prefix="repochat-count-")
    local = os.path.join(workdir, "light.json")

    try:
        stream_download(storage_path_light_json(owner, repo, sha), local)
        return _summarize(local)
    except Exception as e:
        log.warning("Could not read stored index for %s/%s@%s: %s", owner, repo, sha[:12], e)
        return 0, 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _cache_hit(owner: str, repo: str, sha: str, branch: str, description: str) -> LoadRepoResponse:
    """Response for a commit whose index is already stored."""
    file_count, total_lines = summarize_stored(owner, repo, sha)

    return LoadRepoResponse(
        repo_name=f"{owner}/{repo}",
        url=f"https://github.com/{owner}/{repo}",
        sha=sha,
        branch=branch,
        file_count=file_count,
        total_lines=total_lines,
        description=description,
        cached=True,
        storage_path=storage_path_json(owner, repo, sha),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "storage_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_BUCKET")),
        "auth_configured": bool(os.getenv("SUPABASE_JWKS_URL")),
    }


@app.post("/api/loadrepo", response_model=LoadRepoResponse)
async def loadrepo(payload: LoadRepoRequest, authorization: str = Header(default="")) -> LoadRepoResponse:
    """Make a repository chat-ready: reuse its stored index, or build one.

    Blocking work (clone + parse + upload) is pushed to a worker thread.
    """
    verify_bearer_token(authorization)

    branch = (payload.branch or "main").strip() or "main"

    try:
        owner, repo = parse_github_repo_url(payload.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    meta = github_repo_meta(owner, repo)
    description = meta.get("description", "")
    branch = meta.get("default_branch") or branch

    # The storage check comes first: the whole point is to avoid re-cloning a
    # repo that is already indexed at this commit.
    known = stored_shas(owner, repo)
    head_sha = github_head_sha(owner, repo, branch)

    if head_sha and head_sha in known:
        log.info("[%s/%s] cache hit at %s", owner, repo, head_sha[:12])
        return _cache_hit(owner, repo, head_sha, branch, description)

    if meta.get("private"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"{owner}/{repo} is private. Cloning it needs GitHub credentials "
                "wired into this server, which is not set up yet."
            ),
        )

    global _slots
    if _slots is None:
        # Created here rather than at import so it binds to the running loop.
        _slots = asyncio.Semaphore(MAX_CONCURRENT_INDEXES)

    lock = _repo_locks.setdefault(f"{owner}/{repo}", asyncio.Lock())

    async with _slots:
        async with lock:
            # Re-check under the lock: another request for this repo may have
            # finished building it while we waited.
            if head_sha and head_sha in stored_shas(owner, repo):
                log.info("[%s/%s] cache hit after waiting", owner, repo)
                return _cache_hit(owner, repo, head_sha, branch, description)

            started = time.monotonic()
            log.info("[%s/%s] indexing %s @ %s", owner, repo, branch, head_sha[:12] or "?")

            try:
                result = await run_in_threadpool(
                    index_repository, owner, repo, branch, description
                )
            except RepoTooLargeError as e:
                raise HTTPException(status_code=413, detail=str(e)) from e
            except IndexingError as e:
                raise HTTPException(status_code=502, detail=str(e)) from e
            except Exception as e:
                log.exception("[%s/%s] indexing failed", owner, repo)
                raise HTTPException(status_code=500, detail=f"Indexing failed: {e}") from e

            log.info(
                "[%s/%s] done in %.1fs (%d files)",
                owner,
                repo,
                time.monotonic() - started,
                result["file_count"],
            )
            return LoadRepoResponse(**result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the RepoChat API server.")
    parser.add_argument("--host", default=os.getenv("API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="Reload on code changes.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    import uvicorn

    uvicorn.run("main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
