import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import type { SupabaseClient, User } from "@supabase/supabase-js";
import {
  getSupabaseKey,
  getSupabaseUrl,
  isSupabaseConfigured,
  SUPABASE_COOKIE_OPTIONS,
} from "@/lib/supabase/env";

/**
 * Server Supabase client, bound to the current request's cookies.
 *
 * Always call this fresh — never hoist it to module scope. The client caches
 * the session in memory, so sharing one across requests would leak one user's
 * session into another's request.
 *
 * Note this module imports `next/headers`, so it can only be used from Server
 * Components, Server Actions and Route Handlers. `proxy.ts` builds its own
 * client from `NextRequest` cookies instead.
 */
export async function createSupabaseServerClient(): Promise<SupabaseClient> {
  const cookieStore = await cookies();

  return createServerClient(getSupabaseUrl(), getSupabaseKey(), {
    cookieOptions: SUPABASE_COOKIE_OPTIONS,
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        // Cookies are read-only during a Server Component render; only Route
        // Handlers and the proxy may write them. If a token refresh lands here
        // we drop the write and let the proxy refresh it on the next
        // navigation. Swallowing this is deliberate — letting it throw would
        // turn a routine refresh into a 500.
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Read-only cookie context.
        }
      },
    },
  });
}

/**
 * The signed-in user for this request, or null.
 *
 * Uses `getUser()`, which asks the Auth server who the token belongs to, rather
 * than `getSession()`, which only decodes the cookie. `getSession()` is not a
 * security check: anyone can hand-craft a cookie with an arbitrary `sub`.
 */
export async function getServerUser(): Promise<User | null> {
  if (!isSupabaseConfigured()) return null;

  try {
    const supabase = await createSupabaseServerClient();
    const { data, error } = await supabase.auth.getUser();

    return error ? null : data.user;
  } catch {
    return null;
  }
}
