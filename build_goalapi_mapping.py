"""
Builds a GOAL API league-ID mapping for all 61 leagues currently in
daily_predictions.py's LEAGUE_CODES, by fetching GOAL API's full
leagues list and fuzzy-matching names.

Run via GitHub Actions with GOAL_API_KEY as a repo secret.
"""

import os
import time
import json
import difflib
import requests

API_KEY = os.environ["GOAL_API_KEY"]
BASE_URL = "https://api.goal-api.com/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# Same 61 leagues as daily_predictions.py's LEAGUE_CODES — just the
# "Country - League" names, since that's what we're matching against
# GOAL API's own league names.
TARGET_LEAGUES = [
    "Belarus - Vysshaya Liga", "Brazil - Serie A", "Brazil - Serie B",
    "Canada - Premier League", "Chile - Liga de Primera", "China - Super League",
    "China - League One", "Colombia - Primera A", "Ecuador - Liga Pro",
    "Estonia - Meistriliiga", "Faroe Islands - Premier League",
    "Finland - Veikkausliiga", "Finland - Ykkosliiga", "Georgia - Erovnuli Liga",
    "Iceland - Besta deild", "Iceland - 1. Deild", "Ireland - Premier Division",
    "Ireland - First Division", "Kazakhstan - Premier League", "Latvia - Virsliga",
    "Lithuania - A Lyga", "Malaysia - Super League", "Norway - Eliteserien",
    "Norway - 1st Division", "Paraguay - Primera Div.", "Peru - Liga 1",
    "South Korea - K League 1", "South Korea - K League 2", "Sweden - Allsvenskan",
    "Sweden - Superettan", "Uruguay - Liga AUF", "USA - MLS",
    "USA - USL Championship", "Venezuela - Liga FUTVE",
    "England - Southern Football League", "Germany - Bundesliga",
    "Belgium - First Amateur Division", "Algeria - Ligue 1", "Australia - A-League",
    "Australia - Brisbane Premier League", "Chile - Primera B", "Bolivia - LFPB",
    "Greece - Super League 2", "Estonia - Esiliiga", "Iceland - Division 2",
    "Greece - Football League", "India - I-League", "India - Super League",
    "Jamaica - National Premier League", "Iran - Azadegan League",
    "Kenya - Premier League", "Jordan - League", "Morocco - Botola",
    "Singapore - S.League", "New Zealand - Championship", "Syria - Premier League",
    "Thailand - League 1", "Vietnam - V.League 1", "Taiwan - Premier League",
    "Turkmenistan - Higher League", "Tajikistan - Higher League",
]


def fetch_all_leagues():
    """Paginates through GOAL API's /leagues endpoint to get everything."""
    all_leagues = []
    offset = 0
    limit = 100

    while True:
        try:
            resp = requests.get(
                f"{BASE_URL}/leagues",
                headers=HEADERS,
                params={"limit": limit, "offset": offset},
                timeout=20,
            )
        except Exception as e:
            print(f"Request failed at offset {offset}: {e}")
            break

        if resp.status_code != 200:
            print(f"Status {resp.status_code} at offset {offset}: {resp.text[:300]}")
            break

        data = resp.json()
        batch = data.get("data", [])

        if not batch:
            break

        all_leagues.extend(batch)
        print(f"Fetched {len(batch)} leagues at offset {offset} (total so far: {len(all_leagues)})")

        if len(batch) < limit:
            break  # last page

        offset += limit
        time.sleep(0.5)  # be gentle, though free tier is 1000/day not rate/sec limited here

    return all_leagues


def normalize(name):
    return " ".join(name.lower().replace(".", "").split())


# Words that indicate a DIFFERENT competition type than a normal
# top-tier or clearly-named domestic league — a candidate containing
# one of these is rejected UNLESS the target name also contains it
# (e.g. target "China - League One" legitimately contains "League",
# but "Chile - Liga de Primera" should never match "Primera B").
DISQUALIFYING_WORDS = [
    "cup", "reserve", "youth", "friendly", "supercup", "super cup",
    "women", "u21", "u20", "u19", "u23", "academy", "trophy",
]

# Country name normalization overrides — GOAL API uses different
# country naming than Kickwise's LEAGUE_CODES in some cases.
COUNTRY_ALIASES = {
    "ireland": "republic of ireland",
    "south korea": "korea republic",
    "taiwan": "chinese taipei",
    "china": "china pr",
}


