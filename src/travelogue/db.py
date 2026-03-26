"""SQLite schema and thin query helpers.

Design principles:
- No ORM. Plain sqlite3 with explicit SQL.
- Pydantic models handle validation; this module handles persistence.
- All write operations are idempotent (INSERT OR REPLACE or INSERT OR IGNORE).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    start_date TEXT,
    end_date TEXT,
    description TEXT DEFAULT '',
    config_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS people (
    id TEXT NOT NULL,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    display_name TEXT NOT NULL,
    source_label TEXT NOT NULL,
    attribution_mode TEXT DEFAULT 'minimal',
    PRIMARY KEY (id, trip_id)
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    person_id TEXT,
    source_path TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    capture_time_original TEXT,
    capture_time_utc TEXT,
    capture_time_local TEXT,
    gps_lat REAL,
    gps_lon REAL,
    mime_type TEXT DEFAULT 'image/jpeg',
    width INTEGER,
    height INTEGER,
    orientation INTEGER DEFAULT 1,
    exif_json TEXT DEFAULT '{}',
    thumbnail_path TEXT,
    web_path TEXT,
    original_relpath TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_checksum_trip
    ON assets(trip_id, checksum_sha256);

CREATE TABLE IF NOT EXISTS asset_features (
    asset_id TEXT PRIMARY KEY REFERENCES assets(id),
    phash TEXT,
    blur_score REAL,
    exposure_score REAL,
    embedding BLOB,       -- numpy float32 array bytes
    embedding_model TEXT, -- e.g. "gemini-embedding-2"
    scene_tags_json TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS near_duplicate_clusters (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    representative_asset_id TEXT NOT NULL REFERENCES assets(id),
    confidence REAL DEFAULT 1.0,
    method TEXT DEFAULT 'phash+embedding',
    rationale TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS near_duplicate_cluster_members (
    cluster_id TEXT NOT NULL REFERENCES near_duplicate_clusters(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    similarity_score REAL DEFAULT 1.0,
    is_representative INTEGER DEFAULT 0,
    PRIMARY KEY (cluster_id, asset_id)
);

CREATE TABLE IF NOT EXISTS days (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    local_date TEXT NOT NULL,
    title TEXT DEFAULT '',
    order_index INTEGER DEFAULT 0,
    UNIQUE (trip_id, local_date)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    day_id TEXT NOT NULL REFERENCES days(id),
    start_time_local TEXT,
    end_time_local TEXT,
    title TEXT DEFAULT '',
    place_id TEXT REFERENCES places(id),
    event_type TEXT DEFAULT '',
    confidence REAL DEFAULT 1.0,
    hero_asset_id TEXT REFERENCES assets(id),
    order_index INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_assets (
    event_id TEXT NOT NULL REFERENCES events(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    PRIMARY KEY (event_id, asset_id)
);

CREATE TABLE IF NOT EXISTS places (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    name TEXT NOT NULL,
    place_type TEXT DEFAULT 'other',
    lat REAL,
    lon REAL,
    address TEXT DEFAULT '',
    external_links_json TEXT DEFAULT '{}',
    confidence REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS place_assets (
    place_id TEXT NOT NULL REFERENCES places(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    PRIMARY KEY (place_id, asset_id)
);

CREATE TABLE IF NOT EXISTS subject_clusters (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    label TEXT DEFAULT '',
    cluster_type TEXT DEFAULT 'unknown',
    confidence REAL DEFAULT 1.0,
    hero_asset_id TEXT REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS subject_cluster_assets (
    cluster_id TEXT NOT NULL REFERENCES subject_clusters(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    PRIMARY KEY (cluster_id, asset_id)
);

CREATE TABLE IF NOT EXISTS journal_sources (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    path TEXT NOT NULL,
    raw_markdown TEXT NOT NULL,
    parsed_date TEXT,
    date_confidence REAL DEFAULT 0.0,
    heading_structure_json TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS narrative_blocks (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    block_type TEXT NOT NULL,
    content_markdown TEXT NOT NULL,
    locked INTEGER DEFAULT 0,
    source_ref TEXT DEFAULT '',
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_narrative_blocks_scope
    ON narrative_blocks(trip_id, scope_type, scope_id, block_type);

CREATE TABLE IF NOT EXISTS ai_artifacts (
    id TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_json TEXT DEFAULT '{}',
    created_at TEXT,
    status TEXT DEFAULT 'ok'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_artifacts_key
    ON ai_artifacts(trip_id, subject_type, subject_id, provider, task_type, input_hash, prompt_hash);
"""


def get_db_path(trips_root: Path, trip_id: str) -> Path:
    return trips_root / trip_id / "working" / "travelogue.db"


def init_db(db_path: Path) -> None:
    """Create the database and all tables if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for a SQLite connection with sensible defaults."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
