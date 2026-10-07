"use server";

import { getAuthHeaders, getBackendUrl } from "@/lib/utils/getSession";

/**
 * Server Action: read and write the conversation history for one repository.
 *
 * Backs persistent chat: every user question and assistant answer is saved to
 * the `messages` table (via `GET/POST /api/messages`) scoped to the repos row
 * id, so the conversation survives sessions instead of vanishing on reload.
 *
 * History is tied to a repo by its `repos` table row id (`repoId`), not by
 * owner/repo slug — two different uploads of the same GitHub repo keep
 * separate conversations. The backend resolves that row against the caller's
 * own repos before touching anything, so the messages stay as private as the
 * repo they belong to.
 */

/** A persisted message, camelized for the UI. */
export type ChatHistoryMessage = {
  id: string;
  repoId: string;
  role: "user" | "assistant";
  content: string;
  /** File paths an assistant answer cited; empty for user messages. */
  sources: string[];
  createdAt: string;
};

export type FetchHistoryResult =
  | { ok: true; messages: ChatHistoryMessage[] }
  | { ok: false; error: string };

export type SaveMessageResult =
  | { ok: true; message: ChatHistoryMessage }
  | { ok: false; error: string };

const TIMEOUT_MS = 30_000;

/** Load a repository conversation, oldest first. */
export async function fetchMessages(repoId: string): Promise<FetchHistoryResult> {
  let headers: Record<string, string>;

  try {
    ({ headers } = await getAuthHeaders());
  } catch (e) {
    return {
      ok: false,
      error:
        e instanceof Error
          ? e.message
          : "Your session expired. Sign in again to continue.",
    };
  }

  let response: Response;

  try {
    response = await fetch(
      `${getBackendUrl()}/api/messages?repo_id=${encodeURIComponent(repoId)}`,
      {
        headers,
        signal: AbortSignal.timeout(TIMEOUT_MS),
        cache: "no-store",
      },
    );
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      ok: false,
      error: timedOut
        ? "Loading the conversation timed out. Try again."
        : `Could not reach the chat history service at ${getBackendUrl()}. Is it running?`,
    };
  }

  const payload = await readJson(response);

  if (!response.ok) {
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  if (!isListResponse(payload)) {
    return { ok: false, error: "The chat history service returned an unexpected response." };
  }

  return {
    ok: true,
    messages: payload.messages.map((m) => ({
      id: m.id,
      repoId: m.repo_id,
      role: m.role,
      content: m.content,
      sources: Array.isArray(m.sources) ? m.sources : [],
      createdAt: m.created_at ?? "",
    })),
  };
}

/** Append one message to a repository's conversation. */
export async function saveMessage(
  repoId: string,
  role: "user" | "assistant",
  content: string,
  sources: string[] = [],
): Promise<SaveMessageResult> {
  let headers: Record<string, string>;

  try {
    ({ headers } = await getAuthHeaders());
  } catch (e) {
    return {
      ok: false,
      error:
        e instanceof Error
          ? e.message
          : "Your session expired. Sign in again to continue.",
    };
  }

  let response: Response;

  try {
    response = await fetch(`${getBackendUrl()}/api/messages`, {
      method: "POST",
      headers,
      body: JSON.stringify({ repo_id: repoId, role, content, sources }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      ok: false,
      error: timedOut
        ? "Saving the message timed out. Try again."
        : `Could not reach the chat history service at ${getBackendUrl()}. Is it running?`,
    };
  }

  const payload = await readJson(response);

  if (!response.ok) {
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  if (!isSavedMessage(payload)) {
    return { ok: false, error: "The chat history service returned an unexpected response." };
  }

  return {
    ok: true,
    message: {
      id: payload.id,
      repoId: payload.repo_id,
      role: payload.role,
      content: payload.content,
      sources: Array.isArray(payload.sources) ? payload.sources : [],
      createdAt: payload.created_at ?? "",
    },
  };
}

/** FastAPI reports problems as `{"detail": "..."}`; a proxy can return anything. */
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

  return `The chat history service failed (HTTP ${status}).`;
}

type ServerMessage = {
  id: string;
  repo_id: string;
  role: "user" | "assistant";
  content: string;
  sources?: unknown;
  created_at?: string;
};

function isServerMessage(value: unknown): value is ServerMessage {
  if (typeof value !== "object" || value === null) return false;
  const m = value as Record<string, unknown>;
  return (
    typeof m.id === "string" &&
    typeof m.repo_id === "string" &&
    (m.role === "user" || m.role === "assistant") &&
    typeof m.content === "string"
  );
}

function isSavedMessage(value: unknown): value is ServerMessage {
  return isServerMessage(value);
}

function isListResponse(
  value: unknown,
): value is { messages: ServerMessage[]; repo_id: string } {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as { messages?: unknown; repo_id?: unknown };
  return (
    typeof candidate.repo_id === "string" &&
    Array.isArray(candidate.messages) &&
    candidate.messages.every(isServerMessage)
  );
}