"""
Kickwise Daily Blog Poster
Runs at 23:00 UTC (midnight Nigeria WAT) via GitHub Actions
Fetches all leagues with matches, runs predictions, posts to Blogger
"""
import requests
import json
import os
from datetime import date, datetime, timedelta

BACKEND_URL = os.environ["BACKEND_URL"]
BLOG_ID     = os.environ["BLOG_ID"]
CLIENT_ID   = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]

# All league codes to check
# Trimmed from ~114 to 41 leagues (Aug 2026) — dropped every league on an
# Aug-May calendar since those seasons just started (0-2 games played,
# model returns N/A regardless of league quality). Kept leagues on
# Mar-Nov or Southern Hemisphere calendars, genuinely mid-season right
# now. Revisit this list as European seasons progress through the year —
# leagues dropped here aren't bad, just too early right now.
LEAGUE_CODES = {
    "Australia - NPL Victoria": "australia3",
    "Australia - NPL Queensland": "australia4",
    "Australia - NPL Western Australia": "australia5",
    "Australia - NPL South Australia": "australia6",
    "Australia - NPL New South Wales": "australia11",
    "Australia - Victoria Premier League": "australia13",
    "Belarus - Vysshaya Liga": "belarus",
    "Brazil - Serie A": "brazil",
    "Brazil - Serie B": "brazil2",
    "Brazil - Serie C": "brazil3",
    "Canada - Premier League": "canada",
    "Chile - Liga de Primera": "chile",
    "China - Super League": "china",
    "China - League One": "china2",
    "Colombia - Primera A": "colombia",
    "Ecuador - Liga Pro": "ecuador",
    "Estonia - Meistriliiga": "estonia",
    "Faroe Islands - Premier League": "faroeislands",
    "Finland - Veikkausliiga": "finland",
    "Finland - Ykkosliiga": "finland2",
    "Georgia - Erovnuli Liga": "georgia",
    "Iceland - Besta deild": "iceland",
    "Iceland - 1. Deild": "iceland2",
    "Ireland - Premier Division": "ireland",
    "Ireland - First Division": "ireland2",
    "Kazakhstan - Premier League": "kazakhstan",
    "Latvia - Virsliga": "latvia",
    "Lithuania - A Lyga": "lithuania",
    "Malaysia - Super League": "malaysia",
    "Norway - Eliteserien": "norway",
    "Norway - 1st Division": "norway2",
    "Paraguay - Primera Div.": "paraguay",
    "Peru - Liga 1": "peru",
    "South Korea - K League 1": "southkorea",
    "South Korea - K League 2": "southkorea2",
    "Sweden - Allsvenskan": "sweden",
    "Sweden - Superettan": "sweden2",
    "Uruguay - Liga AUF": "uruguay",
    "USA - MLS": "usa",
    "USA - USL Championship": "usa2",
    "Venezuela - Liga FUTVE": "venezuela",
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
        # timeout raised from 15 to 90 — /fixtures now calls ScraperAPI
        # with render=true internally, which can take 30-60+ seconds to
        # solve SoccerStats' Cloudflare challenge. The old 15s timeout was
        # silently killing every single one of these calls (bare except
        # below swallowed it with no log line), which is why every league
        # came back with zero fixtures despite the backend working fine.
        r = requests.get(f"{BACKEND_URL}/fixtures",
                        params={"league": league_code, "date": date_str},
                        timeout=90)
        data = r.json()
        return data.get("matches", [])
    except Exception as e:
        print(f"    ⚠️ get_fixtures failed for {league_code}: {e}")
        return []

def get_prediction(league_code, home, away, retries=2):
    """
    Calls /predict, retrying on failure before giving up. Returns None
    only after all attempts fail — the caller logs this distinctly from
    a genuine N/A prediction, so failed matches aren't silently confused
    with matches that legitimately have no prediction.
    """
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(f"{BACKEND_URL}/predict",
                            params={"league": league_code, "home": home, "away": away},
                            timeout=180)  # raised from 120 — /predict now also does
                                          # market odds + fallback lookups per call
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_error = e
            if attempt < retries:
                print(f"    ⏳ Attempt {attempt} failed for {home} vs {away} ({e}) — retrying...")
    print(f"    ❌ Failed to get prediction for {home} vs {away} after {retries} attempts: {last_error}")
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

    # Market odds (real bookmaker odds, if this league is covered)
    market_odds = pred.get("market_odds") or {}
    market_html = ""
    if market_odds and market_odds.get("home_odds"):
        market_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;font-size:12px;color:#888">
            💰 Market Odds: Home {market_odds['home_odds']} ({market_odds['home_pct']}%) |
            Draw {market_odds['draw_odds']} ({market_odds['draw_pct']}%) |
            Away {market_odds['away_odds']} ({market_odds['away_pct']}%)
          </td>
        </tr>"""

    # Value% — (market odd − model odd) / model odd × 100, plus decision.
    # value_pct: home, draw, away, total, share_diff.
    # value_signal: decision (Home / Away / Home 2-handicap / Away 2-handicap),
    # under (flag from Total, unrelated to the H/A/D decision itself).
    value_pct = pred.get("value_pct") or {}
    value_signal = pred.get("value_signal") or {}
    value_html = ""
    if value_pct:
        def fmt_val(n):
            if n is None:
                return "—"
            sign = "+" if n > 0 else ""
            return f"{sign}{n}%"

        signal_str = f"Signal: {value_signal['under']}" if value_signal.get("under") else ""
        signal_line = f"<br><b style='color:#AAFF3C'>{signal_str}</b>" if signal_str else ""

        decision = value_signal.get("decision", "")
        decision_line = f"<br><b style='color:#F39C12;font-size:14px'>⚡ DECISION: {decision}</b>" if decision else ""

        value_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;font-size:12px;color:#888">
            📈 Value: Home {fmt_val(value_pct.get('home'))} |
            Draw {fmt_val(value_pct.get('draw'))} |
            Away {fmt_val(value_pct.get('away'))} |
            Total {fmt_val(value_pct.get('total'))} |
            Share Diff {fmt_val(value_pct.get('share_diff'))}
            {signal_line}
            {decision_line}
          </td>
        </tr>"""

    # Over/Under 2.5 — model + market odds, and the same value/share formula
    # (UNCHANGED — still uses its own share_diff/result logic, separate
    # from the H/A/D decision above)
    ou25 = pred.get("ou25") or {}
    market_ou25 = pred.get("market_ou25") or {}
    ou25_html = ""
    if ou25.get("over_odds") or market_ou25.get("over_odds"):
        parts = []
        if ou25.get("over_odds"):
            parts.append(f"Model Over {ou25['over_odds']} ({ou25['over_pct']}%) / Under {ou25['under_odds']} ({ou25['under_pct']}%)")
        if market_ou25.get("over_odds"):
            parts.append(f"Market Over {market_ou25['over_odds']} ({market_ou25['over_pct']}%) / Under {market_ou25['under_odds']} ({market_ou25['under_pct']}%)")
        ou25_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;font-size:12px;color:#888">
            ⚽ O/U 2.5: {" | ".join(parts)}
          </td>
        </tr>"""

    ou25_value_pct = pred.get("ou25_value_pct") or {}
    ou25_value_signal = pred.get("ou25_value_signal") or {}
    ou25_value_html = ""
    if ou25_value_pct:
        def fmt_val_ou(n):
            if n is None:
                return "—"
            sign = "+" if n > 0 else ""
            return f"{sign}{n}%"

        ou_signal_str = f"Result: {ou25_value_signal['result']}" if ou25_value_signal.get("result") else ""
        ou_signal_line = f"<br><b style='color:#AAFF3C'>{ou_signal_str}</b>" if ou_signal_str else ""

        ou25_value_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;font-size:12px;color:#888">
            📈 O/U 2.5 Value: Over {fmt_val_ou(ou25_value_pct.get('over'))} |
            Under {fmt_val_ou(ou25_value_pct.get('under'))} |
            Total {fmt_val_ou(ou25_value_pct.get('total'))} |
            Share Diff {fmt_val_ou(ou25_value_pct.get('share_diff'))}
            {ou_signal_line}
          </td>
        </tr>"""

    pred1_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;background:#1a472a;color:#AAFF3C;font-weight:bold">
            ⚡ PREDICTION 1: {pred1}
          </td>
        </tr>""" if pred1 else ""

    if pred2:
        aa15_no = (pred.get("aa15","") or "").lower() == "no" or \
                  (pred.get("aa15r","") or "").lower() == "no"
        b54_over = "over" in (pred.get("b54","") or "").lower() or \
                   "over" in (pred.get("b54r","") or "").lower()
        if b54_over:
            pred2_bg    = "#000000"
            pred2_color = "#ffffff"
        elif aa15_no:
            pred2_bg    = "#C0392B"
            pred2_color = "#ffffff"
        else:
            pred2_bg    = "#1a3a47"
            pred2_color = "#F39C12"
        pred2_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;background:{pred2_bg};color:{pred2_color};font-weight:bold">
            🎯 PREDICTION 2: {pred2}
          </td>
        </tr>"""
    else:
        pred2_html = ""

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
    {market_html}
    {value_html}
    {ou25_html}
    {ou25_value_html}
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
    # Script runs at 23:00 UTC = midnight WAT (Nigeria, UTC+1) — right at
    # the moment Nigeria's calendar day rolls over. date.today() on the
    # GitHub Actions runner returns the UTC date, which at that instant is
    # still the OLD day for Nigeria — the day that just ended, not the one
    # starting. That caused fetch_fixtures() to query SoccerStats for the
    # wrong day, so today's fixtures (like a 5pm match) were never fetched
    # at all — not skipped, never requested in the first place.
    # Fix: compute "today" from Nigeria's local time instead of raw UTC.
    today = (datetime.utcnow() + timedelta(hours=1)).date()
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
    seen_matches = set()  # safety net — league code + normalized team names
    for league_name, code in LEAGUE_CODES.items():
        fixtures = get_fixtures(code, date_str)
        if not fixtures:
            continue
        print(f"  📌 {league_name}: {len(fixtures)} match(es)")
        for fix in fixtures:
            dedup_key = (code, fix["home"].lower().strip(), fix["away"].lower().strip())
            if dedup_key in seen_matches:
                print(f"    ⚠️ Skipping duplicate: {fix['home']} vs {fix['away']} ({league_name})")
                continue
            seen_matches.add(dedup_key)
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
    failed_matches = 0
    na_matches = 0
    current_time = None
    for m in all_matches:
        pred = get_prediction(m["code"], m["fix"]["home"], m["fix"]["away"])

        if pred is None:
            # get_prediction() already logged the failure reason and retried —
            # this match is dropped because the request genuinely failed,
            # NOT because it's a real N/A. Counted separately below.
            failed_matches += 1
            continue

        # Skip ONLY if all key predictions are genuinely N/A
        if all(
            (pred.get(k) or "N/A") in ("N/A", "", "None")
            for k in ["d70", "b120", "c120", "d64", "b46"]
        ):
            print(f"    ⚠️ Skipping {m['fix']['home']} vs {m['fix']['away']} (all N/A)")
            na_matches += 1
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

    print(f"\n📊 Summary: {total_matches} posted | {na_matches} skipped (genuine N/A) | {failed_matches} dropped (request failed after retries)")

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
