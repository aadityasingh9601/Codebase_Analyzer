"use server";

import { getAuthHeaders, getBackendUrl } from "@/lib/utils/getSession";

/**
 * Server Action: ask the backend a question about a connected repository.
 *
 * Runs on the server so the Supabase service-role credentials stay there and
 * the caller's session token travels server-to-server, mirroring
 * `loadGithubRepo`. The backend keeps one reasoning session per (user, repo,
 * commit) and answers by reading the shared in-memory index, so no state is
 * expected on the browser side beyond the caller's selected repo.
 *
 * The backend answers non-streamingly (Phase 1 scope): the whole answer arrives
 * once, with the file paths it drew on, so the UI shows a loading state while
 * this is in flight rather than a token stream.
 *
 * The API can take a long time on a cold index (downloads the repo JSON from
 * storage into a temp file before the model can read it), so the timeout is
 * generous. A failure here is a real failure, not a slow answer.
 */
const TIMEOUT_MS = 5 * 60 * 1000;

/** Matches the `POST /api/userquery` success body. */
export type AnsweredQuery = {
  answer: string;
  tier: string;
  reason: string;
  evidencePaths: string[];
  repoName: string;
  sha: string;
  elapsedSeconds: number;
  /** Present when routing inside the backend failed (model error, budget). */
  error: string | null;
};

/**
 * Serialisable result. Failures come back as `{ ok: false, error }` rather
 * than a thrown error, so the caller can show a message instead of Next's
 * opaque server-action digest.
 */
export type AskResult =
  | { ok: true; query: AnsweredQuery }
  | { ok: false; error: string };

/**
 * Ask about `repoName` (owner/repo) at `sha`. Omit `sha` to use the commit the
 * backend already indexed for that repository.
 */
export async function askQuestion(
  question: string,
  repoName?: string,
  sha?: string,
): Promise<AskResult> {
  if (!repoName) {
    // Guard against the no-repo chat; the UI disables asking, but a stray call
    // should fail with a clear message rather than a confusing 422.
    return {
      ok: false,
      error: "Connect a repository first, then ask about its code.",
    };
  }

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
    response = await fetch(`${getBackendUrl()}/api/userquery`, {
      method: "POST",
      headers,
      body: JSON.stringify({ question, repo_name: repoName, sha: sha || undefined }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      // The query can take minutes; do not let Next's fetch fail first.
      cache: "no-store",
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      ok: false,
      error: timedOut
        ? "Answering took longer than 5 minutes and was abandoned. Try a more specific question."
        : `Could not reach the answering service at ${getBackendUrl()}. Is it running?`,
    };
  }

  const payload = await readJson(response);
  console.log(payload);

  if (!response.ok) {
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  if (!isAnsweredQuery(payload)) {
    return { ok: false, error: "The answering service returned an unexpected response." };
  }

  return {
    ok: true,
    query: {
      answer: payload.answer,
      tier: payload.tier,
      reason: payload.reason ?? "",
      evidencePaths: payload.evidence_paths ?? [],
      repoName: payload.repo_name,
      sha: payload.sha,
      elapsedSeconds: payload.elapsed_seconds,
      error: payload.error ?? null,
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
    // FastAPI's validation errors arrive as a list of objects.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first?.msg === "string") return first.msg;
    }
  }

  if (typeof payload === "string" && payload.trim() && !payload.startsWith("<")) {
    return payload.slice(0, 300);
  }

  return `The answering service failed (HTTP ${status}).`;
}

function isAnsweredQuery(value: unknown): value is {
  answer: string;
  tier: string;
  reason?: string;
  evidence_paths?: string[];
  repo_name: string;
  sha: string;
  elapsed_seconds: number;
  error?: string | null;
} {
  if (typeof value !== "object" || value === null) return false;

  const candidate = value as {
    answer?: unknown;
    tier?: unknown;
    reason?: unknown;
    evidence_paths?: unknown;
    repo_name?: unknown;
    sha?: unknown;
    elapsed_seconds?: unknown;
    error?: unknown;
  };

  return (
    typeof candidate.answer === "string" &&
    typeof candidate.tier === "string" &&
    typeof candidate.repo_name === "string" &&
    typeof candidate.sha === "string" &&
    typeof candidate.elapsed_seconds === "number"
  );
}