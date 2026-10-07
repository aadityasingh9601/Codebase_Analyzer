"use server";

import { getAuthHeaders, getBackendUrl } from "@/lib/utils/getSession";

/**
 * Server Action: the signed-in user's uploaded repositories.
 *
 * Backs the chat sidebar: lists which repositories this user has already
 * indexed (metadata only — the index itself is downloaded per query). Runs on
 * the server so the caller's session token travels server-to-server, mirroring
 * `loadGithubRepo`.
 *
 * Repositories are PRIVATE to the user who uploaded them, and the quota is
 * `repo_limit` (two). The sidebar shows the caller's own rows only; the
 * backend enforces both the owner check and the limit, this action just renders.
 */
const TIMEOUT_MS = 30_000;

/** A connected repository row for the sidebar (matches `GET /api/repos`). */
export type UserRepo = {
  repoName: string;
  url: string;
  branch: string;
  sha: string;
  fileCount: number;
  totalLines: number;
  description: string;
  createdAt: string;
  updatedAt: string;
};

/** Serialisable result, like the other server actions. */
export type GetReposResult =
  | { ok: true; repos: UserRepo[]; repoLimit: number }
  | { ok: false; error: string };

export async function getUserRepos(): Promise<GetReposResult> {
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
    response = await fetch(`${getBackendUrl()}/api/repos`, {
      headers,
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      ok: false,
      error: timedOut
        ? "Loading your repositories timed out. Try again."
        : `Could not reach the repo service at ${getBackendUrl()}. Is it running?`,
    };
  }

  const payload = await readJson(response);

  if (!response.ok) {
    return { ok: false, error: errorFrom(payload, response.status) };
  }

  if (!isReposResponse(payload)) {
    return { ok: false, error: "The repo service returned an unexpected response." };
  }

  return {
    ok: true,
    repos: payload.repos.map((row) => ({
      repoName: row.repo_name,
      url: row.url,
      branch: row.branch,
      sha: row.sha,
      fileCount: row.file_count,
      totalLines: row.total_lines,
      description: row.description ?? "",
      createdAt: row.created_at ?? "",
      updatedAt: row.updated_at ?? "",
    })),
    repoLimit: payload.repo_limit ?? 2,
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

  return `The repo service failed (HTTP ${status}).`;
}

function isReposResponse(
  value: unknown,
): value is {
  repos: Array<{
    repo_name: string;
    url: string;
    branch: string;
    sha: string;
    file_count: number;
    total_lines: number;
    description?: string;
    created_at?: string;
    updated_at?: string;
  }>;
  repo_limit?: number;
} {
  if (typeof value !== "object" || value === null) return false;

  const candidate = value as {
    repos?: unknown;
    repo_limit?: unknown;
  };

  if (!Array.isArray(candidate.repos)) return false;

  return candidate.repos.every((row) => {
    if (typeof row !== "object" || row === null) return false;
    const r = row as Record<string, unknown>;
    return (
      typeof r.repo_name === "string" &&
      typeof r.url === "string" &&
      typeof r.branch === "string" &&
      typeof r.sha === "string" &&
      typeof r.file_count === "number" &&
      typeof r.total_lines === "number"
    );
  });
}