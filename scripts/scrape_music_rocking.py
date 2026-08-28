"""
Athens Tonight — music scraper (rocking.gr)

WHAT THIS DOES
1. Visits rocking.gr's Athens agenda for the current month PLUS the next
   MONTHS_AHEAD months, using the URL pattern
   https://www.rocking.gr/agenda/{year}/{month}/athens
2. Parses each month's page for date-grouped event blocks.
3. Already scoped to Athens by the URL itself (the /athens path segment)
   — unlike ticketmaster.gr and ticketservices.gr, there's no Attica
   filtering heuristic needed here at all.
4. Writes everything to data/music_rocking.json, deduplicated by event
   URL (adjacent months' pages can't overlap in practice since each is a
   distinct calendar month, but de-duping is cheap insurance).

MONTH URL PATTERN — confirmed, with one open question
The site's own "event-months" nav bar (visible on a real saved copy of
the page, 28/8/2026) links to OTHER months using exactly this pattern:
    https://www.rocking.gr/agenda/2026/8/athens   (August)
    https://www.rocking.gr/agenda/2026/10/athens  (October)
    https://www.rocking.gr/agenda/2027/1/athens   (January 2027)
The CURRENT month specifically is linked via a shortcut with NO
year/month in the URL: https://www.rocking.gr/agenda/athens. It's
UNCONFIRMED whether the explicit .../2026/9/athens form (September, the
month this was written in) also resolves — the two forms almost
certainly alias to the same page, which is the typical pattern for this
kind of routing, but this specific case wasn't verified against the live
site. If the explicit current-month URL 404s, parse_events() falls back
to the shortcut URL for that one month only (see the try/except there).

STATUS (28/8/2026): event-block selectors confirmed against a real saved
copy of the September 2026 Athens agenda page — not guesswork. Fetching
itself has NOT been confirmed to work from GitHub Actions specifically —
this environment isn't blocked outright the way ticketmaster.gr is, same
situation as ticketservices.gr, but that's not a live-CI confirmation.
Run locally first and check the output before trusting the daily
workflow.

Run locally first with: python scripts/scrape_music_rocking.py
and check data/music_rocking.json looks sane before automating.
"""

import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.rocking.gr"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "music_rocking.json"
MONTHS_AHEAD = 3  # scrape current month + this many months forward

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

GREEK_MONTH_TO_NUM = {
    "Ιανουαρίου": 1, "Φεβρουαρίου": 2, "Μαρτίου": 3, "Απριλίου": 4,
    "Μαΐου": 5, "Ιουνίου": 6, "Ιουλίου": 7, "Αυγούστου": 8,
    "Σεπτεμβρίου": 9, "Οκτωβρίου": 10, "Νοεμβρίου": 11, "Δεκεμβρίου": 12,
}


def month_url(year: int, month: int) -> str:
    return f"{BASE}/agenda/{year}/{month}/athens"


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def parse_date_header(text: str) -> str | None:
    """'6 Σεπτεμβρίου 2026' -> '2026-09-06'. Returns None if the month
    name isn't recognized (defensive — shouldn't happen in practice)."""
    m = re.match(r"(\d{1,2})\s+(\S+)\s+(\d{4})", text.strip())
    if not m:
        return None
    day, month_name, year = m.groups()
    month_num = GREEK_MONTH_TO_NUM.get(month_name)
    if not month_num:
        return None
    return f"{year}-{month_num:02d}-{int(day):02d}"


def parse_month_page(soup: BeautifulSoup) -> list[dict]:
    """Confirmed from a real saved copy (28/8/2026) of the September 2026
    Athens agenda page:
        <div class="date-box">
          <h2>6 Σεπτεμβρίου 2026</h2>
          <div id="event-block">
            <div class="info">
              <a href="https://www.rocking.gr/agenda/2026/9/6/27483">
                <div class="groups">Parklife: Beatles Symphonic</div>
                <div class="sm-line"></div>
                <div class="city_venue">Αθήνα @ Κέντρο Πολιτισμού Ίδρυμα Σταύρος Νιάρχος</div>
                <small>Ελεύθερη είσοδος</small>
              </a>
            </div>
          </div>
          <!-- multiple event-block divs can repeat under one date-box
               when several shows share the same date -->
        </div>
        <!-- id="event-block" repeats across the page (not unique, which
             is invalid HTML but harmless — CSS id-selectors still match
             every occurrence) -->
    """
    events = []
    for date_box in soup.select("div.date-box"):
        header = date_box.select_one("h2")
        if not header:
            continue
        date_iso = parse_date_header(header.get_text(strip=True))
        if not date_iso:
            continue

        for block in date_box.select("div#event-block div.info a[href]"):
            title_el = block.select_one("div.groups")
            venue_el = block.select_one("div.city_venue")
            price_el = block.select_one("small")

            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            # "Αθήνα @ Venue Name" -> just the venue part
            venue_raw = venue_el.get_text(strip=True) if venue_el else ""
            venue = venue_raw.split("@", 1)[1].strip() if "@" in venue_raw else venue_raw

            events.append({
                "title": title,
                "url": block["href"].strip(),
                "date": date_iso,
                "venue": venue,
                "price_info": price_el.get_text(strip=True) if price_el else "",
                "source": "rocking.gr",
            })

    return events


def fetch_month(year: int, month: int, is_current_month: bool) -> list[dict]:
    try:
        soup = get_soup(month_url(year, month))
    except requests.exceptions.HTTPError:
        if not is_current_month:
            raise
        # See the "MONTH URL PATTERN" note at the top of this file —
        # fall back to the no-year/month shortcut for the current month
        # only, in case the explicit numeric form doesn't resolve.
        print("    Explicit current-month URL failed, trying shortcut URL...")
        soup = get_soup(f"{BASE}/agenda/athens")
    return parse_month_page(soup)


def parse_events() -> list[dict]:
    today = date.today()
    all_events = []
    seen_urls = set()

    year, month = today.year, today.month
    for i in range(MONTHS_AHEAD + 1):
        print(f"  Fetching {year}-{month:02d}...")
        try:
            month_events = fetch_month(year, month, is_current_month=(i == 0))
        except Exception as e:
            print(f"    FAILED: {e}")
            month_events = []

        for ev in month_events:
            if ev["url"] in seen_urls:
                continue
            seen_urls.add(ev["url"])
            all_events.append(ev)

        month += 1
        if month > 12:
            month = 1
            year += 1

    return all_events


def main():
    print("Fetching rocking.gr Athens agenda...")
    events = parse_events()
    print(f"Found {len(events)} events across {MONTHS_AHEAD + 1} months")

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
