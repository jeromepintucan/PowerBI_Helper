from __future__ import annotations
import os
import shutil
from datetime import datetime

from .appdirs import app_base_dir

# Root of the app itself (the .exe's own folder when packaged, or the
# pbip_governance_tool project root otherwise). Backups live here, NOT
# inside the scanned PBIP project, so running this tool never leaves
# anything behind in your project folder.
BACKUPS_DIR = os.path.join(app_base_dir(), "backups")


def backup_files(root_path: str, file_paths: list[str]) -> str:
    """Copy each file (preserving its path relative to root_path) into a
    timestamped backup folder under this app's own backups/ directory -
    never inside the scanned project. Returns the backup folder path. Safe
    to call with duplicate paths (deduped)."""
    root_path = os.path.abspath(root_path)
    project_name = os.path.basename(root_path.rstrip("\\/")) or "project"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = os.path.join(BACKUPS_DIR, f"{project_name}_{stamp}")
    os.makedirs(backup_root, exist_ok=True)

    for fp in sorted(set(file_paths)):
        fp = os.path.abspath(fp)
        try:
            rel = os.path.relpath(fp, root_path)
        except ValueError:
            rel = os.path.basename(fp)
        if rel.startswith(".."):
            rel = os.path.basename(fp)
        dest = os.path.join(backup_root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isfile(fp):
            shutil.copy2(fp, dest)

    return backup_root
