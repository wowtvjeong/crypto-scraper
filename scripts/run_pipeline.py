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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from app.telegram_scraper import scrape_all_channels
from app.press_scraper import scrape_all_press
from app.cryptopanic_scraper import scrape_cryptopanic
from app.xangle_scraper import scrape_xangle
from app.bloomingbit_scraper import scrape_bloomingbit
from app.coinness_scraper import scrape_coinness
from app.ai_filter import score_items
from scripts.notify_telegram import send_telegram_message

CONFIG_PATH = ROOT / "config.yaml"
DATA_PATH = ROOT / "docs" / "articles.json"
ARCHIVE_DIR = ROOT / "docs" / "archive"
ARCHIVE_INDEX_PATH = ARCHIVE_DIR / "index.json"
HEALTH_PATH = ARCHIVE_DIR / "source-health.json"
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


def seen_path(month_key: str) -> Path:
    return ARCHIVE_DIR / f"seen-{month_key}.json"


def load_seen(month_key: str) -> set:
    """이번 달에 이미 AI로 채점한 적 있는 URL 목록 (합격/불합격 무관, 재채점 방지용)."""
    p = seen_path(month_key)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(month_key: str, urls: set):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(seen_path(month_key), "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False)


def update_archive_index():
    """대시보드가 '몇 년 몇 월' 목록을 드롭다운으로 보여줄 수 있도록 인덱스 파일 갱신.
    seen-*.json(내부 원장)은 대시보드용이 아니므로 목록에서 제외한다."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    months = sorted(
        (p.stem for p in ARCHIVE_DIR.glob("*.json")
         if p.stem != "index" and not p.stem.startswith("seen-")),
        reverse=True,
    )
    with open(ARCHIVE_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(months, f, ensure_ascii=False, indent=2)


def load_health() -> dict:
    if HEALTH_PATH.exists():
        with open(HEALTH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_health(health: dict):
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEALTH_PATH, "w", encoding="utf-8") as f:
        json.dump(health, f, ensure_ascii=False, indent=2)


def check_source_health(config: dict, telegram_items: list, press_items: list):
    """설정된 소스가 연속으로 0건을 내면 텔레그램으로 알림.
    RSS/텔레그램 스크래핑은 매번 '최신 N건'을 통째로 가져오는 방식이라,
    정상 소스는 거의 항상 몇 건씩은 잡힌다 — 그래서 하루 단위가 아니라
    '연속 실행 횟수' 기준으로 훨씬 빠르게 이상을 감지한다."""
    hc_cfg = config.get("health_check", {})
    if not hc_cfg.get("enabled", True):
        return
    threshold = hc_cfg.get("consecutive_zero_threshold", 3)

    configured_sources = (
        [c["name"] for c in config.get("telegram_channels", [])] +
        [s["name"] for s in config.get("press_sites", [])]
    )
    counts = Counter(i.source for i in (telegram_items + press_items))

    health = load_health()
    for name in configured_sources:
        count = counts.get(name, 0)
        entry = health.get(name, {"consecutive_zero": 0, "alerted": False})

        if count > 0:
            entry["consecutive_zero"] = 0
            entry["alerted"] = False
            entry["last_ok_run"] = datetime.now(timezone.utc).isoformat()
        else:
            entry["consecutive_zero"] = entry.get("consecutive_zero", 0) + 1
            if entry["consecutive_zero"] >= threshold and not entry.get("alerted"):
                text = (
                    f"⚠️ 소스 이상 감지\n"
                    f"'{name}'에서 {entry['consecutive_zero']}회 연속 0건 수집됐습니다.\n"
                    f"RSS 주소, 셀렉터, 채널명이 여전히 유효한지 확인해주세요."
                )
                try:
                    send_telegram_message(text)
                    print(f"[health] 이상 알림 전송: {name}")
                except Exception as e:
                    print(f"[health] 알림 실패: {e}")
                entry["alerted"] = True

        health[name] = entry

    save_health(health)


async def main():
    config = load_config()

    telegram_items = await scrape_all_channels(config.get("telegram_channels", []))
    press_items = await scrape_all_press(config.get("press_sites", []))

    if config.get("enable_xangle", True):
        press_items += await scrape_xangle()

    if config.get("enable_bloomingbit", True):
        press_items += await scrape_bloomingbit()

    if config.get("enable_coinness", True):
        press_items += await scrape_coinness()

    api_items = []
    if config.get("enable_cryptopanic", True):
        api_items = await scrape_cryptopanic()

    all_items = telegram_items + press_items + api_items
    print(f"[pipeline] 원본 수집: {len(all_items)}건 "
          f"(텔레그램 {len(telegram_items)} / 언론사 {len(press_items)} / API {len(api_items)})")

    check_source_health(config, telegram_items, press_items)

    # ── AI 채점 전에 '이미 본 URL'을 걸러낸다 (Groq 토큰 낭비 방지가 핵심) ──
    # 매번 RSS가 최신 20~30건을 다시 주기 때문에, 이 필터가 없으면 같은 기사를
    # 30분마다 계속 재채점하게 되어 하루 토큰 한도를 순식간에 소진하게 된다.
    existing = load_existing()
    existing_urls = {a["url"] for a in existing}

    month_key = current_month_key()
    already_scored = load_seen(month_key)   # 합격/불합격 무관, 이번 달에 채점한 적 있는 URL 전부

    seen_urls = existing_urls | already_scored
    unseen_items = [i for i in all_items if i.url not in seen_urls]
    print(f"[pipeline] AI 채점 대상(신규): {len(unseen_items)}건 "
          f"(이미 본 {len(all_items) - len(unseen_items)}건은 재채점 건너뜀)")

    filter_cfg = config.get("filter", {})
    min_score = filter_cfg.get("min_relevance_score", 7)
    breaking_score = filter_cfg.get("breaking_score", 9)
    keywords_boost = filter_cfg.get("keywords_boost", [])

    scored = score_items(unseen_items, keywords_boost=keywords_boost)

    # 합격/불합격과 무관하게, 채점을 시도한 URL은 전부 '본 것'으로 원장에 기록
    already_scored |= {i.url for i in unseen_items}
    save_seen(month_key, already_scored)

    new_articles = [a for a in scored if a.relevance_score >= min_score]
    print(f"[pipeline] 필터 통과: {len(new_articles)}건 (기준 {min_score}점)")

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
