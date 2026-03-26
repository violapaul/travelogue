"""Journal ingestion: parse markdown files, infer dates, store in DB."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from pathlib import Path

from markdown_it import MarkdownIt
from rich.console import Console

from travelogue.config import TripConfig
from travelogue.db import connect

# Patterns for date detection in headings and text
_DATE_PATTERNS = [
    # 2026-03-18, 2026/03/18
    (r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", "iso", 0.95),
    # March 18, 2026 / March 18 2026
    (r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})[,\s]+(\d{4})\b", "mdy_long", 0.90),
    # 18 March 2026
    (r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b", "dmy_long", 0.90),
    # March 18 (no year — needs year from context or trip metadata)
    (r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\b", "md_long", 0.50),
    # Day N (Day 3)
    (r"\bDay\s+(\d{1,2})\b", "day_number", 0.30),
]

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from a markdown document. Returns (meta, body)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            import yaml
            try:
                meta = yaml.safe_load(text[3:end]) or {}
                return meta, text[end + 4:].lstrip()
            except Exception:
                pass
    return {}, text


def _try_parse_date(text: str, context_year: int | None = None) -> tuple[date | None, float]:
    """Try to extract a date from a string. Returns (date, confidence)."""
    for pattern, kind, conf in _DATE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue

        try:
            if kind == "iso":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return date(y, mo, d), conf

            elif kind == "mdy_long":
                month_str = m.group(1).lower()
                mo = _MONTH_MAP[month_str]
                d = int(m.group(2))
                y = int(m.group(3))
                return date(y, mo, d), conf

            elif kind == "dmy_long":
                d = int(m.group(1))
                month_str = m.group(2).lower()
                mo = _MONTH_MAP[month_str]
                y = int(m.group(3))
                return date(y, mo, d), conf

            elif kind == "md_long" and context_year:
                month_str = m.group(1).lower()
                mo = _MONTH_MAP[month_str]
                d = int(m.group(2))
                return date(context_year, mo, d), conf

        except (ValueError, KeyError):
            continue

    return None, 0.0


def _extract_headings(tokens: list) -> list[dict]:
    """Extract heading text and level from markdown-it token stream."""
    headings = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            level = int(tok.tag[1])
            # Next token is inline with the heading text
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                text = tokens[i + 1].content
                headings.append({"level": level, "text": text})
    return headings


def _split_by_headings(md_text: str, min_level: int = 2) -> list[tuple[str | None, str]]:
    """Split markdown into (heading_text|None, section_body) chunks."""
    lines = md_text.splitlines(keepends=True)
    heading_re = re.compile(r"^(#{1,6})\s+(.*)")

    sections: list[tuple[str | None, str]] = []
    current_heading: str | None = None
    current_body: list[str] = []

    for line in lines:
        m = heading_re.match(line)
        if m and len(m.group(1)) >= min_level:
            if current_heading is not None or current_body:
                sections.append((current_heading, "".join(current_body).strip()))
            current_heading = m.group(2).strip()
            current_body = []
        else:
            current_body.append(line)

    # Final section
    if current_heading is not None or current_body:
        sections.append((current_heading, "".join(current_body).strip()))

    return sections


def ingest_journal_dirs(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
    console: Console | None = None,
) -> int:
    """Ingest all journal markdown files. Returns count of files processed."""
    if console is None:
        console = Console()

    md_parser = MarkdownIt()
    count = 0
    context_year: int | None = None

    journal_dirs = [trip_dir / jd for jd in cfg.inputs.journals]

    for jdir in journal_dirs:
        if not jdir.exists():
            console.print(f"  [yellow]Warning:[/yellow] journal directory not found: {jdir}")
            continue

        md_files = sorted(jdir.rglob("*.md"))
        console.print(f"  Found {len(md_files)} journal files in {jdir}")

        for md_path in md_files:
            try:
                raw = md_path.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(raw)

                # Try frontmatter date first
                parsed_date: date | None = None
                confidence = 0.0

                if "date" in meta:
                    try:
                        parsed_date = date.fromisoformat(str(meta["date"]))
                        confidence = 0.99
                    except (ValueError, TypeError):
                        pass

                # Try filename date (e.g. 2026-03-18.md)
                if not parsed_date:
                    d, c = _try_parse_date(md_path.stem)
                    if d:
                        parsed_date = d
                        confidence = c

                # Try first heading
                if not parsed_date:
                    tokens = md_parser.parse(body)
                    headings = _extract_headings(tokens)
                    if headings:
                        d, c = _try_parse_date(headings[0]["text"], context_year)
                        if d:
                            parsed_date = d
                            confidence = c

                # Try first few lines of body
                if not parsed_date:
                    for line in body.splitlines()[:5]:
                        d, c = _try_parse_date(line, context_year)
                        if d and c > 0.6:
                            parsed_date = d
                            confidence = c
                            break

                if parsed_date and parsed_date.year:
                    context_year = parsed_date.year

                # Parse heading structure for the DB
                tokens = md_parser.parse(body)
                headings_struct = _extract_headings(tokens)

                journal_id = f"j_{uuid.uuid4().hex[:12]}"
                import json
                with connect(db_path) as conn:
                    # Check if already ingested by path
                    rel_path = str(md_path.relative_to(trip_dir))
                    existing = conn.execute(
                        "SELECT id FROM journal_sources WHERE trip_id=? AND path=?",
                        (trip_id, rel_path),
                    ).fetchone()

                    if existing:
                        # Update raw content in case file changed
                        conn.execute(
                            """UPDATE journal_sources
                            SET raw_markdown=?, parsed_date=?, date_confidence=?, heading_structure_json=?
                            WHERE trip_id=? AND path=?""",
                            (
                                raw,
                                parsed_date.isoformat() if parsed_date else None,
                                confidence,
                                json.dumps(headings_struct),
                                trip_id, rel_path,
                            ),
                        )
                    else:
                        conn.execute(
                            """INSERT INTO journal_sources
                            (id, trip_id, path, raw_markdown, parsed_date, date_confidence, heading_structure_json)
                            VALUES (?,?,?,?,?,?,?)""",
                            (
                                journal_id, trip_id, rel_path, raw,
                                parsed_date.isoformat() if parsed_date else None,
                                confidence,
                                json.dumps(headings_struct),
                            ),
                        )

                date_str = f"[green]{parsed_date}[/green] (conf={confidence:.2f})" if parsed_date else "[yellow]no date[/yellow]"
                console.print(f"  {md_path.name}: {date_str}")
                count += 1

            except Exception as exc:
                console.print(f"  [red]Error ingesting {md_path.name}:[/red] {exc}")

    console.print(f"  [green]Done.[/green] Processed {count} journal files.")
    return count
