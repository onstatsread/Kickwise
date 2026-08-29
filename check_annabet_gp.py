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


def find_gp_column(headers):
    """
    Find the GP column from table headers.

    Accepts:
        GP
        Games Played
        Games
        P
        Pl

    GP is preferred because it is the most explicit.
    """

    normalized = []

    for h in headers:
        h = clean_text(h).lower()
        normalized.append(h)

    # Strongest match first
    for i, h in enumerate(normalized):
        if h in ("gp", "games played", "games-played"):
            return i

    # Possible alternatives
    for i, h in enumerate(normalized):
        if h in ("games", "played"):
            return i

    return None


def extract_team_gp(html):
    """
    Extract team names and their actual GP from tables.

    Returns:

    {
        "teams": [
            {"team": "...", "gp": 23},
            ...
        ],
        "table_found": True/False,
        "gp_column": index,
        "headers": [...]
    }
    """

    soup = BeautifulSoup(html, "html.parser")

    all_teams = []
    found_table = False
    gp_column_found = None
    selected_headers = []

    for table in soup.find_all("table"):

        rows = table.find_all("tr")

        if not rows:
            continue

        # -------------------------------------------------
        # FIND HEADER ROW
        # -------------------------------------------------

        header_row = None
        headers = []

        for row in rows[:5]:

            ths = row.find_all(["th", "td"])

            if not ths:
                continue

            row_headers = [
                clean_text(x.get_text(" ", strip=True))
                for x in ths
            ]

            gp_index = find_gp_column(row_headers)

            if gp_index is not None:
                header_row = row
                headers = row_headers
                gp_column_found = gp_index
                selected_headers = headers
                found_table = True
                break

        if header_row is None:
            continue

        # -------------------------------------------------
        # EXTRACT DATA ROWS
        # -------------------------------------------------

        header_index = rows.index(header_row)

        for row in rows[header_index + 1:]:

            cells = row.find_all("td")

            if not cells:
                continue

            texts = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in cells
            ]

            # Need enough cells to reach GP column
            if len(texts) <= gp_column_found:
                continue

            gp_text = texts[gp_column_found]

            # GP must be an integer
            if not gp_text.isdigit():
                continue

            gp = int(gp_text)

            # Sanity check
            if gp < 0 or gp > 100:
                continue

            # -------------------------------------------------
            # TEAM NAME
            # -------------------------------------------------

            # Usually team is column 1 because:
            # column 0 = rank
            # column 1 = team
            #
            # But be defensive.

            team_name = ""

            if len(texts) > 1:
                team_name = texts[1]

            if not team_name:
                team_name = texts[0]

            # Ignore obvious footer/summary rows
            bad_names = {
                "total",
                "average",
                "home",
                "away",
                "all games",
                "team",
                "league average",
            }

            if team_name.lower() in bad_names:
                continue

            # Ignore rows that are clearly not teams
            if len(team_name) < 2:
                continue

            all_teams.append({
                "team": team_name,
                "gp": gp
            })

    return {
        "teams": all_teams,
        "table_found": found_table,
        "gp_column": gp_column_found,
        "headers": selected_headers,
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

    print()
    print("=" * 70)
    print(f"{name} (serie_{serie_id})")
    print("=" * 70)

    if result["status"] != "ok":

        if result["status"] == "no_data":

            print("❓ No usable GP table found.")
            print(f"   DEBUG: {result.get('debug', 'n/a')}")

        else:

            print(
                f"❌ Failed: "
                f"{result.get('error', 'unknown error')}"
            )

        return

    teams = result["teams"]

    print(
        f"Teams found : {result['team_count']}"
    )

    print(
        f"GP range    : "
        f"{result['min_gp']} - {result['max_gp']}"
    )

    print(
        f"Average GP  : "
        f"{result['average_gp']}"
    )

    print(
        f"GP values   : "
        f"{result['unique_gp']}"
    )

    if result["balanced"]:

        print(
            f"✅ All teams have the same GP: "
            f"{result['max_gp']}"
        )

    else:

        print(
            "⚠️ Teams have different GP values."
        )

    print()
    print(
        f"{'TEAM':45} {'GP':>5}"
    )
    print("-" * 52)

    for team in teams:

        print(
            f"{team['team'][:45]:45} "
            f"{team['gp']:>5}"
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
    # SUMMARY
    # -----------------------------------------------------

    print()
    print()
    print("=" * 80)
    print("📊 FINAL SUMMARY")
    print("=" * 80)

    successful = [
        x for x in results
        if x["max_gp"] > 0
    ]

    print(
        f"\n✅ Successfully extracted GP: "
        f"{len(successful)} leagues"
    )

    print()

    print(
        f"{'LEAGUE':50} "
        f"{'MIN':>5} "
        f"{'MAX':>5} "
        f"{'AVG':>7} "
        f"{'TEAMS':>6}"
    )

    print("-" * 80)

    for r in sorted(
        successful,
        key=lambda x: -x["max_gp"]
    ):

        flag = ""

        if not r["balanced"]:
            flag = " ⚠️"

        print(
            f"{r['name'][:50]:50} "
            f"{r['min_gp']:>5} "
            f"{r['max_gp']:>5} "
            f"{r['average_gp']:>7.2f} "
            f"{r['team_count']:>6}"
            f"{flag}"
        )

    # -----------------------------------------------------
    # UNEQUAL GP LEAGUES
    # -----------------------------------------------------

    unequal = [
        x for x in successful
        if not x["balanced"]
    ]

    if unequal:

        print()
        print(
            "⚠️ LEAGUES WHERE TEAMS HAVE DIFFERENT GP:"
        )

        for r in unequal:

            print(
                f"   {r['name']} "
                f"(serie_{r['serie_id']}): "
                f"{r['min_gp']}–{r['max_gp']} GP"
            )


if __name__ == "__main__":
    main()
