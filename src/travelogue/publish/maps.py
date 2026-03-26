"""Generate map data: GeoJSON markers and GPX trace overlays."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from travelogue.db import connect


def generate_map_data(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    trip_context: dict[str, Any],
) -> dict[str, Any]:
    """Build the map_data dict that is embedded in the site as map_data.js."""

    # Build GeoJSON FeatureCollection of event markers
    features = []
    for day in trip_context["days"]:
        for event in day["events"]:
            hero = event.get("hero")
            if not hero:
                continue
            # Use median GPS of assets in the event
            lats = [a["gps_lat"] for a in event["assets"] if a.get("gps_lat")]
            lons = [a["gps_lon"] for a in event["assets"] if a.get("gps_lon")]
            if not lats:
                continue
            lat = sum(lats) / len(lats)
            lon = sum(lons) / len(lons)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "event_id": event["id"],
                    "title": event["title"],
                    "date": day["date"],
                    "thumbnail": hero.get("thumbnail_path", ""),
                    "day_id": day["id"],
                    "asset_count": event["asset_count"],
                },
            })

    geojson = {"type": "FeatureCollection", "features": features}

    # Parse GPX traces
    gpx_traces = _load_gpx_traces(trip_dir)

    # Day bounding boxes for day map zoom
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
        "markers": geojson,
        "gpx_traces": gpx_traces,
        "day_bounds": day_bounds,
    }


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
    """Extract [lon, lat] pairs from a GPX file."""
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
    """Extract [lon, lat] pairs from a KML file's first LineString."""
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
