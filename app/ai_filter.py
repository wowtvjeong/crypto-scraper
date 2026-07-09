"""
Groq API로 각 기사의 크립토 관련성/중요도를 0~10점으로 판단.
"""
import os
import json
import re
from typing import List
from groq import Groq

from app.models import RawItem, ScoredArticle

# 모델별로 하루 토큰 한도가 따로 매겨진다. 큰 모델(70b) 한도를 다 썼을 때
# 작은 모델(8b)은 별도 한도라 그대로 쓸 수 있는 경우가 많음.
# GROQ_MODEL 환경변수(또는 GitHub Secrets)로 바꿔치기 가능하게 해둠.
# 정확한 모델 목록/한도는 https://console.groq.com/settings/billing 에서 직접 확인 필요
# (제가 실시간으로 검증한 값이 아님).
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

_client = None


def get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY가 설정되지 않았습니다. .env를 확인하세요.")
        _client = Groq(api_key=api_key)
    return _client


SYSTEM_PROMPT = """너는 크립토(암호화폐) 전문 방송 채널의 뉴스 큐레이터야.
입력으로 기사 제목과 본문 일부가 주어지면 아래 기준으로 판단해서 JSON만 출력해.

기준:
- 크립토(비트코인, 이더리움, 알트코인, 거래소, 규제, ETF, 블록체인 산업 등)와 직접 관련 있는가
- 시청자(암호화폐 입문자~일반 대중)에게 방송 소재로서 중요하거나 흥미로운가
- 단순 광고, 홍보성 보도자료, 중복 뉴스는 낮은 점수

출력 형식 (다른 텍스트 없이 JSON만):
{"is_crypto_related": true or false, "relevance_score": 0~10 사이 정수, "reason": "한 문장 이유"}
"""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"JSON을 찾을 수 없음: {text[:200]}")
    return json.loads(match.group(0))


def score_item(item: RawItem, keywords_boost: List[str] = None) -> ScoredArticle:
    client = get_client()

    user_prompt = f"제목: {item.title}\n본문: {item.content[:800]}"

    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=200,
    )

    raw = completion.choices[0].message.content
    parsed = _extract_json(raw)

    score = float(parsed.get("relevance_score", 0))

    # 키워드 가산점 (설정된 경우)
    if keywords_boost:
        combined = f"{item.title} {item.content}"
        if any(kw in combined for kw in keywords_boost):
            score = min(10, score + 1)

    return ScoredArticle(
        source=item.source,
        source_type=item.source_type,
        title=item.title,
        content=item.content,
        url=item.url,
        published_at=item.published_at,
        relevance_score=score,
        is_crypto_related=bool(parsed.get("is_crypto_related", False)),
        reason=parsed.get("reason", ""),
    )


def score_items(items: List[RawItem], keywords_boost: List[str] = None) -> List[ScoredArticle]:
    scored = []
    for item in items:
        try:
            scored.append(score_item(item, keywords_boost))
        except Exception as e:
            print(f"[ai_filter] 스코어링 실패 ({item.source} / {item.title[:30]}): {e}")
    return scored
