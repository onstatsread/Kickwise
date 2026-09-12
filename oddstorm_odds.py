"""
OddStorm odds fetcher — candidate replacement for Oddsbook's odds
fetching, with a major advantage: plain `requests` works with ZERO
Cloudflare challenge (confirmed 2026-09-09), no Playwright/browser
needed at all. Also includes Over/Under 2.5 directly, which Oddsbook
gated behind a login wall we couldn't crack.

CONFIRMED real HTML structure:
    <tr data-mid="13549562">
      <td class="od-c-time">17:30</td>
      <td class="od-c-event"><a href="/odds/match/...">Home – Away</a></td>
      <td class="od-odd" data-f="b1" data-l="1">1.83</td>   Home
      <td class="od-odd" data-f="bx" data-l="X">3.55</td>   Draw
      <td class="od-odd" data-f="b2" data-l="2">3.60</td>   Away
      <td class="od-odd" data-f="bo" data-l="Over">1.82</td>  Over 2.5
      <td class="od-odd" data-f="bu" data-l="Under">2.05</td> Under 2.5
      <td class="od-c-bs">4</td>                              bookmaker count
    </tr>

Team names are split on the en-dash "–" (U+2013), NOT a hyphen.

League grouping IS confirmed present on a single-league page, not
just the homepage — verified via screenshot 2026-09-12 against
https://www.oddstorm.com/odds/league/1940888-georgia-erovnuli, which
showed the same "Georgia · Erovnuli" header, en-dash team names, and
1/X/2 + Over/Under boxes as the homepage listing.

FIX (2026-09-12, part 1): _fetch_odds_page() / get_all_matches() /
get_market_odds() all accept an optional league_url so callers can
fetch a SPECIFIC league's page (via
oddstorm_leagues.get_oddstorm_league_url()) instead of always
scraping the generic /odds/ homepage listing, which only surfaces
whatever's currently featured there. See combined_odds.py.

FIX (2026-09-12, part 2): the same screenshot also showed the
league page defaulting to the NEXT upcoming matchday ("Sunday 13
September 2026"), not necessarily today's date. Each od-group carries
its own date in od-group-date (e.g. "Sunday 13 September 2026 UKT").
_parse_matches() now parses that into a real date per league group,
and get_market_odds()/get_all_matches() accept a target_date to filter
against it — so a match on a page defaulting to tomorrow is never
silently paired with today's stats/prediction run. Without this, a
lookup for today's fixture could match a same-named fixture group
dated for a different day and return the wrong odds without any
error.
"""

import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

ODDSTORM_BASE = "https://www.oddstorm.com"
ODDSTORM_ODDS_URL = f"{ODDSTORM_BASE}/odds/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

_DAY_CACHE = {}
DAY_CACHE_TTL = 120  # odds refresh ~every minute per OddStorm's own FAQ

# e.g. "Sunday 13 September 2026 UKT" -> day=13, month=September, year=2026
_GROUP_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")


def _norm_team(name):
    return " ".join(str(name or "").lower().split()).strip()


def _team_names_match(a, b):
    """
    Exact match first; falls back to substring containment (same
    technique app.py's resolve_team() uses for AnnaBet/GOAL API stats)
    since different odds providers abbreviate team names differently
    — e.g. GOAL API's "Vila Nova" vs a provider's "Vila Nova FC".
    Requires at least 4 chars to avoid short-name false positives.
    """
    a, b = _norm_team(a), _norm_team(b)
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return True
    return False


def _to_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f <= 1.0 or f > 1000:
        return None
    return f


def _parse_group_date(text):
    """
    Parses an od-group-date string like "Sunday 13 September 2026 UKT"
    into a datetime.date. Returns None if it doesn't match the
    expected pattern (e.g. unexpected format change) — callers treat
    None as "unknown date, don't filter it out" rather than dropping
    matches on a parse miss.
    """
    if not text:
        return None

    match = _GROUP_DATE_RE.search(text)
    if not match:
        return None

    day, month_name, year = match.groups()

    try:
        return datetime.strptime(f"{day} {month_name} {year}", "%d %B %Y").date()
    except ValueError:
        return None


