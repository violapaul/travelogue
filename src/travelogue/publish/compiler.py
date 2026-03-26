"""Content compiler: resolve layered narratives and build the render graph.

Precedence for narrative content:
  1. final_override (from overrides/*.md)
  2. human_source (from journals/)
  3. ai_draft (from Gemini)
  4. fallback (deterministic title from date/time)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from travelogue.db import connect


def _resolve_narrative(
    db_path: Path,
    trip_id: str,
    scope_type: str,
    scope_id: str,
) -> str:
    """Return the best available narrative text for a given scope."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT block_type, content_markdown FROM narrative_blocks
            WHERE trip_id=? AND scope_type=? AND scope_id=?
            ORDER BY CASE block_type
                WHEN 'final_override' THEN 1
                WHEN 'human_source' THEN 2
                WHEN 'ai_draft' THEN 3
                ELSE 4
            END""",
            (trip_id, scope_type, scope_id),
        ).fetchall()
    if rows:
        return rows[0]["content_markdown"]
    return ""


def _resolve_title(
    db_path: Path,
    trip_id: str,
    scope_type: str,
    scope_id: str,
    fallback: str,
    override_dir: Path | None = None,
) -> str:
    """Resolve title: check override YAML, then AI artifact, then fallback."""
    # Check YAML override
    if override_dir:
        yaml_path = override_dir / f"{scope_id}.yaml"
        if yaml_path.exists():
            import yaml
            try:
                data = yaml.safe_load(yaml_path.read_text()) or {}
                if "title" in data:
                    return data["title"]
            except Exception:
                pass

    # Check AI artifact for title
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT output_json FROM ai_artifacts
            WHERE trip_id=? AND subject_type=? AND subject_id=?
            AND task_type IN ('summarize_day', 'label_event')
            AND status='ok'
            ORDER BY created_at DESC LIMIT 1""",
            (trip_id, scope_type, scope_id),
        ).fetchone()
    if row:
        try:
            data = json.loads(row["output_json"])
            if "title" in data:
                return data["title"]
        except Exception:
            pass

    # Check DB title field
    with connect(db_path) as conn:
        if scope_type == "day":
            row = conn.execute("SELECT title FROM days WHERE id=?", (scope_id,)).fetchone()
        elif scope_type == "event":
            row = conn.execute("SELECT title FROM events WHERE id=?", (scope_id,)).fetchone()
        else:
            row = None
    if row and row["title"]:
        return row["title"]

    return fallback


