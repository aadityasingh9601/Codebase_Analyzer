"""
Download a repo's indexed context from the Supabase storage bucket back to
local/server disk, exactly where the rest of the codebase reads it from:

  Remote (bucket)                              -> Local
  {bucket}/{owner}/{repo}/codebase.json               -> ./codebases/codebase_<repo>.json
  {bucket}/{owner}/{repo}/codebase_lightweight.json   -> ./codebases/codebase_<repo>_lightweight.json
  {bucket}/{owner}/{repo}/{user_id}.dill              -> ./rlm_sessions/{user_id}.dill

.build_repl_state.py reads from ./codebases and PersistentSession reads/writes
the .dill files from ./rlm_sessions, so these are drop-in locations.

Usage:
    python download_repo_context.py --repo owner/repo --user <user_id>
    python download_repo_context.py --repo owner/repo --user <user_id> --storage ./rlm_sessions

Uses SUPABASE_URL + SUPABASE_SECRET_KEY (or SUPABASE_PUBLISHABLE_KEY) and
SUPABASE_BUCKET from the environment (default bucket: repo-contexts).
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from repo_storage import download, storage_path_dill, storage_path_json, storage_path_light_json

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "codebases")
DEFAULT_STORAGE_PATH = os.path.join(SCRIPT_DIR, "rlm_sessions")


def _sanitize_repo(repo_name: str) -> str:
    """Validate owner/repo and return it normalized (no leading/trailing '/')."""
    repo = (repo_name or "").strip().strip("/")
    if not repo or "/" not in repo or ".." in repo:
        raise ValueError(f"Invalid repo name: {repo_name!r} (expected 'owner/repo')")
    return repo


def _repo_key(repo_name: str) -> str:
    return repo_name.rsplit("/", 1)[-1].lower()


def download_repo_context(
    repo_name: str,
    user_id: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    storage_path: str = DEFAULT_STORAGE_PATH,
    bucket: str = "",
) -> list:
    """Download a repo's codebase JSON index files + session .dill from Supabase.

    Returns the list of local file paths written.
    """
    repo = _sanitize_repo(repo_name)
    repo_key = _repo_key(repo)

    downloads = [
        (storage_path_json(repo), os.path.join(output_dir, f"codebase_{repo_key}.json")),
        (storage_path_light_json(repo), os.path.join(output_dir, f"codebase_{repo_key}_lightweight.json")),
        (storage_path_dill(repo, user_id), os.path.join(storage_path, f"{user_id}.dill")),
    ]

    written = []
    for remote, local in downloads:
        try:
            download(remote, local, bucket)
        except Exception as e:
            print(f"  via supabase: skipping {remote} (not found? {e})")
            continue
        written.append(local)
        print(f"Downloaded -> {local}")

    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repo identity, e.g. 'owner/repo'.")
    parser.add_argument("--user", required=True, help="Session user id (dill filename base).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Where to save the codebase JSON files (default: {DEFAULT_OUTPUT_DIR}).")
    parser.add_argument("--storage", default=DEFAULT_STORAGE_PATH, help=f"Where to save the .dill file (default: {DEFAULT_STORAGE_PATH}).")
    parser.add_argument("--bucket", default="", help="Supabase storage bucket (default: $SUPABASE_BUCKET or repo-contexts).")
    args = parser.parse_args()

    paths = download_repo_context(
        repo_name=args.repo,
        user_id=args.user,
        output_dir=args.output_dir,
        storage_path=args.storage,
        bucket=args.bucket,
    )
    if not paths:
        print("Nothing downloaded (no remote files found).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())