"use client";

import { useState } from "react";
import { GoogleIcon, SpinnerIcon } from "@/components/icons";

type Props = {
  /** Shown instead of the label while the redirect is being prepared. */
  busyLabel?: string;
  className?: string;
};

/**
 * Starts the Supabase Google OAuth flow. Supabase redirects the browser to
 * Google, so this button has no "success" state to render — it either
 * navigates away or reports an error inline.
 */
export function GoogleSignInButton({ busyLabel = "Redirecting…", className = "" }: Props) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function handleClick() {
    setError("");
    setPending(true);

    try {
      const { getSupabase } = await import("@/lib/supabase");
      const supabase = getSupabase();

      const { error: oauthError } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: `${window.location.origin}/auth/callback`,
          scopes: "openid profile email",
        },
      });

      if (oauthError) {
        setError(oauthError.message);
        setPending(false);
      }
      // On success the browser is already navigating away; leave `pending`
      // true so the button stays disabled and doesn't look re-clickable.
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not start sign-in. Check your Supabase configuration.",
      );
      setPending(false);
    }
  }

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={handleClick}
        disabled={pending}
        className={
          "flex h-11 w-full items-center justify-center gap-2.5 rounded-xl border " +
          "border-border-base bg-surface text-sm font-medium text-foreground " +
          "transition-[background-color,border-color,transform] " +
          "hover:border-border-strong hover:bg-surface-subtle " +
          "active:scale-[0.99] disabled:pointer-events-none disabled:opacity-65 " +
          className
        }
      >
        {pending ? <SpinnerIcon className="h-4 w-4" /> : <GoogleIcon />}
        {pending ? busyLabel : "Continue with Google"}
      </button>

      {error ? (
        <p role="alert" className="text-[12.5px] leading-relaxed text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}
