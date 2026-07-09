"""
CryptoPanic 뉴스 애그리게이터 API 연동.
여러 크립토 언론사의 뉴스를 한 번의 API 호출로 모아 가져온다.

⚠️ 요금제: 무료 티어 존재 여부/조건이 자주 바뀌는 영역이라 이 코드 작성 시점 기준으로
   확정적으로 안내하기 어렵습니다. 가입 전 반드시 https://cryptopanic.com/developers/api/
   에서 직접 최신 요금제를 확인하세요. config.yaml의 enable_cryptopanic이 기본 false로
   꺼져 있는 것도 이 때문입니다.

사전 준비 (사용하기로 결정한 경우):
1. https://cryptopanic.com/developers/api/ 에서 계정 가입, 요금제 확인
2. 발급받은 auth_token을 CRYPTOPANIC_API_KEY로 등록
3. config.yaml에서 enable_cryptopanic: true로 변경

주의:
- 무료/저가 티어는 기사 본문 요약을 제공하지 않을 수 있어 title을 content로 대신 사용
- url은 cryptopanic.com 리다이렉트 링크로 오는 경우가 많음 — 클릭하면 원문으로 이동됨
"""
import os
import httpx
from typing import List

from app.models import RawItem

API_URL = "https://cryptopanic.com/api/v1/posts/"


async def scrape_cryptopanic(limit: int = 30) -> List[RawItem]:
    token = os.environ.get("CRYPTOPANIC_API_KEY")
    if not token:
        print("[cryptopanic] CRYPTOPANIC_API_KEY 미설정 — 건너뜀")
        return []

    items: List[RawItem] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                API_URL,
                params={"auth_token": token, "public": "true"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        print(f"[cryptopanic] 요청 실패: {e}")
        return items

    for post in data.get("results", [])[:limit]:
        title = (post.get("title") or "").strip()
        url = post.get("url", "")
        source_domain = (post.get("source") or {}).get("domain", "cryptopanic")
        published = post.get("published_at")

        if not title or not url:
            continue

        items.append(RawItem(
            source=source_domain,
            source_type="api",
            title=title,
            content=title,
            url=url,
            published_at=published,
        ))

    print(f"[cryptopanic] 수집: {len(items)}건")
    return items
