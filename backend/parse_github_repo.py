"""
Parse a locally-cloned GitHub repo (cloned via fetch_github_repo.py using
`git clone --depth 1`) into two JSON files:

  - codebase_<repo>.json                full index (metadata + every file)
  - codebase_<repo>_lightweight.json    metadata only (no file contents)

Files are streamed one by one from disk and written straight into the full
JSON, so we never hold every file's content in memory at once. The latest
commit SHA is captured from the clone so later incremental updates can compare
against it. After parsing finishes, the cloned repo is deleted locally.

Usage:
    python parse_github_repo.py <repo_dir> [--output-dir <dir>]
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys

from dotenv import load_dotenv
from repo_storage import (
    BUCKET,
    check_size_limit,
    resolve_sha,
    split_repo,
    storage_path_json,
    storage_path_light_json,
    upload,
)

load_dotenv()


def extract_imports_python(content: str) -> list:
    try:
        tree = ast.parse(content)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports
    except Exception:
        return []


def extract_imports_js(content: str) -> list:
    patterns = [
        r"import\s+.*?\s+from\s+['\"](.+?)['\"]",
        r"require\(['\"](.+?)['\"]\)",
    ]
    imports = []
    for pattern in patterns:
        imports.extend(re.findall(pattern, content))
    return imports


def get_file_language(path: str) -> str:
    ext_map = {
        '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
        '.tsx': 'tsx', '.jsx': 'jsx', '.md': 'markdown',
        '.json': 'json', '.yml': 'yaml', '.yaml': 'yaml',
        '.css': 'css', '.html': 'html', '.sql': 'sql'
    }
    for ext, lang in ext_map.items():
        if path.endswith(ext):
            return lang
    return 'text'


def should_skip(path: str) -> bool:
    skip = [
        'node_modules', '.git', '.sql', '__pycache__', 'dist/', 'build/',
        '.next/', 'venv/', '.env', '.lock', '.png', '.jpg',
        '.ico', '.svg', '.woff', '.ttf', '.mp4', '.zip'
    ]
    return any(p in path for p in skip)


def iter_repo_files(repo_dir: str):
    """Yield (rel_path, full_path) for every parseable file, one by one. One by one to avoid memory spikes from loading
    full repository in memory at once."""
    for root, dirs, filenames in os.walk(repo_dir):
        # Prune dirs using a trailing separator so substring rules like
        # "venv/" / "dist/" behave the same as they did on API file paths.
        dirs[:] = [d for d in dirs if not should_skip(os.path.join(root, d) + os.sep)]
        for filename in sorted(filenames):
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, repo_dir)
            if should_skip(rel_path):
                continue
            yield rel_path, full_path


def _git_or_empty(repo_dir: str, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_dir, capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def get_repo_info(repo_dir: str) -> dict:
    repo_url = _git_or_empty(repo_dir, "remote", "get-url", "origin")
    repo_name = ""
    if repo_url:
        if repo_url.endswith(".git"):
            repo_url = repo_url[:-4]
        if repo_url.startswith("git@") and ":" in repo_url:
            repo_name = repo_url.split(":", 1)[-1].rstrip("/")
        else:
            repo_name = "/".join([p for p in repo_url.rstrip("/").split("/") if p][-2:])

    return {
        "repo_name": repo_name,
        "default_branch": _git_or_empty(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") or "main",
        "last_commit_sha": _git_or_empty(repo_dir, "rev-parse", "HEAD"),
    }


def _repo_key(repo_name: str) -> str:
    if "/" in repo_name:
        return repo_name.rsplit("/", 1)[-1].lower()
    return (repo_name or "codebase").lower()


def _parse_file(rel_path: str, full_path: str) -> dict:
    try:
        with open(full_path, "rb") as f:
            content = f.read().decode("utf-8")

        language = get_file_language(rel_path)
        if language == 'python':
            imports = extract_imports_python(content)
        elif language in ('javascript', 'typescript', 'tsx', 'jsx'):
            imports = extract_imports_js(content)
        else:
            imports = []

        return {
            "language": language,
            "size_lines": len(content.splitlines()),
            "imports": imports,
            "content": content,
        }
    except Exception as e:
        return {
            "language": get_file_language(rel_path),
            "size_lines": 0,
            "imports": [],
            "content": f"[Could not read: {str(e)}]",
        }


def _write_meta(f, key: str, value) -> None:
    json.dump(key, f)
    f.write(": ")
    json.dump(value, f)
    f.write(",\n")


def parse_repo(repo_dir: str, output_dir: str, description: str = "") -> tuple:
    repo_info = get_repo_info(repo_dir)
    repo_name = repo_info["repo_name"]
    repo_key = _repo_key(repo_name)

    os.makedirs(output_dir, exist_ok=True)
    full_json = os.path.join(output_dir, f"codebase_{repo_key}.json")
    light_json = os.path.join(output_dir, f"codebase_{repo_key}_lightweight.json")

    # First pass: collect the lightweight file tree (path strings only).
    file_tree = [rel for rel, _ in iter_repo_files(repo_dir)]

    files_metadata = {}
    with open(full_json, "w", encoding="utf-8") as f:
        f.write("{\n")
        f.write("  ")
        _write_meta(f, "repo_name", repo_name)
        f.write("  ")
        _write_meta(f, "description", description)
        f.write("  ")
        _write_meta(f, "default_branch", repo_info["default_branch"])
        f.write("  ")
        _write_meta(f, "last_commit_sha", repo_info["last_commit_sha"])

        f.write('  "file_tree": [\n')
        for i, path in enumerate(file_tree):
            f.write("    ")
            json.dump(path, f)
            f.write(",\n" if i < len(file_tree) - 1 else "\n")
        f.write('  ],\n  "files": {\n')

        # Second pass: stream each file's entry straight into the JSON on disk.
        written = 0
        for rel_path, full_path in iter_repo_files(repo_dir):
            entry = _parse_file(rel_path, full_path)
            files_metadata[rel_path] = {
                "language": entry["language"],
                "size_lines": entry["size_lines"],
                "imports": entry["imports"],
            }
            if written:
                f.write(",\n")
            f.write("    ")
            json.dump(rel_path, f)
            f.write(": ")
            json.dump(entry, f)
            written += 1

        f.write("\n  }\n}\n")

    # Validate the full JSON is well-formed before returning.
    with open(full_json, "r", encoding="utf-8") as f:
        json.load(f)

    lightweight = {
        "repo_name": repo_name,
        "description": description,
        "default_branch": repo_info["default_branch"],
        "last_commit_sha": repo_info["last_commit_sha"],
        "file_tree": file_tree,
        "files_metadata": files_metadata,
    }
    with open(light_json, "w", encoding="utf-8") as f:
        json.dump(lightweight, f, indent=2)

    print(f"Parsed '{repo_name}' at commit {repo_info['last_commit_sha']} "
          f"({written} files, branch {repo_info['default_branch']}).")
    return full_json, light_json


def upload_to_supabase(full_json: str, light_json: str, repo_name: str, sha: str = "") -> bool:
    """Upload the generated codebase JSON files to the Supabase storage bucket.

    Bucket + paths (see repo_storage):
        repo-contexts/{owner}/{repo}/{sha}/codebase.json
        repo-contexts/{owner}/{repo}/{sha}/codebase_lightweight.json

    `sha` is the commit the index was built from. If omitted it is read from
    the generated JSON's own "last_commit_sha".

    Returns False (without uploading anything) if the full JSON exceeds the
    50 MB cap, since neither file is stored in that case.
    """
    try:
        check_size_limit(full_json)
    except Exception as e:
        print(f"Skipping Supabase upload: {e}")
        return False

    try:
        sha = resolve_sha(sha, full_json, light_json)
        owner, repo = split_repo(repo_name)
        for local_path, remote_path in (
            (full_json, storage_path_json(owner, repo, sha)),
            (light_json, storage_path_light_json(owner, repo, sha)),
        ):
            upload(local_path, remote_path, "application/json")
            print(f"Uploaded -> {BUCKET}/{remote_path}")
        return True
    except Exception as e:
        print(f"Supabase upload failed: {e}")
        return False


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repo_dir", nargs="?",
        default=os.path.join(script_dir, "cloned_repo_4"),
        help="Path to the locally cloned repo (default: ./cloned_repo_4)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(script_dir, "codebases"),
        help="Where to save the generated JSON files (default: ./codebases)",
    )
    parser.add_argument("--description", default="", help="Optional repo description")
    args = parser.parse_args()

    if not os.path.isdir(args.repo_dir):
        print(f"Cloned repo not found: {args.repo_dir}")
        return 1

    full_json, light_json = parse_repo(args.repo_dir, args.output_dir, args.description)
    print(f"Saved full index      -> {full_json}")
    print(f"Saved lightweight     -> {light_json}")

    # Upload both generated files to the Supabase storage bucket, filed under
    # this commit's SHA so other users on a different commit can't collide.
    repo_info = get_repo_info(args.repo_dir)
    upload_to_supabase(
        full_json,
        light_json,
        repo_info["repo_name"],
        repo_info["last_commit_sha"],
    )

    # Parse is done; drop the cloned repo so it doesn't pile up locally.
    shutil.rmtree(args.repo_dir, ignore_errors=True)
    print(f"Deleted cloned repo: {args.repo_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())