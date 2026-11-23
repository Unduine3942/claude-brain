"""Shared fixtures.

Tests run against an in-memory database and synthetic transcripts. Nothing here
touches the real `%LOCALAPPDATA%` store or `~/.claude`.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from claude_brain.db import migrations
from claude_brain.db.repos import Repos


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory database with the schema applied."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    migrations.run(connection)
    yield connection
    connection.close()


@pytest.fixture
def repos(conn: sqlite3.Connection) -> Repos:
    return Repos(conn)


@pytest.fixture
def make_transcript(tmp_path):
    """Factory writing a JSONL transcript shaped like a real one.

    Mirrors the record types actually observed: metadata records that carry no
    message, both `sessionId` and `session_id` casings, and tool_use blocks
    nested in assistant content.
    """

    def _make(
        name: str = "11111111-2222-3333-4444-555555555555",
        *,
        cwd: str | None = None,
        prompts: tuple[str, ...] = ("do the thing",),
        edits: tuple[str, ...] = (),
        timestamps: tuple[str, ...] = ("2026-05-01T10:00:00.000Z", "2026-05-01T12:00:00.000Z"),
        sidechain: bool = False,
        subdir: str = "C--Users-test-Desktop-Demo",
        extra_records: tuple[dict, ...] = (),
        malformed: int = 0,
        snake_case_id: bool = False,
    ):
        project_dir = tmp_path / "projects" / subdir
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / f"{name}.jsonl"

        id_key = "session_id" if snake_case_id else "sessionId"
        base = {id_key: name, "isSidechain": sidechain}
        if cwd is not None:
            base["cwd"] = cwd

        records: list[dict] = [
            # A metadata record with no message, like `mode` or `last-prompt`.
            {**base, "type": "mode", "timestamp": timestamps[0]},
        ]
        for prompt in prompts:
            records.append(
                {
                    **base,
                    "type": "user",
                    "timestamp": timestamps[0],
                    "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
                }
            )
        if edits:
            records.append(
                {
                    **base,
                    "type": "assistant",
                    "timestamp": timestamps[-1],
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": f"tu_{i}",
                                "name": "Edit",
                                "input": {"file_path": target},
                            }
                            for i, target in enumerate(edits)
                        ],
                    },
                }
            )
        records.extend({**base, **rec} for rec in extra_records)
        # Real transcripts end with further records, so the closing timestamp is
        # always present even in a session that made no edits.
        records.append({**base, "type": "mode", "timestamp": timestamps[-1]})

        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
            for _ in range(malformed):
                handle.write("{not valid json\n")
        return path

    return _make
