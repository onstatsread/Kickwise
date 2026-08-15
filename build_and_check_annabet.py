"""
build_and_check_annabet.py

One-off diagnostic — checks whether AnnaBet's per-league "Upcoming Games"
tab (div id="tabs-5") is present in the raw HTML of a league's serie_ID
page, or whether it's loaded separately via AJAX (and so invisible to a
plain requests.get() call, the same kind of call fetch_stats_annabet()
and fetch_fixtures_annabet() already make on the backend).

Why this matters: the global /upcoming/ page currently used for fixtures
only has a short rolling lookahead window (observed to miss MLS's evening
US kickoffs, which land late UTC — past where /upcoming/ cuts off). Each
league's own serie_ID page has an "Upcoming Games" tab showing several
days ahead instead. If that tab's HTML ships in the initial page load
(like the Results tab clearly does — confirmed by seeing full game
results in the page), we can parse it directly and get a much better
fixtures source than /upcoming/. If it's AJAX-only, we'd need to find
AnnaBet's internal API endpoint instead (not doable from HTML alone).

Run manually via the Actions tab (workflow_dispatch) — no schedule, no
Render deploy needed. Tests every league already in ANNABET_SERIE_ID by
default; edit LEAGUES_TO_TEST below to narrow it down if you just want
to check one or two.
"""
import requests
from bs4 import BeautifulSoup

ANNABET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # NOT "br" — requests can't decompress
                                          # Brotli without an extra package
    "Connection": "keep-alive",
    "Referer": "https://annabet.com/en/soccerstats/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

# Same mapping as the backend's ANNABET_SERIE_ID — kept as a plain dict
# here so this script has zero dependency on the backend codebase and can
# run standalone in this workflow.
ANNABET_SERIE_ID = {
    "belarus": 232, "brazil": 217, "brazil2": 259, "canada": 750,
    "chile": 301, "china": 248, "china2": 731, "colombia": 329,
    "ecuador": 313, "estonia": 242, "faroeislands": 368, "finland": 7,
    "finland2": 35, "georgia": 235, "iceland": 114, "iceland2": 392,
    "ireland": 42, "ireland2": 163, "kazakhstan": 328, "latvia": 223,
    "lithuania": 226, "malaysia": 521, "norway": 36, "norway2": 173,
    "paraguay": 347, "peru": 321, "southkorea": 249, "southkorea2": 543,
    "sweden": 32, "sweden2": 33, "uruguay": 439, "usa": 43, "usa2": 362,
    "venezuela": 314,
}

# Edit this list to test specific leagues only. Defaults to just "usa"
# since that's the one we already know is affected — widen it to
# list(ANNABET_SERIE_ID.keys()) to check every mapped league at once.
LEAGUES_TO_TEST = ["usa"]


def check_league(code, serie_id):
    url = f"https://annabet.com/en/soccerstats/serie_{serie_id}_x.html"
    print(f"\n{'='*60}")
    print(f"Checking: {code} (serie_{serie_id}) — {url}")
    print(f"{'='*60}")

    try:
        resp = requests.get(url, headers=ANNABET_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ❌ Request failed: {e}")
        return

    html = resp.text
    print(f"  ✅ Status {resp.status_code} — page length {len(html)} chars")

    tabs_5_present = 'id="tabs-5"' in html or "id='tabs-5'" in html
    gamereport_count = html.count("gamereport")

    print(f"  📊 gamereport link count (Results-tab sanity check): {gamereport_count}")

    if tabs_5_present:
        print(f"  ✅ tabs-5 (Upcoming Games) IS present in raw HTML")
        soup = BeautifulSoup(html, "html.parser")
        tab_div = soup.find(id="tabs-5")
        if tab_div:
            snippet = tab_div.get_text(" ", strip=True)[:1000]
            print(f"  📝 tabs-5 text snippet:\n{snippet}")

            # Quick check for table rows inside it, since that's what
            # we'd need to parse fixtures out of if we build this out
            tables = tab_div.find_all("table")
            print(f"\n  📋 Found {len(tables)} <table> element(s) inside tabs-5")

            for i, t in enumerate(tables):
                rows = t.find_all("tr")
                print(f"    Table {i}: {len(rows)} row(s)")

            # The text snippet above matched the team-filter dropdown, not
            # real fixture data — dump the RAW HTML (not just extracted
            # text) so we can see the actual tag structure: is there an
            # empty table waiting for JS/AJAX to fill it, or something
            # else entirely (e.g. a nested sub-tab, a different div,
            # a "no upcoming matches" placeholder)?
            raw_html_snippet = str(tab_div)[:3000]
            print(f"\n  🔍 RAW HTML inside tabs-5 (first 3000 chars):\n{raw_html_snippet}")
        else:
            print(f"  ⚠️ String match found but BeautifulSoup couldn't locate the element — check for malformed HTML")
    else:
        print(f"  ❌ tabs-5 NOT found in raw HTML — likely AJAX-loaded, needs a different approach")


def main():
    print(f"Testing {len(LEAGUES_TO_TEST)} league(s) for AnnaBet Upcoming Games tab availability...\n")
    for code in LEAGUES_TO_TEST:
        if code not in ANNABET_SERIE_ID:
            print(f"⚠️ Skipping '{code}' — no serie_ID mapping found")
            continue
        check_league(code, ANNABET_SERIE_ID[code])

    print(f"\n{'='*60}")
    print("Done.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
