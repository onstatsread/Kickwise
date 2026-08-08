"""
AnnaBet URL Slug Test
Checks whether AnnaBet's URL slug text (the "_South_Korean_K-League"
part) is required, or if the numeric serie_ID alone is enough. This
needs to be confirmed before batch-checking 162 leagues — if the slug
IS required, every request in a batch built on ID-only URLs would fail.
"""
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# Known-good URL (confirmed working earlier)
KNOWN_GOOD = "https://annabet.com/en/soccerstats/serie_249_South_Korean_K-League.html"

# Same ID, wrong/placeholder slug — testing if AnnaBet ignores slug text
WRONG_SLUG = "https://annabet.com/en/soccerstats/serie_249_x.html"

# Same ID, no slug at all
NO_SLUG = "https://annabet.com/en/soccerstats/serie_249.html"


def test(label, url):
    print(f"\n--- {label} ---")
    print(url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        print(f"Status: {resp.status_code}, Length: {len(resp.text)}")
        found = "FC Seoul" in resp.text or "K League" in resp.text
        print(f"Contains expected content: {found}")
    except Exception as e:
        print(f"Failed: {e}")


def main():
    test("Known-good URL (correct slug)", KNOWN_GOOD)
    test("Wrong/placeholder slug", WRONG_SLUG)
    test("No slug at all", NO_SLUG)


if __name__ == "__main__":
    main()

