"""
Coinness(코인니스) API 직접 연동.

coinness.com/article도 React 기반 SPA라 HTML 스크래핑이 안 통해서,
브라우저 개발자도구(F12 → Network 탭)에서 실제 데이터를 주는 API를 찾아서 사용.

API: https://api.coinness.com/feed/v1/articles?limit=25&section=latest&categoryId=0&languageCode=ko

특이사항: link 필드가 coinness 자체 페이지가 아니라 원문 매체(블록미디어 등)
링크를 그대로 제공한다. 그래서 이미 개별로 등록해둔 언론사와 같은 기사가
겹쳐서 잡힐 수 있음 (URL이 같으면 자동으로 중복 제거되니 큰 문제는 아님).
"""
import httpx
from typing import List

from app.models import RawItem

API_URL = "https://api.coinness.com/feed/v1/articles"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://coinness.com/",
}


async def scrape_coinness(limit: int = 25) -> List[RawItem]:
    items: List[RawItem] = []

    params = {
        "limit": limit,
        "section": "latest",
        "categoryId": 0,
        "languageCode": "ko",
    }

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
            resp = await client.get(API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        print(f"[coinness] API 요청 실패: {e}")
        return items
    except ValueError as e:
        print(f"[coinness] 응답 JSON 파싱 실패: {e}")
        return items

    # 응답이 배열 자체이거나 {"data": [...]} 형태일 수 있어 둘 다 대응
    articles = data if isinstance(data, list) else data.get("data", data.get("result", []))

    for a in articles[:limit]:
        title = (a.get("title") or "").strip()
        url = a.get("link") or ""
        description = (a.get("description") or "").strip()
        published_at = a.get("publishAt")

        if not title or not url:
            continue

        items.append(RawItem(
            source="coinness",
            source_type="press",
            title=title,
            content=description or title,
            url=url,
            published_at=published_at,
        ))

    print(f"[coinness] 수집: {len(items)}건")
    return items
