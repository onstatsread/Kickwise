"""
Verifies all 61 Oddsbook league slugs in oddsbook_leagues.py against
live data — using Playwright (bundled Chromium is enough, confirmed
working for Oddsbook's regular pages earlier today; only the hidden
standings BFF API needed the stronger real-Chrome-channel treatment).

Plain `requests` gets Cloudflare-blocked on Oddsbook (confirmed
earlier), so this REPLACES oddsbook_leagues.py's original
verify_all_slugs() (which used requests) for this one-off check.
"""

import time
from playwright.sync_api import sync_playwright
from oddsbook_leagues import ODDSBOOK_LEAGUES, oddsbook_league_url

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def main():
    ok = []
    broken = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        for i, name in enumerate(ODDSBOOK_LEAGUES, start=1):
            url = oddsbook_league_url(name)

            try:
                resp = page.goto(url, timeout=20000, wait_until="domcontentloaded")
                status = resp.status if resp else None
                title = page.title()

                is_challenge = "Just a moment" in title
                is_404 = status == 404 or "404" in title

                if status == 200 and not is_challenge and not is_404:
                    ok.append((name, url))
                    print(f"[{i}/{len(ODDSBOOK_LEAGUES)}] OK   {name!r} -> {url} (title: {title!r})")
                else:
                    broken.append((name, url, status, title))
                    print(f"[{i}/{len(ODDSBOOK_LEAGUES)}] FAIL {name!r} -> {url} (status={status}, title={title!r})")

            except Exception as e:
                broken.append((name, url, "exception", str(e)))
                print(f"[{i}/{len(ODDSBOOK_LEAGUES)}] ERROR {name!r} -> {url} -> {e}")

            time.sleep(1.5)  # gentle pacing to avoid Cloudflare rate-suspicion

        browser.close()

    print(f"\n{'=' * 60}")
    print(f"OK: {len(ok)} / {len(ODDSBOOK_LEAGUES)}")
    print("=" * 60)

    print(f"\n{'=' * 60}")
    print(f"BROKEN: {len(broken)}")
    print("=" * 60)
    for name, url, status, title in broken:
        print(f"  {name!r}: {url}")
        print(f"    status={status}, title={title!r}")


if __name__ == "__main__":
    main()
