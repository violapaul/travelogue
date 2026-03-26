# Travelogue

A Python CLI tool that compiles exported photo libraries and markdown journals into a private, map-rich static travelogue website.

## Features

- Ingests photos from one or more exported directories (JPEG, HEIC, PNG)
- Extracts EXIF metadata, GPS coordinates, and timestamps
- Near-duplicate clustering using perceptual hashing + Gemini Embedding 2
- Semantic subject clustering (wildlife, food, architecture, etc.) via embeddings + HDBSCAN
- Day/event segmentation from timestamps and GPS movement
- Reverse geocoding of places (offline, via GeoNames)
- GPX/KML route overlay on maps
- AI enrichment via Gemini: day summaries, event titles, subject labels, draft journal entries
- Human-edit-safe: override files are never overwritten by generated content
- Generates a static site with Story, Map, Everything, and Slideshow views
- MapLibre GL JS for interactive maps with clustered markers

## Installation

```bash
# Create the conda environment (uses conda-forge for numpy/sklearn/hdbscan prebuilts)
conda env create -f environment.yml

conda activate travelogue
```

For updates after pulling new code:

```bash
conda env update -f environment.yml --prune
```

## Quick Start

```bash
# Initialize a new trip workspace
travelogue trip init australia-2026

# Copy your exported photos into the workspace
cp -r ~/Downloads/MyPhotos trips/australia-2026/imports/originals/my_phone/

# Ingest photos and journals
travelogue ingest photos australia-2026
travelogue ingest journals australia-2026

# Run analysis (dedup, segmentation, clustering)
travelogue analyze australia-2026

# Enrich with AI (requires GEMINI_API_KEY env var)
travelogue enrich australia-2026

# Generate and preview the site
travelogue publish australia-2026
travelogue serve australia-2026
```

## Configuration

Each trip has a `trip.yaml` in its workspace directory. See `trips/australia-2026/trip.yaml` after running `travelogue trip init`.

## Environment Variables

- `GEMINI_API_KEY` — Google Gemini API key (required for embedding and enrichment)
- `AWS_PROFILE` — AWS profile for S3 deployment (optional)

## Project Structure

```
src/travelogue/
  cli.py          — CLI entry point (typer)
  config.py       — trip.yaml loader + Pydantic config models
  models.py       — core domain models
  db.py           — SQLite schema + query helpers
  ingest/         — photo + journal ingestion
  analysis/       — embeddings, dedup, segmentation, clustering
  ai/             — Gemini generative enrichment
  publish/        — Jinja2 rendering + map data generation
  deploy/         — local preview + S3 sync
  templates/      — HTML templates
  static/         — CSS + JS assets
trips/            — trip workspaces (gitignored originals, tracked overrides)
tests/            — unit + integration tests
```
