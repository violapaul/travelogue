"""Journal ingestion: parse markdown files, infer dates, store in DB."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path

from markdown_it import MarkdownIt

from travelogue.config import TripConfig
from travelogue.db import connect

log = logging.getLogger(__name__)

_DATE_PATTERNS = [
    (r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", "iso", 0.95),
    (r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})[,\s]+(\d{4})\b", "mdy_long", 0.90),
    (r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b", "dmy_long", 0.90),
    (r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\b", "md_long", 0.50),
    (r"\bDay\s+(\d{1,2})\b", "day_number", 0.30),
]

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
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
    for pattern, kind, conf in _DATE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        try:
            if kind == "iso":
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), conf
            elif kind == "mdy_long":
                return date(int(m.group(3)), _MONTH_MAP[m.group(1).lower()], int(m.group(2))), conf
            elif kind == "dmy_long":
                return date(int(m.group(3)), _MONTH_MAP[m.group(2).lower()], int(m.group(1))), conf
            elif kind == "md_long" and context_year:
                return date(context_year, _MONTH_MAP[m.group(1).lower()], int(m.group(2))), conf
        except (ValueError, KeyError):
            continue
    return None, 0.0


def _extract_headings(tokens: list) -> list[dict]:
    headings = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            level = int(tok.tag[1])
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                headings.append({"level": level, "text": tokens[i + 1].content})
    return headings


def ingest_journal_dirs(
    trip_id: str,
    trip_dir: Path,
    db_path: Path,
    cfg: TripConfig,
) -> int:
    """Ingest all journal markdown files. Returns count of files processed."""
    md_parser = MarkdownIt()
    count = 0
    context_year: int | None = None

    journal_dirs = [trip_dir / jd for jd in cfg.inputs.journals]

    for jdir in journal_dirs:
        if not jdir.exists():
            log.warning("Journal directory not found: %s", jdir)
            continue

        md_files = sorted(jdir.rglob("*.md"))
        log.info("Found %d journal files in %s", len(md_files), jdir)

        for md_path in md_files:
            try:
                raw = md_path.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(raw)

                parsed_date: date | None = None
                confidence = 0.0

                if "date" in meta:
                    try:
                        parsed_date = date.fromisoformat(str(meta["date"]))
                        confidence = 0.99
                        log.debug("  %s: date from frontmatter: %s", md_path.name, parsed_date)
                    except (ValueError, TypeError):
                        pass

                if not parsed_date:
                    parsed_date, confidence = _try_parse_date(md_path.stem)
                    if parsed_date:
                        log.debug("  %s: date from filename: %s (conf=%.2f)", md_path.name, parsed_date, confidence)

                if not parsed_date:
                    tokens = md_parser.parse(body)
                    headings = _extract_headings(tokens)
                    if headings:
                        parsed_date, confidence = _try_parse_date(headings[0]["text"], context_year)
                        if parsed_date:
                            log.debug("  %s: date from heading '%s': %s", md_path.name, headings[0]["text"], parsed_date)

                if not parsed_date:
                    for line in body.splitlines()[:5]:
                        d, c = _try_parse_date(line, context_year)
                        if d and c > 0.6:
                            parsed_date, confidence = d, c
                            log.debug("  %s: date from body text: %s", md_path.name, parsed_date)
                            break

                if parsed_date:
                    context_year = parsed_date.year
                    log.info("Journal %s → %s (conf=%.2f)", md_path.name, parsed_date, confidence)
                else:
                    log.warning("Could not infer date for journal: %s", md_path.name)

                tokens = md_parser.parse(body)
                headings_struct = _extract_headings(tokens)
                rel_path = str(md_path.relative_to(trip_dir))

                with connect(db_path) as conn:
                    existing = conn.execute(
                        "SELECT id FROM journal_sources WHERE trip_id=? AND path=?",
                        (trip_id, rel_path),
                    ).fetchone()
                    if existing:
                        conn.execute(
                            """UPDATE journal_sources
                            SET raw_markdown=?, parsed_date=?, date_confidence=?, heading_structure_json=?
                            WHERE trip_id=? AND path=?""",
                            (raw, parsed_date.isoformat() if parsed_date else None,
                             confidence, json.dumps(headings_struct), trip_id, rel_path),
                        )
                        log.debug("Updated existing journal record for %s", rel_path)
                    else:
                        conn.execute(
                            """INSERT INTO journal_sources
                            (id, trip_id, path, raw_markdown, parsed_date, date_confidence, heading_structure_json)
                            VALUES (?,?,?,?,?,?,?)""",
                            (f"j_{uuid.uuid4().hex[:12]}", trip_id, rel_path, raw,
                             parsed_date.isoformat() if parsed_date else None,
                             confidence, json.dumps(headings_struct)),
                        )
                count += 1

            except Exception as exc:
                log.error("Error ingesting journal %s: %s", md_path.name, exc, exc_info=True)

    log.info("Journal ingest complete — processed %d files", count)
    return count
