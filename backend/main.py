"""
main.py — HTTP API for RepoChat.

    POST /api/loadrepo    index a GitHub repository and store it in Supabase
    GET  /api/repos       metadata for the caller's uploaded repositories
    GET  /api/userquery   answer a question about one of those repositories
    GET  /api/messages    a repository conversation's saved messages
    POST /api/messages    persist one user or assistant message
    GET  /api/keys        list the caller's stored BYOK keys (hints + labels)
    PUT  /api/keys        add a provider API key, encrypted at rest
    PATCH /api/keys/{id}  set a stored key's rotation priority
    DELETE /api/keys/{id} remove one stored key
    GET  /api/health      liveness probe

Repositories are PRIVATE to the user who uploaded them. When the web app
indexes a repo it lands in the storage bucket under `{user_id}/owner/repo/sha/`
and a metadata row is written to the `repos` table (see supabase_repos_table.sql).
Every later read goes through the same owner check: the row is looked up by
`user_id` + `repo_name`, and only that user's storage prefix is ever touched.
Two people uploading the same GitHub repo get two independent copies; nothing
is shared between users.

Limits (constants at the bottom of the header section):
    * MAX_REPOS_PER_USER = 2     — a user can upload at most two repositories.
    * MAX_FULL_JSON_MB   = 50    — parsed index cap, enforced before upload.

`POST /api/loadrepo` runs the pipeline the scripts do by hand:

    1. check the caller's own `repos` row for this repo at the latest commit
       (cache hit — reuse their stored index, nothing is rebuilt)
    2. otherwise shallow-clone and parse it into the two codebase JSONs
    3. enforce the 50 MB cap, upload both under the user's private prefix,
       and upsert the metadata row.

`POST /api/userquery` is self-contained per query. The index files are
downloaded into a temp directory, parsed, a fresh RLM is built for them, the
question is routed (cheap direct model call, or the RLM for real analysis),
and everything is closed and deleted before returning. No sessions are cached
across requests.

`GET/POST /api/messages` persist conversation history so it survives sessions.
Each message stores the repos-table row id (`repo_id`) of the repo the chat
happens in, plus `role`, `content`, `sources` and `created_at` (see
supabase_messages_table.sql). Every read and write first resolves that repo by
`user_id` + `id` — the same ownership rule as everything else — so messages
are as private as the repos they belong to.

`/api/keys*` are the BYOK ("bring your own key") endpoints. The browser POSTs
the caller's provider API key in plaintext (over HTTPS); the server derives a
per-user encryption key (PBKDF2 over `user_id` + `MASTER_SECRET`, computed fresh
and never stored), encrypts the key with AES-256-GCM, and stores ONLY the
encrypted blob plus a last-4 hint in the `user_api_keys` table (see
supabase_user_api_keys.sql). A user may keep MANY keys per provider; every
`/api/userquery` request re-derives the per-user key, picks one available key
for the requested provider with api_key_rotation.py, decrypts its blob in
memory, uses it for that one answer, and discards everything. The plaintext key
never touches disk or logs, and the derived key is never persisted.

Auth: callers send the Supabase access token as `Authorization: Bearer <jwt>`,
the same token the browser holds. It is verified against the project's JWKS, so
the endpoint is not open to anyone. Set `SUPABASE_JWKS_URL` in the environment
to enable that; with it unset the user id is empty (handy for curl during
development, unsafe in production — every anonymous caller then shares one
"user" and one quota).

Usage:
    uvicorn main:app --port 8000
    python main.py --port 8000 --reload

Environment (see backend/.env):
    SUPABASE_URL, SUPABASE_SECRET_KEY, SUPABASE_BUCKET  for storage + repos table
    SUPABASE_JWKS_URL                                  for token verification
    GITHUB_TOKEN                                       optional, raises API rate limits
    MAX_CONCURRENT_INDEXES                             concurrent clone+parse jobs
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
from typing import Literal

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Response
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
    MAX_FULL_JSON_MB,
    check_size_limit,
    delete,
    split_repo,
    storage_path_json,
    storage_path_light_json,
)
import query_router  # noqa: E402
import key_vault  # noqa: E402
import api_key_rotation  # noqa: E402
import usage_quota  # noqa: E402

log = logging.getLogger("repochat")

# A user's repositories are private to them, and the sidebar lists them; cap
# how many one user can keep so the free quota is not a bottomless bucket.
MAX_REPOS_PER_USER = 2

# Cloning is disk- and network-heavy. More than a couple at once makes a
# laptop thrash without finishing any faster.
MAX_CONCURRENT_INDEXES = int(os.getenv("MAX_CONCURRENT_INDEXES", "2"))

# Replies are bounded the same way for the same reason: each query builds an
# RLM and makes model calls, and unbounded concurrency would exhaust the
# threadpool and trip the provider's rate limit.
MAX_CONCURRENT_QUERIES = 4

_slots: asyncio.Semaphore | None = None
_query_slots: asyncio.Semaphore | None = None
_repo_locks: dict[str, asyncio.Lock] = {}
# One in-flight query per user. Serialising a user's requests stops the free
# tier's quota check and its token increment from racing each other (and stops
# a user fanning out parallel free requests to stretch the daily budget).
_user_query_locks: dict[str, asyncio.Lock] = {}


def _index_semaphore() -> asyncio.Semaphore:
    global _slots
    if _slots is None:
        # Created here rather than at import so it binds to the running loop.
        _slots = asyncio.Semaphore(MAX_CONCURRENT_INDEXES)
    return _slots


def _query_semaphore() -> asyncio.Semaphore:
    global _query_slots
    if _query_slots is None:
        _query_slots = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)
    return _query_slots


app = FastAPI(
    title="RepoChat API",
    version="2.0.0",
    description=(
        "Indexes GitHub repositories for the signed-in user who uploaded them. "
        "Repositories are private per user; a user may keep at most two."
    ),
)


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
        examples=["https://github.com/pallets/click", "pallets/click"],
    )
    branch: str = Field(
        "main",
        description="Branch to index. The latest commit on it becomes the stored sha.",
    )


class LoadRepoResponse(BaseModel):
    repo_name: str = Field(..., examples=["pallets/click"])
    url: str = Field(..., examples=["https://github.com/pallets/click"])
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
        ..., description="Bucket path holding the index, under the user's private prefix."
    )
    id: str = Field("", description="The repos table row id, used to scope chat messages.")
    repo_count: int = Field(0, description="How many repositories the user has now.")
    repo_limit: int = Field(MAX_REPOS_PER_USER, description="Per-user repo limit.")


class RepoInfo(BaseModel):
    id: str = Field("", description="The repos table row id, used to scope chat messages.")
    repo_name: str = Field(..., examples=["pallets/click"])
    url: str = Field(..., examples=["https://github.com/pallets/click"])
    branch: str = Field(..., examples=["main"])
    sha: str = Field(..., description="Commit that was indexed.")
    file_count: int = Field(0)
    total_lines: int = Field(0)
    description: str = Field("")
    created_at: str = Field("", description="ISO timestamp of first upload.")
    updated_at: str = Field("", description="ISO timestamp of last update.")


class ListReposResponse(BaseModel):
    repos: list[RepoInfo] = Field(default_factory=list)
    repo_limit: int = Field(MAX_REPOS_PER_USER, description="Per-user repo limit.")


class SavedMessage(BaseModel):
    id: str = Field("", description="The messages table row id.")
    repo_id: str = Field("", description="The repos table row id this message belongs to.")
    user_id: str = Field("", description="The user who owns the conversation.")
    role: str = Field("", description="'user' or 'assistant'.")
    content: str = Field("", description="The message text.")
    sources: list[str] = Field(
        default_factory=list,
        description="File paths an assistant answer cites, for citation chips.",
    )
    created_at: str = Field("", description="ISO timestamp of when the message was saved.")


class SaveMessageRequest(BaseModel):
    repo_id: str = Field(..., description="The repos table row id the conversation belongs to.")
    role: Literal["user", "assistant"] = Field(..., description="Who wrote the message.")
    content: str = Field(
        ...,
        min_length=1,
        max_length=100000,
        description="The message text. Whitespace-only is rejected.",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="File paths an assistant answer cites.",
    )


class ListMessagesResponse(BaseModel):
    repo_id: str = Field(...)
    messages: list[SavedMessage] = Field(default_factory=list)


class UserQueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Natural-language question about one of your repositories.",
        examples=["How does the CLI parse command line options?"],
    )
    repo_name: str | None = Field(
        None,
        description="owner/repo to query. Omit if you have exactly one connected repository.",
        examples=["pallets/click"],
    )
    provider: Literal["openrouter", "openai", "anthropic"] | None = Field(
        None,
        description=(
            "BYOK provider whose stored key should answer this request. The "
            "server decrypts the caller's key for this provider. Omit to use "
            "the server's default model credential."
        ),
    )
    model: str | None = Field(
        None,
        min_length=1,
        max_length=128,
        description=(
            "Provider model id to answer with, e.g. 'gpt-4o-mini', "
            "'claude-sonnet-4-6', 'google/gemini-2.5-flash'. Must match the "
            "selected provider; the provider client rejects unknown ids. Omit "
            "to use that provider's default model."
        ),
    )
    max_iterations: int | None = Field(
        None,
        ge=1,
        le=50,
        description="Cap for RLM iterations (effort control).",
    )


class SaveApiKeyRequest(BaseModel):
    provider: Literal["openrouter", "openai", "anthropic"] = Field(...)
    api_key: str = Field(
        ...,
        min_length=1,
        max_length=8192,
        description=(
            "The caller's provider API key, in plaintext. Sent over HTTPS, "
            "encrypted at rest on this server, and never stored or logged in "
            "plaintext."
        ),
    )
    label: str | None = Field(
        None,
        max_length=60,
        description="Optional user-chosen name for this key, e.g. 'Work'.",
    )


class ApiKeyPriorityRequest(BaseModel):
    priority: int = Field(
        ...,
        ge=0,
        le=1000,
        description="Higher priority keys are tried first during rotation.",
    )


class SaveApiKeyResponse(BaseModel):
    message: str = Field("saved", description="Always 'saved' on success.")
    id: str = Field(..., description="The new user_api_keys row id.")
    provider: str = Field(..., description="Provider id, e.g. 'openrouter'.")
    key_hint: str = Field(..., description="The stored last-4 hint for this key.")
    label: str | None = Field(None, description="Optional name the caller gave this key.")


class ApiKeyInfo(BaseModel):
    id: str = Field(..., description="The user_api_keys row id (used to delete/prioritise).")
    provider: str = Field(..., description="Provider id, e.g. 'openrouter'.")
    key_hint: str = Field(..., description="The stored last-N hint for this key.")
    label: str | None = Field(None, description="Optional name the caller gave this key.")
    is_exhausted: bool = Field(
        False, description="True when the key last hit a provider rate-limit."
    )
    priority: int = Field(0, description="Higher priority keys are tried first.")


class ListApiKeysResponse(BaseModel):
    keys: list[ApiKeyInfo] = Field(default_factory=list)


class UsageInfo(BaseModel):
    """A free-tier user's token budget for the current day."""

    used: int = Field(..., description="Tokens spent today across free queries.")
    limit: int = Field(..., description="Daily free-tier token limit.")
    remaining: int = Field(..., description="Tokens left today (never negative).")
    date: str = Field(..., description="The day these numbers are for (ISO date).")


