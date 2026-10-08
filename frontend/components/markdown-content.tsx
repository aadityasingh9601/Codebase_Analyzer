"use client";

import { Children, useState, type ReactElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { CheckIcon, CopyIcon } from "@/components/icons";
import { cleanDiagramSource } from "@/lib/mermaid-clean";

/**
 * Renders an assistant answer as proper Markdown: GitHub-flavoured tables and
 * lists, syntax-highlighted fenced code blocks.
 *
 * ```mermaid blocks are deliberately NOT live-rendered anymore — mermaid is
 * heavy in the browser and sizing/positioning AI output was fragile. Instead
 * the cleaned source (sanitized so Mermaid itself accepts it) is shown in a
 * decorated code block with a copy button, plus a pointer to mermaid.live so
 * the user can view the full flowchart there. The MermaidDiagram component is
 * kept in the repo (see mermaid-diagram.tsx) for possible future reuse.
 */

const CODE_LANG = /language-([A-Za-z0-9_+-]+)/;

function codeLanguage(className: string | undefined): string | undefined {
  const match = CODE_LANG.exec(className ?? "");
  return match ? match[1].toLowerCase() : undefined;
}

/**
 * Decorated code block that shows (sanitized) mermaid source instead of
 * rendering the live diagram. Users can copy it straight into mermaid.live.
 */
function MermaidCodeBlock({ source }: { source: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(source);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard blocked — not worth interrupting the user over.
    }
  }

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border-base bg-surface-subtle">
      <div className="flex items-center justify-between gap-2 border-b border-border-base py-1.5 pl-4 pr-2">
        <span className="font-mono text-[11px] tracking-wide text-muted uppercase">mermaid</span>
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] text-muted transition-[color,background-color] hover:bg-surface hover:text-foreground"
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
      </div>
      <pre className="scroll-slim max-h-[880px] overflow-auto p-4 font-mono text-[13px] leading-relaxed text-foreground">
        <code>{source}</code>
      </pre>
      <p className="border-t border-border-base px-4 py-2 text-[11.5px] text-muted">
        Paste it into{" "}
        <a
          href="https://mermaid.live"
          target="_blank"
          rel="noreferrer"
          className="font-medium text-accent hover:underline"
        >
          mermaid.live
        </a>{" "}
        to see the full flowchart.
      </p>
    </div>
  );
}

export function MarkdownContent({ content, className = "" }: { content: string; className?: string }) {
  return (
    <div className={`markdown-body ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          // Fenced `` ```mermaid `` blocks become a decorated code block with
          // the cleaned source (never a live diagram — see comment at top).
          pre({ children }) {
            const block = Children.only(children) as ReactElement<{
              className?: string;
              children?: ReactNode;
            }>;

            if (codeLanguage(block.props?.className) === "mermaid") {
              const source = String(block.props?.children ?? "")
                .replace(/\n$/, "")
                .trim();
              return <MermaidCodeBlock source={cleanDiagramSource(source)} />;
            }

            return (
              <pre className="scroll-slim my-3 max-h-[880px] overflow-auto rounded-xl border border-border-base bg-surface-subtle p-4 text-[13px] leading-relaxed">
                {children}
              </pre>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}