def _add_implied_pct(odds_dict, *keys):
    """Same normalization pattern used for AnnaBet/Oddsbook — adds
    *_pct fields (bookmaker overround removed) so existing app.py
    code (format_match_html, meets_blog2_standard) works unchanged."""
    if not odds_dict:
        return odds_dict

    vals = [odds_dict.get(k) for k in keys]
    if any(v is None or v <= 0 for v in vals):
        return odds_dict

    raw = [1 / v for v in vals]
    total = sum(raw)
    if total <= 0:
        return odds_dict

    for k, r in zip(keys, raw):
        pct_key = k.replace("_odds", "_pct")
        odds_dict[pct_key] = round(r / total * 100, 1)

    return odds_dict


def _fetch_odds_page(url=None):
    """
    Fetches an odds page — plain requests works fine (confirmed no
    Cloudflare challenge). Defaults to the generic /odds/ listing,
    but accepts a specific league URL (from oddstorm_leagues.py's
    get_oddstorm_league_url()) to fetch that league's full card
    instead of whatever happens to be currently featured on the
    homepage. Returns raw HTML, or None on failure.
    """
    target = url or ODDSTORM_ODDS_URL

    cached = _DAY_CACHE.get(target)
    if cached and time.time() - cached[0] < DAY_CACHE_TTL:
        return cached[1]

    try:
        resp = SESSION.get(target, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        print(f"OddStorm fetch failed ({target}): {exc}")
        return None

    _DAY_CACHE[target] = (time.time(), html)
    return html


def _parse_matches(html):
    """
    Returns a dict keyed by league:
        {
            "1940888-georgia-erovnuli": {
                "league_name": "Georgia · Erovnuli",
                "league_url": "https://www.oddstorm.com/odds/league/1940888-georgia-erovnuli",
                "date": date(2026, 9, 13),   # parsed from od-group-date, or None
                "matches": [
                    {
                        "match_id": "13549810", "time": "14:00",
                        "home": "FC Dila Gori",
                        "away": "FC Dinamo Batumi",
                        "match_url": "...",
                        "home_odds": 2.56, "draw_odds": 3.45, "away_odds": 2.65,
                        "over_odds": 1.76, "under_odds": 2.05,
                        "bookmaker_count": 11,
                    },
                    ...
                ],
            },
            ...
        }

    CONFIRMED real structure — both on the generic /odds/ homepage
    listing and on a single-league page (screenshot 2026-09-12):
        <div class="od-container">
          <div class="od-group">
            <div class="od-group-head">
              <a class="od-group-league" href="/odds/league/{id}-{slug}">
                {Country} · {League}
              </a>
              <span class="od-group-date">Sunday 13 September 2026 UKT</span>
            </div>
            <table class="od-table">
              <tbody><tr data-mid="...">...</tr>...</tbody>
            </table>
          </div>
          ...
        </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    by_league = {}

    for group in soup.find_all("div", class_="od-group"):
        league_link = group.find("a", class_="od-group-league")
        if not league_link:
            continue

        league_name = league_link.get_text(strip=True)
        league_href = league_link.get("href", "")
        league_url = ODDSTORM_BASE + league_href if league_href.startswith("/") else league_href

        # Extract a stable key from the URL slug, e.g.
        # "/odds/league/1869352-andorra-super-cup" -> "1869352-andorra-super-cup"
        slug_match = re.search(r"/odds/league/([^/]+)", league_href)
        league_key = slug_match.group(1) if slug_match else league_name

        date_cell = group.find("span", class_="od-group-date")
        group_date = _parse_group_date(date_cell.get_text(strip=True) if date_cell else None)

        matches = []

        for row in group.find_all("tr", attrs={"data-mid": True}):
            match_id = row.get("data-mid")

            time_cell = row.find("td", class_="od-c-time")
            time_str = time_cell.get_text(strip=True) if time_cell else None

            event_cell = row.find("td", class_="od-c-event")
            if not event_cell:
                continue

            link = event_cell.find("a", href=True)
            if not link:
                continue

            match_url = ODDSTORM_BASE + link["href"] if link["href"].startswith("/") else link["href"]
            event_text = link.get_text(strip=True)

            # Teams are split on an EN-DASH (–, U+2013), not a hyphen.
            if "–" in event_text:
                home, away = event_text.split("–", 1)
            elif " - " in event_text:
                home, away = event_text.split(" - ", 1)
            else:
                continue

            home = home.strip()
            away = away.strip()

            odds = {}
            for cell in row.find_all("td", attrs={"data-f": True}):
                field = cell.get("data-f")
                value = _to_float(cell.get_text(strip=True))

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

            bs_cell = row.find("td", class_="od-c-bs")
            bookmaker_count = None
            if bs_cell:
                try:
                    bookmaker_count = int(bs_cell.get_text(strip=True))
                except ValueError:
                    pass

            matches.append({
                "match_id": match_id,
                "time": time_str,
                "home": home,
                "away": away,
                "match_url": match_url,
                "home_odds": odds.get("home_odds"),
                "draw_odds": odds.get("draw_odds"),
                "away_odds": odds.get("away_odds"),
                "over_odds": odds.get("over_odds"),
                "under_odds": odds.get("under_odds"),
                "bookmaker_count": bookmaker_count,
            })

        by_league[league_key] = {
            "league_name": league_name,
            "league_url": league_url,
            "date": group_date,
            "matches": matches,
        }

    return by_league


def get_all_matches(league_url=None):
    """
    Returns the by_league dict described in _parse_matches' docstring.

    league_url: pass a specific OddStorm league page URL (from
        oddstorm_leagues.get_oddstorm_league_url()) to fetch that
        league's full card instead of the generic homepage listing,
        which only shows whatever's currently featured there.
    """
    html = _fetch_odds_page(league_url)
    if not html:
        return {}
    return _parse_matches(html)


def _iter_all_matches(by_league, target_date=None):
    """
    Flattens the by_league dict into a simple list of matches.

    target_date: if given (a datetime.date), skips any league group
        whose parsed date doesn't match it — a group with no parseable
        date (group_date is None) is NOT skipped, since silently
        dropping every match on a date-parse miss would be worse than
        occasionally matching an unfiltered group. Pass None to
        disable date filtering entirely.
    """
    for league_data in by_league.values():
        group_date = league_data.get("date")
        if target_date is not None and group_date is not None and group_date != target_date:
            continue
        for m in league_data["matches"]:
            yield m


def get_market_odds(home, away, target_date=None, league_url=None):
    """
    Same return shape as annabet_odds.get_annabet_market_odds() /
    oddsbook_odds.get_oddsbook_market_odds():

        {
            "market_odds": {"home_odds":..., "draw_odds":..., "away_odds":...},
            "market_ou25": {"over_odds":..., "under_odds":...}
        }

    league_url: pass the specific OddStorm league page URL when known
        (see combined_odds.py, which sources it from
        oddstorm_leagues.get_oddstorm_league_url()) so smaller leagues
        not featured on the homepage still get matched correctly.
        Falls back to the generic /odds/ listing if not given.

    target_date: a datetime.date (or None to skip filtering). A league
        page can default to the NEXT upcoming matchday rather than
        today (confirmed via screenshot, e.g. a page fetched on the
        12th showing "Sunday 13 September 2026"). Passing today's date
        here prevents pairing a fixture with a different day's odds
        under the same team names without any error being raised.
    """
    result = {"market_odds": None, "market_ou25": None}

    by_league = get_all_matches(league_url)

    for m in _iter_all_matches(by_league, target_date=target_date):
        if not _team_names_match(m["home"], home):
            continue
        if not _team_names_match(m["away"], away):
            continue

        if m.get("home_odds") is not None:
            result["market_odds"] = _add_implied_pct(
                {
                    "home_odds": m["home_odds"],
                    "draw_odds": m["draw_odds"],
                    "away_odds": m["away_odds"],
                },
                "home_odds", "draw_odds", "away_odds",
            )

        if m.get("over_odds") is not None:
            result["market_ou25"] = _add_implied_pct(
                {
                    "over_odds": m["over_odds"],
                    "under_odds": m["under_odds"],
                },
                "over_odds", "under_odds",
            )

        return result

    return result
