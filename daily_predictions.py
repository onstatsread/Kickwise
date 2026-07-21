"""
Kickwise Daily Blog Poster
Runs at 23:00 UTC (midnight Nigeria WAT) via GitHub Actions
Fetches all leagues with matches, runs predictions, posts to Blogger
"""
import requests
import json
import os
from datetime import date, datetime

BACKEND_URL = os.environ["BACKEND_URL"]
BLOG_ID     = os.environ["BLOG_ID"]
CLIENT_ID   = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]

# All league codes to check
LEAGUE_CODES = {
    "Albania - Abissnet Superiore": "albania",
    "Armenia - Premier League": "armenia",
    "Australia - A-League": "australia",
    "Australia - NPL Victoria": "australia3",
    "Australia - NPL Queensland": "australia4",
    "Australia - NPL Western Australia": "australia5",
    "Australia - NPL South Australia": "australia6",
    "Australia - NPL New South Wales": "australia11",
    "Australia - Victoria Premier League": "australia13",
    "Austria - Bundesliga": "austria",
    "Austria - 2. Liga": "austria2",
    "Azerbaijan - Premier League": "azerbaijan",
    "Belarus - Vysshaya Liga": "belarus",
    "Belgium - Pro League": "belgium",
    "Belgium - Challenger Pro League": "belgium2",
    "Bolivia - Division Profesional": "bolivia",
    "Bosnia - Premier Liga": "bosnia",
    "Brazil - Serie A": "brazil",
    "Brazil - Serie B": "brazil2",
    "Brazil - Serie C": "brazil3",
    "Bulgaria - Parva Liga": "bulgaria",
    "Canada - Premier League": "canada",
    "Chile - Liga de Primera": "chile",
    "China - Super League": "china",
    "China - League One": "china2",
    "Colombia - Primera A": "colombia",
    "Costa Rica - Primera Div.": "costarica",
    "Croatia - 1. HNL": "croatia",
    "Czech Republic - 1. Liga": "czechrepublic",
    "Czech Republic - FNL": "czechrepublic2",
    "Denmark - Superligaen": "denmark",
    "Denmark - 1st Division": "denmark2",
    "Ecuador - Liga Pro": "ecuador",
    "Egypt - Premier League": "egypt",
    "England - Premier League": "england",
    "England - Championship": "england2",
    "England - League One": "england3",
    "England - League Two": "england4",
    "England - National League": "england5",
    "Estonia - Meistriliiga": "estonia",
    "Faroe Islands - Premier League": "faroeislands",
    "Finland - Veikkausliiga": "finland",
    "Finland - Ykkosliiga": "finland2",
    "France - Ligue 1": "france",
    "France - Ligue 2": "france2",
    "France - National": "france3",
    "Georgia - Erovnuli Liga": "georgia",
    "Germany - Bundesliga": "germany",
    "Germany - 2. Bundesliga": "germany2",
    "Germany - 3. Liga": "germany3",
    "Greece - Super League": "greece",
    "Hungary - NB I": "hungary",
    "Iceland - Besta deild": "iceland",
    "Iceland - 1. Deild": "iceland2",
    "Ireland - Premier Division": "ireland",
    "Ireland - First Division": "ireland2",
    "Israel - Ligat HaAl": "israel",
    "Italy - Serie A": "italy",
    "Italy - Serie B": "italy2",
    "Italy - Serie C Group A": "italy3",
    "Italy - Serie C Group B": "italy4",
    "Italy - Serie C Group C": "italy5",
    "Kazakhstan - Premier League": "kazakhstan",
    "Latvia - Virsliga": "latvia",
    "Lithuania - A Lyga": "lithuania",
    "Malaysia - Super League": "malaysia",
    "Mexico - Liga MX": "mexico",
    "Moldova - Divizia Nationala": "moldova",
    "Montenegro - First League": "montenegro",
    "Morocco - Botola Pro": "morocco",
    "Netherlands - Eredivisie": "netherlands",
    "Netherlands - Eerste Divisie": "netherlands2",
    "Northern Ireland - NIFL Premiership": "northernireland",
    "North Macedonia - First League": "northmacedonia",
    "Norway - Eliteserien": "norway",
    "Norway - 1st Division": "norway2",
    "Paraguay - Primera Div.": "paraguay",
    "Peru - Liga 1": "peru",
    "Poland - Ekstraklasa": "poland",
    "Poland - 1. Liga": "poland2",
    "Portugal - Liga Portugal": "portugal",
    "Portugal - Liga Portugal 2": "portugal2",
    "Qatar - Stars League": "qatar",
    "Romania - Liga 1": "romania",
    "Russia - Premier League": "russia",
    "Russia - FNL": "russia2",
    "Saudi Arabia - Professional League": "saudiarabia",
    "Scotland - Premiership": "scotland",
    "Scotland - Championship": "scotland2",
    "Scotland - League One": "scotland3",
    "Scotland - League Two": "scotland4",
    "Serbia - Super Liga": "serbia",
    "Slovakia - 1. Liga": "slovakia",
    "Slovenia - Prva Liga": "slovenia",
    "South Africa - Premier Division": "southafrica",
    "South Korea - K League 1": "southkorea",
    "South Korea - K League 2": "southkorea2",
    "Spain - LaLiga": "spain",
    "Spain - LaLiga2": "spain2",
    "Sweden - Allsvenskan": "sweden",
    "Sweden - Superettan": "sweden2",
    "Switzerland - Super League": "switzerland",
    "Switzerland - Challenge League": "switzerland2",
    "Thailand - Thai League 1": "thailand",
    "Turkiye - Super Lig": "turkey",
    "Turkiye - 1. Lig": "turkey2",
    "UAE - Pro League": "uae",
    "Ukraine - Premier League": "ukraine",
    "Uruguay - Liga AUF": "uruguay",
    "USA - MLS": "usa",
    "USA - USL Championship": "usa2",
    "Venezuela - Liga FUTVE": "venezuela",
    "Vietnam - V League": "vietnam",
    "Wales - Cymru Premier": "wales",
}