class UserQueryResponse(BaseModel):
    answer: str = Field(..., description="Answer text, or '' when it could not be produced.")
    tier: str = Field(..., description="Which routing tier answered: DIRECT, META, or RLM.")
    reason: str = Field("", description="Why the router chose that tier.")
    answer_source: str = Field("", description="Where the answer text came from.")
    evidence_paths: list[str] = Field(
        default_factory=list,
        description="Repository paths the answer drew on, for citation chips.",
    )
    repo_name: str = Field(..., description="Repository that was actually queried.")
    sha: str = Field(..., description="Commit that was actually queried.")
    elapsed_seconds: float = Field(..., description="Server-side time for the whole query.")
    direct_calls: int = 0
    direct_input_tokens: int = 0
    direct_output_tokens: int = 0
    rlm_input_tokens: int = 0
    rlm_output_tokens: int = 0
    rlm_iterations: int = 0
    error: str | None = Field(None, description="Present when the query failed.")
    usage: UsageInfo | None = Field(
        None,
        description=(
            "Free-tier token budget after this query. Present only when the "
            "query ran on the free tier (no provider supplied)."
        ),
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
    against curl still works (all anonymous callers then share one empty
    user id and quota).
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
# `repos` table (metadata only — the indexes live in storage)
# ---------------------------------------------------------------------------

REPOS_TABLE = "repos"
MESSAGES_TABLE = "messages"
API_KEYS_TABLE = "user_api_keys"

# The same whitelist as the `user_api_keys.provider` CHECK constraint.
ALLOWED_PROVIDERS = ("openrouter", "openai", "anthropic")

# Provider key prefixes for save-time validation. Cheap garbage gate only —
# length rules live in the frontend; the provider's own endpoint (see
# _verify_provider_key) is the authoritative validator.
PROVIDER_KEY_PREFIXES: dict[str, tuple[str, ...]] = {
    "openrouter": ("sk-or-v1-",),
    "openai": ("sk-", "sk-proj-"),
    "anthropic": ("sk-ant-",),
}

PROVIDER_LABELS: dict[str, str] = {
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
}


def _db():
    """Supabase client for Postgres (repos table) — same creds as storage."""
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase env vars missing")
    return create_client(url, key)


# ---------------------------------------------------------------------------
# `user_api_keys` table (BYOK blobs — the plaintext key arrives over HTTPS,
# is validated and encrypted here, and only the blob + hint is persisted)
# ---------------------------------------------------------------------------

def _require_provider(provider: str) -> str:
    """Validate a provider id, raising 422 with a clear message otherwise."""
    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported API provider: {provider!r}. Allowed: {', '.join(ALLOWED_PROVIDERS)}.",
        )
    return provider


