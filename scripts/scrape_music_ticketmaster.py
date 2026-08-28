"""
Athens Tonight — music scraper (ticketmaster.gr)

WHAT THIS DOES
1. Visits ticketmaster.gr's Music category search page to get every event
   currently listed (title + link), across all of Greece.
2. Visits each event's OWN page and reads its schema.org JSON-LD block for
   venue name, address, and start/end date-time.
3. Keeps only events whose venue postal code is in the Attica block
   (10xxx-19xxx) — ticketmaster.gr lists all of Greece on one Music page,
   same filtering need as the more.com scraper, just a different signal
   (more.com's schema.org markup has an explicit addressRegion field;
   ticketmaster.gr's does not, so postal code is used instead).
4. Writes everything to data/music_ticketmaster.json.

STATUS (27/8/2026): selectors confirmed against real saved copies of both
the listing page and one event's own page (Μάνος Λοΐζος @ Καλλιμάρμαρο) —
not guesswork, same process as the other two scrapers. Two open items:

  - Only ONE event page was inspected, for a single-date event. The
    listing page also shows events whose data-start-date/data-end-date
    span WEEKS (e.g. a club's summer season, 4/7 to 2/9) — it's
    unconfirmed whether such an event's own JSON-LD exposes each
    individual night or just that outer range. This scraper currently
    just records whatever startDate/endDate the event's own page reports,
    same as the more.com scraper does for its start/end fields — if that
    turns out to be a wide range for recurring events, the site's display
    layer will need to decide how to handle it (same open question would
    apply to more.com's residency-style listings too).
  - The listing page had no visible pagination / "load more" control in
    the static HTML, and only ~30 Music events were present (the site
    also covers sports/theatre, so Music alone is a small category). If
    that number grows past a single page's worth, pagination will need
    handling — unconfirmed either way since only one snapshot was seen.
  - The <a href> in each listing item is just "_sen_{id}.html" with no
    slug (the slug in the real canonical URL, e.g.
    "manos-loizos-...-th-dikh-toy-istoria_sen_2007849.html", appears to
    get added client-side). This scraper uses the id-only URL as-is. That
    should still resolve since the id is what actually routes, but it
    wasn't independently confirmed against a live fetch — ticketmaster.gr
    blocks non-browser requests from the environment this was written in.
    Check the first run's output URLs actually load before relying on
    this long-term; if they don't, the fix is to build the slug from the
    title the same way the site's own JS does.

Run locally first with: python scripts/scrape_music_ticketmaster.py
and check data/music_ticketmaster.json looks sane before automating.
"""

import json
import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ticketmaster.gr"
LISTING_URL = f"{BASE}/search.html?category=Music"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "music_ticketmaster.json"

