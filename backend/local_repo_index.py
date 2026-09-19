"""
Local filesystem repo reader + indexer for the RLM codebase Q&A flow.

Generates the same lightweight codebase JSON the GitHub fetcher does
(repo_name, description, file_tree, files_metadata) but from a local
directory instead of a remote GitHub repo. No external dependencies —
stdlib only.

Soft guardrails: sensitive / generated / binary paths declared by
`should_skip` are never indexed and never readable via the guarded
readers (`read_local_file` / `search_local_files`). These guarded
readers are the canonical source of truth for the in-REPL helpers in
`codebase_helpers.py`.

Usage (CLI):
    python local_repo_index.py /path/to/repo
    python local_repo_index.py --repo-path /path/to/repo --report
    python local_repo_index.py --repo-path /path/to/repo --repo-name myrepo --description "..."

    (no path arg -> interactive input() prompt when on a TTY)

Programmatic:
    index = build_lightweight_index("/path/to/repo")
    text = read_local_file("/path/to/repo", "src/main.py")
    hits = search_local_files("/path/to/repo", "TODO")

The module is side-effect-free at import time (no writes, no network).
Shared helpers below (should_skip, get_file_language, extract_imports_*,
build_lightweight_context) are the canonical versions imported by
fetch_github.py and update_github_repo.py.
"""

import argparse
import ast
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Canonical tags / skip rules
# ---------------------------------------------------------------------------

# Directories that are never indexed (any path component matching exactly).
SKIP_DIR_COMPONENTS = {
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    ".next",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".terraform",
    ".ssh",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "out",
    "target",
    "vendor",
    "site-packages",
    "node_modules/.cache",
}

# Substring-based skips kept for parity with the legacy GitHub parser
# (e.g. ".env" matches ".env", ".env.local", "foo.env-bar").
SKIP_SUBSTRINGS = (
    ".env",
    ".lock",
)

# Extensions treated as binary / non-source (skipped from indexing).
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp4", ".mp3", ".mov",
    ".webm", ".wav", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".sql",
}

# Extensions that may legitimately carry secrets — never indexed, never readable.
SECRET_EXTENSIONS = {
    ".pem", ".key", ".p12", ".pfx", ".p8", ".jks", ".p7b", ".crt", ".cer",
}

# Other data / build-artifact extensions that add no Q&A value.
DATA_EXTENSIONS = {
    ".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".class", ".o", ".a",
    ".so", ".dll", ".dylib", ".exe", ".bin", ".wasm", ".pdf", ".dill",
}

# Content caps for the guarded readers / indexing.
MAX_TEXT_BYTES = 5_000_000   # files larger than this are indexed as metadata-only
READ_CAP_BYTES = 2_000_000   # read_local_file truncates above this
SEARCH_CAP_BYTES = 2_000_000  # per-file search scan cap
SEARCH_MAX_RESULTS = 200

BLOCKED_PREFIX = "[BLOCKED"


def should_skip(path: str) -> bool:
    """Return True if a repo-relative path should be excluded entirely.

    Component-aware (a component equality against SKIP_DIR_COMPONENTS),
    substring-based (SKIP_SUBSTRINGS, legacy parity), and extension-based
    (SKIP_EXTENSIONS | SECRET_EXTENSIONS | DATA_EXTENSIONS).

    Args:
        path: Repo-relative path with '/' separators (forward or back).
    """
    norm = path.replace("\\", "/")
    components = [c for c in norm.split("/") if c]
    if any(c in SKIP_DIR_COMPONENTS for c in components):
        return True
    if any(sub in path for sub in SKIP_SUBSTRINGS):
        return True
    lower = path.lower()
    if lower.endswith(".sql"):
        return True
    leaf = components[-1].lower() if components else ""
    if leaf.endswith(tuple(SKIP_EXTENSIONS | SECRET_EXTENSIONS | DATA_EXTENSIONS)):
        return True
    return False


