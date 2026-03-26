"""Compute Gemini Embedding 2 vectors for all un-embedded assets."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

from travelogue.config import TripConfig
from travelogue.db import connect

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-2-preview"
EMBEDDING_DIM = 3072
RETRY_DELAYS = [2, 5, 15]
DEFAULT_WORKERS = 8


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Export it before running: export GEMINI_API_KEY=your_key"
        )
    return genai.Client(api_key=api_key)


def _progress(msg: str) -> None:
    """Print flushed progress line (survives conda run buffering)."""
    print(msg, file=sys.stderr, flush=True)


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


def _resolve_image_path(row: dict, trip_dir: Path) -> Path | None:
    for col in ("web_path", "thumbnail_path", "source_path"):
        p = row[col]
        if p:
            candidate = trip_dir / p if not Path(p).is_absolute() else Path(p)
            if candidate.exists():
                return candidate
    return None


def compute_embeddings(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
    max_workers: int = DEFAULT_WORKERS,
) -> int:
    """Compute embeddings for all assets that don't have one yet. Returns count embedded."""
    model = EMBEDDING_MODEL
    client = _get_client()

    with connect(db_path) as conn:
        total_assets = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE trip_id = ?", (trip_id,)
        ).fetchone()[0]

        cached = conn.execute(
            """SELECT COUNT(*) FROM asset_features af
            JOIN assets a ON a.id = af.asset_id
            WHERE a.trip_id = ? AND af.embedding IS NOT NULL AND af.embedding_model = ?""",
            (trip_id, model),
        ).fetchone()[0]

        rows = conn.execute(
            """SELECT a.id, a.web_path, a.thumbnail_path, a.source_path
            FROM assets a
            LEFT JOIN asset_features af ON af.asset_id = a.id
            WHERE a.trip_id = ?
            AND (af.embedding IS NULL OR af.embedding_model IS NULL OR af.embedding_model != ?)""",
            (trip_id, model),
        ).fetchall()

    need = len(rows)
    _progress(f"Embeddings: {total_assets} assets total, {cached} cached, {need} to compute")

    if need == 0:
        _progress("All assets already embedded — nothing to do")
        return 0

    # Resolve image paths upfront and filter out missing files
    work_items: list[tuple[str, Path]] = []
    skipped = 0
    for row in rows:
        img_path = _resolve_image_path(dict(row), trip_dir)
        if img_path:
            work_items.append((row["id"], img_path))
        else:
            log.warning("No image file found for asset %s — skipping", row["id"])
            skipped += 1

    _progress(f"Computing {len(work_items)} embeddings with {max_workers} workers...")

    lock = threading.Lock()
    done = 0
    embedded_count = 0
    error_count = skipped
    t0 = time.monotonic()

    def _do_one(asset_id: str, img_path: Path) -> tuple[str, np.ndarray | None]:
        return asset_id, _embed_one(client, img_path, model)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_do_one, aid, p): (aid, p)
            for aid, p in work_items
        }

        for future in as_completed(futures):
            asset_id, vec = future.result()

            with lock:
                done += 1
                if vec is not None:
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
                else:
                    error_count += 1

                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(work_items) - done) / rate if rate > 0 else 0
                _progress(
                    f"  [{done}/{len(work_items)}] "
                    f"{done * 100 // len(work_items)}% "
                    f"({rate:.1f}/s, ~{eta:.0f}s remaining)"
                )

    elapsed = time.monotonic() - t0
    _progress(
        f"Embedding complete — embedded: {embedded_count}, errors: {error_count}, "
        f"time: {elapsed:.1f}s"
    )
    return embedded_count


def load_embedding(embedding_blob: bytes) -> np.ndarray:
    return np.frombuffer(embedding_blob, dtype=np.float32).copy()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
