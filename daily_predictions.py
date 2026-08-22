"""
Kickwise Daily Blog Poster
Runs at 23:00 UTC (midnight Nigeria WAT) via GitHub Actions
Fetches all leagues with matches, runs predictions, posts to Blogger
"""
import requests
import json
import os
import re
from datetime import date, datetime, timedelta

BACKEND_URL = os.environ["BACKEND_URL"]
BLOG_ID     = os.environ["BLOG_ID"]
CLIENT_ID   = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]

# NEW — second blog, different Google account, filtered "standard" subset
# of matches only (not a different league list — same 34 leagues checked
# once, filtered per-match for this blog). All four vars must be set for
# blog 2 to run; if any are missing, blog 2 is silently skipped so this
# doesn't break the main blog if not configured yet.
BLOG_ID_2       = os.environ.get("BLOG_ID_2")
CLIENT_ID_2     = os.environ.get("GOOGLE_CLIENT_ID_2")
CLIENT_SECRET_2 = os.environ.get("GOOGLE_CLIENT_SECRET_2")
REFRESH_TOKEN_2 = os.environ.get("GOOGLE_REFRESH_TOKEN_2")
BLOG2_ENABLED = all([BLOG_ID_2, CLIENT_ID_2, CLIENT_SECRET_2, REFRESH_TOKEN_2])

# NEW — WhatsApp notification via CallMeBot (free) when blog 2 posts
# successfully. Requires a one-time opt-in: save +34 644 51 71 41 as a
# contact, WhatsApp it "I allow callmebot to send me messages", then use
# the API key it replies with. Silently skipped if not configured.
# NEW — Telegram notification when blog 2 posts successfully. Switched
# from CallMeBot (WhatsApp) after it never delivered the opt-in reply —
# Telegram's official Bot API is free and far more reliable since it's
# run by Telegram itself, not a third-party community service.
# Requires a one-time setup: create a bot via @BotFather, message it
# once, then get the token + chat ID. Silently skipped if not configured.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID])


def send_telegram_notification(message):
    """Sends a message via Telegram Bot API. Fails silently (prints a
    warning) — a notification failure should never crash the actual
    blog-posting run."""
    if not TELEGRAM_ENABLED:
        return
    try:
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            params={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=15,
        )
    except Exception as e:
        print(f"⚠️ Telegram notification failed: {e}")

