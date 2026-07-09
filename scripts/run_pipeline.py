"""
GitHub Actions cron이 주기적으로 실행하는 1회성 스크립트.
FastAPI 서버 없이 동작하며, 결과는 docs/articles.json에 저장한다.
(docs/ 는 GitHub Pages가 그대로 정적 서빙하는 폴더)

신규로 통과된 기사 중 '속보' 기준 점수 이상인 것은 텔레그램으로 즉시 알림.
과거 기사는 월별로 docs/archive/YYYY-MM.json에 영구 보관한다 (대시보드에서 기간 선택으로 조회 가능).
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from app.telegram_scraper import scrape_all_channels
from app.press_scraper import scrape_all_press
from app.ai_filter import score_items
from scripts.notify_telegram import send_telegram_message

CONFIG_PATH = ROOT / "config.yaml"
DATA_PATH = ROOT / "docs" / "articles.json"
ARCHIVE_DIR = ROOT / "docs" / "archive"
ARCHIVE_INDEX_PATH = ARCHIVE_DIR / "index.json"
MAX_KEEP = 300  # 최근 목록(articles.json)에 유지할 최대 기사 수 — 대시보드 로딩 속도용


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_existing() -> list:
    if DATA_PATH.exists():
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_articles(articles: list):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def to_dict(a) -> dict:
    return {
        "source": a.source,
        "source_type": a.source_type,
        "title": a.title,
        "content": a.content,
        "url": a.url,
        "published_at": a.published_at,
        "relevance_score": a.relevance_score,
        "is_crypto_related": a.is_crypto_related,
        "reason": a.reason,
        "collected_at": a.collected_at,
    }


def current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load_archive_month(month_key: str) -> list:
    path = ARCHIVE_DIR / f"{month_key}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_archive_month(month_key: str, articles: list):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE_DIR / f"{month_key}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def update_archive_index():
    """대시보드가 '몇 년 몇 월' 목록을 드롭다운으로 보여줄 수 있도록 인덱스 파일 갱신."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    months = sorted(
        (p.stem for p in ARCHIVE_DIR.glob("*.json") if p.stem != "index"),
        reverse=True,
    )
    with open(ARCHIVE_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(months, f, ensure_ascii=False, indent=2)


async def main():
    config = load_config()

    telegram_items = await scrape_all_channels(config.get("telegram_channels", []))
    press_items = await scrape_all_press(config.get("press_sites", []))
    all_items = telegram_items + press_items
    print(f"[pipeline] 원본 수집: {len(all_items)}건 "
          f"(텔레그램 {len(telegram_items)} / 언론사 {len(press_items)})")

    filter_cfg = config.get("filter", {})
    min_score = filter_cfg.get("min_relevance_score", 7)
    breaking_score = filter_cfg.get("breaking_score", 9)
    keywords_boost = filter_cfg.get("keywords_boost", [])

    scored = score_items(all_items, keywords_boost=keywords_boost)
    passed = [a for a in scored if a.relevance_score >= min_score]
    print(f"[pipeline] 필터 통과: {len(passed)}건 (기준 {min_score}점)")

    existing = load_existing()
    existing_urls = {a["url"] for a in existing}
    new_articles = [a for a in passed if a.url not in existing_urls]
    print(f"[pipeline] 신규 기사: {len(new_articles)}건")

    # 속보 알림 (신규 + 고득점만)
    breaking = [a for a in new_articles if a.relevance_score >= breaking_score]
    for a in breaking:
        text = (
            f"🚨 속보 ({a.relevance_score:.1f}점)\n"
            f"{a.title}\n\n"
            f"{a.source} · {a.source_type}\n"
            f"{a.url}"
        )
        try:
            send_telegram_message(text)
            print(f"[notify] 전송: {a.title[:40]}")
        except Exception as e:
            print(f"[notify] 실패: {e}")

    combined = [to_dict(a) for a in new_articles] + existing
    combined = combined[:MAX_KEEP]
    save_articles(combined)
    print(f"[pipeline] 최근 목록 저장 완료: 총 {len(combined)}건 ({DATA_PATH})")

    # 월별 아카이브에도 신규 기사 누적 저장 (몇 달치 조회용, 개수 제한 없음)
    month_key = current_month_key()
    archive = load_archive_month(month_key)
    archive_urls = {a["url"] for a in archive}
    new_for_archive = [to_dict(a) for a in new_articles if a.url not in archive_urls]
    archive = new_for_archive + archive
    save_archive_month(month_key, archive)
    update_archive_index()
    print(f"[pipeline] 아카이브 저장 완료: {month_key} 총 {len(archive)}건")


if __name__ == "__main__":
    asyncio.run(main())
