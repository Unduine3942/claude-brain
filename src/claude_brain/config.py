"""Application-wide constants and paths."""

import os
from pathlib import Path

APP_NAME = "ClaudeBrain"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
DB_PATH = APP_DIR / "claude_brain.db"
LOG_DIR = APP_DIR / "logs"
CONSOLIDATIONS_DIR = APP_DIR / "consolidations"

PROJECTS_ROOT_DEFAULT = Path.home() / "Desktop" / "Scripting_Projects"


def claude_projects_dir() -> Path:
    """Where Claude Code keeps session transcripts.

    Resolved per call rather than at import, so it follows the real home
    directory instead of whichever one happened to exist when this module
    was first imported.
    """
    return Path.home() / ".claude" / "projects"


def claude_sessions_dir() -> Path:
    """Where the ECC plugin writes its per-project-per-day .tmp digests."""
    return Path.home() / ".claude" / "sessions"


VAULT_TEMPLATES_DIR = Path(__file__).parent / "resources" / "vault_templates"

REFRESH_INTERVAL_MS = 30_000  # 30 seconds

# Sentinel dark theme palette
COLORS = {
    "bg": "#090E14",
    "sidebar": "#070B10",
    "panel": "#101820",
    "panel_alt": "#13202B",
    "panel_deep": "#182633",
    "border": "#243849",
    "text": "#E6EEF5",
    "muted": "#8DA2B3",
    "cyan": "#00D5FF",
    "green": "#43FF9B",
    "amber": "#FFB020",
    "red": "#FF4D5E",
    "blue": "#4C8DFF",
    "select": "#1E3A4A",
    "entry_focus": "#00D5FF",
}

FONTS = {
    "body": ("Segoe UI", 9),
    "bold": ("Segoe UI", 9, "bold"),
    "heading": ("Segoe UI", 10, "bold"),
    "title": ("Segoe UI", 11, "bold"),
    "metric": ("Segoe UI", 22, "bold"),
    "caption": ("Segoe UI", 8),
    "mono": ("Consolas", 9),
}

PAD_SM = 4
PAD_MD = 8
PAD_LG = 16
PAD_XL = 24
