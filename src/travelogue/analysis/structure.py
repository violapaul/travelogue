"""Day assignment, event segmentation, and place grouping."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

import numpy as np
from rich.console import Console

from travelogue.config import TripConfig
from travelogue.db import connect

# Segmentation thresholds (configurable via trip.yaml in future)
EVENT_TIME_GAP_MINUTES = 30
EVENT_GPS_GAP_METERS = 500
PLACE_CLUSTER_RADIUS_METERS = 200


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    R = 6_371_000.0
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def _reverse_geocode(lat: float, lon: float) -> str:
    """Return a place name string from GPS coordinates (offline, GeoNames)."""
    try:
        import reverse_geocoder as rg
        results = rg.search((lat, lon), mode=1, verbose=False)
        if results:
            r = results[0]
            parts = [r.get("name", ""), r.get("admin1", ""), r.get("cc", "")]
            return ", ".join(p for p in parts if p)
    except Exception:
        pass
    return f"{lat:.4f}, {lon:.4f}"


def segment_trip(
    trip_id: str,
    db_path: Path,
    cfg: TripConfig,
    console: Console | None = None,
) -> None:
    """Assign assets to days, segment into events, and group into places."""
    if console is None:
        console = Console()

    from zoneinfo import ZoneInfo
    tz = ZoneInfo(cfg.trip.timezone)

    # Load all assets with timestamps
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, capture_time_local, capture_time_utc, gps_lat, gps_lon
               FROM assets WHERE trip_id = ?
               ORDER BY COALESCE(capture_time_local, capture_time_utc)""",
            (trip_id,),
        ).fetchall()

    assets = [dict(r) for r in rows]
    console.print(f"  Segmenting {len(assets)} assets into days and events…")

    # --- Days ---
    _assign_days(trip_id, db_path, assets, console)

    # --- Events ---
    _assign_events(trip_id, db_path, assets, console)

    # --- Places ---
    _assign_places(trip_id, db_path, assets, console)

    console.print("  [green]Segmentation complete.[/green]")


def _assign_days(
    trip_id: str,
    db_path: Path,
    assets: list[dict],
    console: Console,
) -> None:
    """Group assets by local date and create Day records."""
    day_map: dict[date, list[str]] = defaultdict(list)

    for a in assets:
        ts_str = a.get("capture_time_local") or a.get("capture_time_utc")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            day_map[ts.date()].append(a["id"])
        except ValueError:
            continue

    console.print(f"    Days found: {len(day_map)}")

    # Clear existing days for this trip
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM event_assets WHERE event_id IN "
            "(SELECT id FROM events WHERE trip_id=?)", (trip_id,)
        )
        conn.execute("DELETE FROM events WHERE trip_id=?", (trip_id,))
        conn.execute("DELETE FROM days WHERE trip_id=?", (trip_id,))

    day_ids: dict[date, str] = {}
    for i, local_date in enumerate(sorted(day_map.keys())):
        day_id = f"d_{uuid.uuid4().hex[:10]}"
        day_ids[local_date] = day_id
        with connect(db_path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO days (id, trip_id, local_date, order_index)
                VALUES (?,?,?,?)""",
                (day_id, trip_id, local_date.isoformat(), i),
            )


def _assign_events(
    trip_id: str,
    db_path: Path,
    assets: list[dict],
    console: Console,
) -> None:
    """Segment assets into events within each day based on time gaps and GPS jumps."""
    # Re-load day IDs
    with connect(db_path) as conn:
        day_rows = conn.execute(
            "SELECT id, local_date FROM days WHERE trip_id=? ORDER BY local_date",
            (trip_id,),
        ).fetchall()

    day_id_by_date: dict[str, str] = {r["local_date"]: r["id"] for r in day_rows}

    time_gap = timedelta(minutes=EVENT_TIME_GAP_MINUTES)
    event_count = 0

    # Group assets by day
    day_assets: dict[str, list[dict]] = defaultdict(list)
    for a in assets:
        ts_str = a.get("capture_time_local") or a.get("capture_time_utc")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            date_key = ts.date().isoformat()
            if date_key in day_id_by_date:
                day_assets[date_key].append({**a, "_ts": ts})
        except ValueError:
            continue

    for date_str, day_id in day_id_by_date.items():
        day_asset_list = sorted(day_assets.get(date_str, []), key=lambda x: x["_ts"])
        if not day_asset_list:
            continue

        # Segment into events
        current_event: list[dict] = [day_asset_list[0]]

        def flush_event(event_assets_list: list[dict], order: int) -> None:
            nonlocal event_count
            event_id = f"e_{uuid.uuid4().hex[:10]}"
            start = event_assets_list[0]["_ts"]
            end = event_assets_list[-1]["_ts"]
            with connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO events
                    (id, trip_id, day_id, start_time_local, end_time_local, order_index)
                    VALUES (?,?,?,?,?,?)""",
                    (event_id, trip_id, day_id,
                     start.isoformat(), end.isoformat(), order),
                )
                for ea in event_assets_list:
                    conn.execute(
                        "INSERT OR IGNORE INTO event_assets (event_id, asset_id) VALUES (?,?)",
                        (event_id, ea["id"]),
                    )
            event_count += 1

        order = 0
        for a in day_asset_list[1:]:
            prev = current_event[-1]
            ts_gap = a["_ts"] - prev["_ts"]

            # GPS gap check
            gps_gap = 0.0
            if (a.get("gps_lat") and a.get("gps_lon") and
                    prev.get("gps_lat") and prev.get("gps_lon")):
                gps_gap = _haversine(
                    prev["gps_lat"], prev["gps_lon"],
                    a["gps_lat"], a["gps_lon"],
                )

            if ts_gap > time_gap or gps_gap > EVENT_GPS_GAP_METERS:
                flush_event(current_event, order)
                order += 1
                current_event = [a]
            else:
                current_event.append(a)

        if current_event:
            flush_event(current_event, order)

    console.print(f"    Events created: {event_count}")


