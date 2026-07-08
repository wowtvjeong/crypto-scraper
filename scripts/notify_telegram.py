"""
텔레그램 봇 API로 본인 폰에 메시지 전송.

사전 준비:
1. 텔레그램에서 @BotFather 검색 → /newbot → 봇 이름 정하면 토큰 발급됨
   → 이게 TELEGRAM_BOT_TOKEN
2. 새로 만든 봇과 아무 대화나 1번 시작 (/start 등 아무거나 전송)
3. 브라우저에서 아래 접속해서 chat.id 확인
   https://api.telegram.org/bot<발급받은토큰>/getUpdates
   → 응답 JSON에서 "chat":{"id": 123456789, ...} 의 숫자가 TELEGRAM_CHAT_ID
"""
import os
import httpx


def send_telegram_message(text: str) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — 알림 건너뜀")
        return {}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = httpx.post(
        url,
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()
