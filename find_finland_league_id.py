"""
One-off lookup script — run this once with your real GOAL_API_KEY to
find Finland Ykkösliiga's correct, DISTINCT league_id.

Why this exists: goalapi_leagues.py currently has Ykkösliiga sharing
Veikkausliiga's id (cmr77dxae00tlrx06ay98mhmc) as a known placeholder
— see the NOTE comment on that line. That means any Ykkösliiga match
silently pulls Veikkausliiga's standings table instead: wrong teams,
wrong stats, wrong prediction, no error raised anywhere.

Usage:
    export GOAL_API_KEY=your_real_key
    python find_finland_league_id.py

This calls GET /leagues?country=Finland (per the documented
/countries/:id/leagues and /leagues endpoints) and prints every
Finnish league GOAL API has, so you can read off the correct id by
eye rather than guessing or fuzzy-matching blind.
"""

import os
import requests

GOAL_API_BASE = "https://api.goal-api.com/v1"
GOAL_API_KEY = os.environ.get("GOAL_API_KEY", "")

if not GOAL_API_KEY:
    raise SystemExit("Set GOAL_API_KEY in your environment first.")

session = requests.Session()
session.headers.update({"Authorization": f"Bearer {GOAL_API_KEY}"})


def get_all_pages(path, params=None):
    """Simple paginated GET — same pattern as goalapi_fetcher.py's fix,
    since /leagues is a list endpoint too and could paginate."""
    params = dict(params or {})
    params.setdefault("limit", 100)
    offset = 0
    results = []

    while True:
        params["offset"] = offset
        resp = session.get(f"{GOAL_API_BASE}{path}", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        rows = data.get("data") or []
        if not isinstance(rows, list):
            break
        results.extend(rows)

        pagination = data.get("pagination") or {}
        if not pagination.get("hasMore"):
            break
        offset += params["limit"]

    return results


def main():
    # Try the documented search/filter first — /leagues likely accepts
    # a country or search param based on the docs' /countries/:id/leagues
    # and /leagues list. If ?country= doesn't filter server-side, we
    # just filter client-side below instead.
    leagues = get_all_pages("/leagues", params={"country": "Finland"})

    if not leagues:
        print("No results with ?country=Finland — falling back to fetching "
              "the full /leagues list and filtering client-side (slower).")
        leagues = get_all_pages("/leagues")
        leagues = [
            lg for lg in leagues
            if "finland" in str(lg.get("country") or lg.get("countryName") or "").lower()
        ]

    if not leagues:
        print("Still nothing — check the /leagues response shape manually; "
              "field names for country may differ from what this script assumes.")
        return

    print(f"Found {len(leagues)} Finland league(s):\n")
    for lg in leagues:
        print(f"  id:   {lg.get('id')}")
        print(f"  name: {lg.get('name')}")
        print(f"  raw:  {lg}")
        print()

    print(
        "Look for the entry named 'Ykkösliiga' (or a close variant) and "
        "copy its id into goalapi_leagues.py, replacing the placeholder "
        "on the 'Finland - Ykkosliiga' line."
    )


if __name__ == "__main__":
    main()
