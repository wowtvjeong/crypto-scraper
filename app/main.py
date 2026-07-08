import yaml
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import storage
from app.telegram_scraper import scrape_all_channels
from app.press_scraper import scrape_all_press
from app.ai_filter import score_items

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

scheduler = AsyncIOScheduler()

# 대시보드가 폴링할 최근 실행 상태 (메모리 보관만 해도 충분)
last_run_state = {
    "last_result": None,
    "last_run_at": None,
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_pipeline() -> dict:
    """텔레그램+언론사 수집 → AI 필터링 → 저장. 결과 요약 반환."""
    config = load_config()

    telegram_items = await scrape_all_channels(config.get("telegram_channels", []))
    press_items = await scrape_all_press(config.get("press_sites", []))
    all_items = telegram_items + press_items

    print(f"[pipeline] 수집된 원본 아이템: {len(all_items)}개 "
          f"(텔레그램 {len(telegram_items)} / 언론사 {len(press_items)})")

    filter_cfg = config.get("filter", {})
    keywords_boost = filter_cfg.get("keywords_boost", [])
    min_score = filter_cfg.get("min_relevance_score", 7)

    scored = score_items(all_items, keywords_boost=keywords_boost)
    passed = [a for a in scored if a.relevance_score >= min_score]

    saved_count = storage.save_articles(passed)

    result = {
        "collected": len(all_items),
        "scored": len(scored),
        "passed_filter": len(passed),
        "newly_saved": saved_count,
    }
    print(f"[pipeline] 결과: {result}")

    last_run_state["last_result"] = result
    last_run_state["last_run_at"] = datetime.now().isoformat()

    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    config = load_config()
    interval = config.get("schedule", {}).get("interval_minutes", 30)

    scheduler.add_job(run_pipeline, "interval", minutes=interval, id="scrape_job")
    scheduler.start()
    print(f"[startup] {interval}분 간격으로 자동 수집을 시작합니다.")

    yield

    scheduler.shutdown()


app = FastAPI(title="AI 기사 스크랩 봇", lifespan=lifespan)

# 대시보드를 별도 파일(dashboard.html)에서 fetch로 붙일 수 있도록 CORS 전면 허용.
# 로컬 전용 도구이므로 실사용 시 origin을 좁혀도 됨.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status():
    config = load_config()
    interval = config.get("schedule", {}).get("interval_minutes", 30)

    next_run = None
    job = scheduler.get_job("scrape_job")
    if job and job.next_run_time:
        next_run = job.next_run_time.isoformat()

    return {
        "interval_minutes": interval,
        "next_run_at": next_run,
        "last_run_at": last_run_state["last_run_at"],
        "last_result": last_run_state["last_result"],
    }


@app.post("/scrape/now")
async def scrape_now():
    result = await run_pipeline()
    return result


@app.get("/articles")
async def articles(
    min_score: float = Query(0, description="최소 관련성 점수"),
    source_type: str = Query(None, description="telegram 또는 press"),
    limit: int = Query(50, le=200),
):
    return storage.get_articles(min_score=min_score, source_type=source_type, limit=limit)


# 정적 대시보드 서빙: http://localhost:8811/ 접속 시 dashboard.html 반환
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def dashboard():
        return FileResponse(str(STATIC_DIR / "dashboard.html"))
