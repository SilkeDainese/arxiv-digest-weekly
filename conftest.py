"""pytest configuration — add repo root and function directories to sys.path."""
import sys
from pathlib import Path

# Repo root (for shared/)
repo_root = Path(__file__).parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Function directories (for functions.*.main imports)
for func_dir in (repo_root / "functions").iterdir():
    if func_dir.is_dir():
        if str(func_dir.parent) not in sys.path:
            sys.path.insert(0, str(func_dir.parent))
