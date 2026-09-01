"""
Athens Tonight — theatre scraper (more.com)

STATUS (31/8/2026): REWRITTEN AND VALIDATED against a real saved copy of
https://www.more.com/gr-el/tickets/theater/ (354 <article> events,
provided by the user). This replaces the earlier draft, which had two
confirmed bugs:

BUG 1 — genre extraction always returned []. Cause: I'd guessed a
"theatre<genre>" class prefix. The real site spells it the English way,
"theater<genre>" (e.g. "theatercomedy", "theatersocialdrama"). Confirmed
by counting real class tokens across all 354 articles — see
extract_genres() below for the full confirmed genre vocabulary.

BUG 2 — multi-venue tour events (venue name "Πολλαπλοι χωροι") showed
ONE made-up date range and ONE venue, same class of bug already fixed
for music's multi-city tours. Real structure, confirmed by inspecting
the DOM directly:
  - The <article>'s `data-venue` attribute holds an ORDERED list of
    "venueGroupId<ID>d<YYYYMMDD>" tokens, one per performance date.
  - Inside that article's single <div class="playinfo__venue">, there
    is one <div itemprop="address"> block PER PERFORMANCE DATE, in the
    SAME ORDER as the data-venue tokens.
  - Zipping the two together gives the correct venue + region per date.
    Verified on "SEXY LAUNDRY" (more.com/gr-el/tickets/theater/
    sexy-laundry-2026/): its 4 tour dates resolve to Κορυδαλλός/Αττική,
    (no locality)/Θεσσαλονίκη, Γιάννενα/Ιωάννινα, Βόλος/Μαγνησία — i.e.
    only ONE of its four dates is actually in Attica, not all of them.
  - Venue NAMES for tour dates aren't in the address block at all (the
    #PlayVenue span just says "Πολλαπλοι χωροι") — they come from a
    venueGroupId → name lookup, itself confirmed present in the page's
    own "ΠΟΥ" venue filter tree (`data-filter=".venueGroupId<ID>"`
    links), same "extract from the client-side filter, no AJAX
    endpoint" pattern already used for Attica localities.
  - Checked across all 354 articles: address-block count matches
    data-venue token count for 331/354 (93.5%). The 23 mismatches are
    long-running SINGLE-venue shows where data-venue only lists a
    partial sample of dates but the address blocks (all identical,
    since it's one venue) list every date — safe to handle without
    zipping. Only 2/354 are BOTH multi-venue AND mismatched; those are
    marked unconfirmed rather than guessed.

Run locally first with: python scripts/scrape_theatre_more.py
"""

import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.more.com"
LISTING_URL = f"{BASE}/gr-el/tickets/theater/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "theatre_more.json"

TARGET_REGION = "Αττική"
MULTI_VENUE_LABEL = "Πολλαπλοι χωροι"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Confirmed (31/8/2026) against real class tokens on all 354 articles.
# Matches bare "theater<genre>" tokens only — the date-suffixed variant
# ("theatercomedyd20260903") and the bare category token ("theater")
# both naturally fail this regex since it requires letters-only to the
# end of the string.
GENRE_CLASS_RE = re.compile(r"^theater([a-z]+)$")

# Confirmed (31/8/2026) from more.com's own theatre-page filter tree —
# same Attica locality set already validated for the music scraper.
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


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def build_venue_name_map(soup: BeautifulSoup) -> dict[str, str]:
    """venueGroupId -> real venue name, from the page's own venue filter
    tree. Confirmed (31/8/2026): e.g. venueGroupId4213 -> 'Θέατρο Άλσος',
    venueGroupId3846 -> 'Αμφιθέατρο Θαν.Βέγγος'."""
    mapping = {}
    for a in soup.select('a[data-filter^=".venueGroupId"]'):
        m = re.match(r"\.venueGroupId(\d+)$", a.get("data-filter", ""))
        if m:
            mapping[m.group(1)] = a.get_text(strip=True)
    return mapping


def extract_genres(article) -> list[str]:
    genres = []
    for cls in article.get("class", []):
        m = GENRE_CLASS_RE.match(cls)
        if m:
            genres.append(m.group(1))
    return genres


