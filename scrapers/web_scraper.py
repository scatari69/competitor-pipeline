"""
Web scraper: сайт, тарифи, відгуки конкурентів
"""
import requests
from bs4 import BeautifulSoup
import re
import time
import logging
from urllib.parse import urljoin, urlparse
from dataclasses import dataclass, field, asdict
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PRICE_PATTERNS = [
    r"\b(\d{2,4})\s*грн",
    r"\b(\d{2,4})\s*₴",
    r"UAH\s*(\d{2,4})",
    r"(\d{2,4})\s*грн[/\s]міс",
    r"(\d{2,4})\s*/\s*місяц",
    r"(\d{2,4})\s*на місяць",
]

PRICE_KEYWORDS = [
    "тариф", "пакет", "план", "підключення", "абонплата",
    "tariff", "plan", "package", "price", "pricing", "ціна", "вартість",
]

REVIEW_SOURCES = {
    "google":     "https://www.google.com/search?q={company}+відгуки&hl=uk",
    "trustpilot": "https://www.trustpilot.com/search?query={company}",
    "hotline":    "https://hotline.ua/search/?q={company}",
}

SOCIAL_PATTERNS = {
    "facebook":  r'facebook\.com/(?:pages/)?([A-Za-z0-9._-]+)',
    "instagram": r'instagram\.com/([A-Za-z0-9._-]+)',
    "youtube":   r'youtube\.com/(?:channel/|user/|@)([A-Za-z0-9._-]+)',
    "telegram":  r't\.me/([A-Za-z0-9_]+)',
    "linkedin":  r'linkedin\.com/company/([A-Za-z0-9._-]+)',
}


@dataclass
class ScrapeResult:
    url: str
    company_name: str = ""
    title: str = ""
    description: str = ""
    full_text: str = ""
    prices: list = field(default_factory=list)
    price_pages: list = field(default_factory=list)
    social_links: dict = field(default_factory=dict)
    contact_info: dict = field(default_factory=dict)
    nav_items: list = field(default_factory=list)
    technologies: list = field(default_factory=list)
    review_snippets: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    status: str = "pending"

    def to_dict(self):
        return asdict(self)


def fetch(url: str, timeout: int = 15) -> Optional[requests.Response]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp
    except Exception as e:
        log.warning(f"fetch error {url}: {e}")
        return None


def extract_prices(text: str) -> list[dict]:
    prices = []
    seen = set()
    lines = text.split("\n")
    for line in lines:
        for pattern in PRICE_PATTERNS:
            matches = re.findall(pattern, line, re.IGNORECASE)
            for m in matches:
                val = int(m)
                if 50 <= val <= 5000 and val not in seen:
                    seen.add(val)
                    context = line.strip()[:120]
                    prices.append({"value": val, "currency": "UAH", "context": context})
    prices.sort(key=lambda x: x["value"])
    return prices


def extract_social(soup: BeautifulSoup, base_url: str) -> dict:
    socials = {}
    all_links = [a.get("href", "") for a in soup.find_all("a", href=True)]
    full_text = " ".join(all_links)
    for platform, pattern in SOCIAL_PATTERNS.items():
        m = re.search(pattern, full_text, re.IGNORECASE)
        if m:
            handle = m.group(1).rstrip("/")
            if platform == "facebook":
                socials[platform] = f"https://facebook.com/{handle}"
            elif platform == "instagram":
                socials[platform] = f"https://instagram.com/{handle}"
            elif platform == "telegram":
                socials[platform] = f"https://t.me/{handle}"
            elif platform == "youtube":
                socials[platform] = f"https://youtube.com/@{handle}"
            elif platform == "linkedin":
                socials[platform] = f"https://linkedin.com/company/{handle}"
    return socials


def extract_contacts(soup: BeautifulSoup) -> dict:
    text = soup.get_text(" ")
    contacts = {}
    phones = re.findall(r'(?:\+38)?(?:0\d{9}|\(0\d{2}\)\s*\d{3}[-\s]\d{2}[-\s]\d{2})', text)
    if phones:
        contacts["phones"] = list(set(phones[:5]))
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    emails = [e for e in emails if not any(skip in e for skip in ["example", "test", "noreply"])]
    if emails:
        contacts["emails"] = list(set(emails[:3]))
    return contacts


