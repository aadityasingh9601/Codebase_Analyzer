"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { ArrowRightIcon, GithubIcon, SpinnerIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { parseGitHubRepo } from "@/lib/repo";
import { useSelectedRepo } from "@/hooks/use-selected-repo";
import { writeSelectedRepo } from "@/lib/repo";

/**
 * The steps run on the client as a stand-in for the real backend job
 * (clone → parse → upload). Each has a deliberate delay so the loading state
 * can be reviewed; swap `runFetchPipeline` for the real request later.
 */
const STEPS = [
  { label: "Cloning repository", detail: "shallow clone, latest commit" },
  { label: "Indexing source files", detail: "walking the tree, reading files" },
  { label: "Building search index", detail: "symbol + keyword metadata" },
  { label: "Uploading context", detail: "chunked upload to storage" },
];

export function RepoSetup() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const existing = useSelectedRepo();

  // A repo handed over from the chat page's "change repo" action. Read as a
  // URL value rather than mirrored into state, so there is nothing to sync.
  const fromChat = searchParams.get("repo") ?? "";

  const [value, setValue] = useState(fromChat);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [step, setStep] = useState(-1);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (running) return;

    const parsed = parseGitHubRepo(value);
    if (!parsed) {
      setError(
        "That doesn't look like a GitHub repository. Try https://github.com/owner/repo",
      );
      return;
    }

    setError("");
    setRunning(true);
    setStep(0);

    const ok = await runFetchPipeline((next) => setStep(next));
    if (!ok) {
      setRunning(false);
      setStep(-1);
      return;
    }

    writeSelectedRepo({
      slug: parsed.slug,
      url: parsed.url,
      addedAt: new Date().toISOString(),
    });

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
                  if (error) setError("");
                }}
                placeholder="https://github.com/calcom/cal.diy"
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
                className="mt-2.5 text-[12.5px] text-red-600 dark:text-red-400"
              >
                {error}
              </p>
            ) : null}

            {running ? (
              <ol className="mt-5 space-y-2.5 border-t border-border-base pt-5">
                {STEPS.map((s, i) => {
                  const done = i < step;
                  const active = i === step;
                  return (
                    <li key={s.label} className="flex items-start gap-2.5">
                      <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
                        {done ? (
                          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                        ) : active ? (
                          <SpinnerIcon className="h-3.5 w-3.5 text-accent" />
                        ) : (
                          <span className="h-1.5 w-1.5 rounded-full bg-border-strong" />
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span
                          className={
                            "block text-[13.5px] leading-tight " +
                            (active || done
                              ? "text-foreground"
                              : "text-muted/65")
                          }
                        >
                          {s.label}
                        </span>
                        <span className="mt-0.5 block text-[11.5px] text-muted/80">
                          {s.detail}
                        </span>
                      </span>
                    </li>
                  );
                })}
              </ol>
            ) : null}

            <Button
              type="submit"
              size="lg"
              disabled={running || !value.trim()}
              className="mt-5 w-full"
            >
              {running ? (
                <>
                  <SpinnerIcon className="h-4 w-4" />
                  Fetching repository…
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
              Demo mode: the indexing steps above are simulated so the interface
              is reviewable end to end. Wire{" "}
              <code className="font-mono text-[12px]">runFetchPipeline</code> to
              your backend when the API is ready.
            </Notice>
          </div>
        </div>
      </main>
    </AppShell>
  );
}

/** Placeholder for the real clone/parse/upload call. */
async function runFetchPipeline(
  onStep: (step: number) => void,
): Promise<boolean> {
  const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

  for (let i = 0; i < STEPS.length; i++) {
    onStep(i);
    // Last step lingers briefly so the completed state is visible before the
    // redirect.
    await delay(i === STEPS.length - 1 ? 420 : 620);
  }
  onStep(STEPS.length);
  return true;
}
