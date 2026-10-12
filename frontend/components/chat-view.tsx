"use client";

import { memo, Suspense, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { RepoSidebar } from "@/components/repo-sidebar";
import { CopyIcon, CheckIcon, SpinnerIcon, FileIcon, ReloadIcon, KeyIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { useSelectedRepo } from "@/hooks/use-selected-repo";
import { useActiveProvider } from "@/hooks/use-active-provider";
import { useChatMode } from "@/hooks/use-chat-mode";
import { clearSelectedRepo, writeSelectedRepo, type SelectedRepo } from "@/lib/repo";
import { setActiveProvider, type ProviderId } from "@/lib/api-key-store";
import { setChatMode } from "@/lib/chat-mode-store";
import {
  ProviderModelSelector,
  firstModel,
  type ProviderModelSelection,
} from "@/components/providerModelSelector";
import { askQuestion } from "@/app/lib/actions/askQuestion";
import { getUserRepos } from "@/app/lib/actions/getUserRepos";
import {
  fetchMessages,
  saveMessage,
} from "@/app/lib/actions/messages";
import { getUsage } from "@/app/lib/actions/usage";

// Markdown rendering (react-markdown + remark-gfm + highlight.js) is by far
// the heaviest dependency on the chat screen. Loading it into its own async
// chunk and rendering it only on the client keeps the rest of the page (input
// composer, sidebar, layout) responsive while messages hydrate.
const MarkdownContent = dynamic(
  () => import("@/components/markdown-content").then((mod) => mod.MarkdownContent),
  { ssr: false },
);

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** File paths the answer was grounded in. */
  sources?: string[];
  /** True while the assistant reply is loading. */
  pending?: boolean;
  /** Set when the backend could not produce an answer. */
  error?: string;
};

/**
 * Monotonic message ids. Lives at module scope so generating one is not an
 * impure operation inside the component body (Date.now() during render), while
 * staying unique within and across sessions.
 */
let messageSeq = 0;
function nextMessageId(prefix: "u" | "a"): string {
  messageSeq += 1;
  return `${prefix}-${messageSeq}`;
}

const SUGGESTIONS = [
  "Summarise the data model",
  "Where is authentication enforced?",
  "How are background jobs queued?",
  "What would break if I removed this cache?",
];

/**
 * Save a user question and its assistant answer to the repo's conversation.
 * Sequential on purpose, so the question is always stored before the reply
 * that answered it (conversation order is `created_at`). `repo.id` scopes the
 * history; without it (a legacy hand-typed selection) there is nothing to save.
 */
async function persistConversation(
  repo: NonNullable<SelectedRepo>,
  question: string,
  result: Awaited<ReturnType<typeof askQuestion>>,
): Promise<void> {
  if (!repo.id) return;

  try {
    const res = await saveMessage(repo.id, "user", question);
    console.log("response", res);
  } catch {
    return;
  }

  if (result.ok && result.query.answer) {
    const res = await saveMessage(repo.id, "assistant", result.query.answer, result.query.evidencePaths);
    console.log(res);
  }
}

export function ChatView() {
  const router = useRouter();
  const pathname = usePathname();
  const repo = useSelectedRepo();
  const activeProvider = useActiveProvider();
  const chatMode = useChatMode();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [responding, setResponding] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [usage, setUsage] = useState<{ used: number; limit: number } | null>(null);

  // Provider + model for the next question. Default provider is OpenRouter,
  // per the UI spec; changing the provider resets the model to its first one.
  const [provider, setProvider] = useState<ProviderId>("openrouter");
  const [model, setModel] = useState<string>(() => firstModel("openrouter"));

  useEffect(() => {
    if (chatMode === "free") {
      setProvider("openrouter");
      setModel(firstModel("openrouter"));
    }
  }, [chatMode]);

  useEffect(() => {
    if (chatMode !== "free" || !repo) return;
    let cancelled = false;
    getUsage().then((r) => {
      if (cancelled) return;
      if (r.ok) setUsage({ used: r.usage.used, limit: r.usage.limit });
    });
    return () => { cancelled = true; };
  }, [chatMode, repo, responding]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Bumped whenever the conversation is reset or a new user message is added,
  // so a history fetch that resolves late can never clobber newer content.
  const conversationSeq = useRef(0);

  // The transcript belongs to whichever repo is selected: switching to another
  // repository (from the sidebar, or a new upload on /upload-repo) starts a new
  // thread for it instead of asking about two codebases in one conversation.
  const activeRepoSlug = useRef(repo?.slug ?? null);
  useEffect(() => {
    const slug = repo?.slug ?? null;
    if (slug !== activeRepoSlug.current) {
      activeRepoSlug.current = slug;
      conversationSeq.current += 1;
      // Clear while the repo's history loads — showing the previous repo's
      // transcript between the switch and the fetch would misattribute it.
      setMessages([]);
    }
  }, [repo]);

  // Opening `/chat` with nothing selected (fresh session, or a cleared stored
  // choice) can still find a repo to open: if the user has any connected repos,
  // select the first and land on its conversation; if they have none there is
  // nothing to ask about yet, so route them to upload one first.
  useEffect(() => {
    if (repo) return;
    let cancelled = false;

    getUserRepos().then((result) => {
      if (cancelled) return;
      if (!result.ok) return; // leave the empty state — the fetch failed, not the user
      if (result.repos.length === 0) {
        router.replace("/upload-repo");
        return;
      }
      const fallback = result.repos[0];
      writeSelectedRepo({
        slug: fallback.repoName,
        url: fallback.url,
        addedAt: fallback.createdAt || new Date().toISOString(),
        id: fallback.id,
        sha: fallback.sha,
        branch: fallback.branch,
        fileCount: fallback.fileCount,
        totalLines: fallback.totalLines,
        description: fallback.description,
      });
    });

    return () => {
      cancelled = true;
    };
  }, [repo, router]);

  // Load the selected repo's saved conversation. The pathname is the trigger:
  // each time the page becomes `/chat` (initial mount, or a return from /keys,
  // /upload-repo, the sidebar, …) the history is re-fetched, and switching
  // repos re-fetches too. A ref tracks the last repo loaded so re-selecting the
  // SAME repo doesn't refetch, but it is invalidated whenever the page leaves
  // /chat — so coming back always loads, even with the identical repo selected.
  const isChatPage = pathname === "/chat";
  const lastLoadedRepo = useRef("");
  useEffect(() => {
    if (!isChatPage) {
      lastLoadedRepo.current = "";
      return;
    }
    const id = repo?.id;
    if (!id || id === lastLoadedRepo.current) return;
    lastLoadedRepo.current = id;

    const seq = conversationSeq.current;
    let cancelled = false;
    setHistoryLoading(true);
    setHistoryError("");

    fetchMessages(id).then((result) => {
      console.log("Result ->", result);
      if (cancelled || seq !== conversationSeq.current) return;
      setHistoryLoading(false);

      if (!result.ok) {
        setHistoryError(result.error);
        setMessages([]);
        return;
      }
      console.log(result.messages.length > 0);
      if (result.messages.length > 0) {
        console.log("check 1");
        setMessages(
          result.messages.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            sources: m.sources.length > 0 ? m.sources : undefined,
          })),
        );
        console.log("check 2");
      } else {
        setMessages([]);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [repo, isChatPage]);

  // Pin to the newest message. Smooth scrolling would fight the streaming
  // updates, so jump instantly while a reply is coming in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, responding]);

  // Grow the composer with its content, up to a cap.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || responding || !repo) return;

      conversationSeq.current += 1;

      const userMessage: Message = {
        id: nextMessageId("u"),
        role: "user",
        content,
      };
      const replyId = nextMessageId("a");

      setMessages((prev) => [
        ...prev,
        userMessage,
        { id: replyId, role: "assistant", content: "", pending: true },
      ]);
      setInput("");
      setResponding(true);

      const result = await askQuestion(
        content,
        repo.slug,
        // The composer's provider + model drive this request. The backend
        // decrypts the caller's stored key for the selected provider and builds
        // the RLM around the chosen model.
        chatMode === "free" ? { model } : { provider, model },
      );

      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== replyId) return m;

          if (!result.ok) {
            return { ...m, pending: false, error: result.error };
          }

          return {
            ...m,
            pending: false,
            content: result.query.answer,
            sources: result.query.evidencePaths,
            error: result.query.error ?? undefined,
          };
        }),
      );

      setResponding(false);

      // Best-effort persistence: saved so the conversation survives the session,
      // but a database hiccup must never disturb the chat itself.
      void persistConversation(repo, content, result);
    },
    [repo, responding, provider, model, chatMode],
  );

  // Retry a past question from its message bubble — identical to typing the
  // question again (same backend call, fresh answer). No-op while a reply is
  // already in flight (the guard lives inside `send`).
  const handleRetry = useCallback(
    (content: string) => {
      void send(content);
    },
    [send],
  );

  // Provider/model dropdown change: apply locally, and persist the provider as
  // this tab's active provider so /keys stays in sync.
  function handleSelectionChange(selection: ProviderModelSelection) {
    setProvider(selection.provider);
    setModel(selection.model);
    setActiveProvider({ provider: selection.provider });
  }

  function handleChangeRepo() {
    clearSelectedRepo();
    router.push("/upload-repo");
  }

  return (
    <AppShell repo={repo}>
      <div className="flex min-h-0 flex-1">
        <RepoSidebar />
        <div className="flex min-h-0 flex-1 flex-col">
          <div
            ref={scrollRef}
            className="scroll-slim min-h-0 flex-1 overflow-y-auto"
            aria-live="polite"
          >
            <div className="mx-auto w-full max-w-[820px] px-5 py-8">
              {repo ? (
                <div className="mb-7 flex items-center justify-between gap-3 rounded-xl border border-border-base bg-surface-subtle px-4 py-3">
                  <div className="min-w-0">
                    <p className="text-[11px] font-medium uppercase tracking-[0.07em] text-muted">
                      Connected repository
                    </p>
                    <a
                      href={repo.url}
                      target="_blank"
                      rel="noreferrer"
                      className="block truncate font-mono text-[13.5px] text-foreground underline-offset-4 hover:underline"
                    >
                      {repo.slug}
                    </a>
                    <IndexedFacts repo={repo} />
                  </div>
                  <Button type="button" size="sm" variant="secondary" onClick={handleChangeRepo}>
                    Change
                  </Button>
                </div>
              ) : (
                <div className="mb-7 flex items-center justify-between gap-3 rounded-xl border border-dashed border-border-strong bg-surface-subtle px-4 py-3">
                  <p className="text-[13px] text-muted">
                    No repository connected yet — pick one to get grounded answers.
                  </p>
                  <a
                    href="/upload-repo"
                    className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-foreground transition-[filter] hover:brightness-110"
                  >
                    Connect repo
                  </a>
                </div>
              )}

              {repo && !activeProvider ? (
                <div className="mb-5 flex items-center justify-between gap-3 rounded-xl border border-border-base bg-surface-subtle px-4 py-3">
                  <p className="flex items-center gap-2 text-[12.5px] leading-relaxed text-muted">
                    <KeyIcon className="h-4 w-4 shrink-0" />
                    Questions are answered with a provider key you save on /keys.
                    Add yours to pick the model in the composer below.
                  </p>
                  <Link
                    href="/keys"
                    className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-foreground transition-[filter] hover:brightness-110"
                  >
                    Add key
                  </Link>
                </div>
              ) : null}

              {repo?.id && historyLoading ? (
                <p className="mb-5 flex items-center gap-1.5 text-[12.5px] text-muted">
                  <SpinnerIcon className="h-3.5 w-3.5" />
                  Loading saved conversation…
                </p>
              ) : null}

              {repo && historyError ? (
                <p className="mb-5 rounded-lg border border-border-base bg-surface-subtle px-3 py-2 text-[12.5px] leading-relaxed text-muted">
                  Couldn&apos;t load the saved conversation: {historyError}
                </p>
              ) : null}

              <div className="space-y-7">
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} onRetry={repo ? handleRetry : undefined} />
                ))}
              </div>

              {repo && !responding && !historyLoading && messages.length <= 2 ? (
                <div className="mt-9">
                  <p className="mb-3 text-[11px] font-medium uppercase tracking-[0.07em] text-muted">
                    Try asking
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => send(s)}
                        className="rounded-full border border-border-base bg-surface px-3.5 py-1.5 text-[13px] text-muted transition-[background-color,border-color,color] hover:border-border-strong hover:bg-surface-subtle hover:text-foreground"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </div>

          <div className="border-t border-border-base bg-background/85 backdrop-blur-md">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
              className="mx-auto w-full max-w-[820px] px-5 py-4"
            >
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-1">
                <ProviderModelSelector
                  value={{ provider, model }}
                  onChange={handleSelectionChange}
                  disabled={responding || !repo}
                  lockedProvider={chatMode === "free" ? provider : undefined}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex items-center gap-1.5 rounded-xl border border-border-base bg-surface px-2.5 py-1.5">
                    <button
                      type="button"
                      onClick={() => setChatMode("free")}
                      className={`rounded-lg px-2 py-0.5 text-[12px] ${chatMode === "free" ? "bg-accent text-accent-foreground" : "text-muted hover:text-foreground"}`}
                    >
                      Free
                    </button>
                    <button
                      type="button"
                      onClick={() => setChatMode("byok")}
                      className={`rounded-lg px-2 py-0.5 text-[12px] ${chatMode === "byok" ? "bg-accent text-accent-foreground" : "text-muted hover:text-foreground"}`}
                    >
                      Your API key
                    </button>
                  </div>
                  {chatMode === "free" && usage ? (
                    <div className="rounded-xl border border-border-base bg-surface px-2.5 py-1.5 text-[12px] text-muted">
                      {Math.round(usage.used / 1000)}K/{Math.round(usage.limit / 1000)}K used
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="flex items-end gap-2.5 rounded-2xl border border-border-base bg-surface p-2 shadow-[0_1px_2px_rgba(0,0,0,0.04)] focus-within:border-accent focus-within:ring-4 focus-within:ring-accent/12">
                <textarea
                  ref={textareaRef}
                  rows={1}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter sends; Shift+Enter inserts a newline.
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send(input);
                    }
                  }}
                  placeholder={
                    repo
                      ? `Ask anything about ${repo.slug}…`
                      : "Connect a repository to start asking…"
                  }
                  disabled={responding || !repo}
                  className="scroll-slim max-h-[200px] min-h-[38px] flex-1 resize-none bg-transparent px-2.5 py-2 text-[14px] leading-relaxed text-foreground outline-none placeholder:text-muted/70 disabled:opacity-60"
                />
                <Button
                  type="submit"
                  size="md"
                  disabled={responding || !repo || !input.trim()}
                  className="h-[38px] shrink-0 rounded-xl px-4"
                  aria-label={responding ? "Working on your question…" : "Send message"}
                >
                  {responding ? "Working…" : "Send"}
                </Button>
              </div>
              <p className="mt-2 px-1 text-[11.5px] text-muted">
                {repo
                  ? `Enter to send · Shift+Enter for a new line. Answers cite the files they used.`
                  : "Connect a repository on /upload-repo, then this chat reads only its code."}
              </p>
            </form>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

