"""
mempool.space 공개 API로 비트코인 온체인 네트워크 지표를 가져온다.
완전 무료, 로그인·API키 불필요, 실시간(초~분 단위 갱신).
https://mempool.space/docs/api/rest

이전에 만든 "Bitcoin on-chain data 시각화" 방송 화면 위젯과 같은 데이터 소스를 쓴다.
그 위젯은 화면에 보여주는 용도였고, 여기서는 그 숫자를 대본 문장으로 녹여 넣는 용도.
"""
import httpx
from typing import Optional

BASE_URL = "https://mempool.space/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


async def fetch_onchain_snapshot() -> Optional[dict]:
    """실패해도 None만 반환하고 예외를 던지지 않는다 (전체 대본 생성이 이것 때문에
    멈추면 안 되므로 — 이 지표는 '있으면 좋은 것'이지 필수가 아님)."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            mempool_resp = await client.get(f"{BASE_URL}/mempool")
            fees_resp = await client.get(f"{BASE_URL}/v1/fees/recommended")
            height_resp = await client.get(f"{BASE_URL}/blocks/tip/height")
            hashrate_resp = await client.get(f"{BASE_URL}/v1/mining/hashrate/3d")

            mempool_resp.raise_for_status()
            fees_resp.raise_for_status()
            height_resp.raise_for_status()
            hashrate_resp.raise_for_status()

        mempool_data = mempool_resp.json()
        fees_data = fees_resp.json()
        height = int(height_resp.text.strip())
        hashrate_data = hashrate_resp.json()

        hashrate_ehs = hashrate_data.get("currentHashrate", 0) / 1e18

        return {
            "mempool_count": mempool_data.get("count"),
            "fastest_fee_satvb": fees_data.get("fastestFee"),
            "economy_fee_satvb": fees_data.get("economyFee"),
            "latest_block_height": height,
            "hashrate_ehs": round(hashrate_ehs, 1),
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        print(f"[onchain] mempool.space 조회 실패: {e}")
        return None


def format_snapshot_for_prompt(snapshot: Optional[dict]) -> str:
    if not snapshot:
        return "(실시간 온체인 네트워크 지표를 가져오지 못했습니다 — 이 부분은 생략하고 뉴스 위주로만 작성)"
    return (
        f"- 현재 비트코인 네트워크 해시레이트: 약 {snapshot['hashrate_ehs']} EH/s\n"
        f"- 멤풀(미확인 거래) 대기 건수: 약 {snapshot['mempool_count']:,}건\n"
        f"- 권장 거래 수수료: 빠른 처리 기준 {snapshot['fastest_fee_satvb']} sat/vB, "
        f"저렴한 처리 기준 {snapshot['economy_fee_satvb']} sat/vB\n"
        f"- 최신 블록 높이: {snapshot['latest_block_height']:,}"
    )