def get_access_token():
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"
    })
    return resp.json().get("access_token")

def get_fixtures(league_code, date_str):
    try:
        r = requests.get(f"{BACKEND_URL}/fixtures",
                        params={"league": league_code, "date": date_str},
                        timeout=15)
        data = r.json()
        return data.get("matches", [])
    except:
        return []

def get_prediction(league_code, home, away):
    try:
        r = requests.get(f"{BACKEND_URL}/predict",
                        params={"league": league_code, "home": home, "away": away},
                        timeout=120)
        return r.json()
    except:
        return None

def format_match_html(league_name, match, pred):
    if not pred:
        return ""

    time_str = match.get("time", "TBD")
    home = match["home"]
    away = match["away"]

    # Smart Prediction 1
    c120_match = lambda v: (v or "").lower().strip() == "match" or \
                           ("match" in (v or "").lower() and "not" not in (v or "").lower())
    d64_both = "both" in (pred.get("d64","") + pred.get("d64r","")).lower()
    d64_one  = "one"  in (pred.get("d64","") + pred.get("d64r","")).lower()
    b120_combined = (pred.get("b120","") + " " + pred.get("b120r","")).lower()
    b120_double = "double" in b120_combined
    b120_under  = "under"  in b120_combined
    c120_ok = c120_match(pred.get("c120")) or c120_match(pred.get("c120r"))
    d70_main = pred.get("d70") if c120_match(pred.get("c120")) else (pred.get("d70r") or pred.get("d70",""))

    pred1 = ""
    if c120_ok:
        labels = []
        if b120_double: labels.append("double" if d64_both else ("2-handicap" if d64_one else None))
        if b120_under:  labels.append("under"  if d64_both else ("under extend" if d64_one else None))
        labels = list(set(filter(None, labels)))
        if labels:
            b46_match = __import__('re').search(r'(\d+\s*goals)', (pred.get("b46","") + " " + pred.get("b46r","")).lower())
            b46_goals = b46_match.group(1).replace(" ","") if b46_match else ""
            aa15_ok = any((pred.get(k,"") or "").lower() in ("yes","yes1","both") for k in ["aa15","aa15r"])
            goals_suffix = f" / {b46_goals}" if (aa15_ok and b46_goals) else ""
            pred1 = f"{d70_main} / {' + '.join(labels)}{goals_suffix}"

    # Smart Prediction 2
    o73 = (pred.get("o73") or pred.get("o73r") or "").strip()
    d69n = (pred.get("d70") or "").lower()
    d70n = (pred.get("d70val") or "").lower()
    d69r = (pred.get("d70r") or "").lower()
    d70r2 = (pred.get("d70valr") or "").lower()
    o73l = o73.lower()
    b46v = pred.get("b46") or pred.get("b46r") or ""

    r1 = o73l and (d69n.includes(o73l) if hasattr(d69n,'includes') else o73l in d69n) and \
         o73l in d70n and b120_double and c120_ok if o73l else False
    r1 = o73l and (o73l in d69n or o73l in d69r) and \
         (o73l in d70n or o73l in d70r2) and b120_double and c120_ok

    aa15_not_no = not all((pred.get(k,"") or "").lower() == "no" for k in ["aa15","aa15r"])
    b54_empty = not (pred.get("b54","") or "").strip() and not (pred.get("b54r","") or "").strip()
    o73_in_d69d70 = o73l and (o73l in d69n or o73l in d70n or o73l in d69r or o73l in d70r2)
    r2 = aa15_not_no and d64_both and b54_empty and c120_ok and o73_in_d69d70

    pred2 = ""
    all_handicap = all("handicap" in x for x in [d69n, d70n, d69r, d70r2])
    if r1 or r2:
        if all_handicap and o73:
            pred2 = f"{o73} only"
        elif r1 and r2:
            pred2 = f"{o73} / {b46v}"
        elif r1:
            pred2 = o73
        elif r2:
            pred2 = b46v

        if pred2 and not all_handicap:
            b118n = (pred.get("b118","") or "").lower()
            b118r = (pred.get("b118r","") or "").lower()
            n_home = "home g" in b118n or "home c" in b118n
            n_away = "away g" in b118n or "away c" in b118n
            r_home = "home" in b118r
            r_away = "away" in b118r
            if (n_home and r_home) or (n_away and r_away):
                pred2 += " / same"

    # Odds
    odds = pred.get("odds") or pred.get("oddsr") or {}
    odds_html = ""
    if odds and odds.get("home_odds"):
        odds_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;font-size:12px;color:#888">
            📊 Odds: Home {odds['home_odds']} ({odds['home_pct']}%) |
            Draw {odds['draw_odds']} ({odds['draw_pct']}%) |
            Away {odds['away_odds']} ({odds['away_pct']}%)
          </td>
        </tr>"""

    pred1_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;background:#1a472a;color:#AAFF3C;font-weight:bold">
            ⚡ PREDICTION 1: {pred1}
          </td>
        </tr>""" if pred1 else ""

    pred2_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;background:#1a3a47;color:#F39C12;font-weight:bold">
            🎯 PREDICTION 2: {pred2}
          </td>
        </tr>""" if pred2 else ""

    return f"""
