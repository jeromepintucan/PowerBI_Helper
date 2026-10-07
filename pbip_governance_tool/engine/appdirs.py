"""
Single source of truth for "where does this app live on disk" - and there
are two different answers to that, which matters a lot for a PyInstaller
--onefile build:

  - app_base_dir()      - the real, persistent location: the .exe's own
                           folder (or the project root, running from
                           source). Backups and app.log MUST live here,
                           since they need to survive after the app closes
                           and be easy for the user to find next to the exe
                           they double-clicked.
  - resource_base_dir() - where bundled, read-only resources actually sit
                           at runtime: ui/index.html and config/rule_config.
                           json. For --onedir or running from source this is
                           the same as app_base_dir(). For --onefile, the
                           exe self-extracts its bundled data into a
                           temporary folder (sys._MEIPASS) on every launch -
                           a NEW one each run, and deleted afterwards - so
                           this is the only place those files can be found
                           at runtime, and nothing should ever be written
                           there (it won't persist).
"""
from __future__ import annotations
import os
import sys


def app_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # this file lives at <project_root>/engine/appdirs.py
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_base_dir() -> str:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return app_base_dir()
