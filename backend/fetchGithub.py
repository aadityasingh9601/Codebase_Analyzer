import requests
import base64
from github import Github  # pip install PyGithub
from github import Auth
from dotenv import load_dotenv
import os

load_dotenv()

github_token: str = os.getenv("GITHUB_TOKEN")


# # Public Web Github
# g = Github(auth=auth)

def fetch_repo_as_text(repo_url: str, github_token: str) -> str:
    auth = Auth.Token(github_token)
    g = Github(auth=auth)
    # Extract owner/repo from URL
    parts = repo_url.replace("https://github.com/", "").split("/")
    owner, repo_name = parts[0], parts[1]
    print(owner)
    print(repo_name)
    repo = g.get_repo(f"{owner}/{repo_name}")
    print(f"REPO-> {repo}")
    # Get all files recursively
    contents = repo.get_git_tree(sha="main", recursive=True)
    # Close connection after usage.
    g.close()
    print(f"Contents -> {contents}")

    full_codebase = {}

    for item in contents.tree:
        # Skip binary files, node_modules, .git, build artifacts
        if should_skip(item.path):
            continue

        if item.type == "blob":
            try:
                file_content = repo.get_contents(item.path)
                code = base64.b64decode(file_content.content).decode("utf-8")
                full_codebase[item.path] = code
            except:
                continue

    return full_codebase

def should_skip(path: str) -> bool:
    skip_patterns = [
        "node_modules", ".git", "__pycache__",
        ".png", ".jpg", ".ico", ".svg", ".lock",
        "dist/", "build/", ".env"
    ]
    return any(p in path for p in skip_patterns)

def format_codebase_for_rlm(codebase: dict) -> str:
    formatted = "CODEBASE STRUCTURE:\n\n"

    # First add file tree
    formatted += "FILE TREE:\n"
    for path in sorted(codebase.keys()):
        formatted += f"{path}\n"

    formatted += "\n\nFILE CONTENTS:\n\n"

    # Then add each file with clear delimiters
    for path, content in codebase.items():
        formatted += f"=== FILE:{path} ===\n"
        formatted += content
        formatted += f"\n=== END:{path} ===\n\n"

    return formatted

response = fetch_repo_as_text("https://github.com/aadityasingh9601/Adobe-UI-clone",github_token)

formatted = format_codebase_for_rlm(response)

with open("response.txt", "w") as f:
    f.write(formatted)
