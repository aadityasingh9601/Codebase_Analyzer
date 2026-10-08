"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { cleanDiagramSource } from "@/lib/mermaid-clean";

/**
 * Renders one ```mermaid fenced block into a real, interactive diagram.
 *
 * Mermaid is ~400KB and needs the browser DOM, so none of it ships until a
 * diagram actually exists: this component is mounted via `next/dynamic` with
 * `ssr: false` (see markdown-content), and mermaid itself is `import()`-ed
 * lazily on first render here. A page that never contains a diagram never
 * pays for mermaid.
 *
 * Diagrams render at their natural size inside a scrollable pane — nothing is
 * shrunk to fit or cut off — and a tiny +/−/reset control scales them. The
 * LLM's source goes through a cleanup pipeline before it is drawn: fences,
 * accidental box-drawing characters (│ ─ …), leading pipes, HTML comments and
 * stray whitespace are stripped, and unquoted node labels containing mermaid
 * syntax characters are re-quoted (e.g. `I[router.push('/')]` →
 * `I["router.push('/')"]`). The cleaned source is validated with
 * `mermaid.parse()` before `render()`; a diagram that still fails falls back
 * to a code block of the source with the parser error instead of breaking the
 * message.
 *
 * The `theme` follows the app's `data-theme` attribute; re-initialising mermaid
 * only happens when the theme actually changes, never per diagram.
 */

const MAX_SCALE = 3;
const MIN_SCALE = 0.5;
const SCALE_STEP = 0.2;

/** Pixels panned per arrow-key press. */
const PAN_STEP = 48;

/** Big enough to stay unique across mounts (React StrictMode re-runs effects). */
let idCounter = 0;

/** Shared, stable options (theme is applied per use — see `getMermaid`). */
const BASE_MERMAID_CONFIG = {
  startOnLoad: false,
  securityLevel: "strict",
  theme: "default" as const,
  // v12 renders flowcharts with the `neo` look + the ELK layout by default.
  // ELK measures labels without a live DOM, so nodes drift far apart and edges
  // float between huge gaps. The classic dagre pipeline measures real nodes in
  // the DOM and packs ranks tightly — a compact, connected flowchart.
  layout: "dagre",
  // Natural sizing: `useMaxWidth: false` makes mermaid emit explicit pixel
  // width/height instead of `width="100%"` with a `max-width` cap, so wide
  // diagrams no longer get shrunk to fit (the "too small" symptom). Combined
  // with `max-width: none` on `.mermaid-wrapper svg`, a diagram renders at
  // full size and the pane scrolls / the zoom buttons scale it.
  flowchart: { useMaxWidth: false, look: "classic" as const, htmlLabels: true },
  sequence: { useMaxWidth: false },
  class: { useMaxWidth: false },
  state: { useMaxWidth: false },
  er: { useMaxWidth: false },
  gantt: { useMaxWidth: false },
  pie: { useMaxWidth: false },
  themeVariables: {
    fontFamily: "ui-sans-serif, system-ui, sans-serif",
    fontSize: "16px",
  },
} satisfies import("mermaid").MermaidConfig;

/** Initialised once per theme; mermaid's config is global, so cache the last one. */
let initialisedTheme: "dark" | "default" | null = null;

async function getMermaid(theme: "dark" | "default") {
  const mermaid = (await import("mermaid")).default;
  if (initialisedTheme !== theme) {
    mermaid.initialize({ ...BASE_MERMAID_CONFIG, theme });
    initialisedTheme = theme;
  }
  return mermaid;
}

