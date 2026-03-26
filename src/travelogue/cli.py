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

# Default trips root — can be overridden via TRAVELOGUE_TRIPS_ROOT env var
DEFAULT_TRIPS_ROOT = Path("trips")


def get_trips_root() -> Path:
    import os

    root = os.environ.get("TRAVELOGUE_TRIPS_ROOT")
    return Path(root) if root else DEFAULT_TRIPS_ROOT


def require_trip(trip_id: str) -> tuple[Path, Path]:
    """Return (trip_dir, db_path) and abort with a helpful message if the trip doesn't exist."""
    from travelogue.db import get_db_path

    trips_root = get_trips_root()
    trip_dir = trips_root / trip_id
    if not trip_dir.exists():
        console.print(f"[red]Trip '{trip_id}' not found at {trip_dir}[/red]")
        console.print("Run: [bold]travelogue trip init {trip_id}[/bold]")
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
    from travelogue.config import TRIP_YAML_TEMPLATE
    from travelogue.db import get_db_path, init_db

    trips_root = get_trips_root()
    trip_dir = trips_root / trip_id

    if trip_dir.exists():
        console.print(f"[yellow]Trip '{trip_id}' already exists at {trip_dir}[/yellow]")
        raise typer.Exit(0)

    # Create directory structure
    for subdir in [
        "imports/originals",
        "journals",
        "overrides/days",
        "overrides/events",
        "overrides/places",
        "overrides/subjects",
        "generated/ai",
        "generated/reports",
        "working",
        "publish",
    ]:
        (trip_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Write trip.yaml
    resolved_title = title or trip_id.replace("-", " ").title()
    trip_yaml = trip_dir / "trip.yaml"
    trip_yaml.write_text(
        TRIP_YAML_TEMPLATE.format(trip_id=trip_id, title=resolved_title)
    )

    # Initialize SQLite database and insert the trip + people rows
    db_path = get_db_path(trips_root, trip_id)
    init_db(db_path)

    # Seed trip and people records from the just-written config
    from travelogue.config import TripConfig
    from travelogue.db import connect
    cfg = TripConfig.load(trip_yaml)
    with connect(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO trips (id, title, timezone, description)
            VALUES (?,?,?,?)""",
            (cfg.trip.id, cfg.trip.title, cfg.trip.timezone, cfg.trip.description),
        )
        for person in cfg.people:
            conn.execute(
                """INSERT OR IGNORE INTO people (id, trip_id, display_name, source_label, attribution_mode)
                VALUES (?,?,?,?,?)""",
                (person.id, cfg.trip.id, person.display_name, person.source_label, person.attribution_mode),
            )

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
        asset_count = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE trip_id=?", (trip_id,)
        ).fetchone()[0]
        day_count = conn.execute(
            "SELECT COUNT(*) FROM days WHERE trip_id=?", (trip_id,)
        ).fetchone()[0]
        event_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE trip_id=?", (trip_id,)
        ).fetchone()[0]

    console.print(Panel(
        f"[bold]{cfg.trip.title}[/bold]\n"
        f"Timezone: {cfg.trip.timezone}\n"
        f"Assets:   {asset_count}\n"
        f"Days:     {day_count}\n"
        f"Events:   {event_count}",
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

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")

    console.print(f"[bold]Ingesting photos for trip:[/bold] {trip_id}")
    ingest_photo_dirs(trip_id, trip_dir, db_path, cfg, dry_run=dry_run, console=console)


@ingest_app.command("journals")
def ingest_journals(
    trip_id: str = typer.Argument(...),
) -> None:
    """Parse markdown journals and store in DB."""
    from travelogue.config import TripConfig
    from travelogue.ingest.journals import ingest_journal_dirs

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")

    console.print(f"[bold]Ingesting journals for trip:[/bold] {trip_id}")
    ingest_journal_dirs(trip_id, trip_dir, db_path, cfg, console=console)


# ---------------------------------------------------------------------------
# analyze commands
# ---------------------------------------------------------------------------


@analyze_app.command("all")
def analyze_all(
    trip_id: str = typer.Argument(...),
    skip_embeddings: bool = typer.Option(
        False, "--skip-embeddings", help="Skip Gemini embedding calls (use cached only)"
    ),
) -> None:
    """Run the full analysis pipeline: embeddings, dedup, segmentation, clustering."""
    from travelogue.analysis.embeddings import compute_embeddings
    from travelogue.analysis.dedup import cluster_duplicates
    from travelogue.analysis.structure import segment_trip
    from travelogue.analysis.subjects import cluster_subjects
    from travelogue.config import TripConfig

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")

    if not skip_embeddings:
        console.print("[bold]Stage 2a:[/bold] Computing Gemini embeddings…")
        compute_embeddings(trip_id, trip_dir, db_path, cfg, console=console)

    console.print("[bold]Stage 2b:[/bold] Clustering duplicates…")
    cluster_duplicates(trip_id, db_path, console=console)

    console.print("[bold]Stage 2c/d:[/bold] Segmenting days, events, places…")
    segment_trip(trip_id, db_path, cfg, console=console)

    console.print("[bold]Stage 2e:[/bold] Clustering subjects…")
    cluster_subjects(trip_id, db_path, console=console)

    console.print("[green]Analysis complete.[/green]")


@analyze_app.command("embeddings")
def analyze_embeddings(
    trip_id: str = typer.Argument(...),
) -> None:
    """Compute (or refresh) Gemini embeddings for all un-embedded assets."""
    from travelogue.analysis.embeddings import compute_embeddings
    from travelogue.config import TripConfig

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")
    compute_embeddings(trip_id, trip_dir, db_path, cfg, console=console)


# ---------------------------------------------------------------------------
# enrich command
# ---------------------------------------------------------------------------


@app.command("enrich")
def enrich(
    trip_id: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", help="Rerun even if cached"),
) -> None:
    """Call Gemini to generate day/event summaries, titles, and subject labels."""
    from travelogue.ai.gemini import enrich_trip
    from travelogue.config import TripConfig

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")
    console.print(f"[bold]AI enrichment for trip:[/bold] {trip_id}")
    enrich_trip(trip_id, trip_dir, db_path, cfg, force=force, console=console)


# ---------------------------------------------------------------------------
# publish command
# ---------------------------------------------------------------------------


@app.command("publish")
def publish(
    trip_id: str = typer.Argument(...),
) -> None:
    """Compile the trip graph and render the static site."""
    from travelogue.publish.renderer import render_site
    from travelogue.config import TripConfig

    trip_dir, db_path = require_trip(trip_id)
    cfg = TripConfig.load(trip_dir / "trip.yaml")
    console.print(f"[bold]Publishing trip:[/bold] {trip_id}")
    render_site(trip_id, trip_dir, db_path, cfg, console=console)
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

    trip_dir, _ = require_trip(trip_id)
    publish_dir = trip_dir / "publish"
    if not publish_dir.exists() or not any(publish_dir.iterdir()):
        console.print(f"[yellow]No published site found. Run:[/yellow] travelogue publish {trip_id}")
        raise typer.Exit(1)
    console.print(f"[bold]Serving {trip_id} at[/bold] http://localhost:{port}")
    serve_site(publish_dir, port=port)


# ---------------------------------------------------------------------------
# review command
# ---------------------------------------------------------------------------


@app.command("review")
def review(
    trip_id: str = typer.Argument(...),
) -> None:
    """Generate a review report highlighting low-confidence items."""
    from travelogue.publish.compiler import generate_review_report

    trip_dir, db_path = require_trip(trip_id)
    report_path = generate_review_report(trip_id, trip_dir, db_path)
    console.print(f"[green]Review report written to:[/green] {report_path}")


# ---------------------------------------------------------------------------
# version command
# ---------------------------------------------------------------------------


@app.command("version")
def version_cmd() -> None:
    """Show travelogue version."""
    console.print(f"travelogue {__version__}")


if __name__ == "__main__":
    app()