def detect_technologies(soup: BeautifulSoup, resp: requests.Response) -> list:
    techs = []
    html = str(soup)
    server = resp.headers.get("Server", "")
    if server:
        techs.append(f"Server: {server}")
    checks = {
        "React": ["react", "reactDOM", "__react"],
        "Vue.js": ["vue.js", "Vue.config"],
        "Angular": ["ng-version", "angular"],
        "WordPress": ["/wp-content/", "wp-includes"],
        "Bitrix": ["bitrix", "BX."],
        "Google Analytics": ["gtag(", "ga(", "UA-"],
        "Google Tag Manager": ["gtm.js", "GTM-"],
        "Facebook Pixel": ["fbq(", "facebook.com/tr"],
        "Cloudflare": ["cloudflare", "__cfduid"],
        "jQuery": ["jquery", "jQuery"],
    }
    for name, signals in checks.items():
        if any(s.lower() in html.lower() for s in signals):
            techs.append(name)
    return techs


def find_price_pages(soup: BeautifulSoup, base_url: str) -> list:
    price_links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = a["href"].lower()
        if any(kw in text or kw in href for kw in PRICE_KEYWORDS):
            full = urljoin(base_url, a["href"])
            parsed = urlparse(full)
            if parsed.netloc == urlparse(base_url).netloc and full not in seen:
                seen.add(full)
                price_links.append(full)
    return price_links[:5]


def scrape_website(url: str) -> ScrapeResult:
    if not url.startswith("http"):
        url = "https://" + url

    result = ScrapeResult(url=url)
    log.info(f"Scraping main page: {url}")

    resp = fetch(url)
    if not resp:
        result.errors.append("Cannot fetch main page")
        result.status = "error"
        return result

    soup = BeautifulSoup(resp.text, "html.parser")

    # Basic meta
    title_tag = soup.find("title")
    result.title = title_tag.get_text(strip=True) if title_tag else ""

    desc_tag = soup.find("meta", attrs={"name": "description"})
    if not desc_tag:
        desc_tag = soup.find("meta", attrs={"property": "og:description"})
    result.description = desc_tag.get("content", "") if desc_tag else ""

    og_name = soup.find("meta", attrs={"property": "og:site_name"})
    result.company_name = og_name.get("content", "") if og_name else urlparse(url).netloc

    # Full text (clean)
    for tag in soup(["script", "style", "noscript", "svg", "path"]):
        tag.decompose()
    result.full_text = " ".join(soup.get_text(" ", strip=True).split())[:8000]

    # Nav items
    nav = soup.find("nav") or soup.find(attrs={"role": "navigation"})
    if nav:
        result.nav_items = [a.get_text(strip=True) for a in nav.find_all("a") if a.get_text(strip=True)][:15]

    result.prices = extract_prices(result.full_text)
    result.social_links = extract_social(soup, url)
    result.contact_info = extract_contacts(soup)
    result.technologies = detect_technologies(soup, resp)
    result.price_pages = find_price_pages(soup, url)

    # Scrape pricing pages
    for purl in result.price_pages[:3]:
        log.info(f"  Scraping pricing page: {purl}")
        time.sleep(0.5)
        presp = fetch(purl)
        if presp:
            psoup = BeautifulSoup(presp.text, "html.parser")
            for tag in psoup(["script", "style"]):
                tag.decompose()
            ptext = " ".join(psoup.get_text(" ", strip=True).split())[:4000]
            new_prices = extract_prices(ptext)
            existing_vals = {p["value"] for p in result.prices}
            for p in new_prices:
                if p["value"] not in existing_vals:
                    result.prices.append(p)
                    existing_vals.add(p["value"])

    result.status = "ok"
    log.info(f"  Done: {len(result.prices)} prices, {len(result.social_links)} socials")
    return result


def scrape_reviews(company_name: str) -> list[dict]:
    """Search for review snippets about the company."""
    snippets = []
    query = f"{company_name} відгуки інтернет провайдер"
    search_url = f"https://www.google.com/search?q={requests.utils.quote(query)}&hl=uk&num=10"

    log.info(f"Searching reviews for: {company_name}")
    resp = fetch(search_url)
    if not resp:
        return snippets

    soup = BeautifulSoup(resp.text, "html.parser")
    # Extract search result snippets
    for div in soup.find_all("div", class_=re.compile(r"(BNeawe|VwiC3b|lEBKkf|s3v9rd)")):
        text = div.get_text(strip=True)
        if len(text) > 40 and any(kw in text.lower() for kw in ["відгук", "якість", "швидкість", "підтримка", "ціна", "рекоменд"]):
            snippets.append({"source": "google_search", "text": text[:300]})
        if len(snippets) >= 8:
            break

    return snippets


if __name__ == "__main__":
    import sys, json
    url = sys.argv[1] if len(sys.argv) > 1 else "https://lanet.ua"
    r = scrape_website(url)
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
