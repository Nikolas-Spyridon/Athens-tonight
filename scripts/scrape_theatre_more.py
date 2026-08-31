"""
Athens Tonight — theatre scraper (more.com)

WHAT THIS DOES
1. Visits more.com's full theatre listing page (all of Greece, one long
   page — same template family as the music listing, confirmed NOT
   paginated when checked 31/8/2026).
2. Parses each event's schema.org markup: title, venue(s), ISO start/end
   datetime, and genre tags.
3. Keeps only events with at least one Attica venue (addressRegion ==
   "Αττική").
4. Writes everything to data/theatre_more.json.

STATUS (31/8/2026) — READ THIS BEFORE TRUSTING THE OUTPUT:
This scraper is a mirror of scrape_music_more.py, which WAS fully
confirmed against real saved HTML. For theatre, only two things have
actually been confirmed directly:
  (a) the listing URL and general page shape — I fetched
      https://www.more.com/gr-el/tickets/theatre/ and saw the same
      "Πολλαπλοι χωροι" (multi-venue) labelling on tour events that
      caused the multi-city bug in the music scraper, which means the
      per-event page trick (bookingPanel.data) almost certainly applies
      here too.
  (b) the Attica locality list — the 27 localities under "Αττική-Α."
      on the theatre page's own filter tree are IDENTICAL to the ones
      already confirmed for music, so ATTICA_LOCALITIES below is safe
      to reuse as-is.
What is NOT yet confirmed, because I only had text-extracted page
content and not raw HTML/DevTools access:
  - The exact genre class-name prefix on <article> (I've assumed
    "theatre<genre>", e.g. "theatrecomedy", by analogy with music's
    "music<genre>" — this is a GUESS, not a confirmed selector).
  - Whether the schema.org Event/playinfo/address markup is byte-for-
    byte identical between /tickets/music/ and /tickets/theatre/.
Before relying on this in the scheduled workflow: run it locally, print
a few raw `article.get("class")` lists, and eyeball data/theatre_more.json.
If genre extraction comes back empty, that confirms the class-prefix
guess is wrong and needs fixing against real markup — don't silently
ship empty genres.

Run locally first with: python scripts/scrape_theatre_more.py
"""

import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.more.com"
LISTING_URL = f"{BASE}/gr-el/tickets/theatre/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "theatre_more.json"

TARGET_REGION = "Αττική"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# UNCONFIRMED GUESS — see STATUS note above. Verify against real HTML.
GENRE_CLASS_RE = re.compile(r"^theatre([a-z]+)$")

# Confirmed directly (31/8/2026) from more.com's own theatre-page filter
# tree — same 27 Attica localities already validated for the music
# scraper, reused here as-is since it's a site-wide geographic filter,
# not a category-specific one.
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
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def extract_genres(article) -> list[str]:
    """UNCONFIRMED — mirrors music's extract_genres() but with an
    assumed 'theatre<genre>' class prefix. If this always returns []
    once run for real, the prefix guess is wrong; inspect a real
    <article class="..."> in DevTools and fix GENRE_CLASS_RE."""
    genres = []
    for cls in article.get("class", []):
        m = GENRE_CLASS_RE.match(cls)
        if m:
            genres.append(m.group(1))
    return genres


def meta_content(scope, itemprop: str) -> str:
    el = scope.select_one(f'meta[itemprop="{itemprop}"]')
    return el.get("content", "") if el else ""


def extract_venues(article) -> list[dict]:
    """Same multi-venue collection logic as music: a touring show repeats
    the venue block once per city, so we must collect ALL of them, not
    just the first, then filter to Attica ones."""
    venues = []
    for venue_block in article.select("div.playinfo__venue"):
        name_el = venue_block.select_one("#PlayVenue")
        name = name_el.get_text(strip=True) if name_el else ""

        address_block = venue_block.select_one('div[itemprop="address"]')
        if address_block:
            locality = meta_content(address_block, "addressLocality")
            region = meta_content(address_block, "addressRegion")
            street = meta_content(address_block, "streetAddress")
        else:
            locality = region = street = ""

        venues.append({
            "name": name,
            "locality": locality,
            "region": region,
            "street": street,
        })
    return venues


def parse_events() -> list[dict]:
    soup = get_soup(LISTING_URL)
    events = []

    for article in soup.select('article[itemtype="http://schema.org/Event"]'):
        title_el = article.select_one("h3.playinfo__title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        url_path = meta_content(article, "url")
        full_url = url_path if url_path.startswith("http") else BASE + url_path

        start = meta_content(article, "startDate")
        end = meta_content(article, "endDate")
        genres = extract_genres(article)

        all_venues = extract_venues(article)
        attica_venues = [v for v in all_venues if v["region"] == TARGET_REGION]

        # Fallback: if region metadata is missing/empty but the locality
        # text matches a known Attica locality, flag as unconfirmed
        # rather than silently dropping — per project rule, never guess
        # a region classification into "not Attica" by omission.
        if not attica_venues:
            fallback = [
                v for v in all_venues
                if not v["region"] and v["locality"] in ATTICA_LOCALITIES
            ]
            if fallback:
                for v in fallback:
                    v["region_confirmed"] = False
                attica_venues = fallback

        if not attica_venues:
            continue  # genuinely no Attica date for this event — skip

        events.append({
            "title": title,
            "url": full_url,
            "start": start,
            "end": end,
            "genres": genres,
            "venues": attica_venues,
            "source": "more.com",
        })

    return events


def main():
    print("Fetching more.com theatre listings...")
    events = parse_events()
    print(f"Found {len(events)} events with at least one Attica date")

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
