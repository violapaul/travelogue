# Travelogue Generator
## Detailed Design and Requirements Specification

**Document status:** Draft v1.0  
**Audience:** Implementation agents, software engineers, technical reviewers  
**Primary owner/use case:** Reusable personal tool for compiling private trip travelogues from exported photo libraries and markdown journals.  
**Primary deployment target:** Private static site on AWS with authenticated access for friends and family.

---

## 1. Purpose

Build a software system that ingests one or more exported photo rolls plus loose markdown journal text and compiles them into a private, beautiful, map-aware travelogue.

The system must:

- ingest photos from one or more people, initially via **manual export** from Amazon Photos
- ingest loose markdown journals with dates or date-like hints
- analyze and cluster media once, then reuse cached analysis on subsequent runs
- call one or more state-of-the-art AI providers (OpenAI and/or Gemini) for enrichment
- generate a **static website** with authenticated private hosting
- provide multiple browsing modes: **Story**, **Map**, **Everything**, **Slideshow**, and **Print/Photobook**
- perform rough image near-deduplication while preserving access to alternates
- generate AI draft journal/event text without destroying human edits
- create a print-oriented export suitable for physical photobook workflows, preferably using portable formats

This document is intended to be concrete enough for an implementation agent to execute directly.

---

## 2. Product Vision

The product is an **editorial atlas + scrollable visual diary** rather than a generic gallery.

The core experience should feel like:

- a curated trip story
- a geographic map of the journey
- a long, scrollable “show me nearly everything” page
- a lightweight, replayable publishing pipeline

The software should be optimized for repeated use by one primary user, but its design should not prevent later expansion to other users.

---

## 3. High-Level Principles

1. **Static first**  
   Generated output should be static HTML/CSS/JS plus image/media assets.

2. **Compiler architecture**  
   Heavy analysis runs once and produces a compiled trip graph. Rendering is separate from analysis.

3. **Human edits are sacred**  
   AI-generated content must never overwrite human-edited content.

4. **Deterministic core, AI enrichment second**  
   Timestamps, GPS, clustering, and structure should come from deterministic pipelines where possible; AI should label, summarize, rank, and suggest.

5. **Private by design**  
   Hosting must support authenticated private sharing.

6. **Maps are first-class navigation**  
   Maps are not decorative; they are a primary browsing surface.

7. **Duplicates are hidden, not deleted**  
   The UI should prefer one representative image but keep alternates accessible.

8. **Print is a distinct render target**  
   The photobook export should not be treated as “print the website.”

---

## 4. Target Users and Usage Model

### Primary user
A technically sophisticated individual creating travelogues for personal use and sharing with friends/family.

### Secondary viewers
Friends and family browsing the final published site.

### Frequency
The system should be reusable across many future trips.

### Typical trip scale (initial target)
- ~100 photos per trip
- 1–3 photographers
- 1–14 days of travel
- mostly still photos, with video optional and low priority

---

## 5. Scope

### In scope for v1
- manual import of photo exports from local filesystem
- multi-photographer support
- markdown journal ingestion
- EXIF extraction
- GPS-aware event/place grouping
- near-duplicate clustering with representative selection
- AI enrichment via OpenAI and Gemini adapters
- generated story/day/event/place pages
- map views
- “Everything” long-scroll page
- slideshow mode in the web site
- print-oriented HTML/PDF export
- authenticated private hosting design for AWS
- simple human-in-the-loop review via markdown/YAML override files

### Explicitly out of scope for v1
- direct Amazon Photos API integration
- rich in-browser CMS/editor
- full multi-user accounts and roles
- sophisticated video editing or highlight reels
- perfect face recognition / identity management
- tightly coupled vendor-specific photobook export formats
- full collaborative editing workflows

### Possible v1.5 / v2 items
- browser-based local review UI
- GPX / route import
- better wildlife/food/theme clustering
- optional public teaser pages
- improved video handling
- service-specific print export presets (e.g. Blurb bleed/trim profiles)

---

## 6. Functional Requirements

### 6.1 Trip Initialization
The system shall support creation of a trip workspace with:

