"""
AnnaBet market-odds scraper.

Primary markets:
    1X2 / H-D-A
    Over/Under 2.5

Uses AnnaBet's /upcoming/ page for fixtures and its H2H page
for the O/U bookmaker table.

Returns the same structure expected by app.py.
"""

import re
import time
import requests
from bs4 import BeautifulSoup


ANNABET_BASE = "https://annabet.com"

ANNABET_ODDS_HEADERS = {
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
    "Referer": "https://annabet.com/en/soccerstats/upcoming/",
    "Connection": "keep-alive",
}


SESSION = requests.Session()
SESSION.headers.update(ANNABET_ODDS_HEADERS)


# ------------------------------------------------------------
# Cache
# ------------------------------------------------------------

_ODDS_CACHE = {}

# Odds can move, so don't cache too long.
ODDS_CACHE_TTL = 300  # 5 minutes


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

FLOAT_RE = re.compile(r"^\d+(?:\.\d+)?$")


def _float(value):
    """
    Convert a string to float safely.
    """
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    if value.lower() in ("inf", "-inf", "infinity"):
        return None

    if not FLOAT_RE.match(value):
        return None

    try:
        return float(value)
    except ValueError:
        return None


def _clean_name(name):
    return " ".join(str(name).split()).strip().lower()


def _team_match(a, b):
    """
    Reasonably strict team-name comparison.
    """
    a = _clean_name(a)
    b = _clean_name(b)

    if a == b:
        return True

    if a in b or b in a:
        return True

    return False


def _norm_match(home, away):
    return (
        _clean_name(home),
        _clean_name(away),
    )


# ------------------------------------------------------------
# Fetch
# ------------------------------------------------------------

def _fetch(url, timeout=25):
    try:
        response = SESSION.get(url, timeout=timeout)
        response.raise_for_status()
        return response
    except Exception as exc:
        print(f"AnnaBet odds fetch failed: {url} -> {exc}")
        return None


# ------------------------------------------------------------
# Extract 1X2 from Upcoming page
# ------------------------------------------------------------

def _extract_upcoming_1x2(home, away):
    """
    Search AnnaBet's global upcoming page for the requested fixture.

    AnnaBet's upcoming rows contain:

        date/time
        league
        Home - Away
        H/D/A odds

    Returns:

        {
            "home_odds": ...,
            "draw_odds": ...,
            "away_odds": ...,
            "h2h_url": ...
        }

    or None.
    """

    url = f"{ANNABET_BASE}/en/soccerstats/upcoming/"

    response = _fetch(url)

    if not response:
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    target_home = _clean_name(home)
    target_away = _clean_name(away)

    for row in soup.find_all("tr"):

        cells = row.find_all("td")

        if not cells:
            continue

        row_text = row.get_text(" ", strip=True)

        # Quick filter.
        if target_home not in _clean_name(row_text):
            continue

        if target_away not in _clean_name(row_text):
            continue

        # ----------------------------------------------------
        # Find H2H link.
        # ----------------------------------------------------

        h2h_link = None

        for a in row.find_all("a", href=True):
            href = a["href"]

            if "h2h.php" in href:
                h2h_link = href
                break

        # ----------------------------------------------------
        # Find numeric odds in the row.
        # ----------------------------------------------------

        numbers = []

        for cell in cells:

            text = cell.get_text(" ", strip=True)

            # Find normal decimal odds.
            found = re.findall(r"\b\d+\.\d+\b", text)

            for value in found:

                number = _float(value)

                if number is not None:
                    numbers.append(number)

        # We need at least 3 odds.
        if len(numbers) < 3:
            continue

        # The first three relevant odds in the fixture row are
        # expected to be Home / Draw / Away.
        #
        # We deliberately restrict them to sensible bookmaker
        # odds to avoid accidentally grabbing dates/times/etc.
        candidates = [
            n for n in numbers
            if 1.01 <= n <= 100
        ]

        if len(candidates) < 3:
            continue

        h, d, a = candidates[-3:]

        if not (h > 1 and d > 1 and a > 1):
            continue

        if h2h_link and h2h_link.startswith("/"):
            h2h_url = ANNABET_BASE + h2h_link
        elif h2h_link:
            h2h_url = h2h_link
        else:
            h2h_url = None

        return {
            "home_odds": h,
            "draw_odds": d,
            "away_odds": a,
            "h2h_url": h2h_url,
        }

    return None


# ------------------------------------------------------------
# Extract O/U 2.5 from H2H page
# ------------------------------------------------------------

