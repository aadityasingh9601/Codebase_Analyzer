import Link from "next/link";
import { GoogleSignInButton } from "@/components/google-signin-button";
import { AuthShell } from "@/components/brand";
import { Notice } from "@/components/ui/notice";
import { SparkIcon } from "@/components/icons";
import { AUTH_CALLBACK_PATH, POST_AUTH_REDIRECT } from "@/lib/constants";
import { isSupabaseConfigured } from "@/lib/supabase/env";

export const metadata = {
  title: "Sign in",
  description: "Sign in to RepoChat with your Google account.",
};

export default async function AuthPage(props: PageProps<"/auth">) {
  const configured = isSupabaseConfigured();
  // Set by the callback route when the OAuth exchange fails, and by Google when
  // the user declines consent.
  const { error } = await props.searchParams;

  return (
    <AuthShell
      eyebrow="Welcome"
      title="Sign in to RepoChat"
      subtitle="Ask real questions about any GitHub repository and get answers grounded in its actual source files."
    >
      <div className="space-y-4">
        {error ? (
          <Notice tone="error">
            <span className="block font-medium text-foreground">Sign-in didn&apos;t complete</span>
            <span className="mt-1 block break-words">{error}</span>
          </Notice>
        ) : null}

        {configured ? (
          <GoogleSignInButton />
        ) : (
          <>
            <GoogleSignInButton disabled />
            <Notice tone="info">
              <span className="block font-medium text-foreground">One-time setup</span>
              <span className="mt-1 block">
                Copy <code className="font-mono text-[12px]">.env.example</code> to{" "}
                <code className="font-mono text-[12px]">.env.local</code>, add your Supabase
                URL and publishable key, then enable Google under{" "}
                <em>Authentication → Providers</em> and add{" "}
                <code className="font-mono text-[12px]">{AUTH_CALLBACK_PATH}</code> as a
                redirect URL. Restart the dev server.
              </span>
            </Notice>
            <p className="text-center text-[12.5px] text-muted">
              Or{" "}
              <Link
                href={POST_AUTH_REDIRECT}
                className="font-medium text-accent underline underline-offset-2 hover:brightness-110"
              >
                browse the app without signing in
              </Link>
              .
            </p>
          </>
        )}
      </div>

      <div className="mt-6 space-y-2.5 border-t border-border-base pt-6">
        {[
          "Full-text search across every indexed file",
          "Answers cite the exact files and line ranges they used",
          "Sessions resume where you left them",
        ].map((line) => (
          <div key={line} className="flex items-start gap-2.5 text-[13px] text-muted">
            <SparkIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
            <span>{line}</span>
          </div>
        ))}
      </div>
    </AuthShell>
  );
}
