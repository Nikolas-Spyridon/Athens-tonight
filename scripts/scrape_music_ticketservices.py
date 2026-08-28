"""
Athens Tonight — music scraper (ticketservices.gr)

WHAT THIS DOES
1. Visits ticketservices.gr's "LiveConcerts" listing page — ALL current and
   upcoming concerts across Greece, one page, no pagination (confirmed:
   105 events on a real saved copy, spanning into 2027, no "load more"
   control anywhere in the markup).
2. Reads each event's own data-* attributes directly off the <li> — title,
   every performance date, every venue, and the site's own internal
   area-id codes. No need to visit each event's own page (unlike
   ticketmaster.gr) — everything needed is already on the listing.
3. Filters to Attica using the site's OWN area-id taxonomy (see
   ATTICA_AREA_IDS below) — not a guessed keyword/postal-code heuristic.
4. Writes everything to data/music_ticketservices.json.

WHY THIS ONE IS DIFFERENT FROM THE TICKETMASTER.GR SCRAPER
ticketservices.gr is NOT blocking automated requests the way ticketmaster.gr
does — a plain fetch got the real page back with no 403, no challenge.
Still worth a first local run before trusting the daily workflow, since
that hasn't been verified from GitHub Actions' specific IP range yet, only
from this environment.

ATTICA FILTERING — confirmed, not guessed
The live page includes its own internal JS mapping (`var areas = {...}`),
used to build the site's own area filter dropdown:
    1  -> "ΑΘΗΝΑ"              (Athens)
    14 -> "ΥΠΟΛΟΙΠΟ ΑΤΤΙΚΗΣ"    (rest of Attica)
    53 -> "ΣΑΛΑΜΙΝΑ"           (Salamina — its own separate code in the
                                 site's own taxonomy, even though Salamina
                                 is technically part of the Attica region)
Every event's <li> carries `data-areaids="1"` (or "14", or "2,3,18" for
a multi-city tour, etc.) that references this same numbering. Filtering
on area id 1 and 14 is therefore a real structural match, not a keyword
guess like the ticketmaster.gr postal-code approach had to be. Salamina
(53) is deliberately left OUT of ATTICA_AREA_IDS — add it in if "Attica"
should include the island for this project's purposes.

Some events have NO area id at all (data-areaids="" on the raw page —
seen on ~8% of events in the sample checked). These are kept with
region_status="unconfirmed" rather than silently dropped or silently
included, so they surface for a quick human glance rather than
disappearing or polluting the Attica-confirmed set — same
spot-checking philosophy the other scrapers in this project use.

STATUS (28/8/2026): selectors, area-id mapping, and encoding all confirmed
against a real saved copy of the live listing page — not guesswork.

Run locally first with: python scripts/scrape_music_ticketservices.py
and check data/music_ticketservices.json looks sane before automating.
"""

import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ticketservices.gr"
LISTING_URL = f"{BASE}/el/LiveConcerts/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "music_ticketservices.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# See "ATTICA FILTERING" note above — confirmed from the site's own
# `var areas = {...}` JS mapping, not guessed.
ATTICA_AREA_IDS = {1, 14}


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    # Confirmed from the page's own <meta charset="windows-1253"> — without
    # this, requests' auto-detected encoding can mangle the Greek text.
    resp.encoding = "windows-1253"
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def clean_title(raw: str) -> str:
    """data-title values contain a literal '<br>' as a line separator —
    it's HTML-entity-encoded in the raw attribute (&lt;br&gt;), which
    BeautifulSoup decodes into a literal '<br>' substring in the string,
    NOT an actual tag. Collapse it into one readable line."""
    return re.sub(r"\s*<br\s*/?>\s*", " — ", raw).strip()


def parse_events() -> list[dict]:
    """Confirmed from a real saved copy (28/8/2026) of
    https://www.ticketservices.gr/el/LiveConcerts/ :
        <ul id="events_list">
          <li id="ev15080" class="event event_15080 eventevgroup_0 ..."
              data-eventid="15080"
              data-dates="2026-08-28"
              data-areaids="56"
              data-venues="MY CLUB - ΞΑΝΘΗ"
              data-title="ΡΙΑ ΕΛΛΗΝΙΔΟΥ&lt;br&gt;καλοκαίρι 2026">
            <a href="https://www.ticketservices.gr/event/xanthi-ria-ellinidou-2026/?lang=el">
              ...
            </a>
          </li>
          ...
    `data-dates` and `data-venues` are pipe-separated lists — for
    multi-city tours these do NOT line up 1:1 by index (a 5-date event
    can list only 2 distinct venues, e.g.), so this scraper reports the
    full date list and full venue list per event rather than pairing
    them, same lower-precision tradeoff the cinema scraper's "note"
    fields already accept elsewhere in this project.
    """
    soup = get_soup(LISTING_URL)
    events = []

    for li in soup.select("ul#events_list li.event"):
        event_id = li.get("data-eventid", "").strip()
        title_raw = li.get("data-title", "").strip()
        if not event_id or not title_raw:
            continue
        title = clean_title(title_raw)

        link = li.select_one("a[href]")
        url = link["href"].strip() if link and link.get("href") else ""

        dates = [d for d in li.get("data-dates", "").split("|") if d]
        venues = [v.strip() for v in li.get("data-venues", "").split("|") if v.strip()]

        areaids_raw = li.get("data-areaids", "").strip()
        if areaids_raw:
            area_ids = [int(a) for a in areaids_raw.split(",") if a.strip().isdigit()]
        else:
            area_ids = []

        if not area_ids:
            region_status = "unconfirmed"  # no area data — don't silently drop
        elif any(a in ATTICA_AREA_IDS for a in area_ids):
            region_status = "attica"
        else:
            region_status = "not_attica"

        if region_status == "not_attica":
            continue  # confirmed elsewhere in Greece — skip entirely

        events.append({
            "event_id": event_id,
            "title": title,
            "url": url,
            "dates": dates,
            "venues": venues,
            "area_ids": area_ids,
            "region_status": region_status,  # "attica" or "unconfirmed"
            "source": "ticketservices.gr",
        })

    return events


def main():
    print("Fetching ticketservices.gr live concert listings...")
    events = parse_events()
    attica_count = sum(1 for e in events if e["region_status"] == "attica")
    unconfirmed_count = len(events) - attica_count
    print(
        f"Kept {len(events)} events "
        f"({attica_count} confirmed Attica, {unconfirmed_count} unconfirmed — "
        f"no area data on the listing, worth a quick glance)"
    )

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
