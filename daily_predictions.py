"""
Kickwise Daily Blog Poster
Runs at 23:00 UTC (midnight Nigeria WAT) via GitHub Actions
Fetches all leagues with matches, runs predictions, posts to Blogger
"""

import requests
import json
import os
import re
from datetime import datetime, timedelta, timezone


# ============================================================
# CONFIGURATION
# ============================================================

BACKEND_URL = os.environ["BACKEND_URL"]
BLOG_ID = os.environ["BLOG_ID"]

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]


# ============================================================
# SECOND BLOG
# ============================================================

BLOG_ID_2 = os.environ.get("BLOG_ID_2")
CLIENT_ID_2 = os.environ.get("GOOGLE_CLIENT_ID_2")
CLIENT_SECRET_2 = os.environ.get("GOOGLE_CLIENT_SECRET_2")
REFRESH_TOKEN_2 = os.environ.get("GOOGLE_REFRESH_TOKEN_2")

BLOG2_ENABLED = all([
    BLOG_ID_2,
    CLIENT_ID_2,
    CLIENT_SECRET_2,
    REFRESH_TOKEN_2
])


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TELEGRAM_ENABLED = all([
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID
])

TELEGRAM_MAX_LEN = 4096
_TELEGRAM_SPLIT_BUFFER = 60


# ============================================================
# TELEGRAM MESSAGE SPLITTER
# ============================================================

def _split_telegram_message(
    message,
    max_len=TELEGRAM_MAX_LEN - _TELEGRAM_SPLIT_BUFFER
):
    """Split long Telegram messages without breaking match cards."""

    if len(message) <= max_len:
        return [message]

    paragraphs = message.split("\n\n")
    chunks = []
    current = ""

    for paragraph in paragraphs:

        candidate = (
            f"{current}\n\n{paragraph}"
            if current
            else paragraph
        )

        if len(candidate) <= max_len:
            current = candidate

        else:

            if current:
                chunks.append(current)

            if len(paragraph) <= max_len:
                current = paragraph

            else:
                for i in range(0, len(paragraph), max_len):
                    chunks.append(
                        paragraph[i:i + max_len]
                    )

                current = ""

    if current:
        chunks.append(current)

    return chunks


# ============================================================
# TELEGRAM NOTIFICATION
# ============================================================

def send_telegram_notification(message):
    """Send Telegram notification without crashing the workflow."""

    if not TELEGRAM_ENABLED:
        print("ℹ️ Telegram notification skipped — not configured.")
        return

    chunks = _split_telegram_message(message)

    total = len(chunks)

    for i, chunk in enumerate(chunks, start=1):

        text = (
            chunk
            if total == 1
            else f"(part {i}/{total})\n\n{chunk}"
        )

        try:

            response = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                params={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text
                },
                timeout=15
            )

            if response.status_code != 200:
                print(
                    f"⚠️ Telegram returned "
                    f"{response.status_code}: {response.text}"
                )

        except Exception as e:

            print(
                f"⚠️ Telegram notification failed "
                f"(part {i}/{total}): {e}"
            )


# ============================================================
# LEAGUES
# ============================================================

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


# ============================================================
# GOOGLE ACCESS TOKEN
# ============================================================

def get_access_token(
    client_id=None,
    client_secret=None,
    refresh_token=None
):
    """Get Google OAuth access token."""

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id or CLIENT_ID,
            "client_secret": client_secret or CLIENT_SECRET,
            "refresh_token": refresh_token or REFRESH_TOKEN,
            "grant_type": "refresh_token"
        },
        timeout=30
    )

    resp.raise_for_status()

    data = resp.json()

    token = data.get("access_token")

    if not token:
        raise RuntimeError(
            f"Google OAuth did not return an access token: {data}"
        )

    return token


# ============================================================
# GET FIXTURES
# ============================================================

