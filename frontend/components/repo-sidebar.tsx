"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getUserRepos,
  type UserRepo,
} from "@/app/lib/actions/getUserRepos";
import { FileIcon, GithubIcon, SpinnerIcon, ArrowRightIcon } from "@/components/icons";
import { useSelectedRepo } from "@/hooks/use-selected-repo";
import { writeSelectedRepo } from "@/lib/repo";

/**
 * The signed-in user's uploaded repositories. Selecting one writes the shared
 * selection (localStorage + REPO_CHANGE_EVENT), which the chat transcript
 * resets to in `chat-view`.
 *
 * The list is metadata from `GET /api/repos` for THIS user only — repos are
 * private per user, so nobody else's shows up here.
 */
export function RepoSidebar() {
  const router = useRouter();
  const selected = useSelectedRepo();

  const [repos, setRepos] = useState<UserRepo[]>([]);
  const [repoLimit, setRepoLimit] = useState(2);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      const result = await getUserRepos();
      if (cancelled) return;
      setLoading(false);

      if (!result.ok) {
        setError(result.error);
        return;
      }

      setError("");
      setRepos(result.repos);
      setRepoLimit(result.repoLimit);
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const atLimit = repos.length >= repoLimit;

  function select(repo: UserRepo) {
    writeSelectedRepo({
      slug: repo.repoName,
      url: repo.url,
      addedAt: repo.createdAt || new Date().toISOString(),
      sha: repo.sha,
      branch: repo.branch,
      fileCount: repo.fileCount,
      totalLines: repo.totalLines,
      description: repo.description,
    });
  }

  return (
    <aside className="hidden w-[248px] shrink-0 flex-col border-r border-border-base bg-surface-subtle/40 md:flex">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <p className="text-[11px] font-medium uppercase tracking-[0.07em] text-muted">
          Repositories
        </p>
        {!loading && (
          <span className="text-[11px] text-muted">
            {repos.length} / {repoLimit}
          </span>
        )}
      </div>

      <nav className="scroll-slim flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-3 pb-3">
        {loading ? (
          <p className="flex items-center gap-1.5 px-1 py-2 text-[12px] text-muted">
            <SpinnerIcon className="h-3.5 w-3.5" />
            Loading…
          </p>
        ) : error ? (
          <div className="px-1 py-2">
            <p className="text-[12px] leading-relaxed text-muted">{error}</p>
            <button
              type="button"
              onClick={() => {
                setLoading(true);
                setError("");
                getUserRepos().then((result) => {
                  setLoading(false);
                  if (!result.ok) {
                    setError(result.error);
                    return;
                  }
                  setRepos(result.repos);
                  setRepoLimit(result.repoLimit);
                });
              }}
              className="mt-1.5 text-[12px] font-medium text-accent hover:underline"
            >
              Try again
            </button>
          </div>
        ) : repos.length === 0 ? (
          <div className="px-1 py-2">
            <p className="text-[12px] leading-relaxed text-muted">
              No repositories yet. Connect one to start asking grounded questions.
            </p>
            <button
              type="button"
              onClick={() => router.push("/get-started")}
              className="mt-2 inline-flex items-center gap-1.5 text-[12px] font-medium text-accent hover:underline"
            >
              <GithubIcon className="h-3.5 w-3.5" />
              Connect a repo
            </button>
          </div>
        ) : (
          repos.map((repo) => {
            const active = selected?.slug === repo.repoName;
            return (
              <button
                key={repo.repoName}
                type="button"
                onClick={() => select(repo)}
                aria-current={active ? "true" : undefined}
                className={`group rounded-xl border px-3 py-2.5 text-left transition-[background-color,border-color] ${
                  active
                    ? "border-accent/40 bg-accent/10"
                    : "border-border-base bg-surface hover:border-border-strong hover:bg-surface-subtle"
                }`}
              >
                <p className="flex items-center gap-1.5 font-mono text-[12.5px] font-medium text-foreground">
                  <FileIcon className="h-3.5 w-3.5 shrink-0 text-muted" />
                  <span className="truncate">{repo.repoName}</span>
                </p>
                <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
                  {repo.branch ? (
                    <span className="font-mono">{repo.branch}</span>
                  ) : null}
                  {repo.sha ? (
                    <span className="font-mono">@{repo.sha.slice(0, 7)}</span>
                  ) : null}
                  {repo.fileCount > 0 ? (
                    <span>{repo.fileCount.toLocaleString()} files</span>
                  ) : null}
                  {repo.totalLines > 0 ? (
                    <span>{repo.totalLines.toLocaleString()} lines</span>
                  ) : null}
                </p>
              </button>
            );
          })
        )}
      </nav>

      <div className="border-t border-border-base px-3 py-3">
        <button
          type="button"
          onClick={() => router.push("/get-started")}
          disabled={atLimit}
          className="inline-flex w-full items-center gap-1.5 rounded-lg border border-border-base bg-surface px-3 py-2 text-[12.5px] font-medium text-foreground transition-[background-color,border-color] hover:border-border-strong hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-50"
        >
          <GithubIcon className="h-4 w-4" />
          {atLimit ? "Repo limit reached" : "Add a repository"}
          {!atLimit ? <ArrowRightIcon className="ml-auto h-3.5 w-3.5 text-muted" /> : null}
        </button>
      </div>
    </aside>
  );
}