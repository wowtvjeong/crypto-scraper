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
입력으로 기사 제목과 본문 일부가 주어지면, 아래 구간별 기준에 따라 점수를 매겨서 JSON만 출력해.
점수는 0~10 사이이며 0.5 단위까지 세밀하게 매길 수 있어. 아래 기준에 나온 값(7, 7.5, 8, 8.5, 9 등)에
기계적으로 몰아주지 말고, 실제 파급력을 보고 그 사이 값(예: 7.5가 아니라 7.3처럼 느껴지면 7.0이나 7.5 중
더 가까운 쪽)도 자유롭게 사용해. 라운드 넘버(7, 8, 9)에만 점수가 몰리지 않도록 각 기사의 파급력 차이를
세밀하게 구분해서 매겨야 해.

점수 구간별 기준:
- 0~2점: 크립토와 무관하거나, 단순 광고·홍보성 보도자료·중복 뉴스
- 3~4점: 크립토 관련은 있으나 시청자에게 부수적인 소식 (소규모 파트너십, 사소한 이벤트, 개인 의견 트윗 등)
- 5~6점: 크립토 업계의 일반적인 뉴스 (통상적인 시황 코멘트, 소규모 프로젝트 소식, 반복적인 리포트)
- 7점: 시청자에게 유의미하지만 영향 범위가 제한적인 뉴스 (특정 코인·프로젝트 한정 이슈, 중견 기업의 파트너십)
- 7.5점: 7점보다 관련 주체가 더 크거나(유명 거래소·중견 기관 등) 여러 매체가 동시에 다룰 만한 뉴스, 그러나 아직 시장 전체를 흔들 정도는 아님
- 8점: 업계 전반이 주목할 만한 뉴스 — 대형 거래소·유명 프로젝트·구체적 금액이 동반된 정책/자금 관련 소식
- 8.5점: 8점보다 파급력이 크거나 여러 자산·시장 전반에 영향을 줄 가능성이 있는 뉴스 (다만 아직 '즉시 속보'급 확정 사건은 아님)
- 9점: 방송에서 바로 다뤄야 할 만큼 중요한 뉴스 (대형 거래소·기관의 중대 발표, 유의미한 규제 동향, 큰 폭의 가격 변동)
- 9.5~10점: 즉시 속보로 다뤄야 하는 사안만 해당 — ETF 승인/거부 공식 발표, 대형 해킹·거래소 파산·지급불능, 주요국 정부의 전면적 규제 변경(법안 통과/시행), 비트코인·이더리움 등 주요 자산의 급격한 가격 폭등락(단시간 내 두 자릿수 % 변동), SEC·CFTC 등 규제당국의 공식 결정. 예상·추측·루머·개인 의견은 이 구간에 해당하지 않음.

7~9점 구간을 나눌 때 참고할 판단축 (여러 개가 겹칠수록 높은 점수):
- 관련 주체의 규모 (군소 프로젝트 < 중견 기업 < 대형 거래소·기관 < 국가/규제당국)
- 파급 범위 (특정 코인 1개 한정 < 특정 섹터 전체 < 크립토 시장 전반)
- 확정성 (추측·전망 < 공식 발표 예정 < 이미 확정·시행됨)
- 다수 매체가 동시에 다룰 만한 뉴스일수록 높게 (한 매체만 단독으로 다룰 법한 소식은 상대적으로 낮게)

판단 시 유의할 것:
- '확정 발표'는 '예상·전망' 기사보다 높게, '루머·추측'은 낮게 평가한다
- 정기적인 시세 브리핑, 데일리 리포트, 반복되는 옵션 시장 통계는 5~6점을 넘기지 않는다
- 유명인 발언이라도 구체적인 정책·행동 변화가 수반되지 않으면 7점을 넘기지 않는다
- 9.5점 이상은 매우 드물어야 한다 — 하루에 몇 건 나오지 않는 수준의 진짜 속보에만 부여한다

⚠️ 특히 주의: 아래 유형은 실제로 자주 과대평가되는 패턴이야. 절대 7점을 넘기지 마 (보통 4~6점):
- "OO달러 회복/돌파/하락" 같은 단순 가격 수준 언급성 제목 (예: "비트코인 6만4000달러 회복") — 실제로 시장을 흔드는 '급락/급등'이 아니라 그냥 현재가를 알리는 정기 리포트라면 낮게
- "고래 트레이더가 OO억 규모 포지션을 열었다/청산했다" 같은 개별 고래·트레이더 추적성 리포트 — 개별 지갑 동향일 뿐 시장 전체에 영향을 준 사건이 아니면 낮게
- "매달 정기적으로 OO 물량이 언락/해제된다" 같은 예정된 정기 이벤트 리포트
- 옵션시장 미결제약정, 청산 규모 등 매시간/매일 반복되는 파생상품 통계 리포트

이런 유형은 제목에 큰 숫자나 '비트코인', '이더리움' 같은 단어가 들어있어도 그 자체로는 점수를 올리는 근거가 되지 않아 — 실제로 시장 전체에 영향을 준 '사건'인지가 기준이야. 예를 들어 "비트코인 하루만에 12% 급락, OOO 사태로 패닉셀 확산" 같은 진짜 급변동·사건 기사와,
그냥 "비트코인 6만4000달러대, 관망세 지속" 같은 현황 리포트는 완전히 다르게 평가해야 해.

출력 형식 (다른 텍스트 없이 JSON만):
{"is_crypto_related": true or false, "relevance_score": 0~10 사이 숫자(0.5 단위 가능), "reason": "한 문장 이유"}
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