- trip ID
- trip title
- default timezone
- input directories
- output directories
- AI provider configuration
- deployment configuration
- rendering theme configuration

### 6.2 Photo Ingest
The system shall ingest photo files from one or more directories.

Each imported photo shall record:
- original file path
- file checksum
- photographer/source label
- capture timestamp (original and normalized)
- GPS coordinates
- EXIF metadata blob
- image dimensions/orientation
- derived thumbnail paths

The ingest process shall:
- support JPEG, HEIC, PNG at minimum
- preserve originals unchanged
- detect exact duplicates by checksum
- assign stable internal IDs

### 6.3 Journal Ingest
The system shall ingest markdown files containing loose prose.

Journal parsing shall:
- support one file per day and one file with many dated sections
- infer dates from frontmatter, headings, or textual date mentions where possible
- attach journal content to day/event candidates with confidence scores
- allow journal text to remain partially unstructured

### 6.4 Metadata Extraction
The system shall extract:
- EXIF timestamp(s)
- timezone offsets where available
- GPS coordinates
- orientation
- camera/device metadata

The system should normalize timestamps into:
- original timestamp
- UTC timestamp
- trip-local timestamp

### 6.5 Media Analysis
The system shall compute derived media features including:
- perceptual hash
- image embedding or equivalent similarity vector
- basic quality metrics (blur, exposure, orientation)
- optional aesthetic score
- optional scene/object labels

### 6.6 Duplicate and Near-Duplicate Clustering
The system shall group images into exact-duplicate and near-duplicate clusters.

Each near-duplicate cluster shall:
- contain one representative asset
- retain all alternates
- record confidence and clustering rationale

Representative selection shall prefer:
- sharpness
- exposure quality
- resolution
- composition/aesthetic score if available
- face visibility if relevant
- non-accidental crops/rotations

The UI shall default to the representative image while exposing alternates through a popup/lightbox or expandable section.

### 6.7 Time and Event Segmentation
The system shall segment a trip into:
- days
- moments/events within days

Segmentation inputs should include:
- timestamp gaps
- GPS movement/change
- journal text anchors
- AI-suggested event names (post-segmentation)

### 6.8 Place Resolution
The system shall derive place candidates from:
- GPS clusters
- reverse geocoding
- journal text
- AI suggestions

The system shall distinguish between:
- geographic place (city, trail, park, neighborhood)
- point of interest (restaurant, museum, hotel)
- semantic subject group (wildlife, food, architecture)

### 6.9 Subject/Semantic Clustering
Within an event or place, the system should identify subgroups such as:
- wildlife
- food
- views
- architecture
- transportation
- lodging
- people

These semantic clusters shall be used for:
- popup galleries
- sidebars
- map popups
- photobook selection support

### 6.10 AI Enrichment
The system shall support AI enrichment tasks including:
- day summary generation
- event title suggestions
- event summary generation
- place/entity suggestions
- link suggestions for explicit POIs
- representative image ranking assistance
- optional draft journal text generation when the human journal is sparse

The system shall support at least two AI providers:
- OpenAI
- Gemini

The AI layer shall be pluggable.

### 6.11 Human Edit Preservation
The system shall preserve human-authored content across reruns.

Generated and human-authored text must be stored separately.

Final render precedence shall be:
1. explicit override/final edited text
2. imported human journal text
3. generated AI draft text
4. deterministic fallback text

The system shall never overwrite files in the human/override layer unless explicitly instructed.

### 6.12 Story View
The generated site shall include a story-oriented travelogue view featuring:
- chronological flow by day
- hero images
- narrative text
- event sections
- small embedded maps when useful
- duplicate cluster reveal affordances

### 6.13 Map View
The generated site shall include a map-oriented browsing mode with:
- zoomable trip map
- clustered markers at low zoom
- representative images at markers/popups
- links to day/event/place pages
- optional route polylines, especially for day maps
- filters for day/place/subject where practical

The map experience shall support:
- whole-trip overview map
- smaller local/day maps

### 6.14 Everything View
The generated site shall include an “Everything” view:
- long-form, scrollable page
- mostly chronological
- interleaving text, photos, and maps
- showing a large fraction of representative assets
- minimizing click depth

