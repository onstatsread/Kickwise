"""
Kickwise Backend — FastAPI server
Primary market-odds source: AnnaBet
Fallback market-odds sources are kept available but AnnaBet is tried first.

Deploy to Render.com.
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

import requests
import os
import subprocess
import statistics
import tempfile
import shutil
import difflib
import re
import time
import calendar
import hashlib

from scipy.stats import poisson
from datetime import date
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from concurrent.futures import ThreadPoolExecutor


# ============================================================
# EXISTING ODDS FALLBACKS
# ============================================================

from odds import (
    get_odds_for_card,
    get_ou25_for_card,
    router as odds_router
)

from api_football import get_fallback_odds

from odds_api_io import (
    get_odds_api_io_fallback,
    get_ou25_api_io_fallback
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="Kickwise API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(odds_router)


# ============================================================
# CONSTANTS / SESSIONS
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.soccerstats.com/",
    "Connection": "keep-alive",
}

BASE = "https://www.soccerstats.com"
MODEL = "A_mix2.xlsx"


# ============================================================
# ANNABET SESSION
# ============================================================

ANNABET_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": HEADERS["Accept"],
    "Accept-Language": HEADERS["Accept-Language"],
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Referer": "https://annabet.com/en/soccerstats/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

ANNABET_SESSION = requests.Session()
ANNABET_SESSION.headers.update(ANNABET_HEADERS)


# ============================================================
# ANNABET AUTHENTICATION
# ============================================================

ANNABET_LOGIN_URL = (
    "https://annabet.com/auth/ASEngine/ASAjax.php"
)

ANNABET_USERNAME = os.environ.get(
    "ANNABET_USERNAME",
    ""
)

ANNABET_PASSWORD = os.environ.get(
    "ANNABET_PASSWORD",
    ""
)

_ANNABET_LOGGED_IN = False


def annabet_login():
    """
    Logs ANNABET_SESSION into AnnaBet.

    AnnaBet expects a SHA-512 password hash rather than the
    plain-text password.
    """

    global _ANNABET_LOGGED_IN

    if not ANNABET_USERNAME or not ANNABET_PASSWORD:

        print(
            "AnnaBet login skipped — "
            "ANNABET_USERNAME/ANNABET_PASSWORD not set"
        )

        return False

    password_hash = hashlib.sha512(
        ANNABET_PASSWORD.encode("utf-8")
    ).hexdigest()

    try:

        resp = ANNABET_SESSION.post(
            ANNABET_LOGIN_URL,
            data={
                "action": "checkLogin",
                "username": ANNABET_USERNAME,
                "password": password_hash,
            },
            timeout=20,
        )

        resp.raise_for_status()

        result = resp.json()

        if result.get("page"):

            _ANNABET_LOGGED_IN = True

            print(
                "AnnaBet login succeeded"
            )

            return True

        print(
            "AnnaBet login failed — "
            f"unexpected response: {result}"
        )

        return False

    except Exception as e:

        print(
            f"AnnaBet login failed: {e}"
        )

        return False


# ============================================================
# LOGIN ON STARTUP
# ============================================================

annabet_login()


# ============================================================
# ANNABET CONSTANTS
# ============================================================

ANNABET_TABLE_HEADER = [
    "#",
    "Team",
    "GP",
    "W",
    "T",
    "L",
    "GF",
    "GA",
    "Diff",
    "Pts",
    "Pts/G",
    "W%",
    "ØGF",
    "ØGA",
]


ANNABET_SERIE_ID = {
    "belarus": 232,
    "brazil": 217,
    "brazil2": 259,
    "canada": 750,
    "chile": 301,
    "china": 248,
    "china2": 731,
    "colombia": 329,
    "ecuador": 313,
    "estonia": 242,
    "faroeislands": 368,
    "finland": 7,
    "finland2": 35,
    "georgia": 235,
    "iceland": 114,
    "iceland2": 392,
    "ireland": 42,
    "ireland2": 163,
    "kazakhstan": 328,
    "latvia": 223,
    "lithuania": 226,
    "malaysia": 521,
    "norway": 36,
    "norway2": 173,
    "paraguay": 347,
    "peru": 321,
    "southkorea": 249,
    "southkorea2": 543,
    "sweden": 32,
    "sweden2": 33,
    "uruguay": 439,
    "usa": 43,
    "usa2": 362,
    "venezuela": 314,
}


_ANNABET_ID_TO_CODE = {
    v: k
    for k, v in ANNABET_SERIE_ID.items()
}


ANNABET_UPCOMING_URL = (
    "https://annabet.com/en/soccerstats/upcoming/"
)


# ============================================================
# CACHES
# ============================================================

_ANNABET_FIXTURES_CACHE = {}
ANNABET_FIXTURES_CACHE_TTL = 1800

_ANNABET_ODDS_CACHE = {}
ANNABET_ODDS_CACHE_TTL = 300

_STATS_CACHE = {}
STATS_CACHE_TTL = 3600


# ============================================================
# REGEX
# ============================================================

_ANNABET_SERIE_LINK_RE = re.compile(
    r"/serie_(\d+)_"
)

_ANNABET_DATETIME_RE = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.\s+"
    r"(\d{1,2}):(\d{2})"
)

TIME_RE = re.compile(
    r"\b([01]?\d|2[0-3]):([0-5]\d)\b"
)

DAY_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b\s*"
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def _absolute_annabet_url(href):
    if not href:
        return None

    if href.startswith("http://"):
        return href

    if href.startswith("https://"):
        return href

    if href.startswith("/"):
        return "https://annabet.com" + href

    return (
        "https://annabet.com/en/soccerstats/"
        + href
    )


def _norm_team(name):
    return " ".join(
        str(name).lower().split()
    ).strip()


def _team_names_match(a, b):

    a = _norm_team(a)
    b = _norm_team(b)

    return (
        a == b
        or (
            len(a) >= 5
            and a in b
        )
        or (
            len(b) >= 5
            and b in a
        )
    )


def clean_team_name(name):
    return DAY_RE.sub(
        "",
        name
    ).strip()


def _norm_key(a, b):

    def clean(s):
        return " ".join(
            s.lower().split()
        )

    return clean(a), clean(b)


# ============================================================
# ANNABET FIXTURES
# ============================================================

def fetch_all_upcoming_annabet():

    cached = _ANNABET_FIXTURES_CACHE.get(
        "_all"
    )

    if (
        cached
        and time.time() - cached[0]
        < ANNABET_FIXTURES_CACHE_TTL
    ):
        return cached[1]

    resp = ANNABET_SESSION.get(
        ANNABET_UPCOMING_URL,
        timeout=30
    )

    resp.raise_for_status()

    soup = BeautifulSoup(
        resp.text,
        "html.parser"
    )

    by_league = {}

    for row in soup.find_all("tr"):

        cells = row.find_all("td")

        if len(cells) < 3:
            continue

        row_text = cells[0].get_text(
            " ",
            strip=True
        )

        dt_match = _ANNABET_DATETIME_RE.search(
            row_text
        )

        if not dt_match:
            continue

        league_link = None

        for cell in cells:

            a = cell.find(
                "a",
                href=_ANNABET_SERIE_LINK_RE
            )

            if a:
                league_link = a
                break

        if not league_link:
            continue

        serie_match = _ANNABET_SERIE_LINK_RE.search(
            league_link["href"]
        )

        if not serie_match:
            continue

        serie_id = int(
            serie_match.group(1)
        )

        code = _ANNABET_ID_TO_CODE.get(
            serie_id
        )

        if not code:
            continue

        team_link = None

        for cell in cells:

            a = cell.find(
                "a",
                href=re.compile(r"h2h\.php")
            )

            if a:
                team_link = a
                break

        if not team_link:
            continue

        team_text = team_link.get_text(
            " ",
            strip=True
        )

        if " - " not in team_text:
            continue

        home, away = team_text.split(
            " - ",
            1
        )

        day, month, hour, minute = (
            dt_match.groups()
        )

        by_league.setdefault(
            code,
            []
        ).append({
            "date": f"{day}.{month}.",
            "time": f"{hour}:{minute}",
            "home": home.strip(),
            "away": away.strip(),
            "h2h_url": _absolute_annabet_url(
                team_link.get("href")
            ),
        })

    _ANNABET_FIXTURES_CACHE["_all"] = (
        time.time(),
        by_league
    )

    return by_league


def fetch_fixtures_annabet(
    code,
    day,
    month
):

    by_league = fetch_all_upcoming_annabet()

    target = f"{day}.{month}."

    return [
        {
            "time": m["time"],
            "home": m["home"],
            "away": m["away"],
            "h2h_url": m.get("h2h_url"),
        }
        for m in by_league.get(
            code,
            []
        )
        if m["date"] == target
    ]


# ============================================================
# ANNABET TEAM STATISTICS
# ============================================================

def _annabet_get_header(table):

    rows = table.find_all("tr")

    if not rows:
        return None

    return [
        c.get_text(strip=True)
        for c in rows[0].find_all(
            ["td", "th"]
        )
    ]


def _annabet_parse_table(table):

    teams = {}

    rows = table.find_all("tr")

    for row in rows[1:]:

        cells = [
            c.get_text(strip=True)
            for c in row.find_all("td")
        ]

        if len(cells) < 8:
            continue

        try:

            name = cells[1]
            gp = int(cells[2])
            gf = int(cells[6])
            ga = int(cells[7])

            teams[name] = {
                "gp": gp,
                "gf": gf,
                "ga": ga,
            }

        except (
            ValueError,
            IndexError
        ):
            continue

    return teams


def fetch_stats_annabet(serie_id):

    url = (
        "https://annabet.com/en/soccerstats/"
        f"serie_{serie_id}_x.html"
    )

    resp = ANNABET_SESSION.get(
        url,
        timeout=20
    )

    resp.raise_for_status()

    soup = BeautifulSoup(
        resp.text,
        "html.parser"
    )

    tables = soup.find_all("table")

    matching = [
        t
        for t in tables
        if _annabet_get_header(t)
        == ANNABET_TABLE_HEADER
    ]

    if len(matching) < 3:
        return {}

    all_table = matching[0]
    home_table = matching[1]
    away_table = matching[2]

    home_data = _annabet_parse_table(
        home_table
    )

    away_data = _annabet_parse_table(
        away_table
    )

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

            "gf": (
                gf / gp
                if gp
                else 0
            ),

            "ga": (
                ga / gp
                if gp
                else 0
            ),

            "tot": (
                (gf + ga) / gp
                if gp
                else 0
            ),

            "hgf": (
                h["gf"] / h["gp"]
                if h["gp"]
                else 0
            ),

            "hga": (
                h["ga"] / h["gp"]
                if h["gp"]
                else 0
            ),

            "htot": (
                (h["gf"] + h["ga"])
                / h["gp"]
                if h["gp"]
                else 0
            ),

            "agf": (
                a["gf"] / a["gp"]
                if a["gp"]
                else 0
            ),

            "aga": (
                a["ga"] / a["gp"]
                if a["gp"]
                else 0
            ),

            "atot": (
                (a["gf"] + a["ga"])
                / a["gp"]
                if a["gp"]
                else 0
            ),
        }

    return result


def fetch_stats(code):

    cached = _STATS_CACHE.get(code)

    if (
        cached
        and time.time() - cached[0]
        < STATS_CACHE_TTL
    ):
        return cached[1]

    if code in ANNABET_SERIE_ID:

        try:

            result = fetch_stats_annabet(
                ANNABET_SERIE_ID[code]
            )

            if result:

                _STATS_CACHE[code] = (
                    time.time(),
                    result
                )

                return result

        except Exception as e:

            print(
                f"AnnaBet stats failed "
                f"for {code}: {e}"
            )

    return {}


# ============================================================
# ANNABET ODDS HELPERS
# ============================================================

def _annabet_float(value):

    if value is None:
        return None

    s = str(value).strip()

    if not re.fullmatch(
        r"\d+(?:\.\d+)?",
        s
    ):
        return None

    try:
        value = float(s)

    except ValueError:
        return None

    if value <= 1.0 or value > 100:
        return None

    return value


def _add_implied_pct(
    odds_dict,
    *keys
):

    if not odds_dict:
        return odds_dict

    vals = [
        odds_dict.get(k)
        for k in keys
    ]

    if any(
        v is None or v <= 0
        for v in vals
    ):
        return odds_dict

    raw = [
        1 / v
        for v in vals
    ]

    total = sum(raw)

    if total <= 0:
        return odds_dict

    for k, r in zip(
        keys,
        raw
    ):

        pct_key = k.replace(
            "_odds",
            "_pct"
        )

        odds_dict[pct_key] = round(
            r / total * 100,
            1
        )

    return odds_dict


# ============================================================
# CURRENT H/D/A EXTRACTOR
#
# IMPORTANT:
# This remains here for diagnostic purposes.
# The debug endpoint below will show us whether this method
# is responsible for the repeated odds.
# ============================================================

def _extract_fixture_h2h_and_hda(
    home,
    away
):

    cached = _ANNABET_ODDS_CACHE.get(
        (
            "fixture",
            _norm_team(home),
            _norm_team(away)
        )
    )

    if (
        cached
        and time.time() - cached[0]
        < ANNABET_ODDS_CACHE_TTL
    ):
        return cached[1]

    try:

        resp = ANNABET_SESSION.get(
            ANNABET_UPCOMING_URL,
            timeout=30
        )

        resp.raise_for_status()

    except Exception as e:

        print(
            "AnnaBet upcoming odds fetch failed: "
            f"{e}"
        )

        return None

    soup = BeautifulSoup(
        resp.text,
        "html.parser"
    )

    for row in soup.find_all("tr"):

        row_text = row.get_text(
            " ",
            strip=True
        )

        normalized = _norm_team(
            row_text
        )

        if _norm_team(home) not in normalized:
            continue

        if _norm_team(away) not in normalized:
            continue

        h2h_url = None

        for a in row.find_all(
            "a",
            href=True
        ):

            if "h2h.php" in a["href"]:

                h2h_url = _absolute_annabet_url(
                    a["href"]
                )

                break

        numbers = []

        for cell in row.find_all("td"):

            text = cell.get_text(
                " ",
                strip=True
            )

            for raw in re.findall(
                r"\b\d+(?:\.\d+)?\b",
                text
            ):

                n = _annabet_float(raw)

                if n is not None:
                    numbers.append(n)

        if len(numbers) < 3:
            continue

        h, d, a = numbers[-3:]

        result = {
            "home_odds": h,
            "draw_odds": d,
            "away_odds": a,
            "h2h_url": h2h_url,
        }

        _ANNABET_ODDS_CACHE[
            (
                "fixture",
                _norm_team(home),
                _norm_team(away)
            )
        ] = (
            time.time(),
            result
        )

        return result

    return None


# ============================================================
# CURRENT O/U 2.5 EXTRACTOR
#
# IMPORTANT:
# This is also retained unchanged for the diagnostic stage.
# ============================================================

def _extract_annabet_ou25(
    h2h_url
):

    if not h2h_url:
        return None

    cache_key = (
        "ou25",
        h2h_url
    )

    cached = _ANNABET_ODDS_CACHE.get(
        cache_key
    )

    if (
        cached
        and time.time() - cached[0]
        < ANNABET_ODDS_CACHE_TTL
    ):
        return cached[1]

    try:

        resp = ANNABET_SESSION.get(
            h2h_url,
            timeout=30
        )

        resp.raise_for_status()

    except Exception as e:

        print(
            f"AnnaBet H2H O/U fetch failed: {e}"
        )

        return None

    soup = BeautifulSoup(
        resp.text,
        "html.parser"
    )

    candidate_tables = []

    for table in soup.find_all("table"):

        text = table.get_text(
            " ",
            strip=True
        ).lower()

        if "total goals under-over" not in text:
            continue

        if table.find(
            "a",
            href=re.compile(
                r"^/en/link/"
            )
        ):

            candidate_tables.append(
                table
            )

    for table in candidate_tables:

        for cell in table.find_all(
            "td",
            class_="hdr"
        ):

            cell_text = cell.get_text(
                " ",
                strip=True
            )

            if not re.search(
                r"\b2\.5\s+goals\b",
                cell_text,
                re.I
            ):
                continue

            pairs = re.findall(
                r"(\d+(?:\.\d+)?)\s*-\s*"
                r"(\d+(?:\.\d+)?)",
                cell_text
            )

            for left, right in pairs:

                under = _annabet_float(
                    left
                )

                over
