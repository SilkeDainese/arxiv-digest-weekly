"""pytest configuration — add repo root and function directories to sys.path."""
import sys
import types
from pathlib import Path

# ── Mock functions_framework before any function module is imported ────────────
# Cloud Functions require the `functions_framework` package at import time, but it
# is not installed in the test environment. We provide a minimal stub so that
# `@functions_framework.http` decorators are a no-op and the decorated function
# remains callable directly in tests.
if "functions_framework" not in sys.modules:
    _ff = types.ModuleType("functions_framework")
    _ff.http = lambda f: f          # @functions_framework.http → identity decorator
    sys.modules["functions_framework"] = _ff

# Repo root (for shared/)
repo_root = Path(__file__).parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Function directories (for functions.*.main imports)
for func_dir in (repo_root / "functions").iterdir():
    if func_dir.is_dir():
        if str(func_dir.parent) not in sys.path:
            sys.path.insert(0, str(func_dir.parent))
