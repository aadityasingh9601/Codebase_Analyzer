import { GoogleSignInButton } from "@/components/google-signin-button";
import { AuthShell } from "@/components/brand";
import { Notice } from "@/components/ui/notice";
import { isSupabaseConfigured } from "@/lib/supabase";
import { SparkIcon } from "@/components/icons";

export const metadata = {
  title: "Sign in",
  description: "Sign in to RepoChat with your Google account.",
};

export default function AuthPage() {
  const configured = isSupabaseConfigured();

  return (
    <AuthShell
      eyebrow="Welcome"
      title="Sign in to RepoChat"
      subtitle="Ask real questions about any GitHub repository and get answers grounded in its actual source files."
    >
      {configured ? (
        <GoogleSignInButton />
      ) : (
        <div className="space-y-4">
          <div className="pointer-events-none opacity-55">
            <GoogleSignInButton />
          </div>
          <Notice tone="info">
            <span className="block font-medium text-foreground">
              One-time setup
            </span>
            <span className="mt-1 block">
              Copy <code className="font-mono text-[12px]">.env.example</code>{" "}
              to <code className="font-mono text-[12px]">.env.local</code>, add
              your Supabase URL and publishable key, then enable Google under{" "}
              <em>Authentication → Providers</em>. Restart the dev server.
            </span>
          </Notice>
        </div>
      )}

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
