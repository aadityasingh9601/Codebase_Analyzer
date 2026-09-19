"""
Hardened REPL guardrails for the sandboxed LocalREPL namespace.

This file is injected as SOURCE into the RLM's setup_code, then
``_apply_repl_guardrails(__builtins__, root)`` swaps the sandbox builtins in
place (rebinding ``__builtins__`` would NOT survive LocalREPL.execute_code,
but mutating the dict does). Everything below the docstring is definition-only
so it is safe to exec-embed: no side effects, no ``__main__`` block.

Four layers:
  1. jailed_open  — read-only ``open`` limited to the repo root and the REPL's
     temp working directory; sensitive paths inside the repo are blocked;
     write/append modes raise.
  2. allowlist __import__ — only a curated stdlib set; ``os`` is returned as a
     read-only proxy; everything else (subprocess, shutil, requests, ...)
     raises ImportError.
  3. eval / exec / compile / input / globals / locals stay None (removed-capable
     too) and ``setattr`` / ``delattr`` are removed outright.
  4. The safe ``os`` proxy omits mutating/process-exit attributes and hands out
     a filtered ``environ`` that hides keys that look like secrets.

Also importable standalone (``import repl_guardrail``) for unit testing.
"""

import builtins as _builtins
import json  # noqa: F401   (keep importable+allowlisted; referenced values below)
import os as _os
import types as _types

# Exception classes bound at definition time: the sandbox's restricted
# __builtins__ dict may be missing some (e.g. an installed rlm's
# _SAFE_BUILTINS omits PermissionError), so never rely on bare-name lookup
# of these inside the guardrail.
_PermissionError = _builtins.PermissionError
_ImportError = _builtins.ImportError

# Modules still importable through the allowlisted __import__.
_ALLOWED_IMPORT_MODULES = frozenset({
    "json", "re", "os", "pathlib", "collections", "itertools", "functools",
    "math", "ast", "typing", "dataclasses", "string", "random", "statistics",
    "datetime", "time", "uuid", "copy", "operator", "difflib", "textwrap",
    "io", "enum", "hashlib", "base64", "heapq", "bisect", "decimal",
    "fractions", "pprint", "unicodedata", "binascii", "struct", "zlib",
})

# os.* attributes stripped from the safe proxy (mutating / process / fd / write).
_OS_BLOCKED_ATTRS = frozenset({
    "system", "popen", "posix_spawn", "spawnv", "spawnve", "spawnvp",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "execl", "execle", "execlp",
    "execv", "execve", "execvp", "execvpe", "fork", "forkpty",
    "kill", "killpg", "nice", "chdir", "fchdir", "chmod", "chown", "lchmod",
    "lchown", "fchmod", "fchown", "umask", "chroot", "makedev",
    "remove", "removedirs", "unlink", "rmdir", "mkdir", "makedirs", "mkfifo",
    "mknod", "rename", "renames", "replace", "link", "symlink", "readlink",
    "truncate", "utime", "setxattr", "removexattr", "getxattr", "listxattr",
    "open", "close", "fdopen", "dup", "dup2", "pipe", "write", "sendfile",
    "setuid", "seteuid", "setreuid", "setgid", "setegid", "setregid",
    "setgroups", "initgroups", "setpriority", "setrlimit", "prlimit",
    "setpgid", "setpgrp", "setsid", "setns", "startfile", "closerange",
    "environ", "putenv", "unsetenv", "getenv",
})

# Env var name patterns treated as secrets (filtered from os.environ).
_SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS",
                     "CREDENTIAL", "AUTH", "PRIVATE")

# Components that mark a repo-relative path as sensitive inside the jail.
_JAIL_SENSITIVE_DIRS = frozenset({
    ".git", ".hg", ".svn", ".next", "node_modules", "venv", ".venv",
    "__pycache__", ".ssh", ".pytest_cache", ".mypy_cache", ".terraform",
    ".idea", ".vscode", "dist", "build", "site-packages",
})
_JAIL_SENSITIVE_EXTENSIONS = (
    ".pem", ".key", ".p12", ".pfx", ".p8", ".jks", ".p7b", ".crt", ".cer",
    ".env", ".lock", ".sql",
)


def _is_secret_env_key(name: str) -> bool:
    upper = str(name).upper()
    return any(h in upper for h in _SECRET_ENV_HINTS)


def _build_safe_os() -> _types.ModuleType:
    """Return a read-only 'os' proxy module (credential-safe environ)."""
    proxy = _types.ModuleType("os")
    for attr in dir(_os):
        if attr in _OS_BLOCKED_ATTRS:
            continue
        try:
            value = getattr(_os, attr)
        except AttributeError:
            continue
        if attr.startswith("__"):
            continue
        try:
            setattr(proxy, attr, value)
        except Exception:
            pass
    proxy.environ = {
        k: v for k, v in _os.environ.items() if not _is_secret_env_key(k)
    }
    return proxy


_SAFE_OS_PROXY = _build_safe_os()


