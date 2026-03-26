"""Gemini generative AI enrichment.

Calls Gemini to generate day summaries, event titles/summaries,
subject cluster labels, and optional draft journal entries.

All outputs are cached in ai_artifacts; re-runs are free if inputs haven't changed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel
from rich.console import Console
from rich.progress import track

from travelogue.ai.cache import get_cached, make_hash, save_artifact
from travelogue.config import TripConfig
from travelogue.db import connect

PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-2.0-flash"
RETRY_DELAYS = [2, 5, 15]

# Prompt version — bump to invalidate all cached AI outputs
PROMPT_VERSION = "v1"


class DaySummary(BaseModel):
    title: str
    summary: str


class EventLabel(BaseModel):
    title: str
    summary: str
    event_type: str = ""


class SubjectLabel(BaseModel):
    label: str
    cluster_type: str  # wildlife/food/landscape/architecture/transportation/people/other


class JournalDraft(BaseModel):
    draft: str


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set.")
    return genai.Client(api_key=api_key)


def _call_gemini(
    client: genai.Client,
    model: str,
    prompt: str,
    schema: type[BaseModel],
    images: list[Path] | None = None,
) -> dict[str, Any] | None:
    """Call Gemini with structured output. Returns parsed dict or None on failure."""
    parts = []
    if images:
        for img_path in images[:4]:  # limit to 4 images per call
            try:
                with open(img_path, "rb") as f:
                    data = f.read()
                suffix = img_path.suffix.lower()
                mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
                parts.append(genai_types.Part(
                    inline_data=genai_types.Blob(mime_type=mime, data=data)
                ))
            except Exception:
                pass
    parts.append(genai_types.Part(text=prompt))

    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            response = client.models.generate_content(
                model=model,
                contents=genai_types.Content(parts=parts),
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.4,
                ),
            )
            return json.loads(response.text)
        except Exception as exc:
            if attempt == len(RETRY_DELAYS):
                return None
    return None


def _summarize_day(
    client: genai.Client,
    model: str,
    db_path: Path,
    trip_id: str,
    day: dict,
    trip_dir: Path,
    force: bool = False,
) -> None:
    """Generate and cache a day summary."""
    day_id = day["id"]
    local_date = day["local_date"]

    # Build input context
    with connect(db_path) as conn:
        events = conn.execute(
            "SELECT id, title, start_time_local, end_time_local FROM events WHERE day_id=? ORDER BY order_index",
            (day_id,),
        ).fetchall()
        places = conn.execute(
            """SELECT DISTINCT p.name FROM places p
            JOIN place_assets pa ON pa.place_id = p.id
            JOIN event_assets ea ON ea.asset_id = pa.asset_id
            JOIN events e ON e.id = ea.event_id
            WHERE e.day_id=?""",
            (day_id,),
        ).fetchall()
        journal = conn.execute(
            """SELECT raw_markdown FROM journal_sources
            WHERE trip_id=? AND parsed_date=?""",
            (trip_id, local_date),
        ).fetchone()
        # Get a few representative thumbnail paths
        thumb_rows = conn.execute(
            """SELECT a.thumbnail_path FROM assets a
            JOIN event_assets ea ON ea.asset_id = a.id
            JOIN events e ON e.id = ea.event_id
            WHERE e.day_id=? AND a.thumbnail_path IS NOT NULL
            LIMIT 4""",
            (day_id,),
        ).fetchall()

    context = {
        "date": local_date,
        "event_count": len(events),
        "place_names": [p["name"] for p in places],
        "has_journal": journal is not None,
    }
    input_hash = make_hash(context)
    prompt_hash = make_hash({"task": "summarize_day", "version": PROMPT_VERSION})

    if not force:
        cached = get_cached(db_path, trip_id, "day", day_id, PROVIDER, "summarize_day", input_hash, prompt_hash)
        if cached:
            return

    place_str = ", ".join(p["name"] for p in places) if places else "unknown location"
    journal_excerpt = (journal["raw_markdown"][:500] + "…") if journal else "(no journal for this day)"

    prompt = f"""You are writing a travelogue. Generate a concise, evocative title and 2-3 sentence summary for this travel day.

Date: {local_date}
Places visited: {place_str}
Number of events: {len(events)}
Journal notes: {journal_excerpt}

