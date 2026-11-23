"""Schema migrations.

Re-exports the runner so existing callers keep working:
``from claude_brain.db import migrations; migrations.run(conn)``.
"""

from claude_brain.db.migrations.runner import (
    Migration,
    MigrationError,
    applied,
    discover,
    run,
    verify,
)

__all__ = ["Migration", "MigrationError", "applied", "discover", "run", "verify"]
