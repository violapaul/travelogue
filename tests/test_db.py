"""Tests for the database schema and helpers."""

import tempfile
from pathlib import Path

from travelogue.db import connect, get_db_path, init_db


def test_init_db_creates_tables():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "working" / "travelogue.db"
        init_db(db_path)
        assert db_path.exists()

        with connect(db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        expected = {
            "trips", "people", "assets", "asset_features",
            "near_duplicate_clusters", "near_duplicate_cluster_members",
            "days", "events", "event_assets", "places", "place_assets",
            "subject_clusters", "subject_cluster_assets",
            "journal_sources", "narrative_blocks", "ai_artifacts",
        }
        assert expected.issubset(tables)


def test_connect_context_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "working" / "test.db"
        init_db(db_path)
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO trips (id, title, timezone) VALUES (?,?,?)",
                ("t1", "Test Trip", "UTC"),
            )
        with connect(db_path) as conn:
            row = conn.execute("SELECT title FROM trips WHERE id='t1'").fetchone()
        assert row["title"] == "Test Trip"
