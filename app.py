"""
Kickwise Backend — FastAPI server
Deploy to Render.com (free tier)
"""
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import requests, os, subprocess, statistics, tempfile, shutil, difflib
from datetime import date
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(title="Kickwise API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE    = "https://www.soccerstats.com"
MODEL   = "A_mix2.xlsx"


def resolve_team(name, team_data):
    if name in team_data:
        return name
    for k in team_data:
        if name in k or k in name:
            return k
    matches = difflib.get_close_matches(name, team_data.keys(), n=1, cutoff=0.6)
    return matches[0] if matches else name


def fetch_stats(code):
    teams = {}
    try:
        soup = BeautifulSoup(
            requests.get(f"{BASE}/homeaway.asp?league={code}", headers=HEADERS, timeout=6).text,
            "html.parser")
        tables = soup.find_all("table")
        section_count = 0
        for tbl in tables:
            valid_rows = []
            for row in tbl.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 8:
                    continue
                team = cells[1].get_text(strip=True)
                if not team:
                    continue
                try:
                    gp = float(cells[2].get_text(strip=True))
                    gf = float(cells[6].get_text(strip=True))
                    ga = float(cells[7].get_text(strip=True))
                except:
                    continue
                if gp <= 0:
                    continue
                valid_rows.append((team, gp, gf, ga))
            if len(valid_rows) >= 10:
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
        print(f"Stats error: {e}")

    result = {}
    for team, d in teams.items():
        if "hgp" not in d or "agp" not in d:
            continue
        hgp, hgf, hga = d["hgp"], d["hgf"], d["hga"]
        agp, agf, aga = d["agp"], d["agf"], d["aga"]
        gp = hgp + agp
        gf = hgf + agf
        ga = hga + aga
        result[team] = {
            "gp": gp,
            "gf": gf / gp if gp else 0,
            "ga": ga / gp if gp else 0,
            "tot": (gf + ga) / gp if gp else 0,
            "hgf": hgf / hgp if hgp else 0,
            "hga": hga / hgp if hgp else 0,
            "htot": (hgf + hga) / hgp if hgp else 0,
            "agf": agf / agp if agp else 0,
            "aga": aga / agp if agp else 0,
            "atot": (agf + aga) / agp if agp else 0,
        }
    return result

def fetch_fixtures(code, date_str=None):
    if date_str:
        today1 = date_str
        today2 = date_str
    else:
        today1 = f"{date.today().day} {date.today().strftime('%b')}"
        today2 = date.today().strftime("%d %b")

    matches = []
    seen = set()
    try:
        soup = BeautifulSoup(
            requests.get(f"{BASE}/latest.asp?league={code}", headers=HEADERS, timeout=6).text,
            "html.parser")
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                date_text = cells[0].get_text(strip=True)
                if date_text not in (today1, today2):
                    continue
                score_text = cells[-1].get_text(strip=True)
                if score_text != "-":
                    continue
                middle = cells[1].get_text(" ", strip=True)
                if " - " in middle:
                    home, away = middle.split(" - ", 1)
                    home, away = home.strip(), away.strip()
                    key = (home, away)
                    if key not in seen:
                        seen.add(key)
                        matches.append({"time": "", "home": home, "away": away})
    except Exception as e:
        print(f"  Fixtures error: {e}")
    return matches


def run_model(home, away, team_data):
    if home not in team_data or away not in team_data:
        return {"d70": "N/A", "b120": "N/A", "c120": "N/A"}

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

    d70  = str(ws2["D70"].value  or "")
    b120 = str(ws2["B120"].value or "")
    c120 = str(ws2["C120"].value or "")

    if b120 in ("#NAME?", "#N/A", "None", ""):
        parts = [x for x in [str(ws2["B119"].value or ""),
                              str(ws2["C119"].value or ""),
                              str(ws2["D119"].value or "")]
                 if x and x not in ("run", "#NAME?", "#N/A", "None")]
        b120 = " /".join(parts)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"d70": d70, "b120": b120, "c120": c120}