def _read_address(addr_block) -> tuple[str, str, str]:
    if not addr_block:
        return "", "", ""
    loc = addr_block.select_one('meta[itemprop="addressLocality"]')
    reg = addr_block.select_one('meta[itemprop="addressRegion"]')
    street = addr_block.select_one('meta[itemprop="streetAddress"]')
    return (
        loc.get("content", "") if loc else "",
        reg.get("content", "") if reg else "",
        street.get("content", "") if street else "",
    )


def extract_screenings(article, venue_name_map: dict[str, str]) -> tuple[list[dict], bool]:
    """Returns (screenings, region_confirmed) where each screening is
    {'date': iso_or_none, 'venue', 'locality', 'region', 'street'}.
    See module docstring for how this was validated."""
    venue_block = article.select_one("div.playinfo__venue")
    if not venue_block:
        return [], False

    play_venue_el = venue_block.select_one("#PlayVenue")
    play_venue_name = play_venue_el.get_text(strip=True) if play_venue_el else ""
    addr_blocks = venue_block.select('div[itemprop="address"]')

    if play_venue_name != MULTI_VENUE_LABEL:
        # Single physical venue for the whole run. Confirmed: every
        # address block for a single-venue event carries identical
        # locality/region, so one representative block is safe to use
        # for the whole run even when data-venue only samples dates.
        locality, region, street = _read_address(addr_blocks[0] if addr_blocks else None)
        return [{
            "date": None,  # this is a residency, not per-date-specific
            "venue": play_venue_name,
            "locality": locality, "region": region, "street": street,
        }], True

    data_venue = article.get("data-venue", "")
    tokens = re.findall(r"venueGroupId(\d+)d(\d{8})", data_venue)

    if tokens and len(addr_blocks) == len(tokens):
        screenings = []
        for (group_id, date8), addr in zip(tokens, addr_blocks):
            locality, region, street = _read_address(addr)
            screenings.append({
                "date": f"{date8[0:4]}-{date8[4:6]}-{date8[6:8]}",
                "venue": venue_name_map.get(group_id, ""),
                "locality": locality, "region": region, "street": street,
            })
        return screenings, True

    # Counts didn't line up (2/354 in validation) — don't guess a
    # pairing. List what we have, flagged unconfirmed, per project rule
    # of never silently dropping under uncertainty.
    screenings = []
    for addr in addr_blocks:
        locality, region, street = _read_address(addr)
        screenings.append({
            "date": None, "venue": "",
            "locality": locality, "region": region, "street": street,
        })
    return screenings, False


def parse_events() -> list[dict]:
    soup = get_soup(LISTING_URL)
    venue_name_map = build_venue_name_map(soup)
    events = []

    for article in soup.select('article[itemtype="http://schema.org/Event"]'):
        title_el = article.select_one("h3.playinfo__title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        url_el = article.select_one('meta[itemprop="url"]')
        url_path = url_el.get("content", "") if url_el else ""
        full_url = url_path if url_path.startswith("http") else BASE + url_path

        start_el = article.select_one('meta[itemprop="startDate"]')
        end_el = article.select_one('meta[itemprop="endDate"]')
        genres = extract_genres(article)

        screenings, region_confirmed = extract_screenings(article, venue_name_map)
        attica_screenings = [s for s in screenings if s["region"] == TARGET_REGION]

        if not attica_screenings and region_confirmed:
            continue  # confirmed no Attica date for this event — skip

        if not attica_screenings and not region_confirmed:
            # Can't confirm OR rule out Attica — keep it, flagged, rather
            # than silently dropping a possibly-Attica event.
            attica_screenings = screenings

        events.append({
            "title": title,
            "url": full_url,
            "start": start_el.get("content", "") if start_el else "",
            "end": end_el.get("content", "") if end_el else "",
            "genres": genres,
            "screenings": attica_screenings,
            "region_confirmed": region_confirmed,
            "source": "more.com",
        })

    return events


def main():
    print("Fetching more.com theatre listings...")
    events = parse_events()
    unconfirmed = sum(1 for e in events if not e["region_confirmed"])
    print(f"Found {len(events)} events with at least one Attica date "
          f"({unconfirmed} unconfirmed)")

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