This should be a signature experience.

### 6.15 Slideshow View
The generated site shall include a slideshow mode:
- full-screen or large-canvas viewer
- keyboard navigation
- auto-advance optional
- image captions/labels optional
- filtered by trip/day/event if desired

### 6.16 Print / Photobook Export
The system shall generate a print-oriented output using portable formats.

v1 requirement:
- print-oriented HTML
- PDF export
- structured manifest (JSON and/or CSV) for debugging/portability

Print output should favor:
- large images
- limited but useful text
- coherent day/place grouping
- reduced duplicates

### 6.17 External Links
For sufficiently confident explicit POIs, the system may attach external links such as:
- official site
- map listing
- reservation/info page

The system should avoid spammy over-linking. Broader geographic places may have lighter metadata.

### 6.18 Deployment
The system shall support static deployment to AWS.

The recommended deployment model is:
- site assets in S3
- CloudFront in front
- authenticated/private access for viewers

GitHub may be used for source control and CI, but not as the primary private site host.

### 6.19 Preview
The system shall support local preview of the generated site before deployment.

### 6.20 Incremental Rebuilds
The pipeline shall avoid recomputing expensive steps when inputs have not changed.

At minimum, cache invalidation shall distinguish between:
- raw ingest changes
- deterministic analysis changes
- AI enrichment changes
- rendering/template changes

---

## 7. Non-Functional Requirements

### 7.1 Performance
For a trip with ~100 images, a full run should be reasonable on a modern developer machine.

Targets:
- ingest: under 1 minute
- deterministic analysis: under 5 minutes
- AI enrichment: dependent on provider latency, but fully cacheable
- site render: under 1 minute

These are guidance targets, not strict SLAs.

### 7.2 Reliability
- reruns must be safe
- content IDs must be stable
- failures in one stage should not corrupt previous outputs
- the system should recover from partial AI/enrichment failure

### 7.3 Portability
- should run locally on macOS/Linux
- generated outputs should be portable static assets
- print export should not depend on a proprietary external service

### 7.4 Maintainability
- strongly typed internal models
- modular provider interfaces
- explicit configuration files
- reproducible cache/artifact structure

### 7.5 Privacy/Security
- original photos should remain private by default
- published site should support authenticated access
- secrets/API keys must never be embedded in generated static assets
- private metadata leakage should be minimized in public-facing HTML/JS

### 7.6 Aesthetics
The output should feel polished and editorial rather than utilitarian. Design should favor whitespace, strong images, restrained metadata, and smooth map/gallery interactions.

---

## 8. Proposed Architecture

### 8.1 Major subsystems

1. **Ingest subsystem**
   - file scanning
   - metadata extraction
   - journal parsing

2. **Analysis subsystem**
   - quality scoring
   - dedup clustering
   - time/event segmentation
   - place clustering
   - semantic clustering

3. **AI enrichment subsystem**
   - provider abstraction
   - prompt building
   - output caching
   - structured output validation

4. **Review subsystem**
   - low-confidence reports
   - override files
   - resolved content layering

5. **Publish subsystem**
   - content graph compilation
   - HTML generation
   - map data generation
   - image derivation
   - slideshow/print rendering

6. **Deploy subsystem**
   - local preview
   - S3 sync
   - CloudFront invalidation hooks (optional)

### 8.2 Architectural style
Use a **compiled content graph** architecture:

- ingest raw sources
- normalize into canonical entities
- analyze into derived structures
- enrich with AI
- resolve final layered content
- publish static views from the graph

This is preferred over a live database-backed web app.

---

## 9. Technology Recommendations

### Backend
- Python 3.12+
- `typer` for CLI
- `pydantic` for schemas
- `sqlalchemy` + SQLite for canonical store
- `Pillow` for image processing
- `opencv-python` for computer vision utilities
- `imagehash` for perceptual hashing
- optional `numpy` / `scikit-learn` for clustering
- optional embedding model + ANN index for similarity
- `jinja2` for rendering
- `markdown-it-py` or similar for markdown parsing
- `PyYAML` or `ruamel.yaml` for config/overrides

