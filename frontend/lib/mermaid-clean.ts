/**
 * Mermaid source cleaning utilities.
 *
 * LLM answers frequently wrap diagrams in `` ```mermaid `` fences, leave
 * box-drawing characters (`│ ─ ┌ …`) behind from a copied rendering, or emit
 * unquoted node labels that collide with mermaid syntax. None of that renders
 * (or parses) — so before showing/copying the source it is sanitized here.
 *
 * This module deliberately has NO dependency on the `mermaid` library or on
 * mermaid-diagram.tsx — it is cheap to import from anywhere in the app.
 */

/**
 * Fix common LLM output problems before mermaid tries to parse the source.
 * Anything inside a double-quoted label is preserved verbatim. Node labels are
 * then normalized: an unquoted label holding characters that are mermaid
 * syntax itself (`(`, `)`, `[`, `]`, `{`, `}`, `<`, `>`, `/`) is re-quoted,
 * e.g. `I[router.push('/')]` → `I["router.push('/')"]`.
 */
function stripFences(src: string): string {
  const isFence = (line: string) => /^```[A-Za-z0-9_-]*\s*$/.test(line.trim());
  const lines = src.split("\n");
  while (lines.length && isFence(lines[0])) lines.shift();
  while (lines.length && isFence(lines[lines.length - 1])) lines.pop();
  return lines.join("\n");
}

function stripLayoutArtifacts(src: string): string {
  const cleanLine = (line: string): string => {
    let out = "";
    let inQuote = false;
    let seenContent = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        inQuote = !inQuote;
        out += ch;
        seenContent = true;
        continue;
      }
      if (!inQuote) {
        if (/\s/.test(ch)) {
          out += ch;
          continue;
        }
        // A `|` at the start of a line is never valid mermaid (pipes only mean
        // something inline, like `A -->|label| B`) — drop it as an artifact.
        if (!seenContent && ch === "|") continue;
        // Box-drawing block (U+2500–U+257F): `│`, `─`, `┌`, … from a copied
        // diagram. Outside quoted labels they are always junk.
        const code = ch.codePointAt(0) ?? 0;
        if (code >= 0x2500 && code <= 0x257f) continue;
        seenContent = true;
      }
      out += ch;
    }
    // Unbalanced quote — likely a multi-line label; keep the line verbatim.
    return inQuote ? line : out.trim();
  };

  return src
    .split("\n")
    .map(cleanLine)
    .filter((line) => line.length > 0)
    .join("\n")
    .trim();
}

/** Characters that are mermaid syntax themselves and break unquoted labels. */
const PROBLEMATIC_LABEL_CHARS = '(){}[]<>/"';

function hasProblematicChar(text: string): boolean {
  for (const c of PROBLEMATIC_LABEL_CHARS) {
    if (text.includes(c)) return true;
  }
  return false;
}

/**
 * Find the character index where a `[`…`]` / `{`…`}` shell closes, counting
 * nested same-bracket pairs (`arr[0] + arr[1]` inside a label) and skipping
 * quoted spans, which may contain brackets of their own.
 */
function findShellClose(line: string, openChar: "[" | "{", from: number): number {
  const closeChar = openChar === "[" ? "]" : "}";
  let depth = 1;
  let i = from;
  const n = line.length;
  while (i < n) {
    const c = line[i];
    if (c === '"' || c === "`") {
      i++;
      while (i < n && line[i] !== c) {
        if (line[i] === "\\") i++;
        i++;
      }
      if (i < n) i++;
      continue;
    }
    if (c === openChar) depth++;
    else if (c === closeChar) {
      depth--;
      if (depth === 0) return i;
    }
    i++;
  }
  return -1;
}

function maybeQuoteLabel(content: string): string {
  const trimmed = content.trim();
  if (!trimmed) return content;
  // Already quoted, or a `` `markdown` `` label — leave untouched.
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("`") && trimmed.endsWith("`"))
  ) {
    return content;
  }
  if (!hasProblematicChar(trimmed)) return content;
  // `#` starts mermaid's character codes and `"` would close the quote early —
  // encode them so the label round-trips, then quote the whole label.
  const encoded = trimmed.replace(/#/g, "#35;").replace(/"/g, "#quot;");
  return `"${encoded}"`;
}

