"""
Kickwise Daily Predictions — v2 (GOAL API + OddStorm/Oddsbook)

Same output (blog posts, Telegram signals) as daily_predictions.py,
but with a fundamentally different — and much more efficient — fetch
pattern:

    OLD (AnnaBet): loop over 61 leagues, ONE /fixtures call PER
    LEAGUE (61 calls), then ONE /predict call per match.

    NEW (GOAL API): ONE /fixtures-v2 call gets every match across all
    61 leagues in a single request, then ONE /predict-v2 call per
    match (same as before for the predict step).

All HTML formatting, Double Chance signal detection, Blogger posting,
and Telegram notification logic is IMPORTED UNCHANGED from
daily_predictions.py — only the fetch layer differs. This keeps the
two pipelines' output format identical, so they can be compared
side-by-side or swapped without touching downstream logic.
"""

import requests
from datetime import date, datetime, timedelta

from daily_predictions import (
    BACKEND_URL, BLOG_ID, BLOG_ID_2, BLOG2_ENABLED,
    CLIENT_ID_2, CLIENT_SECRET_2, REFRESH_TOKEN_2,
    format_match_html, meets_blog2_standard,
    check_double_chance_signal, refine_double_chance_signal,
    check_double_chance_signal_2, check_double_chance_signal_3,
    passes_double_chance_extra_filter,
    send_telegram_notification, post_to_blogger, get_access_token,
)


def get_fixtures_v2(date_str):
    """
    ONE call gets every match across all 61 active leagues, grouped
    by Kickwise "Country - League" name — replaces 61 separate
    per-league /fixtures calls.
    """
    try:
        r = requests.get(
            f"{BACKEND_URL}/fixtures-v2",
            params={"date": date_str},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("leagues", {})
    except Exception as e:
        print(f"⚠️ get_fixtures_v2 failed: {e}")
        return {}


def get_prediction_v2(league_name, home, away, date_str, retries=2):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                f"{BACKEND_URL}/predict-v2",
                params={"league": league_name, "home": home, "away": away, "date": date_str},
                timeout=180,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_error = e
            if attempt < retries:
                print(f"    ⏳ Attempt {attempt} failed for {home} vs {away} ({e}) — retrying...")
    print(f"    ❌ Failed to get prediction for {home} vs {away} after {retries} attempts: {last_error}")
    return None


def main():
    today = (datetime.utcnow() + timedelta(hours=1)).date()
    date_str = str(today)  # GOAL API wants YYYY-MM-DD, not AnnaBet's "D Mon" format
    today_display = today.strftime("%A, %B %d %Y")

    print(f"🚀 Kickwise Daily Predictions v2 (GOAL API) — {today_display}")

    all_html = f"""
<div style="background:#0A3D1F;color:#AAFF3C;padding:16px;border-radius:8px;font-family:Arial,sans-serif;text-align:center">
  <h2 style="margin:0;font-size:24px">⚽ Kickwise Daily Predictions</h2>
  <p style="margin:4px 0;color:#fff">{today_display}</p>
  <p style="margin:4px 0;font-size:12px;color:#aaa">Predictions powered by A_mix2 Model | Data from GOAL API + OddStorm</p>
</div>
"""

    print("📡 Fetching all fixtures in ONE call...")
    leagues_data = get_fixtures_v2(date_str)
    print(f"  Found matches in {len(leagues_data)} leagues")

    all_matches = []
    seen_matches = set()

    for league_name, fixtures in leagues_data.items():
        if not fixtures:
            continue
        print(f"  📌 {league_name}: {len(fixtures)} match(es)")
        for fix in fixtures:
            home = (fix.get("home") or "").strip()
            away = (fix.get("away") or "").strip()
            if not home or not away:
                continue

            dedup_key = (league_name, home.lower(), away.lower())
            if dedup_key in seen_matches:
                print(f"    ⚠️ Skipping duplicate: {home} vs {away} ({league_name})")
                continue
            seen_matches.add(dedup_key)

            all_matches.append({
                "league_name": league_name,
                "fix": {
                    "time": fix.get("time") or "TBD",
                    "home": home,
                    "away": away,
                },
            })

    def sort_key(m):
        t = m["fix"].get("time", "")
        return t if t and t != "TBD" else "99:99"

    all_matches.sort(key=sort_key)

    total_matches = 0
    failed_matches = 0
    na_matches = 0
    current_time = None

    blog2_html = f"""
<div style="background:#0A3D1F;color:#AAFF3C;padding:16px;border-radius:8px;font-family:Arial,sans-serif;text-align:center">
  <h2 style="margin:0;font-size:24px">⚽ Kickwise Standard Picks</h2>
  <p style="margin:4px 0;color:#fff">{today_display}</p>
  <p style="margin:4px 0;font-size:12px;color:#aaa">Filtered picks meeting the standard | Data from GOAL API + OddStorm</p>
</div>
"""
    blog2_matches = 0
    blog2_current_time = None
    blog2_notify_cards = []
    dc_notify_cards = []
    dc2_notify_cards = []
    dc3_notify_cards = []

    for m in all_matches:
        pred = get_prediction_v2(m["league_name"], m["fix"]["home"], m["fix"]["away"], date_str)

        if pred is None or pred.get("error"):
            failed_matches += 1
            continue

        if all(
            (pred.get(k) or "N/A") in ("N/A", "", "None")
            for k in ["d70", "b120", "c120", "d64", "b46"]
        ):
            print(f"    ⚠️ Skipping {m['fix']['home']} vs {m['fix']['away']} (all N/A)")
            na_matches += 1
            continue

        match_html = format_match_html(m["league_name"], m["fix"], pred)
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
                    f"🧭 Prediction 3: {pred.get('prediction_3') or '—'}"
                )

            value_pct = pred.get("value_pct") or {}

            dc_side = check_double_chance_signal(pred)
            if dc_side:
                dc_signal = refine_double_chance_signal(pred, dc_side)
                if dc_signal and passes_double_chance_extra_filter(value_pct):
                    dc_notify_cards.append(
                        f"🕐 {match_time} | {m['league_name']}\n"
                        f"👥 {m['fix']['home']} vs {m['fix']['away']}\n"
                        f"🎯 Signal: {dc_signal}\n"
                        f"📈 Value: Home {value_pct.get('home')}% | "
                        f"Draw {value_pct.get('draw')}% | "
                        f"Away {value_pct.get('away')}%"
                    )

            dc2_result = check_double_chance_signal_2(pred)
            if dc2_result and passes_double_chance_extra_filter(value_pct):
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
            if dc3_result and passes_double_chance_extra_filter(value_pct):
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

    if total_matches == 0:
        print("No matches found today.")
        return

    print(f"\n📊 Summary: {total_matches} posted | {na_matches} skipped (genuine N/A) | {failed_matches} dropped (request failed after retries)")
    if BLOG2_ENABLED:
        print(f"📊 Blog 2 (standard picks): {blog2_matches} of {total_matches} matches qualified")

    all_html += f'\n<p style="text-align:center;color:#888;font-size:12px;margin-top:20px">Generated by Kickwise v2 | {today_display} | {total_matches} matches processed</p>'

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
            blog2_html += f'\n<p style="text-align:center;color:#888;font-size:12px;margin-top:20px">Generated by Kickwise v2 | {today_display} | {blog2_matches} matches processed</p>'
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


if __name__ == "__main__":
    main()