### Frontend / Static Site
- statically generated HTML/CSS/JS
- Map library: **MapLibre GL JS** preferred, Leaflet acceptable
- lightbox/gallery library or custom minimal implementation
- responsive CSS, likely custom + utility-light approach

### Export / Print
- print HTML templates
- PDF via browser rendering (Playwright) or equivalent reliable HTML-to-PDF pipeline

### Deployment
- AWS S3 + CloudFront
- GitHub for source repo and optional CI

---

## 10. Data Model

The following is the recommended logical model. Exact implementation may vary.

### 10.1 Core entities

#### Trip
- `id`
- `title`
- `timezone`
- `start_date`
- `end_date`
- `description`
- `config_json`

#### Person
- `id`
- `display_name`
- `source_label`
- `attribution_mode`

#### Asset
- `id`
- `trip_id`
- `person_id`
- `source_path`
- `source_filename`
- `checksum_sha256`
- `capture_time_original`
- `capture_time_utc`
- `capture_time_local`
- `gps_lat`
- `gps_lon`
- `mime_type`
- `width`
- `height`
- `orientation`
- `exif_json`
- `thumbnail_path`
- `web_derivative_path`
- `original_relpath`

#### AssetFeature
- `asset_id`
- `phash`
- `embedding_ref`
- `blur_score`
- `exposure_score`
- `aesthetic_score`
- `scene_tags_json`
- `ocr_text`

#### ExactDuplicateGroup
- `id`
- `trip_id`
- `checksum_sha256`

#### NearDuplicateCluster
- `id`
- `trip_id`
- `representative_asset_id`
- `confidence`
- `method_version`
- `rationale_json`

#### NearDuplicateClusterMember
- `cluster_id`
- `asset_id`
- `similarity_score`
- `is_representative`

#### Day
- `id`
- `trip_id`
- `local_date`
- `title`
- `summary_status`
- `order_index`

#### Event
- `id`
- `trip_id`
- `day_id`
- `start_time_local`
- `end_time_local`
- `title`
- `place_id`
- `event_type`
- `confidence`
- `hero_asset_id`

#### Place
- `id`
- `trip_id`
- `name`
- `place_type`  # city/park/trail/restaurant/hotel/museum/etc.
- `lat`
- `lon`
- `address`
- `external_links_json`
- `confidence`

#### SubjectCluster
- `id`
- `trip_id`
- `scope_type`  # trip/day/event/place
- `scope_id`
- `label`
- `cluster_type`  # wildlife/food/views/etc.
- `confidence`
- `hero_asset_id`

#### JournalSource
- `id`
- `trip_id`
- `path`
- `raw_markdown`
- `parsed_date`
- `date_confidence`
- `heading_structure_json`

#### NarrativeBlock
- `id`
- `trip_id`
- `scope_type`   # day/event/place
- `scope_id`
- `block_type`   # human_source / ai_draft / final_override
- `content_markdown`
- `locked`
- `source_ref`
- `updated_at`

#### AiArtifact
- `id`
- `trip_id`
- `subject_type`
- `subject_id`
- `provider`
- `model`
- `task_type`
- `prompt_hash`
- `input_hash`
- `output_json`
- `created_at`
- `status`

### 10.2 Relationship tables
- `event_assets`
- `place_assets`
- `subject_cluster_assets`
- `day_journals`
- `event_journals`

### 10.3 Render-time compiled structures
A separate compiled graph or JSON bundle may be emitted for the frontend, e.g.:
- `trip.json`
- `days.json`
- `map_markers.json`
- `everything_view.json`
- `slideshow.json`
- `print_manifest.json`

---

## 11. Filesystem Layout

Recommended repo/workspace layout:

```text
travelogue/
  pyproject.toml
  README.md
  src/travelogue/
    cli/
    config/
    ingest/
    analysis/
    ai/
    publish/
    print/
    review/
    storage/
    models/
    templates/
    static/
  trips/
    <trip-id>/
      trip.yaml
      imports/
        originals/
          <source-1>/
          <source-2>/
      journals/
      overrides/
        days/
        events/
        places/
        subjects/
      generated/
        ai/
        reports/
      working/
      publish/
  cache/
  output/
```

