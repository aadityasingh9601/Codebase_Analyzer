import requests
import base64
from github import Github  # pip install PyGithub
from github import Auth
from dotenv import load_dotenv
import os
import ast
import re
import json

load_dotenv()
github_token: str = os.getenv("GITHUB_TOKEN")

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
    except:
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
    structured = {
        "repo_name": repo.full_name,
        "description": repo.description or "",
        "default_branch": repo.default_branch,
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

def build_lightweight_context(codebase: dict) -> dict:
    """Only metadata in initial context — no file contents"""
    return {
        "repo_name": codebase["repo_name"],
        "description": codebase["description"],
        "file_tree": codebase["file_tree"],
        "files_metadata": {
            path: {
                "language": data["language"],
                "size_lines": data["size_lines"],
                "imports": data["imports"]
                # NO content here
            }
            for path, data in codebase["files"].items()
        }
    }

# "https://github.com/aadityasingh9601/Adobe-UI-clone"
# "https://github.com/aadityasingh9601/Paytm"
# "https://github.com/aadityasingh9601/ChatPdf"

repo_url = "https://github.com/aadityasingh9601/paytm"

codebase_data = fetch_repo_as_structured_json(repo_url, github_token)
print(codebase_data)

# Save
with open(f"codebase_paytm_lightweight.json", "w") as f:
    json.dump(build_lightweight_context(codebase_data), f, indent=2)

with open(f"codebase_paytm.json", "w") as f:
    json.dump(codebase_data, f, indent=2)