def skip_reason(path: str) -> str | None:
    """Return a short human-readable reason why ``path`` is skipped, or None."""
    norm = path.replace("\\", "/")
    components = [c for c in norm.split("/") if c]
    for c in components:
        if c in SKIP_DIR_COMPONENTS:
            return f"skip dir: {c}"
    for sub in SKIP_SUBSTRINGS:
        if sub in path:
            return f"skip substring: {sub}"
    lower = path.lower()
    if lower.endswith(".sql"):
        return "skip extension: .sql"
    leaf = components[-1].lower() if components else ""
    for ext in sorted(SKIP_EXTENSIONS | SECRET_EXTENSIONS | DATA_EXTENSIONS):
        if leaf.endswith(ext):
            return f"skip extension: {ext}"
    return None


# ---------------------------------------------------------------------------
# Language / import extraction (canonical, shared with the GitHub fetcher)
# ---------------------------------------------------------------------------

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".md": "markdown",
    ".mdx": "markdown",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".css": "css",
    ".scss": "scss",
    ".html": "html",
    ".sql": "sql",
    ".sh": "bash",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".tf": "hcl",
    ".toml": "toml",
    ".ini": "ini",
    ".txt": "text",
    ".csv": "text",
    ".env": "dotenv",
    ".dockerfile": "dockerfile",
}


def get_file_language(path: str) -> str:
    """Return the language tag for a file path (default 'text')."""
    lower = path.lower()
    if lower.endswith("dockerfile"):
        return "dockerfile"
    for ext, lang in LANGUAGE_EXTENSIONS.items():
        if lower.endswith(ext):
            return lang
    return "text"


def extract_imports_python(content: str) -> list:
    """Extract top-level import names from Python source (best effort)."""
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def extract_imports_js(content: str) -> list:
    """Extract bare import specifiers from JS/TS source (best effort)."""
    patterns = [
        r"import\s+.*?\s+from\s+['\"](.+?)['\"]",
        r"require\(['\"](.+?)['\"]\)",
    ]
    imports = []
    for pattern in patterns:
        imports.extend(re.findall(pattern, content))
    return imports


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def _read_text(path: str, cap: int = MAX_TEXT_BYTES) -> tuple[str | None, bool]:
    """Read ``path`` as UTF-8 text if possible.

    Returns (content, truncated). Returns (None, False) for binary files.
    Never raises for decode/IO errors.
    """
    try:
        if os.path.getsize(path) > cap:
            with open(path, "rb") as f:
                raw = f.read(cap)
            try:
                return raw.decode("utf-8", errors="strict"), True
            except UnicodeDecodeError:
                return None, False
        with open(path, "rb") as f:
            raw = f.read()
        try:
            return raw.decode("utf-8"), False
        except UnicodeDecodeError:
            return None, False
    except OSError:
        return None, False


def _count_lines(path: str) -> int:
    """Count newlines without loading the whole file into memory."""
    count = 0
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                count += chunk.count(b"\n")
    except OSError:
        return 0
    return count


def _readme_description(repo_root: str) -> str:
    """Extract the first README paragraph as the repo description."""
    for name in ("README.md", "README.rst", "README"):
        candidate = os.path.join(repo_root, name)
        if not os.path.isfile(candidate):
            continue
        try:
            text, _ = _read_text(candidate, cap=64_000)
            if not text:
                continue
        except OSError:
            continue
        # First paragraph = text until the first blank line; skip the '# title'.
        paragraphs = re.split(r"\n\s*\n", text)
        for para in paragraphs:
            lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
            if not lines:
                continue
            if lines[0].startswith("#"):
                lines = lines[1:]
            if not lines:
                continue
            first = " ".join(lines)
            return first.strip()[:200]
    return ""


