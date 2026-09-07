"""
Round 2 — corrected slug guesses for the 24 leagues that 404'd in
round 1, based on GOAL API's already manually-verified league names
(goalapi_leagues.py) — both platforms likely use similar English
branding for these leagues.
"""

import time
from playwright.sync_api import sync_playwright
from oddsbook_leagues import oddsbook_league_url

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# (Kickwise name, country_slug, corrected league_slug guess)
ROUND_2_CANDIDATES = [
    ("Belarus - Vysshaya Liga", "belarus", "premier-league"),
    ("Canada - Premier League", "canada", "canadian-premier-league"),
    ("Chile - Liga de Primera", "chile", "primera-division"),
    ("Faroe Islands - Premier League", "faroe-islands", "meistaradeildin"),
    ("Iceland - Besta deild", "iceland", "besta-deild-karla"),
    ("Norway - 1st Division", "norway", "1-division"),
    ("Paraguay - Primera Div.", "paraguay", "division-profesional"),
    ("Peru - Liga 1", "peru", "primera-division"),
    ("Uruguay - Liga AUF", "uruguay", "primera-division"),
    ("USA - MLS", "usa", "major-league-soccer"),
    ("Venezuela - Liga FUTVE", "venezuela", "primera-division"),
    ("England - Southern Football League", "england", "southern-league"),
    ("Bolivia - LFPB", "bolivia", "primera-division"),
    ("Estonia - Esiliiga", "estonia", "esiliiga-a"),
    ("Iceland - Division 2", "iceland", "2-deild"),
    ("India - Super League", "india", "indian-super-league"),
    ("Jamaica - National Premier League", "jamaica", "premier-league"),
    ("Kenya - Premier League", "kenya", "fkf-premier-league"),
    ("Morocco - Botola", "morocco", "botola-pro"),
    ("Singapore - S.League", "singapore", "premier-league"),
    ("Thailand - League 1", "thailand", "thai-league-1"),
    ("Vietnam - V.League 1", "vietnam", "v-league-1"),
    ("Turkmenistan - Higher League", "turkmenistan", "yokary-liga"),
    ("Tajikistan - Higher League", "tajikistan", "vysshaya-liga"),
]


def main():
    ok = []
    still_broken = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for i, (name, country_slug, league_slug) in enumerate(ROUND_2_CANDIDATES, start=1):
            url = f"https://oddsbook.com/football/{country_slug}/{league_slug}/"

            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()

            try:
                resp = page.goto(url, timeout=20000, wait_until="domcontentloaded")
                status = resp.status if resp else None
                title = page.title()

                is_challenge = "Just a moment" in title
                is_404 = status == 404 or "404" in title

                if status == 200 and not is_challenge and not is_404:
                    ok.append((name, country_slug, league_slug, title))
                    print(f"[{i}/{len(ROUND_2_CANDIDATES)}] OK   {name!r} -> {url} (title: {title!r})")
                else:
                    still_broken.append((name, country_slug, league_slug, status, title))
                    print(f"[{i}/{len(ROUND_2_CANDIDATES)}] FAIL {name!r} -> {url} (status={status}, title={title!r})")

            except Exception as e:
                still_broken.append((name, country_slug, league_slug, "exception", str(e)))
                print(f"[{i}/{len(ROUND_2_CANDIDATES)}] ERROR {name!r} -> {url} -> {e}")

            context.close()
            time.sleep(2)

        browser.close()

    print(f"\n{'=' * 60}")
    print(f"NOW OK: {len(ok)} / {len(ROUND_2_CANDIDATES)}")
    print("=" * 60)
    for name, country_slug, league_slug, title in ok:
        print(f'    {name!r}: ("{country_slug}", "{league_slug}"),  # {title}')

    print(f"\n{'=' * 60}")
    print(f"STILL BROKEN: {len(still_broken)}")
    print("=" * 60)
    for name, country_slug, league_slug, status, title in still_broken:
        print(f"  {name!r} -> {country_slug}/{league_slug} (status={status}, title={title!r})")


if __name__ == "__main__":
    main()