// Memoized: typing in the input re-renders ChatView, but message objects are
// stable — without this, every keystroke re-parses ALL markdown/highlighting.
const MessageBubble = memo(function MessageBubble({
  message,
  onRetry,
}: {
  message: Message;
  onRetry?: (content: string) => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard blocked — not worth interrupting the user over.
    }
  }

  if (message.role === "user") {
    return (
      <div className="group relative flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[14px] leading-relaxed text-accent-foreground whitespace-pre-wrap">
          {message.content}
        </div>
        {onRetry ? (
          <button
            type="button"
            onClick={() => onRetry(message.content)}
            aria-label="Ask this question again"
            title="Ask again"
            className="absolute -bottom-3.5 right-1 flex h-6 w-6 items-center justify-center rounded-full border border-border-base bg-background text-muted opacity-0 shadow-sm transition-[opacity,background-color,color] hover:bg-surface-subtle hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
          >
            <ReloadIcon className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="group">
      <div className="mb-2 flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-md bg-accent-subtle text-[10px] font-bold text-accent">
          AI
        </span>
        <span className="text-[12px] font-medium text-muted">Assistant</span>
        {message.pending ? (
          <span className="flex items-center gap-1.5 text-[11.5px] text-muted">
            <SpinnerIcon className="h-3 w-3" />
            thinking
          </span>
        ) : null}
      </div>

      <div className="pl-7">
        {message.pending && !message.content ? (
          <span className="inline-block h-4 w-[2px] animate-pulse bg-accent align-text-bottom" />
        ) : (
          <Suspense
            fallback={
              <p className="text-[13.5px] leading-relaxed text-foreground whitespace-pre-wrap">
                {message.content}
              </p>
            }
          >
            <MarkdownContent content={message.content} />
          </Suspense>
        )}

        {message.error ? (
               <p className="mt-3 rounded-lg border border-border-base bg-surface-subtle px-3 py-2 text-[12.5px] leading-relaxed text-muted">
                 {message.content ? "Note: " : ""}
                 {message.error}
                 {/(No API key saved|exhausted|free tier exhausted|Add your own key)/i.test(message.error) ? (
                   <Link href="/keys" className="ml-1 font-medium text-accent hover:underline">
                     Add a key
                   </Link>
                 ) : null}
               </p>
        ) : null}

        {message.sources?.length ? (
          <div className="mt-3.5">
            <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.07em] text-muted">
              Sources
            </p>
            <div className="flex flex-col gap-1">
              {message.sources.map((src) => (
                <div
                  key={src}
                  className="flex items-center gap-2 rounded-lg border border-border-base bg-surface-subtle px-2.5 py-1.5"
                >
                  <FileIcon className="h-3.5 w-3.5 shrink-0 text-muted" />
                  <span className="truncate font-mono text-[12px] text-muted">
                    {src}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {!message.pending && message.content ? (
          <button
            type="button"
            onClick={copy}
            className="mt-2.5 inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] text-muted opacity-0 transition-opacity hover:bg-surface-subtle group-hover:opacity-100 focus-visible:opacity-100"
          >
            {copied ? (
              <>
                <CheckIcon className="h-3.5 w-3.5" />
                Copied
              </>
            ) : (
              <>
                <CopyIcon className="h-3.5 w-3.5" />
                Copy
              </>
            )}
          </button>
        ) : null}
      </div>
    </div>
  );
});

/** Real index metadata for a connected repo; absent for a hand-typed URL. */
function IndexedFacts({ repo }: { repo: NonNullable<SelectedRepo> }) {
  if (!repo.sha) return null;

  return (
    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px] text-muted">
      {repo.branch ? <span className="font-mono">{repo.branch}</span> : null}
      {repo.sha ? <span className="font-mono">@ {repo.sha.slice(0, 7)}</span> : null}
      {repo.fileCount !== undefined ? (
        <span>{repo.fileCount.toLocaleString()} files</span>
      ) : null}
      {repo.totalLines !== undefined ? (
        <span>{repo.totalLines.toLocaleString()} lines</span>
      ) : null}
    </p>
  );
}