def get_fixtures(league_code, date_str):

    try:

        response = requests.get(
            f"{BACKEND_URL}/fixtures",
            params={
                "league": league_code,
                "date": date_str
            },
            timeout=90
        )

        response.raise_for_status()

        data = response.json()

        return data.get("matches", [])

    except Exception as e:

        print(
            f"    ⚠️ get_fixtures failed "
            f"for {league_code}: {e}"
        )

        return []


# ============================================================
# GET PREDICTION
# ============================================================

def get_prediction(
    league_code,
    home,
    away,
    retries=2
):

    last_error = None

    for attempt in range(1, retries + 1):

        try:

            response = requests.get(
                f"{BACKEND_URL}/predict",
                params={
                    "league": league_code,
                    "home": home,
                    "away": away
                },
                timeout=180
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            last_error = e

            if attempt < retries:

                print(
                    f"    ⏳ Attempt {attempt} failed "
                    f"for {home} vs {away} "
                    f"({e}) — retrying..."
                )

    print(
        f"    ❌ Failed to get prediction "
        f"for {home} vs {away} after {retries} attempts: "
        f"{last_error}"
    )

    return None


# ============================================================
# PREDICTION 2
# ============================================================

def build_pred2_text(pred):

    o73 = (
        pred.get("o73")
        or pred.get("o73r")
        or ""
    ).strip()

    d69n = (pred.get("d70") or "").lower()
    d70n = (pred.get("d70val") or "").lower()
    d69r = (pred.get("d70r") or "").lower()
    d70r2 = (pred.get("d70valr") or "").lower()

    o73l = o73.lower()

    b46v = (
        pred.get("b46")
        or pred.get("b46r")
        or ""
    )

    d64_both = "both" in (
        (pred.get("d64", "") or "")
        +
        (pred.get("d64r", "") or "")
    ).lower()

    b120_combined = (
        (pred.get("b120", "") or "")
        + " "
        + (pred.get("b120r", "") or "")
    ).lower()

    b120_double = "double" in b120_combined

    c120_match = lambda v: (
        (v or "").lower().strip() == "match"
        or (
            "match" in (v or "").lower()
            and "not" not in (v or "").lower()
        )
    )

    c120_ok = (
        c120_match(pred.get("c120"))
        or c120_match(pred.get("c120r"))
    )

    r1 = (
        o73l
        and (o73l in d69n or o73l in d69r)
        and (o73l in d70n or o73l in d70r2)
        and b120_double
        and c120_ok
    )

    aa15_not_no = not all(
        (pred.get(k, "") or "").lower() == "no"
        for k in ["aa15", "aa15r"]
    )

    b54_empty = (
        not (pred.get("b54", "") or "").strip()
        and
        not (pred.get("b54r", "") or "").strip()
    )

    o73_in_d69d70 = (
        o73l
        and (
            o73l in d69n
            or o73l in d70n
            or o73l in d69r
            or o73l in d70r2
        )
    )

    r2 = (
        aa15_not_no
        and d64_both
        and b54_empty
        and c120_ok
        and o73_in_d69d70
    )

    pred2 = ""

    all_handicap = all(
        "handicap" in x
        for x in [d69n, d70n, d69r, d70r2]
    )

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

            b118n = (
                pred.get("b118", "") or ""
            ).lower()

            b118r = (
                pred.get("b118r", "") or ""
            ).lower()

            n_home = (
                "home g" in b118n
                or "home c" in b118n
            )

            n_away = (
                "away g" in b118n
                or "away c" in b118n
            )

            r_home = "home" in b118r
            r_away = "away" in b118r

            if (
                (n_home and r_home)
                or
                (n_away and r_away)
            ):
                pred2 += " / same"

    return pred2


# ============================================================
# FORMAT MATCH HTML
# ============================================================

def format_match_html(league_name, match, pred):

    if not pred:
        return ""

    time_str = match.get("time", "TBD")
    home = match["home"]
    away = match["away"]

    # --------------------------------------------------------
    # Prediction 1
    # --------------------------------------------------------

    c120_match = lambda v: (
        (v or "").lower().strip() == "match"
        or (
            "match" in (v or "").lower()
            and "not" not in (v or "").lower()
        )
    )

    d64_both = "both" in (
        (pred.get("d64", "") or "")
        +
        (pred.get("d64r", "") or "")
    ).lower()

    d64_one = "one" in (
        (pred.get("d64", "") or "")
        +
        (pred.get("d64r", "") or "")
    ).lower()

    b120_combined = (
        (pred.get("b120", "") or "")
        + " "
        + (pred.get("b120r", "") or "")
    ).lower()

    b120_double = "double" in b120_combined
    b120_under = "under" in b120_combined

    c120_ok = (
        c120_match(pred.get("c120"))
        or c120_match(pred.get("c120r"))
    )

    d70_main = (
        pred.get("d70")
        if c120_match(pred.get("c120"))
        else (
            pred.get("d70r")
            or pred.get("d70", "")
        )
    )

    pred1 = ""

    if c120_ok:

        labels = []

        if b120_double:
            labels.append(
                "double"
                if d64_both
                else (
                    "2-handicap"
                    if d64_one
                    else None
                )
            )

        if b120_under:
            labels.append(
                "under"
                if d64_both
                else (
                    "under extend"
                    if d64_one
                    else None
                )
            )

        labels = list(
            set(filter(None, labels))
        )

        if labels:

            b46_match = re.search(
                r'(\d+\s*goals)',
                (
                    (pred.get("b46", "") or "")
                    + " "
                    + (pred.get("b46r", "") or "")
                ).lower()
            )

            b46_goals = (
                b46_match.group(1)
                .replace(" ", "")
                if b46_match
                else ""
            )

            aa15_ok = any(
                (
                    pred.get(k, "") or ""
                ).lower()
                in ("yes", "yes1", "both")
                for k in ["aa15", "aa15r"]
            )

            goals_suffix = (
                f" / {b46_goals}"
                if aa15_ok and b46_goals
                else ""
            )

            pred1 = (
                f"{d70_main} / "
                f"{' + '.join(labels)}"
                f"{goals_suffix}"
            )

    # --------------------------------------------------------
    # Prediction 2
    # --------------------------------------------------------

    pred2 = build_pred2_text(pred)

    # --------------------------------------------------------
    # Prediction 3
    # --------------------------------------------------------

    pred3 = pred.get("prediction_3", "")

    # --------------------------------------------------------
    # Model Odds
    # --------------------------------------------------------

    odds = (
        pred.get("odds")
        or pred.get("oddsr")
        or {}
    )

    odds_html = ""

    if odds and odds.get("home_odds"):

        odds_html = f"""
        <tr>
          <td colspan="2"
              style="padding:6px 12px;font-size:12px;color:#888">
            📊 Odds:
            Home {odds['home_odds']} ({odds['home_pct']}%) |
            Draw {odds['draw_odds']} ({odds['draw_pct']}%) |
            Away {odds['away_odds']} ({odds['away_pct']}%)
          </td>
        </tr>
        """

    # --------------------------------------------------------
    # Market Odds
    # --------------------------------------------------------

    market_odds = pred.get("market_odds") or {}

    market_html = ""

    if market_odds and market_odds.get("home_odds"):

        market_html = f"""
        <tr>
          <td colspan="2"
              style="padding:6px 12px;font-size:12px;color:#888">
            💰 Market Odds:
            Home {market_odds['home_odds']} ({market_odds['home_pct']}%) |
            Draw {market_odds['draw_odds']} ({market_odds['draw_pct']}%) |
            Away {market_odds['away_odds']} ({market_odds['away_pct']}%)
          </td>
        </tr>
        """

    # --------------------------------------------------------
    # Value
    # --------------------------------------------------------

    value_pct = pred.get("value_pct") or {}
    value_signal = pred.get("value_signal") or {}

    value_html = ""

    if value_pct:

        def fmt_val(n):

            if n is None:
                return "—"

            sign = "+" if n > 0 else ""

            return f"{sign}{n}%"

        signal_str = (
            f"Signal: {value_signal['under']}"
            if value_signal.get("under")
            else ""
        )

        signal_line = (
            f"<br><b style='color:#AAFF3C'>"
            f"{signal_str}</b>"
            if signal_str
            else ""
        )

        decision = value_signal.get(
            "decision",
            ""
        )

        decision_line = (
            f"<br><b style='color:#F39C12;font-size:14px'>"
            f"⚡ DECISION: {decision}</b>"
            if decision
            else ""
        )

        value_html = f"""
        <tr>
          <td colspan="2"
              style="padding:6px 12px;font-size:12px;color:#888">

            📈 Value:
            Home {fmt_val(value_pct.get('home'))} |
            Draw {fmt_val(value_pct.get('draw'))} |
            Away {fmt_val(value_pct.get('away'))} |
            Total {fmt_val(value_pct.get('total'))} |
            Share Diff {fmt_val(value_pct.get('share_diff'))}

            {signal_line}

            {decision_line}

          </td>
        </tr>
        """

    # --------------------------------------------------------
    # O/U 2.5
    # --------------------------------------------------------

    ou25 = pred.get("ou25") or {}
    market_ou25 = pred.get("market_ou25") or {}

    ou25_html = ""

    if (
        ou25.get("over_odds")
        or market_ou25.get("over_odds")
    ):

        parts = []

        if ou25.get("over_odds"):

            parts.append(
                f"Model Over {ou25['over_odds']} "
                f"({ou25['over_pct']}%) / "
                f"Under {ou25['under_odds']} "
                f"({ou25['under_pct']}%)"
            )

        if market_ou25.get("over_odds"):

            parts.append(
                f"Market Over {market_ou25['over_odds']} "
                f"({market_ou25['over_pct']}%) / "
                f"Under {market_ou25['under_odds']} "
                f"({market_ou25['under_pct']}%)"
            )

        ou25_html = f"""
        <tr>
          <td colspan="2"
              style="padding:6px 12px;font-size:12px;color:#888">
            ⚽ O/U 2.5:
            {" | ".join(parts)}
          </td>
        </tr>
        """

    # --------------------------------------------------------
    # O/U Value
    # --------------------------------------------------------

    ou25_value_pct = (
        pred.get("ou25_value_pct")
        or {}
    )

    ou25_value_signal = (
        pred.get("ou25_value_signal")
        or {}
    )

    ou25_value_html = ""

    if ou25_value_pct:

        def fmt_val_ou(n):

            if n is None:
                return "—"

            sign = "+" if n > 0 else ""

            return f"{sign}{n}%"

        ou_signal_str = (
            f"Result: {ou25_value_signal['result']}"
            if ou25_value_signal.get("result")
            else ""
        )

        ou_signal_line = (
            f"<br><b style='color:#AAFF3C'>"
            f"{ou_signal_str}</b>"
            if ou_signal_str
            else ""
        )

        ou25_value_html = f"""
        <tr>
          <td colspan="2"
              style="padding:6px 12px;font-size:12px;color:#888">

            📈 O/U 2.5 Value:

            Over {fmt_val_ou(ou25_value_pct.get('over'))} |
            Under {fmt_val_ou(ou25_value_pct.get('under'))} |
            Total {fmt_val_ou(ou25_value_pct.get('total'))} |
            Over Share {fmt_val_ou(ou25_value_pct.get('over_share'))} |
            Under Share {fmt_val_ou(ou25_value_pct.get('under_share'))} |
            Abs Diff {fmt_val_ou(ou25_value_pct.get('abs_diff'))} |
            Share Diff {fmt_val_ou(ou25_value_pct.get('share_diff'))}

            {ou_signal_line}

          </td>
        </tr>
        """

    # --------------------------------------------------------
    # Prediction 1 HTML
    # --------------------------------------------------------

    pred1_html = (
        f"""
        <tr>
          <td colspan="2"
              style="padding:6px 12px;background:#1a472a;
                     color:#AAFF3C;font-weight:bold">

            ⚡ PREDICTION 1: {pred1}

          </td>
        </tr>
        """
        if pred1
        else ""
    )

    # --------------------------------------------------------
    # Prediction 2 HTML
    # --------------------------------------------------------

    if pred2:

        aa15_no = (
            (pred.get("aa15", "") or "").lower()
            == "no"
            or
            (pred.get("aa15r", "") or "").lower()
            == "no"
        )

        b54_over = (
            "over"
            in (pred.get("b54", "") or "").lower()
            or
            "over"
            in (pred.get("b54r", "") or "").lower()
        )

        if b54_over:

            pred2_bg = "#000000"
            pred2_color = "#ffffff"

        elif aa15_no:

            pred2_bg = "#C0392B"
            pred2_color = "#ffffff"

        else:

            pred2_bg = "#1a3a47"
            pred2_color = "#F39C12"

        pred2_html = f"""
        <tr>
          <td colspan="2"
              style="padding:6px 12px;
                     background:{pred2_bg};
                     color:{pred2_color};
                     font-weight:bold">

            🎯 PREDICTION 2: {pred2}

          </td>
        </tr>
        """

    else:

        pred2_html = ""

    # --------------------------------------------------------
    # Prediction 3 HTML
    # --------------------------------------------------------

    if pred3:

        if "handicap" in pred3.lower():

            pred3_bg = "#12283a"
            pred3_color = "#85C1E9"

        else:

            pred3_bg = "#1a3a52"
            pred3_color = "#3498DB"

        pred3_html = f"""
        <tr>
          <td colspan="2"
              style="padding:6px 12px;
                     background:{pred3_bg};
                     color:{pred3_color};
                     font-weight:bold">

            🧭 PREDICTION 3: {pred3}

          </td>
        </tr>
        """

    else:

        pred3_html = ""

    # --------------------------------------------------------
    # COMPLETE MATCH CARD
    # --------------------------------------------------------

    return f"""
<div style="border:1px solid #ddd;
            border-radius:8px;
            margin:10px 0;
            overflow:hidden;
            font-family:Arial,sans-serif">

  <table width="100%"
         cellpadding="0"
         cellspacing="0">

    <tr style="background:#0A3D1F;color:white">

      <td style="padding:8px 12px;font-weight:bold">
        {time_str} &nbsp;
        {home} vs {away}
      </td>

      <td style="padding:8px 12px;
                 text-align:right;
                 color:#AAFF3C">

        {league_name}

      </td>

    </tr>

    <tr>

      <td style="padding:6px 12px;font-size:13px">

        <b>D69:</b> {pred.get('d70','')} |
        <b>B120:</b> {pred.get('b120','')} |
        <b>C120:</b> {pred.get('c120','')}

      </td>

      <td style="padding:6px 12px;
                 font-size:13px;
                 color:#666">

        <b>D64:</b> {pred.get('d64','')} |
        <b>B46:</b> {pred.get('b46','')}

      </td>

    </tr>

    <tr style="background:#f9f9f9">

      <td style="padding:6px 12px;font-size:13px">

        <b>REV D69:</b> {pred.get('d70r','')} |
        <b>B120:</b> {pred.get('b120r','')} |
        <b>C120:</b> {pred.get('c120r','')}

      </td>

      <td style="padding:6px 12px;
                 font-size:13px;
                 color:#666">

        <b>D64:</b> {pred.get('d64r','')} |
        <b>B46:</b> {pred.get('b46r','')}

      </td>

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

</div>
"""


# ============================================================
# BLOG 2 FILTER
# ============================================================

def meets_blog2_standard(pred):

    model_odds = (
        pred.get("odds")
        or pred.get("oddsr")
        or {}
    )

    market_odds = (
        pred.get("market_odds")
        or {}
    )

    odds_to_check = [

        model_odds.get("home_odds"),
        model_odds.get("draw_odds"),
        model_odds.get("away_odds"),

        market_odds.get("home_odds"),
        market_odds.get("draw_odds"),
        market_odds.get("away_odds"),

    ]

    if any(
        o is None
        for o in odds_to_check
    ):
        return False

    if any(
        o < 1.45
        for o in odds_to_check
    ):
        return False

    ou25_value_signal = (
        pred.get("ou25_value_signal")
        or {}
    )

    result = ou25_value_signal.get(
        "result",
        ""
    )

    if result not in (
        "under",
        "under confirmed"
    ):
        return False

    prediction_3 = (
        pred.get("prediction_3")
        or ""
    ).lower()

    if "away" not in prediction_3:
        return False

    value_pct = (
        pred.get("value_pct")
        or {}
    )

    hda_values = [

        value_pct.get("home"),
        value_pct.get("draw"),
        value_pct.get("away")

    ]

    if any(
        v is not None and abs(v) >= 98
        for v in hda_values
    ):
        return False

    return True


# ============================================================
# POST TO BLOGGER
# ============================================================

def post_to_blogger(
    access_token,
    blog_id,
    title,
    content
):

    response = requests.post(

        f"https://www.googleapis.com/"
        f"blogger/v3/blogs/{blog_id}/posts/",

        headers={
            "Authorization":
                f"Bearer {access_token}",

            "Content-Type":
                "application/json"
        },

        json={
            "title": title,
            "content": content
        },

        timeout=60
    )

    try:
        result = response.json()
    except Exception:
        result = {
            "raw_response":
                response.text
        }

    return response.status_code, result


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # NIGERIA DATE
    # --------------------------------------------------------

    nigeria_tz = timezone(
        timedelta(hours=1)
    )

    today = datetime.now(
        nigeria_tz
    ).date()

    date_str = (
        f"{today.day} "
        f"{today.strftime('%b')}"
    )

    today_display = today.strftime(
        "%A, %B %d %Y"
    )

    print(
        f"🚀 Kickwise Daily Predictions — "
        f"{today_display}"
    )


    # --------------------------------------------------------
    # BLOG 1 HEADER
    # --------------------------------------------------------

    all_html = f"""

<div style="background:#0A3D1F;
            color:#AAFF3C;
            padding:16px;
            border-radius:8px;
            font-family:Arial,sans-serif;
            text-align:center">

  <h2 style="margin:0;font-size:24px">
    ⚽ Kickwise Daily Predictions
  </h2>

  <p style="margin:4px 0;color:#fff">
    {today_display}
  </p>

  <p style="margin:4px 0;font-size:12px;color:#aaa">
    Predictions powered by A_mix2 Model |
    Data from SoccerStats
  </p>

</div>

"""


    # --------------------------------------------------------
    # COLLECT ALL MATCHES
    # --------------------------------------------------------

    all_matches = []

    seen_matches = set()

    for league_name, code in LEAGUE_CODES.items():

        fixtures = get_fixtures(
            code,
            date_str
        )

        if not fixtures:
            continue

        print(
            f"  📌 {league_name}: "
            f"{len(fixtures)} match(es)"
        )

        for fix in fixtures:

            dedup_key = (
                code,
                fix["home"].lower().strip(),
                fix["away"].lower().strip()
            )

            if dedup_key in seen_matches:

                print(
                    f"    ⚠️ Skipping duplicate: "
                    f"{fix['home']} vs "
                    f"{fix['away']} "
                    f"({league_name})"
                )

                continue

            seen_matches.add(
                dedup_key
            )

            all_matches.append({

                "league_name":
                    league_name,

                "code":
                    code,

                "fix":
                    fix

            })


    # --------------------------------------------------------
    # SORT MATCHES BY TIME
    # --------------------------------------------------------

    def sort_key(match):

        match_time = (
            match["fix"].get(
                "time",
                ""
            )
        )

        if (
            not match_time
            or match_time == "TBD"
        ):
            return "99:99"

        return match_time


    all_matches.sort(
        key=sort_key
    )


    print(
        f"\n📋 Total unique matches found: "
        f"{len(all_matches)}"
    )


    # --------------------------------------------------------
    # BLOG 2 HEADER
    # --------------------------------------------------------

    blog2_html = f"""

<div style="background:#0A3D1F;
            color:#AAFF3C;
            padding:16px;
            border-radius:8px;
            font-family:Arial,sans-serif;
            text-align:center">

  <h2 style="margin:0;font-size:24px">
    ⚽ Kickwise Standard Picks
  </h2>

  <p style="margin:4px 0;color:#fff">
    {today_display}
  </p>

  <p style="margin:4px 0;font-size:12px;color:#aaa">
    Filtered picks meeting the standard |
    Data from SoccerStats
  </p>

</div>

"""


    # --------------------------------------------------------
    # COUNTERS
    # --------------------------------------------------------

    total_matches = 0
    failed_matches = 0
    na_matches = 0

    current_time = None

    blog2_matches = 0
    blog2_current_time = None

    blog2_notify_cards = []


    # ========================================================
    # PROCESS EVERY MATCH
    # ========================================================

    for m in all_matches:

        home = m["fix"]["home"]
        away = m["fix"]["away"]

        print(
            f"\n🔎 Processing: "
            f"{home} vs {away} "
            f"({m['league_name']})"
        )


        # ----------------------------------------------------
        # GET PREDICTION
        # ----------------------------------------------------

        pred = get_prediction(
            m["code"],
            home,
            away
        )


        # ----------------------------------------------------
        # FAILED REQUEST
        # ----------------------------------------------------

        if pred is None:

            failed_matches += 1

            continue


        # ----------------------------------------------------
        # ALL N/A
        # ----------------------------------------------------

        if all(

            (
                pred.get(k)
                or "N/A"
            )
            in (
                "N/A",
                "",
                "None"
            )

            for k in [
                "d70",
                "b120",
                "c120",
                "d64",
                "b46"
            ]

        ):

            print(
                f"    ⚠️ Skipping "
                f"{home} vs {away} "
                f"(all N/A)"
            )

            na_matches += 1

            continue


        # ----------------------------------------------------
        # BUILD MATCH HTML
        # ----------------------------------------------------

        match_html = format_match_html(
            m["league_name"],
            m["fix"],
            pred
        )


        if match_html:

            match_time = (
                m["fix"].get(
                    "time",
                    "TBD"
                )
            )


            # ----------------------------------------------
            # TIME HEADER
            # ----------------------------------------------

            if match_time != current_time:

                current_time = match_time

                all_html += f"""

<div style="background:#2C3E50;
            color:#AAFF3C;
            padding:8px 12px;
            margin:16px 0 4px;
            border-radius:4px;
            font-family:Arial;
            font-weight:bold;
            font-size:15px">

  🕐 {match_time}

</div>

"""


            # ----------------------------------------------
            # ADD MATCH
            # ----------------------------------------------

            all_html += match_html

            total_matches += 1


            # =================================================
            # BLOG 2 FILTER
            # =================================================

            if (
                BLOG2_ENABLED
                and meets_blog2_standard(pred)
            ):

                if (
                    match_time
                    != blog2_current_time
                ):

                    blog2_current_time = (
                        match_time
                    )

                    blog2_html += f"""

<div style="background:#2C3E50;
            color:#AAFF3C;
            padding:8px 12px;
            margin:16px 0 4px;
            border-radius:4px;
            font-family:Arial;
            font-weight:bold;
            font-size:15px">

  🕐 {match_time}

</div>

"""


                blog2_html += match_html

                blog2_matches += 1


                # ------------------------------------------
                # TELEGRAM CARD
                # ------------------------------------------

                value_signal = (
                    pred.get(
                        "value_signal"
                    )
                    or {}
                )

                ou25_value_signal = (
                    pred.get(
                        "ou25_value_signal"
                    )
                    or {}
                )

                b46_out = (
                    pred.get("b46")
                    or pred.get("b46r")
                    or "—"
                )


                blog2_notify_cards.append(

                    f"🕐 {match_time} | "
                    f"{m['league_name']}\n"

                    f"👥 {home} vs {away}\n"

                    f"⚡ Decision: "
                    f"{value_signal.get('decision') or '—'}\n"

                    f"📋 B46: {b46_out}\n"

                    f"📈 O/U Result: "
                    f"{ou25_value_signal.get('result') or '—'}\n"

                    f"🧭 Prediction 3: "
                    f"{pred.get('prediction_3') or '—'}"

                )


    # ========================================================
    # IMPORTANT:
    # NO "return" HERE.
    #
    # The old version had:
    #
    #     return
    #
    # inside the processing section.
    #
    # That caused the script to stop after the first match.
    # ========================================================


    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print(
        f"\n📊 Summary: "
        f"{total_matches} posted | "
        f"{na_matches} skipped "
        f"(genuine N/A) | "
        f"{failed_matches} dropped "
        f"(request failed after retries)"
    )


    if BLOG2_ENABLED:

        print(
            f"📊 Blog 2 "
            f"(standard picks): "
            f"{blog2_matches} of "
            f"{total_matches} "
            f"matches qualified"
        )


    # --------------------------------------------------------
    # BLOG 1 FOOTER
    # --------------------------------------------------------

    all_html += f"""

<p style="text-align:center;
          color:#888;
          font-size:12px;
          margin-top:20px">

  Generated by Kickwise |
  {today_display} |
  {total_matches} matches processed

</p>

"""


    # ========================================================
    # POST BLOG 1
    # ========================================================

    if total_matches == 0:

        print(
            "\n⚠️ No valid predictions generated. "
            "Blog 1 post skipped."
        )

    else:

        print(
            f"\n📝 Posting "
            f"{total_matches} matches to Blogger..."
        )

        try:

            access_token = get_access_token()

            title = (
                f"⚽ Kickwise Predictions — "
                f"{today_display}"
            )

            status, result = post_to_blogger(
                access_token,
                BLOG_ID,
                title,
                all_html
            )


            if status in (200, 201):

                print(
                    f"✅ Posted successfully! "
                    f"URL: {result.get('url', '')}"
                )

            else:

                print(
                    f"❌ Failed to post: "
                    f"{status} — {result}"
                )

        except Exception as e:

            print(
                f"❌ Blog 1 posting error: {e}"
            )


    # ========================================================
    # BLOG 2
    # ========================================================

    if BLOG2_ENABLED:

        if blog2_matches == 0:

            print(
                "\nℹ️ Blog 2: "
                "no matches met the standard "
                "today — skipping post."
            )

        else:

            blog2_html += f"""

<p style="text-align:center;
          color:#888;
          font-size:12px;
          margin-top:20px">

  Generated by Kickwise |
  {today_display} |
  {blog2_matches} matches processed

</p>

"""


            print(
                f"\n📝 Posting "
                f"{blog2_matches} matches "
                f"to Blog 2..."
            )


            try:

                access_token_2 = get_access_token(

                    CLIENT_ID_2,
                    CLIENT_SECRET_2,
                    REFRESH_TOKEN_2

                )


                title_2 = (
                    f"⚽ Kickwise Standard Picks — "
                    f"{today_display}"
                )


                status_2, result_2 = post_to_blogger(

                    access_token_2,
                    BLOG_ID_2,
                    title_2,
                    blog2_html

                )


                if status_2 in (200, 201):

                    print(
                        f"✅ Blog 2 posted successfully! "
                        f"URL: "
                        f"{result_2.get('url', '')}"
                    )


                    # --------------------------------------
                    # TELEGRAM
                    # --------------------------------------

                    cards_text = (
                        "\n\n".join(
                            blog2_notify_cards
                        )
                    )


                    notify_message = (

                        f"⚽ Kickwise Standard Picks — "
                        f"{today_display}\n"

                        f"{blog2_matches} match(es) "
                        f"qualified\n\n"

                        f"{cards_text}\n\n"

                        f"{result_2.get('url', '')}"

                    )


                    send_telegram_notification(
                        notify_message
                    )


                else:

                    print(
                        f"❌ Blog 2 failed to post: "
                        f"{status_2} — "
                        f"{result_2}"
                    )

            except Exception as e:

                print(
                    f"❌ Blog 2 posting error: "
                    f"{e}"
                )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