### Recommended layering
- `imports/`: immutable source material
- `journals/`: human-authored markdown source
- `generated/`: machine-generated outputs
- `overrides/`: manual edits/overrides
- `working/`: intermediate artifacts/cache
- `publish/`: final compiled output for the trip

---

## 12. Content Layering and Edit Preservation

This is a critical requirement.

### 12.1 Rule
Generated content must never overwrite human-authored content.

### 12.2 Suggested precedence
For each day/event/place narrative:
1. `overrides/.../*.md` final edited content
2. human journal content from `journals/`
3. generated AI draft content from `generated/ai/`
4. system fallback text

### 12.3 Suggested file pattern
Examples:

```text
journals/2026-03-18.md
generated/ai/days/2026-03-18.md
overrides/days/2026-03-18.md
```

Or for events:

```text
generated/ai/events/e_012.md
overrides/events/e_012.md
```

### 12.4 Title precedence
For titles:
1. manual override title
2. human heading-derived title
3. AI-generated title
4. fallback generated deterministic title

---

## 13. Pipeline Design

### Stage 0: Trip init
- create trip structure
- validate config
- register people/sources

### Stage 1: Ingest
- scan photo directories
- compute checksums
- extract EXIF/GPS/timestamps
- create derivatives (thumbnails, resized web images)
- ingest markdown journals

### Stage 2: Deterministic analysis
- exact duplicate grouping
- near-duplicate feature extraction
- similarity graph / clustering
- representative image selection
- day assignment
- event segmentation
- initial place grouping
- optional subject feature extraction

### Stage 3: AI enrichment
- build compact context packages per day/event/place
- request day summaries
- request event titles/summaries
- request subject labels
- request POI/entity/link suggestions
- cache all outputs
- validate structured outputs

### Stage 4: Review artifact generation
- generate low-confidence reports
- generate candidate override stubs
- generate contact sheets or thumbnail reports for cluster review

### Stage 5: Publish compile
- resolve layered narratives
- compile frontend JSON
- generate pages
- generate map data
- generate slideshow data
- generate print manifest and print HTML

### Stage 6: Preview / deploy
- local preview server
- deploy static assets to S3
- optionally trigger CDN invalidation

---

## 14. Detailed Algorithmic Requirements

### 14.1 Exact duplicate detection
Method:
- SHA-256 checksum over original file bytes

Acceptance:
- identical files are grouped as exact duplicates
- only one copy needs to be processed for expensive downstream operations

### 14.2 Near-duplicate clustering
Input signals:
- perceptual hash distance
- embedding similarity
- capture time proximity
- GPS proximity
- image dimensions/orientation similarity

Suggested approach:
1. generate candidate neighbors using time/GPS windows
2. compute visual similarity
3. cluster with thresholded graph components or hierarchical clustering
4. pick representative via quality-ranking function

Output:
- cluster members
- representative
- confidence
- rationale fields (e.g. close in time + visually similar)

### 14.3 Representative selection
Suggested weighted score:
- sharpness/blur metric
- exposure score
- resolution
- aesthetic score
- face/subject presence where relevant
- non-rotated or well-oriented image preference

### 14.4 Day assignment
Use trip timezone and normalized local timestamps.

Fallbacks:
- when timestamps are missing or poor, use neighboring assets and journal clues

### 14.5 Event segmentation
Primary signals:
- time gaps
- GPS movement
- journal date/section alignment

Suggested heuristic:
- split on large time gaps
- split on substantial location change
- merge very short adjacent bursts if same place/context
- allow AI to label an event after segmentation, not define the segmentation from scratch

### 14.6 Place grouping
Use:
- geospatial clustering
- reverse geocoding
- journal/place names
- AI entity resolution

Important distinction:
- cluster geography is not always the correct narrative grouping
- one hike may contain multiple subject clusters worth surfacing separately

### 14.7 Subject clustering
Within a place/event, cluster assets by semantic content. Initial types may include:
- wildlife
- food
- landscape/view
- architecture
- transportation
- people

Suggested approach:
- scene/object tags from CV model
- embedding clustering within the scope
- AI label assignment to candidate clusters

