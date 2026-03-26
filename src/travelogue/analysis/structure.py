"""Day assignment, event segmentation, and place grouping."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from travelogue.config import TripConfig
from travelogue.db import connect

log = logging.getLogger(__name__)

EVENT_TIME_GAP_MINUTES = 30
EVENT_GPS_GAP_METERS = 500
PLACE_CLUSTER_RADIUS_METERS = 200


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def _reverse_geocode(lat: float, lon: float) -> str:
    try:
        import reverse_geocoder as rg
        results = rg.search((lat, lon), mode=1, verbose=False)
        if results:
            r = results[0]
            parts = [r.get("name", ""), r.get("admin1", ""), r.get("cc", "")]
            name = ", ".join(p for p in parts if p)
            log.debug("Reverse geocode (%.4f, %.4f) → %s", lat, lon, name)
            return name
    except Exception as exc:
        log.debug("Reverse geocode failed: %s", exc)
    return f"{lat:.4f}, {lon:.4f}"


def segment_trip(trip_id: str, db_path: Path, cfg: TripConfig) -> None:
    """Assign assets to days, segment into events, and group into places."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(cfg.trip.timezone)

    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, capture_time_local, capture_time_utc, gps_lat, gps_lon
               FROM assets WHERE trip_id = ?
               ORDER BY COALESCE(capture_time_local, capture_time_utc)""",
            (trip_id,),
        ).fetchall()

    assets = [dict(r) for r in rows]
    log.info("Segmenting %d assets into days and events", len(assets))

    assets_with_ts = sum(1 for a in assets if a.get("capture_time_local") or a.get("capture_time_utc"))
    assets_with_gps = sum(1 for a in assets if a.get("gps_lat"))
    log.info("  Assets with timestamps: %d / %d", assets_with_ts, len(assets))
    log.info("  Assets with GPS: %d / %d", assets_with_gps, len(assets))

    _assign_days(trip_id, db_path, assets)
    _assign_events(trip_id, db_path, assets)
    _assign_places(trip_id, db_path, assets)

    log.info("Segmentation complete")


def _assign_days(trip_id: str, db_path: Path, assets: list[dict]) -> None:
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

    log.info("Days found: %d", len(day_map))
    for d, ids in sorted(day_map.items()):
        log.debug("  %s: %d assets", d, len(ids))

    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM event_assets WHERE event_id IN (SELECT id FROM events WHERE trip_id=?)", (trip_id,)
        )
        conn.execute("DELETE FROM events WHERE trip_id=?", (trip_id,))
        conn.execute("DELETE FROM days WHERE trip_id=?", (trip_id,))

    for i, local_date in enumerate(sorted(day_map.keys())):
        day_id = f"d_{uuid.uuid4().hex[:10]}"
        with connect(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO days (id, trip_id, local_date, order_index) VALUES (?,?,?,?)",
                (day_id, trip_id, local_date.isoformat(), i),
            )


def _assign_events(trip_id: str, db_path: Path, assets: list[dict]) -> None:
    with connect(db_path) as conn:
        day_rows = conn.execute(
            "SELECT id, local_date FROM days WHERE trip_id=? ORDER BY local_date", (trip_id,)
        ).fetchall()

    day_id_by_date: dict[str, str] = {r["local_date"]: r["id"] for r in day_rows}
    time_gap = timedelta(minutes=EVENT_TIME_GAP_MINUTES)
    event_count = 0

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
        day_list = sorted(day_assets.get(date_str, []), key=lambda x: x["_ts"])
        if not day_list:
            log.debug("Day %s has no timestamped assets", date_str)
            continue

        current: list[dict] = [day_list[0]]
        order = 0

        def flush(items: list[dict], ord_: int) -> None:
            nonlocal event_count
            eid = f"e_{uuid.uuid4().hex[:10]}"
            with connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO events (id, trip_id, day_id, start_time_local, end_time_local, order_index) VALUES (?,?,?,?,?,?)",
                    (eid, trip_id, day_id, items[0]["_ts"].isoformat(), items[-1]["_ts"].isoformat(), ord_),
                )
                for ea in items:
                    conn.execute(
                        "INSERT OR IGNORE INTO event_assets (event_id, asset_id) VALUES (?,?)",
                        (eid, ea["id"]),
                    )
            log.debug("  Event %s: %d assets, %s – %s", eid, len(items),
                      items[0]["_ts"].strftime("%H:%M"), items[-1]["_ts"].strftime("%H:%M"))
            event_count += 1

        for a in day_list[1:]:
            prev = current[-1]
            ts_gap = a["_ts"] - prev["_ts"]
            gps_gap = 0.0
            if a.get("gps_lat") and prev.get("gps_lat"):
                gps_gap = _haversine(prev["gps_lat"], prev["gps_lon"], a["gps_lat"], a["gps_lon"])

            if ts_gap > time_gap or gps_gap > EVENT_GPS_GAP_METERS:
                if ts_gap > time_gap:
                    log.debug("  Time gap %.0fmin on %s → new event", ts_gap.seconds / 60, date_str)
                if gps_gap > EVENT_GPS_GAP_METERS:
                    log.debug("  GPS jump %.0fm on %s → new event", gps_gap, date_str)
                flush(current, order)
                order += 1
                current = [a]
            else:
                current.append(a)

        if current:
            flush(current, order)

    log.info("Events created: %d", event_count)


def _assign_places(trip_id: str, db_path: Path, assets: list[dict]) -> None:
    from sklearn.cluster import DBSCAN

    gps_assets = [a for a in assets if a.get("gps_lat") and a.get("gps_lon")]
    if not gps_assets:
        log.warning("No GPS data found for any assets — skipping place grouping")
        return

    log.info("Place grouping: %d assets have GPS", len(gps_assets))

    coords = np.array([[a["gps_lat"], a["gps_lon"]] for a in gps_assets])
    eps_rad = PLACE_CLUSTER_RADIUS_METERS / 6_371_000.0
    labels = DBSCAN(eps=eps_rad, min_samples=2, algorithm="ball_tree", metric="haversine").fit_predict(
        np.radians(coords)
    )

    noise = sum(1 for l in labels if l == -1)
    log.info("GPS clusters: %d  (noise points: %d)", len(set(labels)) - (1 if -1 in labels else 0), noise)

    with connect(db_path) as conn:
        conn.execute("DELETE FROM place_assets WHERE place_id IN (SELECT id FROM places WHERE trip_id=?)", (trip_id,))
        conn.execute("DELETE FROM places WHERE trip_id=?", (trip_id,))

    cluster_map: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        if label >= 0:
            cluster_map[label].append(idx)

    for label, indices in cluster_map.items():
        cluster_coords = coords[indices]
        lat = float(np.mean(cluster_coords[:, 0]))
        lon = float(np.mean(cluster_coords[:, 1]))
        place_name = _reverse_geocode(lat, lon)
        place_id = f"p_{uuid.uuid4().hex[:10]}"
        log.info("  Place: %s  (%.4f, %.4f)  %d assets", place_name, lat, lon, len(indices))

        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO places (id, trip_id, name, place_type, lat, lon, confidence) VALUES (?,?,?,?,?,?,?)",
                (place_id, trip_id, place_name, "other", lat, lon, 0.8),
            )
            for idx in indices:
                conn.execute(
                    "INSERT OR IGNORE INTO place_assets (place_id, asset_id) VALUES (?,?)",
                    (place_id, gps_assets[idx]["id"]),
                )
