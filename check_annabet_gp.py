"""
AnnaBet League Games-Played Checker
-----------------------------------

Gets ACTUAL team GP from AnnaBet league tables.

Instead of guessing that one of the first few numeric cells is GP,
this version:

1. Finds tables.
2. Reads the table headers.
3. Locates the GP column by header name.
4. Extracts GP for every team.
5. Reports min/max/average GP.
6. Shows every team's GP.
7. Flags leagues where teams have different GP.

BATCHING — added after a full 162-league run got connection-timeout
blocked partway through (succeeded on ~10 leagues, then every request
failed at the TCP-connect level, not even reaching an HTTP response —
the signature of AnnaBet's firewall dropping the IP mid-run, not a
normal rate-limit reply). GitHub Actions runners get a fresh IP on
each new run, so the fix is to run smaller batches as SEPARATE
workflow runs instead of one long 162-league run that eventually
gets blocked on whichever IP it started with.

Control which slice runs via environment variables:
    BATCH_START=0    (0-indexed, inclusive) — which league to start at
    BATCH_END=20     (exclusive) — which league to stop before

Example: leagues 0–19 in one run, 20–39 in the next run, etc.
If unset, BATCH_START defaults to 0 and BATCH_END defaults to the
full league count (i.e. runs everything, old behavior).
"""

import requests
import time
import os
import statistics
from bs4 import BeautifulSoup

from annabet_leagues import ANNABET_LEAGUE_IDS


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Referer": "https://annabet.com/en/soccerstats/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}


SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def clean_text(text):
    """Normalize whitespace."""
    return " ".join(text.split()).strip()


ANNABET_TABLE_HEADER = [
    "#", "Team", "GP", "W", "T", "L", "GF", "GA", "Diff",
    "Pts", "Pts/G", "W%", "ØGF", "ØGA"
]


def _get_exact_header(table):
    """Returns this table's header row cells, or None if it has no rows."""
    rows = table.find_all("tr")
    if not rows:
        return None
    return [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]


def extract_team_gp(html):
    """
    Extract team names and GP from the ONE genuine season-standings
    table on the page — identified by an EXACT match against AnnaBet's
    known 14-column header (ANNABET_TABLE_HEADER), same as app.py's
    fetch_stats_annabet() uses.

    The earlier version of this function matched any table containing
    a column merely named "GP" — but AnnaBet's page has several such
    tables (home-only splits, away-only splits, prior-season history,
    last-N-games form, etc.), so it was concatenating rows from all of
    them together, producing impossible results (400+ "teams", the
    same team appearing multiple times with different GP values).
    Requiring the exact header and using only the FIRST match (which
    is consistently the "All Games" standings table) fixes this.

    Returns:
    {
        "teams": [{"team": "...", "gp": 23}, ...],
        "table_found": True/False,
        "gp_column": index,
        "headers": [...]
    }
    """

    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        if _get_exact_header(table) != ANNABET_TABLE_HEADER:
            continue

        # Found the real standings table — GP is always column index 2
        # in this exact header layout.
        gp_column_found = 2
        all_teams = []

        for row in table.find_all("tr")[1:]:
            cells = [
                clean_text(c.get_text(" ", strip=True))
                for c in row.find_all("td")
            ]

            if len(cells) <= gp_column_found:
                continue

            gp_text = cells[gp_column_found]
            if not gp_text.isdigit():
                continue

            gp = int(gp_text)
            if gp < 0 or gp > 100:
                continue

            team_name = cells[1] if len(cells) > 1 else cells[0]

            bad_names = {
                "total", "average", "home", "away",
                "all games", "team", "league average",
            }
            if team_name.lower() in bad_names or len(team_name) < 2:
                continue

            all_teams.append({"team": team_name, "gp": gp})

        # Only use the FIRST matching table — this is the "All Games"
        # standings table; subsequent matches (if any) are the
        # home-only/away-only split tables that follow it on the page.
        return {
            "teams": all_teams,
            "table_found": True,
            "gp_column": gp_column_found,
            "headers": ANNABET_TABLE_HEADER,
        }

    return {
        "teams": [],
        "table_found": False,
        "gp_column": None,
        "headers": [],
    }


# ---------------------------------------------------------
# LEAGUE CHECK
# ---------------------------------------------------------

