"use client";

import { Children, type ReactElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { MermaidDiagram } from "@/components/mermaid-diagram";

/**
 * Renders an assistant answer as proper Markdown: GitHub-flavoured tables and
 * lists, syntax-highlighted fenced code blocks, and live mermaid diagrams.
 *
 * The model is instructed to answer in Markdown and to emit standalone code in
 * ```<language> blocks and flowcharts as ```mermaid blocks (see
 * `SYSTEM_PROMPT_CODEBASE` in backend/rlm_config.py). This component is what
 * turns that output into the formatted chat bubble.
 */

const CODE_LANG = /language-([A-Za-z0-9_+-]+)/;

function codeLanguage(className: string | undefined): string | undefined {
  const match = CODE_LANG.exec(className ?? "");
  return match ? match[1].toLowerCase() : undefined;
}

export function MarkdownContent({ content, className = "" }: { content: string; className?: string }) {
  return (
    <div className={`markdown-body ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          // Fenced `` ```mermaid `` blocks become a live diagram. The check
          // happens here, in `pre`, so a diagram is never double-wrapped in a
          // styled <pre> as well. The `code` element is left to react-markdown's
          // default (which passes the hljs/language classes through for the
          // syntax theme in globals.css).
          pre({ children }) {
            const block = Children.only(children) as ReactElement<{
              className?: string;
              children?: ReactNode;
            }>;

            if (codeLanguage(block.props?.className) === "mermaid") {
              const source = String(block.props?.children ?? "").replace(/\n$/, "");
              return <MermaidDiagram code={source} />;
            }

            return (
              <pre className="scroll-slim my-3 overflow-x-auto rounded-xl border border-border-base bg-surface-subtle p-4 text-[13px] leading-relaxed">
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