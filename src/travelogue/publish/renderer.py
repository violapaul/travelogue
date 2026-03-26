"""Jinja2-based static site renderer."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from travelogue.config import TripConfig
from travelogue.publish.compiler import build_trip_context
from travelogue.publish.maps import generate_map_data

log = logging.getLogger(__name__)


def _assets_dir() -> Path:
    """Return the path to the package's templates and static directories."""
    return Path(__file__).parent.parent


def render_site(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
) -> None:
    """Compile and render the full static site to trip_dir/publish/."""
    publish_dir = trip_dir / "publish"
    publish_dir.mkdir(parents=True, exist_ok=True)

    templates_dir = _assets_dir() / "templates"
    static_dir = _assets_dir() / "static"

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["datefmt"] = _datefmt
    env.filters["timefmt"] = _timefmt

    log.info("Building trip context")
    ctx = build_trip_context(trip_id, trip_dir, db_path, cfg)
    log.info("  %d days, %d events", len(ctx["days"]),
             sum(len(d["events"]) for d in ctx["days"]))

    # Copy static assets
    if static_dir.exists():
        dest_static = publish_dir / "static"
        if dest_static.exists():
            shutil.rmtree(dest_static)
        shutil.copytree(static_dir, dest_static)

    # Copy image derivatives into publish/
    deriv_src = trip_dir / "working" / "derivatives"
    if deriv_src.exists():
        deriv_dest = publish_dir / "images"
        if not deriv_dest.exists():
            shutil.copytree(deriv_src, deriv_dest, dirs_exist_ok=True)
        else:
            for src_file in deriv_src.rglob("*"):
                if src_file.is_file():
                    rel = src_file.relative_to(deriv_src)
                    dest_file = deriv_dest / rel
                    if not dest_file.exists():
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dest_file)

    # Rewrite image paths to be relative to publish/
    ctx = _rewrite_image_paths(ctx, trip_dir, publish_dir)

    # Generate map data AFTER path rewriting so thumbnails use published paths
    log.info("Generating map data")
    map_data = generate_map_data(trip_id, trip_dir, db_path, ctx)
    log.info("  %d photo markers, %d event markers, %d GPX traces",
             len(map_data["markers"]["features"]),
             len(map_data["events"]["features"]),
             len(map_data["gpx_traces"]))

    # Write map_data.js
    map_js = publish_dir / "map_data.js"
    map_js.write_text(
        f"const MAP_DATA = {json.dumps(map_data, indent=2)};\n"
        f"const TRIP_CONTEXT = {json.dumps(_strip_for_js(ctx), indent=2)};\n"
    )

    # Render pages
    # `base` is a relative prefix: "" for root pages, "../" for pages in subdirs.
    # Templates use {{ base }} to build all internal URLs, so the site works
    # at any mount point (standalone at / or under /<trip-id>/ in multi mode).
    shared = {
        "trip_id": trip_id,
        "trip_title": ctx["trip_title"],
        "days": ctx["days"],
        "total_assets": ctx["total_assets"],
        "cfg": cfg,
    }

    root = {**shared, **ctx, "base": ""}
    _render_page(env, "home.html", publish_dir / "index.html", root)
    _render_page(env, "story.html", publish_dir / "story.html", root)
    _render_page(env, "map.html", publish_dir / "map.html", {**root, "map_data": map_data})
    _render_page(env, "everything.html", publish_dir / "everything.html", root)
    _render_page(env, "slideshow.html", publish_dir / "slideshow.html", root)

    # Per-day pages (one level deep)
    sub = {**shared, **ctx, "base": "../"}
    days_dir = publish_dir / "days"
    days_dir.mkdir(exist_ok=True)
    for day in ctx["days"]:
        _render_page(env, "day.html", days_dir / f"{day['date']}.html",
                     {**sub, "day": day})

    # Per-event pages (one level deep)
    events_dir = publish_dir / "events"
    events_dir.mkdir(exist_ok=True)
    for day in ctx["days"]:
        for event in day["events"]:
            _render_page(env, "event.html", events_dir / f"{event['id']}.html",
                         {**sub, "event": event, "day": day})

    n_pages = _count_pages(ctx)
    log.info("Rendered %d pages to %s", n_pages, publish_dir)


def _render_page(env: Environment, template_name: str, dest: Path, ctx: dict) -> None:
    try:
        tmpl = env.get_template(template_name)
        dest.write_text(tmpl.render(**ctx), encoding="utf-8")
    except Exception as exc:
        # Don't abort the whole render for one bad page
        dest.write_text(f"<html><body><pre>Render error: {exc}</pre></body></html>")


def _rewrite_image_paths(ctx: dict, trip_dir: Path, publish_dir: Path) -> dict:
    """Rewrite derivative paths from working/ to publish/images/ relative paths."""
    import copy
    ctx = copy.deepcopy(ctx)

    def rewrite(path: str | None) -> str | None:
        if not path:
            return None
        p = Path(path)
        # Path is relative to trip_dir: working/derivatives/thumbnails/xyz.jpg
        # -> images/thumbnails/xyz.jpg (relative to publish/)
        if path.startswith("working/derivatives/"):
            return path.replace("working/derivatives/", "images/")
        return path

    for day in ctx.get("days", []):
        for event in day.get("events", []):
            for asset in event.get("assets", []):
                asset["thumbnail_path"] = rewrite(asset.get("thumbnail_path"))
                asset["web_path"] = rewrite(asset.get("web_path"))
            if event.get("hero"):
                event["hero"]["thumbnail_path"] = rewrite(event["hero"].get("thumbnail_path"))
                event["hero"]["web_path"] = rewrite(event["hero"].get("web_path"))
    return ctx


def _strip_for_js(ctx: dict) -> dict:
    """Strip non-serializable items before embedding in JS."""
    import copy
    c = copy.deepcopy(ctx)
    c.pop("trip_dir", None)
    c.pop("cfg", None)
    return c


def _count_pages(ctx: dict) -> int:
    base = 5  # index, story, map, everything, slideshow
    base += len(ctx.get("days", []))
    base += sum(len(d["events"]) for d in ctx.get("days", []))
    return base


def _datefmt(value: str) -> str:
    try:
        from datetime import date
        return date.fromisoformat(value).strftime("%B %-d, %Y")
    except Exception:
        return value


def _timefmt(value: str | None) -> str:
    if not value:
        return ""
    try:
        from datetime import datetime
        return datetime.fromisoformat(value).strftime("%-I:%M %p")
    except Exception:
        return value or ""
