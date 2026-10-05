import { createSupabaseServerClient } from "@/lib/supabase/server";

/**
 * Headers for calling the RepoChat API on behalf of the signed-in user.
 *
 * The API is not reachable from the browser: it holds the Supabase service-role
 * key and writes to the storage bucket, so every call has to originate from the
 * server. That makes Server Actions the right transport, and this is what gives
 * them the user's identity.
 *
 * The access token is the same one the browser holds, forwarded as a bearer
 * token. The API verifies its signature against the project's JWKS, so it can
 * trust the caller's identity without sharing any secret with the frontend.
 *
 * Throws when there is no session. Callers must not swallow this: a request
 * without a token is rejected by the API, and failing early gives a clearer
 * message than a 401 from the other end.
 */
export async function getAuthHeaders(): Promise<{
  headers: Record<string, string>;
  userId: string;
}> {
  const supabase = await createSupabaseServerClient();

  // Two calls, deliberately. `getSession()` alone reads whatever token the
  // cookie holds without checking it — fine for rendering a session, not for
  // authorising a request. `getUser()` revalidates against Supabase (and
  // refreshes an expired token, writing the new cookies) which is what makes
  // the token we forward trustworthy. Order matters: read the token second, so
  // it is the refreshed one.
  const { data: userData, error } = await supabase.auth.getUser();

  if (error || !userData.user) {
    throw new Error("Not signed in. Sign in again to continue.");
  }

  const { data: sessionData } = await supabase.auth.getSession();
  const accessToken = sessionData.session?.access_token;

  if (!accessToken) {
    throw new Error("Your session expired. Sign in again to continue.");
  }

  return {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    userId: userData.user.id,
  };
}

/**
 * Base URL of the API server.
 *
 * Server-only on purpose: `BACKEND_URL` is not a NEXT_PUBLIC_ var, so it never
 * reaches the browser and cannot be used to point someone else's browser at a
 * different host. Defaults to the local dev server started with
 * `python main.py`.
 */
export function getBackendUrl(): string {
  return (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}