@app.get("/analyze")
def analyze(league: str = Query(...), date: str = Query(None)):
    team_data = fetch_stats(league)
    fixtures  = fetch_fixtures(league, date)

    def process(fix):
        home = resolve_team(fix["home"], team_data)
        away = resolve_team(fix["away"], team_data)
        with ThreadPoolExecutor(max_workers=2) as inner:
            f1 = inner.submit(run_model, home, away, team_data)
            f2 = inner.submit(run_model, away, home, team_data)
            r1, r2 = f1.result(), f2.result()
        return {
            "time":  fix["time"],
            "home":  home,
            "away":  away,
            "d70":   r1["d70"],
            "b120":  r1["b120"],
            "c120":  r1["c120"],
            "d70r":  r2["d70"],
            "b120r": r2["b120"],
            "c120r": r2["c120"],
        }

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(process, fixtures))

    return {"league": league, "matches": results}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug")
def debug(league: str = Query(...), date: str = Query(None)):
    team_data = fetch_stats(league)
    fixtures  = fetch_fixtures(league, date)
    resolved  = [{"home": resolve_team(f["home"], team_data),
                   "away": resolve_team(f["away"], team_data),
                   "raw_home": f["home"], "raw_away": f["away"]} for f in fixtures]
    return {
        "team_count": len(team_data),
        "team_names": list(team_data.keys()),
        "fixtures": fixtures,
        "resolved": resolved
    }


