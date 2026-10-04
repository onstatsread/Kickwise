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

BLOG_ID_2       = os.environ.get("BLOG_ID_2")
CLIENT_ID_2     = os.environ.get("GOOGLE_CLIENT_ID_2")
CLIENT_SECRET_2 = os.environ.get("GOOGLE_CLIENT_SECRET_2")
REFRESH_TOKEN_2 = os.environ.get("GOOGLE_REFRESH_TOKEN_2")
BLOG2_ENABLED = all([BLOG_ID_2, CLIENT_ID_2, CLIENT_SECRET_2, REFRESH_TOKEN_2])

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_ENABLED = all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID])


TELEGRAM_MAX_LEN = 4096
_TELEGRAM_SPLIT_BUFFER = 60


def _split_telegram_message(message, max_len=TELEGRAM_MAX_LEN - _TELEGRAM_SPLIT_BUFFER):
    if len(message) <= max_len:
        return [message]

    paragraphs = message.split("\n\n")
    chunks = []
    current = ""
    for p in paragraphs:
        candidate = f"{current}\n\n{p}" if current else p
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(p) <= max_len:
                current = p
            else:
                for i in range(0, len(p), max_len):
                    chunks.append(p[i:i + max_len])
                current = ""
    if current:
        chunks.append(current)
    return chunks


def send_telegram_notification(message):
    if not TELEGRAM_ENABLED:
        return

    chunks = _split_telegram_message(message)
    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        text = chunk if total == 1 else f"(part {i}/{total})\n\n{chunk}"
        try:
            requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                params={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                timeout=15,
            )
        except Exception as e:
            print(f"⚠️ Telegram notification failed (part {i}/{total}): {e}")

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
    "England - Southern Football League": "englandsouthern",
    "Germany - Bundesliga": "germany",
    "Belgium - First Amateur Division": "belgium",
    "Algeria - Ligue 1": "algeria",
    "Australia - A-League": "australia",
    "Australia - Brisbane Premier League": "australiabrisbane",
    "Chile - Primera B": "chile2",
    "Bolivia - LFPB": "bolivia",
    "Greece - Super League 2": "greece2",
    "Estonia - Esiliiga": "estonia2",
    "Iceland - Division 2": "iceland3",
    "Greece - Football League": "greece3",
    "India - I-League": "indiail",
    "India - Super League": "indiaisl",
    "Jamaica - National Premier League": "jamaica",
    "Iran - Azadegan League": "iranazadegan",
    "Kenya - Premier League": "kenya",
    "Jordan - League": "jordan",
    "Morocco - Botola": "morocco",
    "Singapore - S.League": "singapore",
    "New Zealand - Championship": "newzealand",
    "Syria - Premier League": "syria",
    "Thailand - League 1": "thailand",
    "Vietnam - V.League 1": "vietnam",
    "Taiwan - Premier League": "taiwan",
    "Turkmenistan - Higher League": "turkmenistan",
    "Tajikistan - Higher League": "tajikistan",
}

def get_access_token(client_id=None, client_secret=None, refresh_token=None):
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id or CLIENT_ID,
        "client_secret": client_secret or CLIENT_SECRET,
        "refresh_token": refresh_token or REFRESH_TOKEN,
        "grant_type": "refresh_token"
    })
    return resp.json().get("access_token")

def get_fixtures(league_code, date_str):
    try:
        r = requests.get(f"{BACKEND_URL}/fixtures",
                        params={"league": league_code, "date": date_str},
                        timeout=90)
        data = r.json()
        return data.get("matches", [])
    except Exception as e:
        print(f"    ⚠️ get_fixtures failed for {league_code}: {e}")
        return []

def get_prediction(league_code, home, away, retries=2):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(f"{BACKEND_URL}/predict",
                            params={"league": league_code, "home": home, "away": away},
                            timeout=180)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_error = e
            if attempt < retries:
                print(f"    ⏳ Attempt {attempt} failed for {home} vs {away} ({e}) — retrying...")
    print(f"    ❌ Failed to get prediction for {home} vs {away} after {retries} attempts: {last_error}")
    return None

def build_pred1_text(pred):
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

    return pred1


def build_pred2_text(pred):
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


def _pct(d, key):
    """' (NN%)' when the key is present, '' when the data source did not supply it."""
    v = d.get(key)
    return f" ({v}%)" if v is not None else ""


def _ou_text(label, d):
    return (f"{label} Over {d.get('over_odds')}{_pct(d, 'over_pct')} / "
            f"Under {d.get('under_odds')}{_pct(d, 'under_pct')}")