def build_trip_context(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: Any,
) -> dict[str, Any]:
    """Build the full render context for the trip."""
    with connect(db_path) as conn:
        trip_row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        days = [dict(r) for r in conn.execute(
            "SELECT * FROM days WHERE trip_id=? ORDER BY local_date", (trip_id,)
        ).fetchall()]
        total_assets = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE trip_id=?", (trip_id,)
        ).fetchone()[0]
        places = [dict(r) for r in conn.execute(
            "SELECT * FROM places WHERE trip_id=?", (trip_id,)
        ).fetchall()]

    day_contexts = []
    for day in days:
        day_id = day["id"]
        narrative = _resolve_narrative(db_path, trip_id, "day", day_id)
        title = _resolve_title(db_path, trip_id, "day", day_id, day["local_date"],
                               trip_dir / "overrides" / "days")

        with connect(db_path) as conn:
            events = [dict(r) for r in conn.execute(
                "SELECT * FROM events WHERE day_id=? ORDER BY order_index", (day_id,)
            ).fetchall()]

        event_contexts = []
        for event in events:
            event_id = event["id"]
            ev_narrative = _resolve_narrative(db_path, trip_id, "event", event_id)
            ev_title = _resolve_title(db_path, trip_id, "event", event_id,
                                      f"Event {event['order_index'] + 1}",
                                      trip_dir / "overrides" / "events")

            with connect(db_path) as conn:
                # Get representative assets (non-duplicate or first of cluster)
                asset_rows = conn.execute(
                    """SELECT a.id, a.thumbnail_path, a.web_path, a.gps_lat, a.gps_lon,
                              a.capture_time_local
                    FROM assets a
                    JOIN event_assets ea ON ea.asset_id = a.id
                    WHERE ea.event_id=?
                    ORDER BY a.capture_time_local""",
                    (event_id,),
                ).fetchall()

                subject_clusters = [dict(r) for r in conn.execute(
                    """SELECT id, label, cluster_type, hero_asset_id
                    FROM subject_clusters
                    WHERE scope_type='event' AND scope_id=?""",
                    (event_id,),
                ).fetchall()]

                # Get near-duplicate cluster info for this event's assets
                dup_reps = conn.execute(
                    """SELECT ndc.representative_asset_id, COUNT(*) as alt_count
                    FROM near_duplicate_clusters ndc
                    JOIN near_duplicate_cluster_members ndm ON ndm.cluster_id = ndc.id
                    JOIN event_assets ea ON ea.asset_id = ndm.asset_id
                    WHERE ea.event_id=? AND ndc.trip_id=?
                    GROUP BY ndc.representative_asset_id""",
                    (event_id, trip_id),
                ).fetchall()
                dup_map = {r["representative_asset_id"]: r["alt_count"] for r in dup_reps}

            # Build asset list: prefer representatives, skip non-representative duplicates
            all_asset_ids = {r["id"] for r in asset_rows}
            with connect(db_path) as conn:
                suppressed = conn.execute(
                    """SELECT ndm.asset_id FROM near_duplicate_cluster_members ndm
                    JOIN near_duplicate_clusters ndc ON ndc.id = ndm.cluster_id
                    WHERE ndc.trip_id=? AND ndm.is_representative=0
                    AND ndm.asset_id IN ({})""".format(",".join("?" * len(all_asset_ids))),
                    (trip_id, *all_asset_ids),
                ).fetchall() if all_asset_ids else []
            suppressed_ids = {r["asset_id"] for r in suppressed}

            display_assets = []
            for ar in asset_rows:
                aid = ar["id"]
                if aid in suppressed_ids:
                    continue
                display_assets.append({
                    "id": aid,
                    "thumbnail_path": ar["thumbnail_path"],
                    "web_path": ar["web_path"],
                    "gps_lat": ar["gps_lat"],
                    "gps_lon": ar["gps_lon"],
                    "alt_count": dup_map.get(aid, 0),
                })

            hero = display_assets[0] if display_assets else None
            if event.get("hero_asset_id"):
                hero_candidates = [a for a in display_assets if a["id"] == event["hero_asset_id"]]
                if hero_candidates:
                    hero = hero_candidates[0]

            event_contexts.append({
                "id": event_id,
                "title": ev_title,
                "narrative": ev_narrative,
                "start_time": event["start_time_local"],
                "end_time": event["end_time_local"],
                "assets": display_assets,
                "hero": hero,
                "subject_clusters": subject_clusters,
                "asset_count": len(asset_rows),
            })

        day_contexts.append({
            "id": day_id,
            "date": day["local_date"],
            "title": title,
            "narrative": narrative,
            "events": event_contexts,
        })

    return {
        "trip_id": trip_id,
        "trip_title": dict(trip_row)["title"] if trip_row else trip_id,
        "trip_timezone": dict(trip_row)["timezone"] if trip_row else "UTC",
        "days": day_contexts,
        "places": places,
        "total_assets": total_assets,
        "trip_dir": str(trip_dir),
    }


def generate_review_report(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
) -> Path:
    """Generate a markdown review report of low-confidence items."""
    lines = [
        f"# Review Report: {trip_id}\n",
        f"Generated: {datetime.now().isoformat()}\n\n",
    ]

    with connect(db_path) as conn:
        # Days without journal
        days_no_journal = conn.execute(
            """SELECT d.local_date FROM days d
            WHERE d.trip_id=?
            AND NOT EXISTS (
                SELECT 1 FROM journal_sources j
                WHERE j.trip_id=? AND j.parsed_date=d.local_date
            )
            ORDER BY d.local_date""",
            (trip_id, trip_id),
        ).fetchall()

        # Low-confidence events (no title from AI)
        events_no_title = conn.execute(
            "SELECT id, start_time_local FROM events WHERE trip_id=? AND (title IS NULL OR title='')",
            (trip_id,),
        ).fetchall()

        # Assets missing GPS
        no_gps = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE trip_id=? AND (gps_lat IS NULL OR gps_lon IS NULL)",
            (trip_id,),
        ).fetchone()[0]

        # Large duplicate clusters (might need manual review)
        large_clusters = conn.execute(
            """SELECT ndc.id, COUNT(*) as member_count
            FROM near_duplicate_clusters ndc
            JOIN near_duplicate_cluster_members ndm ON ndm.cluster_id = ndc.id
            WHERE ndc.trip_id=?
            GROUP BY ndc.id
            HAVING COUNT(*) >= 5
            ORDER BY COUNT(*) DESC""",
            (trip_id,),
        ).fetchall()

    if days_no_journal:
        lines.append("## Days without journal entries\n")
        for row in days_no_journal:
            lines.append(f"- {row['local_date']}\n")
        lines.append("\n")

    if events_no_title:
        lines.append("## Events without AI-generated titles\n")
        for row in events_no_title:
            lines.append(f"- `{row['id']}` at {row['start_time_local']}\n")
        lines.append("\n")

    lines.append(f"## Assets missing GPS\n- {no_gps} assets have no GPS coordinates\n\n")

    if large_clusters:
        lines.append("## Large near-duplicate clusters (review recommended)\n")
        for row in large_clusters:
            lines.append(f"- Cluster `{row['id']}`: {row['member_count']} images\n")
        lines.append("\n")

    report_dir = trip_dir / "generated" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "review_report.md"
    report_path.write_text("".join(lines))
    return report_path
