"""Core domain models (Pydantic v2).

These are the in-memory representations of the canonical entities.
The db module handles persistence to/from SQLite.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PlaceType(str, Enum):
    city = "city"
    neighborhood = "neighborhood"
    park = "park"
    trail = "trail"
    restaurant = "restaurant"
    hotel = "hotel"
    museum = "museum"
    other = "other"


class ClusterType(str, Enum):
    wildlife = "wildlife"
    food = "food"
    landscape = "landscape"
    architecture = "architecture"
    transportation = "transportation"
    people = "people"
    other = "other"
    unknown = "unknown"


class NarrativeBlockType(str, Enum):
    human_source = "human_source"
    ai_draft = "ai_draft"
    final_override = "final_override"


class ScopeType(str, Enum):
    trip = "trip"
    day = "day"
    event = "event"
    place = "place"


# ---------------------------------------------------------------------------
# Core entities
# ---------------------------------------------------------------------------


class Trip(BaseModel):
    id: str
    title: str
    timezone: str = "UTC"
    start_date: date | None = None
    end_date: date | None = None
    description: str = ""
    config_json: dict[str, Any] = Field(default_factory=dict)


class Person(BaseModel):
    id: str
    trip_id: str
    display_name: str
    source_label: str
    attribution_mode: str = "minimal"


class Asset(BaseModel):
    id: str
    trip_id: str
    person_id: str | None = None
    source_path: str
    source_filename: str
    checksum_sha256: str
    capture_time_original: datetime | None = None
    capture_time_utc: datetime | None = None
    capture_time_local: datetime | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    mime_type: str = "image/jpeg"
    width: int | None = None
    height: int | None = None
    orientation: int = 1
    exif_json: dict[str, Any] = Field(default_factory=dict)
    thumbnail_path: str | None = None
    web_path: str | None = None
    original_relpath: str | None = None


class AssetFeature(BaseModel):
    asset_id: str
    phash: str | None = None
    blur_score: float | None = None
    exposure_score: float | None = None
    # embedding stored separately as BLOB
    scene_tags_json: list[str] = Field(default_factory=list)


class NearDuplicateCluster(BaseModel):
    id: str
    trip_id: str
    representative_asset_id: str
    confidence: float = 1.0
    method: str = "phash+embedding"
    rationale: str = ""


class NearDuplicateClusterMember(BaseModel):
    cluster_id: str
    asset_id: str
    similarity_score: float = 1.0
    is_representative: bool = False


class Day(BaseModel):
    id: str
    trip_id: str
    local_date: date
    title: str = ""
    order_index: int = 0


class Event(BaseModel):
    id: str
    trip_id: str
    day_id: str
    start_time_local: datetime | None = None
    end_time_local: datetime | None = None
    title: str = ""
    place_id: str | None = None
    event_type: str = ""
    confidence: float = 1.0
    hero_asset_id: str | None = None
    order_index: int = 0


class Place(BaseModel):
    id: str
    trip_id: str
    name: str
    place_type: PlaceType = PlaceType.other
    lat: float | None = None
    lon: float | None = None
    address: str = ""
    external_links: dict[str, str] = Field(default_factory=dict)
    confidence: float = 1.0


class SubjectCluster(BaseModel):
    id: str
    trip_id: str
    scope_type: ScopeType
    scope_id: str
    label: str = ""
    cluster_type: ClusterType = ClusterType.unknown
    confidence: float = 1.0
    hero_asset_id: str | None = None


class JournalSource(BaseModel):
    id: str
    trip_id: str
    path: str
    raw_markdown: str
    parsed_date: date | None = None
    date_confidence: float = 0.0
    heading_structure: list[dict[str, Any]] = Field(default_factory=list)


class NarrativeBlock(BaseModel):
    id: str
    trip_id: str
    scope_type: ScopeType
    scope_id: str
    block_type: NarrativeBlockType
    content_markdown: str
    locked: bool = False
    source_ref: str = ""
    updated_at: datetime | None = None


class AiArtifact(BaseModel):
    id: str
    trip_id: str
    subject_type: str
    subject_id: str
    provider: str
    model: str
    task_type: str
    prompt_hash: str
    input_hash: str
    output_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    status: str = "ok"
