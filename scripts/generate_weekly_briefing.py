"""
1인 앵커용 주간 크립토 시황 브리핑 방송 대본 생성 (15~20분 분량, 6단 구성).

구성: 오프닝 → 비트코인 → 이더리움·알트코인 → ETF·기관자금 → 온체인 관련 뉴스 → 다음 주 체크포인트 → 클로징

방송 전에 GitHub Actions에서 수동으로("Run workflow") 실행한다 (자동 스케줄 없음).

Groq 무료 티어의 분당 토큰 한도(6000 TPM) 때문에 한 번의 요청으로 5000자 가까운 긴 대본을
만들 수 없어서, 섹션별로 나눠서 여러 번 호출하고 그 사이에 대기시간을 둬서 한도를 지킨다.
그래서 전체 생성에 6~8분 정도 걸린다 (방송 전 미리 준비하는 용도라 문제 없음).

주의: "온체인 데이터" 섹션은 실제 온체인 원본 지표(거래소 유출입량 등)를 직접 가져오는 게
아니라, 수집된 기사 중 온체인 관련 키워드가 들어간 '뉴스'를 모아 정리하는 방식이다.
"다음 주 체크포인트"도 별도 경제 캘린더 데이터 없이, 이번 주 기사에 언급된 예정 이벤트가
있으면 활용하고 없으면 억지로 지어내지 않는다.

[수정 이력]
- 단위 변환 오류(예: "$197.4M" -> "197.4만 달러"로 100배 축소, "$266.5M" -> "2665억"으로
  1000배 확대) 수정. 소형 모델(llama-3.1-8b-instant)에게 달러 단위 환산을 맡기면 자꾸
  틀려서, normalize_usd_millions_in_text()로 소스 기사 단계에서 미리 정확히 계산된
  한국어 억/만 표기로 바꿔서 넘긴다. 모델은 이제 "복사"만 하면 되고 "계산"은 안 한다.
- "다음 주 체크포인트" 섹션이 앞 섹션과 같은 기사를 반복 요약하던 문제 수정.
  categorize_articles()가 실제로 사용된 기사 URL 집합을 반환하도록 바꾸고, 체크포인트
  섹션에는 (1)앞 섹션에서 안 쓰인 기사 중 (2)"예정/앞두고/다가오는" 등 전망성 키워드가
  있는 기사를 우선으로, 없으면 아직 안 쓰인 기사를 넘긴다. 각 섹션의 `or articles`
  전체 폴백도 제거해서 이미 다룬 기사 재사용을 막았다.
- 섹션마다 어투(해라체/합쇼체)가 섞이던 문제 수정. COMMON_RULES에 합쇼체 고정 규칙과
  예시 문장을 명시.
- 섹션마다 "이번 주 올랐다/내렸다"가 다르게 나오던 문제 수정. CoinGecko에서 실제
  BTC/ETH의 7일 등락률을 코드로 직접 계산해서 오프닝·비트코인·체크포인트 섹션에
  공통 근거로 제공. 기사 제목에 나온 개별 가격 언급과 방향이 다르면 이 실측 데이터를
  우선하도록 지시.
- 대본에 사용된 기사의 URL을 확인할 수 있도록, 저장되는 .md 파일과 텔레그램 메시지에
  섹션별 출처(제목+URL) 목록을 별도로 추가 (낭독용 본문과는 분리해서 전송).
- 생성된 문장에서 금액 표현을 추출해 원본 기사 목록과 대조하는 최소한의 자동
  팩트체크(grounding_check)를 추가. 원본에 없는 금액이 발견되면 .md 파일과 텔레그램에
  "⚠️ 자동 팩트체크 경고"로 표시한다 (완전한 사실검증은 아니고 숫자 지어내기 방지용).
"""
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from groq import Groq
from scripts.notify_telegram import send_telegram_message
from app.onchain_data import fetch_onchain_snapshot, format_snapshot_for_prompt, fetch_200w_ma, format_200w_ma_for_prompt

CONFIG_PATH = ROOT / "config.yaml"
DATA_PATH = ROOT / "docs" / "articles.json"
ARCHIVE_DIR = ROOT / "docs" / "archive"
OUTPUT_DIR = ROOT / "docs" / "weekly-briefings"

TELEGRAM_CHUNK_LIMIT = 3500
CALL_INTERVAL_SEC = 65  # Groq 분당 토큰 한도(6000 TPM)를 넘지 않으려고 호출 사이에 대기