def _extract_ou25(h2h_url):
    """
    Extract bookmaker O/U 2.5 odds from AnnaBet's H2H page.

    AnnaBet's H2H page contains several O/U-related statistics.

    We specifically target the section headed:

        1x2 Betting Odds

    and its:

        Total Goals Under-Over

    table.

    The 2.5 row contains the bookmaker odds.
    """

    if not h2h_url:
        return None

    response = _fetch(h2h_url)

    if not response:
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # --------------------------------------------------------
    # First strategy:
    #
    # Find text containing "1x2 Betting Odds", then inspect
    # nearby tables.
    # --------------------------------------------------------

    heading = None

    for element in soup.find_all(
        string=re.compile(r"1x2 Betting Odds", re.I)
    ):
        heading = element.parent
        break

    tables = soup.find_all("table")

    # Search all tables, but score tables which look like the
    # bookmaker odds table higher.
    candidates = []

    for table in tables:

        text = table.get_text(" ", strip=True)

        lower = text.lower()

        score = 0

        if "total goals under-over" in lower:
            score += 5

        if "2.5 goals" in lower:
            score += 5

        if "1.5 goals" in lower:
            score += 2

        if "3.5 goals" in lower:
            score += 2

        if "at home" in lower:
            score += 1

        if "at away" in lower:
            score += 1

        if "all games" in lower:
            score += 1

        if score:
            candidates.append((score, table))

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    # --------------------------------------------------------
    # Parse candidate tables.
    # --------------------------------------------------------

    for _, table in candidates:

        text = table.get_text(" ", strip=True)

        if "2.5 goals" not in text.lower():
            continue

        # Find the row containing 2.5 goals.
        for row in table.find_all("tr"):

            row_text = row.get_text(" ", strip=True)

            if not re.search(
                r"\b2\.5\s+goals\b",
                row_text,
                re.I
            ):
                continue

            # ------------------------------------------------
            # Find odds pairs.
            #
            # AnnaBet represents pairs as:
            #
            #     1.67-2.50
            #
            # where the first number is Under and the second
            # number is Over.
            # ------------------------------------------------

            pairs = re.findall(
                r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)",
                row_text
            )

            parsed_pairs = []

            for left, right in pairs:

                under = _float(left)
                over = _float(right)

                if (
                    under is None
                    or over is None
                    or under <= 1
                    or over <= 1
                ):
                    continue

                parsed_pairs.append(
                    (under, over)
                )

            if not parsed_pairs:
                continue

            # AnnaBet's table has several columns:
            #
            # At Home
            # At Away
            # All Games
            #
            # and the total-goals bookmaker odds.
            #
            # The final pair is normally the most useful aggregate
            # market figure.
            under, over = parsed_pairs[-1]

            return {
                "over_odds": over,
                "under_odds": under,
            }

    return None


# ------------------------------------------------------------
# Main function
# ------------------------------------------------------------

def get_annabet_market_odds(home, away):
    """
    Get AnnaBet's:

        H/D/A
        O/U 2.5

    Returns:

        {
            "market_odds": {
                "home_odds": ...,
                "draw_odds": ...,
                "away_odds": ...
            },

            "market_ou25": {
                "over_odds": ...,
                "under_odds": ...
            }
        }

    Missing markets are returned as None.
    """

    cache_key = _norm_match(home, away)

    cached = _ODDS_CACHE.get(cache_key)

    if cached:
        timestamp, data = cached

        if time.time() - timestamp < ODDS_CACHE_TTL:
            return data

    result = {
        "market_odds": None,
        "market_ou25": None,
    }

    # --------------------------------------------------------
    # 1. Get H/D/A and H2H URL.
    # --------------------------------------------------------

    upcoming = _extract_upcoming_1x2(home, away)

    if not upcoming:
        print(
            f"AnnaBet: fixture not found: "
            f"{home} - {away}"
        )

        _ODDS_CACHE[cache_key] = (
            time.time(),
            result
        )

        return result

    result["market_odds"] = {
        "home_odds": upcoming["home_odds"],
        "draw_odds": upcoming["draw_odds"],
        "away_odds": upcoming["away_odds"],
    }

    # --------------------------------------------------------
    # 2. Get O/U 2.5.
    # --------------------------------------------------------

    if upcoming.get("h2h_url"):

        ou25 = _extract_ou25(
            upcoming["h2h_url"]
        )

        if ou25:
            result["market_ou25"] = ou25

    _ODDS_CACHE[cache_key] = (
        time.time(),
        result
    )

    return result