def _check_api_key_format(provider: str, api_key: str) -> str | None:
    """Prefix sanity check (step 1 of the BYOK save gate)."""
    prefixes = PROVIDER_KEY_PREFIXES[provider]
    label = PROVIDER_LABELS[provider]
    if not any(api_key.startswith(p) for p in prefixes):
        return (
            f"That doesn't look like a {label} key — {label} keys start with "
            f"{', '.join(repr(p) for p in prefixes)}."
        )
    return None


def _verify_provider_key(provider: str, api_key: str, timeout: float = 8.0) -> str | None:
    """Ask the provider itself whether this key authenticates (step 2 of the
    BYOK save gate).

    Each provider exposes a free, read-only "who am I" endpoint that answers
    401/403 in milliseconds for a bad or over-scoped key. Returns None on
    success, otherwise a user-facing error string. Nothing is saved when this
    returns an error.
    """
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/key"
        headers = {"Authorization": f"Bearer {api_key}"}
    elif provider == "openai":
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        return f"Unsupported provider {provider!r}."

    label = PROVIDER_LABELS.get(provider, provider)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)
        status = response.status_code
    except httpx.HTTPError as e:
        return (
            f"Could not reach {label} to verify this key ({type(e).__name__}). "
            "Try again — nothing was saved."
        )

    if status in (401, 403):
        kind = "an authentication" if status == 401 else "a permission"
        return (
            f"{label} rejected this key (HTTP {status}): that's {kind} error — "
            "the key is invalid, expired, or lacks model access."
        )
    if status != 200:
        return f"{label} didn't accept this key (HTTP {status}). Nothing was saved."
    return None


def _list_user_api_keys(user_id: str) -> list[dict]:
    data = (
        _db()
        .table(API_KEYS_TABLE)
        .select("id, provider, key_hint, label, is_exhausted, priority")
        .eq("user_id", user_id)
        .order("provider")
        .order("priority", desc=True)
        .order("created_at")
        .execute()
    )
    return list(data.data or [])


def _insert_api_key(
    user_id: str, provider: str, encrypted_blob: str, key_hint: str, label: str | None
) -> str:
    """Add a new key row (never replaces). Returns the new row id.

    A user may keep many keys per provider, so this is a plain insert; there is
    no uniqueness on (user_id, provider).
    """
    data = (
        _db()
        .table(API_KEYS_TABLE)
        .insert(
            {
                "user_id": user_id,
                "provider": provider,
                "encrypted_blob": encrypted_blob,
                "key_hint": key_hint,
                "label": label,
            }
        )
        .execute()
    )
    rows = list(data.data or [])
    if not rows:
        raise RuntimeError("Could not read back the saved API key row.")
    return str(rows[0]["id"])


def _delete_api_key(user_id: str, key_id: str) -> None:
    _db().table(API_KEYS_TABLE).delete().eq("user_id", user_id).eq("id", key_id).execute()


