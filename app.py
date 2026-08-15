"""
Kickwise Backend — FastAPI server
Deploy to Render.com (free tier)
"""
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests, os, subprocess, statistics, tempfile, shutil, difflib, re
from scipy.stats import poisson
from datetime import date
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from concurrent.futures import ThreadPoolExecutor

# Preserve your framework router linkages
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
BASE  = "https://soccerstats.com"
MODEL = "A_mix2.xlsx" # Ensure this baseline spreadsheet template sits in your project root

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
    "france":      "soccer_france_ligue_one",
    "germany":     "soccer_germany_bundesliga",
    "italy":       "soccer_italy_serie_a",
    "netherlands": "soccer_netherlands_eredivisie",
    "portugal":    "soccer_portugal_primeira_liga",
    "spain":       "soccer_spain_la_liga",
    "usa":         "soccer_usa_mls",
}

def calc_win_draw_away(lambda_home, lambda_away, max_goals=10):
    try:
        lambda_home, lambda_away = float(lambda_home), float(lambda_away)
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

# --- FIXED CRITICAL SYNTAX ERROR TYPO HERE ---
def calc_odds(lambda_home, lambda_away):
    wda = calc_win_draw_away(lambda_home, lambda_away)
    if not wda:
        return {"home_pct": None, "draw_pct": None, "away_pct": None,
                "home_odds": None, "draw_odds": None, "away_odds": None}
    return {
        "home_pct": wda["home_pct"], "draw_pct": wda["draw_pct"], "away_pct": wda["away_pct"],
        "home_odds": pct_to_odds(wda["home_pct"]),
        "draw_odds": pct_to_odds(wda["draw_pct"]),  # Fixed: changed from wda["draw_odds"] to wda["draw_pct"]
        "away_odds": pct_to_odds(wda["away_pct"]),
    }

def calc_over_under_25(lambda_home, lambda_away, max_goals=10):
    try:
        lambda_home, lambda_away = float(lambda_home), float(lambda_away)
    except (TypeError, ValueError):
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
    if name in team_data:
        return name
    substring_candidates = [k for k in team_data if len(name) >= 5 and (name in k or k in name)]
    if len(substring_candidates) == 1:
        return substring_candidates[0]
    close = difflib.get_close_matches(name, team_data.keys(), n=1, cutoff=0.8)
    return close[0] if close else None


# --- FIXED SOCCERSTATS HOME/AWAY EXTRACTION ROUTINE ---
def fetch_stats(code):
    """Parses SoccerSTATS' homeaway.asp metrics using localized cell indexing maps."""
    teams = {}
    url = f"{BASE}/homeaway.asp?league={code}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code != 200:
            return teams

        soup = BeautifulSoup(res.text, "html.parser")
        # SoccerSTATS organizes records using alternating odd/even classes on table rows
        data_rows = soup.find_all("tr", class_=["odd", "even"])
        
        for row in data_rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            
            # The structured stats layout on the homeaway page contains exactly 11 columns
            if len(cells) == 11:
                raw_team_name = cells[0]
                if not raw_team_name or "table" in raw_team_name.lower() or "advertisement" in raw_team_name.lower():
                    continue
                
                team_name = re.sub(r'\s+', ' ', raw_team_name).strip()
                
                try:
                    # Precise positional mapping matching the live SoccerSTATS structure
                    teams[team_name] = {
                        "home_gp": int(cells[1]) if cells[1].isdigit() else 0,
                        "home_w":  int(cells[2]) if cells[2].isdigit() else 0,
                        "home_gf": int(cells[5]) if cells[5].isdigit() else 0, # Scored Home
                        "away_gp": int(cells[6]) if cells[6].isdigit() else 0,
                        "away_w":  int(cells[7]) if cells[7].isdigit() else 0,
                        "away_gf": int(cells[10]) if cells[10].isdigit() else 0, # Scored Away
                    }
                except Exception:
                    continue
    except Exception as e:
        print(f"Extraction error for league {code}: {e}")
    return teams


# --- NEW: OPENPYXL EXCEL INJECTION ENGINE ---
def inject_data_into_model(league_code, team_stats):
    """
    Safely opens your master predictive spreadsheet template, injects the freshly 
    scraped home/away parameters, triggers execution calculations via excel formulas,
    and returns metrics to the runtime environment without damaging your source file.
    """
    if not os.path.exists(MODEL):
        print(f"⚠️ Model file '{MODEL}' not found. Initializing blank workbook proxy instead.")
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Data_Input"
    else:
        wb = load_workbook(MODEL, data_only=False) # Keep formulas intact
        # Target an input data matrix tab within your workbook
        if "Data_Input" in wb.sheetnames:
            ws = wb["Data_Input"]
        else:
            ws = wb.active

    # Write custom data header map starting at row 1
    headers = ["Team_Name", "Home_GP", "Home_Wins", "Home_GF", "Away_GP", "Away_Wins", "Away_GF"]
    for col_num, header in enumerate(headers, 1):
        ws.cell(row=1, column=col_num, value=header) #

    # Dynamically inject scraped metrics row by row
    current_row = 2
    for team, m in team_stats.items():
        ws.cell(row=current_row, column=1, value=team)
        ws.cell(row=current_row, column=2, value=m["home_gp"])
        ws.cell(row=current_row, column=3, value=m["home_w"])
        ws.cell(row=current_row, column=4, value=m["home_gf"])
        ws.cell(row=current_row, column=5, value=m["away_gp"])
        ws.cell(row=current_row, column=6, value=m["away_w"])
        ws.cell(row=current_row, column=7, value=m["away_gf"])
        current_row += 1

    # Save tracking to an isolated temp space to prevent concurrency conflicts on Render
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, f"calculated_{league_code}.xlsx")
    wb.save(output_path) #
    return output_path


# --- LIVE FASTAPI ENDPOINT LINKAGE ---
@app.get("/api/process-league")
def process_league_pipeline(league: str = Query(..., description="League code e.g. england")):
    if league not in LEAGUE_TO_SPORT_KEY:
        raise HTTPException(status_code=400, detail="Requested league parameter not natively supported.")
    
    # Step 1: Execute scraping script targeting SoccerSTATS
    scraped_data = fetch_stats(league)
    if not scraped_data:
        raise HTTPException(status_code=502, detail="Failed to scrape metrics from upstream data source.")
    
    # Step 2: Inject stats directly into your predictive analytical model workbook
    processed_excel_path = inject_data_into_model(league, scraped_data)
    
    return {
        "status": "success",
        "processed_league": league,
