"""
텔레그램 공개 채널 스크래퍼.

로그인 없이 t.me/s/{channel} 웹 프리뷰 페이지를 파싱한다.
비공개 채널이나 더 안정적인 수집이 필요하면 Telethon(API_ID/API_HASH)으로
교체하는 걸 권장 — 그 경우 scrape_channel() 시그니처만 유지하면
main.py 쪽 수정이 필요 없다.
"""
import httpx
from bs4 import BeautifulSoup
from typing import List

from app.models import RawItem

TELEGRAM_PREVIEW_URL = "https://t.me/s/{channel}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


async def scrape_channel(channel_name: str, limit: int = 20) -> List[RawItem]:
    """공개 텔레그램 채널의 최근 메시지를 가져온다."""
    url = TELEGRAM_PREVIEW_URL.format(channel=channel_name)
    items: List[RawItem] = []

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[telegram] {channel_name} 요청 실패: {e}")
        return items

    soup = BeautifulSoup(resp.text, "html.parser")
    messages = soup.select("div.tgme_widget_message")[-limit:]

    for msg in messages:
        text_el = msg.select_one(".tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text(separator="\n").strip()
        if not text:
            continue

        link_el = msg.select_one("a.tgme_widget_message_date")
        msg_url = link_el["href"] if link_el else url

        time_el = msg.select_one("time")
        published_at = time_el["datetime"] if time_el and time_el.has_attr("datetime") else None

        # 텔레그램 메시지는 제목이 따로 없으므로 첫 줄을 제목으로 사용
        first_line = text.split("\n")[0][:120]

        items.append(RawItem(
            source=channel_name,
            source_type="telegram",
            title=first_line,
            content=text,
            url=msg_url,
            published_at=published_at,
        ))

    return items


async def scrape_all_channels(channels: List[dict], limit_per_channel: int = 20) -> List[RawItem]:
    all_items: List[RawItem] = []
    for ch in channels:
        items = await scrape_channel(ch["name"], limit=limit_per_channel)
        all_items.extend(items)
    return all_items
