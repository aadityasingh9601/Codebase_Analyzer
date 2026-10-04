/**
 * Supabase browser client.
 *
 * The publishable (anon) key is safe to ship to the browser — it only allows
 * signed-in users to read/write what RLS permits. Never put the service-role
 * or secret key in here: anything in a "use client" module is public.
 *
 * Called lazily so a missing key produces a readable error in the UI instead
 * of crashing the module at import time.
 */
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null = null;

export function getSupabaseUrl(): string {
  return process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
}

export function getSupabaseKey(): string {
  return (
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ??
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??
    ""
  );
}

/** True once both required env vars are present. */
export function isSupabaseConfigured(): boolean {
  return Boolean(getSupabaseUrl() && getSupabaseKey());
}

export function getSupabase(): SupabaseClient {
  if (!isSupabaseConfigured()) {
    throw new Error(
      "Supabase is not configured. Add NEXT_PUBLIC_SUPABASE_URL and " +
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY to frontend/.env.local",
    );
  }

  if (!client) {
    client = createClient(getSupabaseUrl(), getSupabaseKey(), {
      auth: {
        // Google OAuth returns to /auth/callback with a `?code=` param, which
        // we exchange for a session in the callback route.
        flowType: "pkce",
        detectSessionInUrl: true,
        persistSession: true,
        autoRefreshToken: true,
      },
    });
  }

  return client;
}
