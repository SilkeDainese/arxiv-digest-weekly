"""ISO week utilities used across Cloud Functions."""
from __future__ import annotations

from datetime import datetime, timezone


def current_week_iso() -> str:
    """Return the current ISO week string, e.g. '2026-W15'."""
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def build_function_url(project_id: str, region: str, function_name: str) -> str:
    """Build a Cloud Functions 2nd-gen base URL."""
    return f"https://{region}-{project_id}.cloudfunctions.net/{function_name}"


def build_logs_url(project_id: str, function_name: str) -> str:
    """Build a GCP Cloud Logging URL for a specific function."""
    filter_str = f'resource.type="cloud_run_revision" resource.labels.service_name="{function_name}"'
    import urllib.parse
    return (
        f"https://console.cloud.google.com/logs/query;"
        f"query={urllib.parse.quote(filter_str)}?"
        f"project={project_id}"
    )
