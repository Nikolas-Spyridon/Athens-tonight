"""
Athens Tonight — cinema scraper (athinorama.gr)

WHAT THIS DOES
1. Visits athinorama's "new releases" listing page to get the current week's
   movies (and a link to each movie's own page).
2. Visits each movie's page to pull the per-cinema, per-day showtimes, AND
   (from the same page fetch — no extra request) the movie's own info:
   original (non-Greek) title, full plot description, athinorama's own
   1-5 rating, and a direct IMDb link when athinorama has one.
3. Flags each screening as open-air ("θερινός") or not, based on the sun
   icon athinorama shows next to open-air cinemas.
4. When an IMDb link is found, looks up the current IMDb user rating via
   OMDb API (omdbapi.com) — a separate, free third-party service that
   licenses this data. We deliberately do NOT scrape imdb.com directly:
   IMDb's own Terms of Use prohibit data-mining/scraping their site, and
   doing so from a daily automated workflow would be exactly that.
   Requires an OMDB_API_KEY environment variable (free key from
   https://www.omdbapi.com/apikey.aspx); if it's not set, imdb_rating is
   just left as None rather than the run failing.
5. When athinorama has NO direct IMDb link for a movie at all (common —
   it links maybe half the time even for well-known films), falls back
   to an OMDb TITLE search using the movie's original (non-Greek) title
   plus its year, both pulled from the same page. Title+year together
   is a strong enough combination to trust directly; the one guardrail
   kept is discarding a result if OMDb's own year is off by more than 1,
   since that means it's very likely a different film with a similar
   title rather than the one that's actually playing.
6. Writes everything to data/cinema.json, which the website reads.

STATUS (2/9/2026): all selectors — including the open-air marker,
original title, description, rating, and IMDb link — were confirmed
against a real saved movie page (Αμελί / Amélie) via its saved HTML, not
guessed. The one remaining soft spot is `.title-infos` for a cinema's
area/address text, which is a reasonable guess but wasn't individually
confirmed like the others.

Note: not every movie has an IMDb link, athinorama rating, or OMDb
rating (older/obscure titles sometimes don't have any of these) — those
fields fall back to "" / None rather than guessing, matching the project
rule to flag uncertainty rather than silently invent a value.

Run locally first with: python scripts/scrape_cinema.py
and check data/cinema.json looks sane before relying on the automated run.
"""

import json
import os
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

# OMDb (omdbapi.com) rating lookup — see module docstring for why we use
# this instead of scraping imdb.com directly. If the key isn't set, the
# scraper still runs fine; imdb_rating just comes back as None everywhere.
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")
IMDB_ID_RE = re.compile(r"(tt\d+)")


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


