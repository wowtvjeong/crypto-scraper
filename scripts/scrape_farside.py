"""
Farside Investors ETF 자금흐름(Flow) 데이터 수집.
farside.co.uk/btc/, farside.co.uk/eth/ 는 숫자가 순수 HTML <table class="etf">에
그대로 박혀있어서 별도 API 없이 파싱만으로 수집 가능하다 (CoinMarketCap 때와 유사).

주의:
- 최신 날짜(오늘) 행은 일부 운용사 데이터가 아직 안 들어와서 "-"로 비어있을 수 있다
  (표에서 초록색 배경으로 표시되는 "잠정" 행). 이 경우 title에 "(잠정치)"를 붙인다.
- 표 구조: thead 안에 헤더 행이 여러 개 있고, 그 중 "두 번째 tr"이 항상 티커
  (IBIT, FBTC...) 행이다. BTC 표는 헤더가 3행(발행사/티커/수수료), ETH 표는
  4행(발행사/티커/수수료/스테이킹수수료)이라 행 개수가 다르지만, 티커는 항상
  두 번째 행에 있어서 이 로직은 공통으로 쓸 수 있다.
"""
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from app.models import RawItem

SKIP_ROW_LABELS = {"total", "average", "maximum", "minimum", "seed"}

TARGETS = [
    {
        "url": "https://farside.co.uk/btc/",
        "label": "비트코인",
        "emoji": "🟠",
        "source": "Farside ETF Flow (BTC)",
    },
    {
        "url": "https://farside.co.uk/eth/",
        "label": "이더리움",
        "emoji": "🔷",
        "source": "Farside ETF Flow (ETH)",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def _parse_num(raw: str) -> Optional[float]:
    """'(239.3)' -> -239.3, '90.4' -> 90.4, '-' -> None, '0.0' -> 0.0, '60,286' -> 60286.0"""
    raw = raw.strip()
    if raw in ("-", "", "—"):
        return None
    neg = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.strip("()").replace(",", "")
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return -val if neg else val


def _parse_etf_table(html: str):
    """table.etf를 파싱해서 (티커 목록, 날짜별 행 목록)을 반환."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="etf")
    if not table:
        return [], []

    thead = table.find("thead")
    if not thead:
        return [], []
    header_rows = thead.find_all("tr")
    if len(header_rows) < 2:
        return [], []

    ticker_row = header_rows[1]
    ths = ticker_row.find_all("th")
    tickers = [th.get_text(strip=True) or "?" for th in ths[1:-1]]

    rows = []
    tbody = table.find("tbody")
    if not tbody:
        return tickers, []

    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        label = tds[0].get_text(strip=True)
        if label.lower() in SKIP_ROW_LABELS:
            continue

        values = [td.get_text(strip=True) for td in tds[1:]]
        if len(values) < 2:
            continue

        *fund_values, total = values
        rows.append({"date": label, "funds": fund_values, "total": total})

    return tickers, rows


def _format_briefing(label: str, emoji: str, tickers, latest_row, prev_row=None) -> tuple:
    """(title, content) 생성."""
    date_str = latest_row["date"]
    total = _parse_num(latest_row["total"])
    fund_vals = {t: _parse_num(v) for t, v in zip(tickers, latest_row["funds"])}

    missing = [t for t, v in fund_vals.items() if v is None]
    is_provisional = len(missing) > len(fund_vals) / 2  # 절반 이상 미집계면 잠정치로 표기

    if total is None:
        flow_word = "집계 중"
        total_str = ""
    elif total >= 0:
        flow_word = "순유입"
        total_str = f"+{total:,.1f}M"
    else:
        flow_word = "순유출"
        total_str = f"{abs(total):,.1f}M"

    tag = " (잠정치 — 일부 운용사 미집계)" if is_provisional else ""
    title_amount = f" {total_str}" if total_str else ""
    title = f"{emoji} {label} 현물 ETF, {date_str} {flow_word}{title_amount}{tag}"

    lines = [f"{label} 현물 ETF 자금흐름 — {date_str}", f"총합: {total_str}", ""]
    for t, v in fund_vals.items():
        if v is None:
            lines.append(f"- {t}: 미집계")
        else:
            sign = "+" if v >= 0 else ""
            lines.append(f"- {t}: {sign}{v:,.1f}M")

    if prev_row:
        prev_total = _parse_num(prev_row["total"])
        if prev_total is not None and total is not None:
            lines.append("")
            lines.append(f"(직전 거래일 {prev_row['date']} 총합: {prev_total:+,.1f}M)")

    content = "\n".join(lines)
    return title, content


async def _scrape_one(target: dict) -> List[RawItem]:
    items: List[RawItem] = []
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            resp = await client.get(target["url"])
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPError as e:
        print(f"[farside] {target['label']} 요청 실패: {e}")
        return items

    tickers, rows = _parse_etf_table(html)
    if not rows:
        print(f"[farside] {target['label']} 표 파싱 실패 (구조 변경 가능성)")
        return items

    latest_row = rows[-1]
    prev_row = rows[-2] if len(rows) >= 2 else None

    title, content = _format_briefing(
        target["label"], target["emoji"], tickers, latest_row, prev_row
    )

    items.append(
        RawItem(
            source=target["source"],
            source_type="api",
            title=title,
            content=content,
            url=target["url"],
            # 수집 시점을 현재(UTC)로 채워서, 대시보드 최신순 정렬에서 묻히지 않게 한다.
            # (Farside 데이터 자체엔 "발행 시각"이 없고 "거래일자"만 있어서, 사실상
            #  이 파이프라인이 그 날짜의 최종 수치를 확인한 시점을 발행시각으로 취급)
            published_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    return items


async def scrape_farside() -> List[RawItem]:
    """BTC + ETH ETF 자금흐름을 모두 수집."""
    items: List[RawItem] = []
    for target in TARGETS:
        items.extend(await _scrape_one(target))
    return items