### 14.8 Map marker generation
Rule:
- low zoom: show event/place markers or clustered representative-photo markers
- medium zoom: show representative asset markers
- high zoom: allow more detailed markers or nearby alternates

The system should avoid rendering all raw assets as pins at low zoom.

### 14.9 Everything view composition
The Everything view should be composed from ranked story blocks, not a raw asset dump.

Possible block types:
- trip intro
- day header
- narrative block
- map block
- hero image block
- image strip block
- place callout
- thematic sidebar block

Composition should be mostly chronological with optional thematic inserts.

### 14.10 Print selection
Default print selection should:
- prefer representative images
- avoid near-duplicates
- favor high-quality and diverse assets
- keep coherent day/place grouping
- use concise captions and labels

---

## 15. AI Provider Abstraction

### 15.1 Goals
The AI layer must be provider-agnostic and cacheable.

### 15.2 Required provider operations
At minimum:
- `summarize_day(context)`
- `label_event(context)`
- `summarize_event(context)`
- `label_subject_cluster(context)`
- `suggest_place_entities(context)`
- `rank_representative_images(context)`
- `draft_journal_entry(context)`

### 15.3 Provider interface sketch

```python
class AiProvider(Protocol):
    def summarize_day(self, context: DayContext) -> DaySummary: ...
    def label_event(self, context: EventContext) -> EventLabel: ...
    def summarize_event(self, context: EventContext) -> EventSummary: ...
    def label_subject_cluster(self, context: SubjectClusterContext) -> SubjectLabel: ...
    def suggest_place_entities(self, context: PlaceContext) -> PlaceSuggestions: ...
    def rank_representative_images(self, context: ImageRankingContext) -> ImageRanking: ...
    def draft_journal_entry(self, context: DayContext) -> JournalDraft: ...
```

### 15.4 Structured outputs
AI calls should return structured JSON validated against Pydantic schemas.

### 15.5 Caching
Each AI artifact must be keyed by:
- provider
- model
- task type
- normalized input hash
- prompt version

### 15.6 Safety rules for AI usage
- do not let AI override hard metadata truth without explicit confidence handling
- store confidence and reasoning fields where practical
- surface low-confidence outputs in the review report

---

## 16. Review and Override Workflow

### 16.1 Goal
Allow simple human-in-the-loop control without building a full CMS.

### 16.2 Mechanisms
- YAML overrides for metadata/title/selection changes
- Markdown overrides for narrative content
- generated “needs review” reports

### 16.3 Override examples

```yaml
# overrides/events/e_014.yaml
title: Lunch at the harbor café
hero_asset_id: a_3381
hide_asset_ids:
  - a_3387
subject_clusters:
  - sc_22
```

### 16.4 Review report contents
Should include:
- uncertain place matches
- uncertain event boundaries
- low-confidence AI summaries/titles
- duplicate clusters with borderline confidence
- poor representative image selections
- days lacking human journal text

### 16.5 Optional later enhancement
A small local review site may be added later, but the file-based workflow is the initial requirement.

---

## 17. Rendering Requirements

### 17.1 Required site views

#### Home
- trip hero image
- dates
- route/region summary
- entry points to Story / Map / Everything / Slideshow / Print

#### Story
- day-by-day narrative flow
- embedded maps where useful
- representative images with alternate access

#### Day page
- date header
- local summary
- optional day map
- event sections
- image clusters

#### Event page
- title
- time range
- place link
- hero image
- narrative summary
- related subject clusters
- alternate images

#### Place page
- place metadata
- gallery from that place
- related events
- external links if available
- optional mini-map

#### Map page
- interactive zoomable map
- low-zoom clustering
- marker popups with photos + navigation links

#### Everything page
- long scroll composition
- mostly chronological
- integrated maps, narrative, and images

#### Slideshow
- image-forward full-screen or near full-screen view
- keyboard support

#### Print page/export
- print-optimized HTML and/or PDF

### 17.2 Visual design guidance
- editorial, magazine-like feel
- generous image sizes
- restrained metadata display
- minimal attribution display
- avoid dashboard-like clutter

