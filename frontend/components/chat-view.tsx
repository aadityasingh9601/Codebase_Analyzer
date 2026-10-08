"use client";

import { memo, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { RepoSidebar } from "@/components/repo-sidebar";
import { MarkdownContent } from "@/components/markdown-content";
import { CopyIcon, CheckIcon, SendIcon, SpinnerIcon, FileIcon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { useSelectedRepo } from "@/hooks/use-selected-repo";
import { clearSelectedRepo, writeSelectedRepo, type SelectedRepo } from "@/lib/repo";
import { askQuestion } from "@/app/lib/actions/askQuestion";
import { getUserRepos } from "@/app/lib/actions/getUserRepos";
import {
  fetchMessages,
  saveMessage,
} from "@/app/lib/actions/messages";

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
 * Preview transcript, shown only when nothing is connected. It exists so the
 * interface can be judged on its own; once a repository is actually indexed it
 * is replaced, because attributing these answers — and their source paths — to
 * someone else's code would be a lie.
 */
const DEMO_MESSAGES: Message[] = [
  {
    id: "demo-u1",
    role: "user",
    content: "How does booking availability get calculated for a team event?",
  },
  {
    id: "demo-a1",
    role: "assistant",
    content:
      "Availability is computed on the server and returned to the client as a pre-built map of time slots.\n\nThe request path is: the calendar view calls the slots endpoint with the event id and a date range, which delegates to the availability service. That service pulls existing bookings, applies the event's time range, minimum notice, and buffer between meetings, then walks each day in 15-minute increments marking slots as available or taken.\n\nThe interesting part is that buffers are applied per-booking rather than globally, so a 30-minute buffer after one meeting does not eat the gap before the next one.",
    sources: [
      "packages/features/bookings/lib/getAvailability.ts",
      "apps/web/app/api/slots/route.ts",
      "packages/features/bookings/lib/bookingBuffers.ts",
    ],
  },
];

/** Opening message for a real, indexed repository. */
function greeting(repo: NonNullable<SelectedRepo>): Message {
  const facts = [
    repo.fileCount !== undefined ? `${repo.fileCount.toLocaleString()} files` : null,
    repo.totalLines !== undefined ? `${repo.totalLines.toLocaleString()} lines` : null,
    repo.branch && repo.sha ? `${repo.branch} @ ${repo.sha.slice(0, 7)}` : null,
  ].filter(Boolean);

  return {
    id: "greeting",
    role: "assistant",
    content: `Connected to ${repo.slug}${facts.length ? ` (${facts.join(", ")})` : ""}.${
      repo.description ? `\n\n${repo.description}` : ""
    }\n\nAsk me anything about the code and I will answer from the index.`,
  };
}

function initialMessages(repo: SelectedRepo | null): Message[] {
  return repo ? [greeting(repo)] : DEMO_MESSAGES;
}

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
  const repo = useSelectedRepo();
  const [messages, setMessages] = useState<Message[]>(() => initialMessages(repo));
  const [input, setInput] = useState("");
  const [responding, setResponding] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");

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
      // transcript between the switch and the fetch would misattribute it. A
      // repo without a row id has nothing persisted, so show the greeting.
      setMessages(repo?.id ? [] : initialMessages(repo));
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

  // Load the selected repo's saved conversation once. Re-selecting the same
  // repo keeps the already-loaded thread instead of re-fetching.
  const lastLoadedRepoId = useRef<string | undefined>(undefined);
  useEffect(() => {
    const id = repo?.id;
    if (!id || id === lastLoadedRepoId.current) return;
    lastLoadedRepoId.current = id;

    const seq = conversationSeq.current;
    let cancelled = false;
    setHistoryLoading(true);
    setHistoryError("");

    fetchMessages(id).then((result) => {
      if (cancelled || seq !== conversationSeq.current) return;
      setHistoryLoading(false);

      if (!result.ok) {
        setHistoryError(result.error);
        setMessages(initialMessages(repo));
        return;
      }

      if (result.messages.length > 0) {
        setMessages(
          result.messages.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            sources: m.sources.length > 0 ? m.sources : undefined,
          })),
        );
      } else {
        setMessages(initialMessages(repo));
      }
    });

    return () => {
      cancelled = true;
    };
  }, [repo]);

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

  async function send(text: string) {
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

    const result = await askQuestion(content, repo.slug);

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
                  <MessageBubble key={m.id} message={m} />
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
                  className="h-[38px] w-[38px] shrink-0 rounded-xl p-0"
                  aria-label="Send message"
                >
                  {responding ? (
                    <SpinnerIcon className="h-4 w-4" />
                  ) : (
                    <SendIcon className="h-4 w-4" />
                  )}
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
const MessageBubble = memo(function MessageBubble({ message }: { message: Message }) {
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
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[14px] leading-relaxed text-accent-foreground whitespace-pre-wrap">
          {message.content}
        </div>
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
          <MarkdownContent content={message.content} />
        )}

        {message.error ? (
          <p className="mt-3 rounded-lg border border-border-base bg-surface-subtle px-3 py-2 text-[12.5px] leading-relaxed text-muted">
            {message.content ? "Note: " : ""}
            {message.error}
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
