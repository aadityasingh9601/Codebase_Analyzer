/**
 * Browser-side encryption for BYOK ("bring your own key").
 *
 * The user's AI provider API key never leaves the browser in plaintext for
 * storage: it is encrypted with AES-GCM under a key derived from a password
 * the user chooses. Only the resulting blob (base64 of salt || iv || cipher)
 * is sent to the backend and persisted. The server has no way to decrypt it;
 * the plaintext key only lives transiently in this browser tab's memory.
 */

const PBKDF2_ITERATIONS = 310000;
const SALT_BYTES = 16;
const IV_BYTES = 12;

/**
 * Derive a 256-bit AES-GCM key from the user's password.
 */
async function deriveKey(password: string, salt: Uint8Array): Promise<CryptoKey> {
  const encoder = new TextEncoder();
  const material = await crypto.subtle.importKey(
    "raw",
    encoder.encode(password),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt: salt as BufferSource,
      iterations: PBKDF2_ITERATIONS,
    },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

/**
 * Encrypt an API key (or any short secret) into a portable base64 blob.
 *
 * Output format — base64(salt(16) || iv(12) || ciphertext). Decrypting
 * requires the exact password used here; there is no recovery path, so
 * losing the password means re-entering a fresh key.
 */
export async function encryptApiKey(
  keyValue: string,
  password: string,
): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const aesKey = await deriveKey(password, salt);

  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: iv as BufferSource },
    aesKey,
    new TextEncoder().encode(keyValue),
  );

  const blob = new Uint8Array(salt.length + iv.length + ciphertext.byteLength);
  blob.set(salt, 0);
  blob.set(iv, salt.length);
  blob.set(new Uint8Array(ciphertext), salt.length + iv.length);
  return bytesToBase64(blob);
}

/**
 * Decrypt a blob produced by {@link encryptApiKey}.
 *
 * Throws when the password is wrong or the data is corrupt (the AES-GCM auth
 * tag fails), so callers can surface a friendly "wrong password" message.
 */
export async function decryptApiKey(
  blob: string,
  password: string,
): Promise<string> {
  const raw = base64ToBytes(blob);
  if (raw.length < SALT_BYTES + IV_BYTES) {
    throw new Error("Decryption failed — corrupted data.");
  }

  const salt = raw.slice(0, SALT_BYTES);
  const iv = raw.slice(SALT_BYTES, SALT_BYTES + IV_BYTES);
  const ciphertext = raw.slice(SALT_BYTES + IV_BYTES);

  const aesKey = await deriveKey(password, salt);
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: iv as BufferSource },
    aesKey,
    ciphertext as BufferSource,
  );
  return new TextDecoder().decode(plaintext);
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function base64ToBytes(encoded: string): Uint8Array {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

/** Format a hint from a raw API key: last 4 characters, e.g. "...k1x2". */
export function apiKeyHint(keyValue: string): string {
  const trimmed = keyValue.trim();
  return `...${trimmed.slice(-4)}`;
}