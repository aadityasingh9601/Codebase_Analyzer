/**
 * Remembers where an unauthenticated visitor was heading, so the Google round
 * trip can drop them back on the page they asked for instead of always
 * landing on /chat.
 *
 * A cookie rather than a `?next=` search param: Supabase appends `code` to the
 * `redirectTo` it was configured with, and threading an extra param through
 * that indirection is fragile. The callback Route Handler can read this
 * directly, which a `searchParams` value on the login page could not.
 */

const REDIRECT_COOKIE = "repochat-redirect";

/** Generous enough for a Google consent screen, short enough to be harmless. */
const MAX_AGE_SECONDS = 60 * 10;

/**
 * Accepts only same-origin relative paths.
 *
 * Returns null for anything else — an absolute URL here would turn the login
 * flow into an open redirect, letting an attacker bounce a signed-in user to a
 * page of their choosing. `//evil.com` is rejected too, since browsers read it
 * as protocol-relative.
 */
export function sanitiseRedirect(value: string | null | undefined): string | null {
  if (!value) return null;

  const trimmed = value.trim();
  if (!trimmed.startsWith("/")) return null;
  if (trimmed.startsWith("//")) return null;
  // Same page as the login screen would cause a pointless bounce.
  if (trimmed === "/auth" || trimmed.startsWith("/auth/")) return null;

  return trimmed;
}

/** Browser-only: stores the post-login destination. */
export function rememberRedirect(path: string): void {
  const safe = sanitiseRedirect(path);
  if (!safe) return;

  try {
    document.cookie =
      `${REDIRECT_COOKIE}=${encodeURIComponent(safe)}; path=/; max-age=${MAX_AGE_SECONDS}; samesite=lax`;
  } catch {
    // Cookies unavailable (private mode): fall back to the default landing page.
  }
}

/** The cookie name, so a Route Handler can expire it on its own response. */
export const REDIRECT_COOKIE_NAME = REDIRECT_COOKIE;

/** Server-only: reads the stored destination. Callers expire the cookie. */
export function readRedirectCookie(
  cookieHeader: string | null | undefined,
): string | null {
  const raw = (cookieHeader ?? "")
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${REDIRECT_COOKIE}=`))
    ?.slice(REDIRECT_COOKIE.length + 1);

  if (!raw) return null;

  try {
    return sanitiseRedirect(decodeURIComponent(raw));
  } catch {
    return null;
  }
}

/** Drops a stale destination so a later sign-in cannot reuse it. */
export function clearRedirect(): void {
  try {
    document.cookie = `${REDIRECT_COOKIE}=; path=/; max-age=0; samesite=lax`;
  } catch {
    // Ignore.
  }
}