<div style="border:1px solid #ddd;border-radius:8px;margin:10px 0;overflow:hidden;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr style="background:#0A3D1F;color:white">
      <td style="padding:8px 12px;font-weight:bold">{time_str} &nbsp; {home} vs {away}</td>
      <td style="padding:8px 12px;text-align:right;color:#AAFF3C">{league_name}</td>
    </tr>
    <tr>
      <td style="padding:6px 12px;font-size:13px"><b>D69:</b> {pred.get('d70','')} | <b>B120:</b> {pred.get('b120','')} | <b>C120:</b> {pred.get('c120','')}</td>
      <td style="padding:6px 12px;font-size:13px;color:#666"><b>D64:</b> {pred.get('d64','')} | <b>B46:</b> {pred.get('b46','')}</td>
    </tr>
    <tr style="background:#f9f9f9">
      <td style="padding:6px 12px;font-size:13px"><b>REV D69:</b> {pred.get('d70r','')} | <b>B120:</b> {pred.get('b120r','')} | <b>C120:</b> {pred.get('c120r','')}</td>
      <td style="padding:6px 12px;font-size:13px;color:#666"><b>D64:</b> {pred.get('d64r','')} | <b>B46:</b> {pred.get('b46r','')}</td>
    </tr>
    {pred1_html}
    {pred2_html}
    {odds_html}
  </table>