# All league codes to check
# Trimmed from ~114 to 41 leagues (Aug 2026) — dropped every league on an
# Aug-May calendar since those seasons just started (0-2 games played,
# model returns N/A regardless of league quality). Kept leagues on
# Mar-Nov or Southern Hemisphere calendars, genuinely mid-season right
# now. Revisit this list as European seasons progress through the year —
# leagues dropped here aren't bad, just too early right now.
#
# Further trimmed from 41 to 33 (Aug 2026) — dropped the 6 Australia NPL
# state leagues and Brazil Serie C after switching fully to AnnaBet as
# the data source (no more ScraperAPI/SoccerStats fallback). AnnaBet
# doesn't have a mapped equivalent for these 7 leagues, so they'd return
# nothing now. Re-add if/when an AnnaBet mapping is found for them.
LEAGUE_CODES = {
    "Belarus - Vysshaya Liga": "belarus",
    "Brazil - Serie A": "brazil",
    "Brazil - Serie B": "brazil2",
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

def get_access_token(client_id=None, client_secret=None, refresh_token=None):
    """Parameterized so it works for either blog's Google account — defaults
    to blog 1's credentials if called with no arguments (existing behavior
    unchanged for the main blog)."""
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id or CLIENT_ID,
        "client_secret": client_secret or CLIENT_SECRET,
        "refresh_token": refresh_token or REFRESH_TOKEN,
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

def build_pred2_text(pred):
    """Builds the Prediction 2 string from a /predict response. Used by
    format_match_html() to display Prediction 2 on the card. (No longer
    used by the blog 2 filter — meets_blog2_standard() now checks odds,
    O/U result, and Prediction 3 instead.)"""
    o73 = (pred.get("o73") or pred.get("o73r") or "").strip()
    d69n = (pred.get("d70") or "").lower()
    d70n = (pred.get("d70val") or "").lower()
    d69r = (pred.get("d70r") or "").lower()
    d70r2 = (pred.get("d70valr") or "").lower()
    o73l = o73.lower()
    b46v = pred.get("b46") or pred.get("b46r") or ""

    d64_both = "both" in (pred.get("d64","") + pred.get("d64r","")).lower()
    b120_combined = (pred.get("b120","") + " " + pred.get("b120r","")).lower()
    b120_double = "double" in b120_combined
    c120_match = lambda v: (v or "").lower().strip() == "match" or \
                           ("match" in (v or "").lower() and "not" not in (v or "").lower())
    c120_ok = c120_match(pred.get("c120")) or c120_match(pred.get("c120r"))

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

    return pred2


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
            b46_match = re.search(r'(\d+\s*goals)', (pred.get("b46","") + " " + pred.get("b46r","")).lower())
            b46_goals = b46_match.group(1).replace(" ","") if b46_match else ""
            aa15_ok = any((pred.get(k,"") or "").lower() in ("yes","yes1","both") for k in ["aa15","aa15r"])
            goals_suffix = f" / {b46_goals}" if (aa15_ok and b46_goals) else ""
            pred1 = f"{d70_main} / {' + '.join(labels)}{goals_suffix}"

    # Smart Prediction 2 — built via shared helper build_pred2_text()
    pred2 = build_pred2_text(pred)

    # NEW — Prediction 3, straight from the backend (/predict already
    # computes this — cross-check of the O/U step5 signal against the
    # H/D/A decision, or "Under" if the hidden 4-condition gate passed).
    # No extra logic needed here, just display it.
    pred3 = pred.get("prediction_3", "")

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

    # O/U 2.5 value — NEW step 2-6 shape: over/under/total/over_share/
    # under_share/abs_diff, plus step4/step5/step6 + final result
    # ("under confirmed" / "under" / "over") from ou25_value_signal.
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
            Over Share {fmt_val_ou(ou25_value_pct.get('over_share'))} |
            Under Share {fmt_val_ou(ou25_value_pct.get('under_share'))} |
            Abs Diff {fmt_val_ou(ou25_value_pct.get('abs_diff'))} |
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

    # NEW — Prediction 3 block. Styled blue to match the frontend's
    # Prediction 3 card. "handicap" outcomes get a slightly darker/flagged
    # background, same visual language as Prediction 2's handicap/no cases.
    if pred3:
        if "handicap" in pred3.lower():
            pred3_bg    = "#12283a"
            pred3_color = "#85C1E9"
        else:
            pred3_bg    = "#1a3a52"
            pred3_color = "#3498DB"
        pred3_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;background:{pred3_bg};color:{pred3_color};font-weight:bold">
            🧭 PREDICTION 3: {pred3}
          </td>
        </tr>"""
    else:
        pred3_html = ""

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
    {pred3_html}
  </table>
</div>"""

# NEW — blog 2 standard (replaces the earlier Prediction-2-based standard
# AND the Prediction-3-gate standard — this is now the ONLY filter). A
# match qualifies only if ALL 3 conditions are met:
#   1. None of the model or market odds (Home/Draw/Away, both directions)
#      are below 1.4 — filters out heavily lopsided/near-certain matches.
#   2. ou25_value_signal['result'] is "under" or "under confirmed".
#   3. prediction_3 contains "away" (covers "Away", "Away handicap",
#      "Away 2-handicap", and the hidden-gate "Away/ under Ngoals" form).
def meets_blog2_standard(pred):
    model_odds = pred.get("odds") or pred.get("oddsr") or {}
    market_odds = pred.get("market_odds") or {}

    odds_to_check = [
        model_odds.get("home_odds"), model_odds.get("draw_odds"), model_odds.get("away_odds"),
        market_odds.get("home_odds"), market_odds.get("draw_odds"), market_odds.get("away_odds"),
    ]
    if any(o is None for o in odds_to_check):
        return False
    if any(o < 1.4 for o in odds_to_check):
        return False

    ou25_value_signal = pred.get("ou25_value_signal") or {}
    result = ou25_value_signal.get("result", "")
    if result not in ("under", "under confirmed"):
        return False

    prediction_3 = (pred.get("prediction_3") or "").lower()
    if "away" not in prediction_3:
        return False

    return True


def post_to_blogger(access_token, blog_id, title, content):
    resp = requests.post(
        f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/",
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

    # NEW — blog 2 gets its own separate HTML, built alongside blog 1's
    # in the same loop (one set of predictions, two filtered outputs —
    # no extra backend calls needed for blog 2).
    blog2_html = f"""
<div style="background:#0A3D1F;color:#AAFF3C;padding:16px;border-radius:8px;font-family:Arial,sans-serif;text-align:center">
  <h2 style="margin:0;font-size:24px">⚽ Kickwise Standard Picks</h2>
  <p style="margin:4px 0;color:#fff">{today_display}</p>
  <p style="margin:4px 0;font-size:12px;color:#aaa">Filtered picks meeting the standard | Data from SoccerStats</p>
</div>
"""
    blog2_matches = 0
    blog2_current_time = None

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

            # NEW — check blog 2's standard on this same match, using the
            # same already-fetched prediction (no extra backend call).
            # This is now the ONLY blog 2 standard — the old Prediction-2
            # and Prediction-3-gate standards were both retired in favor
            # of this odds-floor + O/U-result + Prediction-3-contains-Away
            # rule.
            if BLOG2_ENABLED and meets_blog2_standard(pred):
                if match_time != blog2_current_time:
                    blog2_current_time = match_time
                    blog2_html += f'\n<div style="background:#2C3E50;color:#AAFF3C;padding:8px 12px;margin:16px 0 4px;border-radius:4px;font-family:Arial;font-weight:bold;font-size:15px">🕐 {match_time}</div>\n'
                blog2_html += match_html
                blog2_matches += 1

    if total_matches == 0:
        print("No matches found today.")
        return

    print(f"\n📊 Summary: {total_matches} posted | {na_matches} skipped (genuine N/A) | {failed_matches} dropped (request failed after retries)")
    if BLOG2_ENABLED:
        print(f"📊 Blog 2 (standard picks): {blog2_matches} of {total_matches} matches qualified")

    all_html += f'\n<p style="text-align:center;color:#888;font-size:12px;margin-top:20px">Generated by Kickwise | {today_display} | {total_matches} matches processed</p>'

    print(f"\n📝 Posting {total_matches} matches to Blogger...")
    access_token = get_access_token()
    title = f"⚽ Kickwise Predictions — {today_display}"
    status, result = post_to_blogger(access_token, BLOG_ID, title, all_html)

    if status == 200:
        print(f"✅ Posted successfully! URL: {result.get('url','')}")
    else:
        print(f"❌ Failed to post: {status} — {result}")

    # NEW — post to blog 2, only if enabled and at least one match qualified
    if BLOG2_ENABLED:
        if blog2_matches == 0:
            print("\nℹ️ Blog 2: no matches met the standard today — skipping post.")
        else:
            blog2_html += f'\n<p style="text-align:center;color:#888;font-size:12px;margin-top:20px">Generated by Kickwise | {today_display} | {blog2_matches} matches processed</p>'
            print(f"\n📝 Posting {blog2_matches} matches to Blog 2...")
            access_token_2 = get_access_token(CLIENT_ID_2, CLIENT_SECRET_2, REFRESH_TOKEN_2)
            title_2 = f"⚽ Kickwise Standard Picks — {today_display}"
            status_2, result_2 = post_to_blogger(access_token_2, BLOG_ID_2, title_2, blog2_html)

            if status_2 == 200:
                print(f"✅ Blog 2 posted successfully! URL: {result_2.get('url','')}")
                send_telegram_notification(
                    f"⚽ Kickwise Standard Picks posted! {blog2_matches} match(es) today.\n{result_2.get('url','')}"
                )
            else:
                print(f"❌ Blog 2 failed to post: {status_2} — {result_2}")

if __name__ == "__main__":
    main()