BTC_KEYWORDS = ["비트코인", "BTC"]
ETH_ALT_KEYWORDS = [
    "이더리움", "ETH", "알트코인", "리플", "XRP", "솔라나", "SOL",
    "도지코인", "DOGE", "바이낸스코인", "BNB", "카르다노", "ADA",
    "폴카닷", "체인링크", "LINK", "라이트코인", "LTC", "트론", "TRX",
]
ETF_INST_KEYWORDS = ["ETF", "기관", "블랙록", "피델리티", "자산운용", "펀드", "자금 유입", "자금 유출"]
ONCHAIN_KEYWORDS = ["온체인", "고래", "거래소 유입", "거래소 유출", "스테이블코인", "청산", "미결제약정", "순유출", "순유입"]
FORWARD_LOOKING_KEYWORDS = ["예정", "앞두고", "다가오는", "예고", "출시 예정", "발표 예정", "다음 주", "다음달", "다음 달"]

COMMON_RULES = """
- 마크다운 기호(*, #, - 등)나 불릿포인트는 절대 쓰지 말 것 — 그대로 소리내어 읽을 문장으로만 작성
- 사실관계는 반드시 주어진 기사 목록 범위 안에서만 서술하고, 목록에 없는 정보나 임의의 가격 전망·추측·일정은 절대 만들어내지 말 것
- 앵커가 실제로 말하듯 자연스러운 구어체로, 소제목 없이 이어지는 문장으로 작성할 것
- 관련 기사가 부족하면 억지로 분량을 늘리지 말고 짧게 언급만 하고 넘어갈 것
- 문장 종결은 반드시 정중한 합쇼체("~습니다", "~입니다", "~했습니다")로 통일할 것.
  "~했다", "~이다", "~보인다" 같은 해라체(신문 기사체)는 절대 섞어 쓰지 말 것.
  예시: "비트코인 가격이 상승했습니다" (O) / "비트코인 가격이 상승했다" (X)
- 기사 제목·본문에 나온 금액·수치는 이미 정확한 한국어 단위(억/만 달러 등)로
  변환되어 주어지니, 그 표기를 그대로 사용하고 스스로 다시 계산하거나 단위를
  바꿔 쓰지 말 것 (예: "1억 9,740만 달러"를 "197.4만 달러"처럼 임의로 바꾸지 말 것)
"""


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_dt(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def load_recent_articles(days: int = 7) -> list:
    """최근 목록 + 이번달/지난달 아카이브를 합쳐서 지난 N일치 기사를 모은다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen_urls = set()
    collected = []

    if DATA_PATH.exists():
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            for a in json.load(f):
                dt = _parse_dt(a.get("collected_at"))
                if dt and dt >= cutoff and a["url"] not in seen_urls:
                    collected.append(a)
                    seen_urls.add(a["url"])

    now = datetime.now(timezone.utc)
    prev_month = (now.replace(day=1) - timedelta(days=1))
    for month_key in {now.strftime("%Y-%m"), prev_month.strftime("%Y-%m")}:
        path = ARCHIVE_DIR / f"{month_key}.json"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for a in json.load(f):
                dt = _parse_dt(a.get("collected_at"))
                if dt and dt >= cutoff and a["url"] not in seen_urls:
                    collected.append(a)
                    seen_urls.add(a["url"])

    collected.sort(key=lambda a: a["relevance_score"], reverse=True)
    return collected


def _matches(article: dict, keywords: list) -> bool:
    text = f"{article['title']} {article.get('content', '')}".lower()
    return any(kw.lower() in text for kw in keywords)


def categorize_articles(articles: list) -> tuple:
    """제목·본문 키워드 기준으로 섹션별 후보 기사를 나눈다 (근사치 분류).
    우선순위: 비트코인 > 이더리움·알트코인 > ETF·기관 > 온체인
    반환값: (버킷 딕셔너리, 버킷에 실제로 들어간 기사들의 url 집합)
    -- url 집합은 뒤에서 "다음 주 체크포인트" 섹션이 이미 다룬 기사를 피하는 데 쓴다."""
    buckets = {"btc": [], "eth_alt": [], "etf_inst": [], "onchain": []}
    used_urls = set()

    for a in articles:
        if a["url"] in used_urls:
            continue
        if _matches(a, BTC_KEYWORDS):
            buckets["btc"].append(a)
        elif _matches(a, ETH_ALT_KEYWORDS):
            buckets["eth_alt"].append(a)
        elif _matches(a, ETF_INST_KEYWORDS):
            buckets["etf_inst"].append(a)
        elif _matches(a, ONCHAIN_KEYWORDS):
            buckets["onchain"].append(a)
        else:
            continue
        used_urls.add(a["url"])

    return buckets, used_urls


def has_forward_looking_keywords(article: dict) -> bool:
    text = f"{article['title']} {article.get('content', '')}"
    return any(kw in text for kw in FORWARD_LOOKING_KEYWORDS)


# ── 달러 단위(M/million) 표기를 정확한 한국어 억/만으로 변환 ──
# 소형 모델(llama-3.1-8b-instant)이 이 계산을 직접 하면 자주 틀려서
# (예: "$197.4M" -> "197.4만 달러"로 100배 축소, "$266.5M" -> "2665억"으로 확대),
# 모델에게 넘기기 전에 코드에서 미리 정확히 계산해둔다.
_USD_PATTERN = re.compile(r'\$\s?(-?[\d,]+(?:\.\d+)?)\s?(M|million|Million|MM)\b')


def _usd_to_korean(value: float) -> str:
    value = int(round(value))
    sign = "-" if value < 0 else ""
    value = abs(value)
    eok = value // 100_000_000
    man = (value % 100_000_000) // 10_000
    parts = []
    if eok:
        parts.append(f"{eok:,}억")
    if man:
        parts.append(f"{man:,}만")
    if not parts:
        parts.append(f"{value:,}")
    return sign + " ".join(parts) + " 달러"


def normalize_usd_millions_in_text(text: str) -> str:
    def repl(m):
        num = float(m.group(1).replace(",", ""))
        return _usd_to_korean(num * 1_000_000)
    return _USD_PATTERN.sub(repl, text or "")


def build_source_list(articles: list, limit: int = 8) -> str:
    if not articles:
        return "(관련 기사 없음 — 짧게만 언급하거나 생략할 것)"
    lines = []
    for a in articles[:limit]:
        title = normalize_usd_millions_in_text(a["title"])
        reason = normalize_usd_millions_in_text((a.get("reason") or "")[:40])
        lines.append(f"- [{a['relevance_score']:.1f}] {title} ({a['source']}) — {reason}")
    return "\n".join(lines)


def build_reference_block(label: str, articles: list, limit: int = 8) -> str:
    """대본 낭독용이 아닌, 검수/팩트체크용 출처 목록 (제목 — URL)."""
    if not articles:
        return f"[{label}]\n(참고한 기사 없음)"
    lines = [f"[{label}]"]
    for a in articles[:limit]:
        lines.append(f"- {a['title']}\n  {a['url']}")
    return "\n".join(lines)


# ── 생성된 문장에 원본 기사에 없는 금액이 섞여 들어갔는지 확인(완전한 팩트체크는 아니고,
# "숫자를 지어내지 않았는지"를 잡아내는 최소한의 자동 대조) ──
_AMOUNT_RE = re.compile(
    r'-?\d[\d,]*(?:\.\d+)?억(?:\s*\d[\d,]*(?:\.\d+)?만)?\s*달러'
    r'|-?\d[\d,]*(?:\.\d+)?만\s*달러'
)
_RAW_USD_RE = re.compile(r'\$\s?-?[\d,]+(?:\.\d+)?\s?(?:M|million|Million|MM)\b')


def _normalize_amount_str(s: str) -> str:
    return s.replace(",", "").replace(" ", "")


def grounding_check(generated_text: str, source_articles: list) -> list:
    """생성된 섹션 텍스트에서 금액 표현을 뽑아, 소스 기사 목록에 실제로 있던
    금액인지 대조한다. 못 찾으면 '검증 필요' 목록에 넣어 반환한다.
    주의: 완벽한 팩트체크가 아니라 '숫자 지어내기'를 잡아내는 보조 장치일 뿐이다."""
    warnings = []

    # 소스 기사 쪽에서 나올 수 있는 모든 금액 표현(정규화된 원본 + 우리가 미리
    # 변환해둔 한국어 표기)을 모아 '허용된 금액 집합'을 만든다.
    known = set()
    for a in source_articles:
        combined = f"{a.get('title', '')} {a.get('content', '')} {a.get('reason', '')}"
        combined = normalize_usd_millions_in_text(combined)
        for m in _AMOUNT_RE.findall(combined):
            known.add(_normalize_amount_str(m))

    # 1) 모델이 자체적으로 "$XXXM" 원문 표기를 그대로 새로 만들어낸 경우
    #    (우리가 소스 단계에서 이미 다 변환해서 넘겼으므로, 출력에 이게 남아있으면
    #    모델이 소스에 없는 걸 새로 지어냈거나 변환을 무시했다는 신호)
    for m in _RAW_USD_RE.findall(generated_text):
        warnings.append(f"원본 단위 표기가 그대로 남음(변환 누락 의심): '{m}'")

    # 2) 생성된 텍스트의 금액 표현이 소스 목록에 있던 금액과 일치하는지 확인
    for m in _AMOUNT_RE.findall(generated_text):
        if _normalize_amount_str(m) not in known:
            warnings.append(f"소스 기사에서 확인되지 않는 금액: '{m}'")

    return warnings


# ── 이번 주 실제 가격 등락(코드로 직접 계산) ──
# 기사 제목만 보고 모델이 "올랐다/내렸다"를 추론하게 하면 섹션마다 다르게 판단해서
# 서로 모순되는 서술이 나온다. CoinGecko에서 7일 등락률을 직접 가져와 모든 섹션에
# 공통 근거로 제공한다.
async def fetch_weekly_price_summary() -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": "bitcoin,ethereum",
                    "price_change_percentage": "7d",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        print(f"[weekly] 가격 스냅샷 조회 실패: {e}")
        return None

    name_map = {"bitcoin": "비트코인", "ethereum": "이더리움"}
    lines = []
    for coin in data:
        name = name_map.get(coin.get("id"))
        if not name:
            continue
        price = coin.get("current_price")
        change = coin.get("price_change_percentage_7d_in_currency")
        if price is None or change is None:
            continue
        direction = "상승" if change >= 0 else "하락"
        lines.append(f"{name}: 현재가 약 ${price:,.0f}, 지난 7일간 {abs(change):.1f}% {direction}")

    if not lines:
        return None
    return (
        "실제 시세 데이터(이 수치가 절대 기준이며, 기사 제목에 나온 개별 가격 언급과 "
        "방향이 다르면 반드시 이 데이터를 우선할 것):\n" + "\n".join(lines)
    )


def call_groq(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY가 설정되지 않았습니다.")
    client = Groq(api_key=api_key)
    model = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=max_tokens,
    )
    return completion.choices[0].message.content.strip()


def gen_opening(top_articles: list, price_context: str = None) -> str:
    system = f"""너는 1인 진행 크립토 주간 시황 브리핑 방송의 오프닝 작가야.
아래 이번 주 주요 뉴스 목록을 참고해서, 방송 시작 인사와 "이번 주를 한 줄로 요약하면" 하는
느낌의 짧은 도입부를 작성해. 200~300자 내외로 짧게 작성할 것.
{COMMON_RULES}"""
    user = f"이번 주 주요 뉴스:\n\n{build_source_list(top_articles, limit=8)}"
    if price_context:
        user += f"\n\n{price_context}"
    return call_groq(system, user, max_tokens=500)


def gen_section(title: str, role: str, articles: list, target_chars: str, max_tokens: int,
                 extra_context: str = None, avoid_repeat: bool = False) -> str:
    system = f"""너는 1인 진행 크립토 주간 시황 브리핑 방송의 작가야.
지금 작성할 부분은 "{title}" 섹션이야. {role}
분량은 {target_chars} 내외로 작성해.
{COMMON_RULES}"""
    if avoid_repeat:
        system += "\n- 이 방송의 앞선 섹션들(비트코인/이더리움·알트코인/ETF·기관/온체인)에서 이미 " \
                   "다룬 내용은 반복해서 요약하지 말고, 아래 목록에 있는 새로운 내용이나 " \
                   "다가오는 이벤트 위주로 짧게 짚어줄 것."

    user = f"이번 주 관련 뉴스 목록:\n\n{build_source_list(articles, limit=8)}"
    if extra_context:
        user += f"\n\n{extra_context}"
    return call_groq(system, user, max_tokens=max_tokens)


def split_for_telegram(text: str, limit: int = TELEGRAM_CHUNK_LIMIT) -> list:
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def main():
    articles = load_recent_articles(days=7)
    print(f"[weekly] 지난 7일간 수집된 기사: {len(articles)}건")

    if not articles:
        print("[weekly] 최근 7일간 기사가 없어 브리핑을 생성하지 않습니다.")
        return

    buckets, used_urls = categorize_articles(articles)
    print(
        f"[weekly] 분류 결과 — 비트코인 {len(buckets['btc'])}건 / "
        f"이더리움·알트코인 {len(buckets['eth_alt'])}건 / "
        f"ETF·기관 {len(buckets['etf_inst'])}건 / "
        f"온체인 {len(buckets['onchain'])}건"
    )

    print("[weekly] 이번 주 실제 시세(BTC/ETH) 조회 중 (CoinGecko)...")
    price_context = await fetch_weekly_price_summary()
    print(f"[weekly] 시세 조회: {'성공' if price_context else '실패 — 기사 기반으로만 작성'}")

    segments = []  # (transition_or_None, content, label, source_articles)

    print("[weekly] 오프닝 생성 중...")
    opening_sources = articles[:10]
    segments.append((None, gen_opening(opening_sources, price_context=price_context), "오프닝", opening_sources))

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] 비트코인 섹션 생성 중...")
    segments.append((
        "비트코인 이슈부터 살펴보겠습니다.",
        gen_section(
            "비트코인 주요 이슈",
            "비트코인 가격 동향과 관련 주요 뉴스를 중심으로 정리해.",
            buckets["btc"], "800~1000자", max_tokens=1100,
            extra_context=price_context,
        ),
        "비트코인", buckets["btc"],
    ))

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] 이더리움·알트코인 섹션 생성 중...")
    segments.append((
        "이어서 이더리움과 알트코인 시장 살펴보겠습니다.",
        gen_section(
            "이더리움·알트코인",
            "이더리움 및 주요 알트코인의 가격·이슈를 중심으로 정리해.",
            buckets["eth_alt"], "800~1000자", max_tokens=1100,
        ),
        "이더리움·알트코인", buckets["eth_alt"],
    ))

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] ETF·기관자금 섹션 생성 중...")
    segments.append((
        "ETF와 기관 자금 흐름도 짚어보겠습니다.",
        gen_section(
            "ETF·기관 자금 흐름",
            "ETF 관련 소식과 기관 투자자의 자금 유입·유출 동향을 중심으로 정리해.",
            buckets["etf_inst"], "600~800자", max_tokens=900,
        ),
        "ETF·기관 자금", buckets["etf_inst"],
    ))

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] 온체인 네트워크 지표 조회 중 (mempool.space)...")
    onchain_snapshot = await fetch_onchain_snapshot()
    onchain_context = format_snapshot_for_prompt(onchain_snapshot)
    print(f"[weekly] 온체인 지표: {'조회 성공' if onchain_snapshot else '조회 실패 — 뉴스만으로 작성'}")

    print("[weekly] 200주 이동평균선 계산 중 (CoinGecko)...")
    ma_200w = await fetch_200w_ma()
    ma_context = format_200w_ma_for_prompt(ma_200w)
    print(f"[weekly] 200주 이평선: {'계산 성공' if ma_200w else '계산 실패'}")

    combined_context = "\n".join(filter(None, [onchain_context, ma_context])) or None

    print("[weekly] 온체인 관련 뉴스 섹션 생성 중...")
    segments.append((
        "온체인 관련 소식으로 넘어가 보면",
        gen_section(
            "온체인 관련 뉴스 및 네트워크 지표",
            "고래 이동, 거래소 자금 유출입, 스테이블코인 발행량 등 온체인 관련 보도와, "
            "함께 제공되는 실시간 비트코인 네트워크 지표(해시레이트·멤풀·수수료) 및 "
            "200주 이동평균선 대비 현재가 위치를 자연스럽게 엮어서 정리해. "
            "뉴스 부분은 실제 원본 데이터가 아니라 보도된 뉴스를 종합하는 것임을 감안해.",
            buckets["onchain"], "600~900자", max_tokens=1000,
            extra_context=combined_context,
        ),
        "온체인", buckets["onchain"],
    ))

    # ── "다음 주 체크포인트": 앞 섹션에서 다루지 않은 기사만 후보로 사용 ──
    # 전망 키워드(예정/앞두고/다가오는 등)가 있는 기사를 우선하고, 없으면
    # 아직 언급되지 않은 기사 중 상위 점수 기사로 대체한다.
    leftover = [a for a in articles if a["url"] not in used_urls]
    forward_looking = [a for a in leftover if has_forward_looking_keywords(a)]
    checkpoint_source = forward_looking or leftover
    print(
        f"[weekly] 체크포인트 후보 — 미사용 기사 {len(leftover)}건 중 "
        f"전망성 키워드 포함 {len(forward_looking)}건"
    )

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] 다음 주 체크포인트 생성 중...")
    segments.append((
        "마지막으로 다음 주에 주목할 부분입니다.",
        gen_section(
            "다음 주 체크포인트",
            "이번 주 기사들 중 '예정', '앞두고', '다가오는' 같은 표현으로 향후 일정이 언급된 내용이 "
            "있다면 그것을 짚어주고, 없다면 이번 주 흐름을 토대로 다음 주에 지켜볼 만한 부분을 "
            "짧게 짚어줘. 목록에 없는 구체적 날짜·일정을 지어내지 말 것.",
            checkpoint_source, "500~700자", max_tokens=800,
            extra_context=price_context,
            avoid_repeat=True,
        ),
        "다음 주 체크포인트", checkpoint_source,
    ))

    closing = "오늘 준비한 주간 크립토 시황 브리핑은 여기까지입니다. 다음 주에 또 새로운 소식으로 찾아뵙겠습니다. 지금까지 시청해주셔서 감사합니다."
    segments.append((None, closing, None, []))

    script_parts = []
    reference_blocks = []
    all_warnings = []

    for transition, content, label, source_articles in segments:
        if transition:
            script_parts.append(transition)
        script_parts.append(content)

        if not label:
            continue  # 클로징 등 출처가 없는 세그먼트는 건너뜀

        warns = grounding_check(content, source_articles)
        if warns:
            all_warnings.append(f"[{label}]\n" + "\n".join(f"  - {w}" for w in warns))

        reference_blocks.append(build_reference_block(label, source_articles))

    script = "\n\n".join(script_parts)
    references_text = "\n\n".join(reference_blocks)

    print(f"[weekly] 최종 대본 길이: {len(script)}자")
    if all_warnings:
        print("[weekly] ⚠️ 팩트체크 경고:")
        for w in all_warnings:
            print(w)
    else:
        print("[weekly] 팩트체크: 이상 없음 (소스에 없는 금액 표현 미발견)")

    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    date_str = now_kst.strftime("%Y-%m-%d")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{date_str}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# 주간 시황 브리핑 대본 ({date_str})\n\n")
        f.write(f"{script}\n\n")
        f.write("---\n\n## 출처 (낭독용 아님 — 검수/팩트체크용)\n\n")
        f.write(references_text + "\n")
        if all_warnings:
            f.write("\n## ⚠️ 자동 팩트체크 경고\n\n")
            f.write(
                "아래 표현은 소스 기사 목록에서 정확히 확인되지 않았습니다. "
                "AI가 숫자를 잘못 옮겼을 가능성이 있으니 방송 전 원문과 대조해주세요.\n\n"
            )
            f.write("\n\n".join(all_warnings) + "\n")
    print(f"[weekly] 대본 파일 저장 완료: {out_path}")

    est_minutes = max(1, len(script) // 280)
    header = (
        f"📋 주간 시황 브리핑 대본 ({date_str})\n"
        f"(총 {len(script)}자, 약 {est_minutes}분 분량 — 방송 전 검토 후 사용하세요)\n\n"
    )
    full_text = header + script

    for chunk in split_for_telegram(full_text):
        try:
            send_telegram_message(chunk)
        except Exception as e:
            print(f"[weekly] 텔레그램 전송 실패: {e}")

    # 출처 목록은 낭독용 본문과 분리해서 별도 메시지로 전송 (검수용)
    ref_header = "🔗 출처 목록 (아래는 방송에서 읽지 않는 검수·팩트체크용 링크입니다)\n\n"
    for chunk in split_for_telegram(ref_header + references_text):
        try:
            send_telegram_message(chunk)
        except Exception as e:
            print(f"[weekly] 텔레그램 전송 실패(출처): {e}")

    # 팩트체크 경고가 있으면 눈에 띄게 별도 알림
    if all_warnings:
        warn_text = "⚠️ 자동 팩트체크 경고 — 아래 표현은 원본 기사에서 확인되지 않았습니다. 방송 전 꼭 확인해주세요.\n\n" + "\n\n".join(all_warnings)
        for chunk in split_for_telegram(warn_text):
            try:
                send_telegram_message(chunk)
            except Exception as e:
                print(f"[weekly] 텔레그램 전송 실패(경고): {e}")

    print("[weekly] 완료")


if __name__ == "__main__":
    asyncio.run(main())