LEAGUE_CODES = {
    "Albania - Abissnet Superiore": "albania",
    "Andorra - Primera Divisio": "andorra",
    "Argentina - Liga Profesional - Apertura": "argentina",
    "Argentina - Liga Profesional - Clausura": "argentina2",
    "Argentina - Primera Nacional": "argentina3",
    "Argentina - Primera B": "argentina4",
    "Argentina - Primera B - Clausura": "argentina5",
    "Armenia - Premier League": "armenia",
    "Australia - A-League": "australia",
    "Australia - A-League Women": "australia2",
    "Australia - NPL Victoria": "australia3",
    "Australia - NPL Queensland": "australia4",
    "Australia - NPL Western Australia": "australia5",
    "Australia - NPL South Australia": "australia6",
    "Australia - NPL Northern NSW": "australia7",
    "Australia - NPL Tasmania": "australia8",
    "Australia - NPL Capital Territory": "australia10",
    "Australia - NPL New South Wales": "australia11",
    "Australia - Victoria Premier League": "australia13",
    "Australia - WA State League": "australia14",
    "Australia - Queensland Premier League": "australia16",
    "Austria - Bundesliga": "austria",
    "Austria - 2. Liga": "austria2",
    "Austria - Regionalliga West": "austria5",
    "Austria - Regionalliga Ost": "austria7",
    "Austria - Bundesliga Women": "austria8",
    "Azerbaijan - Premier League": "azerbaijan",
    "Azerbaijan - First Division": "azerbaijan2",
    "Bahrain - Premier League": "bahrain",
    "Bangladesh - Premier League": "bangladesh",
    "Belarus - Vysshaya Liga": "belarus",
    "Belgium - Pro League": "belgium",
    "Belgium - Challenger Pro League": "belgium2",
    "Belgium - U21 Pro League": "belgium6",
    "Bolivia - Division Profesional": "bolivia",
    "Bosnia - Premier Liga": "bosnia",
    "Bosnia - Prva Liga FBiH": "bosnia2",
    "Brazil - Serie A": "brazil",
    "Brazil - Serie B": "brazil2",
    "Brazil - Serie C": "brazil3",
    "Brazil - Brasileiro Women": "brazil5",
    "Brazil - Mineiro": "brazil7",
    "Brazil - Goiano": "brazil8",
    "Brazil - Brasiliense": "brazil11",
    "Brazil - Matogrossense": "brazil12",
    "Brazil - Paulista A1": "brazil14",
    "Brazil - Paulista A2": "brazil15",
    "Brazil - Paulista A3": "brazil16",
    "Brazil - Parabaino": "brazil17",
    "Brazil - Sergipano": "brazil19",
    "Brazil - Tocantinense": "brazil21",
    "Bulgaria - Parva Liga": "bulgaria",
    "Bulgaria - Vtora Liga": "bulgaria2",
    "Canada - Premier League": "canada",
    "Chile - Liga de Primera": "chile",
    "Chile - Liga de Ascenso": "chile2",
    "China - Super League": "china",
    "China - League One": "china2",
    "China - League Two": "china3",
    "Colombia - Primera A - Apertura": "colombia",
    "Colombia - Primera A - Clausura": "colombia2",
    "Colombia - Primera B - Apertura": "colombia3",
    "Colombia - Primera B - Clausura": "colombia4",
    "Costa Rica - Primera Div. - Apertura": "costarica",
    "Costa Rica - Primera Div. - Clausura": "costarica2",
    "Croatia - 1. HNL": "croatia",
    "Croatia - 1. NL": "croatia2",
    "Cyprus - Cyprus League": "cyprus",
    "Czech Republic - 1. Liga": "czechrepublic",
    "Czech Republic - FNL": "czechrepublic2",
    "Czech Republic - U19 League": "czechrepublic3",
    "Czech Republic - 1. Liga Women": "czechrepublic4",
    "Denmark - Superligaen": "denmark",
    "Denmark - 1st Division": "denmark2",
    "Denmark - 2nd Division": "denmark3",
    "Ecuador - Liga Pro": "ecuador",
    "Egypt - Premier League": "egypt",
    "England - Premier League": "england",
    "England - Championship": "england2",
    "England - League One": "england3",
    "England - League Two": "england4",
    "England - National League": "england5",
    "England - National League North": "england6",
    "England - National League South": "england7",
    "England - Isthmian League": "england8",
    "England - Northern League": "england9",
    "England - Southern Central": "england10",
    "England - Southern South": "england11",
    "England - Premier League 2": "england15",
    "England - Women Super League": "england17",
    "England - WSL 2": "england18",
    "England - Professional Development League": "england19",
    "Estonia - Meistriliiga": "estonia",
    "Faroe Islands - Premier League": "faroeislands",
    "Faroe Islands - 1. Deild": "faroeislands2",
    "Finland - Veikkausliiga": "finland",
    "Finland - Ykkosliiga": "finland2",
    "Finland - Ykkonen": "finland3",
    "Finland - Kakkonen Group A": "finland4",
    "Finland - Kakkonen Group B": "finland5",
    "Finland - Kakkonen Group C": "finland6",
    "Finland - Kansallinen Liiga Women": "finland7",
    "France - Ligue 1": "france",
    "France - Ligue 2": "france2",
    "France - National": "france3",
    "France - Premiere Ligue Women": "france14",
    "Georgia - Erovnuli Liga": "georgia",
    "Georgia - Erovnuli Liga 2": "georgia2",
    "Germany - Bundesliga": "germany",
    "Germany - 2. Bundesliga": "germany2",
    "Germany - 3. Liga": "germany3",
    "Germany - Regionalliga Nord": "germany4",
    "Germany - Regionalliga Nordost": "germany5",
    "Germany - Regionalliga West": "germany6",
    "Germany - Regionalliga Sudwest": "germany7",
    "Germany - Regionalliga Bayern": "germany8",
    "Germany - Oberliga Baden-Wurttemberg": "germany9",
    "Germany - Oberliga Hamburg": "germany10",
    "Germany - Oberliga Niederrhein": "germany11",
    "Germany - Oberliga Hessen": "germany15",
    "Germany - Oberliga Niedersachsen": "germany17",
    "Germany - Oberliga Mittelrhein": "germany19",
    "Germany - Oberliga Bayern Nord": "germany21",
    "Germany - Oberliga Bayern Sud": "germany22",
    "Germany - Bundesliga Women": "germany23",
    "Germany - 2. Bundesliga Women": "germany24",
    "Greece - Super League": "greece",
    "Guatemala - Liga Nacional - Apertura": "guatemala",
    "Guatemala - Liga Nacional - Clausura": "guatemala2",
    "Hong Kong - Premier League": "hongkong",
    "Hungary - NB I": "hungary",
    "Hungary - NB II": "hungary2",
    "Hungary - NB I Women": "hungary3",
    "Iceland - Besta deild": "iceland",
    "Iceland - 1. Deild": "iceland2",
    "Iceland - 2. Deild": "iceland3",
    "Iceland - Besta deild Women": "iceland5",
    "India - Super League": "india",
    "Indonesia - Liga 1": "indonesia",
    "Iran - Pro League": "iran",
    "Ireland - Premier Division": "ireland",
    "Ireland - First Division": "ireland2",
    "Ireland - Women National League": "ireland3",
    "Israel - Ligat HaAl": "israel",
    "Italy - Serie A": "italy",
    "Italy - Serie B": "italy2",
    "Italy - Serie C Group A": "italy3",
    "Italy - Serie C Group B": "italy4",
    "Italy - Serie C Group C": "italy5",
    "Italy - Serie D Group A": "italy6",
    "Italy - Serie D Group B": "italy7",
    "Italy - Serie D Group C": "italy8",
    "Italy - Serie D Group D": "italy9",
    "Italy - Serie D Group E": "italy10",
    "Italy - Serie D Group F": "italy11",
    "Italy - Serie D Group G": "italy12",
    "Italy - Serie D Group H": "italy13",
    "Italy - Serie D Group I": "italy14",
    "Italy - Primavera 1": "italy15",
    "Italy - Serie A Women": "italy17",
    "Italy - Serie B Women": "italy18",
    "Jamaica - Premier League": "jamaica",
    "Japan - WE League": "japan4",
    "Japan - Nadeshiko League 1": "japan5",
    "Jordan - Premier League": "jordan",
    "Kazakhstan - Premier League": "kazakhstan",
    "Kuwait - Premier League": "kuwait",
    "Latvia - Virsliga": "latvia",
    "Latvia - 1. Liga": "latvia2",
    "Lithuania - A Lyga": "lithuania",
    "Lithuania - 1st League": "lithuania2",
    "Malaysia - Super League": "malaysia",
    "Mexico - Liga MX - Apertura": "mexico",
    "Mexico - Liga MX - Clausura": "mexico2",
    "Moldova - Divizia Nationala": "moldova",
    "Montenegro - First League": "montenegro",
    "Morocco - Botola Pro": "morocco",
    "Netherlands - Eredivisie": "netherlands",
    "Netherlands - Eerste Divisie": "netherlands2",
    "Netherlands - Tweede Divisie": "netherlands3",
    "Netherlands - Derde Divisie Group A": "netherlands4",
    "Netherlands - Derde Divisie Group B": "netherlands5",
    "Netherlands - Eredivisie Women": "netherlands6",
    "Northern Ireland - NIFL Premiership": "northernireland",
    "Northern Ireland - NIFL Championship": "northernireland2",
    "North Macedonia - First League": "northmacedonia",
    "Norway - Eliteserien": "norway",
    "Norway - 1st Division": "norway2",
    "Norway - Division 2 Group 1": "norway3",
    "Norway - Division 2 Group 2": "norway4",
    "Norway - Division 3 Group 1": "norway5",
    "Norway - Division 3 Group 2": "norway6",
    "Norway - Division 3 Group 3": "norway7",
    "Norway - Division 3 Group 4": "norway8",
    "Norway - Division 3 Group 5": "norway9",
    "Norway - Division 3 Group 6": "norway10",
    "Norway - Toppserien Women": "norway11",
    "Norway - 1. Division Women": "norway12",
    "Paraguay - Primera Div. - Apertura": "paraguay",
    "Paraguay - Primera Div. - Clausura": "paraguay2",
    "Paraguay - Division Intermedia": "paraguay3",
    "Peru - Liga 1 - Apertura": "peru",
    "Peru - Liga 1 - Clausura": "peru2",
    "Philippines - PFL": "philippines",
    "Poland - Ekstraklasa": "poland",
    "Poland - 1. Liga": "poland2",
    "Poland - 2. Liga": "poland3",
    "Poland - Ekstraliga Women": "poland4",
    "Portugal - Liga Portugal": "portugal",
    "Portugal - Liga Portugal 2": "portugal2",
    "Portugal - First Division Women": "portugal8",
    "Qatar - Stars League": "qatar",
    "Qatar - Division 2": "qatar2",
    "Romania - Liga 1": "romania",
    "Romania - Liga 2": "romania2",
    "Romania - Superliga Women": "romania3",
    "Russia - Premier League": "russia",
    "Russia - FNL": "russia2",
    "San Marino - Campionato Sammarinese": "sanmarino",
    "Saudi Arabia - Professional League": "saudiarabia",
    "Saudi Arabia - Division 1": "saudiarabia2",
    "Scotland - Premiership": "scotland",
    "Scotland - Championship": "scotland2",
    "Scotland - League One": "scotland3",
    "Scotland - League Two": "scotland4",
    "Scotland - SWPL 1 Women": "scotland7",
    "Serbia - Super Liga": "serbia",
    "Serbia - Prva Liga": "serbia2",
    "Singapore - Premier League": "singapore",
    "Slovakia - 1. Liga": "slovakia",
    "Slovakia - 2. Liga": "slovakia2",
    "Slovakia - 1. Liga Women": "slovakia3",
    "Slovenia - Prva Liga": "slovenia",
    "South Africa - Premier Division": "southafrica",
    "South Africa - First Division": "southafrica2",
    "South Korea - K League 1": "southkorea",
    "South Korea - K League 2": "southkorea2",
    "South Korea - WK League Women": "southkorea4",
    "Spain - LaLiga": "spain",
    "Spain - LaLiga2": "spain2",
    "Spain - Primera RFEF Group 1": "spain3",
    "Spain - Primera RFEF Group 2": "spain4",
    "Spain - Segunda RFEF Group 1": "spain5",
    "Spain - Segunda RFEF Group 2": "spain6",
    "Spain - Segunda RFEF Group 3": "spain7",
    "Spain - Segunda RFEF Group 4": "spain8",
    "Spain - Segunda RFEF Group 5": "spain9",
    "Sweden - Allsvenskan": "sweden",
    "Sweden - Superettan": "sweden2",
    "Sweden - Div 1 Norra": "sweden3",
    "Sweden - Div 1 Sodra": "sweden4",
    "Sweden - Div 2 Norra Gotaland": "sweden5",
    "Sweden - Div 2 Norra Svealand": "sweden6",
    "Sweden - Div 2 Norrland": "sweden7",
    "Sweden - Div 2 Sodra Svealand": "sweden8",
    "Sweden - Div 2 Vastra Gotaland": "sweden9",
    "Sweden - Div 2 Sodra Gotaland": "sweden10",
    "Sweden - Allsvenskan Women": "sweden11",
    "Sweden - Elitettan Women": "sweden12",
    "Switzerland - Super League": "switzerland",
    "Switzerland - Challenge League": "switzerland2",
    "Switzerland - Promotion League": "switzerland3",
    "Switzerland - Women Super League": "switzerland4",
    "Thailand - Thai League 1": "thailand",
    "Turkiye - Super Lig": "turkey",
    "Turkiye - 1. Lig": "turkey2",
    "Turkiye - 2. Lig White Group": "turkey3",
    "Turkiye - 2. Lig Red Group": "turkey4",
    "Turkiye - 3. Lig Group 1": "turkey5",
    "Turkiye - 3. Lig Group 2": "turkey6",
    "Turkiye - 3. Lig Group 3": "turkey7",
    "Turkiye - 3. Lig Group 4": "turkey8",
    "UAE - Pro League": "uae",
    "Ukraine - Premier League": "ukraine",
    "Ukraine - Persha Liga": "ukraine2",
    "Ukraine - U19 League": "ukraine5",
    "Uruguay - Liga AUF - Apertura": "uruguay",
    "Uruguay - Liga AUF - Intermediate": "uruguay2",
    "Uruguay - Liga AUF - Clausura": "uruguay3",
    "USA - MLS": "usa",
    "USA - USL Championship": "usa2",
    "USA - USL League One": "usa3",
    "USA - NWSL": "usa6",
    "USA - USL Super League": "usa7",
    "Venezuela - Liga FUTVE - Apertura": "venezuela",
    "Venezuela - Liga FUTVE - Clausura": "venezuela2",
    "Vietnam - V League": "vietnam",
    "Vietnam - National League Women": "vietnam3",
    "Wales - Cymru Premier": "wales",
}


@app.get("/leagues_today")
def leagues_today(date: str = Query(None)):
    def check(item):
        name, code = item
        try:
            fixtures = fetch_fixtures(code, date)
        except:
            fixtures = []
        return {"name": name, "code": code, "count": len(fixtures)} if fixtures else None

    with ThreadPoolExecutor(max_workers=50) as executor:
        results = list(executor.map(check, LEAGUE_CODES.items()))

    available = [r for r in results if r]
    available.sort(key=lambda x: x["name"])
    return {"leagues": available}