Return JSON with fields: title (short, evocative, 3-8 words), summary (2-3 sentences, vivid and specific)."""

    images = [
        trip_dir / r["thumbnail_path"]
        for r in thumb_rows
        if (trip_dir / r["thumbnail_path"]).exists()
    ]

    result = _call_gemini(client, model, prompt, DaySummary, images=images)
    if result:
        save_artifact(
            db_path, trip_id, "day", day_id, PROVIDER, model,
            "summarize_day", input_hash, prompt_hash, result
        )
        # Write as AI draft narrative block
        _upsert_narrative_block(
            db_path, trip_id, "day", day_id, "ai_draft",
            result.get("summary", ""),
            title=result.get("title", ""),
        )


def _label_event(
    client: genai.Client,
    model: str,
    db_path: Path,
    trip_id: str,
    event: dict,
    trip_dir: Path,
    force: bool = False,
) -> None:
    """Generate and cache an event title and summary."""
    event_id = event["id"]

    with connect(db_path) as conn:
        asset_count = conn.execute(
            "SELECT COUNT(*) FROM event_assets WHERE event_id=?", (event_id,)
        ).fetchone()[0]
        place = conn.execute(
            """SELECT p.name FROM places p
            JOIN place_assets pa ON pa.place_id = p.id
            JOIN event_assets ea ON ea.asset_id = pa.asset_id
            WHERE ea.event_id=? LIMIT 1""",
            (event_id,),
        ).fetchone()
        thumb_rows = conn.execute(
            """SELECT a.thumbnail_path FROM assets a
            JOIN event_assets ea ON ea.asset_id = a.id
            WHERE ea.event_id=? AND a.thumbnail_path IS NOT NULL
            LIMIT 4""",
            (event_id,),
        ).fetchall()

    context = {
        "event_id": event_id,
        "start": event["start_time_local"],
        "end": event["end_time_local"],
        "asset_count": asset_count,
        "place_name": place["name"] if place else None,
    }
    input_hash = make_hash(context)
    prompt_hash = make_hash({"task": "label_event", "version": PROMPT_VERSION})

    if not force:
        cached = get_cached(db_path, trip_id, "event", event_id, PROVIDER, "label_event", input_hash, prompt_hash)
        if cached:
            return

    place_str = place["name"] if place else "unknown location"
    prompt = f"""You are writing a travelogue. Generate a concise title and brief summary for this travel event (a cluster of photos taken in one place/session).

Time: {event['start_time_local']} to {event['end_time_local']}
Location: {place_str}
Photo count: {asset_count}

Look at the attached photos and return JSON with:
- title (2-5 words, descriptive and evocative)
- summary (1-2 sentences)
- event_type (one of: meal, hike, sightseeing, transit, accommodation, activity, other)"""

    images = [
        trip_dir / r["thumbnail_path"]
        for r in thumb_rows
        if (trip_dir / r["thumbnail_path"]).exists()
    ]

    result = _call_gemini(client, model, prompt, EventLabel, images=images)
    if result:
        save_artifact(
            db_path, trip_id, "event", event_id, PROVIDER, model,
            "label_event", input_hash, prompt_hash, result
        )
        # Update event title and type in DB
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE events SET title=?, event_type=? WHERE id=?",
                (result.get("title", ""), result.get("event_type", ""), event_id),
            )
        _upsert_narrative_block(
            db_path, trip_id, "event", event_id, "ai_draft",
            result.get("summary", ""),
        )


def _label_subject_cluster(
    client: genai.Client,
    model: str,
    db_path: Path,
    trip_id: str,
    cluster: dict,
    trip_dir: Path,
    force: bool = False,
) -> None:
    """Generate a subject cluster label."""
    cluster_id = cluster["id"]

    with connect(db_path) as conn:
        thumb_rows = conn.execute(
            """SELECT a.thumbnail_path FROM assets a
            JOIN subject_cluster_assets sca ON sca.asset_id = a.id
            WHERE sca.cluster_id=? AND a.thumbnail_path IS NOT NULL
            LIMIT 4""",
            (cluster_id,),
        ).fetchall()
        member_count = conn.execute(
            "SELECT COUNT(*) FROM subject_cluster_assets WHERE cluster_id=?",
            (cluster_id,),
        ).fetchone()[0]

    context = {"cluster_id": cluster_id, "member_count": member_count}
    input_hash = make_hash(context)
    prompt_hash = make_hash({"task": "label_subject", "version": PROMPT_VERSION})

    if not force:
        cached = get_cached(db_path, trip_id, "subject_cluster", cluster_id, PROVIDER, "label_subject", input_hash, prompt_hash)
        if cached:
            return

    prompt = f"""Look at these {member_count} photos from a travel trip. They were grouped together because they are visually similar.

