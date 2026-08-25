"""
Athens Tonight — cinema scraper (athinorama.gr)

WHAT THIS DOES
1. Visits athinorama's "new releases" listing page to get the current week's
   movies (and a link to each movie's own page).
2. Visits each movie's page to pull the per-cinema, per-day showtimes.
3. Writes everything to data/cinema.json, which the website reads.

STATUS (25/8/2026): all selectors below have been confirmed against the
real page via browser inspector — this is not guesswork. The one remaining
soft spot is `.title-infos` for a cinema's area/address text, which is a
reasonable guess but wasn't individually confirmed like the others.

Run locally first with: python scripts/scrape_cinema.py
and check data/cinema.json looks sane before relying on the automated run.
"""

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.athinorama.gr"
LISTING_URL = f"{BASE}/cinema/guide"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "cinema.json"

HEADERS = {
    # A normal browser user-agent avoids some basic bot-blocking.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_current_movies() -> list[dict]:
    """Return [{'title': ..., 'url': ...}, ...] for everything currently
    playing (not just this week's new releases).

    Confirmed from the real page (via browser inspector, 25/8/2026),
    https://www.athinorama.gr/cinema/guide :
        <div class="item horizontal card-item">
          <div class="item-content">
            <div class="item-description">
              <h2 class="item-title"><a href="/cinema/movie/...">Title</a></h2>
    """
    soup = get_soup(LISTING_URL)
    movies = []
    seen_urls = set()

    for item in soup.select("div.item.horizontal.card-item"):
        link = item.select_one("h2.item-title a")
        if not link or not link.get("href"):
            continue
        href = link["href"]
        full_url = href if href.startswith("http") else BASE + href
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        title = link.get_text(strip=True)
        if title:
            movies.append({"title": title, "url": full_url})

    return movies


# Confirmed from the real page (via browser inspector, 25/8/2026):
#   Each cinema on a movie's page is:
#     <div class="item card-item" data-lat="..." data-lon="...">
#       <h2 class="item-title"><a href="/cinema/halls/...">Cinema Name</a></h2>
#       <div class="title-infos">...</div>          <- likely address/area
#       <ul class="schedule-infos">
#         <li><div class="inner"><span class="time">Σάβ.: 20.20</span></div></li>
#         ... one <li> per showtime, day+time combined as text ...
#       </ul>
#     </div>
#
# The day is a 3-letter Greek abbreviation, time uses a dot not a colon,
# e.g. "Σάβ.: 20.20" = Saturday, 20:20.

GREEK_DAY_ABBR = {
    "Δευ": 0, "Τρι": 1, "Τετ": 2, "Πεμ": 3,
    "Παρ": 4, "Σαβ": 5, "Σάβ": 5, "Κυρ": 6,
}


def week_monday(d: date) -> date:
    """Return the Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def date_for_weekday_this_week(weekday: int, today: date) -> date:
    """Map a weekday index (Mon=0..Sun=6) to a date within the SAME week as
    `today` — athinorama's schedule page shows one specific week, so a
    'Δευ' shown on a Tuesday page means that week's Monday, which may
    already be in the past, not next week's Monday."""
    return week_monday(today) + timedelta(days=weekday)


def parse_showtimes_for_movie(url: str, today: date | None = None) -> list[dict]:
    """
    Return a list of screenings for one movie:
    [{'cinema': ..., 'area': ..., 'lat': ..., 'lon': ...,
      'showtimes': {'2026-08-24': ['20:30'], ...}}]
    """
    today = today or date.today()
    soup = get_soup(url)
    screenings = []

    for block in soup.select("div.item.card-item"):
        title_el = block.select_one("h2.item-title a")
        if not title_el:
            continue
        cinema_name = title_el.get_text(strip=True)

        area_el = block.select_one(".title-infos")
        area = area_el.get_text(" ", strip=True) if area_el else ""

        lat = block.get("data-lat", "")
        lon = block.get("data-lon", "")

        showtimes_by_date: dict[str, list[str]] = {}
        for time_span in block.select("ul.schedule-infos li .time"):
            text = time_span.get_text(strip=True)  # e.g. "Σάβ.: 20.20"
            m = re.match(r"(\D+)\.?:\s*(\d{1,2})[.:](\d{2})", text)
            if not m:
                continue
            day_abbr = m.group(1).strip().rstrip(".")
            hh, mm = m.group(2), m.group(3)
            weekday = GREEK_DAY_ABBR.get(day_abbr)
            if weekday is None:
                continue
            show_date = date_for_weekday_this_week(weekday, today)
            key = show_date.isoformat()
            showtimes_by_date.setdefault(key, []).append(f"{hh.zfill(2)}:{mm}")

        screenings.append({
            "cinema": cinema_name,
            "area": area,
            "lat": lat,
            "lon": lon,
            "showtimes": showtimes_by_date,
        })

    return screenings


def main():
    print("Fetching current movie list...")
    movies = get_current_movies()
    print(f"Found {len(movies)} movies")

    results = []
    for m in movies:
        print(f"  Scraping {m['title']}...")
        try:
            screenings = parse_showtimes_for_movie(m["url"])
        except Exception as e:
            print(f"    FAILED: {e}")
            screenings = []
        results.append({
            "title": m["title"],
            "url": m["url"],
            "screenings": screenings,
        })
        time.sleep(1.5)  # be polite — don't hammer the site

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {"updated": date.today().isoformat(), "movies": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
