import subprocess
import shutil
import os
from pathlib import Path

def clone_repo(repo_url: str, target_dir: str = "./cloned_repo_3") -> str:
    """
    Clone a GitHub repo using shallow clone.
    Returns the path to cloned repo.
    """
    
    # Clean up if already exists
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    
    print(f"Cloning {repo_url}...")
    
    result = subprocess.run(
        [
            "git", "clone",
            "--depth", "1",        # shallow clone — latest commit only
            "--single-branch",     # only default branch, no others
            "--no-tags",           # skip tags, saves time
            repo_url,
            target_dir
        ],
        capture_output=True,
        text=True,
        timeout=120               # fail after 2 minutes, not hang forever
    )
    
    if result.returncode != 0:
        raise Exception(f"Clone failed: {result.stderr}")
    
    print(f"Cloned successfully to {target_dir}")
    return target_dir


repo_path = clone_repo("https://github.com/aadityasingh9601/Adobe-UI-clone", "./cloned_repo_4")
print(repo_path)

# "https://github.com/fastapi/fastapi"
# "https://github.com/calcom/cal.diy"
# https://github.com/ToolJet/ToolJet
# "https://github.com/aadityasingh9601/ToolJet"
# https://github.com/aadityasingh9601/Paytm
# "https://github.com/aadityasingh9601/Adobe-UI-clone"
# "https://github.com/aadityasingh9601/Paytm"
# "https://github.com/aadityasingh9601/ChatPdf"
# "https://github.com/aaditysingh9601/Jarvis"