Give this group a short, descriptive label (2-4 words, e.g. "Koala sightings", "Harbor dining", "Mountain views") and classify the type.

Return JSON with:
- label (2-4 words)
- cluster_type (one of: wildlife, food, landscape, architecture, transportation, people, other)"""

    images = [
        trip_dir / r["thumbnail_path"]
        for r in thumb_rows
        if (trip_dir / r["thumbnail_path"]).exists()
    ]

    result = _call_gemini(client, model, prompt, SubjectLabel, images=images)
    if result:
        save_artifact(
            db_path, trip_id, "subject_cluster", cluster_id, PROVIDER, model,
            "label_subject", input_hash, prompt_hash, result
        )
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE subject_clusters SET label=?, cluster_type=? WHERE id=?",
                (result.get("label", ""), result.get("cluster_type", "other"), cluster_id),
            )


def _upsert_narrative_block(
    db_path: Path,
    trip_id: str,
    scope_type: str,
    scope_id: str,
    block_type: str,
    content: str,
    title: str = "",
) -> None:
    """Insert or update a narrative block, respecting the locked flag."""
    import uuid as _uuid
    from datetime import datetime, timezone

    with connect(db_path) as conn:
        existing = conn.execute(
            """SELECT id, locked FROM narrative_blocks
            WHERE trip_id=? AND scope_type=? AND scope_id=? AND block_type=?""",
            (trip_id, scope_type, scope_id, block_type),
        ).fetchone()

        if existing:
            if existing["locked"]:
                return  # Never overwrite locked (human-edited) content
            conn.execute(
                """UPDATE narrative_blocks SET content_markdown=?, updated_at=?
                WHERE id=?""",
                (content, datetime.now(timezone.utc).isoformat(), existing["id"]),
            )
        else:
            block_id = f"nb_{_uuid.uuid4().hex[:10]}"
            conn.execute(
                """INSERT INTO narrative_blocks
                (id, trip_id, scope_type, scope_id, block_type, content_markdown, updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (block_id, trip_id, scope_type, scope_id, block_type, content,
                 datetime.now(timezone.utc).isoformat()),
            )


def enrich_trip(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
    force: bool = False,
    console: Console | None = None,
) -> None:
    """Run all AI enrichment tasks for a trip."""
    if console is None:
        console = Console()

    client = _get_client()
    gemini_cfg = cfg.ai.providers.get("gemini")
    model = gemini_cfg.model if gemini_cfg else DEFAULT_MODEL

    tasks = cfg.ai.tasks

    # Days
    if tasks.summarize_days:
        with connect(db_path) as conn:
            days = [dict(r) for r in conn.execute(
                "SELECT id, local_date FROM days WHERE trip_id=? ORDER BY local_date",
                (trip_id,),
            ).fetchall()]
        console.print(f"  Summarizing {len(days)} days…")
        for day in track(days, description="  Days…", console=console):
            _summarize_day(client, model, db_path, trip_id, day, trip_dir, force=force)

    # Events
    if tasks.summarize_events:
        with connect(db_path) as conn:
            events = [dict(r) for r in conn.execute(
                "SELECT id, start_time_local, end_time_local FROM events WHERE trip_id=?",
                (trip_id,),
            ).fetchall()]
        console.print(f"  Labeling {len(events)} events…")
        for event in track(events, description="  Events…", console=console):
            _label_event(client, model, db_path, trip_id, event, trip_dir, force=force)

    # Subject clusters
    if tasks.label_subjects:
        with connect(db_path) as conn:
            clusters = [dict(r) for r in conn.execute(
                "SELECT id FROM subject_clusters WHERE trip_id=?", (trip_id,)
            ).fetchall()]
        console.print(f"  Labeling {len(clusters)} subject clusters…")
        for cluster in track(clusters, description="  Clusters…", console=console):
            _label_subject_cluster(client, model, db_path, trip_id, cluster, trip_dir, force=force)

    console.print("[green]AI enrichment complete.[/green]")
