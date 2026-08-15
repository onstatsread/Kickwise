"""
Kickwise Backend — FastAPI server
Deploy to Render.com (free tier)
"""
import os
import re
import shutil
import tempfile
import difflib
import subprocess
import statistics
import requests
from datetime import date
from bs4 import BeautifulSoup
from scipy.stats import poisson
from openpyxl import load_workbook
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor

# External framework linkages (Make sure odds.py, api_football.py, odds_api_io.py exist in folder)
try:
    from odds import get_odds_for_card, get_ou25_for_card, router as odds_router
    from api_football import get_fallback_odds
    from odds_api_io import get_odds_api_io_fallback, get_ou25_api_io_fallback
except ImportError:
    from fastapi import APIRouter
    odds_router = APIRouter()

app = FastAPI(title="Kickwise API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(odds_router)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://soccerstats.com",
    "Connection": "keep-alive",
}
BASE = "https://soccerstats.com"
MODEL = "A_mix2.xlsx"

LEAGUE_TO_SPORT_KEY = {
    "argentina":   "soccer_argentina_primera_division",
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
    "mexico":      "soccer_mexico_ligamx",
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
}

# --- POISSON & ODDS MATRICES ---
def calc_win_draw_away(lambda_home, lambda_away, max_goals=10):
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
        return {"home_pct": None, "draw_pct": None, "away_pct": None, "home_odds": None, "draw_odds": None, "away_odds": None}
    return {
        "home_pct": wda["home_pct"], 
        "draw_pct": wda["draw_pct"], 
        "away_pct": wda["away_pct"], 
        "home_odds": pct_to_odds(wda["home_pct"]), 
        "draw_odds": pct_to_odds(wda["draw_pct"]), # FIXED: changed from draw_odds lookup to draw_pct
        "away_odds": pct_to_odds(wda["away_pct"]), 
    }

def calc_over_under_25(lambda_home, lambda_away, max_goals=10):
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
        "over_pct": over_pct, "under_pct": under_pct, "over_odds": pct_to_odds(over_pct), "under_odds": pct_to_odds(under_pct),
    }

def resolve_team(name, team_data):
    if name in team_data:
        return name
    substring_candidates = [k for k in team_data if len(name) >= 5 and (name in k or k in name)]
    if len(substring_candidates) == 1:
        return substring_candidates[0]
    close = difflib.get_close_matches(name, team_data.keys(), n=1, cutoff=0.8) # FIXED: n=1 ensures singular absolute match string return
    if len(close) == 1:
        return close[0]
    return None

# --- FIXED SOCCERSTATS RAW STATISTICS SCAPER ---
def fetch_stats(code):
    """Parses homeaway.asp into an uncalculated raw layout matching Excel templates."""
    teams = {}
    try:
        url = f"{BASE}/homeaway.asp?league={code}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        section_count = 0
        
        for tbl in tables:
            valid_rows = []
            for row in tbl.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 8:
                    continue
                team = cells[1].get_text(strip=True)
                if not team or "table" in team.lower() or "advertisement" in team.lower():
                    continue
                try:
                    # Clean position prefixes (e.g. '1. Arsenal' -> 'Arsenal')
                    team = re.sub(r'^\d+\.\s*', '', team)
                    gp = float(cells[2].get_text(strip=True))
                    gf = float(cells[6].get_text(strip=True))
                    ga = float(cells[7].get_text(strip=True))
                except:
                    continue
                if gp <= 0:
                    continue
                valid_rows.append((team, gp, gf, ga))
            
            if len(valid_rows) >= 4: # Standard leagues have at least 4+ teams per section block
                section_count += 1
                for team, gp, gf, ga in valid_rows:
                    if team not in teams:
                        teams[team] = {}
                    if section_count == 1:
                        teams[team]["hgp"] = gp
                        teams[team]["hgf"] = gf
                        teams[team]["hga"] = ga
                    elif section_count == 2:
                        teams[team]["agp"] = gp
                        teams[team]["agf"] = gf
                        teams[team]["aga"] = ga
                if section_count >= 2:
                    break
    except Exception as e:
        print(f"Stats compilation error: {e}")
        
    # Standardize baseline structures to feed into data parsing iterations seamlessly
    result = {}
    for team, d in teams.items():
        if "hgp" not in d or "agp" not in d:
            continue
        result[team] = {
            "gp": d["hgp"] + d["agp"],
            "hgp": d["hgp"], "hgf": d["hgf"], "hga": d["hga"],
            "agp": d["agp"], "agf": d["agf"], "aga": d["aga"]
        }
    return result

TIME_RE = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')
DAY_RE  = re.compile(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b\s*')

def clean_team_name(name):
    return DAY_RE.sub("", name).strip()

def _norm_key(a, b):
    def clean(s): return " ".join(s.lower().split())
    return (clean(a), clean(b))

def fetch_fixtures(code, date_str=None):
    if date_str:
        today1 = date_str.strip()
    else:
        d = date.today()
        today1 = f"{d.day} {d.strftime('%b')}"
    matches = []
    seen = set()
    time_map = {}
    try:
        resp = requests.get(f"{BASE}/latest.asp?league={code}", headers=HEADERS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
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
