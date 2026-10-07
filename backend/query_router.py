import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from rlm import RLM
from rlm.clients import get_client
from rlm_config import DEFAULT_MODEL, ai_api_key
from session_index import (
    acquire_session,
    close_session,
    current_session,
    current_session_id,
    reset_default_session_id,
    resolve_session_paths,
    set_default_session_id,
)

GAUGE_MAX_OUTPUT_TOKENS = 4096
DIRECT_BUDGET_CHARS = 60_000
GAUGE_ROUNDS = 2
EVIDENCE_FILES_PER_ROUND = 4
SNIPPET_LIMIT_CHARS = 8_000
SNIPPET_TAIL_CHARS = 4_000
MAX_INDEX_CHARS = 30_000
SHORT_QUESTION_CHARS = 20
LONG_QUESTION_CHARS = 300

META_PATTERNS = [
    r"\b(how many|how much|count|total number of|number of)\b",
    r"\b(list|what are|which)\b.*\b(files|directories|folders|modules|packages|languages|dependencies)\b",
    r"\b(tech stack|stack|dependencies|package manager|structure|layout|overview|entry point|readme)\b",
]

RLM_PATTERNS = [
    r"\b(architecture|architectural|diagram|mermaid|flow|design|class hierarchy)\b",
    r"\b(how does|how do|why does|why is|explain|describe|trace|call graph|call chain|calls into|dependency graph|data flow|end-?to-?end|compare|refactor|optimize|debug|fix)\b",
    r"\b(risks?|concerns?|trade-?offs?|recommend|suggests?|pros and cons)\b",
    r"\b(above|before|earlier|previous|you said|we discussed|instead of)\b",
    r"\b(manually|step by step|in detail|thoroughly)\b",
]

_SECTION_HEADER = re.compile(r"^(ANSWER|NEED_FILES|UNSURE)\s*:?", re.IGNORECASE | re.MULTILINE)

_COMPLAINT_RE = re.compile(
    r"\bcontext is empty\b"
    r"|\b(i can't?|i cannot|i could n?ot|i am unable to)\b.{0,80}(access|see|view|read|retrieve).{0,80}(the )?(actual )?file (contents?|content)\b"
    r"|\bi (do n?ot|don't) have .{0,40}(the )?(file|actual) (contents?|content)\b"
    r"|(the )?(file|actual) (contents?|content) (are|is|were|was) (not|never) (available|provided|included|present|shown)\b"
    r"|no (file )?(contents?|content) (available|included|provided|present|shown)\b",
    re.IGNORECASE,
)


@dataclass
class RouterResult:
    answer: Optional[str]
    tier: str
    reason: str
    answer_source: str
    evidence_paths: list[str] = field(default_factory=list)
    direct_calls: int = 0
    direct_input_tokens: int = 0
    direct_output_tokens: int = 0
    rlm_input_tokens: int = 0
    rlm_output_tokens: int = 0
    rlm_iterations: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


# Index loading used to be @lru_cache(maxsize=1), which cached correctly but
# pinned the parsed dict for the whole process with no way to release it. It is
# now delegated to session_index, which shares one parse across all sessions on
# the same repo, reference-counts it, and frees it when the last session closes.
#
# No arguments on purpose: these run deep inside `route_and_answer` and the
# exec'd REPL, where the index paths are not in scope. `current_session()`
# resolves them from the session_index registry for the current request.
# Passing a hardcoded path here was the wrong-repo bug: a session pointed at
# one repo silently read another's index.
def _load_index() -> dict:
    return current_session().light_metadata()


def _load_full() -> dict:
    return current_session().full_index()


def _file_tree() -> list[str]:
    return _load_index()["file_tree"]


def close_query_session() -> None:
    """Release the current session's index reference, freeing the RAM.

    The server calls this when a user's session ends. Until then, the index
    stays resident so repeated queries don't re-parse 50MB.
    """
    close_session()


def _serialize_index(index: dict) -> str:
    summary = {
        "repo_name": index["repo_name"],
        "description": index["description"],
    }
    files_out = {}
    for path in _file_tree():
        meta = index["files_metadata"].get(path, {})
        files_out[path] = {
            "language": meta.get("language"),
            "size_lines": meta.get("size_lines"),
            "imports": meta.get("imports", []),
        }
    summary["files"] = files_out
    text = json.dumps(summary, indent=1, ensure_ascii=False)
    if len(text) > MAX_INDEX_CHARS:
        text = text[:MAX_INDEX_CHARS] + "\n... (truncated)"
    return text


def _client() -> "object":
    return get_client(
        "openrouter",
        {
            "model_name": DEFAULT_MODEL,
            "api_key": ai_api_key,
            "sampling_args": {"max_tokens": GAUGE_MAX_OUTPUT_TOKENS},
        },
    )


def _snapshot_tokens(client) -> tuple[int, int]:
    inp = sum(client.model_input_tokens.values())
    out = sum(client.model_output_tokens.values())
    return inp, out


