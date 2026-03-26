"""Photo ingestion: scan directories, extract EXIF/GPS, generate derivatives, store in DB."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import exifread
import imagehash
from PIL import Image, UnidentifiedImageError
from rich.console import Console
from rich.progress import track

from travelogue.config import TripConfig
from travelogue.db import connect

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif"}

# Derivative sizes: (name, longest_edge)
THUMBNAIL_SIZE = 300
WEB_SIZE = 1600


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _dms_to_decimal(dms: Any, ref: str) -> float | None:
    """Convert EXIF DMS tuple to decimal degrees."""
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
    """Extract EXIF tags as a flat dict."""
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        return {k: str(v) for k, v in tags.items()}
    except Exception:
        return {}


def _extract_gps(exif: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return (lat, lon) in decimal degrees, or (None, None)."""
    lat_dms = exif.get("GPS GPSLatitude")
    lat_ref = exif.get("GPS GPSLatitudeRef")
    lon_dms = exif.get("GPS GPSLongitude")
    lon_ref = exif.get("GPS GPSLongitudeRef")

    if not (lat_dms and lat_ref and lon_dms and lon_ref):
        return None, None

    # Re-read as raw values for DMS conversion
    try:
        with open(_last_path, "rb") as f:  # noqa: F821 — injected via closure below
            tags = exifread.process_file(f, details=False)
        lat = _dms_to_decimal(tags.get("GPS GPSLatitude"), str(lat_ref))
        lon = _dms_to_decimal(tags.get("GPS GPSLongitude"), str(lon_ref))
        return lat, lon
    except Exception:
        return None, None


def extract_gps_from_path(path: Path) -> tuple[float | None, float | None]:
    """Extract GPS from a file path directly (avoiding the closure hack)."""
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


# Common EXIF datetime formats
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
    """Resize and save a derivative image. Returns True on success."""
    try:
        with Image.open(src) as img:
            img = img.convert("RGB")
            w, h = img.size
            scale = longest_edge / max(w, h)
            if scale < 1.0:
                new_w = int(w * scale)
                new_h = int(h * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, "JPEG", quality=85, optimize=True)
        return True
    except Exception:
        return False


def _compute_blur_score(path: Path) -> float | None:
    """Laplacian variance as a sharpness proxy. Higher = sharper."""
    try:
        import numpy as np
        with Image.open(path) as img:
            gray = img.convert("L")
            arr = np.array(gray, dtype=float)
        # Laplacian kernel
        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]])
        from scipy.ndimage import convolve
        lap = convolve(arr, kernel)
        return float(np.var(lap))
    except Exception:
        return None


def _compute_phash(path: Path) -> str | None:
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def ingest_photo_dirs(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
    dry_run: bool = False,
    console: Console | None = None,
) -> int:
    """Ingest all configured photo directories. Returns count of new assets ingested."""
    if console is None:
        console = Console()

    tz = ZoneInfo(cfg.trip.timezone)
    new_count = 0
    skip_count = 0
    error_count = 0

    # Build person_id -> Person lookup
    person_map = {p.source_label: p.id for p in cfg.people}

    for photo_input in cfg.inputs.photos:
        src_dir = trip_dir / photo_input.path
        if not src_dir.exists():
            console.print(f"  [yellow]Warning:[/yellow] photo directory not found: {src_dir}")
            continue

        person_id = photo_input.person_id
        files = [
            f for f in sorted(src_dir.rglob("*"))
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

        console.print(f"  Found {len(files)} files in {src_dir}")

        deriv_base = trip_dir / "working" / "derivatives"

        for src_path in track(files, description=f"  Ingesting {photo_input.path}…", console=console):
            try:
                checksum = sha256_file(src_path)

                # Check if already ingested
                with connect(db_path) as conn:
                    existing = conn.execute(
                        "SELECT id FROM assets WHERE trip_id=? AND checksum_sha256=?",
                        (trip_id, checksum),
                    ).fetchone()
                    if existing:
                        skip_count += 1
                        continue

                if dry_run:
                    console.print(f"  [dim]DRY RUN:[/dim] would ingest {src_path.name}")
                    new_count += 1
                    continue

                # Extract EXIF
                exif = _parse_exif(src_path)
                cap_time = _parse_capture_time(exif)
                orientation = _extract_orientation(exif)
                lat, lon = extract_gps_from_path(src_path)

                # Normalize timestamps
                cap_utc = None
                cap_local = None
                if cap_time:
                    # EXIF time has no tz info; treat as local trip time
                    cap_local = cap_time.replace(tzinfo=tz)
                    cap_utc = cap_local.astimezone(timezone.utc)

                # Image dimensions
                width, height = None, None
                try:
                    with Image.open(src_path) as img:
                        width, height = img.size
                except (UnidentifiedImageError, Exception):
                    pass

                # Generate asset ID and derivative paths
                asset_id = f"a_{uuid.uuid4().hex[:12]}"
                rel_stem = src_path.stem
                thumb_path = deriv_base / "thumbnails" / f"{asset_id}.jpg"
                web_path = deriv_base / "web" / f"{asset_id}.jpg"

                _make_derivative(src_path, thumb_path, THUMBNAIL_SIZE)
                _make_derivative(src_path, web_path, WEB_SIZE)

                # Compute quality features from thumbnail (faster)
                phash = _compute_phash(thumb_path if thumb_path.exists() else src_path)
                blur = _compute_blur_score(thumb_path if thumb_path.exists() else src_path)

                # Relative paths for portability
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
                            str(src_path), src_path.name,
                            checksum,
                            cap_time.isoformat() if cap_time else None,
                            cap_utc.isoformat() if cap_utc else None,
                            cap_local.isoformat() if cap_local else None,
                            lat, lon,
                            _guess_mime(src_path),
                            width, height, orientation,
                            json.dumps(exif),
                            relpath(thumb_path) if thumb_path.exists() else None,
                            relpath(web_path) if web_path.exists() else None,
                            relpath(src_path),
                        ),
                    )
                    conn.execute(
                        """INSERT OR REPLACE INTO asset_features
                        (asset_id, phash, blur_score, exposure_score)
                        VALUES (?,?,?,?)""",
                        (asset_id, phash, blur, None),
                    )

                new_count += 1

            except Exception as exc:
                console.print(f"  [red]Error ingesting {src_path.name}:[/red] {exc}")
                error_count += 1

    console.print(
        f"  [green]Done.[/green] New: {new_count}, Skipped (already ingested): {skip_count}, Errors: {error_count}"
    )
    return new_count


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".heic": "image/heic",
        ".heif": "image/heic",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }.get(ext, "image/jpeg")