### 17.3 Responsive requirements
The site should work on:
- desktop (primary target)
- tablet
- phone (functional, though not necessarily equally rich)

---

## 18. Map Requirements

### 18.1 Whole-trip map
The whole-trip map shall:
- show deduped representative assets and/or event/place markers
- support zoom and pan
- support clustered markers at low zoom
- allow navigation to subpages
- optionally show route lines if chronology is stable enough

### 18.2 Day/local maps
Day maps shall:
- focus on a smaller area such as a city walk or hike
- optionally show route/movement polylines
- link to event sections
- have simpler, more contextual presentation than the main map page

### 18.3 Marker popup contents
A marker popup should contain:
- representative image thumbnail
- title
- date or time context
- short snippet
- links to story/day/event/place page

### 18.4 Filtering
First-release filtering may include:
- all trip
- specific day
- specific place type
- specific subject type

### 18.5 Data privacy
Map tiles and linked services should be selected with privacy in mind. Avoid leaking unnecessary private metadata in outbound requests.

---

## 19. CLI Requirements

The system should expose a CLI suitable for scripting.

### Suggested commands

```bash
travelogue trip init <trip-id>
travelogue ingest photos <trip-id>
travelogue ingest journals <trip-id>
travelogue analyze dedup <trip-id>
travelogue analyze structure <trip-id>
travelogue enrich ai <trip-id> --provider openai
travelogue review report <trip-id>
travelogue publish site <trip-id>
travelogue publish print <trip-id>
travelogue serve preview <trip-id>
travelogue deploy aws <trip-id>
```

### CLI behavior requirements
- commands must be composable
- commands should be idempotent where practical
- commands should support dry-run or report-only modes where useful
- logging should be clear and stage-based

---

## 20. Configuration Requirements

### Trip configuration (`trip.yaml`)
Suggested fields:

```yaml
trip:
  id: australia-2026
  title: Australia 2026
  timezone: Australia/Sydney

people:
  - id: p1
    display_name: Paul
    source_label: paul_phone
  - id: p2
    display_name: Companion
    source_label: companion_phone

inputs:
  photos:
    - path: imports/originals/paul_phone
      person_id: p1
    - path: imports/originals/companion_phone
      person_id: p2
  journals:
    - journals/

ai:
  default_provider: openai
  providers:
    openai:
      model: gpt-5-class
    gemini:
      model: gemini-class
  tasks:
    summarize_days: true
    summarize_events: true
    label_subjects: true
    draft_missing_journal_text: true

render:
  theme: editorial
  default_view: everything
  show_maps: true
  attribution: minimal

deploy:
  target: aws
  private: true
  bucket: travelogue-private-site
  distribution_id: EXAMPLE
```

### Config requirements
- explicit defaults
- environment variable support for secrets
- task-level enable/disable switches

---

## 21. Deployment Design

### 21.1 Recommended target
- S3 for static assets
- CloudFront for CDN and authenticated/private access

### 21.2 Requirements
- no secrets embedded in published frontend bundles
- support local preview
- media assets should not be world-readable by default

### 21.3 Suggested deployment modes
1. local preview only
2. publish to static folder
3. sync to S3
4. optional CloudFront invalidation

### 21.4 Auth note
Exact auth implementation can be deferred, but the implementation must preserve a deployment shape compatible with private CloudFront-backed access.

---

## 22. Testing Requirements

### 22.1 Unit tests
- EXIF extraction
- journal date parsing
- checksum stability
- clustering helpers
- override precedence resolution
- AI response schema validation

### 22.2 Integration tests
- end-to-end small sample trip
- rerun without changes uses cache
- override content persists across reruns
- site generation emits valid pages/assets

### 22.3 Visual/manual tests
- whole-trip map usability
- Everything page readability
- duplicate reveal workflow
- print export quality

### 22.4 Fixture requirements
Create a small synthetic/sample trip fixture with:
- 2 people
- 10–20 photos
- a few near-duplicates
- a couple of journal entries
- city and hike examples

---

## 23. Observability and Debuggability

The system should produce:
- stage-level logs
- per-trip reports
- AI call logs/artifacts
- clustering summaries
- low-confidence review report
- render manifest