export function MermaidDiagram({ code, className = "" }: { code: string; className?: string }) {
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [scale, setScale] = useState(1);
  const [grabbing, setGrabbing] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  // Handed-off drag state (mouse drag pans the pane by adjusting scroll).
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    left: number;
    top: number;
  } | null>(null);
  // Unique per mounted block: mermaid.render() addresses the svg by id.
  // Generated inside the effect (impure — never during render).
  const renderId = useRef("");

  function panBy(dx: number, dy: number) {
    const el = wrapperRef.current;
    if (!el) return;
    el.scrollLeft += dx;
    el.scrollTop += dy;
  }

  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    // Mouse drag pans the pane (touch keeps native scrolling).
    if (e.pointerType !== "mouse") return;
    const el = wrapperRef.current;
    if (!el) return;
    dragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      left: el.scrollLeft,
      top: el.scrollTop,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
    setGrabbing(true);
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    const d = dragRef.current;
    const el = wrapperRef.current;
    if (!d || !el) return;
    el.scrollLeft = d.left - (e.clientX - d.startX);
    el.scrollTop = d.top - (e.clientY - d.startY);
  }

  function endDrag(e: PointerEvent<HTMLDivElement>) {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    dragRef.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    setGrabbing(false);
  }

  function onWrapperKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    switch (e.key) {
      case "ArrowLeft":
        panBy(-PAN_STEP, 0);
        e.preventDefault();
        break;
      case "ArrowRight":
        panBy(PAN_STEP, 0);
        e.preventDefault();
        break;
      case "ArrowUp":
        panBy(0, -PAN_STEP);
        e.preventDefault();
        break;
      case "ArrowDown":
        panBy(0, PAN_STEP);
        e.preventDefault();
        break;
      default:
        break;
    }
  }

  function resetZoom() {
    setScale(1);
    wrapperRef.current?.scrollTo({ left: 0, top: 0 });
  }

  useEffect(() => {
    let cancelled = false;
    if (!renderId.current) {
      renderId.current = `mermaid-${(++idCounter).toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }

    (async () => {
      const container = containerRef.current;
      if (!container) return;

      const theme =
        document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "default";

      try {
        // Scoped render: the container is the 3rd arg of `mermaid.render()`.
        // WITHOUT it, mermaid v12 appends the finished, full-size svg to
        // `<body>` (it would glitch large in the top-left corner of the page).
        container.innerHTML = "";
        const mermaid = await getMermaid(theme);

        // Pipeline: AI mermaid → sanitize/normalize → validate → render.
        const source = cleanDiagramSource(code);
        // `parse()` validates before we pay for a full render. With
        // `suppressErrors` it returns false on bad syntax instead of throwing,
        // and then a second parse (without the flag) surfaces the parser's
        // detail message for the fallback display.
        const valid = await mermaid.parse(source, { suppressErrors: true });
        if (!valid) {
          let detail = "";
          try {
            await mermaid.parse(source);
          } catch (e) {
            detail = e instanceof Error ? e.message : String(e);
          }
          throw new Error(`Invalid mermaid syntax${detail ? `: ${detail}` : ""}.`);
        }

        const rendered = await mermaid.render(renderId.current, source, container);
        if (cancelled) return;
        container.innerHTML = rendered.svg;
        setSvg(rendered.svg);
        setScale(1);
        // Fresh diagram: start the pane back at the top-left corner.
        wrapperRef.current?.scrollTo({ left: 0, top: 0 });
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code]);

  if (error) {
    return (
      <div className="my-3 overflow-hidden rounded-xl border border-border-base bg-surface-subtle">
        <p className="border-b border-border-base px-3 py-2 text-[11.5px] text-muted">
          Could not render the diagram (Mermaid error):
        </p>
        <p className="border-b border-border-base px-3 py-2 font-mono text-[11px] leading-relaxed text-muted">
          {error}
        </p>
        <p className="border-b border-border-base px-3 py-2 text-[11.5px] text-muted">Source:</p>
        <pre className="scroll-slim m-0 max-h-72 overflow-auto whitespace-pre-wrap p-3 font-mono text-[12px] leading-relaxed text-muted">
          <code>{code}</code>
        </pre>
      </div>
    );
  }

  return (
    <div className={`mermaid-wrapper ${className}`}>
      {svg ? (
        <div className="flex items-center gap-1.5 pb-2">
          <button
            type="button"
            onClick={() => setScale((s) => Math.min(s + SCALE_STEP, MAX_SCALE))}
            aria-label="Zoom in"
            className="flex h-7 w-7 items-center justify-center rounded-md border border-border-base bg-surface text-[13px] font-medium text-muted transition-[border-color,color] hover:border-border-strong hover:text-foreground"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => setScale((s) => Math.max(s - SCALE_STEP, MIN_SCALE))}
            aria-label="Zoom out"
            className="flex h-7 w-7 items-center justify-center rounded-md border border-border-base bg-surface text-[13px] font-medium text-muted transition-[border-color,color] hover:border-border-strong hover:text-foreground"
          >
            −
          </button>
          <button
            type="button"
            onClick={resetZoom}
            className="h-7 rounded-md border border-border-base bg-surface px-2 text-[12px] font-medium text-muted transition-[border-color,color] hover:border-border-strong hover:text-foreground"
          >
            Reset
          </button>
          {Math.round(scale * 100) !== 100 ? (
            <button
              type="button"
              onClick={() => setScale(1)}
              className="px-1.5 text-[11px] text-accent underline underline-offset-2"
            >
              {Math.round(scale * 100)}%
            </button>
          ) : null}
          <span className="ml-auto hidden text-[11px] text-muted sm:inline">
            Drag or arrow keys to pan
          </span>
        </div>
      ) : null}

      {!svg && !error ? (
        <div className="my-0.5 h-28 animate-pulse rounded-lg border border-border-base bg-surface-subtle" />
      ) : null}

      {/* Focusable, scrollable pane: mouse-drag and arrow keys pan it, and it
          has a bounded height so long diagrams scroll INSIDE the pane instead
          of stretching the chat message. */}
      <div
        ref={wrapperRef}
        tabIndex={0}
        role="region"
        aria-label="Mermaid diagram — drag or use arrow keys to pan"
        onKeyDown={onWrapperKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className={`mermaid-wrapper ${grabbing ? "grabbing" : ""} ${className}`}
      >
        {/* `zoom` (not transform) so the scroll area grows with the diagram and
            the whole thing stays reachable at any zoom level. */}
        <div className="mermaid-scaler" style={{ zoom: scale }}>
          <div ref={containerRef} />
        </div>
      </div>
    </div>
  );
}