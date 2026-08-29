"""
Athens Tonight — music scraper (more.com)

WHAT THIS DOES
1. Visits more.com's full music/concert listing page (all of Greece, one
   long page — confirmed NOT paginated or lazy-loaded, 26/8/2026).
2. Parses each event's schema.org markup: title, venue(s), ISO start/end
   datetime, and genre tags.
3. Keeps only events with at least one Attica venue (addressRegion ==
   "Αττική") — more.com lists all of Greece on one page, we filter here.
4. Writes everything to data/music_more.json.

STATUS (26/8/2026): selectors confirmed against the real page via browser
inspector — this is not guesswork, same process as the cinema scraper.

KNOWN STRUCTURE, confirmed via inspector:
    <article itemtype="http://schema.org/Event"
             class="... music musicmetal ...">
      <meta itemprop="url" content="/gr-el/tickets/music/satyricon/">
      <meta itemprop="startDate" content="2026-09-18T20:00">
      <meta itemprop="endDate" content="2026-09-18T20:00">
      <a id="ItemLink">
        <div class="playinfo">
          <h3 class="playinfo__title" itemprop="name">Satyricon</h3>
          <div class="playinfo__venue" itemprop="location" itemscope
               itemtype="http://schema.org/Place">
            <span id="PlayVenue" itemprop="name">Floyd</span>
            <div itemprop="address" itemscope
                 itemtype="http://schema.org/PostalAddress">
              <meta itemprop="streetAddress" content="...">
              <meta itemprop="addressLocality" content="Αθήνα">
              <meta itemprop="addressRegion" content="Αττική">
            </div>
          </div>
          <!-- Multi-city tours repeat the venue block above MORE THAN
               ONCE inside the same article — must collect all of them,
               not just the first, or non-Athens tour dates get lost. -->
        </div>
      </a>
    </article>

Genre: comes from the article's own class list, as a bare token matching
'music' + lowercase letters (e.g. 'musicmetal', 'musicrock') — separate
from the date-suffixed version of the same token (e.g. 'musicmetald20260918'
also appears and is NOT the one we want).

Run locally first with: python scripts/scrape_music_more.py
and check data/music_more.json looks sane before relying on automation.
"""

import json
import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.more.com"
LISTING_URL = f"{BASE}/gr-el/tickets/music/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "music_more.json"

TARGET_REGION = "Αττική"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

GENRE_CLASS_RE = re.compile(r"^music([a-z]+)$")


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def extract_genres(article) -> list[str]:
    """Genre tags are bare 'music<genre>' classes on the <article>, e.g.
    'musicmetal'. The literal 'music' class (top-level category, not a
    genre) is excluded, as is the date-suffixed variant like
    'musicmetald20260918' (GENRE_CLASS_RE requires letters only, no
    digits, so the suffixed one won't match)."""
    genres = []
    for cls in article.get("class", []):
        m = GENRE_CLASS_RE.match(cls)
        if m:
            genres.append(m.group(1))
    return genres


def meta_content(scope, itemprop: str) -> str:
    el = scope.select_one(f'meta[itemprop="{itemprop}"]')
    return el.get("content", "") if el else ""


def extract_venues(article) -> list[dict]:
    """An event can list MULTIPLE venues (multi-city tours repeat the
    venue block) — collect all of them, then the caller filters to
    Attica ones."""
    venues = []
    for venue_block in article.select("div.playinfo__venue"):
        name_el = venue_block.select_one("#PlayVenue")
        name = name_el.get_text(strip=True) if name_el else ""

        address_block = venue_block.select_one(
            'div[itemprop="address"]'
        )
        if address_block:
            locality = meta_content(address_block, "addressLocality")
            region = meta_content(address_block, "addressRegion")
            street = meta_content(address_block, "streetAddress")
        else:
            locality = region = street = ""

        venues.append({
            "name": name,
            "locality": locality,
            "region": region,
            "street": street,
        })
    return venues


def parse_events() -> list[dict]:
    soup = get_soup(LISTING_URL)
    events = []

    for article in soup.select('article[itemtype="http://schema.org/Event"]'):
        title_el = article.select_one("h3.playinfo__title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        url_path = meta_content(article, "url")
        full_url = url_path if url_path.startswith("http") else BASE + url_path

        start = meta_content(article, "startDate")
        end = meta_content(article, "endDate")
        genres = extract_genres(article)

        all_venues = extract_venues(article)
        attica_venues = [v for v in all_venues if v["region"] == TARGET_REGION]
        if not attica_venues:
            continue  # this event has no Attica date at all — skip entirely

        events.append({
            "title": title,
            "url": full_url,
            "start": start,
            "end": end,
            "genres": genres,
            "venues": attica_venues,
            "source": "more.com",
        })

    return events


def load_previous_first_seen(output_path: Path, key_field: str = "url") -> dict[str, str]:
    """Return {key: first_seen_date} read from the PREVIOUS run's output
    file (still sitting on disk before this run overwrites it). Used so
    a still-running event keeps the date it was originally announced
    instead of getting reset to "today" every single day it's re-scraped.
    Returns {} on first run (no previous file) or if it can't be parsed
    for any reason — safe default, just means everything in this run
    gets stamped as first-seen today, same as a genuine first run would."""
    if not output_path.exists():
        return {}
    try:
        old_data = json.loads(output_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    result = {}
    for ev in old_data.get("events", []):
        key = ev.get(key_field)
        first_seen = ev.get("first_seen")
        if key and first_seen:
            result[key] = first_seen
    return result


def main():
    print("Fetching more.com music listings...")
    events = parse_events()
    print(f"Found {len(events)} events with at least one Attica date")

    today_iso = date.today().isoformat()
    previous_first_seen = load_previous_first_seen(OUTPUT_PATH, key_field="url")
    new_today = sum(1 for ev in events if ev["url"] not in previous_first_seen)
    for ev in events:
        ev["first_seen"] = previous_first_seen.get(ev["url"], today_iso)
    print(f"  {new_today} newly-announced event(s) since the last run")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {"updated": today_iso, "events": events},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