</div>"""

def post_to_blogger(access_token, title, content):
    resp = requests.post(
        f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/",
        headers={"Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/json"},
        json={"title": title, "content": content}
    )
    return resp.status_code, resp.json()

def main():
    today = date.today()
    date_str = f"{today.day} {today.strftime('%b')}"
    today_display = today.strftime("%A, %B %d %Y")

    print(f"🚀 Kickwise Daily Predictions — {today_display}")

    all_html = f"""
<div style="background:#0A3D1F;color:#AAFF3C;padding:16px;border-radius:8px;font-family:Arial,sans-serif;text-align:center">
  <h2 style="margin:0;font-size:24px">⚽ Kickwise Daily Predictions</h2>
  <p style="margin:4px 0;color:#fff">{today_display}</p>
  <p style="margin:4px 0;font-size:12px;color:#aaa">Predictions powered by A_mix2 Model | Data from SoccerStats</p>
</div>
"""

    # Collect ALL matches from all leagues first
    all_matches = []
    for league_name, code in LEAGUE_CODES.items():
        fixtures = get_fixtures(code, date_str)
        if not fixtures:
            continue
        print(f"  📌 {league_name}: {len(fixtures)} match(es)")
        for fix in fixtures:
            all_matches.append({
                "league_name": league_name,
                "code": code,
                "fix": fix
            })

    # Sort all matches by time (TBD goes to end)
    def sort_key(m):
        t = m["fix"].get("time", "")
        if not t or t == "TBD":
            return "99:99"
        return t
    all_matches.sort(key=sort_key)

    # Run predictions and build HTML — sorted by time, no league grouping
    total_matches = 0
    current_time = None
    for m in all_matches:
        pred = get_prediction(m["code"], m["fix"]["home"], m["fix"]["away"])
        # Skip if all key predictions are N/A
        if pred and all(
            (pred.get(k) or "N/A") in ("N/A", "", "None")
            for k in ["d70", "b120", "c120", "d64", "b46"]
        ):
            print(f"    ⚠️ Skipping {m['fix']['home']} vs {m['fix']['away']} (all N/A)")
            continue
        match_html = format_match_html(m["league_name"], m["fix"], pred)
        if match_html:
            # Add time separator header when time changes
            match_time = m["fix"].get("time", "TBD")
            if match_time != current_time:
                current_time = match_time
                all_html += f'\n<div style="background:#2C3E50;color:#AAFF3C;padding:8px 12px;margin:16px 0 4px;border-radius:4px;font-family:Arial;font-weight:bold;font-size:15px">🕐 {match_time}</div>\n'
            all_html += match_html
            total_matches += 1

    if total_matches == 0:
        print("No matches found today.")
        return

    all_html += f'\n<p style="text-align:center;color:#888;font-size:12px;margin-top:20px">Generated by Kickwise | {today_display} | {total_matches} matches processed</p>'

    print(f"\n📝 Posting {total_matches} matches to Blogger...")
    access_token = get_access_token()
    title = f"⚽ Kickwise Predictions — {today_display}"
    status, result = post_to_blogger(access_token, title, all_html)

    if status == 200:
        print(f"✅ Posted successfully! URL: {result.get('url','')}")
    else:
        print(f"❌ Failed to post: {status} — {result}")

if __name__ == "__main__":
    main()