def _assign_places(
    trip_id: str,
    db_path: Path,
    assets: list[dict],
    console: Console,
) -> None:
    """Cluster GPS coordinates into places using simple radius-based grouping."""
    gps_assets = [a for a in assets if a.get("gps_lat") and a.get("gps_lon")]
    if not gps_assets:
        console.print("    No GPS data found; skipping place grouping.")
        return

    # Simple greedy clustering: assign each asset to existing place within radius,
    # or create a new place.
    from sklearn.cluster import DBSCAN

    coords = np.array([[a["gps_lat"], a["gps_lon"]] for a in gps_assets])
    # DBSCAN with haversine metric; eps in radians (200m / Earth radius)
    eps_rad = PLACE_CLUSTER_RADIUS_METERS / 6_371_000.0
    db = DBSCAN(eps=eps_rad, min_samples=2, algorithm="ball_tree", metric="haversine")
    labels = db.fit_predict(np.radians(coords))

    # Clear existing places
    with connect(db_path) as conn:
        conn.execute("DELETE FROM place_assets WHERE place_id IN (SELECT id FROM places WHERE trip_id=?)", (trip_id,))
        conn.execute("DELETE FROM places WHERE trip_id=?", (trip_id,))

    # Build clusters
    cluster_map: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        if label >= 0:  # -1 = noise
            cluster_map[label].append(idx)

    console.print(f"    GPS clusters found: {len(cluster_map)}")

    for label, indices in cluster_map.items():
        cluster_coords = coords[indices]
        centroid_lat = float(np.mean(cluster_coords[:, 0]))
        centroid_lon = float(np.mean(cluster_coords[:, 1]))

        place_name = _reverse_geocode(centroid_lat, centroid_lon)
        place_id = f"p_{uuid.uuid4().hex[:10]}"

        with connect(db_path) as conn:
            conn.execute(
                """INSERT INTO places
                (id, trip_id, name, place_type, lat, lon, confidence)
                VALUES (?,?,?,?,?,?,?)""",
                (place_id, trip_id, place_name, "other", centroid_lat, centroid_lon, 0.8),
            )
            for idx in indices:
                asset_id = gps_assets[idx]["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO place_assets (place_id, asset_id) VALUES (?,?)",
                    (place_id, asset_id),
                )