def heuristic_route(question: str) -> tuple[Optional[str], str]:
    text = question.strip()
    if not text:
        return "RLM", "empty question"
    lower = text.lower()
    if len(text) < SHORT_QUESTION_CHARS:
        return "RLM", "short question likely refers to prior session context"
    if len(text) > LONG_QUESTION_CHARS:
        return "RLM", "long/multi-part question"
    for pattern in RLM_PATTERNS:
        if re.search(pattern, lower):
            return "RLM", f"matches broad-analysis signal: {pattern}"
    for pattern in META_PATTERNS:
        if re.search(pattern, lower):
            return "META", f"matches index-level signal: {pattern}"
    return None, "ambiguous"


def _named_files(question: str) -> list[str]:
    tree = _file_tree()
    q = question.lower()
    matches = [p for p in tree if p.lower() in q]
    if not matches:
        matches = [p for p in tree if os.path.basename(p).lower() in q]
    return matches[:EVIDENCE_FILES_PER_ROUND]


def _resolve_paths(requested: list[str]) -> list[str]:
    tree = _file_tree()
    resolved = []
    seen = set()
    for path in requested[:EVIDENCE_FILES_PER_ROUND]:
        if path in tree:
            candidate = path
        else:
            matches = [p for p in tree if os.path.basename(p).lower() == os.path.basename(path).lower()]
            candidate = matches[0] if matches else None
        if candidate and candidate not in seen:
            seen.add(candidate)
            resolved.append(candidate)
    return resolved


def _retrieve(paths: list[str]) -> str:
    full = _load_full()
    files = full["files"]
    out = []
    budget = DIRECT_BUDGET_CHARS
    for path in _resolve_paths(paths):
        entry = files.get(path)
        if not entry:
            continue
        content = entry.get("content", "")
        n = entry.get("size_lines", 0)
        if len(content) > SNIPPET_LIMIT_CHARS:
            head = content[:SNIPPET_LIMIT_CHARS - SNIPPET_TAIL_CHARS]
            tail = content[-SNIPPET_TAIL_CHARS:]
            content = head + "\n... [truncated middle] ...\n" + tail
        if budget - len(content) < 0:
            break
        out.append(f"FILE {path} ({n} lines):\n{content}")
        budget -= len(content)
    return "\n\n".join(out)


def _gauge_messages(
    question: str, index_text: str, evidence: str, allow_files: bool
) -> list[dict]:
    custom = (
        "Do NOT request files. If the index alone is insufficient to answer "
        "reliably, respond with UNSURE."
        if not allow_files
        else (
            "You may request the up to 4 specific files you need (only paths from "
            "the FILE TREE) and they will be provided before you answer."
        )
    )
    system = (
        "You answer questions about the source code in the provided index. "
        "Respond with EXACTLY ONE block, nothing else:\n\n"
        "ANSWER:<final answer to the user>\n"
        "OR\n"
        "NEED_FILES:path1, path2, path3, path4\n"
        "OR\n"
        "UNSURE\n\n"
        "Rules:\n"
        "- Never invent paths. Only paths present in the FILE TREE.\n"
        "- Never claim file contents you cannot see from the provided material.\n"
        "- If you can answer from what is provided, give ANSWER.\n"
        f"- {custom}\n"
        "- Keep ANSWER concise and directly on point."
    )
    user = (
        f"QUESTION:\n{question}\n\n"
        f"REPOSITORY INDEX:\n{index_text}\n"
    )
    if evidence:
        user += f"\nFILE EXCERPTS:\n{evidence}\n"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_response(response: str, file_tree: list[str]) -> tuple[str, list[str]]:
    if not response:
        return "UNSURE", []
    text = response.strip()
    match = _SECTION_HEADER.search(text)
    if not match:
        return "UNSURE", []
    marker = match.group(1).upper()
    rest = text[match.end():].strip()
    if marker == "UNSURE":
        return "UNSURE", []
    if marker == "ANSWER":
        answer_text = rest if rest else ""
        if _COMPLAINT_RE.search(answer_text):
            return "UNSURE", []
        return "ANSWER", [answer_text]
    if marker == "NEED_FILES":
        cleaned = re.sub(r"[\[\]\"']", "", rest)
        raw = [p.strip() for p in re.split(r"[,;\n]", cleaned) if p.strip()]
        valid = list(dict.fromkeys(p for p in raw if p in file_tree))
        if not valid:
            return "UNSURE", []
        return "NEED_FILES", valid[:EVIDENCE_FILES_PER_ROUND]
    return "UNSURE", []


