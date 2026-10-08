/** Repo selection shared between /upload-repo and /chat. */

export const REPO_STORAGE_KEY = "repochat-selected-repo";

/** Fired on `window` whenever the stored repo changes, so tabs stay in sync. */
export const REPO_CHANGE_EVENT = "repochat:repo-changed";

export type SelectedRepo = {
  slug: string;
  url: string;
  addedAt: string;
  /**
   * The `repos` table row id in Supabase. Populated once the backend has
   * actually indexed the repo. Chat history is scoped to it: messages are
   * saved under this id when the user chats, and reloaded when they return.
   */
  id?: string;
  /**
   * Populated once the backend has actually indexed the repo. A selection
   * without these is a URL the user typed but never indexed.
   */
  sha?: string;
  branch?: string;
  fileCount?: number;
  totalLines?: number;
  description?: string;
  storagePath?: string;
  /** True when the index already existed and nothing was rebuilt. */
  cached?: boolean;
};

/**
 * Parse a GitHub repo URL into `owner/repo`.
 *
 * Accepts what people actually paste:
 *   https://github.com/calcom/cal.diy
 *   https://github.com/calcom/cal.diy.git
 *   github.com/calcom/cal.diy
 *   git@github.com:calcom/cal.diy.git
 *   calcom/cal.diy
 *
 * Returns null when the input isn't recognisable so callers can show a
 * friendly message instead of a cryptic fetch failure.
 */
export function parseGitHubRepo(input: string): {
  owner: string;
  repo: string;
  slug: string;
  url: string;
} | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  // Strip a trailing .git and any trailing slashes.
  let cleaned = trimmed.replace(/\.git$/, "").replace(/\/+$/, "");

  // scp-style SSH remote: git@github.com:owner/repo
  const sshMatch = cleaned.match(/^git@[^:]+:([^/]+)\/(.+)$/);
  if (sshMatch) {
    cleaned = `${sshMatch[1]}/${sshMatch[2]}`;
  } else {
    // https://github.com/owner/repo → owner/repo
    cleaned = cleaned.replace(/^(https?:\/\/)?(www\.)?github\.com\//i, "");
  }

  const parts = cleaned.split("/").filter(Boolean);
  if (parts.length !== 2) return null;

  const [owner, repo] = parts;
  // GitHub owner/repo names: letters, digits, dots, dashes, underscores.
  const validSegment = /^[A-Za-z0-9._-]+$/;
  if (!validSegment.test(owner) || !validSegment.test(repo)) return null;

  return {
    owner,
    repo,
    slug: `${owner}/${repo}`,
    url: `https://github.com/${owner}/${repo}`,
  };
}

/** The raw localStorage string, or null. Uncached — callers may memoise. */
export function readRepoRaw(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(REPO_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeSelectedRepo(repo: SelectedRepo): void {
  try {
    localStorage.setItem(REPO_STORAGE_KEY, JSON.stringify(repo));
    window.dispatchEvent(new Event(REPO_CHANGE_EVENT));
  } catch {
    // Non-fatal: chat falls back to a placeholder repo name.
  }
}

export function clearSelectedRepo(): void {
  try {
    localStorage.removeItem(REPO_STORAGE_KEY);
    window.dispatchEvent(new Event(REPO_CHANGE_EVENT));
  } catch {
    // Ignore.
  }
}
