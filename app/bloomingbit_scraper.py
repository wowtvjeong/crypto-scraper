"""
Bloomingbit(블루밍비트) 전용 스크래퍼.

일반 CSS 셀렉터 대신, 페이지에 검색엔진최적화(SEO)용으로 심어둔
<script type="application/ld+json"> 안의 구조화 데이터(JSON-LD, ItemList)를
직접 파싱한다. React 기반이라 일반 HTML 셀렉터로는 내용을 못 읽지만,
이 JSON-LD 블록은 서버가 미리 렌더링해서 넣어주기 때문에 안정적으로 읽힌다.
"""
import json
import httpx
from bs4 import BeautifulSoup
from typing import List

from app.models import RawItem

LIST_URL = "https://bloomingbit.io"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


async def scrape_bloomingbit(limit: int = 20) -> List[RawItem]:
    items: List[RawItem] = []

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(LIST_URL)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[bloomingbit] 요청 실패: {e}")
        return items

    soup = BeautifulSoup(resp.text, "html.parser")

    item_list = None
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("@type") == "ItemList":
            item_list = data
            break

    if not item_list:
        print("[bloomingbit] ItemList 구조화 데이터를 찾지 못함 — 사이트 구조가 바뀌었을 수 있음")
        return items

    for entry in item_list.get("itemListElement", [])[:limit]:
        title = (entry.get("name") or "").strip()
        url = entry.get("url", "")

        if not title or not url:
            continue

        items.append(RawItem(
            source="bloomingbit",
            source_type="press",
            title=title,
            content=title,  # 목록 JSON-LD에는 요약이 따로 없어 제목으로 대신함
            url=url,
            published_at=None,
        ))

    print(f"[bloomingbit] 수집: {len(items)}건")
    return items
