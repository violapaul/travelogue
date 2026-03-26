"""Compute Gemini Embedding 2 vectors for all un-embedded assets.

Embeddings are stored as raw float32 bytes in the asset_features.embedding column,
keyed by asset checksum so they survive re-ingests.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types as genai_types
from rich.console import Console
from rich.progress import track

from travelogue.config import TripConfig
from travelogue.db import connect

EMBEDDING_MODEL = "gemini-embedding-exp-03-07"  # Gemini Embedding 2 (March 2026)
EMBEDDING_DIM = 3072
BATCH_SIZE = 10  # images per API call
RETRY_DELAYS = [2, 5, 15]  # seconds between retries


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Set it before running: export GEMINI_API_KEY=your_key"
        )
    return genai.Client(api_key=api_key)


def _embed_image_batch(
    client: genai.Client,
    image_paths: list[Path],
    model: str = EMBEDDING_MODEL,
) -> list[np.ndarray | None]:
    """Embed a batch of images. Returns a list of embedding arrays (None on failure)."""
    results: list[np.ndarray | None] = []

    for img_path in image_paths:
        # Embed one image at a time (Gemini Embedding 2 multimodal API)
        for attempt, delay in enumerate([0] + RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                with open(img_path, "rb") as f:
                    image_bytes = f.read()

                # Determine MIME type
                suffix = img_path.suffix.lower()
                mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

                response = client.models.embed_content(
                    model=model,
                    contents=genai_types.Content(
                        parts=[genai_types.Part(
                            inline_data=genai_types.Blob(mime_type=mime, data=image_bytes)
                        )]
                    ),
                )
                vec = np.array(response.embeddings[0].values, dtype=np.float32)
                results.append(vec)
                break
            except Exception as exc:
                if attempt == len(RETRY_DELAYS):
                    results.append(None)
                # else: retry
        else:
            results.append(None)

    return results


def compute_embeddings(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
    console: Console | None = None,
) -> int:
    """Compute embeddings for all assets that don't have one yet. Returns count embedded."""
    if console is None:
        console = Console()

    # Get model from config
    gemini_cfg = cfg.ai.providers.get("gemini")
    model = EMBEDDING_MODEL  # always use embedding model, not the generative model

    client = _get_client()

    # Find assets without embeddings
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT a.id, a.web_path, a.thumbnail_path, a.source_path
            FROM assets a
            LEFT JOIN asset_features af ON af.asset_id = a.id
            WHERE a.trip_id = ?
            AND (af.embedding IS NULL OR af.embedding_model IS NULL OR af.embedding_model != ?)""",
            (trip_id, model),
        ).fetchall()

    if not rows:
        console.print("  All assets already embedded.")
        return 0

    console.print(f"  Embedding {len(rows)} assets with {model}…")
    embedded_count = 0
    error_count = 0
    log_every = max(1, len(rows) // 20)

    for row_idx, row in enumerate(rows):
        asset_id = row["id"]
        # Prefer web derivative; fall back to thumbnail, then source
        img_path = None
        for path_col in ("web_path", "thumbnail_path", "source_path"):
            p = row[path_col]
            if p:
                candidate = trip_dir / p if not Path(p).is_absolute() else Path(p)
                if candidate.exists():
                    img_path = candidate
                    break

        if row_idx % log_every == 0:
            console.print(f"  [{row_idx+1}/{len(rows)}] embedding {img_path.name if img_path else asset_id}…")

        if not img_path:
            console.print(f"  [yellow]No image file found for asset {asset_id}[/yellow]")
            error_count += 1
            continue

        try:
            vecs = _embed_image_batch(client, [img_path], model=model)
            vec = vecs[0]
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

        except Exception as exc:
            console.print(f"  [red]Embedding error for {asset_id}:[/red] {exc}")
            error_count += 1

    console.print(
        f"  [green]Done.[/green] Embedded: {embedded_count}, Errors: {error_count}"
    )
    return embedded_count


def load_embedding(embedding_blob: bytes) -> np.ndarray:
    """Deserialize a stored embedding blob to a float32 numpy array."""
    return np.frombuffer(embedding_blob, dtype=np.float32).copy()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