def format_match_html(league_name, match, pred):
    if not pred:
        return ""

    time_str = match.get("time", "TBD")
    home = match["home"]
    away = match["away"]

    pred1 = build_pred1_text(pred)

    pred2 = build_pred2_text(pred)

    pred3 = pred.get("prediction_3", "")

    odds = pred.get("odds") or pred.get("oddsr") or {}
    odds_html = ""
    if odds and odds.get("home_odds"):
        odds_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;font-size:12px;color:#888">
            📊 Odds: Home {odds.get('home_odds')}{_pct(odds, 'home_pct')} |
            Draw {odds.get('draw_odds')}{_pct(odds, 'draw_pct')} |
            Away {odds.get('away_odds')}{_pct(odds, 'away_pct')}
          </td>
        </tr>"""

    market_odds = pred.get("market_odds") or {}
    market_html = ""
    if market_odds and market_odds.get("home_odds"):
        market_html = f"""
        <tr>
          <td colspan="2" style="padding:6px 12px;font-size:12px;color:#888">
            💰 Market Odds: Home {market_odds.get('home_odds')}{_pct(market_odds, 'home_pct')} |
            Draw {market_odds.get('draw_odds')}{_pct(market_odds, 'draw_pct')} |
            Away {market_odds.get('away_odds')}{_pct(market_odds, 'away_pct')}
          </td>
        </tr>"""

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

    ou25 = pred.get("ou25") or {}
    market_ou25 = pred.get("market_ou25") or {}
    ou25_html = ""
    if ou25.get("over_odds") or market_ou25.get("over_odds"):
        parts = []
        if ou25.get("over_odds"):
            parts.append(_ou_text("Model", ou25))
        if market_ou25.get("over_odds"):
            parts.append(_ou_text("Market", market_ou25))
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

# ============================================================
# STANDARD PICKS RISK FLAGS — shown on the Telegram "Kickwise
# Standard Picks" cards. WARNING ONLY: no pick is removed and the
# Blog 2 post is unchanged.
#
# Built from the Oct 2026 review of 49 Standard Picks (target: under
# 2.5 goals). Each flag below is one point of risk:
#   1. B46 says 4goals or 5goals
#   2. D64 says "both"
#   3. Underdog's model odds are 3.8 or higher (one clear favourite)
# Levels: 0-1 flags = OK | 2 flags = CAREFUL | 3 flags = AVOID
# (3 flags went over 2.5 in ~8 of 10 picks.)
# Good sign: B120 says "under" (0 of 7 picks went over so far).
#
# NOTE: cut-offs were chosen by looking at the same picks, over ~3
# weeks. Re-check against new results before relying on them.
# ============================================================
STANDARD_RISK_B46_LABELS   = ("4goals", "5goals")
STANDARD_RISK_DOG_ODDS_MIN = 3.8


def standard_pick_flags(pred):
    flags = []

    b46 = pred.get("b46") or pred.get("b46r") or ""
    if str(b46).replace(" ", "").lower() in STANDARD_RISK_B46_LABELS:
        flags.append(f"B46 {b46}")

    if str(pred.get("d64") or "").strip().lower() == "both":
        flags.append("D64 both")

    model_odds = pred.get("odds") or pred.get("oddsr") or {}
    home_o, away_o = model_odds.get("home_odds"), model_odds.get("away_odds")
    if home_o is not None and away_o is not None:
        dog = max(home_o, away_o)
        if dog >= STANDARD_RISK_DOG_ODDS_MIN:
            flags.append(f"underdog odds {dog}")

    return flags


def standard_pick_risk_line(pred):
    flags = standard_pick_flags(pred)
    n = len(flags)
    if n >= 3:
        level = "🚩 AVOID"
    elif n == 2:
        level = "⚠️ CAREFUL"
    else:
        level = "✅ OK"
    line = f"{level} — {n}/3 flags"
    if flags:
        line += ": " + "; ".join(flags)
    if str(pred.get("b120") or "").strip().lower() == "under":
        line += "\n👍 B120 says under (good sign)"
    return line


# ============================================================
# WATCH LIST 3 UNDER — a pick list for goals UNDER, built on B46.
# Built from the Oct 2026 review of all scored matches:
#   B46 says 2goals or less / 3goals  AND  both teams' model odds
#   are under 3.0 (an evenly matched game, no clear underdog)
#   -> 34 matches, 68% finished under 2.5 goals (all matches: 45%;
#   B46 3goals or less alone: 53%). Held in the early, later and
#   newest matches.
# NOTE: only 34 matches (margin of error ~ +/-16 points) and the
# 3.0 cut-off was set after looking at the data. Re-check against
# new results before relying on it. Nothing else is changed.
# ============================================================
UNDER3_B46_LABELS   = ("2goalsorless", "3goals")
UNDER3_MAX_DOG_ODDS = 3.0   # both teams' model odds must be below this


def check_under_list_3(pred):
    """Returns True when the match qualifies for Watch List 3 UNDER."""
    b46 = pred.get("b46") or pred.get("b46r") or ""
    if str(b46).replace(" ", "").lower() not in UNDER3_B46_LABELS:
        return False
    model_odds = pred.get("odds") or pred.get("oddsr") or {}
    home_o, away_o = model_odds.get("home_odds"), model_odds.get("away_odds")
    if home_o is None or away_o is None:
        return False
    return max(home_o, away_o) < UNDER3_MAX_DOG_ODDS


# ============================================================
# WATCH LIST 4 OVER — a pick list for goals OVER 2.5, built on B46.
# Built from the Oct 2026 review of all scored matches:
#   B46 says 4goals or 5goals  AND  model draw odds are 4.5 or
#   higher  AND  O/U result is "under confirmed" or "over"
#   -> 78 matches, 68% finished over 2.5 goals (all matches: 55%;
#   B46 4goals/5goals alone: 60%). Held in the early, later and
#   newest matches.
# NOTE: edge is ~13 points with a margin of error of ~ +/-10 points,
# and the 4.5 cut-off was set after looking at the data. Re-check
# against new results before relying on it. Nothing else is changed.
# ============================================================
OVER4_B46_LABELS     = ("4goals", "5goals")
OVER4_MIN_DRAW_ODDS  = 4.5
OVER4_OU_RESULTS     = ("under confirmed", "over")


def check_over_list_4(pred):
    """Returns True when the match qualifies for Watch List 4 OVER."""
    b46 = pred.get("b46") or pred.get("b46r") or ""
    if str(b46).replace(" ", "").lower() not in OVER4_B46_LABELS:
        return False
    model_odds = pred.get("odds") or pred.get("oddsr") or {}
    draw_o = model_odds.get("draw_odds")
    if draw_o is None or draw_o < OVER4_MIN_DRAW_ODDS:
        return False
    ou_result = (pred.get("ou25_value_signal") or {}).get("result")
    ou_result = ou_result.strip().lower() if isinstance(ou_result, str) else ""
    return ou_result in OVER4_OU_RESULTS


def meets_blog2_standard(pred):
    model_odds = pred.get("odds") or pred.get("oddsr") or {}
    market_odds = pred.get("market_odds") or {}

    odds_to_check = [
        model_odds.get("home_odds"), model_odds.get("draw_odds"), model_odds.get("away_odds"),
        market_odds.get("home_odds"), market_odds.get("draw_odds"), market_odds.get("away_odds"),
    ]
    if any(o is None for o in odds_to_check):
        return False
    if any(o < 1.45 for o in odds_to_check):
        return False

    ou25_value_signal = pred.get("ou25_value_signal") or {}
    result = ou25_value_signal.get("result", "")
    if result not in ("under", "under confirmed"):
        return False

    prediction_3 = (pred.get("prediction_3") or "").lower()
    if "away" not in prediction_3:
        return False

    value_pct = pred.get("value_pct") or {}
    hda_values = [value_pct.get("home"), value_pct.get("draw"), value_pct.get("away")]
    if any(v is not None and abs(v) >= 98 for v in hda_values):
        return False

    return True


# ============================================================
# NEW — shared extra gate applied to ALL THREE existing Double
# Chance signals (1, 2, 3), on top of each signal's own existing
# logic. Rules, as specified:
#   1. If either home_v or away_v (value_pct) is greater than 65,
#      the match is disqualified.
#   2. Requires home_v and away_v to have opposite signs, with
#      neither equal to 0 (one strictly positive, one strictly
#      negative) — this is what makes "the positive one divided by
#      the negative one" meaningful in the first place.
#   3. Ratio = positive_value / abs(negative_value). If that ratio is
#      greater than 1.7, disqualified; otherwise the match qualifies
#      (passes this filter).
# ============================================================
def passes_double_chance_extra_filter(value_pct):
    if not value_pct:
        return False

    home_v = value_pct.get("home")
    away_v = value_pct.get("away")

    if home_v is None or away_v is None:
        return False

    # Rule 1
    if home_v > 65 or away_v > 65:
        return False

    # Rule 2 — one strictly positive, one strictly negative, neither zero.
    if home_v == 0 or away_v == 0:
        return False
    if (home_v > 0) == (away_v > 0):
        return False

    positive_val = home_v if home_v > 0 else away_v
    negative_val = home_v if home_v < 0 else away_v

    ratio = positive_val / abs(negative_val)

    if ratio <= 0:
        return False

    # Rule 3
    if ratio > 1.7:
        return False

    return True


# ============================================================
# NEW — additional criterion applied on top of
# passes_double_chance_extra_filter, to ALL THREE Double Chance
# signals. Divides the POSITIVE value_pct side by the absolute value
# of the NEGATIVE side; only passes when that ratio is LESS THAN 1
# (i.e. the positive side's magnitude is smaller than the negative
# side's). Confirmed via real examples given directly:
#     home_v=-45.2, away_v=30  -> 30/45.2  = 0.66 -> PASS (< 1)
#     home_v=48,    away_v=-40 -> 48/40    = 1.2  -> FAIL (>= 1)
# NOTE: this is stricter than passes_double_chance_extra_filter's own
# existing ratio<=1.7 cutoff — nothing between 1.0 and 1.7 will pass
# this new check even though it passes the old one, so this
# effectively replaces the practical effect of that older threshold
# for these three signals, not just adds to it. Re-derives its own
# positive/negative split rather than trusting a value computed
# elsewhere, so it stays self-contained and safe to call on its own.
# ============================================================
def passes_dominance_ratio_filter(value_pct):
    if not value_pct:
        return False

    home_v = value_pct.get("home")
    away_v = value_pct.get("away")

    if home_v is None or away_v is None:
        return False
    if home_v == 0 or away_v == 0:
        return False
    if (home_v > 0) == (away_v > 0):
        return False

    positive_val = home_v if home_v > 0 else away_v
    negative_val = home_v if home_v < 0 else away_v

    ratio = positive_val / abs(negative_val)

    return ratio < 1


def check_double_chance_signal(pred):
    # NEW condition — the model's H/D/A odds must all be no bigger than
    # 15 for the match to qualify at all, checked before the value-pct
    # conditions below.
    model_odds = pred.get("odds") or {}
    home_odds = model_odds.get("home_odds")
    draw_odds = model_odds.get("draw_odds")
    away_odds = model_odds.get("away_odds")

    if home_odds is None or draw_odds is None or away_odds is None:
        return None
    if home_odds > 15 or draw_odds > 15 or away_odds > 15:
        return None

    value_pct = pred.get("value_pct") or {}
    home_v = value_pct.get("home")
    away_v = value_pct.get("away")
    draw_v = value_pct.get("draw")
    if home_v is None or away_v is None or draw_v is None:
        return None

    home_qualifies = home_v < -40
    away_qualifies = away_v < -40

    if home_qualifies and away_qualifies:
        return None
    if not home_qualifies and not away_qualifies:
        return None

    if home_qualifies:
        return "home" if abs(home_v) > abs(draw_v) else None
    else:
        return "away" if abs(away_v) > abs(draw_v) else None


def refine_double_chance_signal(pred, side):
    value_pct = pred.get("value_pct") or {}
    side_v = value_pct.get(side)

    market_odds = pred.get("market_odds") or {}
    if side == "home":
        selected_odd = market_odds.get("home_odds")
        opposite_odd = market_odds.get("away_odds")
        side_label = "Home"
    else:
        selected_odd = market_odds.get("away_odds")
        opposite_odd = market_odds.get("home_odds")
        side_label = "Away"

    if side_v is None or not selected_odd or not opposite_odd:
        return None

    ratio = abs(side_v) / ((selected_odd / opposite_odd) * 10)

    if ratio <= 2.4:
        return f"{side_label} 3-handicap"
    elif ratio <= 5:
        return f"{side_label} 2-handicap"
    else:
        return f"{side_label} win or draw"


def check_double_chance_signal_2(pred):
    model_odds = pred.get("odds") or {}
    home_odds = model_odds.get("home_odds")
    away_odds = model_odds.get("away_odds")

    if home_odds is None or away_odds is None:
        return None

    if not (1.5 <= home_odds <= 4.00):
        return None
    if not (1.5 <= away_odds <= 4.00):
        return None

    value_pct = pred.get("value_pct") or {}
    home_v = value_pct.get("home")
    away_v = value_pct.get("away")

    if home_v is None or away_v is None:
        return None

    if home_v < 0 and away_v < 0:
        return None
    if home_v > 0 and away_v > 0:
        return None
    if home_v == 0 or away_v == 0:
        return None

    decision = (pred.get("value_signal") or {}).get("decision", "")
    decision_lower = decision.lower()

    if home_v < 0:
        return decision if "home" in decision_lower else None
    else:
        return decision if "away" in decision_lower else None


def check_double_chance_signal_3(pred):
    # Step 4 gate — must pass before anything else is checked.
    model_odds = pred.get("odds") or {}
    home_odds = model_odds.get("home_odds")
    draw_odds = model_odds.get("draw_odds")
    away_odds = model_odds.get("away_odds")

    if home_odds is None or draw_odds is None or away_odds is None:
        return None
    if home_odds > 15 or draw_odds > 15 or away_odds > 15:
        return None

    # Step 2 — candidate side from the H/D/A decision.
    decision = (pred.get("value_signal") or {}).get("decision", "")
    decision_lower = decision.lower()

    if "home" in decision_lower:
        side = "home"
    elif "away" in decision_lower:
        side = "away"
    else:
        return None

    # Steps 1 & 3 — confirm the side appears in Prediction 1, 2, or 3.
    pred1_text = build_pred1_text(pred).lower()
    pred2_text = build_pred2_text(pred).lower()
    pred3_text = (pred.get("prediction_3") or "").lower()

    confirmed = (
        side in pred1_text
        or side in pred2_text
        or side in pred3_text
    )
    if not confirmed:
        return None

    # Final label — compare the selected side's model odd against the
    # opposite side's (home vs away only, draw not involved here).
    selected_odd = home_odds if side == "home" else away_odds
    opposite_odd = away_odds if side == "home" else home_odds
    side_label = side.capitalize()

    if selected_odd < opposite_odd:
        return side_label
    else:
        return f"{side_label} 2-handicap"


# ============================================================
# WATCH LIST 1 — red-flag checklist for the bot's own 2-handicap
# picks. A match is flagged when ANY of the four flags below is
# true. Built from the Sep 2026 review of Telegram 2-handicap picks
# (66 scored picks, 11 went over their B46 goal count).
#
# Flags:
#   1. Draw value (value_pct["draw"]) is -15% or lower
#   2. Model draw odds are 4.5 or higher
#   3. O/U 2.5 result says "under confirmed" or "over"
#   4. The match is in Sweden or Ireland
#
# NOTE: these patterns were found on a small sample (3 weeks) and did
# not repeat on the other 2-handicap matches, so treat them as a
# WATCH LIST (extra caution), not a hard filter. Nothing is removed
# from the other signals — this is a separate notification.
# ============================================================
WATCHLIST1_DRAW_VALUE_MAX   = -15    # flag when draw value <= this (%)
WATCHLIST1_DRAW_ODDS_MIN    = 4.5    # flag when model draw odds >= this
WATCHLIST1_OU_RESULTS       = ("under confirmed", "over")
WATCHLIST1_COUNTRIES        = ("sweden", "ireland")
# True  = only check matches already picked as a 2-handicap by
#         Double Chance signal 1, 2 or 3 (what the review was based on).
# False = check every match that was processed.
WATCHLIST1_ONLY_2HC_PICKS   = True


def check_watch_list_1(pred, league_name):
    flags = []

    value_pct = pred.get("value_pct") or {}
    draw_v = value_pct.get("draw")
    if draw_v is not None and draw_v <= WATCHLIST1_DRAW_VALUE_MAX:
        flags.append(f"Draw value {draw_v}%")

    model_odds = pred.get("odds") or {}
    draw_odds = model_odds.get("draw_odds")
    if draw_odds is not None and draw_odds >= WATCHLIST1_DRAW_ODDS_MIN:
        flags.append(f"Draw odds {draw_odds}")

    ou_result = (pred.get("ou25_value_signal") or {}).get("result")
    ou_result = ou_result.strip().lower() if isinstance(ou_result, str) else ""
    if ou_result in WATCHLIST1_OU_RESULTS:
        flags.append(f"O/U result: {ou_result}")

    country = (league_name or "").split(" - ")[0].strip().lower()
    if country in WATCHLIST1_COUNTRIES:
        flags.append(f"Country: {country.title()}")

    return flags


# ============================================================
# WATCH LIST 2 — red-flag checklist for the bot's own Double Chance /
# handicap picks (DC1, DC2, DC3 — every label, not only 2-handicap).
# It is about the PICKED TEAM LOSING (not about goals).
# Built from the Sep 2026 review of 84 scored picks: 0 flags ->
# 23 picks, 1 lost; 1+ flags -> far more losses.
#
# Flags:
#   A. Picked team's model odds are 6 or higher (big underdog)
#   B. Draw value is -20% or lower, OR model draw odds are 4.5+
#   C. Opposing team's value is 25% or more
#
# Levels: 0 flags = CLEAN | 1 flag = CAUTION | 2-3 flags = DANGER
#
# NOTE: cut-offs were chosen by looking at the same picks they were
# tested on, over ~3 weeks. Treat as a warning only. Nothing is
# removed from the other signals.
# ============================================================
WATCHLIST2_PICK_ODDS_MIN   = 6
WATCHLIST2_DRAW_VALUE_MAX  = -20
WATCHLIST2_DRAW_ODDS_MIN   = 4.5
WATCHLIST2_OPP_VALUE_MIN   = 25


def check_watch_list_2(pred, side):
    """side is 'home' or 'away' (the picked team). Returns a list of flags."""
    flags = []
    side = (side or "").lower()
    if side not in ("home", "away"):
        return flags
    opp = "away" if side == "home" else "home"

    model_odds = pred.get("odds") or {}
    value_pct = pred.get("value_pct") or {}

    pick_odds = model_odds.get(f"{side}_odds")
    if pick_odds is not None and pick_odds >= WATCHLIST2_PICK_ODDS_MIN:
        flags.append(f"A: pick odds {pick_odds}")

    draw_v = value_pct.get("draw")
    draw_odds = model_odds.get("draw_odds")
    b_parts = []
    if draw_v is not None and draw_v <= WATCHLIST2_DRAW_VALUE_MAX:
        b_parts.append(f"draw value {draw_v}%")
    if draw_odds is not None and draw_odds >= WATCHLIST2_DRAW_ODDS_MIN:
        b_parts.append(f"draw odds {draw_odds}")
    if b_parts:
        flags.append("B: " + ", ".join(b_parts))

    opp_v = value_pct.get(opp)
    if opp_v is not None and opp_v >= WATCHLIST2_OPP_VALUE_MIN:
        flags.append(f"C: opponent value {opp_v}%")

    return flags


def post_to_blogger(access_token, blog_id, title, content):
    resp = requests.post(
        f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/",
        headers={"Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/json"},
        json={"title": title, "content": content}
    )
    return resp.status_code, resp.json()

def main():
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

    all_matches = []
    seen_matches = set()
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

    def sort_key(m):
        t = m["fix"].get("time", "")
        if not t or t == "TBD":
            return "99:99"
        return t
    all_matches.sort(key=sort_key)

    total_matches = 0
    failed_matches = 0
    na_matches = 0
    current_time = None

    blog2_html = f"""
