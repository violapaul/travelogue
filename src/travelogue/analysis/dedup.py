"""Near-duplicate clustering.

Two-tier approach:
1. Fast phash pre-filter: find candidate pairs with hamming distance < PHASH_THRESHOLD
   within the same day (to reduce false positives across unrelated days).
2. Embedding cosine similarity: refine candidates; mark pairs with similarity > EMBED_THRESHOLD
   as near-duplicates.

Connected components of near-duplicate pairs form clusters.
Within each cluster, pick the best representative by a weighted quality score.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import imagehash
import numpy as np
from rich.console import Console

from travelogue.db import connect
from travelogue.analysis.embeddings import load_embedding, cosine_similarity

PHASH_THRESHOLD = 12       # Hamming distance (0=identical, 64=max)
EMBED_THRESHOLD = 0.92     # Cosine similarity for near-duplicate
TIME_WINDOW_HOURS = 2.0    # Only compare assets within this time window


def _phash_distance(h1: str, h2: str) -> int:
    try:
        return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)
    except Exception:
        return 64


def _quality_score(blur: float | None, width: int | None, height: int | None) -> float:
    """Higher is better. Used to pick the representative from a cluster."""
    score = 0.0
    if blur is not None:
        # Normalize blur score: higher variance = sharper
        score += min(blur / 5000.0, 1.0) * 0.6
    if width and height:
        # Prefer larger images (up to 20 MP saturation)
        mp = (width * height) / 20_000_000
        score += min(mp, 1.0) * 0.4
    return score


def cluster_duplicates(
    trip_id: str,
    db_path: Path,
    console: Console | None = None,
) -> int:
    """Compute near-duplicate clusters. Returns count of clusters created."""
    if console is None:
        console = Console()

    # Load all assets with features for this trip
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT a.id, a.capture_time_local, a.width, a.height,
                      af.phash, af.blur_score, af.embedding
               FROM assets a
               LEFT JOIN asset_features af ON af.asset_id = a.id
               WHERE a.trip_id = ?
               ORDER BY a.capture_time_local""",
            (trip_id,),
        ).fetchall()

    assets = [dict(r) for r in rows]
    n = len(assets)
    console.print(f"  Clustering {n} assets for duplicates…")

    if n == 0:
        return 0

    # Build union-find structure
    parent = {a["id"]: a["id"] for a in assets}
    sim_scores: dict[tuple[str, str], float] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str, score: float) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx
        key = (min(x, y), max(x, y))
        sim_scores[key] = max(sim_scores.get(key, 0.0), score)

    # Phase 1: phash pre-filter
    # For efficiency, only compare assets within a time window
    from datetime import datetime, timedelta

    def parse_time(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    times = [parse_time(a["capture_time_local"]) for a in assets]
    window = timedelta(hours=TIME_WINDOW_HOURS)

    candidate_pairs: list[tuple[int, int]] = []
    for i in range(n):
        if not assets[i]["phash"]:
            continue
        ti = times[i]
        for j in range(i + 1, n):
            if not assets[j]["phash"]:
                continue
            tj = times[j]
            # Time window check (skip if both have times and are far apart)
            if ti and tj and abs(ti - tj) > window:
                break  # list is sorted by time, no need to look further
            dist = _phash_distance(assets[i]["phash"], assets[j]["phash"])
            if dist <= PHASH_THRESHOLD:
                candidate_pairs.append((i, j))

    console.print(f"  Phase 1 (phash): {len(candidate_pairs)} candidate pairs")

    # Phase 2: embedding refinement
    confirmed = 0
    for i, j in candidate_pairs:
        ai, aj = assets[i], assets[j]
        blob_i = ai.get("embedding")
        blob_j = aj.get("embedding")

        if blob_i and blob_j:
            vi = load_embedding(blob_i)
            vj = load_embedding(blob_j)
            sim = cosine_similarity(vi, vj)
        else:
            # No embeddings yet: fall back to phash-only decision
            dist = _phash_distance(ai["phash"], aj["phash"])
            sim = 1.0 - (dist / 64.0)

        if sim >= EMBED_THRESHOLD:
            union(ai["id"], aj["id"], sim)
            confirmed += 1

    console.print(f"  Phase 2 (embedding): {confirmed} confirmed near-duplicate pairs")

    # Build clusters from union-find
    from collections import defaultdict
    clusters: dict[str, list[str]] = defaultdict(list)
    for a in assets:
        root = find(a["id"])
        clusters[root].append(a["id"])

    # Only keep clusters with > 1 member (singletons are not duplicates)
    dup_clusters = {root: members for root, members in clusters.items() if len(members) > 1}
    console.print(f"  Found {len(dup_clusters)} near-duplicate clusters")

    # Build asset lookup for quality scoring
    asset_by_id = {a["id"]: a for a in assets}

    # Clear existing clusters for this trip
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM near_duplicate_cluster_members WHERE cluster_id IN "
            "(SELECT id FROM near_duplicate_clusters WHERE trip_id=?)",
            (trip_id,),
        )
        conn.execute(
            "DELETE FROM near_duplicate_clusters WHERE trip_id=?", (trip_id,)
        )

    # Write new clusters
    cluster_count = 0
    for root, members in dup_clusters.items():
        # Pick representative: highest quality score
        def qscore(aid: str) -> float:
            a = asset_by_id[aid]
            return _quality_score(a.get("blur_score"), a.get("width"), a.get("height"))

        representative = max(members, key=qscore)

        # Average similarity score across pairs in the cluster
        avg_sim = 1.0
        pair_sims = [
            sim_scores.get((min(a, b), max(a, b)), 1.0)
            for a in members for b in members if a < b
        ]
        if pair_sims:
            avg_sim = sum(pair_sims) / len(pair_sims)

        cluster_id = f"nd_{uuid.uuid4().hex[:10]}"
        with connect(db_path) as conn:
            conn.execute(
                """INSERT INTO near_duplicate_clusters
                (id, trip_id, representative_asset_id, confidence, method, rationale)
                VALUES (?,?,?,?,?,?)""",
                (cluster_id, trip_id, representative, avg_sim, "phash+embedding",
                 f"{len(members)} images, avg_sim={avg_sim:.3f}"),
            )
            for member_id in members:
                conn.execute(
                    """INSERT INTO near_duplicate_cluster_members
                    (cluster_id, asset_id, similarity_score, is_representative)
                    VALUES (?,?,?,?)""",
                    (
                        cluster_id, member_id,
                        sim_scores.get((min(member_id, representative), max(member_id, representative)), 1.0),
                        1 if member_id == representative else 0,
                    ),
                )
        cluster_count += 1

    console.print(f"  [green]Done.[/green] Wrote {cluster_count} duplicate clusters.")
    return cluster_count