def _set_key_priority(user_id: str, key_id: str, priority: int) -> None:
    (
        _db()
        .table(API_KEYS_TABLE)
        .update({"priority": priority})
        .eq("user_id", user_id)
        .eq("id", key_id)
        .execute()
    )


# Words providers use when a key is being rate-limited / out of quota. We are
# deliberately strict: an internal "budget exhausted" message must NOT mark a
# user's key as exhausted.
_RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "rate-limit",
    "rate_limit",
    "too many requests",
    "quota",
    "insufficient_quota",
)


def _suggests_rate_limit(message: str) -> bool:
    lowered = (message or "").lower()
    return any(token in lowered for token in _RATE_LIMIT_MARKERS)


def _select_provider_key(user_id: str, provider: str) -> dict:
    """Best available stored key row for `provider`, or raise a clear error.

    422 when the user has never saved a key for the provider; 400 when keys
    exist but every one is currently rate-limited/exhausted. Used both for the
    initial pick and to re-route after a key is marked exhausted mid-query.
    """
    db = _db()
    row = api_key_rotation.get_available_api_key(user_id, provider, db)
    if row is not None:
        return row
    if api_key_rotation.has_saved_key(user_id, provider, db):
        raise HTTPException(
            status_code=400,
            detail=(
                "All your API keys for this provider are currently exhausted. "
                "Add a new key on /keys, or wait a bit for their rate limits to reset."
            ),
        )
    raise HTTPException(
        status_code=422,
        detail=f"No API key saved for provider {provider!r}. Add one on /keys first.",
    )


def _decrypt_key_row(row: dict, user_id: str, provider: str) -> str:
    """Decrypt one stored key row's blob in memory for this request only."""
    try:
        return key_vault.decrypt_api_key(row["encrypted_blob"], user_id)
    except Exception as e:
        log.warning("Could not decrypt stored key %s for %s/%s: %s", row.get("id"), user_id, provider, e)
        raise HTTPException(
            status_code=400,
            detail=(
                f"A stored {provider} key could not be decrypted. It was "
                "probably saved before the server-side encryption change — "
                "remove it on /keys and add it again."
            ),
        ) from e


def _usage_info(used: int) -> UsageInfo:
    """Wrap a used-token count in the response shape the UI expects."""
    limit = usage_quota.FREE_TOKENS_PER_DAY
    return UsageInfo(
        used=used,
        limit=limit,
        remaining=max(0, limit - used),
        date=usage_quota.today_iso(),
    )


def _list_user_repos(user_id: str) -> list[dict]:
    """All metadata rows for one user, oldest first."""
    data = _db().table(REPOS_TABLE).select("*").eq("user_id", user_id).execute()
    return list(data.data or [])


def _get_user_repo(user_id: str, repo_name: str) -> dict | None:
    """One user's metadata row for `repo_name`, or None.

    Looking the row up by (user_id, repo_name) is the ownership check: a repo
    that is not in this caller's rows is not this caller's repo.
    """
    data = (
        _db()
        .table(REPOS_TABLE)
        .select("*")
        .eq("user_id", user_id)
        .eq("repo_name", repo_name)
        .limit(1)
        .execute()
    )
    rows = list(data.data or [])
    return rows[0] if rows else None


def _get_user_repo_by_id(user_id: str, repo_id: str) -> dict | None:
    """The caller's repo metadata row by its `repos` id, or None.

    Messages are scoped to a repo row id. Resolving that row by (user_id, id)
    is the ownership check for the conversation: a message can only ever be
    read or written inside a repo that is actually this caller's.
    """
    data = (
        _db()
        .table(REPOS_TABLE)
        .select("*")
        .eq("user_id", user_id)
        .eq("id", repo_id)
        .limit(1)
        .execute()
    )
    rows = list(data.data or [])
    return rows[0] if rows else None


def _count_user_repos(user_id: str) -> int:
    # Rows are unique per (user_id, repo_name), so the row count is the repo count.
    return len(_list_user_repos(user_id))


