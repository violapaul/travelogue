"""Subject/semantic clustering using HDBSCAN on Gemini embeddings.

Within each event scope, cluster visually similar assets into subject groups.
Labels are assigned later by the AI enrichment step (Phase 4).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from pathlib import Path

import numpy as np
from rich.console import Console

from travelogue.db import connect
from travelogue.analysis.embeddings import load_embedding, cosine_similarity
from travelogue.analysis.dedup import _quality_score

MIN_CLUSTER_SIZE = 3   # HDBSCAN minimum cluster size
MIN_SAMPLES = 2        # HDBSCAN min_samples


def cluster_subjects(
    trip_id: str,
    db_path: Path,
    console: Console | None = None,
) -> int:
    """Run HDBSCAN on embeddings within each event to find subject clusters.
    Returns total number of subject clusters created."""
    if console is None:
        console = Console()

    try:
        import hdbscan
    except ImportError:
        console.print("  [yellow]hdbscan not installed; skipping subject clustering.[/yellow]")
        console.print("  Install with: pip install hdbscan")
        return 0

    # Load events for this trip
    with connect(db_path) as conn:
        events = conn.execute(
            "SELECT id FROM events WHERE trip_id=?", (trip_id,)
        ).fetchall()

    if not events:
        console.print("  No events found; run 'travelogue analyze all' first.")
        return 0

    console.print(f"  Subject clustering across {len(events)} events…")

    # Clear existing subject clusters
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM subject_cluster_assets WHERE cluster_id IN "
            "(SELECT id FROM subject_clusters WHERE trip_id=?)", (trip_id,)
        )
        conn.execute("DELETE FROM subject_clusters WHERE trip_id=?", (trip_id,))

    total_clusters = 0

    for event_row in events:
        event_id = event_row["id"]

        # Load assets for this event with embeddings
        with connect(db_path) as conn:
            rows = conn.execute(
                """SELECT a.id, a.width, a.height, af.blur_score, af.embedding
                FROM assets a
                JOIN event_assets ea ON ea.asset_id = a.id
                LEFT JOIN asset_features af ON af.asset_id = a.id
                WHERE ea.event_id = ?
                AND af.embedding IS NOT NULL""",
                (event_id,),
            ).fetchall()

        if len(rows) < MIN_CLUSTER_SIZE * 2:
            continue  # Not enough assets for meaningful clustering

        asset_ids = [r["id"] for r in rows]
        embeddings = np.array([
            load_embedding(r["embedding"]) for r in rows
        ], dtype=np.float32)

        # Normalize for cosine clustering
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        embeddings_norm = embeddings / norms

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=MIN_CLUSTER_SIZE,
            min_samples=MIN_SAMPLES,
            metric="euclidean",  # on normalized vectors, euclidean ≈ cosine
        )
        labels = clusterer.fit_predict(embeddings_norm)

        cluster_map: dict[int, list[int]] = defaultdict(list)
        for idx, label in enumerate(labels):
            if label >= 0:
                cluster_map[label].append(idx)

        for label_id, indices in cluster_map.items():
            members = [asset_ids[i] for i in indices]
            member_rows = [rows[i] for i in indices]

            # Pick representative: best quality score
            def qscore(row: dict) -> float:
                return _quality_score(
                    row.get("blur_score"), row.get("width"), row.get("height")
                )

            best_idx = max(range(len(member_rows)), key=lambda i: qscore(dict(member_rows[i])))
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

    console.print(f"  [green]Done.[/green] Created {total_clusters} subject clusters.")
    return total_clusters