def country_matches(target_country, candidate_country):
    t = normalize(target_country)
    c = normalize(candidate_country)
    t_alias = COUNTRY_ALIASES.get(t, t)
    return t == c or t_alias == c or t in c or c in t


def has_disqualifying_word(candidate_name, target_league_name):
    cand_norm = normalize(candidate_name)
    target_norm = normalize(target_league_name)

    for word in DISQUALIFYING_WORDS:
        if word in cand_norm and word not in target_norm:
            return True
    return False


def main():
    print("Fetching full GOAL API leagues list...")
    all_leagues = fetch_all_leagues()
    print(f"\nTotal leagues fetched: {len(all_leagues)}\n")

    if not all_leagues:
        print("No leagues fetched — aborting.")
        return

    lookup = []
    for lg in all_leagues:
        name = lg.get("name", "")
        country = (lg.get("country") or {}).get("name") or lg.get("countryName", "")
        lookup.append({"id": lg.get("id"), "name": name, "country": country})

    mapping = {}
    unmatched = []
    flagged_for_review = []

    for target in TARGET_LEAGUES:
        if " - " not in target:
            unmatched.append(target)
            continue

        country_part, league_part = target.split(" - ", 1)

        # HARD requirement: country must match. This alone eliminates
        # the Turkmenistan-Youth-League-style cross-country errors.
        country_candidates = [
            l for l in lookup if country_matches(country_part, l["country"])
        ]

        if not country_candidates:
            unmatched.append(f"{target}  (NO COUNTRY MATCH for {country_part!r})")
            continue

        # Reject candidates with disqualifying words (cup/reserve/
        # youth/wrong-tier suffixes) unless the target itself implies
        # that tier/type.
        clean_candidates = [
            l for l in country_candidates
            if not has_disqualifying_word(l["name"], league_part)
        ]

        if not clean_candidates:
            unmatched.append(f"{target}  (only cup/reserve/youth matches found in {country_part})")
            continue

        # Rank remaining candidates by name similarity to the league
        # part only (not combined with country, which was already
        # filtered on above).
        league_norm = normalize(league_part)
        scored = []
        for l in clean_candidates:
            ratio = difflib.SequenceMatcher(
                None, league_norm, normalize(l["name"])
            ).ratio()
            scored.append((ratio, l))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_ratio, best_match = scored[0]

        entry = {
            "goalapi_id": best_match["id"],
            "goalapi_name": best_match["name"],
            "goalapi_country": best_match["country"],
            "match_confidence": round(best_ratio, 2),
        }

        mapping[target] = entry

        # Flag anything below a reasonable confidence for manual review
        # rather than trusting it silently.
        if best_ratio < 0.6:
            flagged_for_review.append((target, entry))

    print(f"\n{'=' * 60}")
    print(f"MATCHED: {len(mapping)} / {len(TARGET_LEAGUES)}")
    print("=" * 60)
    for target, info in mapping.items():
        flag = "  ⚠️ LOW CONFIDENCE — VERIFY" if info["match_confidence"] < 0.6 else ""
        print(f"  {target!r:45s} -> {info['goalapi_id']} "
              f"({info['goalapi_country']} / {info['goalapi_name']}) "
              f"[{info['match_confidence']}]{flag}")

    print(f"\n{'=' * 60}")
    print(f"UNMATCHED: {len(unmatched)}")
    print("=" * 60)
    for target in unmatched:
        print(f"  {target}")

    print(f"\n{'=' * 60}")
    print(f"FLAGGED FOR MANUAL REVIEW (confidence < 0.6): {len(flagged_for_review)}")
    print("=" * 60)
    for target, info in flagged_for_review:
        print(f"  {target!r} -> {info['goalapi_name']} ({info['goalapi_country']}) [{info['match_confidence']}]")

    print("\n\nFull mapping as Python dict (VERIFY flagged entries before using):\n")
    print("GOALAPI_LEAGUE_IDS = {")
    for target, info in mapping.items():
        comment = f"  # ⚠️ VERIFY: {info['goalapi_name']}" if info["match_confidence"] < 0.6 else ""
        print(f'    {target!r}: {info["goalapi_id"]!r},{comment}')
    print("}")


if __name__ == "__main__":
    main()