def _upsert_repo(user_id: str, fields: dict) -> None:
    """Insert or update one user's repo row (keyed by user_id + repo_name)."""
    payload = {
        "user_id": user_id,
        "repo_name": fields["repo_name"],
        "url": fields["url"],
        "branch": fields["branch"],
        "sha": fields["sha"],
        "file_count": int(fields.get("file_count") or 0),
        "total_lines": int(fields.get("total_lines") or 0),
        "description": fields.get("description") or "",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _db().table(REPOS_TABLE).upsert(payload, on_conflict="user_id,repo_name").execute()


def _repo_info(row: dict) -> RepoInfo:
    return RepoInfo(
        id=row.get("id") or "",
        repo_name=row["repo_name"],
        url=row["url"],
        branch=row["branch"],
        sha=row["sha"],
        file_count=int(row.get("file_count") or 0),
        total_lines=int(row.get("total_lines") or 0),
        description=row.get("description") or "",
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def _message_info(row: dict) -> SavedMessage:
    return SavedMessage(
        id=row.get("id") or "",
        repo_id=row.get("repo_id") or "",
        user_id=row.get("user_id") or "",
        role=row.get("role") or "",
        content=row.get("content") or "",
        sources=list(row.get("sources") or []),
        created_at=str(row.get("created_at") or ""),
    )


# ---------------------------------------------------------------------------
# `messages` table (persistent chat history — see supabase_messages_table.sql)
# ---------------------------------------------------------------------------


def _list_messages(user_id: str, repo_id: str) -> list[dict]:
    """One conversation's rows, oldest first (stable order by id too)."""
    data = (
        _db()
        .table(MESSAGES_TABLE)
        .select("*")
        .eq("user_id", user_id)
        .eq("repo_id", repo_id)
        .order("created_at")
        .order("id")
        .execute()
    )
    return list(data.data or [])


def _insert_message(user_id: str, repo_id: str, role: str, content: str, sources: list[str]) -> dict:
    """Append one message to a conversation and return the stored row."""
    created = (
        _db()
        .table(MESSAGES_TABLE)
        .insert(
            {
                "user_id": user_id,
                "repo_id": repo_id,
                "role": role,
                "content": content,
                "sources": list(sources),
            }
        )
        .execute()
    )
    rows = list(created.data or [])
    if not rows:
        raise RuntimeError("message insert returned no row")
    return rows[0]


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
    user_id: str = "",
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
        if not upload_to_supabase(full_json, light_json, resolved_name, sha, user_id=user_id):
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
            "storage_path": storage_path_json(owner, repo, sha, user_id),
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


def summarize_stored(user_id: str, owner: str, repo: str, sha: str) -> tuple[int, int]:
    """(file count, total lines) for an index already in the bucket.

    Pulls the lightweight index only — metadata, no file contents — so this
    stays cheap. A cache hit that reported "0 files" would be worse than no
    count at all, so it is worth the one small download.
    """
    from repo_storage import stream_download

    workdir = tempfile.mkdtemp(prefix="repochat-count-")
    local = os.path.join(workdir, "light.json")

    try:
        stream_download(storage_path_light_json(owner, repo, sha, user_id), local)
        return _summarize(local)
    except Exception as e:
        log.warning("Could not read stored index for %s/%s@%s: %s", owner, repo, sha[:12], e)
        return 0, 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _cache_hit(
    user_id: str,
    owner: str,
    repo: str,
    sha: str,
    branch: str,
    description: str,
    repo_id: str = "",
) -> LoadRepoResponse:
    """Response for a commit whose index is already stored for this user."""
    file_count, total_lines = summarize_stored(user_id, owner, repo, sha)

    return LoadRepoResponse(
        repo_name=f"{owner}/{repo}",
        url=f"https://github.com/{owner}/{repo}",
        sha=sha,
        branch=branch,
        file_count=file_count,
        total_lines=total_lines,
        description=description,
        cached=True,
        storage_path=storage_path_json(owner, repo, sha, user_id),
        id=repo_id,
        repo_count=_count_user_repos(user_id),
        repo_limit=MAX_REPOS_PER_USER,
    )


def _delete_stored_index(user_id: str, owner: str, repo: str, sha: str) -> None:
    """Remove one commit's two index files from the bucket (best effort)."""
    for remote in (
        storage_path_json(owner, repo, sha, user_id),
        storage_path_light_json(owner, repo, sha, user_id),
    ):
        try:
            delete(remote)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "storage_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_BUCKET")),
        "auth_configured": bool(os.getenv("SUPABASE_JWKS_URL")),
        "max_repos_per_user": MAX_REPOS_PER_USER,
        "max_index_mb": MAX_FULL_JSON_MB,
    }


@app.post("/api/loadrepo", response_model=LoadRepoResponse)
async def loadrepo(payload: LoadRepoRequest, authorization: str = Header(default="")) -> LoadRepoResponse:
    """Make a repository the caller can chat with: reuse their stored index,
    or build one. Repositories are private per user.

    Blocking work (clone + parse + upload) is pushed to a worker thread.
    """
    user_id = verify_bearer_token(authorization)

    branch = (payload.branch or "main").strip() or "main"

    try:
        owner, repo = parse_github_repo_url(payload.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    repo_name = f"{owner}/{repo}"
    meta = github_repo_meta(owner, repo)
    description = meta.get("description", "")
    branch = meta.get("default_branch") or branch

    # Private-per-user: the caller's own row decides both the limit and the
    # cache. Someone else upload the same GitHub repo? Irrelevant here.
    existing = _get_user_repo(user_id, repo_name)

    if existing is None and _count_user_repos(user_id) >= MAX_REPOS_PER_USER:
        raise HTTPException(
            status_code=403,
            detail=(
                f"You can connect at most {MAX_REPOS_PER_USER} repositories. "
                "Remove one from the sidebar before connecting another."
            ),
        )

    head_sha = github_head_sha(owner, repo, branch)

    if head_sha and existing and existing["sha"] == head_sha:
        log.info("[%s] %s cache hit at %s", user_id or "anonymous", repo_name, head_sha[:12])
        return _cache_hit(
            user_id, owner, repo, head_sha, branch, description, repo_id=existing.get("id", "")
        )

    if meta.get("private"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"{owner}/{repo} is private. Cloning it needs GitHub credentials "
                "wired into this server, which is not set up yet."
            ),
        )

    lock = _repo_locks.setdefault(repo_name, asyncio.Lock())

    async with _index_semaphore():
        async with lock:
            # Re-check under the lock: another request for this repo + user may
            # have finished building it while we waited.
            current = _get_user_repo(user_id, repo_name)
            head_now = head_sha or github_head_sha(owner, repo, branch)
            if head_now and current and current["sha"] == head_now:
                log.info("[%s] %s cache hit after waiting", user_id or "anonymous", repo_name)
                return _cache_hit(
                    user_id, owner, repo, head_now, branch, description, repo_id=current.get("id", "")
                )

            started = time.monotonic()
            log.info("[%s] indexing %s @ %s", user_id or "anonymous", repo_name, head_now[:12] or "?")

            try:
                result = await run_in_threadpool(
                    index_repository, owner, repo, branch, description, user_id
                )
            except RepoTooLargeError as e:
                raise HTTPException(status_code=413, detail=str(e)) from e
            except IndexingError as e:
                raise HTTPException(status_code=502, detail=str(e)) from e
            except Exception as e:
                log.exception("[%s/%s] indexing failed", owner, repo)
                raise HTTPException(status_code=500, detail=f"Indexing failed: {e}") from e

            # Key the sidebar row by whatever the user pasted, so the row and
            # the storage prefix always agree.
            result["repo_name"] = repo_name

            _upsert_repo(
                user_id,
                {
                    "repo_name": repo_name,
                    "url": result["url"],
                    "branch": result["branch"],
                    "sha": result["sha"],
                    "file_count": result["file_count"],
                    "total_lines": result["total_lines"],
                    "description": result.get("description", ""),
                },
            )
            log.info(
                "[%s] %s done in %.1fs (%d files)",
                user_id or "anonymous",
                repo_name,
                time.monotonic() - started,
                result["file_count"],
            )

            # The sidebar's chat is scoped by the repos row id; fetch the row we
            # just upserted so the response carries it.
            upserted = _get_user_repo(user_id, repo_name) or {}

            return LoadRepoResponse(
                **result,
                id=upserted.get("id", ""),
                repo_count=_count_user_repos(user_id),
                repo_limit=MAX_REPOS_PER_USER,
            )


