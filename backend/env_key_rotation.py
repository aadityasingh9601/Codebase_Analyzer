"""Free-tier server-key rotation.

The free tier answers with the server's own OpenRouter credential(s), read from
the environment as `AI_API_KEY_1`, `AI_API_KEY_2`, `AI_API_KEY_3`, ... (a single
legacy `AI_API_KEY` is still honoured when no numbered key is set). A single key
can hit its provider rate limit, so this module hands out one key at a time and
lets the caller rotate to the next one when the current key comes back
rate-limited:

    key = current_key()          # sticky: the key to use right now
    ... provider call ...
    if rate_limited and rotate(): # move to the next configured key
        key = current_key()       # retry with this one
    else:
        ... no more keys: surface the error ...

`current_key()` is sticky across requests, so once a key exhausts every later
request starts from the next key instead of retrying the dead one. `rotate()`
wraps around, and returns False when there is no other key to try (zero or one
configured), so the caller's retry loop stays bounded.
"""

import os
import threading

from dotenv import load_dotenv

load_dotenv()

_lock = threading.Lock()


def _load_keys() -> list[str]:
    """Numbered keys in numeric order, falling back to a lone legacy key."""
    numbered: list[tuple[int, str]] = []
    for name, value in os.environ.items():
        if not name.startswith("AI_API_KEY_"):
            continue
        suffix = name[len("AI_API_KEY_") :]
        cleaned = (value or "").strip()
        if suffix.isdigit() and cleaned:
            numbered.append((int(suffix), cleaned))
    numbered.sort(key=lambda item: item[0])

    keys = [value for _, value in numbered]
    if not keys:
        legacy = (os.getenv("AI_API_KEY") or "").strip()
        if legacy:
            keys = [legacy]
    return keys


_keys: list[str] = _load_keys()
_index = 0


def available_keys() -> list[str]:
    """All configured free-tier keys, in order. Never exposes the values in logs."""
    with _lock:
        return list(_keys)


def current_key() -> str:
    """The free-tier key to try now. Empty string when none is configured."""
    with _lock:
        return _keys[_index] if _keys else ""


def rotate() -> bool:
    """Advance to the next key. False when there is no other key to try."""
    global _index
    with _lock:
        if len(_keys) < 2:
            return False
        _index = (_index + 1) % len(_keys)
        return True


def reload() -> None:
    """Re-read the environment and point back at the first key (tests, on demand)."""
    global _keys, _index
    with _lock:
        _keys = _load_keys()
        _index = 0
