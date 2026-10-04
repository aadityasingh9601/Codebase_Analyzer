"use client";

import { useState } from "react";
import type { User } from "@supabase/supabase-js";
import { LogOutIcon, SpinnerIcon } from "@/components/icons";
import { useSession } from "@/hooks/use-session";
import { signOutAndRedirect } from "@/lib/auth/sign-out";

/**
 * Account chip and sign-out button, pinned to the top right of the navbar.
 *
 * The button is always visible rather than tucked behind an avatar menu: ending
 * a session is a frequent, deliberate action, not one worth a hover. On narrow
 * screens the label drops and the icon keeps the target at a usable size.
 *
 * Renders nothing until a session exists, which keeps the navbar clean in demo
 * mode and avoids a layout that appears only after hydration.
 */
export function UserMenu() {
  const { status, user } = useSession();
  const [pending, setPending] = useState(false);

  if (status !== "authenticated" || !user) return null;

  return (
    <div className="flex items-center gap-2">
      <AccountChip user={user} />
      <SignOutButton
        pending={pending}
        onSignOut={async () => {
          setPending(true);
          try {
            await signOutAndRedirect();
          } finally {
            // Only reached if the redirect is blocked; otherwise the document
            // is being replaced and this never paints.
            setPending(false);
          }
        }}
      />
    </div>
  );
}

function AccountChip({ user }: { user: User }) {
  const email = user.email ?? "";
  const name = (user.user_metadata?.full_name as string) || email || "Account";
  const initial = name.trim().charAt(0).toUpperCase();
  const avatarUrl = user.user_metadata?.avatar_url as string | undefined;

  return (
    <span
      className="flex h-9 max-w-[190px] items-center gap-2 rounded-lg border border-border-base bg-surface-subtle pl-1 pr-2.5"
      title={email}
    >
      {avatarUrl ? (
        // eslint-disable-next-line @next/next/no-img-element -- external avatar host; next/image needs remotePatterns configured
        <img
          src={avatarUrl}
          alt=""
          className="h-7 w-7 rounded-full object-cover"
          width={28}
          height={28}
          referrerPolicy="no-referrer"
        />
      ) : (
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-subtle text-[12px] font-semibold text-accent">
          {initial}
        </span>
      )}
      <span className="hidden truncate text-[13px] text-muted md:inline">{email}</span>
    </span>
  );
}

function SignOutButton({
  pending,
  onSignOut,
}: {
  pending: boolean;
  onSignOut: () => Promise<void>;
}) {
  return (
    <button
      type="button"
      onClick={onSignOut}
      disabled={pending}
      title="Sign out"
      className="flex h-9 items-center gap-1.5 rounded-lg border border-border-base bg-surface px-2.5 text-[13px] font-medium text-muted transition-colors hover:border-border-strong hover:bg-surface-subtle hover:text-foreground disabled:pointer-events-none disabled:opacity-55"
    >
      {pending ? <SpinnerIcon className="h-4 w-4" /> : <LogOutIcon className="h-4 w-4" />}
      <span className="hidden sm:inline">{pending ? "Signing out…" : "Sign out"}</span>
      <span className="sr-only sm:hidden">{pending ? "Signing out…" : "Sign out"}</span>
    </button>
  );
}
