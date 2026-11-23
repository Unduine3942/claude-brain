"""Claude Brain — durable, queryable memory across Claude Code sessions."""

from __future__ import annotations

try:
    # Written at build time by hatch-vcs; absent in a bare source tree.
    from claude_brain._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
