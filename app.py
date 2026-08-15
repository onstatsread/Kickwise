"""
Kickwise Backend — FastAPI server
Deploy to Render.com (free tier)
"""
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests, os, subprocess, statistics, tempfile, shutil, difflib, re, time
from scipy.stats import poisson
from datetime import date
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from concurrent.futures import ThreadPoolExecutor
from odds import get_odds_for_card, get_ou25_for_card, router as odds_router  # NEW — market odds from The Odds API
from api_football import get_fallback_odds  # Fallback tier 2 — currently suspended, kept in case reactivated
from odds_api_io import get_odds_api_io_fallback, get_ou25_api_io_fallback  # NEW — fallback tier, tried before api_football

app = FastAPI(title="Kickwise API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(odds_router)  # NEW — registers /odds/{sport_key} and /odds/{sport_key}/{home}/{away}


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.soccerstats.com/",
    "Connection": "keep-alive",
}
BASE    = "https://www.soccerstats.com"
MODEL   = "A_mix2.xlsx"

# ── AnnaBet stats source — free, no Cloudflare block, replaces
# SoccerStats+ScraperAPI for leagues it covers. Verified: their table
# structure is ['#','Team','GP','W','T','L','GF','GA','Diff','Pts',
# 'Pts/G','W%','ØGF','ØGA'], found as the FIRST group of 3 consecutive
# tables sharing that header (All Games / At Home / At Away, in that
# order) — confirmed correct by checking Home GP + Away GP = All GP for
# every team on a real page (K League 1). Falls back to SoccerStats for
# leagues AnnaBet doesn't cover (Brazil Serie C, Australia NPL states).
ANNABET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # NOT "br" — requests can't decompress
                                          # Brotli without an extra package,
                                          # asking for it returns garbage text
    "Connection": "keep-alive",
    "Referer": "https://annabet.com/en/soccerstats/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}
ANNABET_SESSION = requests.Session()  # persistent session — bare/fresh
ANNABET_SESSION.headers.update(ANNABET_HEADERS)  # connections got blocked

ANNABET_TABLE_HEADER = ['#', 'Team', 'GP', 'W', 'T', 'L', 'GF', 'GA', 'Diff', 'Pts', 'Pts/G', 'W%', 'ØGF', 'ØGA']

# Maps your existing SoccerStats-style league codes (used everywhere
# else in the app) to AnnaBet's serie_ID. Only the leagues currently in
# blog.py's trimmed LEAGUE_CODES are mapped — leagues not listed here
# fall back to SoccerStats automatically.
ANNABET_SERIE_ID = {
    "belarus": 232, "brazil": 217, "brazil2": 259, "canada": 750,
    "chile": 301, "china": 248, "china2": 731, "colombia": 329,
    "ecuador": 313, "estonia": 242, "faroeislands": 368, "finland": 7,
    "finland2": 35, "georgia": 235, "iceland": 114, "iceland2": 392,
    "ireland": 42, "ireland2": 163, "kazakhstan": 328, "latvia": 223,
    "lithuania": 226, "malaysia": 521, "norway": 36, "norway2": 173,
    "paraguay": 347, "peru": 321, "southkorea": 249, "southkorea2": 543,
    "sweden": 32, "sweden2": 33, "uruguay": 439, "usa": 43, "usa2": 362,
    "venezuela": 314,
}
# Reverse lookup: serie_ID -> our league code, used to match fixture
# rows on the global "upcoming" page back to our own league codes.
_ANNABET_ID_TO_CODE = {v: k for k, v in ANNABET_SERIE_ID.items()}

ANNABET_UPCOMING_URL = "https://annabet.com/en/soccerstats/upcoming/"
_ANNABET_FIXTURES_CACHE = {}  # (timestamp, {league_code: [matches]})
ANNABET_FIXTURES_CACHE_TTL = 1800  # 30 min — this page updates frequently
                                    # as kickoffs pass, shorter TTL than stats

_ANNABET_SERIE_LINK_RE = re.compile(r'/serie_(\d+)_')
_ANNABET_DATETIME_RE = re.compile(r'(\d{1,2})\.(\d{1,2})\.\s+(\d{1,2}):(\d{2})')


