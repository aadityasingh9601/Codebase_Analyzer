"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { User } from "@supabase/supabase-js";
import { LogOutIcon } from "@/components/icons";
import { isSupabaseConfigured } from "@/lib/supabase";

/**
 * Shows the signed-in avatar, or nothing while Supabase is unconfigured so the
 * navbar stays clean during development.
 */
export function UserMenu() {
  const router = useRouter();
  const configured = isSupabaseConfigured();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    if (!configured) return;

    let active = true;
    let unsubscribe: (() => void) | undefined;

    (async () => {
      const { getSupabase } = await import("@/lib/supabase");
      const supabase = getSupabase();

      const { data } = await supabase.auth.getUser();
      if (!active) return;
      setUser(data.user ?? null);

      const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
        if (active) setUser(session?.user ?? null);
      });
      unsubscribe = () => sub.subscription.unsubscribe();
    })();

    return () => {
      active = false;
      unsubscribe?.();
    };
  }, [configured]);

  if (!configured || !user) return null;

  const email = user.email ?? "";
  const initial = (user.user_metadata?.full_name ?? email ?? "?").toString().trim().charAt(0).toUpperCase();
  const avatarUrl = user.user_metadata?.avatar_url as string | undefined;

  async function handleSignOut() {
    const { getSupabase } = await import("@/lib/supabase");
    await getSupabase().auth.signOut();
    router.push("/auth");
    router.refresh();
  }

  return (
    <div className="group relative">
      <button
        type="button"
        aria-label="Account"
        className="flex h-9 items-center gap-2 rounded-lg pl-1 pr-2 transition-colors hover:bg-surface-subtle"
      >
        {avatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- external avatar host; next/image needs remotePatterns configured
          <img
            src={avatarUrl}
            alt=""
            className="h-7 w-7 rounded-full object-cover"
            width={28}
            height={28}
          />
        ) : (
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-subtle text-[12px] font-semibold text-accent">
            {initial}
          </span>
        )}
        <span className="hidden max-w-[120px] truncate text-[13px] text-muted md:inline">
          {email}
        </span>
      </button>

      <div className="invisible absolute right-0 top-full z-40 mt-1 w-52 translate-y-1 rounded-xl border border-border-base bg-surface p-1.5 opacity-0 shadow-lg transition-[opacity,transform] group-focus-within:visible group-focus-within:translate-y-0 group-focus-within:opacity-100 group-hover:visible group-hover:translate-y-0 group-hover:opacity-100">
        <div className="border-b border-border-base px-2.5 py-2">
          <p className="truncate text-[13px] font-medium text-foreground">
            {(user.user_metadata?.full_name as string) || email}
          </p>
          <p className="truncate text-[11.5px] text-muted">{email}</p>
        </div>
        <button
          type="button"
          onClick={handleSignOut}
          className="mt-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-muted transition-colors hover:bg-surface-subtle hover:text-foreground"
        >
          <LogOutIcon className="h-4 w-4" />
          Sign out
        </button>
      </div>
    </div>
  );
}
