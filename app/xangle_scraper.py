"""
Xangle 리서치 API 직접 연동.

xangle.io는 React 기반 SPA라 순수 HTML 스크래핑이 안 통해서(내용이 자바스크립트로
나중에 채워짐), 브라우저 개발자도구(F12 → Network 탭)에서 실제 데이터를 주는
내부 API를 찾아서 그걸 직접 호출하는 방식으로 만들었다.

API: https://portal.xangle.io/research/v1/list?lang=ko&offset=0
기사 상세 페이지: https://xangle.io/research/detail/{content_id}
"""
import httpx
from datetime import datetime, timezone
from typing import List

from app.models import RawItem

API_URL = "https://portal.xangle.io/research/v1/list"
DETAIL_URL_TEMPLATE = "https://xangle.io/research/detail/{content_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://xangle.io/",
}


async def scrape_xangle(limit: int = 20) -> List[RawItem]:
    items: List[RawItem] = []

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
            resp = await client.get(API_URL, params={"lang": "ko", "offset": 0})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        print(f"[xangle] API 요청 실패: {e}")
        return items
    except ValueError as e:
        print(f"[xangle] 응답 JSON 파싱 실패: {e}")
        return items

    articles = (data.get("result") or {}).get("data", [])

    for a in articles[:limit]:
        title = (a.get("title") or "").strip()
        content_id = a.get("content_id")
        summary = (a.get("summary") or "").strip()
        published_at = a.get("published_at")

        if not title or not content_id:
            continue

        url = DETAIL_URL_TEMPLATE.format(content_id=content_id)

        items.append(RawItem(
            source="xangle",
            source_type="press",
            title=title,
            content=summary or title,
            url=url,
            published_at=published_at,
        ))

    print(f"[xangle] 수집: {len(items)}건")
    return items
