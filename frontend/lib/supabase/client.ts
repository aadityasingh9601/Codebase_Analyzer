"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  getSupabaseKey,
  getSupabaseUrl,
  isSupabaseConfigured,
  SUPABASE_COOKIE_OPTIONS,
} from "@/lib/supabase/env";

let client: SupabaseClient | null = null;

/**
 * Browser Supabase client.
 *
 * Built on `@supabase/ssr` rather than `createClient` so the session is kept in
 * *cookies* instead of localStorage. That is what lets `proxy.ts` verify the
 * user before a protected page renders — localStorage is invisible to the
 * server, so an app that stores its session there cannot be gated server-side.
 *
 * `createBrowserClient` already sets `flowType: "pkce"`, `detectSessionInUrl`
 * and `autoRefreshToken` for the browser and caches a singleton internally;
 * the wrapper only adds the readable error for an unconfigured project.
 */
export function getSupabase(): SupabaseClient {
  if (!isSupabaseConfigured()) {
    throw new Error(
      "Supabase is not configured. Add NEXT_PUBLIC_SUPABASE_URL and " +
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY to frontend/.env.local",
    );
  }

  client ??= createBrowserClient(getSupabaseUrl(), getSupabaseKey(), {
    cookieOptions: SUPABASE_COOKIE_OPTIONS,
  });

  return client;
}
