"""
언론사 스크래퍼.

config.yaml의 press_sites 항목에 rss가 있으면 RSS로 수집.
rss가 없고 대신 list_url + selector가 지정돼 있으면 직접 파싱한다.

selector 방식 예시 (config.yaml):
  - name: "some_press"
    list_url: "https://example.com/news/crypto"
    item_selector: "div.article-list li"
    title_selector: "a.title"
    link_selector: "a.title"       # href 속성 사용
    link_attr: "href"
"""
import httpx
import feedparser
from bs4 import BeautifulSoup
from typing import List
from urllib.parse import urljoin

from app.models import RawItem

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


async def scrape_rss(site_name: str, rss_url: str, limit: int = 20) -> List[RawItem]:
    items: List[RawItem] = []
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(rss_url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[press] {site_name} RSS 요청 실패: {e}")
        return items

    feed = feedparser.parse(resp.text)
    for entry in feed.entries[:limit]:
        title = entry.get("title", "").strip()
        summary = entry.get("summary", "") or entry.get("description", "")
        content = BeautifulSoup(summary, "html.parser").get_text().strip()
        link = entry.get("link", "")
        published = entry.get("published", None) or entry.get("updated", None)

        if not title or not link:
            continue

        items.append(RawItem(
            source=site_name,
            source_type="press",
            title=title,
            content=content or title,
            url=link,
            published_at=published,
        ))
    return items


async def scrape_selector(site: dict, limit: int = 20) -> List[RawItem]:
    items: List[RawItem] = []
    list_url = site.get("list_url")
    if not list_url:
        return items

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(list_url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[press] {site['name']} 페이지 요청 실패: {e}")
        return items

    soup = BeautifulSoup(resp.text, "html.parser")
    elements = soup.select(site.get("item_selector", "li"))[:limit]

    for el in elements:
        title_el = el.select_one(site.get("title_selector", "a"))
        link_el = el.select_one(site.get("link_selector", "a"))
        if not title_el or not link_el:
            continue

        title = title_el.get_text().strip()
        href = link_el.get(site.get("link_attr", "href"), "").strip()

        # href가 빈 문자열이면 urljoin()이 list_url 자체를 반환해서
        # "목록 페이지가 기사 URL"이 되는 잘못된 결과가 나옴 — 미리 걸러낸다.
        if not href:
            continue

        url = urljoin(list_url, href)

        if not title or not url:
            continue

        items.append(RawItem(
            source=site["name"],
            source_type="press",
            title=title,
            content=title,  # 목록 페이지에는 보통 본문이 없음. 필요하면 상세 페이지 추가 요청 로직 확장.
            url=url,
            published_at=None,
        ))
    return items


async def scrape_all_press(sites: List[dict], limit_per_site: int = 20) -> List[RawItem]:
    all_items: List[RawItem] = []
    for site in sites:
        if site.get("rss"):
            items = await scrape_rss(site["name"], site["rss"], limit=limit_per_site)
        else:
            items = await scrape_selector(site, limit=limit_per_site)
        all_items.extend(items)
    return all_items
