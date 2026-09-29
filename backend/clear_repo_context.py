"""
Delete a user session's local repo-context files (the two codebase JSON index
files + the RLM session .dill) from local/server disk.

Run this when a user ends their session so the persisted session artifacts
don't pile up on the (limited) local disk. Everything deleted here can be
re-downloaded from the Supabase storage bucket when the user starts a new
session (see download_repo_context.py).

Deletes:
  ./codebases/codebase_<repo>.json
  ./codebases/codebase_<repo>_lightweight.json
  ./rlm_sessions/{user_id}.dill

Usage:
    python clear_repo_context.py --repo owner/repo --user <user_id>
    python clear_repo_context.py --repo owner/repo --user <user_id> --keep-json

WARNING -- shared-repo fast path:
    The two codebase .json files are PER-REPO artifacts, but the .dill is
    PER-USER. If multiple users can work on the same repo at the same time,
    deleting the .json files when ONE user leaves will break the others who are
    still using that repo (get_file_content/_load_full_index would fail).
    Use --keep-json in that case, or only clear the .dill.
"""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "codebases")
DEFAULT_STORAGE_PATH = os.path.join(SCRIPT_DIR, "rlm_sessions")


def _sanitize_repo(repo_name: str) -> str:
    repo = (repo_name or "").strip().strip("/")
    if not repo or "/" not in repo or ".." in repo:
        raise ValueError(f"Invalid repo name: {repo_name!r} (expected 'owner/repo')")
    return repo


def _repo_key(repo_name: str) -> str:
    return repo_name.rsplit("/", 1)[-1].lower()


def clear_repo_context(
    repo_name: str,
    user_id: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    storage_path: str = DEFAULT_STORAGE_PATH,
    keep_json: bool = False,
) -> list:
    """Remove the session's local codebase JSON index files + .dill.

    Deletion is idempotent: files that do not exist are skipped silently.

    Args:
        repo_name: 'owner/repo' driving the codebase JSON filenames.
        user_id: User/session id driving the .dill filename.
        output_dir: Directory holding the codebase JSON files.
        storage_path: Directory holding the session .dill files.
        keep_json: If True, leave the per-repo codebase JSON files on disk
            (recommended when several users share the same repo).

    Returns:
        List of files that were removed.
    """
    repo = _sanitize_repo(repo_name)
    repo_key = _repo_key(repo)
    dill_path = os.path.join(storage_path, f"{user_id}.dill")

    targets = []
    if not keep_json:
        targets.append(os.path.join(output_dir, f"codebase_{repo_key}.json"))
        targets.append(os.path.join(output_dir, f"codebase_{repo_key}_lightweight.json"))
    targets.append(dill_path)

    removed = []
    for path in targets:
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
            print(f"Removed -> {path}")
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repo identity, e.g. 'owner/repo'.")
    parser.add_argument("--user", required=True, help="Session user id (dill filename base).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Codebase JSON directory (default: {DEFAULT_OUTPUT_DIR}).")
    parser.add_argument("--storage", default=DEFAULT_STORAGE_PATH, help=f"Session .dill directory (default: {DEFAULT_STORAGE_PATH}).")
    parser.add_argument("--keep-json", action="store_true", help="Keep the shared codebase JSON files (only clear the user's .dill).")
    args = parser.parse_args()

    removed = clear_repo_context(
        repo_name=args.repo,
        user_id=args.user,
        output_dir=args.output_dir,
        storage_path=args.storage,
        keep_json=args.keep_json,
    )
    if not removed:
        print("Nothing to clear (files already absent).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())