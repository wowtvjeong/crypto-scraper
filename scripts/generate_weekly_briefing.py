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
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

COMMON_RULES = """
- 마크다운 기호(*, #, - 등)나 불릿포인트는 절대 쓰지 말 것 — 그대로 소리내어 읽을 문장으로만 작성
- 사실관계는 반드시 주어진 기사 목록 범위 안에서만 서술하고, 목록에 없는 정보나 임의의 가격 전망·추측·일정은 절대 만들어내지 말 것
- 앵커가 실제로 말하듯 자연스러운 구어체로, 소제목 없이 이어지는 문장으로 작성할 것
- 관련 기사가 부족하면 억지로 분량을 늘리지 말고 짧게 언급만 하고 넘어갈 것
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


def categorize_articles(articles: list) -> dict:
    """제목·본문 키워드 기준으로 섹션별 후보 기사를 나눈다 (근사치 분류).
    우선순위: 비트코인 > 이더리움·알트코인 > ETF·기관 > 온체인"""
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

    return buckets


def build_source_list(articles: list, limit: int = 8) -> str:
    if not articles:
        return "(관련 기사 없음 — 짧게만 언급하거나 생략할 것)"
    lines = []
    for a in articles[:limit]:
        reason = (a.get("reason") or "")[:40]
        lines.append(f"- [{a['relevance_score']:.1f}] {a['title']} ({a['source']}) — {reason}")
    return "\n".join(lines)


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


def gen_opening(top_articles: list) -> str:
    system = f"""너는 1인 진행 크립토 주간 시황 브리핑 방송의 오프닝 작가야.
아래 이번 주 주요 뉴스 목록을 참고해서, 방송 시작 인사와 "이번 주를 한 줄로 요약하면" 하는
느낌의 짧은 도입부를 작성해. 200~300자 내외로 짧게 작성할 것.
{COMMON_RULES}"""
    user = f"이번 주 주요 뉴스:\n\n{build_source_list(top_articles, limit=8)}"
    return call_groq(system, user, max_tokens=500)


def gen_section(title: str, role: str, articles: list, target_chars: str, max_tokens: int, extra_context: str = None) -> str:
    system = f"""너는 1인 진행 크립토 주간 시황 브리핑 방송의 작가야.
지금 작성할 부분은 "{title}" 섹션이야. {role}
분량은 {target_chars} 내외로 작성해.
{COMMON_RULES}"""
    user = f"이번 주 관련 뉴스 목록:\n\n{build_source_list(articles, limit=8)}"
    if extra_context:
        user += f"\n\n실시간 온체인 네트워크 지표(참고용, 자연스럽게 문장에 녹여서 언급할 것):\n{extra_context}"
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

    buckets = categorize_articles(articles)
    print(
        f"[weekly] 분류 결과 — 비트코인 {len(buckets['btc'])}건 / "
        f"이더리움·알트코인 {len(buckets['eth_alt'])}건 / "
        f"ETF·기관 {len(buckets['etf_inst'])}건 / "
        f"온체인 {len(buckets['onchain'])}건"
    )

    segments = []  # (transition_or_None, content)

    print("[weekly] 오프닝 생성 중...")
    segments.append((None, gen_opening(articles[:10])))

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] 비트코인 섹션 생성 중...")
    segments.append((
        "비트코인 이슈부터 살펴보겠습니다.",
        gen_section(
            "비트코인 주요 이슈",
            "비트코인 가격 동향과 관련 주요 뉴스를 중심으로 정리해.",
            buckets["btc"] or articles, "800~1000자", max_tokens=1100,
        ),
    ))

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] 이더리움·알트코인 섹션 생성 중...")
    segments.append((
        "이어서 이더리움과 알트코인 시장 살펴보겠습니다.",
        gen_section(
            "이더리움·알트코인",
            "이더리움 및 주요 알트코인의 가격·이슈를 중심으로 정리해.",
            buckets["eth_alt"] or articles, "800~1000자", max_tokens=1100,
        ),
    ))

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] ETF·기관자금 섹션 생성 중...")
    segments.append((
        "ETF와 기관 자금 흐름도 짚어보겠습니다.",
        gen_section(
            "ETF·기관 자금 흐름",
            "ETF 관련 소식과 기관 투자자의 자금 유입·유출 동향을 중심으로 정리해.",
            buckets["etf_inst"] or articles, "600~800자", max_tokens=900,
        ),
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
            buckets["onchain"] or articles, "600~900자", max_tokens=1000,
            extra_context=combined_context,
        ),
    ))

    time.sleep(CALL_INTERVAL_SEC)
    print("[weekly] 다음 주 체크포인트 생성 중...")
    segments.append((
        "마지막으로 다음 주에 주목할 부분입니다.",
        gen_section(
            "다음 주 체크포인트",
            "이번 주 기사들 중 '예정', '앞두고', '다가오는' 같은 표현으로 향후 일정이 언급된 내용이 "
            "있다면 그것을 짚어주고, 없다면 이번 주 흐름을 토대로 다음 주에 지켜볼 만한 부분을 "
            "짧게 짚어줘. 목록에 없는 구체적 날짜·일정을 지어내지 말 것.",
            articles, "500~700자", max_tokens=800,
        ),
    ))

    closing = "오늘 준비한 주간 크립토 시황 브리핑은 여기까지입니다. 다음 주에 또 새로운 소식으로 찾아뵙겠습니다. 지금까지 시청해주셔서 감사합니다."
    segments.append((None, closing))

    script_parts = []
    for transition, content in segments:
        if transition:
            script_parts.append(transition)
        script_parts.append(content)
    script = "\n\n".join(script_parts)

    print(f"[weekly] 최종 대본 길이: {len(script)}자")

    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    date_str = now_kst.strftime("%Y-%m-%d")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{date_str}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# 주간 시황 브리핑 대본 ({date_str})\n\n{script}\n")
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

    print("[weekly] 완료")


if __name__ == "__main__":
    asyncio.run(main())
