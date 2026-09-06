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


def main():
    print("Fetching full GOAL API leagues list...")
    all_leagues = fetch_all_leagues()
    print(f"\nTotal leagues fetched: {len(all_leagues)}\n")

    if not all_leagues:
        print("No leagues fetched — aborting.")
        return

    # Build a lookup: normalized "country name" -> list of (id, full_name, country)
    lookup = []
    for lg in all_leagues:
        name = lg.get("name", "")
        country = (lg.get("country") or {}).get("name") or lg.get("countryName", "")
        lookup.append({
            "id": lg.get("id"),
            "name": name,
            "country": country,
            "combined": f"{country} {name}",
        })

    mapping = {}
    unmatched = []

    for target in TARGET_LEAGUES:
        # target format: "Country - League"
        if " - " not in target:
            unmatched.append(target)
            continue

        country_part, league_part = target.split(" - ", 1)
        target_norm = normalize(f"{country_part} {league_part}")

        # Try exact-ish match first: country appears AND league name is
        # a close match.
        candidates = [
            l for l in lookup
            if normalize(country_part) in normalize(l["country"])
            or normalize(country_part) in normalize(l["name"])
        ]

        if not candidates:
            candidates = lookup  # fall back to searching everything

        best = difflib.get_close_matches(
            target_norm,
            [normalize(c["combined"]) for c in candidates],
            n=1,
            cutoff=0.5,
        )

        if best:
            idx = [normalize(c["combined"]) for c in candidates].index(best[0])
            match = candidates[idx]
            mapping[target] = {
                "goalapi_id": match["id"],
                "goalapi_name": match["name"],
                "goalapi_country": match["country"],
            }
        else:
            unmatched.append(target)

    print(f"\n{'=' * 60}")
    print(f"MATCHED: {len(mapping)} / {len(TARGET_LEAGUES)}")
    print("=" * 60)
    for target, info in mapping.items():
        print(f"  {target!r:45s} -> {info['goalapi_id']} ({info['goalapi_country']} / {info['goalapi_name']})")

    print(f"\n{'=' * 60}")
    print(f"UNMATCHED: {len(unmatched)}")
    print("=" * 60)
    for target in unmatched:
        print(f"  {target}")

    print("\n\nFull mapping as Python dict (paste into a module):\n")
    print("GOALAPI_LEAGUE_IDS = {")
    for target, info in mapping.items():
        print(f'    {target!r}: {info["goalapi_id"]!r},')
    print("}")


if __name__ == "__main__":
    main()