@app.get("/api/repos", response_model=ListReposResponse)
def list_repos(authorization: str = Header(default="")) -> ListReposResponse:
    """The caller's uploaded repositories — metadata only, streaming the index
    happens per query. This is what the sidebar renders."""
    user_id = verify_bearer_token(authorization)

    try:
        rows = _list_user_repos(user_id)
    except Exception as e:
        log.exception("[repos] listing failed for %s", user_id or "anonymous")
        raise HTTPException(status_code=502, detail="Could not load your repositories right now.") from e

    return ListReposResponse(
        repos=[_repo_info(row) for row in rows],
        repo_limit=MAX_REPOS_PER_USER,
    )


# ---------------------------------------------------------------------------
# Persistent chat history
# ---------------------------------------------------------------------------


@app.get("/api/messages", response_model=ListMessagesResponse)
def list_messages(
    repo_id: str | None = None,
    authorization: str = Header(default=""),
) -> ListMessagesResponse:
    """The saved conversation for one of the caller's repositories, oldest first.

    `repo_id` is the `repos` table row id. It is resolved against the caller's
    own rows first — a conversation can only be read inside a repo this user
    actually owns, mirroring the per-user privacy of the repos themselves.
    """
    user_id = verify_bearer_token(authorization)

    repo_id = (repo_id or "").strip()
    if not repo_id:
        raise HTTPException(status_code=422, detail="repo_id is required")

    if not _get_user_repo_by_id(user_id, repo_id):
        raise HTTPException(status_code=404, detail="That repository isn't yours.")

    try:
        rows = _list_messages(user_id, repo_id)
    except Exception as e:
        log.exception("[messages] listing failed for %s", user_id or "anonymous")
        raise HTTPException(status_code=502, detail="Could not load the conversation right now.") from e

    return ListMessagesResponse(
        repo_id=repo_id,
        messages=[_message_info(row) for row in rows],
    )


@app.post("/api/messages", response_model=SavedMessage)
def save_message(
    payload: SaveMessageRequest,
    authorization: str = Header(default=""),
) -> SavedMessage:
    """Append one user or assistant message to a repository conversation.

    The repo row id is ownership-checked the same way as the GET above, so a
    message can only be saved inside a repo this user actually owns.
    """
    user_id = verify_bearer_token(authorization)

    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="message content must not be empty")

    if not _get_user_repo_by_id(user_id, payload.repo_id):
        raise HTTPException(status_code=404, detail="That repository isn't yours.")

    try:
        row = _insert_message(user_id, payload.repo_id, payload.role, content, payload.sources)
    except Exception as e:
        log.exception("[messages] save failed for %s", user_id or "anonymous")
        raise HTTPException(status_code=502, detail="Could not save the message. Try again.") from e

    return _message_info(row)


# ---------------------------------------------------------------------------
# Question answering
# ---------------------------------------------------------------------------


def _user_repo_target(user_id: str, requested: str) -> dict:
    """The caller's repo metadata row to answer against.

    Resolves from the caller's OWN rows only — the (user_id, repo_name) lookup
    is the authorization check, so one user can never address another's repo.
    """
    requested = (requested or "").strip().strip("/")
    rows = _list_user_repos(user_id)
    if not rows:
        raise HTTPException(
            status_code=422,
            detail="No repository connected. Connect one from /get-started first.",
        )
    if requested:
        for row in rows:
            if row["repo_name"] == requested:
                return row
        raise HTTPException(
            status_code=404,
            detail=f"You haven't connected {requested}. Connect it from /get-started first.",
        )
    if len(rows) > 1:
        names = ", ".join(sorted(r["repo_name"] for r in rows))
        raise HTTPException(
            status_code=422,
            detail=f"Several repositories are connected ({names}). Specify which one to query.",
        )
    return rows[0]


def _query_error_detail(exc: Exception) -> str:
    """A message safe to return to the browser.

    Storage and filesystem exceptions carry local temp paths and signed URLs;
    neither belongs in a response, so only validation errors echo their text.
    The full detail is logged separately (the callers log.exception).
    """
    if isinstance(exc, FileNotFoundError):
        return "That repository's index is no longer available. Reconnect it from /get-started."
    if isinstance(exc, ValueError):
        return str(exc)
    return "An unexpected server error occurred. Try again in a moment."


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _cap_error_text(error: str) -> str:
    """Trim a model/provider error for display: first line, no URLs, capped."""
    text = (error or "").strip()
    if not text:
        return text
    text = text.splitlines()[0].strip()
    text = _URL_RE.sub("…", text)
    if len(text) > 200:
        text = text[:197] + "..."
    return text


