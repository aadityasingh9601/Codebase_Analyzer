"""Server-side BYOK key protection.

The browser uploads a provider API key in plaintext (over HTTPS). This module
derives a per-user encryption key and protects the key at rest:

    derived_key = PBKDF2(MASTER_SECRET, salt=(user_id + MASTER_SECRET),
                         iterations=100000)

The derived key is computed fresh on every save and every decrypt request and
is never stored anywhere — same deterministic inputs produce the same key, so
Phase 1 (save) and Phase 2 (per-query decryption) always agree. Only the
AES-256-GCM ciphertext (+ a last-4 hint) is stored in `user_api_keys`.

`MASTER_SECRET` comes from the environment. It is the one secret that protects
every stored key: anyone who obtains it can decrypt all of them, so it must be
treated like a private key. Nothing works — save or decrypt — without it.
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Per the BYOK spec: 100000 iterations of PBKDF2-HMAC-SHA256, 32-byte key.
PBKDF2_ITERATIONS = 100000
KEY_SIZE = 32
NONCE_SIZE = 12


def master_secret() -> str:
    secret = os.getenv("MASTER_SECRET") or ""
    if not secret:
        raise RuntimeError(
            "MASTER_SECRET is not set. Saving or using BYOK keys is disabled "
            "until it is configured (see backend/.env.example)."
        )
    return secret


def derive_key(user_id: str) -> bytes:
    """Deterministic per-user key: PBKDF2(MASTER_SECRET, salt=user_id+secret).

    The `:` delimiter keeps `user_id` and `secret` unambiguous, and hashing the
    combined string fixes the salt length. Same inputs, same key, every call.
    """
    secret = master_secret()
    salt = hashlib.sha256(f"{user_id}:{secret}".encode("utf-8")).digest()
    return hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=KEY_SIZE,
    )


def encrypt_api_key(api_key: str, user_id: str) -> str:
    """AES-256-GCM encrypt `api_key` for `user_id`; base64(nonce || ct || tag)."""
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(derive_key(user_id)).encrypt(nonce, api_key.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_api_key(blob: str, user_id: str) -> str:
    """Inverse of encrypt_api_key; raises when the key/wrapper is wrong."""
    raw = base64.b64decode((blob or "").encode("ascii"))
    nonce, ciphertext = raw[:NONCE_SIZE], raw[NONCE_SIZE:]
    plaintext = AESGCM(derive_key(user_id)).decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")


def key_hint(api_key: str) -> str:
    """Non-secret identifier for the list UI, e.g. '...sk3f'."""
    return f"...{api_key[-4:]}"