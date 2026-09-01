"""
Athens Tonight — theatre scraper (ticketservices.gr)

STATUS (31/8/2026): REWRITTEN from real saved HTML of
https://www.ticketservices.gr/el/theatre/ (provided by the user). My
first draft's CARD_SELECTOR and area-id assumptions were pure guesses
and wrong; this version is built entirely from what's actually there.

CONFIRMED STRUCTURE:
  - Events live in <ul id="events_list"><li class="event" ...>. Each
    <li> carries everything we need directly as data-* attributes —
    no need to parse nested markup for the basics:
      data-eventid, data-title (HTML-entity-encoded, "<br>"-joined for
      multi-line titles), data-dates ('YYYY-MM-DD|YYYY-MM-DD|...'),
      data-venueids (comma-separated), data-venues (pipe-separated,
      same order as data-venueids), data-areaids (comma-separated,
      often EMPTY for multi-venue "ΠΟΛΛΑΠΛΟΙ ΧΩΡΟΙ" events).
    The event URL is the single <a href="/event/<slug>/?lang=el"> the
    <li> wraps.
  - The page embeds two JS object literals with the authoritative
    area and venue data (same "read the page's own client-side data,
    no separate AJAX endpoint" pattern already used elsewhere in this
    project):
      areas = {1:{"name":"ΑΘΗΝΑ",...}, 7:{"name":"ΠΕΙΡΑΙΑΣ",...}, ...}
      venues = {4:{"name":"...ΛΥΚΑΒΗΤΤΟΥ","areaid":"1",...}, ...}
    Every venueid in data-venueids resolves to an areaid via `venues`,
    and every areaid resolves to a place name via `areas`.
  - IMPORTANT: these area ids are NOT the same numbering as the music
    scraper's confirmed "1 = Athens, 14 = rest of Attica" scheme — that
    was specific to the music category page. Here, Attica suburbs each
    get their OWN area id (e.g. area 92 = "ΧΑΙΔΑΡΙ"), alongside non-
    Attica cities (2 = Thessaloniki, 3 = Patra, 16 = Heraklion, 19 =
    Volos, 30 = Kavala, 71 = Komotini). So instead of hardcoding ids,
    this script resolves each area id to its NAME and checks that name
    (accent/case-normalized) against the same confirmed Attica-locality
    set used for more.com — names are stable across sources, numeric
    ids are not.
  - LIMITATION, stated plainly: for multi-venue events ("ΠΟΛΛΑΠΛΟΙ
    ΧΩΡΟΙ"), data-dates and data-venueids are two SEPARATE lists with
    no positional correspondence between them (e.g. one real event had
    13 dates but only 5 venues) — unlike more.com, there is no per-date
    venue pairing available on this listing page. This script can only
    tell you "this event has at least one Attica venue among N venues"
    for those cases, not which specific date is at which specific
    venue. That's flagged per-event as `dates_venues_paired: False`.
  - No showtime (clock time) appears anywhere on this listing page —
    only date(s). If you need showtimes, that requires visiting each
    event's own page, which is out of scope for this first pass.

Run locally first with: python scripts/scrape_theatre_ticketservices.py
"""

