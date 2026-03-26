"""Travelogue CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from travelogue import __version__

app = typer.Typer(
    name="travelogue",
    help="Compile photo exports and markdown journals into a static travelogue site.",
    no_args_is_help=True,
)
console = Console()

# Sub-command groups
trip_app = typer.Typer(help="Manage trip workspaces.", no_args_is_help=True)
ingest_app = typer.Typer(help="Ingest photos and journals.", no_args_is_help=True)
analyze_app = typer.Typer(help="Run analysis pipeline.", no_args_is_help=True)

app.add_typer(trip_app, name="trip")
app.add_typer(ingest_app, name="ingest")
app.add_typer(analyze_app, name="analyze")

DEFAULT_TRIPS_ROOT = Path("trips")

# Global verbosity state set by callback
_verbosity: str = "normal"


@app.callback()
def global_options(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show DEBUG-level detail per item."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Show WARNING and above only."),
) -> None:
    global _verbosity
    if verbose:
        _verbosity = "verbose"
    elif quiet:
        _verbosity = "quiet"
    else:
        _verbosity = "normal"

    from travelogue.logging_config import setup_logging
    setup_logging(_verbosity, console=console)


def get_trips_root() -> Path:
    import os
    root = os.environ.get("TRAVELOGUE_TRIPS_ROOT")
    return Path(root) if root else DEFAULT_TRIPS_ROOT


def require_trip(trip_id: str) -> tuple[Path, Path]:
    from travelogue.db import get_db_path
    trips_root = get_trips_root()
    trip_dir = trips_root / trip_id
    if not trip_dir.exists():
        console.print(f"[red]Trip '{trip_id}' not found at {trip_dir}[/red]")
        console.print(f"Run: [bold]travelogue trip init {trip_id}[/bold]")
        raise typer.Exit(1)
    db_path = get_db_path(trips_root, trip_id)
    return trip_dir, db_path


# ---------------------------------------------------------------------------
# trip commands
# ---------------------------------------------------------------------------

@trip_app.command("init")
def trip_init(
    trip_id: str = typer.Argument(..., help="Unique trip identifier, e.g. australia-2026"),
    title: Annotated[Optional[str], typer.Option("--title", "-t")] = None,
) -> None:
    """Initialize a new trip workspace."""
    import logging
    from travelogue.config import TRIP_YAML_TEMPLATE, TripConfig
    from travelogue.db import connect, get_db_path, init_db

    log = logging.getLogger("travelogue.cli")

    trips_root = get_trips_root()
    trip_dir = trips_root / trip_id

    if trip_dir.exists():
        log.warning("Trip '%s' already exists at %s", trip_id, trip_dir)
        raise typer.Exit(0)

    for subdir in [
        "imports/originals", "journals",
        "overrides/days", "overrides/events", "overrides/places", "overrides/subjects",
        "generated/ai", "generated/reports", "working", "publish",
    ]:
        (trip_dir / subdir).mkdir(parents=True, exist_ok=True)

    resolved_title = title or trip_id.replace("-", " ").title()
    trip_yaml = trip_dir / "trip.yaml"
    trip_yaml.write_text(TRIP_YAML_TEMPLATE.format(trip_id=trip_id, title=resolved_title))

    db_path = get_db_path(trips_root, trip_id)
    init_db(db_path)

    cfg = TripConfig.load(trip_yaml)
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

    log.info("Trip '%s' initialized at %s", trip_id, trip_dir)
    console.print(
        Panel(
            f"[green]Trip workspace created:[/green] {trip_dir}\n\n"
            f"Next steps:\n"
            f"  1. Edit [bold]{trip_yaml}[/bold]\n"
            f"  2. Copy photos into [bold]{trip_dir}/imports/originals/<source>/[/bold]\n"
            f"  3. Run: [bold]travelogue ingest photos {trip_id}[/bold]",
            title=f"Trip '{trip_id}' initialized",
        )
    )


@trip_app.command("info")
def trip_info(trip_id: str = typer.Argument(...)) -> None:
    """Show trip workspace info and asset counts."""
    from travelogue.config import TripConfig
    from travelogue.db import connect

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")

    with connect(db_path) as conn:
        asset_count = conn.execute("SELECT COUNT(*) FROM assets WHERE trip_id=?", (trip_id,)).fetchone()[0]
        day_count = conn.execute("SELECT COUNT(*) FROM days WHERE trip_id=?", (trip_id,)).fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM events WHERE trip_id=?", (trip_id,)).fetchone()[0]
        embedded = conn.execute(
            "SELECT COUNT(*) FROM asset_features WHERE embedding IS NOT NULL AND asset_id IN "
            "(SELECT id FROM assets WHERE trip_id=?)", (trip_id,)
        ).fetchone()[0]
        clusters = conn.execute("SELECT COUNT(*) FROM near_duplicate_clusters WHERE trip_id=?", (trip_id,)).fetchone()[0]

    console.print(Panel(
        f"[bold]{cfg.trip.title}[/bold]\n"
        f"Timezone: {cfg.trip.timezone}\n"
        f"Assets:   {asset_count}  ({embedded} embedded)\n"
        f"Days:     {day_count}\n"
        f"Events:   {event_count}\n"
        f"Dup clusters: {clusters}",
        title=f"Trip: {trip_id}",
    ))


# ---------------------------------------------------------------------------
# ingest commands
# ---------------------------------------------------------------------------

@ingest_app.command("photos")
def ingest_photos(
    trip_id: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan only, don't write to DB"),
) -> None:
    """Scan photo directories, extract EXIF, generate derivatives, and store in DB."""
    from travelogue.config import TripConfig
    from travelogue.ingest.photos import ingest_photo_dirs
    import logging
    log = logging.getLogger("travelogue.cli")

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")
    log.info("Ingesting photos for trip: %s", trip_id)
    ingest_photo_dirs(trip_id, trip_dir, db_path, cfg, dry_run=dry_run)


@ingest_app.command("journals")
def ingest_journals(trip_id: str = typer.Argument(...)) -> None:
    """Parse markdown journals and store in DB."""
    from travelogue.config import TripConfig
    from travelogue.ingest.journals import ingest_journal_dirs
    import logging
    log = logging.getLogger("travelogue.cli")

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")
    log.info("Ingesting journals for trip: %s", trip_id)
    ingest_journal_dirs(trip_id, trip_dir, db_path, cfg)


# ---------------------------------------------------------------------------
# analyze commands
# ---------------------------------------------------------------------------

@analyze_app.command("all")
def analyze_all(
    trip_id: str = typer.Argument(...),
    skip_embeddings: bool = typer.Option(False, "--skip-embeddings"),
) -> None:
    """Run the full analysis pipeline: embeddings, dedup, segmentation, clustering."""
    import logging
    from travelogue.analysis.embeddings import compute_embeddings
    from travelogue.analysis.dedup import cluster_duplicates
    from travelogue.analysis.structure import segment_trip
    from travelogue.analysis.subjects import cluster_subjects
    from travelogue.config import TripConfig

    log = logging.getLogger("travelogue.cli")
    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")

    if not skip_embeddings:
        log.info("Stage 2a: Computing Gemini embeddings")
        compute_embeddings(trip_id, trip_dir, db_path, cfg)

    log.info("Stage 2b: Clustering duplicates")
    cluster_duplicates(trip_id, db_path)

    log.info("Stage 2c/d: Segmenting days, events, places")
    segment_trip(trip_id, db_path, cfg)

    log.info("Stage 2e: Clustering subjects")
    cluster_subjects(trip_id, db_path)

    log.info("Analysis complete")


@analyze_app.command("embeddings")
def analyze_embeddings(trip_id: str = typer.Argument(...)) -> None:
    """Compute (or refresh) Gemini embeddings for all un-embedded assets."""
    import logging
    from travelogue.analysis.embeddings import compute_embeddings
    from travelogue.config import TripConfig

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")
    logging.getLogger("travelogue.cli").info("Computing embeddings for trip: %s", trip_id)
    compute_embeddings(trip_id, trip_dir, db_path, cfg)


# ---------------------------------------------------------------------------
# enrich command
# ---------------------------------------------------------------------------

@app.command("enrich")
def enrich(
    trip_id: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", help="Rerun even if cached"),
) -> None:
    """Call Gemini to generate day/event summaries, titles, and subject labels."""
    import logging
    from travelogue.ai.gemini import enrich_trip
    from travelogue.config import TripConfig

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")
    logging.getLogger("travelogue.cli").info("AI enrichment for trip: %s", trip_id)
    enrich_trip(trip_id, trip_dir, db_path, cfg, force=force)


# ---------------------------------------------------------------------------
# publish command
# ---------------------------------------------------------------------------

@app.command("publish")
def publish(trip_id: str = typer.Argument(...)) -> None:
    """Compile the trip graph and render the static site."""
    import logging
    from travelogue.publish.renderer import render_site
    from travelogue.config import TripConfig

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")
    logging.getLogger("travelogue.cli").info("Publishing trip: %s", trip_id)
    render_site(trip_id, trip_dir, db_path, cfg)
    console.print(f"[green]Site written to:[/green] {trip_dir / 'publish'}")


# ---------------------------------------------------------------------------
# serve command
# ---------------------------------------------------------------------------

@app.command("serve")
def serve(
    trip_id: str = typer.Argument(...),
    port: int = typer.Option(8080, "--port", "-p"),
) -> None:
    """Start a local preview server for the generated site."""
    from travelogue.deploy.preview import serve_site
    import logging

    trip_dir, _ = require_trip(trip_id)
    publish_dir = trip_dir / "publish"
    if not publish_dir.exists() or not any(publish_dir.iterdir()):
        console.print(f"[yellow]No published site found. Run:[/yellow] travelogue publish {trip_id}")
        raise typer.Exit(1)
    logging.getLogger("travelogue.cli").info("Serving %s at http://localhost:%d", trip_id, port)
    console.print(f"[bold]Serving[/bold] http://localhost:{port}  (Ctrl-C to stop)")
    serve_site(publish_dir, port=port)


# ---------------------------------------------------------------------------
# review command
# ---------------------------------------------------------------------------

@app.command("review")
def review(trip_id: str = typer.Argument(...)) -> None:
    """Generate a review report highlighting low-confidence items."""
    from travelogue.publish.compiler import generate_review_report
    import logging

    trip_dir, db_path = require_trip(trip_id)
    report_path = generate_review_report(trip_id, trip_dir, db_path)
    logging.getLogger("travelogue.cli").info("Review report written to: %s", report_path)
    console.print(f"[green]Review report:[/green] {report_path}")


# ---------------------------------------------------------------------------
# version command
# ---------------------------------------------------------------------------

@app.command("version")
def version_cmd() -> None:
    """Show travelogue version."""
    console.print(f"travelogue {__version__}")


if __name__ == "__main__":
    app()