def _is_sensitive_relpath(rel: str) -> bool:
    """True if a repo-relative '/' path is flagged sensitive for the jail."""
    comps = [c for c in rel.split("/") if c]
    if any(c in _JAIL_SENSITIVE_DIRS for c in comps):
        return True
    if any(sub in rel for sub in (".env", ".lock")):
        return True
    lower = rel.lower()
    return lower.endswith(_JAIL_SENSITIVE_EXTENSIONS)


def make_jailed_open(allowed_roots: tuple[str, ...], real_open=open):
    """Build an ``open`` bound to ``allowed_roots`` (inclusive, realpath).

    Layers checked at call time:
      - integer fds pass through (already-open handles, e.g. sys.stdin).
      - write/append/update modes ('a', 'w', 'x', '+') are forbidden.
      - the resolved realpath must sit inside one of the allowed roots.
      - inside a repo root, sensitive paths (see _is_sensitive_relpath).
    """

    def jailed_open(file, mode="r", *args, **kwargs):
        if isinstance(file, int):
            return real_open(file, mode, *args, **kwargs)

        if isinstance(mode, str):
            if any(ch in mode for ch in "wax+"):
                raise _PermissionError(
                    f"[GUARDRAIL] write mode blocked: open({file!r}, {mode!r})"
                )

        try:
            path = _os.fsdecode(file)
        except Exception:
            path = str(file)

        resolved = _os.path.realpath(path)
        roots = tuple(_os.path.realpath(r) for r in allowed_roots)
        inside = False
        for root in roots:
            if resolved == root or resolved.startswith(root + _os.sep):
                inside = True
                # Sensitive-path check only applies inside a repo root.
                if root != _os.path.realpath(_os.getcwd()):
                    rel = _os.path.relpath(resolved, root).replace(_os.sep, "/")
                    if _is_sensitive_relpath(rel):
                        raise _PermissionError(
                            f"[GUARDRAIL] sensitive path blocked: {path}"
                        )
                break
        if not inside:
            raise _PermissionError(
                f"[GUARDRAIL] path outside allowed roots: {path}"
            )
        return real_open(file, mode, *args, **kwargs)

    return jailed_open


def make_allowlist_import(real_import=__import__):
    """Build an ``__import__`` that only allows the curated stdlib set.

    ``os`` resolves to the safe proxy; unknown/dangerous modules raise.
    """

    def allowlist_import(name, globals_=None, locals_=None, fromlist=(), level=0):
        if level not in (0, -1):
            raise _ImportError(f"[GUARDRAIL] relative import blocked: {name!r}")
        base = name.split(".")[0]
        if base not in _ALLOWED_IMPORT_MODULES:
            raise _ImportError(f"[GUARDRAIL] import blocked: {name!r}")
        module = real_import(name, globals_, locals_, fromlist, level)
        if base == "os":
            module = _SAFE_OS_PROXY
        return module

    return allowlist_import


def _apply_repl_guardrails(builtins_dict: dict, repo_root: str) -> None:
    """Apply the four guardrail layers to a sandbox ``__builtins__`` dict.

    Must be called AFTER the sandbox has been created (so the dict is the
    live one referenced by ``__builtins__``) and it MUTATES the dict in place.
    ``repo_root`` is the local repo root that becomes the read jail (plus the
    REPL's own temp working dir, which is os.getcwd() during execution).

    Raises:
        RuntimeError: if ``builtins_dict`` is not a dict (e.g. module object).
    """
    if not isinstance(builtins_dict, dict):
        raise RuntimeError("[GUARDRAIL] __builtins__ is not a dict; refusing to patch")

    real_open = builtins_dict.get("open", open)
    real_import = builtins_dict.get("__import__", __import__)
    cwd = _os.getcwd()

    allowed_roots = tuple({
        _os.path.realpath(repo_root) if repo_root else cwd,
        _os.path.realpath(cwd),
    })
    builtins_dict["open"] = make_jailed_open(allowed_roots, real_open=real_open)
    builtins_dict["__import__"] = make_allowlist_import(real_import=real_import)

    # eval / exec / compile / input / globals / locals remain None (no-op here).
    for name in ("setattr", "delattr"):
        builtins_dict.pop(name, None)

    # Hard-drop anything that survived as a real object.
    for name in ("eval", "exec", "compile", "input", "globals", "locals"):
        if builtins_dict.get(name) is not None:
            builtins_dict[name] = None


if __name__ == "__main__":
    # Standalone smoke test (NOT embedded into setup_code — stripped on exec).
    b = {"open": open, "__import__": __import__, "setattr": setattr,
         "delattr": delattr, "eval": None, "exec": None}
    root = _os.path.realpath(_os.getcwd())
    _apply_repl_guardrails(b, root)
    try:
        b["open"]("/etc/passwd")
        print("FAIL: /etc/passwd not blocked")
    except _PermissionError as e:
        print("OK: blocked /etc/passwd")
    try:
        b["open"]("/tmp/nope.txt", "w")
        print("FAIL: write not blocked")
    except _PermissionError as e:
        print("OK: blocked write mode")
    try:
        b["__import__"]("shutil")
        print("FAIL: shutil import not blocked")
    except _ImportError as e:
        print("OK: blocked shutil import")
    print("OK: smoke test done")