<div style="background:#0A3D1F;color:#AAFF3C;padding:16px;border-radius:8px;font-family:Arial,sans-serif;text-align:center">
  <h2 style="margin:0;font-size:24px">⚽ Kickwise Standard Picks</h2>
  <p style="margin:4px 0;color:#fff">{today_display}</p>
  <p style="margin:4px 0;font-size:12px;color:#aaa">Filtered picks meeting the standard | Data from SoccerStats</p>
</div>
"""
    blog2_matches = 0
    blog2_current_time = None
    blog2_notify_cards = []
    dc_notify_cards = []
    dc2_notify_cards = []  # per-match info for the double-chance signal 2 notification
    dc3_notify_cards = []  # per-match info for the double-chance signal 3 notification
    wl1_notify_cards = []  # per-match info for watch list 1 notification
    wl1_clean_cards = []   # 2-handicap picks with NO watch list 1 flags
    wl2_danger_cards = []   # watch list 2: 2-3 flags
    wl2_caution_cards = []  # watch list 2: 1 flag
    wl2_clean_cards = []    # watch list 2: 0 flags
    under3_cards = []       # watch list 3 UNDER picks
    over4_cards = []        # watch list 4 OVER picks

    for m in all_matches:
        pred = get_prediction(m["code"], m["fix"]["home"], m["fix"]["away"])

        if pred is None:
            failed_matches += 1
            continue

        if all(
            (pred.get(k) or "N/A") in ("N/A", "", "None")
            for k in ["d70", "b120", "c120", "d64", "b46"]
        ):
            print(f"    ⚠️ Skipping {m['fix']['home']} vs {m['fix']['away']} (all N/A)")
            na_matches += 1
            continue
        try:
            match_html = format_match_html(m["league_name"], m["fix"], pred)
        except Exception as e:
            # one malformed prediction must not stop the whole daily run
            print(f"    ❌ Could not format {m['fix']['home']} vs {m['fix']['away']}: {type(e).__name__}: {e}")
            failed_matches += 1
            continue
        if match_html:
            match_time = m["fix"].get("time", "TBD")
            if match_time != current_time:
                current_time = match_time
                all_html += f'\n<div style="background:#2C3E50;color:#AAFF3C;padding:8px 12px;margin:16px 0 4px;border-radius:4px;font-family:Arial;font-weight:bold;font-size:15px">🕐 {match_time}</div>\n'
            all_html += match_html
            total_matches += 1

            if BLOG2_ENABLED and meets_blog2_standard(pred):
                if match_time != blog2_current_time:
                    blog2_current_time = match_time
                    blog2_html += f'\n<div style="background:#2C3E50;color:#AAFF3C;padding:8px 12px;margin:16px 0 4px;border-radius:4px;font-family:Arial;font-weight:bold;font-size:15px">🕐 {match_time}</div>\n'
                blog2_html += match_html
                blog2_matches += 1

                value_signal = pred.get("value_signal") or {}
                ou25_value_signal = pred.get("ou25_value_signal") or {}
                b46_out = pred.get("b46") or pred.get("b46r") or "—"
                blog2_notify_cards.append(
                    f"🕐 {match_time} | {m['league_name']}\n"
                    f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                    f"⚡ Decision: {value_signal.get('decision') or '—'}\n"
                    f"📋 B46: {b46_out}\n"
                    f"📈 O/U Result: {ou25_value_signal.get('result') or '—'}\n"
                    f"🧭 Prediction 3: {pred.get('prediction_3') or '—'}\n"
                    f"{standard_pick_risk_line(pred)}"
                )

            value_pct = pred.get("value_pct") or {}

            wl1_signals = []  # 2-handicap picks from DC1/DC2/DC3 for this match
            wl2_signals = []  # ALL picks from DC1/DC2/DC3 for this match

            dc_side = check_double_chance_signal(pred)
            if dc_side:
                dc_signal = refine_double_chance_signal(pred, dc_side)
                if (
                    dc_signal
                    and passes_double_chance_extra_filter(value_pct)
                    and passes_dominance_ratio_filter(value_pct)
                ):
                    wl2_signals.append(f"DC1: {dc_signal}")
                    if "2-handicap" in dc_signal:
                        wl1_signals.append(f"DC1: {dc_signal}")
                    dc_notify_cards.append(
                        f"🕐 {match_time} | {m['league_name']}\n"
                        f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                        f"🎯 Signal: {dc_signal}\n"
                        f"📈 Value: Home {value_pct.get('home')}% | "
                        f"Draw {value_pct.get('draw')}% | "
                        f"Away {value_pct.get('away')}%"
                    )

            dc2_result = check_double_chance_signal_2(pred)
            if (
                dc2_result
                and passes_double_chance_extra_filter(value_pct)
                and passes_dominance_ratio_filter(value_pct)
            ):
                wl2_signals.append(f"DC2: {dc2_result}")
                if "2-handicap" in dc2_result:
                    wl1_signals.append(f"DC2: {dc2_result}")
                model_odds = pred.get("odds") or {}
                dc2_notify_cards.append(
                    f"🕐 {match_time} | {m['league_name']}\n"
                    f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                    f"🎯 Signal: {dc2_result}\n"
                    f"💰 Model Odds: Home {model_odds.get('home_odds')} | "
                    f"Away {model_odds.get('away_odds')}\n"
                    f"📈 Value: Home {value_pct.get('home')}% | "
                    f"Away {value_pct.get('away')}%"
                )

            dc3_result = check_double_chance_signal_3(pred)
            if (
                dc3_result
                and passes_double_chance_extra_filter(value_pct)
                and passes_dominance_ratio_filter(value_pct)
            ):
                wl2_signals.append(f"DC3: {dc3_result}")
                if "2-handicap" in dc3_result:
                    wl1_signals.append(f"DC3: {dc3_result}")
                model_odds = pred.get("odds") or {}
                value_signal = pred.get("value_signal") or {}
                dc3_notify_cards.append(
                    f"🕐 {match_time} | {m['league_name']}\n"
                    f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                    f"🎯 Signal: {dc3_result}\n"
                    f"⚡ H/D/A Decision: {value_signal.get('decision') or '—'}\n"
                    f"💰 Model Odds: Home {model_odds.get('home_odds')} | "
                    f"Draw {model_odds.get('draw_odds')} | "
                    f"Away {model_odds.get('away_odds')}"
                )

            # Watch List 1 — red flags on the bot's own 2-handicap picks
            if wl1_signals or not WATCHLIST1_ONLY_2HC_PICKS:
                wl1_flags = check_watch_list_1(pred, m["league_name"])
                if not wl1_flags and wl1_signals:
                    # 2-handicap pick with no red flags -> clean list
                    b46_clean = pred.get("b46") or pred.get("b46r") or "—"
                    wl1_clean_cards.append(
                        f"🕐 {match_time} | {m['league_name']}\n"
                        f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                        f"🎯 Picks: {' | '.join(wl1_signals)}\n"
                        f"📋 B46: {b46_clean}"
                    )
                if wl1_flags:
                    model_odds = pred.get("odds") or {}
                    value_pct = pred.get("value_pct") or {}
                    b46_wl = pred.get("b46") or pred.get("b46r") or "—"
                    picks_line = " | ".join(wl1_signals) if wl1_signals else "—"
                    wl1_notify_cards.append(
                        f"🕐 {match_time} | {m['league_name']}\n"
                        f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                        f"🎯 Picks: {picks_line}\n"
                        f"📋 B46: {b46_wl}\n"
                        f"🚩 Flags: {', '.join(wl1_flags)}\n"
                        f"💰 Draw: odds {model_odds.get('draw_odds')} | "
                        f"value {value_pct.get('draw')}%"
                    )

            # Watch List 2 — red flags on every DC1/DC2/DC3 pick
            if wl2_signals:
                wl2_side = wl2_signals[0].split(": ", 1)[1].split()[0].lower()
                wl2_flags = check_watch_list_2(pred, wl2_side)
                b46_wl2 = pred.get("b46") or pred.get("b46r") or "—"
                wl2_head = (
                    f"🕐 {match_time} | {m['league_name']}\n"
                    f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                    f"🎯 Picks: {' | '.join(wl2_signals)}\n"
                    f"📋 B46: {b46_wl2}"
                )
                if len(wl2_flags) >= 2:
                    wl2_danger_cards.append(wl2_head + f"\n🚩 Flags ({len(wl2_flags)}): " + "; ".join(wl2_flags))
                elif len(wl2_flags) == 1:
                    wl2_caution_cards.append(wl2_head + f"\n⚠️ Flag (1): {wl2_flags[0]}")
                else:
                    wl2_clean_cards.append(wl2_head)


            # Watch List 3 UNDER — B46 3goals or less + evenly matched game
            if check_under_list_3(pred):
                u3_odds = pred.get("odds") or pred.get("oddsr") or {}
                u3_b46 = pred.get("b46") or pred.get("b46r") or "—"
                under3_cards.append(
                    f"🕐 {match_time} | {m['league_name']}\n"
                    f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                    f"📋 B46: {u3_b46}\n"
                    f"💰 Model Odds: Home {u3_odds.get('home_odds')} | "
                    f"Draw {u3_odds.get('draw_odds')} | "
                    f"Away {u3_odds.get('away_odds')}"
                )


            # Watch List 4 OVER — B46 4goals+ + high draw odds + O/U confirms
            if check_over_list_4(pred):
                o4_odds = pred.get("odds") or pred.get("oddsr") or {}
                o4_b46 = pred.get("b46") or pred.get("b46r") or "—"
                o4_ou = (pred.get("ou25_value_signal") or {}).get("result") or "—"
                over4_cards.append(
                    f"🕐 {match_time} | {m['league_name']}\n"
                    f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                    f"📋 B46: {o4_b46}\n"
                    f"📈 O/U Result: {o4_ou}\n"
                    f"💰 Model Odds: Home {o4_odds.get('home_odds')} | "
                    f"Draw {o4_odds.get('draw_odds')} | "
                    f"Away {o4_odds.get('away_odds')}"
                )

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
                cards_text = "\n\n".join(blog2_notify_cards)
                notify_message = (
                    f"⚽ Kickwise Standard Picks — {today_display}\n"
                    f"{blog2_matches} match(es) qualified\n\n"
                    f"{cards_text}\n\n"
                    f"{result_2.get('url','')}"
                )
                send_telegram_notification(notify_message)
            else:
                print(f"❌ Blog 2 failed to post: {status_2} — {result_2}")

    if dc_notify_cards:
        dc_cards_text = "\n\n".join(dc_notify_cards)
        dc_message = (
            f"🎯 Kickwise Double Chance Signals — {today_display}\n"
            f"{len(dc_notify_cards)} match(es) flagged\n\n"
            f"{dc_cards_text}"
        )
        send_telegram_notification(dc_message)
    else:
        print("\nℹ️ Double chance signal: no matches flagged today.")

    if dc2_notify_cards:
        dc2_cards_text = "\n\n".join(dc2_notify_cards)
        dc2_message = (
            f"🎯 Kickwise Double Chance Signal 2 — {today_display}\n"
            f"{len(dc2_notify_cards)} match(es) flagged\n\n"
            f"{dc2_cards_text}"
        )
        send_telegram_notification(dc2_message)
    else:
        print("\nℹ️ Double chance signal 2: no matches flagged today.")

    if dc3_notify_cards:
        dc3_cards_text = "\n\n".join(dc3_notify_cards)
        dc3_message = (
            f"🎯 Kickwise Double Chance Signal 3 — {today_display}\n"
            f"{len(dc3_notify_cards)} match(es) flagged\n\n"
            f"{dc3_cards_text}"
        )
        send_telegram_notification(dc3_message)
    else:
        print("\nℹ️ Double chance signal 3: no matches flagged today.")

    if wl1_notify_cards or wl1_clean_cards:
        wl1_parts = [f"⚠️ Kickwise Watch List 1 — {today_display}"]
        if wl1_notify_cards:
            wl1_parts.append(
                f"🚩 FLAGGED — {len(wl1_notify_cards)} match(es), extra caution\n\n"
                + "\n\n".join(wl1_notify_cards)
            )
        else:
            wl1_parts.append("🚩 FLAGGED — none today")
        if wl1_clean_cards:
            wl1_parts.append(
                f"✅ CLEAN (no flags) — {len(wl1_clean_cards)} 2-handicap pick(s)\n\n"
                + "\n\n".join(wl1_clean_cards)
            )
        else:
            wl1_parts.append("✅ CLEAN (no flags) — none today")
        send_telegram_notification("\n\n".join(wl1_parts))
    else:
        print("\nℹ️ Watch list 1: no 2-handicap picks today.")

    if wl2_danger_cards or wl2_caution_cards or wl2_clean_cards:
        wl2_parts = [f"⚠️ Kickwise Watch List 2 — {today_display}"]
        if wl2_danger_cards:
            wl2_parts.append(
                f"🚩 DANGER (2-3 flags) — {len(wl2_danger_cards)} pick(s)\n\n"
                + "\n\n".join(wl2_danger_cards)
            )
        else:
            wl2_parts.append("🚩 DANGER (2-3 flags) — none today")
        if wl2_caution_cards:
            wl2_parts.append(
                f"⚠️ CAUTION (1 flag) — {len(wl2_caution_cards)} pick(s)\n\n"
                + "\n\n".join(wl2_caution_cards)
            )
        else:
            wl2_parts.append("⚠️ CAUTION (1 flag) — none today")
        if wl2_clean_cards:
            wl2_parts.append(
                f"✅ CLEAN (no flags) — {len(wl2_clean_cards)} pick(s)\n\n"
                + "\n\n".join(wl2_clean_cards)
            )
        else:
            wl2_parts.append("✅ CLEAN (no flags) — none today")
        send_telegram_notification("\n\n".join(wl2_parts))
    else:
        print("\nℹ️ Watch list 2: no double chance picks today.")

    if under3_cards:
        under3_message = (
            f"🎯 Kickwise Watch List 3 UNDER — {today_display}\n"
            f"{len(under3_cards)} match(es): B46 3goals or less + evenly matched\n\n"
            + "\n\n".join(under3_cards)
        )
        send_telegram_notification(under3_message)
    else:
        print("\nℹ️ Watch list 3 UNDER: no matches today.")

    if over4_cards:
        over4_message = (
            f"🎯 Kickwise Watch List 4 OVER — {today_display}\n"
            f"{len(over4_cards)} match(es): B46 4goals+ with high draw odds\n\n"
            + "\n\n".join(over4_cards)
        )
        send_telegram_notification(over4_message)
    else:
        print("\nℹ️ Watch list 4 OVER: no matches today.")

if __name__ == "__main__":
    main()
