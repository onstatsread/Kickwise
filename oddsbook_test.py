import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = "https://oddsbook.com/football/"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 11) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

print("Connecting to Oddsbook...")

try:
    response = requests.get(
        URL,
        headers=headers,
        timeout=30
    )

    print("Status code:", response.status_code)
    print("Final URL:", response.url)
    print("Page size:", len(response.text))

    if response.status_code != 200:
        print("Could not access Oddsbook.")
        print(response.text[:500])
        raise SystemExit

    soup = BeautifulSoup(response.text, "html.parser")

    print("\nTITLE:")
    print(soup.title.get_text(strip=True) if soup.title else "No title")

    links = soup.find_all("a", href=True)

    print("\nLinks found:", len(links))

    football_links = []

    for link in links:
        href = link.get("href")
        text = link.get_text(" ", strip=True)

        if "/football/" in href:
            football_links.append({
                "text": text,
                "url": urljoin(URL, href)
            })

    print("\nFootball links found:", len(football_links))

    for item in football_links[:30]:
        print(
            f"- {item['text'][:60]} -> {item['url']}"
        )

except Exception as e:
    print("ERROR:", repr(e))