def _escalate_to_rlm(rlm: Optional[RLM], question: str, reason: str) -> RouterResult:
    start = time.perf_counter()
    if rlm is None:
        rlm = _create_default_rlm()
        close_after = True
    else:
        close_after = False
    try:
        # `context` is injected by setup_code (a SharedContext proxy). The
        # skip flag is one-shot — RLM clears it after the first completion —
        # so re-arm it here, or the next completion() replaces our metadata
        # with the (empty) prompt string and `context['file_tree']` breaks.
        if hasattr(rlm, "_skip_next_context_add"):
            rlm._skip_next_context_add = True
        result = rlm.completion(prompt="", root_prompt=question)
    except Exception as exc:
        return RouterResult(
            answer=None,
            tier="RLM",
            reason=reason,
            answer_source="error",
            error=str(exc),
            elapsed_seconds=time.perf_counter() - start,
        )
    finally:
        if close_after:
            rlm.close()
    iterations = 0
    if result.metadata:
        iterations = len(result.metadata.get("iterations", []))
    return RouterResult(
        answer=result.response,
        tier="RLM",
        reason=reason,
        answer_source="rlm",
        rlm_input_tokens=result.usage_summary.total_input_tokens,
        rlm_output_tokens=result.usage_summary.total_output_tokens,
        rlm_iterations=iterations,
        elapsed_seconds=time.perf_counter() - start,
    )


def route_and_answer(question: str, rlm: Optional[RLM] = None) -> RouterResult:
    start = time.perf_counter()
    index_text = _serialize_index(_load_index())
    file_tree = _file_tree()

    category, reason = heuristic_route(question)
    if category == "RLM":
        return _escalate_to_rlm(rlm, question, reason)
    if category == "META":
        tier = "META"
        allow_files = False
        route_reason = reason
    else:
        tier = "DIRECT"
        allow_files = True
        route_reason = "ambiguous -> gauge"

    client = _client()
    evidence_paths: list[str] = []
    evidence_text = ""
    prior_in, prior_out = _snapshot_tokens(client)

    named = _named_files(question)
    if named:
        evidence_paths = named
        evidence_text = _retrieve(named)
        route_reason += f"; preloaded named files {named}"

    for round_idx in range(GAUGE_ROUNDS):
        messages = _gauge_messages(question, index_text, evidence_text, allow_files)
        try:
            response = client.completion(messages, model=DEFAULT_MODEL)
        except Exception as exc:
            return _escalate_to_rlm(
                rlm, question, f"direct tier failed (round {round_idx}): {exc}"
            )

        kind, payload = _parse_response(response, file_tree)
        if kind == "ANSWER":
            in_tok, out_tok = _snapshot_tokens(client)
            return RouterResult(
                answer=payload[0],
                tier=tier,
                reason=route_reason,
                answer_source=f"gauge_round_{round_idx}",
                evidence_paths=evidence_paths,
                direct_calls=round_idx + 1,
                direct_input_tokens=in_tok - prior_in,
                direct_output_tokens=out_tok - prior_out,
                elapsed_seconds=time.perf_counter() - start,
            )
        if kind == "NEED_FILES":
            if round_idx >= GAUGE_ROUNDS - 1 or not allow_files:
                break
            evidence_paths = payload
            evidence_text = _retrieve(payload)
            if not evidence_text:
                route_reason += f"; requested files not found in repo ({payload})"
                break
            route_reason += f"; retrieved {payload}"
            continue
        break

    in_tok, out_tok = _snapshot_tokens(client)
    result = _escalate_to_rlm(rlm, question, route_reason)
    result.direct_calls = GAUGE_ROUNDS
    result.direct_input_tokens = in_tok - prior_in
    result.direct_output_tokens = out_tok - prior_out
    result.elapsed_seconds = time.perf_counter() - start
    return result


def _create_default_rlm() -> RLM:
    """Fallback RLM for the current session (CLI use, tests).

    The HTTP path always passes an explicit `rlm` built by the session
    registry, so this only runs when nothing has registered an index yet —
    in which case there is no repo to point it at.
    """
    from rlm_config import create_rlm

    paths = resolve_session_paths()
    if paths is None:
        raise FileNotFoundError(
            "No codebase index registered for the current session. "
            "acquire_session() must be called before routing a query."
        )
    full_path, light_path = paths
    return create_rlm(
        full_json_path=full_path,
        light_json_path=light_path,
        session_id=current_session_id(),
    )


def answer_for_session(
    session_id: str,
    full_json_path: str,
    light_json_path: str,
    question: str,
) -> RouterResult:
    """Answer `question` for one self-contained, per-user session.

    The HTTP endpoint's whole per-query flow: parse the index, build a fresh
    RLM for it, route the question, and tear everything down before returning.
    There is no registry, no reuse and nothing shared across users or
    requests — the caller hands us the paths to one user's private index files
    and a session id under that user's namespace.

    Call from a worker thread when the event loop must stay responsive.
    """
    from rlm_config import create_rlm

    token = set_default_session_id(session_id)
    rlm = None
    try:
        # Pin the parsed index for this session (cheap, ref-counted — later
        # RLM helper calls resolve the same parse). Must happen before the RLM
        # is built: `create_rlm(...).init_env()` runs setup_code that defines
        # `context` over this index's session.
        acquire_session(session_id, full_json_path, light_json_path)
        rlm = create_rlm(
            full_json_path=full_json_path,
            light_json_path=light_json_path,
            session_id=session_id,
        )
        return route_and_answer(question, rlm=rlm)
    finally:
        reset_default_session_id(token)
        if rlm is not None:
            try:
                rlm.close()
            except Exception:
                pass
        close_session(session_id)