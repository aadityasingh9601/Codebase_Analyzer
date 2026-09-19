import base64
import os

import requests  # noqa: F401  (kept for parity with historical imports)
from dotenv import load_dotenv
from github import Auth, Github  # pip install PyGithub

from local_repo_index import (
    build_lightweight_context,
    extract_imports_js,
    extract_imports_python,
    get_file_language,
    should_skip,
)

load_dotenv()
github_token: str = os.getenv("GITHUB_TOKEN")

def get_repo_commit_sha(repo_url: str, github_token: str) -> str:
    """Fetch the latest commit SHA of the default branch for a repo URL."""
    auth = Auth.Token(github_token)
    g = Github(auth=auth)
    parts = repo_url.replace("https://github.com/", "").rstrip("/").split("/")
    owner, repo_name = parts[0], parts[1]
    repo = g.get_repo(f"{owner}/{repo_name}")
    return repo.get_branch(repo.default_branch).commit.sha


def fetch_repo_as_structured_json(repo_url: str, github_token: str) -> dict:
    auth = Auth.Token(github_token)
    g = Github(auth=auth)
    parts = repo_url.replace("https://github.com/", "").rstrip("/").split("/")
    owner, repo_name = parts[0], parts[1]
    repo = g.get_repo(f"{owner}/{repo_name}")

    print(owner)
    print(repo_name)
    print(f"REPO-> {repo}")
    # Get repo metadata
    # Resolve the latest commit SHA of the default branch so we can later do
    # incremental syncs (compare against this SHA to detect repo updates).
    branch = repo.get_branch(repo.default_branch)
    last_commit_sha = branch.commit.sha
    structured = {
        "repo_name": repo.full_name,
        "description": repo.description or "",
        "default_branch": repo.default_branch,
        "last_commit_sha": last_commit_sha,
        "file_tree": [],
        "files": {}
    }
    print(f"Structured -> {structured}")
    # Get all files
    contents = repo.get_git_tree(sha=repo.default_branch, recursive=True)
    print(f"Contents -> {contents}")
    
    for item in contents.tree:
        if should_skip(item.path):
            continue
        
        if item.type == "blob":
            structured["file_tree"].append(item.path)
            
            try:
                file_content = repo.get_contents(item.path)
                decoded = base64.b64decode(file_content.content).decode("utf-8")
                language = get_file_language(item.path)
                
                # Extract imports based on language
                if language == 'python':
                    imports = extract_imports_python(decoded)
                elif language in ['javascript', 'typescript', 'tsx', 'jsx']:
                    imports = extract_imports_js(decoded)
                else:
                    imports = []
                
                structured["files"][item.path] = {
                    "language": language,
                    "size_lines": len(decoded.splitlines()),
                    "imports": imports,
                    "content": decoded
                }
            except Exception as e:
                structured["files"][item.path] = {
                    "language": get_file_language(item.path),
                    "size_lines": 0,
                    "imports": [],
                    "content": f"[Could not read: {str(e)}]"
                }
    
    return structured

# "https://github.com/aadityasingh9601/Adobe-UI-clone"
# "https://github.com/aadityasingh9601/Paytm"
# "https://github.com/aadityasingh9601/ChatPdf"
# "https://github.com/aaditysingh9601/Jarvis"

# Only run the fetch/save flow when this file is executed directly. Guarding it
# under __main__ means importing fetchGithub (e.g. from updateGithubRepo.py) for
# its helper functions does NOT trigger an expensive full-repo download.
if __name__ == "__main__":
    repo_url = "https://github.com/aadityasingh9601/jarvis"

    codebase_data = fetch_repo_as_structured_json(repo_url, github_token)
    print(codebase_data)

    # Save
    with open(f"codebase_jarvis_lightweight.json", "w") as f:
        json.dump(build_lightweight_context(codebase_data), f, indent=2)

    with open(f"codebase_jarvis.json", "w") as f:
        json.dump(codebase_data, f, indent=2)