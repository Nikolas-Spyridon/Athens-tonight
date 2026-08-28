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

WHY THIS USES PLAYWRIGHT, NOT requests (IMPORTANT)
A plain `requests.get()` — even with a full, realistic browser header set
(User-Agent, Accept, Accept-Language, Sec-Fetch-*, a persistent session for
cookies) — got a 403 Forbidden from ticketmaster.gr every time, confirmed
via two separate real GitHub Actions runs (28/8/2026). Headers alone not
being enough points to a check `requests` can't pass at all: most likely
the TLS handshake / network-level fingerprint (JA3 or similar), which is
determined by the underlying HTTP library, not anything set in Python.
Playwright drives an actual Chromium browser, so its network fingerprint
matches a real browser rather than a scripting library. This is the
standard fix for that class of block, but it was NOT possible to verify
against the live site from the environment this was written in either
(ticketmaster.gr is unreachable from there too) — if this still gets
blocked, the site may be doing full behavioral/JS-challenge detection,
which would need a stealth plugin (e.g. playwright-stealth) or a paid
scraping-proxy service on top of this.

WHAT HAPPENED WITH THE FIRST PLAYWRIGHT ATTEMPT (28/8/2026): no more 403,
but page.wait_for_selector() for the event list timed out — Playwright
successfully loaded *something*, but the expected content never appeared
within the wait window. This could be slow JS rendering that just needed
more time, a challenge/interstitial page served without an HTTP error, or
genuinely different markup than what was captured in the saved HTML this
scraper was built against. This version (a) waits for "networkidle"
instead of just "domcontentloaded" and gives the selector wait more time,
and (b) saves a screenshot + the raw HTML to debug_artifacts/ if the wait
times out again, so the next failure is diagnosable instead of a bare
timeout with no evidence of what was actually on the page.

STATUS (27-28/8/2026): CSS/JSON-LD selectors below are confirmed against
real saved copies of both the listing page and one event's own page
(Μάνος Λοΐζος @ Καλλιμάρμαρο) — not guesswork. The Playwright fetch
mechanism itself (this file's main change) is UNCONFIRMED against the
live site — see above. Other open items carried over from the requests
version:

  - Only ONE event page was inspected, for a single-date event. The
    listing page also shows events whose data-start-date/data-end-date
    span WEEKS (e.g. a club's summer season, 4/7 to 2/9) — it's
    unconfirmed whether such an event's own JSON-LD exposes each
    individual night or just that outer range. This scraper currently
    just records whatever startDate/endDate the event's own page reports,
    same as the more.com scraper does for its start/end fields.
  - The listing page had no visible pagination / "load more" control in
    the static HTML, and only ~30 Music events were present (the site
    also covers sports/theatre, so Music alone is a small category). If
    that number grows past a single page's worth, pagination will need
    handling — unconfirmed either way.
  - The <a href> in each listing item is just "_sen_{id}.html" with no
    slug (the slug in the real canonical URL, e.g.
    "manos-loizos-...-th-dikh-toy-istoria_sen_2007849.html", appears to
    get added client-side). This scraper uses the id-only URL as-is,
    trusting the id is what actually routes — still unconfirmed against
    a live fetch.

SETUP: this needs the `playwright` pip package PLUS its Chromium browser
binary — pip alone doesn't install the browser. Deliberately kept out of
the shared requirements.txt so the other two scrapers don't pull it in
unnecessarily; install it locally with:
    pip install playwright
    playwright install --with-deps chromium
(the workflow file does the same as its own step, not via requirements.txt)

Run locally first with: python scripts/scrape_music_ticketmaster.py
and check data/music_ticketmaster.json looks sane before automating.
"""

import json
import re
import time
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

BASE = "https://www.ticketmaster.gr"
LISTING_URL = f"{BASE}/search.html?category=Music"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "music_ticketmaster.json"

# A normal, current desktop Chrome UA — Playwright's own default headless
# UA string contains "HeadlessChrome", which is itself a giveaway to some
# bot checks, so this is overridden explicitly.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def get_soup(page: Page, url: str, wait_selector: str, debug_name: str = "debug") -> BeautifulSoup:
    """Navigate Playwright's page to `url`, wait for `wait_selector` to
    show up (confirms the real content — not just a loading/challenge
    screen — has rendered), then parse the resulting HTML.

    If the wait times out, saves a screenshot + the raw HTML to
    debug_artifacts/ before re-raising, so a failed CI run leaves
    evidence of what actually got served (challenge page, empty shell,
    genuinely different markup, etc.) instead of just a bare timeout."""
    page.goto(url, timeout=30000, wait_until="networkidle")
    try:
        page.wait_for_selector(wait_selector, timeout=20000)
    except Exception:
        debug_dir = Path(__file__).resolve().parent.parent / "debug_artifacts"
        debug_dir.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(debug_dir / f"{debug_name}.png"), full_page=True)
        except Exception as e:
            print(f"    (could not save screenshot: {e})")
        (debug_dir / f"{debug_name}.html").write_text(page.content(), encoding="utf-8")
        print(f"    Saved debug screenshot/html to {debug_dir}/{debug_name}.*")
        raise
    return BeautifulSoup(page.content(), "html.parser")


def get_current_events(page: Page) -> list[dict]:
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
    soup = get_soup(
        page, LISTING_URL,
        wait_selector="div.categoryPageWrapper div.event",
        debug_name="listing_page",
    )
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


def parse_event_page(page: Page, url: str) -> dict | None:
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
    soup = get_soup(
        page, url,
        wait_selector='script[type="application/ld+json"]',
        debug_name=f"event_{url.rsplit('/', 1)[-1]}",
    )
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


def parse_events(page: Page) -> list[dict]:
    listing = get_current_events(page)
    print(f"Found {len(listing)} music events listed")

    events = []
    for item in listing:
        print(f"  Checking {item['title']}...")
        try:
            details = parse_event_page(page, item["url"])
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

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="el-GR",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            events = parse_events(page)
        finally:
            browser.close()

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
