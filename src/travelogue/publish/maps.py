"""Generate map data: GeoJSON markers, route lines, and GPX trace overlays."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from travelogue.db import connect

log = logging.getLogger(__name__)


def generate_map_data(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    trip_context: dict[str, Any],
) -> dict[str, Any]:
    """Build the map_data dict that is embedded in the site as map_data.js."""

    photo_features = []
    event_features = []
    for day in trip_context["days"]:
        for event in day["events"]:
            # Per-photo markers
            for asset in event["assets"]:
                lat = asset.get("gps_lat")
                lon = asset.get("gps_lon")
                if not lat or not lon:
                    continue
                photo_features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "asset_id": asset["id"],
                        "event_id": event["id"],
                        "title": event["title"],
                        "date": day["date"],
                        "thumbnail": asset.get("thumbnail_path", ""),
                        "web": asset.get("web_path", ""),
                        "day_id": day["id"],
                    },
                })

            # Per-event centroid marker
            hero = event.get("hero")
            lats = [a["gps_lat"] for a in event["assets"] if a.get("gps_lat")]
            lons = [a["gps_lon"] for a in event["assets"] if a.get("gps_lon")]
            if not lats:
                continue
            lat = sum(lats) / len(lats)
            lon = sum(lons) / len(lons)
            event_features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "event_id": event["id"],
                    "title": event["title"],
                    "date": day["date"],
                    "thumbnail": (hero or {}).get("thumbnail_path", ""),
                    "day_id": day["id"],
                    "asset_count": event["asset_count"],
                },
            })

    photo_geojson = {"type": "FeatureCollection", "features": photo_features}
    event_geojson = {"type": "FeatureCollection", "features": event_features}

    gpx_traces = _load_gpx_traces(trip_dir)

    route = _build_route(trip_context)

    day_bounds = {}
    for day in trip_context["days"]:
        all_lats, all_lons = [], []
        for event in day["events"]:
            for a in event["assets"]:
                if a.get("gps_lat") and a.get("gps_lon"):
                    all_lats.append(a["gps_lat"])
                    all_lons.append(a["gps_lon"])
        if all_lats:
            day_bounds[day["id"]] = {
                "min_lat": min(all_lats), "max_lat": max(all_lats),
                "min_lon": min(all_lons), "max_lon": max(all_lons),
            }

    return {
        "markers": photo_geojson,
        "events": event_geojson,
        "gpx_traces": gpx_traces,
        "route": route,
        "day_bounds": day_bounds,
    }


def _build_route(trip_context: dict[str, Any]) -> dict[str, Any]:
    """Build chronological route GeoJSON from individual photo GPS coordinates.

    Uses all geolocated photos in chronological order (days → events → assets),
    so the route threads directly through the photo bubble locations.

    Produces two segment types:
    - "drive" for consecutive photos within FLIGHT_THRESHOLD_KM
    - "flight" for longer jumps (likely a flight between locations)
    """
    import math

    FLIGHT_THRESHOLD_KM = 500

    points: list[tuple[float, float]] = []
    for day in trip_context["days"]:
        for event in day["events"]:
            for asset in event["assets"]:
                lat = asset.get("gps_lat")
                lon = asset.get("gps_lon")
                if lat and lon:
                    points.append((lon, lat))

    if len(points) < 2:
        return {"type": "FeatureCollection", "features": []}

    def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    features = []
    current_type = None
    current_coords: list[list[float]] = []

    for i in range(1, len(points)):
        prev, curr = points[i - 1], points[i]
        dist = haversine_km(prev[0], prev[1], curr[0], curr[1])
        seg_type = "flight" if dist > FLIGHT_THRESHOLD_KM else "drive"

        if seg_type != current_type:
            if current_coords and len(current_coords) >= 2:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": current_coords},
                    "properties": {"type": current_type},
                })
            current_type = seg_type
            current_coords = [list(prev)]

        if not current_coords:
            current_coords = [list(prev)]
        current_coords.append(list(curr))

    if current_coords and len(current_coords) >= 2:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": current_coords},
            "properties": {"type": current_type},
        })

    log.debug("Route: %d points, %d segments (%d drive, %d flight)",
              len(points), len(features),
              sum(1 for f in features if f["properties"]["type"] == "drive"),
              sum(1 for f in features if f["properties"]["type"] == "flight"))

    return {"type": "FeatureCollection", "features": features}


def _load_gpx_traces(trip_dir: Path) -> list[dict]:
    """Parse GPX files from the trip workspace into GeoJSON LineString features."""
    traces = []
    gpx_files = list(trip_dir.rglob("*.gpx")) + list(trip_dir.rglob("*.kml"))

    for gpx_path in gpx_files:
        try:
            if gpx_path.suffix.lower() == ".gpx":
                coords = _parse_gpx(gpx_path)
            else:
                coords = _parse_kml(gpx_path)
            if coords:
                traces.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"name": gpx_path.stem},
                })
        except Exception:
            pass
    return traces


def _parse_gpx(path: Path) -> list[list[float]]:
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}
    coords = []
    for trkpt in root.findall(".//gpx:trkpt", ns):
        lat = float(trkpt.attrib.get("lat", 0))
        lon = float(trkpt.attrib.get("lon", 0))
        coords.append([lon, lat])
    return coords


def _parse_kml(path: Path) -> list[list[float]]:
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    for coord_el in root.findall(".//kml:coordinates", ns):
        text = coord_el.text or ""
        coords = []
        for token in text.split():
            parts = token.split(",")
            if len(parts) >= 2:
                try:
                    coords.append([float(parts[0]), float(parts[1])])
                except ValueError:
                    pass
        if coords:
            return coords
    return []