def _download_index(user_id: str, owner: str, repo: str, sha: str, full_path: str, light_path: str) -> None:
    """Stream this user's two index files into `full_path` / `light_path`."""
    from repo_storage import stream_download

    stream_download(storage_path_json(owner, repo, sha, user_id), full_path)
    stream_download(storage_path_light_json(owner, repo, sha, user_id), light_path)
    for path in (full_path, light_path):
        if os.path.getsize(path) == 0:
            raise RuntimeError(f"Empty index object for {repo}@{sha[:7]}")


# ---------------------------------------------------------------------------
# BYOK API keys
# ---------------------------------------------------------------------------

@app.get("/api/keys", response_model=ListApiKeysResponse)
def list_keys(authorization: str = Header(default="")) -> ListApiKeysResponse:
    """Non-secret metadata (id, hint, label, status) for the caller's saved keys."""
    user_id = verify_bearer_token(authorization)
    rows = _list_user_api_keys(user_id or "")
    return ListApiKeysResponse(
        keys=[
            ApiKeyInfo(
                id=str(r["id"]),
                provider=r["provider"],
                key_hint=r["key_hint"],
                label=r.get("label"),
                is_exhausted=bool(r.get("is_exhausted")),
                priority=int(r.get("priority") or 0),
            )
            for r in rows
        ]
    )


@app.put("/api/keys", response_model=SaveApiKeyResponse)
def save_key(payload: SaveApiKeyRequest, authorization: str = Header(default="")) -> SaveApiKeyResponse:
    """Add a provider's API key, encrypted at rest.

    The plaintext key travels from the browser over HTTPS; the server validates
    it (format, then a minimal authenticated call to the provider), derives a
    per-user key (PBKDF2 over `user_id + MASTER_SECRET`, never stored), encrypts
    the key with AES-256-GCM, and stores only the blob + a last-4 hint in
    `user_api_keys`. A user may keep many keys per provider, so this always adds
    a new row. The plaintext never reaches disk or logs, and nothing is saved
    unless the provider accepts the key.
    """
    user_id = verify_bearer_token(authorization) or ""
    _require_provider(payload.provider)

    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="API key must not be empty")

    label = (payload.label or "").strip() or None

    # Save gate: format first (fast), then let the provider authenticate it.
    # A validation failure (format, 401/403, unreachable) aborts the store.
    format_error = _check_api_key_format(payload.provider, api_key)
    if format_error:
        raise HTTPException(status_code=422, detail=format_error)

    verify_error = _verify_provider_key(payload.provider, api_key)
    if verify_error:
        raise HTTPException(status_code=422, detail=verify_error)

    try:
        encrypted_blob = key_vault.encrypt_api_key(api_key, user_id)
        hint = key_vault.key_hint(api_key)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    key_id = _insert_api_key(user_id, payload.provider, encrypted_blob, hint, label)
    return SaveApiKeyResponse(
        message="saved",
        id=key_id,
        provider=payload.provider,
        key_hint=hint,
        label=label,
    )


@app.patch("/api/keys/{key_id}", status_code=204)
def set_key_priority_route(
    key_id: str,
    payload: ApiKeyPriorityRequest,
    authorization: str = Header(default=""),
) -> Response:
    """Set one stored key's rotation priority (higher is tried first)."""
    user_id = verify_bearer_token(authorization) or ""
    _set_key_priority(user_id, key_id, payload.priority)
    return Response(status_code=204)


@app.delete("/api/keys/{key_id}", status_code=204)
def delete_key(key_id: str, authorization: str = Header(default="")) -> Response:
    """Remove one stored key by id. The user_id filter makes it owner-scoped."""
    user_id = verify_bearer_token(authorization) or ""
    _delete_api_key(user_id, key_id)
    return Response(status_code=204)


@app.get("/api/usage", response_model=UsageInfo)
def get_usage(authorization: str = Header(default="")) -> UsageInfo:
    """The caller's free-tier token budget for today (for the chat UI meter).

    Requires a real signed-in user: the free tier is tied to an account so it
    cannot be reset by clearing storage, and there is no anonymous quota.
    """
    user_id = verify_bearer_token(authorization)
    if not user_id:
        raise HTTPException(status_code=401, detail="Sign in to see your free-tier usage.")
    return _usage_info(usage_quota.get_used_tokens(user_id, _db()))


