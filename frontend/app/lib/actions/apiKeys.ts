"use server";

import { getAuthHeaders, getBackendUrl } from "@/lib/utils/getSession";
import { PROVIDERS, type ProviderId } from "@/lib/api-key-store";

/**
 * Server Action: manage the caller's BYOK API keys.
 *
 * A user may keep MANY keys per provider. Each key is sent in plaintext from
 * the browser (over HTTPS); the backend derives a per-user encryption key from
 * `user_id + MASTER_SECRET`, stores the key as an AES-256-GCM blob plus a
 * last-4 hint, and never persists or logs the plaintext. At query time the
 * browser sends only the provider id; the backend picks one available key
 * (rotating around exhausted ones) and decrypts it server-side for that one
 * request.
 */

/** A stored key row (matches `GET /api/keys`). */
export type SavedApiKey = {
  id: string;
  provider: ProviderId;
  keyHint: string;
  /** Optional user-chosen name; null when the user left it blank. */
  label: string | null;
  /** True when this key last hit a provider rate-limit (rotation skips it). */
  isExhausted: boolean;
  /** Higher wins during rotation. */
  priority: number;
};

export type ListApiKeysResult =
  | { ok: true; keys: SavedApiKey[] }
  | { ok: false; error: string };

export type SaveApiKeyResult =
  | { ok: true; key: SavedApiKey }
  | { ok: false; error: string };

export type DeleteApiKeyResult = { ok: true } | { ok: false; error: string };

export type UpdateApiKeyResult = { ok: true } | { ok: false; error: string };

async function authHeaders(): Promise<Record<string, string>> {
  const { headers } = await getAuthHeaders();
  return headers;
}

function authError(e: unknown): string {
  return e instanceof Error
    ? e.message
    : "Your session expired. Sign in again to continue.";
}

export async function listSavedApiKeys(): Promise<ListApiKeysResult> {
  let headers: Record<string, string>;

  try {
    headers = await authHeaders();
  } catch (e) {
    return { ok: false, error: authError(e) };
  }

  let response: Response;

  try {
    response = await fetch(`${getBackendUrl()}/api/keys`, {
      headers,
      signal: AbortSignal.timeout(30_000),
      cache: "no-store",
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      ok: false,
      error: timedOut
        ? "Loading your API keys timed out. Try again."
        : `Could not reach the key service at ${getBackendUrl()}. Is it running?`,
    };
  }

  const payload = await readJson(response);

  if (!response.ok) {
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  if (!isKeysResponse(payload)) {
    return { ok: false, error: "The key service returned an unexpected response." };
  }

  return {
    ok: true,
    keys: payload.keys
      .filter((row) => PROVIDERS.includes(row.provider as ProviderId))
      .map((row) => ({
        id: row.id,
        provider: row.provider as ProviderId,
        keyHint: row.key_hint,
        label: row.label,
        isExhausted: row.is_exhausted,
        priority: row.priority,
      })),
  };
}

/**
 * Add a key for `provider` (never replaces). The backend encrypts it at rest;
 * the plaintext travels only over HTTPS and is never persisted. `label` is an
 * optional user-chosen name.
 */
export async function saveApiKey(
  provider: ProviderId,
  apiKey: string,
  label?: string,
): Promise<SaveApiKeyResult> {
  let headers: Record<string, string>;

  try {
    headers = await authHeaders();
  } catch (e) {
    return { ok: false, error: authError(e) };
  }

  const trimmedLabel = (label ?? "").trim();

  let response: Response;

  try {
    response = await fetch(`${getBackendUrl()}/api/keys`, {
      method: "PUT",
      headers,
      body: JSON.stringify({
        provider,
        api_key: apiKey,
        ...(trimmedLabel ? { label: trimmedLabel } : {}),
      }),
      signal: AbortSignal.timeout(30_000),
      cache: "no-store",
    });
  } catch (e) {
    return {
      ok: false,
      error:
        e instanceof Error
          ? `Could not reach the key service at ${getBackendUrl()}. Is it running?`
          : "Could not save your API key.",
    };
  }

  const payload = await readJson(response);

  if (!response.ok) {
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  if (!isHintedKey(payload)) {
    return { ok: false, error: "The key service returned an unexpected response." };
  }

  return {
    ok: true,
    key: {
      id: payload.id,
      provider,
      keyHint: payload.key_hint,
      label: payload.label ?? null,
      isExhausted: false,
      priority: 0,
    },
  };
}

/** Delete one stored key by its row id. */
export async function deleteApiKey(keyId: string): Promise<DeleteApiKeyResult> {
  let headers: Record<string, string>;

  try {
    headers = await authHeaders();
  } catch (e) {
    return { ok: false, error: authError(e) };
  }

  let response: Response;

  try {
    response = await fetch(`${getBackendUrl()}/api/keys/${encodeURIComponent(keyId)}`, {
      method: "DELETE",
      headers,
      signal: AbortSignal.timeout(30_000),
      cache: "no-store",
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      ok: false,
      error: timedOut
        ? "Deleting the key timed out. Try again."
        : `Could not reach the key service at ${getBackendUrl()}. Is it running?`,
    };
  }

  if (!response.ok) {
    const payload = await readJson(response);
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  return { ok: true };
}

/** Set a stored key's rotation priority (higher is tried first). */
export async function setApiKeyPriority(
  keyId: string,
  priority: number,
): Promise<UpdateApiKeyResult> {
  let headers: Record<string, string>;

  try {
    headers = await authHeaders();
  } catch (e) {
    return { ok: false, error: authError(e) };
  }

  let response: Response;

  try {
    response = await fetch(`${getBackendUrl()}/api/keys/${encodeURIComponent(keyId)}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify({ priority }),
      signal: AbortSignal.timeout(30_000),
      cache: "no-store",
    });
  } catch (e) {
    return {
      ok: false,
      error: e instanceof Error ? e.message : "Could not update the key priority.",
    };
  }

  if (!response.ok) {
    const payload = await readJson(response);
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  return { ok: true };
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();

  if (!text) return null;

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorFrom(payload: unknown, status: number): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const { detail } = payload as { detail: unknown };

    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first?.msg === "string") return first.msg;
    }
  }

  if (typeof payload === "string" && payload.trim() && !payload.startsWith("<")) {
    return payload.slice(0, 300);
  }

  return `The key service failed (HTTP ${status}).`;
}

function isKeysResponse(
  value: unknown,
): value is {
  keys: Array<{
    id: string;
    provider: string;
    key_hint: string;
    label: string | null;
    is_exhausted: boolean;
    priority: number;
  }>;
} {
  if (typeof value !== "object" || value === null) return false;

  const candidate = value as { keys?: unknown };

  if (!Array.isArray(candidate.keys)) return false;

  return candidate.keys.every((row) => {
    if (typeof row !== "object" || row === null) return false;
    const r = row as Record<string, unknown>;
    return (
      typeof r.id === "string" &&
      typeof r.provider === "string" &&
      typeof r.key_hint === "string" &&
      (r.label === null || typeof r.label === "string") &&
      typeof r.is_exhausted === "boolean" &&
      typeof r.priority === "number"
    );
  });
}

function isHintedKey(
  value: unknown,
): value is { id: string; key_hint: string; label?: string | null } {
  if (typeof value !== "object" || value === null) return false;
  const r = value as Record<string, unknown>;
  return typeof r.id === "string" && typeof r.key_hint === "string";
}