# Confirmed against a real saved page (Αμελί / Amélie, 1/9/2026) — this
# header block appears once per movie page, above the per-cinema listing:
#   <div class="review-header">
#     <div class="review-title">
#       <h1>Αμελί</h1>
#       <ul class="review-details">
#         <li><span class="original-title">Le Fabuleux Destin d'Amelie Poulain</span></li>
#         ...
#         <li><div class="rating-stars ..."><span class="rating-value">3</span></div></li>
#       </ul>
#       <div class="summary"><p>full plot synopsis...</p></div>
#     </div>
#     <div class="review-links">
#       <div class="external-links"><ul>
#         <li><a class="imdb" href="http://www.imdb.com/title/tt0211915/">...</a></li>
#       </ul></div>
#     </div>
#   </div>
# Not every movie will have all of these (older/obscure titles may lack
# an IMDb link or a rating) — each field falls back to "" / None rather
# than guessing, consistent with never silently fabricating a value.
def search_imdb_by_title(title: str, year: int | None) -> dict | None:
    """Fallback for when athinorama has no direct IMDb link at all: search
    OMDb by title instead of by ID. Only used as a fallback, and only
    trusted when OMDb's own reported year is within 1 year of athinorama's
    — titles alone are often ambiguous (remakes, common words), and a
    year mismatch is treated as 'not a confident match' rather than
    silently attaching a plausible-looking but wrong film's rating.
    Returns {'imdb_url':..., 'imdb_rating':...} or None."""
    if not title or not OMDB_API_KEY:
        return None
    try:
        params = {"t": title, "apikey": OMDB_API_KEY}
        if year:
            params["y"] = year
        resp = requests.get("https://www.omdbapi.com/", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("Response") != "True":
            return None
        imdb_id = data.get("imdbID")
        if not imdb_id:
            return None
        if year:
            omdb_year_digits = re.sub(r"\D", "", data.get("Year", ""))
            if omdb_year_digits and abs(int(omdb_year_digits[:4]) - year) > 1:
                return None  # likely a different film with a similar title — don't guess
        rating_str = data.get("imdbRating")
        rating = None
        if rating_str and rating_str != "N/A":
            try:
                rating = float(rating_str)
            except ValueError:
                rating = None
        return {"imdb_url": f"https://www.imdb.com/title/{imdb_id}/", "imdb_rating": rating}
    except requests.RequestException:
        return None


def fetch_imdb_rating(imdb_url: str) -> float | None:
    """Look up the current IMDb user rating for a movie via OMDb API,
    using the IMDb ID already present in athinorama's own imdb_url — NOT
    by visiting imdb.com itself (see module docstring). Returns None if:
    there's no imdb_url to begin with, OMDB_API_KEY isn't configured,
    OMDb doesn't have this title, or the request fails for any reason.
    Never guesses a number — a missing rating is left missing."""
    if not imdb_url or not OMDB_API_KEY:
        return None
    match = IMDB_ID_RE.search(imdb_url)
    if not match:
        return None
    imdb_id = match.group(1)
    try:
        resp = requests.get(
            "https://www.omdbapi.com/",
            params={"i": imdb_id, "apikey": OMDB_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        rating_str = data.get("imdbRating")
        if not rating_str or rating_str == "N/A":
            return None
        return float(rating_str)
    except (requests.RequestException, ValueError):
        return None


def parse_movie_info(soup: BeautifulSoup) -> dict:
    header = soup.select_one("div.review-header")
    if not header:
        return {"original_title": "", "description": "", "athinorama_rating": None, "imdb_url": "", "year": None}

    orig_el = header.select_one(".original-title")
    original_title = orig_el.get_text(strip=True) if orig_el else ""

    desc_el = header.select_one("div.summary p")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""

    rating_el = header.select_one(".rating-stars .rating-value")
    rating = None
    if rating_el:
        try:
            rating = float(rating_el.get_text(strip=True))
        except ValueError:
            rating = None

    imdb_el = header.select_one("a.imdb[href]")
    imdb_url = imdb_el["href"].strip() if imdb_el else ""

    # Confirmed on the same real saved page (Αμελί): <li><span class="year">2001</span></li>,
    # sitting right next to .original-title in .review-details. Used below
    # to sanity-check OMDb title-search matches when athinorama has no
    # direct IMDb link to look up by ID instead.
    year_el = header.select_one(".year")
    year = None
    if year_el:
        digits = re.sub(r"\D", "", year_el.get_text(strip=True))
        if digits:
            year = int(digits[:4])

    return {
        "original_title": original_title,
        "description": description,
        "athinorama_rating": rating,
        "imdb_url": imdb_url,
        "year": year,
    }


def parse_showtimes_for_movie(url: str, today: date | None = None) -> list[dict]:
    """
    Return a list of screenings for one movie:
    [{'cinema': ..., 'area': ..., 'lat': ..., 'lon': ..., 'is_open_air': ...,
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

        # Confirmed against the real page: an open-air ("θερινός") cinema
        # carries a small sun icon (summerRoom.png) inside its .tags list,
        # right next to the phone number — matching on the distinctive
        # icon filename rather than the Greek text itself, so this still
        # works if the wording around it ever changes.
        is_open_air = block.select_one('.tags img[src*="summerRoom"]') is not None

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
            "is_open_air": is_open_air,
            "showtimes": showtimes_by_date,
        })

    movie_info = parse_movie_info(soup)
    return movie_info, screenings


def main():
    print("Fetching current movie list...")
    movies = get_current_movies()
    print(f"Found {len(movies)} movies")

    if not OMDB_API_KEY:
        print("  NOTE: OMDB_API_KEY not set — imdb_rating will be null for every movie.")

    results = []
    for m in movies:
        print(f"  Scraping {m['title']}...")
        try:
            movie_info, screenings = parse_showtimes_for_movie(m["url"])
        except Exception as e:
            print(f"    FAILED: {e}")
            movie_info, screenings = {"original_title": "", "description": "", "athinorama_rating": None, "imdb_url": "", "year": None}, []

        imdb_url = movie_info["imdb_url"]
        imdb_rating = fetch_imdb_rating(imdb_url)

        # athinorama itself has no IMDb link for this one — try resolving
        # it via a title search instead (original title preferred over
        # the Greek title, since that's what's actually on IMDb), rather
        # than leaving every un-linked movie without a rating.
        if not imdb_url:
            fallback = search_imdb_by_title(
                movie_info["original_title"] or m["title"],
                movie_info["year"],
            )
            if fallback:
                imdb_url = fallback["imdb_url"]
                imdb_rating = fallback["imdb_rating"]

        results.append({
            "title": m["title"],
            "url": m["url"],
            "original_title": movie_info["original_title"],
            "description": movie_info["description"],
            "athinorama_rating": movie_info["athinorama_rating"],
            "imdb_url": imdb_url,
            "imdb_rating": imdb_rating,
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