HEADERS = {
    # A bare User-Agent alone was NOT enough — ticketmaster.gr returned a
    # 403 to a plain requests.get() with only that header set (confirmed
    # via a real workflow run, 28/8/2026). This fuller set mimics an
    # actual Chrome navigation more closely. Whether it's enough to clear
    # whatever check is in place is UNCONFIRMED — this environment can't
    # reach ticketmaster.gr either, so it couldn't be tested here. If this
    # still 403s, the detection is likely deeper than headers (e.g. a
    # Cloudflare/Akamai JS challenge or TLS fingerprint check), which
    # `requests` fundamentally can't pass — see the note in main().
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# A shared session persists cookies between the listing request and each
# event-page request, which is closer to how a real browser session
# behaves (some bot checks set a cookie on the first hit and expect it
# back on subsequent ones).
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def get_soup(url: str) -> BeautifulSoup:
    resp = SESSION.get(url, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_current_events() -> list[dict]:
    """Return [{'title': ..., 'url': ..., 'event_id': ...}, ...] for every
    event currently listed on the Music category search page.

    Confirmed from a real saved copy (27/8/2026) of
    https://www.ticketmaster.gr/search.html?category=Music :
        <div class="grid categoryPageWrapper">
          <div class="event" id="2008151"
               data-name="Rooftop Festival Athens 2026"
               data-venue="" data-start-date="2026-09-20 17:00:00.0"
               data-end-date="2026-09-20 23:59:59.0">
            <a href="_sen_2008151.html">...</a>
          </div>
          ...
    `data-venue` is blank on many items (including some clearly outside
    Attica, e.g. Thessaloniki dates) — it isn't reliable for filtering,
    which is why we visit each event's own page instead.
    """
    soup = get_soup(LISTING_URL)
    events = []
    seen_ids = set()

    for item in soup.select("div.categoryPageWrapper div.event"):
        event_id = item.get("id", "")
        link = item.select_one("a[href]")
        title = item.get("data-name", "").strip()
        if not event_id or not link or not title:
            continue
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)

        href = link["href"]
        full_url = href if href.startswith("http") else BASE + "/" + href.lstrip("/")

        events.append({"title": title, "url": full_url, "event_id": event_id})

    return events


def is_attica_postal_code(postal_code: str) -> bool:
    """Greek postal codes are assigned in regional blocks; the Attica
    block is 10xxx-19xxx (Athens, Piraeus, and the rest of Attica all
    start with '1'). This is the only Attica signal available here since,
    unlike more.com's markup, there's no explicit addressRegion field."""
    digits = re.sub(r"\D", "", postal_code or "")
    return len(digits) == 5 and digits[0] == "1"


def parse_event_page(url: str) -> dict | None:
    """Return venue/address/date details for one event, read from its
    schema.org JSON-LD block.

    Confirmed from a real saved copy of one event's own page (Μάνος
    Λοΐζος, 27/8/2026):
        <script type="application/ld+json">
        {
          "@type": "Event",
          "name": "...",
          "startDate": "2026-09-14 21:00:00.0",
          "endDate": "2026-09-14 23:59:59.0",
          "location": {
            "@type": "Place",
            "name": "Παναθηναϊκό Στάδιο",
            "address": {
              "@type": "PostalAddress",
              "streetAddress": "...",
              "addressLocality": "Αθήνα",
              "postalCode": "11635",
              "addressCountry": "GR"
            }
          }
        }
        </script>
    """
    soup = get_soup(url)
    script = soup.find("script", attrs={"type": "application/ld+json"})
    if not script or not script.string:
        return None

    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return None

    if data.get("@type") != "Event":
        return None

    location = data.get("location", {}) or {}
    address = location.get("address", {}) or {}

    return {
        "title": data.get("name", ""),
        "venue": location.get("name", ""),
        "street": address.get("streetAddress", ""),
        "locality": address.get("addressLocality", ""),
        "postal_code": address.get("postalCode", ""),
        "start": data.get("startDate", ""),
        "end": data.get("endDate", ""),
    }


def parse_events() -> list[dict]:
    listing = get_current_events()
    print(f"Found {len(listing)} music events listed")

    events = []
    for item in listing:
        print(f"  Checking {item['title']}...")
        try:
            details = parse_event_page(item["url"])
        except Exception as e:
            print(f"    FAILED: {e}")
            details = None
        time.sleep(1.0)  # be polite — don't hammer the site

        if not details:
            continue
        if not is_attica_postal_code(details["postal_code"]):
            continue  # not an Attica venue — skip entirely

        events.append({
            "title": details["title"] or item["title"],
            "url": item["url"],
            "venue": details["venue"],
            "street": details["street"],
            "locality": details["locality"],
            "postal_code": details["postal_code"],
            "start": details["start"],
            "end": details["end"],
            "source": "ticketmaster.gr",
        })

    return events


def main():
    print("Fetching ticketmaster.gr music listings...")
    try:
        events = parse_events()
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            print(
                "\nGot a 403 Forbidden from ticketmaster.gr. This means their "
                "bot detection is blocking plain HTTP requests outright "
                "(headers alone weren't enough) — a Cloudflare/Akamai-style "
                "JS challenge or TLS fingerprint check most likely can't be "
                "passed by the `requests` library at all, from any IP. The "
                "next real option is scraping with an actual headless "
                "browser (e.g. Playwright) instead of `requests`, which is "
                "a bigger change to this script and workflow."
            )
        raise
    print(f"Found {len(events)} events with an Attica venue")

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