/**
 * Re-quote unquoted node labels that contain problem characters. Only brackets
 * that directly follow a node id are treated as label shells; edge-link text
 * (`-- text -->`, `-->|text|`) is left alone, and quoted / markdown labels are
 * never touched.
 */
function normalizeNodeLabels(src: string): string {
  const normalizeLine = (line: string): string => {
    let out = "";
    let i = 0;
    const n = line.length;
    let prevNode = false;
    while (i < n) {
      const ch = line[i];
      // Copy quoted spans (labels) through untouched.
      if (ch === '"' || ch === "`") {
        out += ch;
        i++;
        while (i < n && line[i] !== ch) {
          if (line[i] === "\\") {
            out += line[i];
            i++;
            if (i < n) {
              out += line[i];
              i++;
            }
            continue;
          }
          out += line[i];
          i++;
        }
        if (i < n) {
          out += line[i];
          i++;
        }
        prevNode = false;
        continue;
      }
      // Node ids (alphanumeric, `-`, `_`).
      if (/[A-Za-z0-9_-]/.test(ch)) {
        let j = i;
        while (j < n && /[A-Za-z0-9_-]/.test(line[j])) j++;
        out += line.slice(i, j);
        prevNode = true;
        i = j;
        continue;
      }
      if (ch === "[") {
        const next = line[i + 1];
        let contentStart = i + 1;
        let close: string;
        let closeIdx: number;
        if (next === "[") {
          close = "]]";
          contentStart = i + 2;
          closeIdx = line.indexOf(close, contentStart);
        } else if (next === "(") {
          close = ")]";
          contentStart = i + 2;
          closeIdx = line.indexOf(close, contentStart);
        } else if (next === "/") {
          close = "/]";
          contentStart = i + 2;
          closeIdx = line.indexOf(close, contentStart);
        } else if (next === "\\") {
          close = "\\]";
          contentStart = i + 2;
          closeIdx = line.indexOf(close, contentStart);
        } else {
          close = "]";
          closeIdx = findShellClose(line, "[", i + 1);
        }
        if (prevNode && closeIdx !== -1) {
          out += line.slice(i, contentStart);
          out += maybeQuoteLabel(line.slice(contentStart, closeIdx));
          out += close;
          i = closeIdx + close.length;
          prevNode = false;
          continue;
        }
        out += ch;
        i++;
        prevNode = false;
        continue;
      }
      if (ch === "{") {
        if (prevNode) {
          const closeIdx = findShellClose(line, "{", i + 1);
          if (closeIdx !== -1) {
            out += "{";
            out += maybeQuoteLabel(line.slice(i + 1, closeIdx));
            out += "}";
            i = closeIdx + 1;
            prevNode = false;
            continue;
          }
        }
        out += ch;
        i++;
        prevNode = false;
        continue;
      }
      // `-->|label|` edge labels: copy through so pipes/braces in them are kept.
      if (ch === "|" && !prevNode) {
        const closeIdx = line.indexOf("|", i + 1);
        if (closeIdx !== -1) {
          out += line.slice(i, closeIdx + 1);
          i = closeIdx + 1;
          prevNode = false;
          continue;
        }
      }
      out += ch;
      i++;
      prevNode = false;
    }
    return out;
  };

  return src.split("\n").map(normalizeLine).join("\n");
}

/**
 * Sanitize diagram source so it is parseable by mermaid (and safe to copy into
 * mermaid.live): strip fences, layout artifacts, HTML comments and CRLF, then
 * normalize node labels, then escape bare `&` last (so it also covers labels
 * that normalization just quoted).
 */
export function cleanDiagramSource(src: string): string {
  const cleaned = stripLayoutArtifacts(
    normalizeNodeLabels(
      stripFences(
        src
          .trim()
          .replace(/\r\n?/g, "\n")
          // LLMs occasionally wrap labels in HTML comments; mermaid chokes on them.
          .replace(/<!--[\s\S]*?-->/g, ""),
      ),
    ),
  );
  // Escape bare `&` (leave real entities like &amp;, &lt;, … untouched) last so
  // it also covers the labels that normalization just quoted.
  return cleaned.replace(/&(?!#?[A-Za-z0-9]+;)/g, "&amp;");
}