def build_lightweight_index(
    repo_root: str,
    repo_name: str | None = None,
    description: str | None = None,
) -> dict:
    """Build the lightweight codebase JSON for a local repo directory.

    Args:
        repo_root: Absolute path to the local repository folder.
        repo_name: Override for the ``repo_name`` key (default: folder basename).
        description: Override for the ``description`` key (default: first
            paragraph of README).

    Returns:
        dict with keys: repo_name, description, file_tree, files_metadata.
        The ``files_metadata`` maps repo-relative '/' paths to
        {"language", "size_lines", "imports"} — no file content.
    """
    root = os.path.realpath(repo_root)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Not a directory: {repo_root}")

    resolved_name = repo_name or os.path.basename(root) or root
    resolved_desc = description if description is not None else _readme_description(root)

    file_tree: list[str] = []
    files_metadata: dict[str, dict] = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")

        kept_dirs = []
        for d in dirnames:
            rel_dir_path = f"{rel_dir}/{d}".strip("/") if rel_dir else d
            if should_skip(rel_dir_path):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in filenames:
            rel_path = f"{rel_dir}/{fname}".strip("/") if rel_dir else fname
            if should_skip(rel_path):
                continue
            abs_path = os.path.join(dirpath, fname)
            # Skip symlinks so a link can never drag indexed content outside root.
            if os.path.islink(abs_path):
                continue

            language = get_file_language(rel_path)
            size_lines = _count_lines(abs_path)
            imports: list = []

            if language in ("python", "javascript", "typescript", "tsx", "jsx"):
                content, truncated = _read_text(abs_path, cap=MAX_TEXT_BYTES)
                if content is not None:
                    if language == "python":
                        imports = extract_imports_python(content)
                    else:
                        imports = extract_imports_js(content)
                else:
                    language = "binary"

            file_tree.append(rel_path)
            files_metadata[rel_path] = {
                "language": language,
                "size_lines": size_lines,
                "imports": imports,
            }

    file_tree.sort()
    ordered_metadata = {p: files_metadata[p] for p in file_tree}
    return {
        "repo_name": resolved_name,
        "description": resolved_desc,
        "file_tree": file_tree,
        "files_metadata": ordered_metadata,
    }


def build_lightweight_context(codebase: dict) -> dict:
    """Strip a full codebase JSON (with content) down to the lightweight form.

    Canonical equivalent of the old fetch_github.build_lightweight_context —
    used by update_github_repo.py on full indices fetched from GitHub.
    """
    files = codebase.get("files", {})
    return {
        "repo_name": codebase["repo_name"],
        "description": codebase["description"],
        "file_tree": codebase["file_tree"],
        "files_metadata": {
            path: {
                "language": data["language"],
                "size_lines": data["size_lines"],
                "imports": data["imports"],
            }
            for path, data in files.items()
        },
    }


# ---------------------------------------------------------------------------
# Guarded readers (host-side soft guardrails, used by codebase_helpers)
# ---------------------------------------------------------------------------

def _realpath_relative(repo_root: str, path: str) -> str | None:
    """Resolve ``path`` against ``repo_root``; None if it escapes the root."""
    base = os.path.realpath(repo_root)
    candidate = os.path.realpath(os.path.join(base, path.lstrip("/\\")))
    if candidate == base or candidate.startswith(base + os.sep):
        return candidate
    return None


def read_local_file(repo_root: str, path: str) -> str:
    """Read a file from a local repo with soft guardrails enforced.

    Returns the file text on success; otherwise a short diagnostic string
    prefixed with "[BLOCKED" (hard block) or "File not found"/"Error".
    The repo-relative ``files_metadata`` paths from the index always work.
    """
    root = os.path.realpath(repo_root)
    rel_path = path.replace("\\", "/")
    if rel_path.startswith("/") or ".." in rel_path.split("/"):
        return f"{BLOCKED_PREFIX}: path outside repo root: {path}"

    if should_skip(rel_path):
        reason = skip_reason(rel_path) or "sensitive/generated file"
        return f"{BLOCKED_PREFIX}: skipped ({reason}): {path}"

    abs_path = _realpath_relative(root, path)
    if abs_path is None:
        return f"{BLOCKED_PREFIX}: path outside repo root: {path}"
    if not os.path.exists(abs_path):
        return f"File not found: {path}"
    if os.path.isdir(abs_path):
        return f"{BLOCKED_PREFIX}: is a directory (use file_tree to list files): {path}"

    content, truncated = _read_text(abs_path, cap=READ_CAP_BYTES)
    if content is None:
        return f"{BLOCKED_PREFIX}: binary file, not readable: {path}"
    if truncated:
        content = content + f"\n[TRUNCATED: file larger than {READ_CAP_BYTES} bytes; showing the first {READ_CAP_BYTES} bytes]"
    return content


