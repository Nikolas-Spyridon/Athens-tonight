"""
Athens Tonight — theatre scraper (ticketservices.gr)

WHAT THIS DOES (intended)
1. Visits ticketservices.gr's theatre listing page.
2. Parses each event card: title, venue, date(s), URL.
3. Filters to Attica using the site's own area ids — data-areaids="1"
   for Athens and "14" for the rest of Attica, same mapping already
   confirmed for the music scraper.
4. Writes everything to data/theatre_ticketservices.json.

STATUS (31/8/2026) — READ THIS BEFORE TRUSTING THE OUTPUT:
This is NOT verified against real markup the way the rest of the
project is, and I want to be upfront about why: I fetched
https://www.ticketservices.gr/el/theatre/ and confirmed the page
exists, uses windows-1253 encoding (same as music), and lists events at
URLs like /event/<slug>/?lang=el — but my fetch tool only gave me
text-extracted content, not the raw HTML. I could not see the actual
DOM structure (card class names, whether data-areaids is even present
on this page, how multi-date/multi-venue runs are marked up).

Everything selector-shaped below — CARD_SELECTOR, the data-areaids
lookup, the date/venue parsing — is carried over from the music
scraper BY ASSUMPTION, not confirmed. Given this project's rule
("never guess at selectors — validate against real markup"), treat
this file as a first draft / starting point, not a working scraper.

TO MAKE THIS REAL:
1. Open https://www.ticketservices.gr/el/theatre/ in a browser,
   DevTools open.
2. Confirm the actual card container selector and whether
   data-areaids exists on this page (theatre may be structured
   differently from the LiveConcerts page the music scraper targets).
3. Save a real HTML snapshot locally and adjust CARD_SELECTOR /
   the field extraction below to match, the same way every other
   scraper in this project was built.
4. Only then wire this into a GitHub Actions workflow.

Run locally first with: python scripts/scrape_theatre_ticketservices.py
and inspect data/theatre_ticketservices.json — if it comes back empty
or nonsensical, that's the selectors, not the site.
"""

import json
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ticketservices.gr"
LISTING_URL = f"{BASE}/el/theatre/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "theatre_ticketservices.json"

# Confirmed for the music scraper's LiveConcerts page — UNCONFIRMED here.
ATTICA_AREA_IDS = {"1", "14"}  # 1 = Athens, 14 = rest of Attica

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# UNCONFIRMED GUESS — replace with the real container once inspected.
CARD_SELECTOR = "div.event-item"


def get_soup(url: str) -> BeautifulSoup:
    # windows-1253 confirmed for this domain (mirrors the music scraper).
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.encoding = "windows-1253"
    return BeautifulSoup(resp.text, "html.parser")


def parse_events() -> list[dict]:
    soup = get_soup(LISTING_URL)
    events = []

    for card in soup.select(CARD_SELECTOR):
        link_el = card.select_one("a")
        if not link_el or not link_el.get("href"):
            continue
        href = link_el["href"]
        full_url = href if href.startswith("http") else BASE + href

        title_el = card.select_one(".event-title") or link_el
        title = title_el.get_text(strip=True)

        venue_el = card.select_one(".event-venue")
        venue = venue_el.get_text(strip=True) if venue_el else ""

        date_el = card.select_one(".event-date")
        date_text = date_el.get_text(strip=True) if date_el else ""

        area_id = card.get("data-areaids", "")
        # UNCONFIRMED: never silently drop on uncertain region — if this
        # page doesn't actually carry data-areaids, area_id will always
        # be "" and EVERY event will be flagged unconfirmed rather than
        # dropped, which is the correct failure mode per project rules,
        # but also a loud signal that this selector needs fixing.
        is_attica = area_id in ATTICA_AREA_IDS
        region_confirmed = bool(area_id)

        if not is_attica and region_confirmed:
            continue  # confirmed non-Attica — safe to skip

        events.append({
            "title": title,
            "url": full_url,
            "venue": venue,
            "date_text": date_text,
            "region_confirmed": region_confirmed,
            "source": "ticketservices.gr",
        })

    return events


def main():
    print("Fetching ticketservices.gr theatre listings...")
    events = parse_events()
    unconfirmed = sum(1 for e in events if not e["region_confirmed"])
    print(f"Found {len(events)} events ({unconfirmed} with unconfirmed region)")
    if unconfirmed == len(events) and events:
        print("WARNING: every event is unconfirmed — CARD_SELECTOR or "
              "data-areaids is almost certainly wrong. Inspect real HTML.")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {"updated": date.today().isoformat(), "events": events},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