import html as html_module
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.ticketservices.gr"
LISTING_URL = f"{BASE}/el/theatre/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "theatre_ticketservices.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Same confirmed Attica locality set used for more.com (see
# scrape_theatre_more.py). Compared accent/case-insensitively below
# since ticketservices.gr's area names are all-caps, unaccented-ish
# Greek ("ΧΑΙΔΑΡΙ") while more.com's are properly cased ("Χαϊδάρι").
ATTICA_LOCALITIES = {
    "Αγ. Δημήτριος", "Αθήνα", "Αιγάλεω", "Αμπελόκηποι", "Αχαρνές", "Βάρη",
    "Βοτανικός", "Βριλήσσια", "Βύρωνας", "Γαλάτσι", "Γκάζι", "Δραπετσώνα",
    "Ελευσίνα", "Εξάρχεια", "Ηλιούπολη", "Θησείο", "Ίλιον", "Ιλίσια",
    "Καλλιθέα", "Καλύβια Θορικού", "Κεραμεικός", "Κολωνάκι", "Κολωνός",
    "Κορυδαλλός", "Κορωπί", "Κουκάκι", "Κυψέλη", "Λαύριο", "Λυκαβηττός",
    "Μαρούσι", "Μεταξουργείο", "Ν. Ηράκλειο", "Ν. Κόσμος", "Νέα Μάκρη",
    "Νέα Σμύρνη", "Νίκαια", "Παγκράτι", "Παλαιό Φάληρο", "Παπάγου",
    "Πειραιάς", "Περιστέρι", "Πετρούπολη", "Πόρτο Ράφτη", "Ραφήνα",
    "Ρούφ", "Σύνταγμα", "Ταύρος", "Φάληρο", "Χαϊδάρι", "Χαλάνδρι",
    "Ψυρρή", "Ωρωπός",
}


def _normalize_gr(s: str) -> str:
    """Strip accents + casefold, so 'ΧΑΙΔΑΡΙ' and 'Χαϊδάρι' compare
    equal. Same technique as cinema.py's strip_accents()."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return stripped.casefold()


_ATTICA_NORMALIZED = {_normalize_gr(name) for name in ATTICA_LOCALITIES}


def get_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.encoding = "cp1253"  # confirmed windows-1253, same as music scraper
    return resp.text


def build_area_and_venue_maps(raw_html: str) -> tuple[dict[str, str], dict[str, str]]:
    """Extract the page's own embedded `areas = {...}` and
    `venues = {...}` JS object literals. Confirmed (31/8/2026) present
    in the raw HTML as plain JS, not loaded via a separate request.
    Returns (area_id -> area_name, venue_id -> area_id)."""
    areas_match = re.search(r"areas\s*=\s*(\{.*?\});", raw_html)
    venues_match = re.search(r"venues\s*=\s*(\{.*?\});", raw_html)

    area_names: dict[str, str] = {}
    if areas_match:
        for area_id, name in re.findall(
            r'(\d+):\{"id":"\d+","name":"([^"]+)"', areas_match.group(1)
        ):
            area_names[area_id] = name

    venue_areaids: dict[str, str] = {}
    if venues_match:
        for venue_id, _name, area_id in re.findall(
            r'(\d+):\{"id":"\d+","name":"([^"]+)","areaid":"(\d+)"',
            venues_match.group(1),
        ):
            venue_areaids[venue_id] = area_id

    return area_names, venue_areaids


def _is_attica_area(area_id: str, area_names: dict[str, str]) -> bool | None:
    """True/False if we can resolve the area id to a name and check it
    against the confirmed Attica list; None if the id itself is
    unknown (never seen in the areas map) — caller should treat that
    as unconfirmed, not as "not Attica"."""
    name = area_names.get(area_id)
    if name is None:
        return None
    return _normalize_gr(name) in _ATTICA_NORMALIZED


def parse_events(raw_html: str) -> list[dict]:
    soup = BeautifulSoup(raw_html, "html.parser")
    area_names, venue_areaids = build_area_and_venue_maps(raw_html)

    events = []
    for li in soup.select("ul#events_list li.event"):
        link = li.find("a", href=True)
        if not link:
            continue
        full_url = link["href"] if link["href"].startswith("http") else BASE + link["href"]

        raw_title = li.get("data-title", "")
        title = html_module.unescape(raw_title).replace("<br>", " ").replace("<br/>", " ").strip()
        if not title:
            continue

        dates = [d for d in li.get("data-dates", "").split("|") if d]
        venue_ids = [v for v in li.get("data-venueids", "").split(",") if v]
        venue_names_raw = li.get("data-venues", "")
        venue_names = [html_module.unescape(v) for v in venue_names_raw.split("|") if v]

        if not venue_ids:
            # No resolvable venue ids at all (rare) — keep the event,
            # flagged, rather than dropping something possibly Attica.
            events.append({
                "title": title, "url": full_url, "dates": dates,
                "venues": [{"name": n, "area": None, "is_attica": None} for n in venue_names],
                "dates_venues_paired": False,
                "any_attica_venue": None,
                "source": "ticketservices.gr",
            })
            continue

        venues_out = []
        any_attica = False
        any_unconfirmed = False
        for i, vid in enumerate(venue_ids):
            area_id = venue_areaids.get(vid)
            is_attica = _is_attica_area(area_id, area_names) if area_id is not None else None
            if is_attica is None:
                any_unconfirmed = True
            elif is_attica:
                any_attica = True
            venues_out.append({
                "name": venue_names[i] if i < len(venue_names) else "",
                "area": area_names.get(area_id, "") if area_id else "",
                "is_attica": is_attica,
            })

        if not any_attica and not any_unconfirmed:
            continue  # every venue confirmed non-Attica — safe to skip

        events.append({
            "title": title,
            "url": full_url,
            "dates": dates,
            "venues": venues_out,
            # False whenever there's more than one venue: this listing
            # page gives no positional link between data-dates and
            # data-venueids for multi-venue events (see module docstring).
            "dates_venues_paired": len(venue_ids) <= 1,
            "any_attica_venue": any_attica or None if any_unconfirmed else any_attica,
            "source": "ticketservices.gr",
        })

    return events


def main():
    print("Fetching ticketservices.gr theatre listings...")
    raw_html = get_html(LISTING_URL)
    events = parse_events(raw_html)
    unpaired = sum(1 for e in events if not e["dates_venues_paired"])
    print(f"Found {len(events)} events with a possible Attica venue "
          f"({unpaired} multi-venue with unpaired dates)")

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
