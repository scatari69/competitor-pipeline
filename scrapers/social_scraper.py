"""
Соцмережі: публічні дані (без авторизації)
Facebook Graph (публічні сторінки), Telegram канали, Instagram (публічні профілі)
"""
import requests
import re
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}


@dataclass
class SocialProfile:
    platform: str
    handle: str
    url: str
    followers: Optional[int] = None
    posts_count: Optional[int] = None
    recent_posts: list = field(default_factory=list)
    bio: str = ""
    verified: bool = False
    error: str = ""

    def to_dict(self):
        return asdict(self)


def fetch(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"Social fetch error {url}: {e}")
        return None


def scrape_telegram(handle: str) -> SocialProfile:
    """Telegram public channel preview page."""
    handle = handle.lstrip("@")
    url = f"https://t.me/{handle}"
    profile = SocialProfile(platform="telegram", handle=handle, url=url)

    resp = fetch(f"https://t.me/s/{handle}")
    if not resp:
        profile.error = "cannot fetch"
        return profile

    soup = BeautifulSoup(resp.content, "html.parser")

    # followers / subscribers
    counter = soup.find(class_=re.compile(r"tgme_page_extra"))
    if counter:
        text = counter.get_text()
        m = re.search(r"([\d\s]+)\s*(?:subscriber|members|підписник)", text, re.IGNORECASE)
        if m:
            profile.followers = int(re.sub(r"\s", "", m.group(1)))

    # bio
    desc = soup.find(class_=re.compile(r"tgme_page_description"))
    if desc:
        profile.bio = desc.get_text(strip=True)[:300]

    # recent posts
    posts = []
    for msg in soup.find_all(class_=re.compile(r"tgme_widget_message_text"), limit=5):
        text = msg.get_text(strip=True)[:200]
        if text:
            posts.append(text)
    profile.recent_posts = posts
    profile.posts_count = len(soup.find_all(class_=re.compile(r"tgme_widget_message"), limit=50))

    return profile


def scrape_facebook_page(handle: str) -> SocialProfile:
    """Facebook public page — обмежено без авторизації."""
    url = f"https://www.facebook.com/{handle}"
    profile = SocialProfile(platform="facebook", handle=handle, url=url)

    # Facebook blocks most scraping without login — we use mbasic
    resp = fetch(f"https://mbasic.facebook.com/{handle}")
    if not resp:
        profile.error = "blocked or requires login"
        return profile

    soup = BeautifulSoup(resp.content, "html.parser")
    # Try to get like count / followers from meta
    m = re.search(r"([\d,]+)\s*(?:likes?|people like)", resp.text, re.IGNORECASE)
    if m:
        profile.followers = int(m.group(1).replace(",", ""))

    # About text
    about_section = soup.find(id=re.compile(r"about", re.I))
    if about_section:
        profile.bio = about_section.get_text(strip=True)[:300]

    # Posts (mbasic shows some)
    posts = []
    for p in soup.find_all("p", limit=10):
        text = p.get_text(strip=True)
        if len(text) > 40:
            posts.append(text[:200])
    profile.recent_posts = posts[:5]

    return profile


def scrape_instagram(handle: str) -> SocialProfile:
    """Instagram public profile (very limited without API)."""
    url = f"https://www.instagram.com/{handle}/"
    profile = SocialProfile(platform="instagram", handle=handle, url=url)

    resp = fetch(url)
    if not resp:
        profile.error = "cannot fetch"
        return profile

    # Instagram embeds some data in <script> tags
    text = resp.text
    followers_m = re.search(r'"edge_followed_by":\{"count":(\d+)\}', text)
    if followers_m:
        profile.followers = int(followers_m.group(1))

    posts_m = re.search(r'"edge_owner_to_timeline_media":\{"count":(\d+)', text)
    if posts_m:
        profile.posts_count = int(posts_m.group(1))

    bio_m = re.search(r'"biography":"([^"]*)"', text)
    if bio_m:
        profile.bio = bio_m.group(1).encode().decode("unicode_escape")[:300]

    return profile


def scrape_social_presence(social_links: dict) -> dict:
    """Given a dict of platform->url, scrape each."""
    results = {}
    extractors = {
        "telegram": scrape_telegram,
        "facebook": scrape_facebook_page,
        "instagram": scrape_instagram,
    }

    for platform, url in social_links.items():
        if platform not in extractors:
            continue
        # extract handle from URL
        patterns = {
            "telegram":  r"t\.me/s?/([A-Za-z0-9_]+)",
            "facebook":  r"facebook\.com/(?:pages/[^/]+/)?([A-Za-z0-9._-]+)",
            "instagram": r"instagram\.com/([A-Za-z0-9._-]+)",
        }
        m = re.search(patterns[platform], url, re.IGNORECASE)
        if m:
            handle = m.group(1).rstrip("/")
            log.info(f"  Scraping {platform}: @{handle}")
            try:
                profile = extractors[platform](handle)
                results[platform] = profile.to_dict()
            except Exception as e:
                log.warning(f"  Social error {platform}: {e}")
                results[platform] = {"platform": platform, "error": str(e)}

    return results


if __name__ == "__main__":
    import json
    result = scrape_telegram("lanет_ua")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
