"""Daily free-tier token quota.

Users who have not brought their own key share the server's OpenRouter
credential, so their usage is metered per day rather than left unbounded.

Tokens (not request counts) are the unit: a request's total input+output
tokens, summed across the cheap direct/gauge calls and the RLM, are added to a
per-user, per-day row after the answer is produced. A rough pre-check runs
before the query, because the exact cost is unknowable until the model runs.

The addition is done by the `increment_tokens` SQL function (see
supabase_user_usage.sql) as a single INSERT ... ON CONFLICT DO UPDATE, so
parallel requests can never clobber each other's totals and the quota cannot be
bypassed by firing many requests at once. That function is granted to the
service role only, so a signed-in user cannot call it (or the table) to tamper
with their own counter. The route also holds a per-user lock, which serialises
a single user's requests within a process.

The limit is read from `FREE_TOKENS_PER_DAY` so it can be raised without a code
change. Resets are free: a new day is simply a new `(user_id, date)` row.
"""

import logging
import os
from datetime import date

log = logging.getLogger(__name__)

# How many tokens a free-tier user may spend per calendar day. Conservative on
# purpose — a first launch can afford to be stingy and then relax the cap.
FREE_TOKENS_PER_DAY = int(os.getenv("FREE_TOKENS_PER_DAY", "100000"))

# The one provider the free tier is allowed to use. Fixed, so a free request
# can never be pointed at a different (more expensive) backend.
FREE_TIER_PROVIDER = "openrouter"

# Rough cost assumed when deciding whether a request may start. The exact token
# count is only known after the model answers; this keeps a request that is
# already close to the ceiling from starting and then overshooting badly.
FREE_ESTIMATE_TOKENS = 2000

USER_USAGE_TABLE = "user_usage"

QUOTA_EXHAUSTED_MESSAGE = (
    "You've used all of today's free tokens. Add your own API key on /keys to "
    "keep going, or wait until tomorrow for the limit to reset."
)


def today_iso() -> str:
    return date.today().isoformat()


def get_used_tokens(user_id: str, supabase) -> int:
    """Tokens this user has spent today (0 when they have no row yet)."""
    try:
        if hasattr(supabase, "table") and callable(supabase.table):
            result = (
                supabase.table(USER_USAGE_TABLE)
                .select("tokens_used")
                .eq("user_id", user_id)
                .eq("date", today_iso())
                .execute()
            )
            rows = list(getattr(result, "data", []) or [])
            if not rows:
                return 0
            return int(rows[0].get("tokens_used") or 0)
    except Exception as e:
        log.warning("Could not read free-tier usage for %s: %s", user_id, e)
        return 0
    return 0


def check_token_quota(user_id: str, tokens_needed_estimate: int, supabase) -> bool:
    """Whether this user can start a request costing ~`tokens_needed_estimate`.

    A rough gate, not a reservation: the request may still use fewer or more
    tokens than the estimate, and the real total is recorded afterwards.
    """
    try:
        used = get_used_tokens(user_id, supabase)
        return (used + tokens_needed_estimate) <= FREE_TOKENS_PER_DAY
    except Exception:
        return True


def add_tokens_used(user_id: str, tokens: int, supabase) -> None:
    """Add the actual token count to today's row, atomically.

    Calls the `increment_tokens` SQL function (a single INSERT ... ON CONFLICT
    DO UPDATE), so parallel requests cannot clobber each other's totals — the
    quota cannot be bypassed by firing many requests simultaneously. That
    function is executable by the service role only (see supabase_user_usage.sql),
    so a signed-in user cannot call it to tamper with their own counter. A
    non-positive count is a no-op.
    """
    if tokens <= 0:
        return
    try:
        if hasattr(supabase, "rpc") and callable(supabase.rpc):
            supabase.rpc(
                "increment_tokens",
                {"p_user_id": user_id, "p_date": today_iso(), "p_tokens": int(tokens)},
            ).execute()
        else:
            log.warning("No rpc() on Supabase client; usage not recorded for %s", user_id)
    except Exception as e:
        # Never break the answer, but never fail silently either: a missing,
        # mis-granted or renamed function is exactly how "0 tokens used" hides
        # an unenforced quota.
        log.warning("Could not record %s free-tier tokens for %s: %s", tokens, user_id, e)
