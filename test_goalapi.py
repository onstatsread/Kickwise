"""
GOAL API exploration script — discovers real response shapes since
their docs list endpoints but not full example payloads. Run via
GitHub Actions with GOAL_API_KEY as a repo secret (never hardcode it).
"""

import os
import time
import json
from datetime import date
import requests

API_KEY = os.environ["GOAL_API_KEY"]
BASE_URL = "https://api.goal-api.com/v1"

HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def get(path, params=None):
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
    print(f"\n{'=' * 60}")
    print(f"GET {url} params={params}")
    print(f"Status: {resp.status_code}")

    # Rate limit headers — worth logging every time to track real usage.
    for h in ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"]:
        if h in resp.headers:
            print(f"{h}: {resp.headers[h]}")

    try:
        data = resp.json()
    except Exception as e:
        print(f"Failed to parse JSON: {e}")
        print(resp.text[:1000])
        return None

    print(json.dumps(data, indent=2)[:3000])
    return data


def main():
    print("### 0. API status (no auth needed) ###")
    try:
        resp = requests.get(f"{BASE_URL}/status", timeout=15)
        print(f"Status: {resp.status_code}")
        print(resp.text[:1000])
    except Exception as e:
        print(f"Status check failed: {e}")

    today = date.today().strftime("%Y-%m-%d")

    print("\n\n### 1. Today's fixtures (all leagues) — with retry ###")
    fixtures = None
    for attempt in range(1, 4):
        fixtures = get(f"/fixtures/date/{today}")
        if fixtures is not None:
            break
        print(f"Retrying in 5s (attempt {attempt}/3)...")
        time.sleep(5)

    print("\n\n### 2. Leagues list — searching for real domestic leagues ###")
    leagues = None
    for attempt in range(1, 4):
        leagues = get("/leagues", params={"limit": 100, "offset": 0})
        if leagues is not None:
            break
        print(f"Retrying in 5s (attempt {attempt}/3)...")
        time.sleep(5)

    target_names = ["Premier League", "Ligue 1", "Serie A", "Bundesliga", "La Liga"]
    found_leagues = []

    if leagues and leagues.get("data"):
        for entry in leagues["data"]:
            name = entry.get("name", "")
            if any(t.lower() in name.lower() for t in target_names):
                found_leagues.append(entry)
                print(f"Found candidate: {entry.get('id')} — {name} ({entry.get('country')})")

    if not found_leagues and leagues and leagues.get("data"):
        print("No target-name matches in first 100 — using first entry as fallback.")
        found_leagues = leagues["data"][:1]

    # If we got any fixtures, grab one real fixture ID and check its odds shape.
    if fixtures and fixtures.get("data"):
        fixture_list = fixtures["data"]
        if isinstance(fixture_list, list) and fixture_list:
            sample_id = fixture_list[0].get("id") or fixture_list[0].get("matchId")
            if sample_id:
                print(f"\n\n### 3. Odds for sample fixture id={sample_id} ###")
                get(f"/fixtures/{sample_id}/odds")

    # If we got any leagues, grab a REAL domestic league (not a random
    # first entry, which was Copa America — no home/away splits for
    # international group-stage football) and check standings shape.
    if found_leagues:
        for candidate in found_leagues[:2]:
            sample_league_id = candidate.get("id")
            print(f"\n\n### 4. Standings (combined) for {candidate.get('name')} id={sample_league_id} ###")
            standings = get(f"/standings/{sample_league_id}")

            # Check if home/away fields are populated inline for this league.
            if standings and standings.get("data"):
                rows = standings["data"]
                if isinstance(rows, list) and rows:
                    first_row = rows[0]
                    has_home_away = first_row.get("homeLeagueGF") is not None
                    print(f"\n>>> Inline home/away fields populated: {has_home_away}")

            print(f"\n\n### 5. Standings (home) for {candidate.get('name')} id={sample_league_id} ###")
            get(f"/standings/{sample_league_id}/home")

            print(f"\n\n### 6. Standings (away) for {candidate.get('name')} id={sample_league_id} ###")
            get(f"/standings/{sample_league_id}/away")

    print("\n\nDone.")


if __name__ == "__main__":
    main()
