"use server";

import { getAuthHeaders, getBackendUrl } from "@/lib/utils/getSession";

/**
 * Server Action: make a repository chat-ready.
 *
 * Called from /get-started when the user submits a GitHub URL. Runs on the
 * server, so the Supabase service-role credentials the API needs never touch the
 * browser, and the caller's session token travels server-to-server.
 *
 * The API does the real work — check storage, then clone, parse and upload — and
 * can take minutes on a large repository, hence the generous timeout.
 */

/** Matches the `POST /api/loadrepo` response body. */
export type LoadedRepo = {
  repo_name: string;
  url: string;
  sha: string;
  branch: string;
  file_count: number;
  total_lines: number;
  description: string;
  cached: boolean;
  storage_path: string;
};

/**
 * Serialisable result. Server Actions must return plain JSON-serialisable data,
 * so failures come back as `{ ok: false, error }` rather than a thrown error —
 * throwing across the boundary produces an opaque digest instead of a message
 * the UI can show.
 */
export type LoadRepoResult =
  | { ok: true; repo: LoadedRepo }
  | { ok: false; error: string };

/**
 * A 50 MB index on a cold cache over a slow link is slow, not broken. This is
 * generous on purpose: failing early would just make the user retry work that
 * was about to succeed.
 */
const TIMEOUT_MS = 10 * 60 * 1000;

export async function loadGithubRepo(
  repoUrl: string,
  branch = "main",
): Promise<LoadRepoResult> {
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
    response = await fetch(`${getBackendUrl()}/api/loadrepo`, {
      method: "POST",
      headers,
      body: JSON.stringify({ repo_url: repoUrl, branch }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      // Indexing can take minutes. Next's server-action fetch must not time out
      // on its own default before our own timeout is reached.
      cache: "no-store",
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      ok: false,
      error: timedOut
        ? "Indexing took longer than 10 minutes and was abandoned. Try a smaller repository."
        : `Could not reach the indexing service at ${getBackendUrl()}. Is it running?`,
    };
  }

  const payload = await readJson(response);

  if (!response.ok) {
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  if (!isLoadedRepo(payload)) {
    return { ok: false, error: "The indexing service returned an unexpected response." };
  }

  return { ok: true, repo: payload };
}

/**
 * FastAPI reports problems as `{"detail": "..."}`, but a proxy or crash can put
 * HTML or an empty body here instead, so never assume the shape.
 */
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
    // FastAPI's validation errors arrive as a list of objects.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first?.msg === "string") return first.msg;
    }
  }

  if (typeof payload === "string" && payload.trim() && !payload.startsWith("<")) {
    return payload.slice(0, 300);
  }

  return `The indexing service failed (HTTP ${status}).`;
}

function isLoadedRepo(value: unknown): value is LoadedRepo {
  if (typeof value !== "object" || value === null) return false;

  const candidate = value as Partial<LoadedRepo>;

  return (
    typeof candidate.repo_name === "string" &&
    typeof candidate.url === "string" &&
    typeof candidate.sha === "string" &&
    typeof candidate.branch === "string" &&
    typeof candidate.file_count === "number" &&
    typeof candidate.cached === "boolean"
  );
}