This is especially important for investigating wrong event splits, poor dedup choices, or unexpected AI outputs.

---

## 24. Security and Privacy Requirements

- do not mutate source imports
- keep originals private unless explicitly exported
- segregate build-time secrets from render-time assets
- minimize personally identifying metadata exposed in frontend output
- avoid unintentionally exposing precise GPS in contexts where that is undesirable; provide a configuration option for coarse location display later

---

## 25. Acceptance Criteria for v1

The implementation is acceptable for v1 if all of the following are true:

1. A user can create a trip workspace and point it at one or more exported photo directories and a journal directory.
2. The system ingests photos and journals and extracts timestamps/GPS/metadata.
3. The system groups exact and near-duplicate images and selects representatives.
4. The system segments the trip into days and reasonable event groupings.
5. The system can call at least one AI provider to generate draft day/event summaries and titles.
6. Human override markdown/YAML files survive reruns unchanged and take precedence in rendering.
7. The generated site includes:
   - Story view
   - Map view
   - Everything view
   - Slideshow view
   - Day and event pages
8. The map view supports zooming, marker popups, and navigation to subpages.
9. The Everything view presents a long-form scrollable representation of the trip.
10. Duplicate alternates are accessible from representative images.
11. The system generates a print-oriented HTML/PDF export.
12. The output can be previewed locally.
13. The site can be deployed as static assets suitable for a private AWS hosting setup.

---

## 26. Suggested Implementation Plan

### Milestone 1: Foundation
- repo scaffolding
- config loader
- trip workspace init
- ingest photos/journals
- SQLite schema
- derivative generation

### Milestone 2: Deterministic analysis
- checksums
- EXIF normalization
- perceptual hashing
- near-duplicate clustering
- representative ranking
- day/event segmentation
- place clustering baseline

### Milestone 3: Rendering baseline
- static templates
- home/day/event pages
- Story view
- local preview

### Milestone 4: Maps + Everything view
- map data generation
- whole-trip map
- day maps
- Everything view composition

### Milestone 5: AI layer + review
- provider abstraction
- OpenAI adapter
- Gemini adapter or placeholder interface
- generated drafts
- review reports
- overrides precedence

### Milestone 6: Print + deployment
- print templates
- PDF export
- S3/CloudFront deployment hooks

### Milestone 7: Polish
- slideshow
- better thematic clusters
- improved aesthetics and transitions

---

## 27. Open Questions / Explicit Deferrals

These are not blockers for v1 but should be documented:

- exact authentication mechanism at CloudFront edge
- whether to expose full-resolution downloads to viewers
- whether to support video thumbnails/playback in v1 or defer
- whether to add GPX routes in v1.5
- whether to include offline packaging/export of the static site
- whether to hide or coarsen exact location data for privacy-sensitive trips

---

## 28. Implementation Notes for an Agent

An implementation agent should treat the following as hard requirements:

- preserve human edits; generated files and override files must be separate
- prefer static output over a database-backed runtime web app
- design around reusable trip workspaces
- keep maps as a first-class feature, not an afterthought
- implement dedup as clustering with alternates, not destructive deletion
- create the Everything view as a deliberate composition layer
- produce a print-specific output, not merely a browser print of the story view
- make the AI layer pluggable and cacheable

The agent should prefer straightforward, inspectable implementations over overly clever ones.

---

## 29. Recommended First Deliverable

The recommended first working deliverable is a command-line tool that can:

1. initialize a trip
2. ingest 10–20 sample photos and markdown entries
3. compute metadata and near-duplicate clusters
4. generate a basic Story view, Map view, and Everything view
5. preview locally

Once that path is working, add AI enrichment, print export, and deployment.

---

## 30. Summary

This system should be implemented as a Python-based travelogue compiler that transforms photo exports and markdown journals into a private, polished, map-rich static travel publication. The distinguishing features are:

- strong static-first architecture
- map-centric navigation
- Everything long-scroll view
- layered human/AI narrative workflow
- representative-photo dedup with alternate access
- print/photobook readiness

The resulting output should be pleasant enough to browse like a magazine, useful enough to preserve a trip’s details, and structured enough to be repeatable across many future trips.
