"""Subject/semantic clustering using HDBSCAN on Gemini embeddings."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from pathlib import Path

import numpy as np

from travelogue.db import connect
from travelogue.analysis.embeddings import load_embedding
from travelogue.analysis.dedup import _quality_score

log = logging.getLogger(__name__)

MIN_CLUSTER_SIZE = 3
MIN_SAMPLES = 2


def cluster_subjects(trip_id: str, db_path: Path) -> int:
    """Run HDBSCAN on embeddings within each event to find subject clusters."""
    try:
        import hdbscan
    except ImportError:
        log.warning("hdbscan not installed — skipping subject clustering (pip install hdbscan)")
        return 0

    with connect(db_path) as conn:
        events = conn.execute("SELECT id FROM events WHERE trip_id=?", (trip_id,)).fetchall()

    if not events:
        log.warning("No events found — run 'travelogue analyze all' first")
        return 0

    log.info("Subject clustering across %d events", len(events))

    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM subject_cluster_assets WHERE cluster_id IN "
            "(SELECT id FROM subject_clusters WHERE trip_id=?)", (trip_id,)
        )
        conn.execute("DELETE FROM subject_clusters WHERE trip_id=?", (trip_id,))

    total_clusters = 0
    skipped_events = 0

    for event_row in events:
        event_id = event_row["id"]

        with connect(db_path) as conn:
            rows = conn.execute(
                """SELECT a.id, a.width, a.height, af.blur_score, af.embedding
                FROM assets a
                JOIN event_assets ea ON ea.asset_id = a.id
                LEFT JOIN asset_features af ON af.asset_id = a.id
                WHERE ea.event_id = ? AND af.embedding IS NOT NULL""",
                (event_id,),
            ).fetchall()

        if len(rows) < MIN_CLUSTER_SIZE * 2:
            log.debug("Event %s: only %d embedded assets — skipping subject clustering", event_id, len(rows))
            skipped_events += 1
            continue

        asset_ids = [r["id"] for r in rows]
        embeddings = np.array([load_embedding(r["embedding"]) for r in rows], dtype=np.float32)

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        embeddings_norm = embeddings / norms

        labels = hdbscan.HDBSCAN(
            min_cluster_size=MIN_CLUSTER_SIZE,
            min_samples=MIN_SAMPLES,
            metric="euclidean",
        ).fit_predict(embeddings_norm)

        cluster_map: dict[int, list[int]] = defaultdict(list)
        for idx, label in enumerate(labels):
            if label >= 0:
                cluster_map[label].append(idx)

        if cluster_map:
            log.debug("Event %s: %d subject clusters from %d assets", event_id, len(cluster_map), len(rows))

        for label_id, indices in cluster_map.items():
            members = [asset_ids[i] for i in indices]
            member_rows = [rows[i] for i in indices]

            best_idx = max(range(len(member_rows)), key=lambda i: _quality_score(
                dict(member_rows[i]).get("blur_score"), dict(member_rows[i]).get("width"), dict(member_rows[i]).get("height")
            ))
            hero_id = members[best_idx]

            cluster_id = f"sc_{uuid.uuid4().hex[:10]}"
            with connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO subject_clusters
                    (id, trip_id, scope_type, scope_id, label, cluster_type, confidence, hero_asset_id)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (cluster_id, trip_id, "event", event_id, "", "unknown", 0.7, hero_id),
                )
                for member_id in members:
                    conn.execute(
                        "INSERT OR IGNORE INTO subject_cluster_assets (cluster_id, asset_id) VALUES (?,?)",
                        (cluster_id, member_id),
                    )
            total_clusters += 1

    log.info(
        "Subject clustering complete — %d clusters created (%d events skipped, too few embedded assets)",
        total_clusters, skipped_events,
    )
    return total_clusters
