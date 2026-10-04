"use client";

import { useEffect, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useSession } from "@/hooks/use-session";
import { AUTH_PATH } from "@/lib/constants";

/**
 * Client-side half of the auth gate.
 *
 * `proxy.ts` already blocks unauthenticated requests before the page renders,
 * so this is not the primary defence. It covers the cases the proxy cannot see:
 * a session that expires while the tab sits open, a sign-out in another tab,
 * and the one legitimate case where an expired access token is allowed through
 * for the client to renew.
 *
 * Children render only once a session is confirmed, so a protected page is
 * never painted for a signed-out visitor.
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { status } = useSession();
  const pathname = usePathname();

  useEffect(() => {
    if (status !== "anonymous") return;

    // `next` lets the login flow return the visitor to this page.
    const next = `${pathname}${window.location.search}`;
    window.location.replace(`${AUTH_PATH}?next=${encodeURIComponent(next)}`);
  }, [status, pathname]);

  if (status === "authenticated" || status === "unconfigured") {
    return <>{children}</>;
  }

  return (
    <main className="flex flex-1 items-center justify-center px-5">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border-base border-t-accent" />
        <p className="text-[13px] text-muted">
          {status === "checking" ? "Checking your session…" : "Redirecting to sign in…"}
        </p>
      </div>
    </main>
  );
}
