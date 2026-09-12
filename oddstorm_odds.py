"""
OddStorm odds fetcher for Kickwise.

Primary markets:
    - Home / Draw / Away (1X2)
    - Over 2.5
    - Under 2.5

Uses plain requests + BeautifulSoup.
No Playwright.
No browser.
No login.

The OddStorm HTML structure used here was confirmed from the
working scraper:

    <tr data-mid="...">
        <td class="od-c-time">17:30</td>
        <td class="od-c-event">
            <a href="...">Home – Away</a>
        </td>

        <td class="od-odd" data-f="b1" data-l="1">1.83</td>
        <td class="od-odd" data-f="bx" data-l="X">3.55</td>
        <td class="od-odd" data-f="b2" data-l="2">3.60</td>
        <td class="od-odd" data-f="bo" data-l="Over">1.82</td>
        <td class="od-odd" data-f="bu" data-l="Under">2.05</td>

        <td class="od-c-bs">4</td>
    </tr>

Important:
    OddStorm's main odds page is cached and parsed ONCE per cache
    period. Individual match lookups then use the parsed index instead
    of repeatedly running BeautifulSoup over the same HTML.
"""

import re
import time
import requests

from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

ODDSTORM_BASE = "https://www.oddstorm.com"
ODDSTORM_ODDS_URL = f"{ODDSTORM_BASE}/odds/"

DAY_CACHE_TTL = 120

REQUEST_TIMEOUT = 20


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# CACHE
# ============================================================

_HTML_CACHE = None
_PARSED_CACHE = None
_CACHE_TIME = 0


# ============================================================
# NORMALIZATION
# ============================================================

def _norm_team(name):
    """
    Normalize a team name for comparison.
    """

    if name is None:
        return ""

    name = str(name).lower().strip()

    # Replace common punctuation with spaces.
    name = name.replace("–", " ")
    name = name.replace("—", " ")
    name = name.replace("-", " ")
    name = name.replace(".", " ")

    # Collapse whitespace.
    name = " ".join(name.split())

    return name


def _team_names_match(a, b):
    """
    Match team names between GOAL API and OddStorm.

    Priority:
        1. exact normalized match
        2. substring match
        3. token-based comparison

    Minimum four characters are required for substring matching
    to reduce false positives.
    """

    a = _norm_team(a)
    b = _norm_team(b)

    if not a or not b:
        return False

    # Exact.
    if a == b:
        return True

    # Substring.
    if len(a) >= 4 and len(b) >= 4:
        if a in b or b in a:
            return True

    # Token comparison.
    a_tokens = set(a.split())
    b_tokens = set(b.split())

    if not a_tokens or not b_tokens:
        return False

    common = a_tokens.intersection(b_tokens)

    # If one name is essentially contained in the other by tokens.
    if common:
        shorter = min(len(a_tokens), len(b_tokens))

        if len(common) >= shorter and shorter >= 2:
            return True

    return False


# ============================================================
# NUMBER CONVERSION
# ============================================================

def _to_float(value):
    """
    Convert bookmaker odds into float.

    Valid range:
        > 1.00
        <= 1000
    """

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if value <= 1.0:
        return None

    if value > 1000:
        return None

    return value


# ============================================================
# IMPLIED PROBABILITY
# ============================================================

def _add_implied_pct(odds_dict, *keys):
    """
    Convert odds into normalized implied percentages.

    Example:

        home_odds = 2.00
        draw_odds = 3.50
        away_odds = 4.00

    Adds:

        home_pct
        draw_pct
        away_pct

    with bookmaker margin normalized out.
    """

    if not odds_dict:
        return odds_dict

    values = []

    for key in keys:

        value = odds_dict.get(key)

        if value is None or value <= 0:
            return odds_dict

        values.append(value)

    inverse = [
        1.0 / value
        for value in values
    ]

    total = sum(inverse)

    if total <= 0:
        return odds_dict

    for key, probability in zip(keys, inverse):

        pct_key = key.replace(
            "_odds",
            "_pct"
        )

        odds_dict[pct_key] = round(
            probability / total * 100,
            1,
        )

    return odds_dict


# ============================================================
# FETCH HTML
# ============================================================

