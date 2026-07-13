"""
1인 앵커용 주간 크립토 시황 브리핑 방송 대본 생성.

방송 전에 GitHub Actions에서 수동으로("Run workflow") 실행한다 (자동 스케줄 없음).
지난 7일간 수집·채점된 기사 중 관련도 높은 것들을 골라, Groq가 그것들을 종합해서
① 가격동향 ② 규제·정책 동향 ③ 주요 사건·사고 순서로 구성된
"그대로 소리내어 읽을 수 있는" 자연스러운 구어체 방송 대본으로 다시 써준다.

낱개 기사를 하나하나 나열하는 게 아니라, AI가 비슷한 내용을 묶고 앵커가
화제를 전환하며 이어 말하는 것처럼 재구성한다는 점이 일간 브리핑(generate_briefing.py)과
다른 부분이다.

결과는 (1) docs/weekly-briefings/{날짜}.md 파일로 저장되고 (2) 텔레그램으로도 전송된다.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from groq import Groq
from scripts.notify_telegram import send_telegram_message

CONFIG_PATH = ROOT / "config.yaml"
DATA_PATH = ROOT / "docs" / "articles.json"
ARCHIVE_DIR = ROOT / "docs" / "archive"
OUTPUT_DIR = ROOT / "docs" / "weekly-briefings"

TELEGRAM_CHUNK_LIMIT = 3500  # 텔레그램 메시지 4096자 제한보다 여유 있게 잘라서 보냄

SYSTEM_PROMPT = """너는 1인 진행 크립토 시황 브리핑 방송의 작가야.
아래는 지난 7일간 수집된 크립토 뉴스 목록(AI가 매긴 관련도 점수와 한줄 이유 포함)이야.
이걸 보고, 앵커가 방송에서 그대로 소리내어 읽을 수 있는 자연스러운 구어체 대본을 작성해.

반드시 지킬 것:
1. 아래 세 섹션 순서로 구성: ① 가격동향(시황) ② 규제·정책 동향 ③ 주요 사건·사고
2. 각 섹션은 소제목을 달지 말고, 앵커가 자연스럽게 화제를 전환하며 이어 말하는 느낌으로 작성
   (예: "이번 주 가격 흐름부터 보겠습니다", "규제 쪽 소식으로 넘어가 보면" 같은 자연스러운 전환 문장 사용)
3. 목록에 있는 여러 기사 중 실제로 방송에서 다룰 만큼 중요한 것만 골라 종합하고,
   비슷한 내용의 기사는 하나로 묶어서 설명할 것 (기사 하나하나를 다 읽지 말 것)
4. 마크다운 기호(*, #, - 등)나 불릿포인트는 절대 쓰지 말 것 — 그대로 소리내어 읽을 문장으로만 작성
5. 전체 분량은 1200~2000자 내외 (5~8분 분량의 1인 브리핑 방송 기준)
6. 시작 인사와 마무리 멘트를 자연스럽게 포함할 것
7. 사실관계는 반드시 아래 목록 범위 안에서만 서술하고, 목록에 없는 정보나
   임의의 가격 전망·추측은 절대 만들어내지 말 것
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
    """최근 목록 + 이번달/지난달 아카이브를 합쳐서 지난 N일치 기사를 모은다
    (월 경계를 넘어가는 주간 조회에 대비해 아카이브도 함께 확인)."""
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


def build_source_list(articles: list) -> str:
    lines = []
    for a in articles:
        lines.append(f"- [{a['relevance_score']:.1f}] {a['title']} ({a['source']}) — {a.get('reason', '')}")
    return "\n".join(lines)


def generate_script(articles: list) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY가 설정되지 않았습니다.")

    client = Groq(api_key=api_key)
    model = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

    user_prompt = f"지난 7일간 수집된 크립토 뉴스 목록:\n\n{build_source_list(articles)}"

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=2000,
    )
    return completion.choices[0].message.content.strip()


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


def main():
    articles = load_recent_articles(days=7)
    print(f"[weekly] 지난 7일간 수집된 기사: {len(articles)}건")

    if not articles:
        print("[weekly] 최근 7일간 기사가 없어 브리핑을 생성하지 않습니다.")
        return

    # 프롬프트 크기 관리를 위해 관련도 상위 기사만 최대 50건 사용
    top_articles = articles[:50]
    print(f"[weekly] 대본 생성에 사용할 상위 기사: {len(top_articles)}건")

    script = generate_script(top_articles)

    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    date_str = now_kst.strftime("%Y-%m-%d")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{date_str}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# 주간 시황 브리핑 대본 ({date_str})\n\n{script}\n")
    print(f"[weekly] 대본 파일 저장 완료: {out_path}")

    header = (
        f"📋 주간 시황 브리핑 대본 ({date_str})\n"
        f"(기사 {len(top_articles)}건을 종합 — 방송 전 검토 후 사용하세요)\n\n"
    )
    full_text = header + script

    for chunk in split_for_telegram(full_text):
        try:
            send_telegram_message(chunk)
        except Exception as e:
            print(f"[weekly] 텔레그램 전송 실패: {e}")

    print("[weekly] 완료")


if __name__ == "__main__":
    main()
