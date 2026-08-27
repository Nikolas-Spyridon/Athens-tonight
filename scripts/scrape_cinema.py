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

import unicodedata

GREEK_DAY_ABBR = {
    "δε": 0, "τρ": 1, "τε": 2, "πε": 3,
    "πα": 4, "σα": 5, "κυ": 6,
}


def strip_accents(s: str) -> str:
    """Remove Greek tonos/diacritics so 'Πέμ' and 'Πεμ' compare equal."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def normalize_day(raw: str):
    """Map a day abbreviation of ANY length (2-4 letters seen so far: 'Τρ',
    'Σάβ', 'Δευτ') to a weekday index. Matches on just the first 2 letters
    (accent-stripped, lowercased) since that alone distinguishes all 7
    Greek weekday names — athinorama is inconsistent about abbreviation
    length, so matching on a fixed 3-char prefix silently drops 2-letter
    forms like 'Τρ.'."""
    key2 = strip_accents(raw.strip().rstrip(".")).lower()[:2]
    return GREEK_DAY_ABBR.get(key2)


def cycle_start_date(today: date) -> date:
    """Return the Thursday that starts the CURRENT programme cycle
    containing `today` — i.e. the most recent Thursday on or before
    today (today itself, if today IS a Thursday).

    This replaces a simpler 'Monday of the calendar week' anchor, which
    was WRONG: when scraping on a Thursday (weekday()==3), the calendar
    Mon-Sun week containing today has its Mon/Tue/Wed portion already in
    the PAST (last week), but the real cycle's Mon/Tue/Wed lands NEXT
    calendar week. Anchoring to the cycle's actual start (Thursday)
    fixes this for every day of the week, not just Thursdays."""
    THURSDAY = 3  # Python's date.weekday(): Mon=0 .. Sun=6
    days_since_thursday = (today.weekday() - THURSDAY) % 7
    return today - timedelta(days=days_since_thursday)


def date_for_weekday_in_cycle(weekday: int, today: date) -> date:
    """Map a weekday index (Mon=0..Sun=6) to its actual date within the
    CURRENT programme cycle (Thu..Wed) containing `today` — not the
    calendar Mon-Sun week. Cycle day order is Thu,Fri,Sat,Sun,Mon,Tue,Wed,
    so a weekday's offset from the cycle's Thursday start is
    (weekday - 3) % 7."""
    THURSDAY = 3
    offset = (weekday - THURSDAY) % 7
    return cycle_start_date(today) + timedelta(days=offset)


def parse_daypart(text: str) -> tuple[str, str | None]:
    """Split ONE comma-separated piece into (day_spec, time_str_or_None).
    time_str is None if this piece has no time of its own — this happens
    when several day-ranges share ONE trailing time, e.g.
    'Πέμ.-Παρ., Κυρ.-Δευτ.: 21.45' means Thu,Fri,Sun,Mon are ALL 21:45,
    but only the last piece has the actual time written."""
    m = re.search(r"(\d{1,2})[.:](\d{2})", text)
    if not m:
        return text.strip().rstrip(":").strip(), None
    hh, mm = m.group(1), m.group(2)
    time_str = f"{hh.zfill(2)}:{mm}"
    day_spec = text[:m.start()].strip().rstrip(":").strip()
    return day_spec, time_str


def expand_day_range(day_spec: str, time_str: str, today: date) -> dict[str, list[str]]:
    """Turn a day_spec ('Σάβ' or 'Πέμ.-Τετ') plus a resolved time into
    {date_iso: [time_str]}, using the CURRENT programme cycle (not the
    calendar week) to resolve each weekday to an actual date."""
    if "-" in day_spec:
        start_raw, end_raw = day_spec.split("-", 1)
    else:
        start_raw = end_raw = day_spec
    start_wd = normalize_day(start_raw)
    end_wd = normalize_day(end_raw)
    if start_wd is None or end_wd is None:
        return {}

    out: dict[str, list[str]] = {}
    wd = start_wd
    for _ in range(7):  # safety cap — never more than a full week
        show_date = date_for_weekday_in_cycle(wd, today)
        out.setdefault(show_date.isoformat(), []).append(time_str)
        if wd == end_wd:
            break
        wd = (wd + 1) % 7
    return out


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
            text = time_span.get_text(strip=True)
            # A single <span class="time"> can contain MULTIPLE day-ranges
            # separated by commas, in (at least) two different shapes:
            #   'Πέμ.-Κυρ.: 22.30, Δευτ.-Τετ. 20.30'   <- each piece has its OWN time
            #   'Πέμ.-Παρ., Κυρ.-Δευτ.: 21.45'          <- ONE shared time, only on the LAST piece
            # Split on commas, then fill in any missing time by carrying
            # it backward from the next piece that has one (right-to-left),
            # since the missing-time case always precedes the one with it.
            pieces = [p.strip() for p in text.split(",") if p.strip()]
            parsed = [parse_daypart(p) for p in pieces]
            resolved: list[tuple[str, str]] = [None] * len(parsed)
            carried_time = None
            for i in range(len(parsed) - 1, -1, -1):
                day_spec, time_str = parsed[i]
                if time_str is not None:
                    carried_time = time_str
                resolved[i] = (day_spec, carried_time)

            for day_spec, time_str in resolved:
                if time_str is None:
                    continue  # no time found anywhere in this text at all
                fragment = expand_day_range(day_spec, time_str, today)
                for date_key, times in fragment.items():
                    showtimes_by_date.setdefault(date_key, []).extend(times)

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
