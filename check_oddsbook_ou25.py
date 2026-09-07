"""
Checks a real Oddsbook match-detail page for Over/Under 2.5 odds —
the day-list page (/football/?date=) only showed a 1X2 market block;
this checks whether O/U lives on the individual match page instead,
similar to how AnnaBet's O/U 2.5 lives on a separate H2H page.
"""

from datetime import date
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import re

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def main():
    today = date.today().strftime("%Y-%m-%d")
    day_url = f"https://oddsbook.com/football/?date={today}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        # Step 1: get today's fixtures to find a real match URL.
        print(f"Loading {day_url} ...")
        page.goto(day_url, timeout=45000, wait_until="domcontentloaded")

        try:
            page.wait_for_function(
                "document.title !== 'Just a moment...'", timeout=20000
            )
        except Exception:
            pass

        page.wait_for_timeout(2000)

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        first_article = soup.find("article", attrs={"data-game-item": True})
        if not first_article:
            print("No fixtures found today — try again on a day with matches.")
            return

        canonical = first_article.get("data-canonical-url", "")
        match_url = "https://oddsbook.com" + canonical if canonical.startswith("/") else canonical
        home = first_article.get("data-home-name")
        away = first_article.get("data-away-name")

        print(f"Found match: {home} vs {away}")
        print(f"Match URL: {match_url}")

        # Step 2: visit the match detail page, fresh context (avoid
        # Cloudflare rate-suspicion from reusing the same session).
        context.close()
        context2 = browser.new_context(user_agent=USER_AGENT)
        page2 = context2.new_page()

        print(f"\nLoading match detail page ...")
        page2.goto(match_url, timeout=45000, wait_until="domcontentloaded")

        try:
            page2.wait_for_function(
                "document.title !== 'Just a moment...'", timeout=20000
            )
        except Exception:
            pass

        page2.wait_for_timeout(2500)
        print(f"Page title: {page2.title()}")

        match_html = page2.content()
        match_soup = BeautifulSoup(match_html, "html.parser")

        # Search for Over/Under related markers.
        keywords = ["Over/Under", "Over Under", "O/U", "Total Goals", "2.5"]
        body_text = match_soup.get_text(" ", strip=True)

        print(f"\nKeyword presence in match page text:")
        for kw in keywords:
            print(f"  {kw!r}: {kw in body_text}")

        # Look for aria-label markets similar to the "1X2" one we
        # already confirmed works on the day-list page.
        market_divs = match_soup.find_all("div", attrs={"aria-label": True})
        print(f"\nAll aria-label market divs found on match page ({len(market_divs)}):")
        for d in market_divs[:20]:
            print(f"  aria-label={d.get('aria-label')!r}")

        # Also check for data-market attributes beyond "1x2".
        market_buttons = match_soup.find_all(attrs={"data-market": True})
        distinct_markets = set(b.get("data-market") for b in market_buttons)
        print(f"\nDistinct data-market values found: {distinct_markets}")

        browser.close()


if __name__ == "__main__":
    main()