@app.post("/api/userquery", response_model=UserQueryResponse)
async def userquery(payload: UserQueryRequest, authorization: str = Header(default="")) -> UserQueryResponse:
    """Answer a question about one of the caller's own repositories.

    Self-contained per query: download the user's index into a temp dir, parse
    it, build a fresh RLM, route, and tear down before returning. Nothing is
    cached across requests, so there is nothing to share and no session state.

    Every stage that can block — downloading, parsing up to 50MB of JSON,
    building the REPL, and the query itself — runs in a worker thread.

    Failures split by where they happen:
      - before/around routing (unknown repo, missing index) -> 4xx/5xx status;
      - inside the answer itself (model error, exhausted budget) -> 200 with
        `error` populated, so the caller still gets the tier and timing.
      - a BYOK key that gets rate-limited is parked and the next available key
        is tried automatically; when none remain -> 400 "all keys exhausted".
    """
    user_id = verify_bearer_token(authorization)
    # print(payload)  # avoid noisy logs in prod-like runs

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be empty")

    # Determine mode: if client explicitly requests a BYOK provider, this is
    # "Your Key" mode and must NOT fall back to free tier. Otherwise it is
    # "Free" mode and must use the free tier (OpenRouter) with quota tracking.
    mode_byok = payload.provider is not None

    api_key = ""
    used_key_row = None
    free_tier = not mode_byok

    if mode_byok:
        provider = (payload.provider or "").strip().lower()
        _require_provider(provider)
        used_key_row = _select_provider_key(user_id or "", provider)
        api_key = _decrypt_key_row(used_key_row, user_id or "", provider)
        model = (payload.model or "").strip()
    else:
        # Free tier: always use OpenRouter with the server's credential.
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="Sign in to use the free tier.",
            )
        provider = usage_quota.FREE_TIER_PROVIDER
        api_key = ""
        model = (payload.model or "").strip()
        # Rough pre-check to avoid starting requests that are almost certainly

    row = _user_repo_target(user_id, payload.repo_name or "")
    repo_name = row["repo_name"]
    sha = row["sha"]
    log.info("[query] %s asking %s@%s", user_id or "anonymous", repo_name, sha[:12])

    owner, repo = split_repo(repo_name)
    session_id = f"{user_id}:{repo_name}@{sha[:16]}"

    workdir = tempfile.mkdtemp(prefix="repochat-query-")
    full_path = os.path.join(workdir, "codebase.json")
    light_path = os.path.join(workdir, "codebase_lightweight.json")
    started = time.monotonic()

    result = None
    # Per-user lock: the free tier's pre-check and post-query increment must not
    # race with another in-flight request for the same user. BYOK doesn't spend
    # the free budget, but re-using the same lock keeps the rule general.
    if not user_id:
        # Development fallback without auth: share one global lock to avoid
        # creating unbounded keys; quota is not tracked for anonymous.
        uq_lock = _repo_locks.setdefault(f"anon_query:{repo_name}", asyncio.Lock())
    else:
        uq_lock = _user_query_locks.setdefault(user_id, asyncio.Lock())

    try:
        async with uq_lock:
            async with _query_semaphore():
                try:
                    await run_in_threadpool(
                        _download_index, user_id, owner, repo, sha, full_path, light_path
                    )
                except Exception as e:
                    log.exception("[query] download failed for %s@%s", repo_name, sha[:12])
                    raise HTTPException(
                        status_code=502,
                        detail="Could not retrieve your repository index. Reconnect it from /get-started.",
                    ) from e

            # Answer, re-routing across the caller's keys if the current one is
            # rate-limited. Each failed key is parked (marked exhausted) before
            # re-selecting, so the pool shrinks by one per pass and the loop is
            # bounded by the number of keys; when none remain, re-selection
            # raises the clear "all keys exhausted" 400.
            while True:
                try:
                    # Only forward max_iterations when the caller selected an
                    # effort explicitly; older callers (and tests) omit it and
                    # rely on answer_for_session's own default.
                    call_args = (
                        session_id,
                        full_path,
                        light_path,
                        payload.question,
                        provider,
                        api_key,
                        model,
                    )
                    if payload.max_iterations is not None:
                        call_args += (payload.max_iterations,)
                    result = await run_in_threadpool(
                        query_router.answer_for_session, *call_args
                    )
                except FileNotFoundError as e:
                    raise HTTPException(status_code=404, detail=_query_error_detail(e)) from e
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=str(e)) from e
                except Exception as e:
                    log.exception("[query] %s@%s failed", repo_name, sha[:12])
                    raise HTTPException(status_code=502, detail=_query_error_detail(e)) from e

                if used_key_row is None or not _suggests_rate_limit(result.error or ""):
                    break

                log.info(
                    "[query] key %s rate-limited; rotating to the next key",
                    used_key_row["id"],
                )
                api_key_rotation.mark_key_exhausted(used_key_row["id"], _db())
                used_key_row = _select_provider_key(user_id or "", provider)
                api_key = _decrypt_key_row(used_key_row, user_id or "", provider)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    error = _cap_error_text(result.error) or None

    # The key that produced the final answer gets its LRU stamp. A key that was
    # rate-limited never reaches here — it was marked exhausted in the loop.
    if used_key_row is not None and not error:
        api_key_rotation.update_key_last_used(used_key_row["id"], _db())

    # Add actual tokens to the free-tier budget only if the request ran on the
    # free tier (no BYOK key was used). BYOK consumption is the user's own cost.
    usage_info = None
    if free_tier and user_id:
        tokens = (
            (result.direct_input_tokens or 0)
            + (result.direct_output_tokens or 0)
            + (result.rlm_input_tokens or 0)
            + (result.rlm_output_tokens or 0)
        )
        # If there was an error, we may still have spent provider tokens in a
        # failed attempt? The router records what it measured; don't double-count
        # when nothing was spent, and don't subtract for errors. Record what the
        # request actually consumed.
        if error is None:
            # Some gauge paths can run 0 actual tokens before escalating — still
            # record what we have. But we only add when positive.
            pass
        usage_quota.add_tokens_used(user_id, tokens, _db())
        usage_info = _usage_info(usage_quota.get_used_tokens(user_id, _db()))

    return UserQueryResponse(
        answer=result.answer or "",
        tier=result.tier,
        reason=result.reason,
        answer_source=result.answer_source,
        evidence_paths=result.evidence_paths,
        repo_name=repo_name,
        sha=sha,
        elapsed_seconds=result.elapsed_seconds or (time.monotonic() - started),
        direct_calls=result.direct_calls,
        direct_input_tokens=result.direct_input_tokens,
        direct_output_tokens=result.direct_output_tokens,
        rlm_input_tokens=result.rlm_input_tokens,
        rlm_output_tokens=result.rlm_output_tokens,
        rlm_iterations=result.rlm_iterations,
        error=error,
        usage=usage_info,
    )


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