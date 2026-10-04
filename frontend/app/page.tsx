"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Root route: send people to sign in, or straight to /get-started when a
 * Supabase session already exists.
 */
export default function IndexRedirect() {
  const router = useRouter();

  useEffect(() => {
    async function resolve() {
      // Without Supabase configured there is no session to check, so go
      // straight to the repo setup step rather than showing a dead login.
      const { isSupabaseConfigured, getSupabase } = await import("@/lib/supabase");
      if (!isSupabaseConfigured()) {
        router.replace("/get-started");
        return;
      }

      try {
        const { data } = await getSupabase().auth.getUser();
        router.replace(data.user ? "/get-started" : "/auth");
      } catch {
        router.replace("/auth");
      }
    }

    void resolve();
  }, [router]);

  return (
    <main className="flex flex-1 items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border-base border-t-accent" />
        <p className="text-[13px] text-muted">Loading…</p>
      </div>
    </main>
  );
}
