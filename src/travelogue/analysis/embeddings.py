"""Compute Gemini Embedding 2 vectors for all un-embedded assets."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

from travelogue.config import TripConfig
from travelogue.db import connect

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-exp-03-07"
EMBEDDING_DIM = 3072
RETRY_DELAYS = [2, 5, 15]


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Export it before running: export GEMINI_API_KEY=your_key"
        )
    return genai.Client(api_key=api_key)


def _embed_one(client: genai.Client, img_path: Path, model: str) -> np.ndarray | None:
    suffix = img_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

    with open(img_path, "rb") as f:
        image_bytes = f.read()

    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime)

    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            log.debug("Retry %d for %s (waiting %ds)", attempt, img_path.name, delay)
            time.sleep(delay)
        try:
            response = client.models.embed_content(
                model=model,
                contents=image_part,
            )
            return np.array(response.embeddings[0].values, dtype=np.float32)
        except Exception as exc:
            if attempt == len(RETRY_DELAYS):
                log.error("Embedding failed for %s after %d attempts: %s", img_path.name, attempt + 1, exc)
                return None
            log.warning("Embedding attempt %d failed for %s: %s", attempt + 1, img_path.name, exc)
    return None


def compute_embeddings(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
) -> int:
    """Compute embeddings for all assets that don't have one yet. Returns count embedded."""
    model = EMBEDDING_MODEL
    client = _get_client()

    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT a.id, a.web_path, a.thumbnail_path, a.source_path
            FROM assets a
            LEFT JOIN asset_features af ON af.asset_id = a.id
            WHERE a.trip_id = ?
            AND (af.embedding IS NULL OR af.embedding_model IS NULL OR af.embedding_model != ?)""",
            (trip_id, model),
        ).fetchall()

    total = len(rows)
    if total == 0:
        log.info("All assets already embedded — nothing to do")
        return 0

    log.info("Embedding %d assets using %s", total, model)
    embedded_count = 0
    error_count = 0
    log_every = max(1, total // 20)

    for idx, row in enumerate(rows):
        asset_id = row["id"]

        img_path = None
        for col in ("web_path", "thumbnail_path", "source_path"):
            p = row[col]
            if p:
                candidate = trip_dir / p if not Path(p).is_absolute() else Path(p)
                if candidate.exists():
                    img_path = candidate
                    break

        if idx % log_every == 0:
            log.info("[%d/%d] %s", idx + 1, total, img_path.name if img_path else asset_id)

        if not img_path:
            log.warning("No image file found for asset %s — skipping", asset_id)
            error_count += 1
            continue

        vec = _embed_one(client, img_path, model)
        if vec is None:
            error_count += 1
            continue

        with connect(db_path) as conn:
            conn.execute(
                """INSERT INTO asset_features (asset_id, embedding, embedding_model)
                VALUES (?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    embedding = excluded.embedding,
                    embedding_model = excluded.embedding_model""",
                (asset_id, vec.tobytes(), model),
            )
        embedded_count += 1
        log.debug("  Embedded %s (dim=%d)", asset_id, len(vec))

    log.info("Embedding complete — embedded: %d, errors: %d", embedded_count, error_count)
    return embedded_count


def load_embedding(embedding_blob: bytes) -> np.ndarray:
    return np.frombuffer(embedding_blob, dtype=np.float32).copy()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
