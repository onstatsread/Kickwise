"""
AnnaBet Accessibility Test
One-off script — checks whether AnnaBet can be reached with a plain
requests.get() (no ScraperAPI, no FlareSolverr, no special proxy) the
same way our actual backend would call it. If this comes back clean,
AnnaBet could replace SoccerStats without any of the scraping-fight
infrastructure we've been building tonight.
"""
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TEST_URL = "https://annabet.com/en/soccerstats/serie_249_South_Korean_K-League.html"


def main():
    print(f"🔍 Testing plain request to: {TEST_URL}\n")

    try:
        resp = requests.get(TEST_URL, headers=HEADERS, timeout=20)
    except Exception as e:
        print(f"❌ Request failed entirely: {e}")
        return

    print(f"Status code: {resp.status_code}")
    print(f"Content length: {len(resp.text)}")

    # Check for signs of a Cloudflare-style block vs real content
    lower = resp.text.lower()
    if "just a moment" in lower or "challenges.cloudflare.com" in lower:
        print("\n❌ BLOCKED — Cloudflare challenge page detected, same as SoccerStats.")
    elif "fc seoul" in lower or "k league" in lower:
        print("\n✅ SUCCESS — real page content came through, includes expected team/league names.")
    else:
        print("\n⚠️ UNCLEAR — no Cloudflare block detected, but also didn't find expected")
        print("   content markers. Print a snippet below to inspect manually.")

    print(f"\nFirst 500 characters of response:\n{resp.text[:500]}")


if __name__ == "__main__":
    main()