def get_multiple_files_local(repo_root: str, paths: list) -> dict:
    """Guarded bulk read; returns {path: text} mapping with BLOCKED markers."""
    result = {}
    for p in paths:
        result[p] = read_local_file(repo_root, p)
    return result


def search_local_files(repo_root: str, keyword: str, max_results: int = SEARCH_MAX_RESULTS) -> list:
    """Search file contents of a local repo for a keyword (case-insensitive).

    Respects the same skip rules as indexing. Returns a list of repo-relative
    paths on success, or a single "[BLOCKED..."/"[Search error...]" entry on
    failure, capped at ``max_results`` matches.
    """
    root = os.path.realpath(repo_root)
    if not keyword:
        return ["[Search error: empty keyword]"]
    needle = keyword.lower()
    matches: list[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            rel_dir = os.path.relpath(dirpath, root)
            rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")

            kept_dirs = []
            for d in dirnames:
                rel_dir_path = f"{rel_dir}/{d}".strip("/") if rel_dir else d
                if should_skip(rel_dir_path):
                    continue
                kept_dirs.append(d)
            dirnames[:] = kept_dirs

            for fname in filenames:
                rel_path = f"{rel_dir}/{fname}".strip("/") if rel_dir else fname
                if should_skip(rel_path):
                    continue
                abs_path = os.path.join(dirpath, fname)
                if os.path.islink(abs_path):
                    continue
                content, _ = _read_text(abs_path, cap=SEARCH_CAP_BYTES)
                if content is not None and needle in content.lower():
                    matches.append(rel_path)
                    if len(matches) >= max_results:
                        return matches
    except OSError as e:
        return [f"[Search error: {e}]"]
    return matches


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_output_path(repo_name: str, out_dir: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", repo_name or "repo").lower()
    return os.path.join(out_dir, f"codebase_{safe}_lightweight.json")


def _report(index: dict, repo_root: str, output_path: str) -> None:
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, repo_root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for d in dirnames:
            rel = f"{rel_dir}/{d}".strip("/") if rel_dir else d
            if should_skip(rel):
                skipped.append(rel)
        for f in filenames:
            rel = f"{rel_dir}/{f}".strip("/") if rel_dir else f
            if should_skip(rel):
                skipped.append(rel)

    print(f"repo:      {index['repo_name']}")
    print(f"root:      {os.path.realpath(repo_root)}")
    print(f"desc:      {index['description'][:80]}")
    print(f"indexed:   {len(index['file_tree'])} files")
    langs = {}
    for meta in index["files_metadata"].values():
        langs[meta["language"]] = langs.get(meta["language"], 0) + 1
    print(f"langs:     {dict(sorted(langs.items(), key=lambda kv: -kv[1]))}")
    print(f"skipped:   {len(skipped)} entries ({', '.join(sorted(skipped)[:12])}{'...' if len(skipped) > 12 else ''})")
    print(f"written:   {output_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a lightweight codebase JSON index from a local repo.",
    )
    parser.add_argument("path", nargs="?", help="Path to the local repo directory.")
    parser.add_argument("--repo-path", dest="repo_path", help="Path to the local repo directory.")
    parser.add_argument("--output", help="Output JSON path (default: codebase_<name>_lightweight.json).")
    parser.add_argument("--repo-name", help="Value for the repo_name key (default: folder basename).")
    parser.add_argument("--description", help="Value for the description key (default: first README paragraph).")
    parser.add_argument("--report", action="store_true", help="Print a scan summary after writing.")
    args = parser.parse_args(argv)

    repo_path = args.path or args.repo_path
    if not repo_path:
        try:
            repo_path = input("Path to local repo: ").strip()
        except EOFError:
            repo_path = ""
    if not repo_path:
        parser.error("repo path is required.")

    root = os.path.realpath(repo_path)
    if not os.path.isdir(root):
        parser.error(f"Not a directory: {repo_path}")

    index = build_lightweight_index(
        root,
        repo_name=args.repo_name,
        description=args.description,
    )

    output_path = args.output or _default_output_path(index["repo_name"], os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(index, f, indent=2)

    if args.report:
        _report(index, root, output_path)
    else:
        print(f"Built lightweight index -> {output_path} ({len(index['file_tree'])} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())