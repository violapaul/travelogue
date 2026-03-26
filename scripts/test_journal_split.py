"""Test script: send a multi-day travel journal to Gemini and get structured per-day sections."""

import json
import os
import textwrap
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel


JOURNAL_PATH = Path(__file__).resolve().parent.parent / "trips/nz-aus-2026/journals/aus.md"
MODEL = "gemini-2.5-flash"


class JournalSection(BaseModel):
    date: str
    title: str | None = None
    content: str


class JournalSplitResult(BaseModel):
    sections: list[JournalSection]


PROMPT_TEMPLATE = """\
You are a precise text-processing assistant. Your job is to split a multi-day \
travel journal into per-day sections.

## Rules

1. **Identify day boundaries.** Days are separated by horizontal-rule dividers \
(lines of dashes, sometimes preceded by an em-dash or backslash). A new date \
line like "Mar 14, 2026" marks the start of a new day.

2. **Normalize dates** to ISO format YYYY-MM-DD.

3. **Preserve the author's original words.** Return the content for each day \
exactly as written — do NOT rewrite, summarize, or paraphrase. You may only:
   - Un-escape markdown characters: convert `\\!` to `!`, `\\-` to `-`, \
`\\.` to `.`, etc.
   - Remove the divider lines themselves (the rows of dashes).
   - Remove the date header line (since date is in the structured field).
   - Trim leading/trailing blank lines from each section.

4. **Generate a short title** (3-8 words) for each day that captures the \
highlights or theme. Think like a chapter heading, not a first-sentence excerpt. \
Use sentence case (capitalize first word and proper nouns only). \
Examples of good titles: "Sydney to Bondi by bike", "Sailing the Whitsundays", \
"Great Ocean Walk day one". If the day is very sparse, derive from whatever is there.

5. **Handle edge cases:**
   - Some days are very sparse (e.g., just "GOW day1"). Still include them.
   - If the same date appears twice, treat them as separate sections (keep both).
   - If content after one date header flows into the next day's topic \
(e.g., arrival at a new place), keep it with the date header it appeared under.

## Input journal

```
{journal_text}
```

Return a JSON object with a `sections` array. Each section has:
- `date`: string in YYYY-MM-DD format
- `title`: short descriptive title (string or null)
- `content`: the cleaned-up markdown content for that day
"""


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY environment variable")

    journal_text = JOURNAL_PATH.read_text()
    prompt = PROMPT_TEMPLATE.format(journal_text=journal_text)

    print(f"Journal: {JOURNAL_PATH}")
    print(f"Journal length: {len(journal_text)} chars, {len(journal_text.splitlines())} lines")
    print(f"Model: {MODEL}")
    print(f"Prompt length: {len(prompt)} chars")
    print()

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=JournalSplitResult,
            temperature=0.2,
        ),
    )

    result = json.loads(response.text)
    sections = result["sections"]

    print(f"{'=' * 70}")
    print(f"Got {len(sections)} sections")
    print(f"{'=' * 70}\n")

    for i, section in enumerate(sections):
        date = section["date"]
        title = section.get("title") or "(no title)"
        content = section["content"]

        print(f"--- Section {i + 1}: {date} — {title} ---")
        preview = content[:300]
        if len(content) > 300:
            preview += "..."
        print(preview)
        print(f"  [content length: {len(content)} chars]")
        print()

    print(f"\n{'=' * 70}")
    print("FULL JSON OUTPUT")
    print(f"{'=' * 70}")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"\n{'=' * 70}")
    print("PROMPT USED")
    print(f"{'=' * 70}")
    print(prompt)


if __name__ == "__main__":
    main()
