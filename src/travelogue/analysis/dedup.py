"""Near-duplicate clustering: phash pre-filter + embedding cosine refinement."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import imagehash
import numpy as np

from travelogue.db import connect
from travelogue.analysis.embeddings import load_embedding, cosine_similarity

log = logging.getLogger(__name__)

PHASH_THRESHOLD = 12
EMBED_THRESHOLD = 0.92
TIME_WINDOW_HOURS = 2.0


def _phash_distance(h1: str, h2: str) -> int:
    try:
        return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)
    except Exception:
        return 64


def _quality_score(blur: float | None, width: int | None, height: int | None) -> float:
    score = 0.0
    if blur is not None:
        score += min(blur / 5000.0, 1.0) * 0.6
    if width and height:
        score += min((width * height) / 20_000_000, 1.0) * 0.4
    return score


def cluster_duplicates(trip_id: str, db_path: Path) -> int:
    """Compute near-duplicate clusters. Returns count of clusters created."""
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
    log.info("Clustering %d assets for near-duplicates", n)
    if n == 0:
        return 0

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
        for j in range(i + 1, n):
            if not assets[j]["phash"]:
                continue
            ti, tj = times[i], times[j]
            if ti and tj and abs(ti - tj) > window:
                break
            dist = _phash_distance(assets[i]["phash"], assets[j]["phash"])
            if dist <= PHASH_THRESHOLD:
                candidate_pairs.append((i, j))

    log.info("Phase 1 (phash): %d candidate pairs (threshold=%d)", len(candidate_pairs), PHASH_THRESHOLD)

    confirmed = 0
    for i, j in candidate_pairs:
        ai, aj = assets[i], assets[j]
        blob_i, blob_j = ai.get("embedding"), aj.get("embedding")
        if blob_i and blob_j:
            sim = cosine_similarity(load_embedding(blob_i), load_embedding(blob_j))
            log.debug("  Pair %s/%s: cosine_sim=%.3f", ai["id"], aj["id"], sim)
        else:
            dist = _phash_distance(ai["phash"], aj["phash"])
            sim = 1.0 - (dist / 64.0)
            log.debug("  Pair %s/%s: phash fallback sim=%.3f", ai["id"], aj["id"], sim)

        if sim >= EMBED_THRESHOLD:
            union(ai["id"], aj["id"], sim)
            confirmed += 1

    log.info("Phase 2 (embedding): %d confirmed near-duplicate pairs (threshold=%.2f)", confirmed, EMBED_THRESHOLD)

    clusters: dict[str, list[str]] = defaultdict(list)
    for a in assets:
        clusters[find(a["id"])].append(a["id"])

    dup_clusters = {root: members for root, members in clusters.items() if len(members) > 1}
    log.info("Found %d near-duplicate clusters", len(dup_clusters))

    asset_by_id = {a["id"]: a for a in assets}

    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM near_duplicate_cluster_members WHERE cluster_id IN "
            "(SELECT id FROM near_duplicate_clusters WHERE trip_id=?)", (trip_id,)
        )
        conn.execute("DELETE FROM near_duplicate_clusters WHERE trip_id=?", (trip_id,))

    cluster_count = 0
    for root, members in dup_clusters.items():
        representative = max(members, key=lambda aid: _quality_score(
            asset_by_id[aid].get("blur_score"),
            asset_by_id[aid].get("width"),
            asset_by_id[aid].get("height"),
        ))
        pair_sims = [
            sim_scores.get((min(a, b), max(a, b)), 1.0)
            for a in members for b in members if a < b
        ]
        avg_sim = sum(pair_sims) / len(pair_sims) if pair_sims else 1.0

        log.debug("Cluster of %d: representative=%s avg_sim=%.3f", len(members), representative, avg_sim)

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
                    (cluster_id, member_id,
                     sim_scores.get((min(member_id, representative), max(member_id, representative)), 1.0),
                     1 if member_id == representative else 0),
                )
        cluster_count += 1

    log.info("Dedup complete — wrote %d clusters", cluster_count)
    return cluster_count
