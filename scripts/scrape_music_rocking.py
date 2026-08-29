"""
Athens Tonight — music scraper (rocking.gr)

WHAT THIS DOES
1. Fetches rocking.gr's current-month Athens agenda page as a bootstrap
   step, then reads that page's OWN "event-months" nav bar to discover
   every month the site currently has events for — from the current
   month onward. This is NOT a hardcoded "current + N months" guess (an
   earlier version of this script did that and was wrong — see below).
2. Fetches each of those discovered month pages and parses their
   date-grouped event blocks.
3. Already scoped to Athens by the URL itself (the /athens path segment)
   — unlike ticketmaster.gr and ticketservices.gr, no Attica-region
   filtering is needed here at all.
4. Writes everything to data/music_rocking.json, deduplicated by event
   URL.

WHY NAV-DRIVEN DISCOVERY, NOT A FIXED MONTH COUNT (IMPORTANT)
A real saved copy of the site (28/8/2026) showed its own nav bar listing
months from August 2026 all the way to July 2027 — but skipping May and
June 2027 entirely. That confirms the nav isn't a fixed rolling window;
it lists exactly the months that currently have at least one announced
event, however far out that happens to reach, and that reach changes
day to day as new shows get announced or old months' events pass. A
fixed "current + 3 months" guess (what this script used to do) misses
real events the site already publishes further out, and would also
silently miss any newly-announced month once it appears in the nav —
reading the nav fresh on every run avoids both problems with no ongoing
maintenance.

STATUS (28/8/2026): event-block selectors AND the nav-bar structure are
both confirmed against a real saved copy of the site — not guesswork.
Fetching itself has NOT been confirmed to work from GitHub Actions
specifically — this environment isn't blocked outright the way
ticketmaster.gr is (same situation as ticketservices.gr), and a real
run already succeeded once producing correct output, but that's still
not a permanent guarantee against future blocking.

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
CURRENT_MONTH_URL = f"{BASE}/agenda/athens"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "music_rocking.json"
MAX_MONTHS = 24  # defensive cap only — real coverage is whatever the nav lists

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


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_month_links(soup: BeautifulSoup) -> list[tuple[int, int, str]]:
    """Read the site's own "event-months" nav bar to discover every
    month it currently publishes. Confirmed from a real saved copy
    (28/8/2026):
        <div class="event-months">
          <ul>
            <li><a href="https://www.rocking.gr/agenda/2026/8/athens">Αύγουστος</a></li>
            <li><a href="https://www.rocking.gr/agenda/athens" class="active">Σεπτέμβριος</a></li>
            <li><a href="https://www.rocking.gr/agenda/2026/10/athens">Οκτώβριος</a></li>
            ...
    The CURRENT month is linked via the no-year/month shortcut URL
    (.../agenda/athens) rather than the explicit numeric form other
    months use — resolved to today's actual (year, month) here so it
    sorts/dedupes correctly against the explicit-form links.
    Returns [(year, month, url), ...].
    """
    today = date.today()
    links = []
    for a in soup.select("div.event-months li a[href]"):
        href = a["href"].strip()
        m = re.search(r"/agenda/(\d{4})/(\d{1,2})/athens", href)
        if m:
            links.append((int(m.group(1)), int(m.group(2)), href))
        elif href.rstrip("/").endswith("/agenda/athens"):
            links.append((today.year, today.month, href))
    return links


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
          <!-- multiple event-block divs can repeat under one date-box -->
        </div>
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


def parse_events() -> list[dict]:
    today = date.today()

    print("  Fetching current month to discover available months...")
    current_soup = get_soup(CURRENT_MONTH_URL)
    month_links = get_month_links(current_soup)

    # The nav also lists past months (once the current month rolls
    # forward past them) — only keep current-month-or-later.
    month_links = [
        (y, m, href) for (y, m, href) in month_links
        if (y, m) >= (today.year, today.month)
    ]

    # De-dupe by (year, month) — the current month can appear via both
    # its shortcut URL and, in principle, an explicit one.
    seen_months = set()
    unique_month_links = []
    for y, m, href in month_links:
        if (y, m) in seen_months:
            continue
        seen_months.add((y, m))
        unique_month_links.append((y, m, href))

    unique_month_links = unique_month_links[:MAX_MONTHS]
    print(f"  Site currently publishes {len(unique_month_links)} month(s) from now onward")

    all_events = []
    seen_urls = set()

    for i, (y, m, href) in enumerate(unique_month_links):
        # The current month was already fetched above (that's how we
        # got the nav in the first place) — reuse it instead of
        # fetching it twice.
        if i == 0:
            soup = current_soup
        else:
            print(f"  Fetching {y}-{m:02d}...")
            try:
                soup = get_soup(href)
            except Exception as e:
                print(f"    FAILED: {e}")
                continue

        for ev in parse_month_page(soup):
            if ev["url"] in seen_urls:
                continue
            seen_urls.add(ev["url"])
            all_events.append(ev)

    return all_events


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
    print("Fetching rocking.gr Athens agenda...")
    events = parse_events()
    print(f"Found {len(events)} events")

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
