"""AI artifact caching.

Each AI call is keyed by (trip_id, subject_type, subject_id, provider, task_type,
input_hash, prompt_hash). If a matching artifact exists with status='ok', the cached
output is returned without making an API call.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from travelogue.db import connect


def make_hash(data: Any) -> str:
    """Stable SHA-256 hash of JSON-serializable data."""
    serialized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def get_cached(
    db_path: Path,
    trip_id: str,
    subject_type: str,
    subject_id: str,
    provider: str,
    task_type: str,
    input_hash: str,
    prompt_hash: str,
) -> dict[str, Any] | None:
    """Return cached output_json if a matching artifact exists, else None."""
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT output_json FROM ai_artifacts
            WHERE trip_id=? AND subject_type=? AND subject_id=?
            AND provider=? AND task_type=? AND input_hash=? AND prompt_hash=?
            AND status='ok'""",
            (trip_id, subject_type, subject_id, provider, task_type, input_hash, prompt_hash),
        ).fetchone()
    if row:
        try:
            return json.loads(row["output_json"])
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def save_artifact(
    db_path: Path,
    trip_id: str,
    subject_type: str,
    subject_id: str,
    provider: str,
    model: str,
    task_type: str,
    input_hash: str,
    prompt_hash: str,
    output: dict[str, Any],
    status: str = "ok",
) -> None:
    """Persist an AI artifact to the database."""
    artifact_id = f"ai_{uuid.uuid4().hex[:12]}"
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO ai_artifacts
            (id, trip_id, subject_type, subject_id, provider, model, task_type,
             prompt_hash, input_hash, output_json, created_at, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trip_id, subject_type, subject_id, provider, task_type, input_hash, prompt_hash)
            DO UPDATE SET output_json=excluded.output_json, status=excluded.status,
                          created_at=excluded.created_at, model=excluded.model""",
            (
                artifact_id, trip_id, subject_type, subject_id,
                provider, model, task_type,
                prompt_hash, input_hash,
                json.dumps(output),
                datetime.now(timezone.utc).isoformat(),
                status,
            ),
        )
