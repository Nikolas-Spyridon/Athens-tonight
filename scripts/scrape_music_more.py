"""
Athens Tonight — music scraper (more.com)

WHAT THIS DOES
1. Visits more.com's full music/concert listing page (all of Greece, one
   long page — confirmed NOT paginated or lazy-loaded, 26/8/2026) to
   discover every event's title, URL, and genre tags.
2. Visits EACH event's own page and reads its embedded `bookingPanel.data`
   JavaScript object — the authoritative source of every actual
   performance date and which venue it's at.
3. Classifies each (date, venue) pair as Attica or not, using a known-
   localities list (see ATTICA_LOCALITIES below) since more.com's
   per-venue data here has no explicit region flag.
4. Writes everything to data/music_more.json.

WHY THIS VISITS EVERY EVENT'S OWN PAGE NOW (IMPORTANT — this replaced an
earlier, buggy version of this scraper)
The ORIGINAL version of this script read venue + date entirely from the
listing page's schema.org microdata: one shared `<meta itemprop=
"startDate">` per `<article>`, with multiple `playinfo__venue` blocks
repeated inside for multi-city tours. That's WRONG for any real
multi-city tour — confirmed via two real saved event pages (28/8/2026):
a single-city event (Satyricon @ Floyd, one date) and a genuine 5-city
tour (City Of The Sun: Θεσσαλονίκη 18/9, ΑΘΗΝΑ/Gazarte 19/9, Ιωάννινα
20/9, Καρδίτσα 21/9, Σέρρες 22/9 — five DIFFERENT dates). The old
approach would apply just one shared date across every city, silently
mis-dating (or entirely dropping, depending on how the Attica match
happened to land) the Attica leg of any multi-city tour.

Every more.com event page embeds a `bookingPanel.data = {...};` JS
object with the real per-date, per-venue breakdown — confirmed identical
in shape for both the single-city and multi-city real pages:
    bookingPanel.data = {
      "events": [
        {"day": "2026-09-19", "event-date": "2026-09-19T20:00:00",
         "event-end-date": "2026-09-19T23:00:00", "venueId": 4832, ...},
        ...
      ],
      "venues": [
        {"id": 4832, "venue-name": "Gazarte - Ground Stage",
         "venue-city": "Γκάζι", "venue-address": "Βουτάδων 32-34"},
        ...
      ],
      "plays": [{"id": "...", "play-title": "..."}],
      ...
    };
This is now the sole source of venue/date truth. The listing page is
still used, but ONLY to discover which events exist and their genre tags
(genre lives in the listing article's own CSS class list and is NOT
present in bookingPanel.data at all).

ATTICA FILTERING — a real limitation, not a guess dressed up as one
bookingPanel.data's venue entries have a `venue-city` string ("Αθήνα",
"Γκάζι", "Θεσσαλονίκη"...) but NO explicit region field the way the old
listing-page microdata's `addressRegion="Αττική"` did. ATTICA_LOCALITIES
below is a best-effort, non-exhaustive list of Athens + Attica
neighbourhoods/suburbs. Any venue-city that doesn't match is marked
region_status="unconfirmed" rather than silently dropped or silently
trusted — same philosophy as the ticketservices.gr scraper's area-id
handling. If a real Attica show keeps showing up "unconfirmed", the fix
is adding its locality name to this list.

STATUS (28/8/2026): bookingPanel.data structure confirmed against two
real saved event pages — not guesswork. This DOES mean fetching ~1 extra
page per event (order of 90+ requests total, based on real event counts
seen so far) instead of just the one listing page — expect a few extra
minutes of runtime, with a polite delay between requests, same as the
cinema scraper's approach to the same tradeoff.

Run locally first with: python scripts/scrape_music_more.py
and check data/music_more.json looks sane before relying on automation.
"""

import json
import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.more.com"
LISTING_URL = f"{BASE}/gr-el/tickets/music/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "music_more.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

GENRE_CLASS_RE = re.compile(r"^music([a-z]+)$")

