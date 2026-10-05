"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { ArrowRightIcon, CheckIcon, GithubIcon, SpinnerIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { loadGithubRepo, type LoadedRepo } from "@/app/lib/actions/loadGithubRepo";
import { parseGitHubRepo, writeSelectedRepo } from "@/lib/repo";
import { useSelectedRepo } from "@/hooks/use-selected-repo";

/**
 * What the backend does during a load. Listed as context while the request is
 * in flight — not as progress: the API is a single blocking call, so there is no
 * per-step signal to show and inventing one would be a lie.
 */
const PIPELINE_STEPS = [
  "Check whether this commit is already indexed",
  "Shallow-clone the repository",
  "Parse source files into the index",
  "Store the index in Supabase",
];

type Status =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "done"; repo: LoadedRepo }
  | { phase: "error"; message: string };

/** Long enough to read the result, short enough not to feel like a stall. */
const RESULT_DWELL_MS = 700;

export function RepoSetup() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const existing = useSelectedRepo();

  // A repo handed over from the chat page's "change repo" action. Read as a
  // URL value rather than mirrored into state, so there is nothing to sync.
  const fromChat = searchParams.get("repo") ?? "";

  const [value, setValue] = useState(fromChat);
  const [status, setStatus] = useState<Status>({ phase: "idle" });

  const running = status.phase === "loading";
  const error = status.phase === "error" ? status.message : "";

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (running) return;

    const parsed = parseGitHubRepo(value);
    if (!parsed) {
      setStatus({
        phase: "error",
        message: "That doesn't look like a GitHub repository. Try https://github.com/owner/repo",
      });
      return;
    }

    setStatus({ phase: "loading" });

    const result = await loadGithubRepo(parsed.url);

    if (!result.ok) {
      setStatus({ phase: "error", message: result.error });
      return;
    }

    setStatus({ phase: "done", repo: result.repo });

    writeSelectedRepo({
      slug: result.repo.repo_name,
      url: result.repo.url,
      addedAt: new Date().toISOString(),
      sha: result.repo.sha,
      branch: result.repo.branch,
      fileCount: result.repo.file_count,
      totalLines: result.repo.total_lines,
      description: result.repo.description,
      storagePath: result.repo.storage_path,
      cached: result.repo.cached,
    });

    await new Promise((resolve) => setTimeout(resolve, RESULT_DWELL_MS));
    router.push("/chat");
  }

  const showExisting = Boolean(existing) && !running && !fromChat;

  return (
    <AppShell repo={existing}>
      <main className="flex flex-1 items-center justify-center px-5 py-14">
        <div className="w-full max-w-[560px]">
          <div className="mb-8 text-center">
            <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.025em] text-foreground">
              Point us at a repository
            </h1>
            <p className="mx-auto mt-2 max-w-[420px] text-[14px] leading-relaxed text-muted">
              We index the source so every answer can cite real files. Paste a
              GitHub URL to begin.
            </p>
          </div>

          <form
            onSubmit={handleSubmit}
            className="rounded-2xl border border-border-base bg-surface p-5 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_12px_28px_-14px_rgba(0,0,0,0.14)]"
          >
            <label
              htmlFor="repo-url"
              className="mb-2 block text-[12.5px] font-medium text-foreground"
            >
              GitHub repository URL
            </label>

            <div className="relative">
              <GithubIcon className="pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-muted" />
              <input
                id="repo-url"
                type="text"
                inputMode="url"
                autoComplete="off"
                spellCheck={false}
                value={value}
                onChange={(e) => {
                  setValue(e.target.value);
                  if (error) setStatus({ phase: "idle" });
                }}
                placeholder="https://github.com/pallets/click"
                disabled={running}
                className={
                  "h-12 w-full rounded-xl border border-border-base bg-background pl-11 pr-4 " +
                  "font-mono text-[13.5px] text-foreground placeholder:font-sans " +
                  "placeholder:text-muted/70 outline-none " +
                  "focus:border-accent focus:ring-4 focus:ring-accent/12 " +
                  "disabled:opacity-60"
                }
              />
            </div>

            {error ? (
              <p
                role="alert"
                className="mt-2.5 text-[12.5px] leading-relaxed text-red-600 dark:text-red-400"
              >
                {error}
              </p>
            ) : null}

            {running ? (
              <div className="mt-5 rounded-xl border border-border-base bg-surface-subtle px-4 py-3.5">
                <div className="flex items-center gap-2.5 text-[13.5px] text-foreground">
                  <SpinnerIcon className="h-4 w-4 text-accent" />
                  <span>Indexing repository…</span>
                </div>
                <ul className="mt-2.5 space-y-1 pl-6 text-[11.5px] leading-relaxed text-muted">
                  {PIPELINE_STEPS.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ul>
                <p className="mt-2.5 pl-6 text-[11.5px] text-muted/80">
                  A large repository can take a few minutes. Keep this tab open.
                </p>
              </div>
            ) : null}

            {status.phase === "done" ? <IndexedSummary repo={status.repo} /> : null}

            <Button
              type="submit"
              size="lg"
              disabled={running || !value.trim()}
              className="mt-5 w-full"
            >
              {running ? (
                <>
                  <SpinnerIcon className="h-4 w-4" />
                  Indexing…
                </>
              ) : (
                <>
                  Index this repository
                  <ArrowRightIcon className="h-4 w-4" />
                </>
              )}
            </Button>
          </form>

          {showExisting && existing ? (
            <div className="mt-5 flex items-center justify-between gap-3 rounded-xl border border-border-base bg-surface-subtle px-4 py-3">
              <div className="min-w-0">
                <p className="text-[11px] font-medium uppercase tracking-[0.07em] text-muted">
                  Already connected
                </p>
                <p className="truncate font-mono text-[13px] text-foreground">
                  {existing.slug}
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => router.push("/chat")}
              >
                Open chat
              </Button>
            </div>
          ) : null}

          <div className="mt-7">
            <Notice tone="info">
              Indexing runs on the backend and can take a few minutes on a large
              repository. If a commit has been indexed before, it is reused
              instead of rebuilt.
            </Notice>
          </div>
        </div>
      </main>
    </AppShell>
  );
}

/** Confirmation shown for a moment before the redirect to /chat. */
function IndexedSummary({ repo }: { repo: LoadedRepo }) {
  return (
    <div className="mt-5 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] px-4 py-3.5">
      <div className="flex items-center gap-2.5 text-[13.5px] font-medium text-foreground">
        <CheckIcon className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
        {repo.cached ? "Already indexed — reused the stored copy" : "Indexed successfully"}
      </div>
      <p className="mt-2 pl-[26px] text-[12.5px] leading-relaxed text-muted">
        <span className="font-mono text-foreground">{repo.repo_name}</span>
        {" · "}
        {repo.file_count.toLocaleString()} files
        {" · "}
        {repo.total_lines.toLocaleString()} lines
        {" · "}
        {repo.branch} @ <span className="font-mono">{repo.sha.slice(0, 7)}</span>
      </p>
    </div>
  );
}
