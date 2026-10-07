"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Renders one ```mermaid fenced block from an assistant answer into a real
 * diagram, the way GitHub renders README diagrams: scaled to fit the container
 * width (never clipped), centered, whole diagram always visible.
 *
 * Mermaid is browser-only and heavy, so it is imported lazily inside the
 * effect (never during SSR) and only when a mermaid block actually exists.
 * The theme follows the app's `data-theme` attribute. A failed diagram never
 * kills the note: it falls back to the raw mermaid source in a code block.
 */
export function MermaidDiagram({ code, className = "" }: { code: string; className?: string }) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState("");

  // The `<div>` mermaid renders into. It is the 3rd arg of `mermaid.render()`:
  // WITHOUT it, mermaid v12 appends the fully-sized svg to `document.body`
  // (which the app's fixed full-viewport shell leaves empty — so the diagram
  // would glitch at a large size in the top-left corner of the screen).
  // Passing the container keeps the render scoped to our bubble.
  //
  // IMPORTANT: the container stays *empty* from React's point of view. Mermaid
  // clears and writes its svg into it imperatively, so if React owned a child
  // there the two would fight (React would keep writing to a detached node).
  const containerRef = useRef<HTMLDivElement>(null);

  // Unique per mounted block: mermaid.render() addresses the svg by id.
  // Generated inside the effect (impure on purpose — never during render).
  const renderId = useRef("");

  useEffect(() => {
    if (!renderId.current) {
      renderId.current = `mermaid-${Math.random().toString(36).slice(2, 10)}`;
    }
    let cancelled = false;
    const dark =
      typeof document !== "undefined" &&
      document.documentElement.getAttribute("data-theme") === "dark";

    (async () => {
      // Reset on re-run (only happens when the block text changes). Kept
      // inside the async runner so the effect body itself never syncs state.
      setReady(false);
      setFailed("");
      try {
        const container = containerRef.current;
        if (!container) throw new Error("diagram container not mounted");
        container.innerHTML = "";

        const { default: mermaid } = await import("mermaid");
        await mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          // `useMaxWidth` makes the svg scale down to its container (with a
          // viewBox to keep proportions) instead of overflowing it — this is
          // how GitHub README diagrams fit the page. Combined with the
          // `.markdown-body svg` rule, wide flowcharts are fully visible.
          // (Set per-diagram: the option lives in each diagram's config block.)
          flowchart: { useMaxWidth: true },
          sequence: { useMaxWidth: true },
          class: { useMaxWidth: true },
          theme: dark ? "dark" : "default",
          themeVariables: {
            fontFamily: "ui-sans-serif, system-ui, sans-serif",
            fontSize: "16px",
          },
        });
        const { svg } = await mermaid.render(renderId.current, code, container);
        if (cancelled) return;
        container.innerHTML = svg;
        /**
         * Size of the svg may exceed the container while mermaid fits the
         * whole diagram into it via the viewBox; overflow-x-auto on the
         * wrapper then lets extreme cases scroll instead of clipping.
         */
        setReady(true);
      } catch (e) {
        if (!cancelled) {
          setFailed(
            e instanceof Error && e.message ? e.message : "could not render the diagram",
          );
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code]);

  if (failed) {
    return (
      <div className="my-3 overflow-hidden rounded-xl border border-border-base bg-surface-subtle">
        <p className="border-b border-border-base px-3 py-2 text-[11.5px] text-muted">
          Could not render the diagram ({failed}). Source:
        </p>
        <pre className="scroll-slim max-h-72 overflow-auto whitespace-pre-wrap p-3 font-mono text-[12px] leading-relaxed text-muted">
          {code}
        </pre>
      </div>
    );
  }

  return (
    <>
      {!ready && (
        <div className="my-3 h-28 animate-pulse rounded-xl border border-border-base bg-surface-subtle" />
      )}
      {/* Plain GitHub-style wrapper: no card/chrome around the diagram. The svg
          is sized and centred by the `.markdown-body svg` rule (max-width:
          100%, margin-inline: auto) and this wrapper only guards overflow. */}
      <div ref={containerRef} className={`my-3 overflow-x-auto ${className}`} />
    </>
  );
}