# CONFIRMED, not guessed — transcribed directly from more.com's own "Πού"
# (Where) filter tree on the live listing page (28/8/2026 fetch). This
# filter turns out to be entirely client-side (every event for all of
# Greece is server-rendered on one page; "Πού" just toggles visibility
# via a CSS class already in the markup) — there's no AJAX endpoint to
# call. So instead of guessing region membership from free-text
# addresses, these two sets are the SITE'S OWN authoritative locality
# groupings. This also resolves the earlier Ηράκλειο ambiguity cleanly:
# more.com's own data confirms "Ηράκλειο" is its own separate region
# (Crete's Heraklion), never Αττική — it's now correctly in
# NON_ATTICA_HINTS instead of being left out of both sets.
ATTICA_LOCALITIES = {
    "αγ. ιωαννης ρεντης", "αθηνα", "αλιμος", "βαρη", "βοτανικος",
    "βριλησσια", "γαλατσι", "γκαζι", "ελευσινα", "ζωγραφου", "ιερα οδος",
    "κορυδαλλος", "κορωπι", "κυψελη", "λυκαβηττος", "μαρουσι",
    "μοναστηρακι", "νεα σμυρνη", "νικαια", "ομονοια", "παπαγου",
    "πειραιας", "περιστερι", "πετρουπολη", "ρουφ", "ταυρος",
    "φιλοπαππου", "χαλανδρι",
    # Latin-script transliterations of the same places — added after a
    # confirmed real case (30/8/2026 more.com data): venue-address for a
    # genuinely-Attica event read "Unnamed Road, Zografou 115 27", all in
    # Latin script. strip_accents() only removes diacritics, it does NOT
    # transliterate between alphabets, so a Latin "Zografou" was never
    # going to match the Greek-only "ζωγραφου" and fell through to
    # unconfirmed. more.com's own address data is inconsistently Greek
    # vs. Latin script depending on the event, so both need covering.
    "athens", "athina", "alimos", "vari", "votanikos", "vrilissia",
    "galatsi", "gazi", "elefsina", "eleusis", "iera odos", "korydallos",
    "koropi", "kypseli", "lykavittos", "lycabettus", "marousi",
    "maroussi", "monastiraki", "nea smyrni", "nikaia", "nikea",
    "omonoia", "omonia", "papagou", "piraeus", "pireas", "peristeri",
    "petroupoli", "petroupolis", "rouf", "tavros", "filopappou",
    "philopappou", "chalandri", "halandri", "zografou", "zographou",
}

NON_ATTICA_HINTS = {
    "αμπελωνας", "ανδρος", "αργος", "βεροια", "βολος", "γιαννενα",
    "διον", "διστομο", "ηρακλειο", "θεσσαλονικη", "ιωαννινα", "καβαλα",
    "καλαμαρια", "καλαματα", "καρδιτσα", "κατω τουμπα", "κιλκις",
    "κοζανη", "κομοτηνη", "κορινθος", "λαγυνα", "λαρισα", "μουδανια",
    "μυκονος", "ξανθη", "πατρα", "πτολεμαιδα", "πυλαια", "ρεθυμνο",
    "ροδος", "σερρες", "σκιαθος", "σταυρουπολη", "συρος", "τρικαλα",
    "υπατη λαμιας", "χαλκιδα", "χανια", "χιλιομοδι", "χιος",
    # "χορτ" (stem, not full word) deliberately catches BOTH "Χόρτος"
    # (nominative) and "Χόρτου" (genitive, as it actually appeared in a
    # real venue name: "Υπαίθριο Θέατρο Χόρτου") — Greek case endings
    # change the tail of the word, so matching on the stem is needed to
    # catch grammatical variants, the same way "θερινος"-style matching
    # already works elsewhere in this project via prefix/stem matching.
    "χορτ",
    # Latin-script transliterations — see the comment on ATTICA_LOCALITIES
    # above for why these are needed. "patra"/"patras" is the confirmed
    # real case (30/8/2026): venue-address "Kontaxi, Patra 264 42" was
    # falling through to unconfirmed and being shown as if it might be
    # an Attica event, when it's actually a Patras venue.
    "ampelonas", "andros", "argos", "veroia", "veria", "volos",
    "ioannina", "giannena", "dion", "distomo", "irakleio", "iraklio",
    "heraklion", "thessaloniki", "kavala", "kalamaria", "kalamata",
    "karditsa", "kato toumpa", "kilkis", "kozani", "komotini",
    "korinthos", "corinth", "lagyna", "larisa", "moudania", "mykonos",
    "xanthi", "patra", "patras", "ptolemaida", "pylaia", "rethymno",
    "rodos", "rhodes", "serres", "skiathos", "stavroupoli", "syros",
    "trikala", "ypati lamias", "chalkida", "chania", "hiliomodi",
    "chios", "chortos", "chorto",
}


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def classify_locality(city_text: str) -> str:
    """Returns "attica", "not_attica", or "unconfirmed" against the
    authoritative ATTICA_LOCALITIES/NON_ATTICA_HINTS sets above. Uses
    SUBSTRING containment, not exact match, since real locality values
    are often multi-word or abbreviated (e.g. "Αγ. Ιωάννης Ρέντης",
    "Κατράκειο Θέατρο Νίκαιας")."""
    if not city_text or city_text.strip() == "-":
        return "unconfirmed"
    normalized = strip_accents(city_text).strip().lower()
    if any(k in normalized for k in ATTICA_LOCALITIES):
        return "attica"
    if any(k in normalized for k in NON_ATTICA_HINTS):
        return "not_attica"
    return "unconfirmed"


def is_blank_locality(city_text: str) -> bool:
    return not city_text or city_text.strip() == "-"


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def extract_genres(article) -> list[str]:
    """Genre tags are bare 'music<genre>' classes on the <article>, e.g.
    'musicmetal'. The literal 'music' class (top-level category, not a
    genre) is excluded, as is the date-suffixed variant like
    'musicmetald20260918' (GENRE_CLASS_RE requires letters only, no
    digits, so the suffixed one won't match)."""
    genres = []
    for cls in article.get("class", []):
        m = GENRE_CLASS_RE.match(cls)
        if m:
            genres.append(m.group(1))
    return genres


