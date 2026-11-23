"""Application bootstrap — wires together DB, repos, and UI.

Ingest no longer lives here. It moved to :mod:`claude_brain.core.ingest` so that
hooks and the CLI can reach it: this module imports tkinter at module scope, so
anything defined here is unreachable from a headless process.
"""

import logging
import tkinter as tk

from claude_brain import config
from claude_brain.core.ingest import ingest_all, relink_unlinked
from claude_brain.db.repos import Repos, open_repos
from claude_brain.ui import theme
from claude_brain.ui.main_window import MainWindow

log = logging.getLogger(__name__)

__all__ = ["Repos", "run"]


def run() -> None:
    _configure_logging()
    repos = open_repos()
    _startup_ingest(repos)

    root = tk.Tk()
    theme.apply(root)

    def on_settings():
        from claude_brain.ui.settings_dialog import SettingsDialog

        SettingsDialog(root, repos)

    window = MainWindow(root, repos, on_settings)
    _start_refresh(root, window, repos)

    log.info("Claude Brain started.")
    root.mainloop()


# ---- startup helpers ---------------------------------------------------


def _startup_ingest(repos: Repos) -> None:
    """Catch up on anything the hooks missed.

    This is a convenience, not the capture mechanism: hooks keep the store
    current whether or not this window is ever opened. Previously it was the
    *only* ingest path, which is why the database went two months stale.
    """
    try:
        result = ingest_all(repos)
        log.info("startup ingest: %s", result)
        linked = relink_unlinked(repos)
        if linked:
            log.info("relinked %d previously unlinked session(s)", linked)
    except Exception as exc:
        log.warning("startup ingest failed: %s", exc)


def _start_refresh(root: tk.Tk, window: MainWindow, repos: Repos) -> None:
    def _refresh():
        try:
            vault_count = repos.vaults.count()
            window.refresh_status(vault_count)
        except Exception:
            pass
        root.after(config.REFRESH_INTERVAL_MS, _refresh)

    root.after(2000, _refresh)


def _configure_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_DIR / "claude_brain.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
