"""Photo ingestion: scan directories, extract EXIF/GPS, generate derivatives, store in DB."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import exifread
import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

from travelogue.config import TripConfig
from travelogue.db import connect

log = logging.getLogger(__name__)

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False
    log.debug("pillow_heif not available; HEIC files will be skipped")

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif"}
THUMBNAIL_SIZE = 300
WEB_SIZE = 1600


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _dms_to_decimal(dms: Any, ref: str) -> float | None:
    try:
        d = float(dms.values[0].num) / float(dms.values[0].den)
        m = float(dms.values[1].num) / float(dms.values[1].den)
        s = float(dms.values[2].num) / float(dms.values[2].den)
        decimal = d + m / 60 + s / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return None


def _parse_exif(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        return {k: str(v) for k, v in tags.items()}
    except Exception as exc:
        log.debug("EXIF parse failed for %s: %s", path.name, exc)
        return {}


def extract_gps_from_path(path: Path) -> tuple[float | None, float | None]:
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        lat_dms = tags.get("GPS GPSLatitude")
        lat_ref = str(tags.get("GPS GPSLatitudeRef", ""))
        lon_dms = tags.get("GPS GPSLongitude")
        lon_ref = str(tags.get("GPS GPSLongitudeRef", ""))
        if not (lat_dms and lat_ref and lon_dms and lon_ref):
            return None, None
        lat = _dms_to_decimal(lat_dms, lat_ref)
        lon = _dms_to_decimal(lon_dms, lon_ref)
        return lat, lon
    except Exception:
        return None, None


_DATETIME_PATTERNS = [
    ("%Y:%m:%d %H:%M:%S", "EXIF DateTimeOriginal"),
    ("%Y:%m:%d %H:%M:%S", "EXIF DateTimeDigitized"),
    ("%Y:%m:%d %H:%M:%S", "Image DateTime"),
]


def _parse_capture_time(exif: dict[str, Any]) -> datetime | None:
    for fmt, key in _DATETIME_PATTERNS:
        val = exif.get(key)
        if val:
            try:
                return datetime.strptime(str(val), fmt)
            except ValueError:
                continue
    return None


def _extract_orientation(exif: dict[str, Any]) -> int:
    val = exif.get("Image Orientation")
    if val:
        try:
            return int(str(val).split()[0])
        except (ValueError, IndexError):
            pass
    return 1


def _make_derivative(src: Path, dest: Path, longest_edge: int) -> bool:
    try:
        with Image.open(src) as img:
            # Normalize EXIF rotation before resizing so landscape phone photos
            # are saved with the expected pixel orientation in derivatives.
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            w, h = img.size
            scale = longest_edge / max(w, h)
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, "JPEG", quality=85, optimize=True)
        return True
    except Exception as exc:
        log.warning("Derivative generation failed for %s (%dpx): %s", src.name, longest_edge, exc)
        return False


def _compute_blur_score(path: Path) -> float | None:
    try:
        import numpy as np
        from scipy.ndimage import convolve
        with Image.open(path) as img:
            arr = np.array(img.convert("L"), dtype=float)
        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]])
        return float(np.var(convolve(arr, kernel)))
    except Exception:
        return None


def _compute_phash(path: Path) -> str | None:
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def _guess_mime(path: Path) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".heic": "image/heic", ".heif": "image/heic",
        ".tiff": "image/tiff", ".tif": "image/tiff",
    }.get(path.suffix.lower(), "image/jpeg")


def ingest_photo_dirs(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
    dry_run: bool = False,
) -> int:
    """Ingest all configured photo directories. Returns count of new assets ingested."""
    tz = ZoneInfo(cfg.trip.timezone)
    new_count = 0
    skip_count = 0
    error_count = 0
    no_gps_count = 0
    no_ts_count = 0

    # Ensure trip and people rows exist (idempotent)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO trips (id, title, timezone, description) VALUES (?,?,?,?)",
            (cfg.trip.id, cfg.trip.title, cfg.trip.timezone, cfg.trip.description),
        )
        for person in cfg.people:
            conn.execute(
                "INSERT OR IGNORE INTO people (id, trip_id, display_name, source_label, attribution_mode) VALUES (?,?,?,?,?)",
                (person.id, cfg.trip.id, person.display_name, person.source_label, person.attribution_mode),
            )

    person_map = {p.source_label: p.id for p in cfg.people}

    for photo_input in cfg.inputs.photos:
        src_dir = trip_dir / photo_input.path
        if not src_dir.exists():
            log.warning("Photo directory not found: %s", src_dir)
            continue

        person_id = photo_input.person_id
        files = [
            f for f in sorted(src_dir.rglob("*"))
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

        log.info("Found %d files in %s", len(files), src_dir)
        deriv_base = trip_dir / "working" / "derivatives"
        log_every = max(1, len(files) // 20)

        for file_idx, src_path in enumerate(files):
            try:
                if file_idx % log_every == 0:
                    log.info("[%d/%d] %s", file_idx + 1, len(files), src_path.name)

                checksum = sha256_file(src_path)

                with connect(db_path) as conn:
                    existing = conn.execute(
                        "SELECT id FROM assets WHERE trip_id=? AND checksum_sha256=?",
                        (trip_id, checksum),
                    ).fetchone()
                    if existing:
                        log.debug("Skipping already-ingested: %s", src_path.name)
                        skip_count += 1
                        continue

                if dry_run:
                    log.info("DRY RUN: would ingest %s", src_path.name)
                    new_count += 1
                    continue

                exif = _parse_exif(src_path)
                cap_time = _parse_capture_time(exif)
                orientation = _extract_orientation(exif)
                lat, lon = extract_gps_from_path(src_path)

                cap_utc = cap_local = None
                if cap_time:
                    cap_local = cap_time.replace(tzinfo=tz)
                    cap_utc = cap_local.astimezone(timezone.utc)
                else:
                    no_ts_count += 1
                    log.debug("No timestamp found for %s", src_path.name)

                if lat is None:
                    no_gps_count += 1
                    log.debug("No GPS for %s", src_path.name)
                else:
                    log.debug("  GPS: %.4f, %.4f  time: %s", lat, lon, cap_local)

                width = height = None
                try:
                    with Image.open(src_path) as img:
                        img = ImageOps.exif_transpose(img)
                        width, height = img.size
                except (UnidentifiedImageError, Exception) as exc:
                    log.debug("Could not read dimensions for %s: %s", src_path.name, exc)

                asset_id = f"a_{uuid.uuid4().hex[:12]}"
                thumb_path = deriv_base / "thumbnails" / f"{asset_id}.jpg"
                web_path = deriv_base / "web" / f"{asset_id}.jpg"

                thumb_ok = _make_derivative(src_path, thumb_path, THUMBNAIL_SIZE)
                web_ok = _make_derivative(src_path, web_path, WEB_SIZE)

                phash = _compute_phash(thumb_path if thumb_path.exists() else src_path)
                blur = _compute_blur_score(thumb_path if thumb_path.exists() else src_path)
                log.debug("  phash=%s  blur=%.1f  size=%sx%s", phash, blur or 0, width, height)

                def relpath(p: Path) -> str:
                    try:
                        return str(p.relative_to(trip_dir))
                    except ValueError:
                        return str(p)

                with connect(db_path) as conn:
                    conn.execute(
                        """INSERT OR IGNORE INTO assets
                        (id, trip_id, person_id, source_path, source_filename,
                         checksum_sha256, capture_time_original, capture_time_utc, capture_time_local,
                         gps_lat, gps_lon, mime_type, width, height, orientation,
                         exif_json, thumbnail_path, web_path, original_relpath)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            asset_id, trip_id, person_id,
                            str(src_path), src_path.name, checksum,
                            cap_time.isoformat() if cap_time else None,
                            cap_utc.isoformat() if cap_utc else None,
                            cap_local.isoformat() if cap_local else None,
                            lat, lon, _guess_mime(src_path),
                            width, height, orientation,
                            json.dumps(exif),
                            relpath(thumb_path) if thumb_ok else None,
                            relpath(web_path) if web_ok else None,
                            relpath(src_path),
                        ),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO asset_features (asset_id, phash, blur_score) VALUES (?,?,?)",
                        (asset_id, phash, blur),
                    )

                new_count += 1

            except Exception as exc:
                log.error("Error ingesting %s: %s", src_path.name, exc, exc_info=True)
                error_count += 1

    log.info(
        "Ingest complete — new: %d, skipped: %d, errors: %d "
        "(no GPS: %d, no timestamp: %d)",
        new_count, skip_count, error_count, no_gps_count, no_ts_count,
    )
    return new_count
