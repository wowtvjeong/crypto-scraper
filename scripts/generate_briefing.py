"""
하루 두 번(아침 8시 / 저녁 8시, 한국시간) 주요 크립토 뉴스를 텔레그램으로 브리핑.
.github/workflows/briefing.yml 에서 스케줄 실행.

이미 채점 완료된 docs/articles.json을 재활용하므로 AI를 다시 호출하지 않는다
(Groq 토큰을 추가로 쓰지 않음).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from scripts.notify_telegram import send_telegram_message

CONFIG_PATH = ROOT / "config.yaml"
DATA_PATH = ROOT / "docs" / "articles.json"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_articles() -> list:
    if DATA_PATH.exists():
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def main():
    config = load_config()
    briefing_cfg = config.get("briefing", {})
    if not briefing_cfg.get("enabled", True):
        print("[briefing] 비활성화 상태 — 종료")
        return

    top_n = briefing_cfg.get("top_n", 5)
    lookback_hours = briefing_cfg.get("lookback_hours", 13)

    articles = load_articles()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    recent = []
    for a in articles:
        try:
            collected = datetime.fromisoformat(a["collected_at"])
            if collected.tzinfo is None:
                collected = collected.replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if collected >= cutoff:
            recent.append(a)

    if not recent:
        print(f"[briefing] 최근 {lookback_hours}시간 내 기사 없음 — 브리핑 스킵")
        return

    recent.sort(key=lambda a: a["relevance_score"], reverse=True)
    top = recent[:top_n]

    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    time_label = "아침" if now_kst.hour < 12 else "저녁"

    lines = [
        f"📰 {time_label} 브리핑 ({now_kst.strftime('%Y-%m-%d %H:%M')} KST)",
        f"지난 {lookback_hours}시간 주요 크립토 뉴스 TOP {len(top)}",
        "",
    ]
    for i, a in enumerate(top, 1):
        lines.append(f"{i}. [{a['relevance_score']:.1f}] {a['title']}")
        lines.append(f"   {a['source']} · {a['url']}")
        lines.append("")

    text = "\n".join(lines).strip()

    try:
        send_telegram_message(text)
        print(f"[briefing] 전송 완료: {len(top)}건")
    except Exception as e:
        print(f"[briefing] 전송 실패: {e}")


if __name__ == "__main__":
    main()