def meta_content(scope, itemprop: str) -> str:
    el = scope.select_one(f'meta[itemprop="{itemprop}"]')
    return el.get("content", "") if el else ""


def get_current_events() -> list[dict]:
    """Discovery pass only — title, URL, genres. Confirmed from the real
    listing page (26-28/8/2026):
        <article itemtype="http://schema.org/Event" class="... musicmetal ...">
          <meta itemprop="url" content="/gr-el/tickets/music/satyricon/">
          <h3 class="playinfo__title" itemprop="name">Satyricon</h3>
          ...
    Venue/date extraction happens later, per-event, via bookingPanel.data —
    NOT here (see the module docstring for why)."""
    soup = get_soup(LISTING_URL)
    events = []

    for article in soup.select('article[itemtype="http://schema.org/Event"]'):
        title_el = article.select_one("h3.playinfo__title")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        url_path = meta_content(article, "url")
        full_url = url_path if url_path.startswith("http") else BASE + url_path

        events.append({
            "title": title,
            "url": full_url,
            "genres": extract_genres(article),
        })

    return events


def extract_booking_data(html: str) -> dict | None:
    """Pull the `bookingPanel.data = {...};` JS object out of an event's
    own page. Brace-counts to find the real end of the object rather than
    searching for the next "};", since the object contains nested
    objects/arrays with their own closing braces. See the module
    docstring for the confirmed shape."""
    marker = "bookingPanel.data = "
    start = html.find(marker)
    if start == -1:
        return None
    start += len(marker)

    depth = 0
    end = None
    for i, ch in enumerate(html[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None

    try:
        return json.loads(html[start:end])
    except json.JSONDecodeError:
        return None


def parse_event_venues(booking_data: dict) -> list[dict]:
    """Turn one event's bookingPanel.data into a list of (date, venue)
    entries, each independently classified for Attica. This is what
    fixes the multi-city date bug: every entry gets ITS OWN start/end,
    read from the matching "events" record via venueId, instead of one
    shared date applied to every venue.

    When `venue-city` itself is blank/dash, falls back to classifying
    the street address instead — real cases confirmed 30/8/2026: e.g.
    "Anyma presents ÆDEN ATHENS" has city="" but street="Μαρούσι, 151 23"
    (a real Attica address), while "ΛΕΞ Tour 2026" has city="" but
    street="...Βόλος...ΜΑΓΝΗΣΙΑΣ" (genuinely not Attica). Without this,
    both cases were stuck as "unconfirmed" even though the real answer
    was recoverable from data already on hand."""
    venues_by_id = {v["id"]: v for v in booking_data.get("venues", [])}
    results = []

    for ev in booking_data.get("events", []):
        venue = venues_by_id.get(ev.get("venueId"))
        if not venue:
            continue

        city = venue.get("venue-city", "")
        street = venue.get("venue-address", "")
        venue_name = venue.get("venue-name", "")

        # Try city first, then street, then the venue's own display name
        # (some venues spell the city out directly in their name, e.g.
        # "PRIVILEGE EVENT HOUSE – ΠΑΤΡΑ" — confirmed real case,
        # 2/9/2026) — stopping at the first field that actually resolves
        # to something other than "unconfirmed", rather than committing
        # to a single field and giving up if that one happens to be
        # blank or unrecognized.
        region_status = classify_locality(city)
        if region_status == "unconfirmed" and street:
            region_status = classify_locality(street)
        if region_status == "unconfirmed" and venue_name:
            region_status = classify_locality(venue_name)
        if region_status == "not_attica":
            continue  # confirmed elsewhere in Greece — skip entirely

        results.append({
            "name": venue_name,
            "locality": city,
            "street": street,
            "start": ev.get("event-date", ""),
            "end": ev.get("event-end-date", ""),
            "region_status": region_status,
        })

    return results


def parse_events() -> list[dict]:
    listing = get_current_events()
    print(f"Found {len(listing)} music events listed")

    events = []
    for item in listing:
        print(f"  Checking {item['title']}...")
        try:
            html = requests.get(item["url"], headers=HEADERS, timeout=20).text
            booking_data = extract_booking_data(html)
        except Exception as e:
            print(f"    FAILED: {e}")
            booking_data = None
        time.sleep(1.0)  # be polite — don't hammer the site

        if not booking_data:
            continue

        venues = parse_event_venues(booking_data)
        if not venues:
            continue  # no Attica (or unconfirmed) date at all — skip entirely

        events.append({
            "title": item["title"],
            "url": item["url"],
            "genres": item["genres"],
            "venues": venues,
            "source": "more.com",
        })

    return events


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
    print("Fetching more.com music listings...")
    events = parse_events()
    print(f"Found {len(events)} events with an Attica (or unconfirmed) date")

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