def check_league(name, serie_id, retries=2):

    url = (
        f"https://annabet.com/en/soccerstats/"
        f"serie_{serie_id}_x.html"
    )

    last_error = None

    for attempt in range(1, retries + 1):

        try:

            response = SESSION.get(
                url,
                timeout=20
            )

            response.raise_for_status()

            data = extract_team_gp(response.text)

            teams = data["teams"]

            if not teams:

                snippet = (
                    response.text[:500]
                    .replace("\n", " ")
                    .replace("\r", " ")
                )

                return {
                    "status": "no_data",
                    "teams": [],
                    "debug": (
                        f"status={response.status_code} "
                        f"len={len(response.text)} "
                        f"url={response.url} "
                        f"headers={data['headers']} "
                        f"snippet={snippet}"
                    )
                }

            gps = [x["gp"] for x in teams]

            minimum_gp = min(gps)
            maximum_gp = max(gps)
            average_gp = statistics.mean(gps)

            unique_gp = sorted(set(gps))

            # -------------------------------------------------
            # DETERMINE WHETHER ALL TEAMS HAVE SAME GP
            # -------------------------------------------------

            balanced = len(unique_gp) == 1

            return {
                "status": "ok",

                "teams": teams,

                "team_count": len(teams),

                "min_gp": minimum_gp,

                "max_gp": maximum_gp,

                "average_gp": round(average_gp, 2),

                "unique_gp": unique_gp,

                "balanced": balanced,

                "gp_column": data["gp_column"],

                "headers": data["headers"],

            }

        except Exception as e:

            last_error = e

            if attempt < retries:
                time.sleep(5)

    return {
        "status": "error",
        "error": str(last_error),
        "teams": [],
    }


# ---------------------------------------------------------
# PRINT LEAGUE
# ---------------------------------------------------------

def print_league_result(name, serie_id, result):
    """
    One compact line per league: name, serie_id, GP, team count.
    (Previously printed a full per-team roster table for every league,
    which made scanning 162 leagues' worth of output impractical.)
    """

    if result["status"] != "ok":

        if result["status"] == "no_data":
            print(f"❓ {name:45} serie_{serie_id:<5} no GP table found")
        else:
            print(
                f"❌ {name:45} serie_{serie_id:<5} "
                f"failed: {result.get('error', 'unknown error')}"
            )

        return

    flag = "" if result["balanced"] else " ⚠️ mixed GP"

    print(
        f"{'✅' if result['max_gp'] >= 10 else '  '} "
        f"{name:45} serie_{serie_id:<5} "
        f"GP {result['min_gp']}-{result['max_gp']} "
        f"({result['team_count']} teams){flag}"
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    all_leagues = list(ANNABET_LEAGUE_IDS.items())

    # BATCH_START/BATCH_END let you run a slice of the full league list
    # as its own GitHub Actions invocation (fresh IP each run), instead
    # of one long run that gets connection-blocked partway through.
    batch_start = int(os.environ.get("BATCH_START", "0"))
    batch_end = int(os.environ.get("BATCH_END", str(len(all_leagues))))

    test_leagues = dict(all_leagues[batch_start:batch_end])

    print(
        f"🔍 Checking ACTUAL team GP for leagues "
        f"{batch_start}–{batch_end - 1} "
        f"({len(test_leagues)} of {len(all_leagues)} total) on AnnaBet..."
    )

    print(
        "📌 GP is extracted from the table's GP column — "
        "not guessed from fixture count."
    )

    print()

    results = []

    for name, serie_id in test_leagues.items():

        result = check_league(
            name,
            serie_id
        )

        print_league_result(
            name,
            serie_id,
            result
        )

        if result["status"] == "ok":

            results.append({
                "name": name,
                "serie_id": serie_id,
                "min_gp": result["min_gp"],
                "max_gp": result["max_gp"],
                "average_gp": result["average_gp"],
                "team_count": result["team_count"],
                "balanced": result["balanced"],
            })

        else:

            results.append({
                "name": name,
                "serie_id": serie_id,
                "min_gp": 0,
                "max_gp": 0,
                "average_gp": 0,
                "team_count": 0,
                "balanced": False,
            })

        # AnnaBet request pacing
        time.sleep(20)

    # -----------------------------------------------------
    # SUMMARY — just the leagues that qualify (GP >= 10),
    # since each league already printed its own compact line above.
    # -----------------------------------------------------

    qualifying = [
        x for x in results
        if x["max_gp"] >= 10
    ]

    print()
    print("=" * 60)
    print(f"📊 Qualifying leagues (GP >= 10): {len(qualifying)}")
    print("=" * 60)

    for r in sorted(qualifying, key=lambda x: -x["max_gp"]):
        print(f"   {r['name']:45} serie_{r['serie_id']:<5} GP {r['max_gp']}")


if __name__ == "__main__":
    main()
