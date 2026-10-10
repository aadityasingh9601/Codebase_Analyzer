"""API key rotation for BYOK provider keys.

A user can store several keys per provider. Every `/api/userquery` picks the
"best available" key for that provider instead of a fixed one:

    1. keys that are active and not currently exhausted, highest `priority`
       first, least recently used (`last_used_at`) first;
    2. if all are exhausted, keys whose exhaustion happened more than an hour
       ago are auto-reset and retried (provider rate limits usually reset
       hourly);
    3. otherwise None — the route turns that into an "all keys exhausted"
       response.

`mark_key_exhausted` is called when a provider call returns a rate-limit error,
`update_key_last_used` after a successful use, so the LRU rotation keeps
spreading work across keys.
"""

from datetime import datetime, timedelta, timezone

EXHAUSTION_COOLDOWN = timedelta(hours=1)


def get_available_api_key(user_id: str, provider: str, supabase) -> dict | None:
    """Best available key for this user+provider, one DB round trip per path.

    Returns the full row (id, encrypted_blob, hint, label, ...). The caller
    decrypts the returned row's blob and uses it for the request.
    """
    now = datetime.now(timezone.utc)
    one_hour_ago = now - EXHAUSTION_COOLDOWN

    # First try: active, not exhausted, highest priority first, LRU first.
    result = (
        supabase.table("user_api_keys")
        .select("*")
        .eq("user_id", user_id)
        .eq("provider", provider)
        .eq("is_active", True)
        .eq("is_exhausted", False)
        .order("priority", desc=True)
        .order("last_used_at", desc=False)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]

    # Second try: exhausted keys old enough that rate limits may have reset.
    result = (
        supabase.table("user_api_keys")
        .select("*")
        .eq("user_id", user_id)
        .eq("provider", provider)
        .eq("is_active", True)
        .eq("is_exhausted", True)
        .lt("exhausted_at", one_hour_ago.isoformat())
        .order("exhausted_at", desc=False)
        .limit(1)
        .execute()
    )
    if result.data:
        row = result.data[0]
        # Give it another chance; if the provider still refuses, the rate-limit
        # path marks it exhausted again and it falls below the newer keys.
        (
            supabase.table("user_api_keys")
            .update({"is_exhausted": False, "exhausted_at": None})
            .eq("id", row["id"])
            .execute()
        )
        return row

    return None


def has_saved_key(user_id: str, provider: str, supabase) -> bool:
    """Whether the user has ANY stored key for this provider (exhausted or not).

    Lets the route say "you never added a key — add one on /keys" instead of
    the scarier "all keys exhausted" when there is nothing to wait on.
    """
    result = (
        supabase.table("user_api_keys")
        .select("id")
        .eq("user_id", user_id)
        .eq("provider", provider)
        .limit(1)
        .execute()
    )
    return bool(result.data)


def mark_key_exhausted(key_id: str, supabase) -> None:
    """Call this when a provider call fails with a rate-limit / quota error."""
    (
        supabase.table("user_api_keys")
        .update(
            {
                "is_exhausted": True,
                "exhausted_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", key_id)
        .execute()
    )


def update_key_last_used(key_id: str, supabase) -> None:
    """Call this after every successful use, so rotation is LRU within priority."""
    (
        supabase.table("user_api_keys")
        .update({"last_used_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", key_id)
        .execute()
    )