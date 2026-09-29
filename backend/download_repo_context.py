"""
Download a repo's indexed context from the Supabase storage bucket back to
local/server disk, exactly where the rest of the codebase reads it from:

  Remote (bucket)                                              -> Local
  {bucket}/{owner}/{repo}/{sha}/codebase.json                    -> ./codebases/codebase_<repo>.json
  {bucket}/{owner}/{repo}/{sha}/codebase_lightweight.json        -> ./codebases/codebase_<repo>_lightweight.json
  {bucket}/{owner}/{repo}/{sha}/users/{user_id}.dill             -> ./rlm_sessions/{user_id}.dill

The commit SHA is part of the remote path, so all three files come from one
commit of one repo and never mix with another user's context.

.build_repl_state.py reads from ./codebases and PersistentSession reads/writes
the .dill files from ./rlm_sessions, so these are drop-in locations.

Usage:
    python download_repo_context.py --repo owner/repo --user <user_id> --sha <commit>
    python download_repo_context.py --repo owner/repo --user <user_id> --sha <commit> --storage ./rlm_sessions

If --sha is omitted it is read from the local codebase JSON's last_commit_sha
(use --list-shas to see what is stored for a repo).

Uses SUPABASE_URL + SUPABASE_SECRET_KEY (or SUPABASE_PUBLISHABLE_KEY) and
SUPABASE_BUCKET from the environment (default bucket: repo-contexts).
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from repo_storage import (
    list_shas,
    resolve_sha,
    split_repo,
    storage_path_dill,
    storage_path_json,
    storage_path_light_json,
    stream_download,
)

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "codebases")
DEFAULT_STORAGE_PATH = os.path.join(SCRIPT_DIR, "rlm_sessions")


def _repo_key(owner: str, repo: str) -> str:
    return repo.lower()


def download_repo_context(
    repo_name: str,
    user_id: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    storage_path: str = DEFAULT_STORAGE_PATH,
    bucket: str = "",
    sha: str = "",
) -> list:
    """Download a repo's codebase JSON index files + session .dill from Supabase.

    All three files come from the single commit `sha` of `repo_name`. When
    `sha` is omitted it is read from the local codebase JSON's
    last_commit_sha, so the local index and the remote files always match.

    Returns the list of local file paths written.
    """
    owner, repo = split_repo(repo_name)
    repo_key = _repo_key(owner, repo)

    # Prefer the caller's sha; otherwise fall back to what is already on disk.
    sha = resolve_sha(
        sha,
        os.path.join(output_dir, f"codebase_{repo_key}.json"),
        os.path.join(output_dir, f"codebase_{repo_key}_lightweight.json"),
    )
    print(f"Repo context commit: {sha}")

    downloads = [
        (storage_path_json(owner, repo, sha), os.path.join(output_dir, f"codebase_{repo_key}.json")),
        (storage_path_light_json(owner, repo, sha), os.path.join(output_dir, f"codebase_{repo_key}_lightweight.json")),
        (storage_path_dill(owner, repo, sha, user_id), os.path.join(storage_path, f"{user_id}.dill")),
    ]

    written = []
    for remote, local in downloads:
        try:
            stream_download(remote, local, bucket)
        except Exception as e:
            print(f"  via supabase: skipping {remote} (not found? {e})")
            continue
        written.append(local)
        print(f"Downloaded -> {local}")

    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repo identity, e.g. 'owner/repo'.")
    parser.add_argument("--user", required=False, help="Session user id (dill filename base).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Where to save the codebase JSON files (default: {DEFAULT_OUTPUT_DIR}).")
    parser.add_argument("--storage", default=DEFAULT_STORAGE_PATH, help=f"Where to save the .dill file (default: {DEFAULT_STORAGE_PATH}).")
    parser.add_argument("--bucket", default="", help="Supabase storage bucket (default: $SUPABASE_BUCKET or repo-contexts).")
    parser.add_argument("--sha", default="", help="Commit sha to download (default: read from the local codebase JSON's last_commit_sha).")
    parser.add_argument("--list-shas", action="store_true", help="List the commit shas stored for the repo, then exit.")
    args = parser.parse_args()

    if args.list_shas:
        owner, repo = split_repo(args.repo)
        shas = list_shas(owner, repo, args.bucket)
        if not shas:
            print(f"No stored contexts for {args.repo}.")
            return 1
        for sha in shas:
            print(sha)
        return 0

    if not args.user:
        parser.error("--user is required unless --list-shas is used")

    try:
        paths = download_repo_context(
            repo_name=args.repo,
            user_id=args.user,
            output_dir=args.output_dir,
            storage_path=args.storage,
            bucket=args.bucket,
            sha=args.sha,
        )
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}")
        return 1

    if not paths:
        print("Nothing downloaded (no remote files found).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())