def fetch_all_upcoming_annabet():
    """Fetch the single global upcoming-fixtures page and group matches
    by our league codes (via serie_ID matched from each row's league
    link). One fetch covers every AnnaBet-mapped league at once —
    cheaper than the old per-league fixture calls SoccerStats needed.

    NOTE: this page only shows a short rolling window (observed to cut
    off ~12-14 hours ahead of the current time, not a full day) — late
    kickoffs (e.g. MLS evening US games) may not appear here yet even
    though they're the correct day. See fetch_fixtures_annabet_perleague()
    below for a per-league alternative under investigation that may have
    a longer (multi-day) lookahead window instead.
    """
    cached = _ANNABET_FIXTURES_CACHE.get("_all")
    if cached and (time.time() - cached[0]) < ANNABET_FIXTURES_CACHE_TTL:
        return cached[1]

    resp = ANNABET_SESSION.get(ANNABET_UPCOMING_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    by_league = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        row_text = cells[0].get_text(" ", strip=True)
        dt_match = _ANNABET_DATETIME_RE.search(row_text)
        if not dt_match:
            continue

        # Find the league link (points to /serie_{ID}_...) to identify
        # which league this fixture belongs to
        league_link = None
        for cell in cells:
            a = cell.find("a", href=_ANNABET_SERIE_LINK_RE)
            if a:
                league_link = a
                break
        if not league_link:
            continue
        serie_match = _ANNABET_SERIE_LINK_RE.search(league_link["href"])
        serie_id = int(serie_match.group(1))
        code = _ANNABET_ID_TO_CODE.get(serie_id)
        if not code:
            continue  # league we don't track — skip

        # Find the team matchup link (h2h.php)
        team_link = None
        for cell in cells:
            a = cell.find("a", href=re.compile(r'h2h\.php'))
            if a:
                team_link = a
                break
        if not team_link:
            continue
        team_text = team_link.get_text(strip=True)
        if " - " not in team_text:
            continue
        home, away = team_text.split(" - ", 1)

        day, month, hour, minute = dt_match.groups()
        time_str = f"{hour}:{minute}"
        date_str = f"{day}.{month}."

        by_league.setdefault(code, []).append({
            "date": date_str, "time": time_str,
            "home": home.strip(), "away": away.strip(),
        })

    _ANNABET_FIXTURES_CACHE["_all"] = (time.time(), by_league)
    return by_league


def fetch_fixtures_annabet(code, day, month):
    """Returns fixtures for one league code on a given day/month (both
    ints), matching against the cached global upcoming-fixtures data."""
    by_league = fetch_all_upcoming_annabet()
    matches = by_league.get(code, [])
    target = f"{day}.{month}."
    return [
        {"time": m["time"], "home": m["home"], "away": m["away"]}
        for m in matches if m["date"] == target
    ]


def _annabet_get_header(table):
    rows = table.find_all("tr")
    if not rows:
        return None
    return [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]


def _annabet_parse_table(table):
    teams = {}
    for row in table.find_all("tr")[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) < 8:
            continue
        try:
            name = cells[1]
            gp = int(cells[2])
            gf = int(cells[6])
            ga = int(cells[7])
            teams[name] = {"gp": gp, "gf": gf, "ga": ga}
        except (ValueError, IndexError):
            continue
    return teams


def fetch_stats_annabet(serie_id):
    """Fetch team stats from AnnaBet for one league. Returns the same
    shape fetch_stats() (SoccerStats version) returns, so run_model()
    doesn't need to know which source the data came from."""
    url = f"https://annabet.com/en/soccerstats/serie_{serie_id}_x.html"
    resp = ANNABET_SESSION.get(url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tables = soup.find_all("table")
    matching = [t for t in tables if _annabet_get_header(t) == ANNABET_TABLE_HEADER]
    if len(matching) < 3:
        return {}

    all_table, home_table, away_table = matching[0], matching[1], matching[2]
    home_data = _annabet_parse_table(home_table)
    away_data = _annabet_parse_table(away_table)

    result = {}
    for team, h in home_data.items():
        a = away_data.get(team)
        if not a:
            continue
        gp = h["gp"] + a["gp"]
        gf = h["gf"] + a["gf"]
        ga = h["ga"] + a["ga"]
        result[team] = {
            "gp": gp,
            "gf": gf / gp if gp else 0,
            "ga": ga / gp if gp else 0,
            "tot": (gf + ga) / gp if gp else 0,
            "hgf": h["gf"] / h["gp"] if h["gp"] else 0,
            "hga": h["ga"] / h["gp"] if h["gp"] else 0,
            "htot": (h["gf"] + h["ga"]) / h["gp"] if h["gp"] else 0,
            "agf": a["gf"] / a["gp"] if a["gp"] else 0,
            "aga": a["ga"] / a["gp"] if a["gp"] else 0,
            "atot": (a["gf"] + a["ga"]) / a["gp"] if a["gp"] else 0,
        }
    return result

# NEW — in-memory cache for fetch_stats() results, keyed by league code.
# Without this, every match in a league triggers its own full scrape of
# that league's homeaway.asp page — e.g. 5 matches in a league = 5
# separate ScraperAPI render calls (30-60s each) fetching the exact same
# team stats. Team stats don't meaningfully change within a day, so this
# caches per league for 1 hour, cutting most of those redundant calls.
_STATS_CACHE = {}  # {league_code: (timestamp, team_data)}
STATS_CACHE_TTL = 3600  # seconds


# NEW — reverted back to ScraperAPI (new key from a fresh account) after
# FlareSolverr's self-hosted instance kept crash-looping from Chromium
# running out of memory on Render's free 512MB tier. render=true solves
# Cloudflare's Turnstile challenge; premium=true added after seeing
# intermittent 500s from ScraperAPI on this domain with plain render=true.
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")

def fetch_protected(url, timeout=60):
    """Fetch `url` through ScraperAPI's rendering proxy. Returns a
    requests.Response-like object (has .text and .status_code) so it's a
    drop-in replacement for requests.get() at existing call sites."""
    return requests.get(
        "https://api.scraperapi.com/",
        params={
            "api_key": SCRAPERAPI_KEY,
            "url": url,
            "render": "true",
            "premium": "true",
        },
        timeout=timeout,
    )


# NEW — maps your SoccerStats league codes to The Odds API's sport keys.
# Only major leagues are covered by the odds provider — leagues not listed
# here simply won't get market_odds (the frontend already handles that
# gracefully since it only renders when odds data is present).
LEAGUE_TO_SPORT_KEY = {
    # Verified directly against this account's /v4/sports/ response —
    # every key below was confirmed to exist, not guessed.
    "argentina":   "soccer_argentina_primera_division",  # Apertura; same key likely also covers Clausura fixtures
    "austria":     "soccer_austria_bundesliga",
    "belgium":     "soccer_belgium_first_div",
    "brazil":      "soccer_brazil_campeonato",
    "brazil2":     "soccer_brazil_serie_b",
    "chile":       "soccer_chile_campeonato",
    "china":       "soccer_china_superleague",
    "denmark":     "soccer_denmark_superliga",
    "england":     "soccer_epl",
    "england2":    "soccer_efl_champ",
    "england3":    "soccer_england_league1",
    "england4":    "soccer_england_league2",
    "finland":     "soccer_finland_veikkausliiga",
    "france":      "soccer_france_ligue_one",
    "germany":     "soccer_germany_bundesliga",
    "germany2":    "soccer_germany_bundesliga2",
    "germany3":    "soccer_germany_liga3",
    "greece":      "soccer_greece_super_league",
    "ireland":     "soccer_league_of_ireland",
    "italy":       "soccer_italy_serie_a",
    "italy2":      "soccer_italy_serie_b",
    "mexico":      "soccer_mexico_ligamx",  # Apertura; likely also covers Clausura fixtures
    "netherlands": "soccer_netherlands_eredivisie",
    "norway":      "soccer_norway_eliteserien",
    "poland":      "soccer_poland_ekstraklasa",
    "portugal":    "soccer_portugal_primeira_liga",
    "russia":      "soccer_russia_premier_league",
    "scotland":    "soccer_spl",
    "southkorea":  "soccer_korea_kleague1",
    "spain":       "soccer_spain_la_liga",
    "sweden":      "soccer_sweden_allsvenskan",
    "sweden2":     "soccer_sweden_superettan",
    "switzerland": "soccer_switzerland_superleague",
    "turkey":      "soccer_turkey_super_league",
    "usa":         "soccer_usa_mls",
    # NOT available on this plan (confirmed absent from /v4/sports/):
    # UEFA Champions League, UEFA Europa League — only qualification
    # rounds and Nations League exist in this account's coverage.
    # Re-check /v4/sports/?apiKey=YOUR_KEY periodically — The Odds API
    # adds leagues over time, and any newly available league just needs
    # a new line here.
}


def calc_win_draw_away(lambda_home, lambda_away, max_goals=10):
    """Compute Win/Draw/Away probabilities from Poisson-distributed expected goals."""
    try:
        lambda_home = float(lambda_home)
        lambda_away = float(lambda_away)
    except (TypeError, ValueError):
        return None
    if lambda_home < 0 or lambda_away < 0:
        return None

    home_win = draw = away_win = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p
    total = home_win + draw + away_win
    if total <= 0:
        return None
    return {
        "home_pct": round(home_win / total * 100, 1),
        "draw_pct": round(draw / total * 100, 1),
        "away_pct": round(away_win / total * 100, 1),
    }


def pct_to_odds(pct):
    if not pct or pct <= 0:
        return None
    return round(100 / pct, 2)


def calc_odds(lambda_home, lambda_away):
    wda = calc_win_draw_away(lambda_home, lambda_away)
    if not wda:
        return {"home_pct": None, "draw_pct": None, "away_pct": None,
                "home_odds": None, "draw_odds": None, "away_odds": None}
    return {
        "home_pct": wda["home_pct"], "draw_pct": wda["draw_pct"], "away_pct": wda["away_pct"],
        "home_odds": pct_to_odds(wda["home_pct"]),
        "draw_odds": pct_to_odds(wda["draw_pct"]),
        "away_odds": pct_to_odds(wda["away_pct"]),
    }


def calc_over_under_25(lambda_home, lambda_away, max_goals=10):
    """
    Model's own Over/Under 2.5 goals probabilities — same Poisson approach
    as calc_win_draw_away, just summing joint probabilities by TOTAL goals
    (home + away) relative to the 2.5 line instead of by which side scores
    more. Returns model-implied odds the same way calc_odds() does for 1X2.
    """
    try:
        lambda_home = float(lambda_home)
        lambda_away = float(lambda_away)
    except (TypeError, ValueError):
        return {"over_pct": None, "under_pct": None, "over_odds": None, "under_odds": None}
    if lambda_home < 0 or lambda_away < 0:
        return {"over_pct": None, "under_pct": None, "over_odds": None, "under_odds": None}

    over = under = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)
            if h + a > 2.5:
                over += p
            else:
                under += p
    total = over + under
    if total <= 0:
        return {"over_pct": None, "under_pct": None, "over_odds": None, "under_odds": None}

    over_pct = round(over / total * 100, 1)
    under_pct = round(under / total * 100, 1)
    return {
        "over_pct": over_pct, "under_pct": under_pct,
        "over_odds": pct_to_odds(over_pct), "under_odds": pct_to_odds(under_pct),
    }


def resolve_team(name, team_data):
    """
    Try to match `name` to a team in team_data (SoccerStats' own stats page).
    Returns None — rather than a wrong guess — when no confident match exists.
    Silently substituting the closest-sounding name (e.g. matching
    "KuPS Akatemia" to "SJK Akatemia") corrupts the whole prediction
    downstream, so this only returns a match it's actually confident about.
    """
    if name in team_data:
        return name

    # Substring match — only accept if exactly ONE team qualifies, and the
    # shorter string is long enough that a coincidental substring is unlikely.
    substring_candidates = [
        k for k in team_data
        if len(name) >= 5 and (name in k or k in name)
    ]
    if len(substring_candidates) == 1:
        return substring_candidates[0]

    # Fuzzy match — raised cutoff from 0.6 to 0.8, and only accept if
    # there's a single clear best match (not several similarly-close ones).
    close = difflib.get_close_matches(name, team_data.keys(), n=2, cutoff=0.8)
    if len(close) == 1:
        return close[0]

    return None


def fetch_stats(code):
    # Check cache first — skip the slow scrape entirely if we fetched
    # this league's stats within the last hour.
    cached = _STATS_CACHE.get(code)
    if cached and (time.time() - cached[0]) < STATS_CACHE_TTL:
        return cached[1]

    # AnnaBet only — no ScraperAPI/SoccerStats fallback, per decision to
    # go fully free. Leagues not in ANNABET_SERIE_ID (Brazil Serie C,
    # Australia NPL states) will return empty here — they were dropped
    # from LEAGUE_CODES for this reason.
    if code in ANNABET_SERIE_ID:
        try:
            result = fetch_stats_annabet(ANNABET_SERIE_ID[code])
            if result:
                _STATS_CACHE[code] = (time.time(), result)
                return result
        except Exception as e:
            print(f"AnnaBet fetch failed for {code}: {e}")

    return {}
TIME_RE  = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')
DAY_RE   = re.compile(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b\s*')


def clean_team_name(name):
    return DAY_RE.sub("", name).strip()


def _norm_key(a, b):
    """Normalized dedup key — lowercased, whitespace-collapsed, so the same
    match listed with slightly different formatting across SoccerStats'
    different tables (extra spaces, casing) is still recognized as a
    duplicate instead of slipping through as two separate fixtures."""
    def clean(s):
        return " ".join(s.lower().split())
    return (clean(a), clean(b))


def fetch_fixtures(code, date_str=None):
    if date_str:
        today1 = date_str.strip()
    else:
        d = date.today()
        today1 = f"{d.day} {d.strftime('%b')}"

    # AnnaBet only — no ScraperAPI/SoccerStats fallback, per decision to
    # go fully free. One shared global fetch covers all mapped leagues,
    # so this is fast/cheap after the first call in a run. Returns
    # immediately even if AnnaBet genuinely has no matches that day for
    # this league (a valid empty result, not a failure) — mapped leagues
    # never touch the old SoccerStats/ScraperAPI path below. Leagues not
    # in ANNABET_SERIE_ID (Brazil Serie C, Australia NPL states) fall
    # through, but those were dropped from LEAGUE_CODES for this reason.
    if code in ANNABET_SERIE_ID:
        try:
            d = date.today() if not date_str else None
            if d:
                return fetch_fixtures_annabet(code, d.day, d.month)
            else:
                # date_str was given explicitly (e.g. by blog.py) — parse
                # "D Mon" format (e.g. "9 Aug") back into day/month ints
                import calendar
                parts = date_str.strip().split()
                day_num = int(parts[0])
                month_num = list(calendar.month_abbr).index(parts[1][:3].title())
                return fetch_fixtures_annabet(code, day_num, month_num)
        except Exception as e:
            print(f"AnnaBet fixtures failed for {code}: {e}")
            return []

    matches = []
    seen = set()
    time_map = {}

    try:
        resp = fetch_protected(f"{BASE}/latest.asp?league={code}")
        soup = BeautifulSoup(resp.text, "html.parser")

        # Pass 0 - dedicated "upcoming matches" table near top of page.
        # Structure: "Sun 28 Jun 16:00" | "TeamA TeamB" | pmatch link
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                c0 = cells[0].get_text(" ", strip=True)
                c0_nodate = DAY_RE.sub("", c0).strip()
                if not c0_nodate.startswith(today1):
                    continue
                t = TIME_RE.search(c0)
                if not t:
                    continue
                time_str = f"{t.group(1)}:{t.group(2)}"

                links = [a.get_text(strip=True) for a in row.find_all("a")
                         if "team=" in (a.get("href") or "") or "teamstats.asp" in (a.get("href") or "")]
                if len(links) >= 2:
                    h, a_ = clean_team_name(links[0]), clean_team_name(links[1])
                else:
                    c1 = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""
                    parts = c1.split()
                    if len(parts) < 2:
                        continue
                    mid = len(parts) // 2
                    h = clean_team_name(" ".join(parts[:mid]))
                    a_ = clean_team_name(" ".join(parts[mid:]))

                if h and a_ and h != a_ and len(h) < 30 and len(a_) < 30:
                    time_map[_norm_key(h, a_)] = time_str
                    time_map[_norm_key(a_, h)] = time_str
                    key = _norm_key(h, a_)
                    if key not in seen:
                        seen.add(key)
                        matches.append({"time": time_str, "home": h, "away": a_})

        # Pass 1 - collect any remaining matches from form history table rows
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                c0 = cells[0].get_text(" ", strip=True)
                c0_clean = DAY_RE.sub("", c0).strip()
                if not (c0_clean == today1 or c0_clean.startswith(today1 + " ")):
                    continue
                c_last = cells[-1].get_text(strip=True)
                if c_last != "-":
                    continue
                c1 = cells[1].get_text(" ", strip=True)
                if " - " not in c1 or len(c1) > 50:
                    continue
                home_raw, away_raw = c1.split(" - ", 1)
                home = clean_team_name(home_raw)
                away = clean_team_name(away_raw)
                if not home or not away or home == away:
                    continue
                if len(home) > 25 or len(away) > 25:
                    continue
                key = _norm_key(home, away)
                if key in seen:
                    continue
                seen.add(key)
                t = TIME_RE.search(c0)
                time_str = f"{t.group(1)}:{t.group(2)}" if t else ""
                matches.append({"time": time_str, "home": home, "away": away})

        # Pass 2 - backfill times for any match still missing one
        for m in matches:
            if not m["time"]:
                key = _norm_key(m["home"], m["away"])
                if key in time_map:
                    m["time"] = time_map[key]
                else:
                    for (h, a), t in time_map.items():
                        if (m["home"] in h or h in m["home"]) and \
                           (m["away"] in a or a in m["away"]):
                            m["time"] = t
                            break

    except Exception as e:
        print(f"  Fixtures error: {e}")

    # Final safety-net dedup — belt-and-braces in case anything slipped
    # through the per-pass checks above with a normalized-key collision
    # that wasn't caught inline (e.g. ordering edge cases).
    final_seen = set()
    deduped = []
    for m in matches:
        key = _norm_key(m["home"], m["away"])
        if key in final_seen:
            continue
        final_seen.add(key)
        deduped.append(m)

    return deduped



def run_model(home, away, team_data):
    if home not in team_data or away not in team_data:
        return {"d70": "N/A", "b120": "N/A", "c120": "N/A", "b46": "N/A", "d64": "N/A", "b118": "N/A", "aa15": "N/A", "b54": "N/A", "odds": None, "ou25": None, "b119": "", "d119": "", "d70val": "", "o73": "", "o74": ""}

    data = sorted([
        (n, d["gp"], d["gf"], d["ga"], d["tot"],
         d["hgf"], d["hga"], d["htot"], d["agf"], d["aga"], d["atot"])
        for n, d in team_data.items()], key=lambda x: x[0])

    lhs = statistics.mean([d[5] for d in data]) or 1
    lhc = statistics.mean([d[6] for d in data]) or 1
    las = statistics.mean([d[8] for d in data]) or 1
    lac = statistics.mean([d[9] for d in data]) or 1

    wb = load_workbook(MODEL)
    ws = wb.active
    for row in ws.iter_rows(min_row=6, max_row=42, min_col=3, max_col=22):
        for cell in row:
            cell.value = None

    for i, d in enumerate(data):
        r = 6 + i
        hs, hc, ht = d[5], d[6], d[7]
        as_, ac, at_ = d[8], d[9], d[10]
        ws.cell(r, 3).value  = d[0]
        ws.cell(r, 4).value  = d[1]
        ws.cell(r, 5).value  = round(d[2], 4)
        ws.cell(r, 6).value  = round(d[3], 4)
        ws.cell(r, 7).value  = round(d[4], 4)
        ws.cell(r, 8).value  = "  "
        ws.cell(r, 9).value  = round(hs, 4)
        ws.cell(r, 10).value = round(hc, 4)
        ws.cell(r, 11).value = round(ht, 4)
        ws.cell(r, 12).value = "  "
        ws.cell(r, 13).value = round(as_, 4)
        ws.cell(r, 14).value = round(ac, 4)
        ws.cell(r, 15).value = round(at_, 4)
        ws.cell(r, 16).value = round(hs / lhs, 4)
        ws.cell(r, 17).value = round(hc / lhc, 4)
        ws.cell(r, 18).value = round(as_ / las, 4)
        ws.cell(r, 19).value = round(ac / lac, 4)
        ws.cell(r, 20).value = round(max((hs - as_) / d[1], 0), 4)
        ws.cell(r, 22).value = round((ht + at_) / 2, 4)

    ws["B69"] = home
    ws["C69"] = away
    ws.title  = "Sheet1"

    tmp_dir  = tempfile.mkdtemp()
    tmp_file = os.path.join(tmp_dir, "fm_tmp.xlsx")
    out_dir  = os.path.join(tmp_dir, "out")
    os.makedirs(out_dir)
    wb.save(tmp_file)

    subprocess.run(["libreoffice", "--headless", "--calc", "--convert-to", "xlsx",
                    "--outdir", out_dir, tmp_file],
                   capture_output=True, timeout=90)

    out_file = os.path.join(out_dir, "fm_tmp.xlsx")
    wb2 = load_workbook(out_file, data_only=True)
    ws2 = wb2.active

    d70  = str(ws2["D69"].value  or "")
    c120 = str(ws2["C120"].value or "")

    # B120 = TEXTJOIN of B119/C119/D119 ("double"/"under"/"run").
    # Always rebuild from the three source cells directly so we reliably
    # capture all 4 outcomes: empty, "double", "under", or "double /under".
    b119_raw = str(ws2["B119"].value or "")
    c119_raw = str(ws2["C119"].value or "")
    d119_raw = str(ws2["D119"].value or "")
    parts = [x for x in [b119_raw, c119_raw, d119_raw]
             if x and x not in ("run", "#NAME?", "#N/A", "None")]
    b120 = " /".join(parts)  # "" if none, "double" / "under" alone, or "double /under" combined

    # Helper: safely read a cell, returning "" on any error value
    def safe(ref, sheet=None):
        s = sheet if sheet else ws2
        v = str(s[ref].value or "")
        return "" if v in ("#NAME?", "#N/A", "#VALUE!", "None") else v

    # B118 = TEXTJOIN("/ ", L115, N111, O111) — always rebuild from source cells
    b118_parts = [x for x in [safe("L115"), safe("N111"), safe("O111")] if x]
    b118 = "/ ".join(b118_parts)

    # B46 = TEXTJOIN(", ", C114, IFERROR(O84,""), IFERROR(O85,"")) — rebuild from source
    b46_parts = [x for x in [safe("C114"), safe("O84"), safe("O85")] if x]
    b46 = ", ".join(b46_parts)

    d64 = safe("D64")

    sheet2 = wb2["Sheet2"]
    aa15 = safe("AA15", sheet2)

    # B54 = TEXTJOIN(T99, T100) — rebuild from source cells
    t99  = safe("T99")
    t100 = safe("T100")
    b54_parts = [x for x in [t99, t100] if x]
    b54 = "/ ".join(b54_parts)

    # Win/Draw/Away odds from the model's own computed expected goals (Sheet2!C5, D5)
    lambda_home = sheet2["C5"].value
    lambda_away = sheet2["D5"].value
    odds = calc_odds(lambda_home, lambda_away)
    ou25 = calc_over_under_25(lambda_home, lambda_away)  # NEW — model's own O/U 2.5

    # B119, D119 — show only when not "run"
    b119_raw = safe("B119")
    d119_raw = safe("D119")
    b119 = b119_raw if b119_raw not in ("run", "") else ""
    d119 = d119_raw if d119_raw not in ("run", "") else ""

    # D70
    d70_val = safe("D70")

    # Sheet2!O73 and O74
    o73 = safe("O73", sheet2)
    o74_raw = sheet2["O74"].value
    try:
        o74 = str(round(float(o74_raw), 1)) + "%" if o74_raw is not None else ""
    except:
        # o74_raw wasn't a number — likely an Excel error value like
        # #VALUE!, #N/A, #NAME? (e.g. formula couldn't compute for this
        # matchup). Blank it out instead of leaking the raw error string,
        # same treatment safe() gives every other cell.
        o74_str = str(o74_raw or "")
        o74 = "" if o74_str in ("#NAME?", "#N/A", "#VALUE!", "None") else o74_str

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"d70": d70, "b120": b120, "c120": c120, "b46": b46, "d64": d64,
            "b118": b118, "aa15": aa15, "b54": b54, "odds": odds, "ou25": ou25,
            "b119": b119, "d119": d119, "d70val": d70_val,
            "o73": o73, "o74": o74}


@app.get("/fixtures")
def fixtures_endpoint(league: str = Query(...), date: str = Query(None)):
    t0 = time.time()
    matches = fetch_fixtures(league, date)
    print(f"⏱ fetch_fixtures({league}) took {time.time() - t0:.1f}s")
    return {"league": league, "matches": matches}


@app.get("/predict")
async def predict(league: str = Query(...), home: str = Query(...), away: str = Query(...)):
    t0 = time.time()
    team_data = fetch_stats(league)
    t1 = time.time()
    print(f"⏱ fetch_stats({league}) took {t1 - t0:.1f}s")

    resolved_h = resolve_team(home, team_data)
    resolved_a = resolve_team(away, team_data)
    # If resolution failed, fall back to the raw name for display/model lookup —
    # run_model already returns clean "N/A" values when a name isn't in
    # team_data, so this doesn't risk silently using a wrong team anymore.
    h = resolved_h or home
    a = resolved_a or away

    with ThreadPoolExecutor(max_workers=5) as executor:
        f1 = executor.submit(run_model, h, a, team_data)
        f2 = executor.submit(run_model, a, h, team_data)
        r1, r2 = f1.result(), f2.result()
    t2 = time.time()
    print(f"⏱ run_model (both directions) took {t2 - t1:.1f}s")

    # NEW — fetch real bookmaker odds alongside the model's own implied odds.
    # Uses the RAW fixture team names (home/away as given), not the
    # SoccerStats-resolved names — market odds providers use their own team
    # naming and have nothing to do with whether SoccerStats recognized the
    # team, so a stats-resolution failure shouldn't block market odds too.
    # Only leagues in LEAGUE_TO_SPORT_KEY are covered by the primary provider;
    # everything else just gets market_odds: None, which the frontend
    # can treat the same way it already treats missing odds.
    market_odds = None
    market_ou25 = None  # NEW — market Over/Under 2.5 goals odds
    sport_key = LEAGUE_TO_SPORT_KEY.get(league)
    if sport_key:
        market_odds = await get_odds_for_card(sport_key, home, away)
        market_ou25 = await get_ou25_for_card(sport_key, home, away)

    # NEW — if the primary provider had nothing (unmapped league, or no
    # odds posted for this specific match), try Odds-API.io next, then
    # API-Football as a last resort (currently suspended, but kept in
    # case it gets reactivated). Each fails safe to None.
    if not market_odds:
        market_odds = await get_odds_api_io_fallback(home, away)
    if not market_odds:
        market_odds = await get_fallback_odds(home, away)

    # NEW — same fallback chain for O/U 2.5. API-Football's odds endpoint
    # doesn't return totals/O-U markets, so it's not part of this chain —
    # just the primary provider (already tried above) then Odds-API.io.
    if not market_ou25:
        market_ou25 = await get_ou25_api_io_fallback(home, away)

    # ── Value% and Decision — H/A/D decision logic ──────────────────────
    # value = (market_odds - model_odds) / model_odds * 100, per side.
    # Negative value = market quoting SHORTER odds than the model on that
    # side (market more confident than the model there).
    #
    # Decision rule (backtested across 165+ matches):
    #   Step 1 — share_diff signal: share_diff = home_share - away_share,
    #            where each share is that side's |value| as a % of
    #            (|home_v| + |draw_v| + |away_v|). Including draw_v dilutes
    #            outlier spikes rather than letting a single blown-up
    #            percentage dominate a two-way comparison.
    #            share_diff > 0 -> signal "Away"
    #            share_diff < 0 -> signal "Home"
    #   Step 2 — confirm the signal against the raw side value:
    #            signal "Away" AND away_v < 0  -> decision "Away"
    #            signal "Home" AND home_v < 0  -> decision "Home"
    #   Step 3 — mismatch: hand a 2-handicap to whichever raw value is
    #            negative (if both negative, the more negative one gets it,
    #            since that's the side the market is most confident about).
    value_pct = None
    value_signal = None
    model_odds = r1.get("odds")
    if model_odds and market_odds:
        def pct_diff(market_o, model_o):
            if not model_o or not market_o:
                return None
            return round(((market_o - model_o) / model_o) * 100, 1)

        home_v = pct_diff(market_odds.get("home_odds"), model_odds.get("home_odds"))
        draw_v = pct_diff(market_odds.get("draw_odds"), model_odds.get("draw_odds"))
        away_v = pct_diff(market_odds.get("away_odds"), model_odds.get("away_odds"))

        value_pct = {"home": home_v, "draw": draw_v, "away": away_v}

        decision = ""
        under_flag = ""
        if home_v is not None and away_v is not None:
            # Total — signed sum, kept for the under-flag call below (and
            # for display). NOT used for the H/A/D decision itself anymore.
            total_v = round(sum(x for x in [home_v, draw_v, away_v] if x is not None), 1)
            value_pct["total"] = total_v

            # Step 1 — share_diff signal
            abs_sum = abs(home_v) + abs(draw_v or 0) + abs(away_v)
            signal = ""
            if abs_sum > 0:
                home_share = abs(home_v) / abs_sum * 100
                away_share = abs(away_v) / abs_sum * 100
                share_diff = home_share - away_share
                value_pct["share_diff"] = round(share_diff, 1)
                if share_diff > 0:
                    signal = "Away"
                elif share_diff < 0:
                    signal = "Home"
                # share_diff == 0 -> no signal

            # Step 2 — confirm signal against raw value
            if signal == "Away" and away_v < 0:
                decision = "Away"
            elif signal == "Home" and home_v < 0:
                decision = "Home"
            else:
                # Step 3 — mismatch -> 2-handicap to whichever raw value is
                # negative (more negative one wins if both are negative)
                if home_v < 0 and away_v < 0:
                    decision = "Home 2-handicap" if home_v < away_v else "Away 2-handicap"
                elif home_v < 0:
                    decision = "Home 2-handicap"
                elif away_v < 0:
                    decision = "Away 2-handicap"
                # else neither negative -> decision stays "" (no confident call)

            # Under flag — total_v falls in the -30 to 0 range, EXCEPT when
            # home_v and away_v are the same sign (both negative or both
            # positive), in which case it's suppressed.
            same_sign = (home_v < 0 and away_v < 0) or (home_v > 0 and away_v > 0)
            under_flag = "" if same_sign else ("under" if -30 <= total_v <= 0 else "")

        value_signal = {"decision": decision, "under": under_flag}

    # ── Over/Under 2.5 goals — value% (UNCHANGED, still uses the original
    # share/share_diff formula for O/U, separate from the H/A/D logic above) ──
    ou25_value_pct = None
    ou25_value_signal = None
    model_ou25 = r1.get("ou25")
    if model_ou25 and market_ou25:
        def pct_diff_ou(market_o, model_o):
            if not model_o or not market_o:
                return None
            return round(((market_o - model_o) / model_o) * 100, 1)

        over_v = pct_diff_ou(market_ou25.get("over_odds"), model_ou25.get("over_odds"))
        under_v = pct_diff_ou(market_ou25.get("under_odds"), model_ou25.get("under_odds"))

        if over_v is not None or under_v is not None:
            ou_total_v = round(sum(x for x in [over_v, under_v] if x is not None), 1)

            ou_abs_sum = abs(over_v or 0) + abs(under_v or 0)

            def ou_share(v):
                if v is None or ou_abs_sum == 0:
                    return None
                return round(abs(v) / ou_abs_sum * 100, 1)

            over_share_v = ou_share(over_v)
            under_share_v = ou_share(under_v)

            ou25_value_pct = {
                "over": over_v, "under": under_v, "total": ou_total_v,
                "over_share": over_share_v, "under_share": under_share_v,
            }

            ou_share_diff = None
            if over_share_v is not None and under_share_v is not None:
                ou_share_diff = round(over_share_v - under_share_v, 1)
                ou25_value_pct["share_diff"] = ou_share_diff

            ou_result_signal = ""
            if ou_share_diff is not None and ou_share_diff != 0:
                ou_result_signal = "Under" if ou_share_diff > 0 else "Over"

            ou25_value_signal = {"result": ou_result_signal}

    return {
        "home": h, "away": a,
        "d70": r1["d70"], "b120": r1["b120"], "c120": r1["c120"],
        "b46": r1["b46"], "d64": r1["d64"], "b118": r1["b118"], "aa15": r1["aa15"], "b54": r1["b54"],
        "odds": r1.get("odds"),
        "ou25": r1.get("ou25"),  # model's own Over/Under 2.5 goals odds
        "market_ou25": market_ou25,  # market Over/Under 2.5 goals odds (primary provider only for now)
        "ou25_value_pct": ou25_value_pct,  # O/U 2.5 value% (unchanged formula)
        "ou25_value_signal": ou25_value_signal,  # Over/Under decision from share_diff
        "market_odds": market_odds,
        "value_pct": value_pct,  # signed % diff between market and model odds, plus share_diff
        "value_signal": value_signal,  # H/A/D decision (Home / Away / Home 2-handicap / Away 2-handicap)
        "b119": r1["b119"], "d119": r1["d119"], "d70val": r1["d70val"],
        "o73": r1["o73"], "o74": r1["o74"],
        "d70r": r2["d70"], "b120r": r2["b120"], "c120r": r2["c120"],
        "b46r": r2["b46"], "d64r": r2["d64"], "b118r": r2["b118"], "aa15r": r2["aa15"], "b54r": r2["b54"],
        "oddsr": r2.get("odds"),
        "b119r": r2["b119"], "d119r": r2["d119"], "d70valr": r2["d70val"],
        "o73r": r2["o73"], "o74r": r2["o74"],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/league_gp")
def league_gp(league: str = Query(...)):
    """Lightweight check — just fetches team stats for one league and
    reports the max games-played across all teams, plus team count.
    Used to identify which leagues have enough season data (e.g. >=10 GP)
    to be worth keeping in LEAGUE_CODES. Deliberately does NOT also fetch
    fixtures or run homeaway/latest twice like /debug does — one scrape
    per league keeps a full-league-list batch check as cheap as possible.
    """
    team_data = fetch_stats(league)
    if not team_data:
        return {"league": league, "team_count": 0, "max_gp": 0}
    max_gp = max(d.get("gp", 0) for d in team_data.values())
    return {"league": league, "team_count": len(team_data), "max_gp": max_gp}


# NEW — one-off diagnostic for the AnnaBet "Upcoming Games" tab question.
# The global /upcoming/ page (fetch_all_upcoming_annabet above) only has a
# short rolling lookahead window, which was confirmed to miss late-kickoff
# leagues like MLS (evening US kickoffs = late UTC, past where /upcoming/
# currently cuts off). Each league's own serie_ID page has an "Upcoming
# Games" tab showing several days ahead — but AnnaBet renders tabs via
# jQuery UI, so it's unknown whether that tab's HTML is already present in
# the raw page (just hidden by CSS/JS, like the Results tab clearly is)
# or loaded separately via AJAX after the page renders (which plain
# requests can't see).
#
# This endpoint fetches one league's serie_ID page directly and reports
# whether the "Upcoming Games" tab content (div id="tabs-5") is present in
# the raw HTML. tabs_5_present == True means we can add a BeautifulSoup
# parser for that div and use it as a proper multi-day fixtures source —
# tabs_5_present == False means it's AJAX-loaded and needs a different
# approach (finding AnnaBet's internal API endpoint via browser dev tools).
@app.get("/debug_upcoming_tab")
def debug_upcoming_tab(league: str = Query(...)):
    if league not in ANNABET_SERIE_ID:
        return {"error": f"'{league}' has no AnnaBet serie_ID mapping"}

    serie_id = ANNABET_SERIE_ID[league]
    # URL slug after the ID doesn't need to match exactly — AnnaBet
    # resolves the page from the numeric ID alone — so a generic "_x"
    # slug works the same as the full name slug for this check.
    url = f"https://annabet.com/en/soccerstats/serie_{serie_id}_x.html"

    try:
        resp = ANNABET_SESSION.get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        return {"error": str(e), "url": url}

    html = resp.text
    tabs_5_present = 'id="tabs-5"' in html or "id='tabs-5'" in html
    gamereport_count = html.count("gamereport")

    result = {
        "url": url,
        "status_code": resp.status_code,
        "page_length": len(html),
        "tabs_5_present": tabs_5_present,
        "gamereport_link_count": gamereport_count,  # sanity check — Results
                                                      # tab links use this
                                                      # pattern, so a high
                                                      # count here confirms
                                                      # at least Results
                                                      # loaded fully
    }

    # If the tab IS present, also pull out a snippet of that div so we can
    # see its actual structure (table layout, date format, etc.) without
    # needing a second round trip.
    if tabs_5_present:
        soup = BeautifulSoup(html, "html.parser")
        tab_div = soup.find(id="tabs-5")
        if tab_div:
            snippet = tab_div.get_text(" ", strip=True)[:1000]
            result["tabs_5_text_snippet"] = snippet

    return result


@app.get("/debug")
def debug(league: str = Query(...), date: str = Query(None)):
    debug_info = {}
    try:
        resp = fetch_protected(f"{BASE}/homeaway.asp?league={league}")
        debug_info["homeaway_status"] = resp.status_code
        debug_info["homeaway_length"] = len(resp.text)
        debug_info["homeaway_snippet"] = resp.text[:500]
    except Exception as e:
        debug_info["homeaway_error"] = str(e)

    try:
        resp2 = fetch_protected(f"{BASE}/latest.asp?league={league}")
        debug_info["latest_status"] = resp2.status_code
        debug_info["latest_length"] = len(resp2.text)
    except Exception as e:
        debug_info["latest_error"] = str(e)

    team_data = fetch_stats(league)
    fixtures  = fetch_fixtures(league, date)
    resolved  = [{"home": resolve_team(f["home"], team_data),
                   "away": resolve_team(f["away"], team_data),
                   "raw_home": f["home"], "raw_away": f["away"]} for f in fixtures]
    return {
        "debug_info": debug_info,
        "team_count": len(team_data),
        "team_names": list(team_data.keys()),
        "fixtures": fixtures,
        "resolved": resolved
    }