def _fetch_odds_page():
    """
    Fetch OddStorm main odds page.

    Returns HTML or None.
    """

    global _HTML_CACHE
    global _CACHE_TIME

    now = time.time()

    if (
        _HTML_CACHE is not None
        and now - _CACHE_TIME < DAY_CACHE_TTL
    ):
        return _HTML_CACHE

    try:

        response = SESSION.get(
            ODDSTORM_ODDS_URL,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        html = response.text

        if not html:
            print("OddStorm returned empty HTML.")
            return None

        _HTML_CACHE = html
        _CACHE_TIME = now

        return html

    except requests.RequestException as exc:

        print(
            f"OddStorm request failed: {exc}"
        )

        return None

    except Exception as exc:

        print(
            f"OddStorm unexpected fetch error: {exc}"
        )

        return None


# ============================================================
# PARSER
# ============================================================

def _parse_matches(html):
    """
    Parse OddStorm HTML.

    Returns:

        {
            "league-key": {
                "league_name": "...",
                "league_url": "...",
                "matches": [...]
            }
        }
    """

    if not html:
        return {}

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    by_league = {}

    groups = soup.find_all(
        "div",
        class_="od-group",
    )

    for group in groups:

        league_link = group.find(
            "a",
            class_="od-group-league",
        )

        if not league_link:
            continue

        league_name = league_link.get_text(
            " ",
            strip=True,
        )

        league_href = (
            league_link.get("href")
            or ""
        )

        if league_href.startswith("/"):
            league_url = (
                ODDSTORM_BASE +
                league_href
            )
        else:
            league_url = league_href

        slug_match = re.search(
            r"/odds/league/([^/]+)",
            league_href,
        )

        if slug_match:
            league_key = slug_match.group(1)
        else:
            league_key = league_name

        matches = []

        rows = group.find_all(
            "tr",
            attrs={"data-mid": True},
        )

        for row in rows:

            match_id = row.get(
                "data-mid"
            )

            # ------------------------------------------------
            # TIME
            # ------------------------------------------------

            time_cell = row.find(
                "td",
                class_="od-c-time",
            )

            match_time = (
                time_cell.get_text(
                    " ",
                    strip=True,
                )
                if time_cell
                else None
            )

            # ------------------------------------------------
            # EVENT
            # ------------------------------------------------

            event_cell = row.find(
                "td",
                class_="od-c-event",
            )

            if not event_cell:
                continue

            link = event_cell.find(
                "a",
                href=True,
            )

            if not link:
                continue

            href = link.get("href") or ""

            if href.startswith("/"):
                match_url = (
                    ODDSTORM_BASE + href
                )
            else:
                match_url = href

            event_text = link.get_text(
                " ",
                strip=True,
            )

            # ------------------------------------------------
            # TEAM SPLIT
            # ------------------------------------------------

            if "–" in event_text:

                home, away = (
                    event_text.split(
                        "–",
                        1,
                    )
                )

            elif " - " in event_text:

                home, away = (
                    event_text.split(
                        " - ",
                        1,
                    )
                )

            else:
                continue

            home = home.strip()
            away = away.strip()

            if not home or not away:
                continue

            # ------------------------------------------------
            # ODDS
            # ------------------------------------------------

            odds = {
                "home_odds": None,
                "draw_odds": None,
                "away_odds": None,
                "over_odds": None,
                "under_odds": None,
            }

            odd_cells = row.find_all(
                "td",
                attrs={"data-f": True},
            )

            for cell in odd_cells:

                field = (
                    cell.get("data-f")
                    or ""
                ).lower()

                value = _to_float(
                    cell.get_text(
                        " ",
                        strip=True,
                    )
                )

                if field == "b1":
                    odds["home_odds"] = value

                elif field == "bx":
                    odds["draw_odds"] = value

                elif field == "b2":
                    odds["away_odds"] = value

                elif field == "bo":
                    odds["over_odds"] = value

                elif field == "bu":
                    odds["under_odds"] = value

            # ------------------------------------------------
            # BOOKMAKER COUNT
            # ------------------------------------------------

            bookmaker_count = None

            bs_cell = row.find(
                "td",
                class_="od-c-bs",
            )

            if bs_cell:

                raw_bs = bs_cell.get_text(
                    " ",
                    strip=True,
                )

                try:
                    bookmaker_count = int(
                        raw_bs
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    bookmaker_count = None

            matches.append({
                "match_id": match_id,
                "time": match_time,
                "home": home,
                "away": away,
                "match_url": match_url,

                "home_odds": odds[
                    "home_odds"
                ],
                "draw_odds": odds[
                    "draw_odds"
                ],
                "away_odds": odds[
                    "away_odds"
                ],

                "over_odds": odds[
                    "over_odds"
                ],
                "under_odds": odds[
                    "under_odds"
                ],

                "bookmaker_count":
                    bookmaker_count,
            })

        if matches:

            by_league[league_key] = {
                "league_name": league_name,
                "league_url": league_url,
                "matches": matches,
            }

    return by_league


# ============================================================
# PARSED CACHE
# ============================================================

def get_all_matches():
    """
    Fetch and parse OddStorm.

    The HTML and parsed result are cached so repeated match lookups
    do not repeatedly run BeautifulSoup.
    """

    global _PARSED_CACHE

    now = time.time()

    if (
        _PARSED_CACHE is not None
        and now - _CACHE_TIME < DAY_CACHE_TTL
    ):
        return _PARSED_CACHE

    html = _fetch_odds_page()

    if not html:
        return {}

    parsed = _parse_matches(html)

    _PARSED_CACHE = parsed

    return parsed


# ============================================================
# FLAT ITERATOR
# ============================================================

def _iter_all_matches(by_league):
    """
    Flatten league dictionary into individual matches.
    """

    if not by_league:
        return

    for league_data in by_league.values():

        for match in league_data.get(
            "matches",
            [],
        ):
            yield match


# ============================================================
# MATCH LOOKUP
# ============================================================

def find_match(home, away):
    """
    Find one OddStorm match by home/away teams.

    Returns the complete match dictionary or None.
    """

    if not home or not away:
        return None

    by_league = get_all_matches()

    if not by_league:
        return None

    for match in _iter_all_matches(
        by_league
    ):

        if not _team_names_match(
            match.get("home"),
            home,
        ):
            continue

        if not _team_names_match(
            match.get("away"),
            away,
        ):
            continue

        return match

    return None


# ============================================================
# MARKET ODDS
# ============================================================

def get_market_odds(home, away):
    """
    Return market odds in the same general structure used by
    Kickwise's existing odds modules.

    Returns:

        {
            "market_odds": {
                "home_odds": ...,
                "draw_odds": ...,
                "away_odds": ...,
                "home_pct": ...,
                "draw_pct": ...,
                "away_pct": ...,
                "bookmaker_count": ...
            },

            "market_ou25": {
                "over_odds": ...,
                "under_odds": ...,
                "over_pct": ...,
                "under_pct": ...,
                "bookmaker_count": ...
            }
        }

    If no match is found:

        {
            "market_odds": None,
            "market_ou25": None
        }
    """

    result = {
        "market_odds": None,
        "market_ou25": None,
    }

    match = find_match(
        home,
        away,
    )

    if not match:
        print(
            f"OddStorm match not found: "
            f"{home} vs {away}"
        )

        return result

    # --------------------------------------------------------
    # 1X2
    # --------------------------------------------------------

    h = match.get("home_odds")
    d = match.get("draw_odds")
    a = match.get("away_odds")

    if (
        h is not None
        and d is not None
        and a is not None
    ):

        market = {
            "home_odds": h,
            "draw_odds": d,
            "away_odds": a,
        }

        _add_implied_pct(
            market,
            "home_odds",
            "draw_odds",
            "away_odds",
        )

        market["bookmaker_count"] = (
            match.get("bookmaker_count")
        )

        market["match_id"] = (
            match.get("match_id")
        )

        market["match_url"] = (
            match.get("match_url")
        )

        result["market_odds"] = market

    # --------------------------------------------------------
    # OVER / UNDER 2.5
    # --------------------------------------------------------

    over = match.get("over_odds")
    under = match.get("under_odds")

    if (
        over is not None
        and under is not None
    ):

        market_ou = {
            "over_odds": over,
            "under_odds": under,
        }

        _add_implied_pct(
            market_ou,
            "over_odds",
            "under_odds",
        )

        market_ou["bookmaker_count"] = (
            match.get("bookmaker_count")
        )

        market_ou["match_id"] = (
            match.get("match_id")
        )

        market_ou["match_url"] = (
            match.get("match_url")
        )

        result["market_ou25"] = market_ou

    return result


# ============================================================
# ALIASES
# ============================================================

def get_oddstorm_market_odds(home, away):
    """
    Explicit alias for use from combined_odds.py.
    """
    return get_market_odds(
        home,
        away,
    )


# ============================================================
# CACHE CONTROL
# ============================================================

def clear_cache():
    """
    Clear OddStorm HTML + parsed caches.
    """

    global _HTML_CACHE
    global _PARSED_CACHE
    global _CACHE_TIME

    _HTML_CACHE = None
    _PARSED_CACHE = None
    _CACHE_TIME = 0

    print("OddStorm cache cleared.")


# ============================================================
# DEBUG
# ============================================================

def oddstorm_status():
    """
    Basic diagnostic information.
    """

    parsed_leagues = (
        len(_PARSED_CACHE)
        if _PARSED_CACHE
        else 0
    )

    match_count = 0

    if _PARSED_CACHE:

        for league_data in _PARSED_CACHE.values():

            match_count += len(
                league_data.get(
                    "matches",
                    [],
                )
            )

    return {
        "base_url": ODDSTORM_BASE,
        "odds_url": ODDSTORM_ODDS_URL,
        "cache_ttl_seconds": DAY_CACHE_TTL,
        "cache_active": (
            _PARSED_CACHE is not None
        ),
        "parsed_leagues": parsed_leagues,
        "parsed_matches": match_count,
    }

2. Replace "combined_odds.py"

This version makes OddStorm first. Your other sources remain fallbacks.
